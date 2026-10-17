"""What the CA-side tests share: settings, database connections, test campaigns, the link and the gateway.

Run from the repository root of the CA's machine (the settings are read from its .env)."""

import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from campaign_agent import console_flow
from campaign_agent.ma_link.server import resolve_bind
from campaign_agent.settings import Settings, load_settings
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Database
from qa.lib.core import Ctx, Skip, http

QA_PREFIX = "[CA-TEST] QA "  # `python -m campaign_agent try --clean` removes these too
QA_USER = "qa-harness@example.test"
REPO = Path(__file__).resolve().parents[2]
WORKING_STATES = ("INTERPRETING", "RESEARCHING", "VALIDATING", "DRAFT", "NEW")


def settings(ctx: Ctx) -> Settings:
    if "settings" not in ctx.data:
        ctx.data["settings"] = load_settings()
    return ctx.data["settings"]  # type: ignore[no-any-return]


def admin_db(ctx: Ctx) -> Database:
    """The owner login: what the dashboard's server stands in for (create campaigns, send requests), and clean-up."""
    if "admin_db" not in ctx.data:
        s = settings(ctx)
        url = s.migrate_database_url or s.database_url
        if url is None:
            raise Skip("no MIGRATE_DATABASE_URL or DATABASE_URL")
        ctx.data["admin_db"] = Database(url.get_secret_value())
    return ctx.data["admin_db"]  # type: ignore[no-any-return]


def agent_db(ctx: Ctx) -> Database:
    """The Campaign Agent's own limited login, to check what it can see."""
    if "agent_db" not in ctx.data:
        url = settings(ctx).database_url
        if url is None:
            raise Skip("no DATABASE_URL")
        ctx.data["agent_db"] = Database(url.get_secret_value())
    return ctx.data["agent_db"]  # type: ignore[no-any-return]


def q(db: Database, sql: str, *params: Any) -> list[dict[str, Any]]:
    with db.transaction() as conn:
        return list(conn.execute(sql, params or None).fetchall())


def one(db: Database, sql: str, *params: Any) -> dict[str, Any] | None:
    rows = q(db, sql, *params)
    return rows[0] if rows else None


def systemd_active(unit: str) -> str:
    done = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return done.stdout.strip() or done.stderr.strip()


# --- the link to the Marketing Agent ------------------------------------------------------------------------------


def link_base(ctx: Ctx) -> str:
    s = settings(ctx)
    if not s.ma_link_enabled:
        raise Skip("MA_LINK_ENABLED is off")
    address = resolve_bind(s.ma_link_bind)
    if address is None:
        raise Skip("Tailscale has no address on this machine")
    return f"http://{address}:{s.ma_link_port}/ma/v1"


def link_headers(ctx: Ctx) -> dict[str, str]:
    token = settings(ctx).ma_link_token
    if token is None:
        raise Skip("MA_LINK_TOKEN is not set")
    return {"Authorization": f"Bearer {token.get_secret_value()}"}


# --- the gateway (needs the token the owner holds) -----------------------------------------------------------------


class Gateway:
    def __init__(self, base: str, token: str) -> None:
        self.base, self.token = base.rstrip("/"), token

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        token: str | None = "default",
        timeout: float = 90.0,
    ):  # type: ignore[no-untyped-def]
        headers = (
            {}
            if token is None
            else {"Authorization": f"Bearer {self.token if token == 'default' else token}"}
        )
        return http(method, self.base + path, headers=headers, body=body, timeout=timeout)


def gateway(ctx: Ctx) -> Gateway:
    token = ctx.getenv("QA_GATEWAY_TOKEN")
    if not token:
        raise Skip(
            "QA_GATEWAY_TOKEN is not set (put it in qa/.env.qa on this machine to run the gateway tests)"
        )
    return Gateway(ctx.getenv("QA_GATEWAY_URL", "https://campaign-agent-maha.onrender.com"), token)


# --- test campaigns ------------------------------------------------------------------------------------------------


def make_campaign(ctx: Ctx, detail: str, tag: str, *, request: bool = False) -> str:
    """A campaign as the dashboard saves it. With request=True the interpret request is sent at once, as the dashboard does."""
    db = admin_db(ctx)
    name = f"{QA_PREFIX}{ctx.run_id} {tag}"[:80]
    with db.transaction() as conn:
        row = conn.execute(
            "insert into public.campaign (campaign_name, campaign_detail, start_date, end_date, is_active) "
            "values (%s, %s, current_date, current_date + 2, true) returning id",
            (name, detail[:5000]),
        ).fetchone()
    assert row is not None
    cid = str(row["id"])
    ctx.data.setdefault("campaigns", []).append(cid)
    if request:
        send(ctx, cid, "interpret")
    return cid


def send(
    ctx: Ctx,
    cid: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    base: int | None = None,
    key: str | None = None,
) -> str:
    """A request to the Campaign Agent, written the way the dashboard writes it. Returns the request id."""
    with admin_db(ctx).transaction() as conn:
        row = repo.enqueue_request(
            conn, repo.NewRequest(cid, kind, QA_USER, key or uuid.uuid4().hex, payload or {}, base)
        )
    return str(row["id"])


def request_row(ctx: Ctx, rid: str) -> dict[str, Any] | None:
    return one(
        admin_db(ctx),
        "select request_type, status, error_code, error_detail, result, created_at, claimed_at, finished_at from public.campaign_request where id = %s",
        rid,
    )


def wait_request(ctx: Ctx, rid: str, timeout_s: float) -> dict[str, Any] | None:
    return ctx.poll(
        lambda: (r := request_row(ctx, rid)) and r["status"] in ("done", "failed") and r,
        timeout_s,
        4.0,
    )  # type: ignore[no-any-return]


def view(ctx: Ctx, cid: str) -> dict[str, Any]:
    return console_flow.status(admin_db(ctx), cid)


def wait_state(
    ctx: Ctx, cid: str, states: tuple[str, ...], timeout_s: float
) -> dict[str, Any] | None:
    """The status once the campaign is in one of `states` and no request is running; None when it never gets there."""

    def ready() -> dict[str, Any] | None:
        v = view(ctx, cid)
        return v if v["state"] in states and not v["busy"] else None

    return ctx.poll(ready, timeout_s, 5.0)  # type: ignore[no-any-return]


def live_now(ctx: Ctx) -> dict[str, Any] | None:
    return one(
        admin_db(ctx),
        "select s.campaign_id::text as campaign_id, v.version_no, v.sha256 from public.campaign_state s "
        "join public.campaign_version v on v.id = s.active_version_id",
    )


def pipeline(ctx: Ctx) -> dict[str, Any] | None:
    return one(
        admin_db(ctx),
        "select desired_state, actual_state, current_step, requested_by, heartbeat_at from public.pipeline_control where id = 1",
    )


def delete_campaigns(ctx: Ctx) -> int:
    ids = ctx.data.get("campaigns", [])
    if not ids:
        return 0
    with admin_db(ctx).transaction() as conn:
        conn.execute("delete from public.campaign where id = any(%s::uuid[])", (ids,))
    n = len(ids)
    ctx.data["campaigns"] = []
    return n


def wait_idle_worker(ctx: Ctx, timeout_s: float = 120) -> bool:
    """The Campaign Agent is up and not paused."""
    return bool(
        ctx.poll(
            lambda: (
                (
                    r := one(
                        admin_db(ctx),
                        "select agent_status, extract(epoch from now() - last_seen) as age from public.ca_heartbeat",
                    )
                )
                and r["age"] < 45
                and r["agent_status"] in ("idle", "busy")
            ),
            timeout_s,
            5.0,
        )
    )


def now() -> float:
    return time.time()

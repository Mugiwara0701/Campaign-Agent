"""A live terminal view of what the Campaign Agent is doing: python -m campaign_agent console.

It only reads: the database, the model server and the link port. It starts nothing and changes nothing, so it can run
next to the service (`start`) on the same machine. `--demo` plays a scripted story instead and says so on every screen.
"""

import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import psycopg

from campaign_agent.settings import Settings
from campaign_agent.store.db import Conn, Database

OK, WARN, FAIL, OFF = "ok", "warn", "fail", "off"
_MARK = {
    OK: "\033[32m●\033[0m",
    WARN: "\033[33m●\033[0m",
    FAIL: "\033[31m●\033[0m",
    OFF: "\033[90m○\033[0m",
}
_CLEAR = "\033[H\033[2J"
HEARTBEAT_FRESH_S = 30


@dataclass(frozen=True)
class Line:
    name: str
    state: str
    detail: str


@dataclass(frozen=True)
class Snapshot:
    lines: tuple[Line, ...]
    requests: tuple[str, ...]
    events: tuple[str, ...]


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


def _query(conn: Conn, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]] | None:
    """Rows, or None when the table is missing or not readable (migration not applied yet). Inside a savepoint."""
    try:
        with conn.transaction():
            return list(conn.execute(sql, params).fetchall())
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        return None


def _database(db: Database) -> Line:
    started = time.monotonic()
    try:
        with db.transaction() as conn:
            conn.execute("select 1")
    except Exception as exc:
        return Line("Database", FAIL, f"not reachable ({type(exc).__name__})")
    return Line("Database", OK, f"reachable, {int((time.monotonic() - started) * 1000)} ms")


def _worker(conn: Conn, model: str) -> Line:
    rows = _query(
        conn,
        "select agent_status, model_name, extract(epoch from now() - last_seen) as age from public.ca_heartbeat",
    )
    if not rows:
        return Line(
            "Worker", FAIL, "no heartbeat yet: `start` has not run, or the database was reset"
        )
    row = rows[0]
    age = float(row["age"])  # type: ignore[arg-type]
    state = OK if age <= HEARTBEAT_FRESH_S else FAIL
    if state == OK and row["agent_status"] == "paused":
        return Line(
            "Worker",
            WARN,
            f"paused from the dashboard: takes no new request, last beat {_ago(age)}",
        )
    note = f"{row['agent_status']}, model {row['model_name'] or model}, last beat {_ago(age)}"
    return Line("Worker", state, note if state == OK else f"stopped? {note}")


def _model(settings: Settings) -> Line:
    path = "/api/tags" if settings.llm_backend == "ollama" else "/v1/models"
    try:
        response = httpx.get(settings.llm_base_url.rstrip("/") + path, timeout=3.0)
    except httpx.HTTPError as exc:
        return Line(
            "Model server", FAIL, f"{settings.llm_base_url} not reachable ({type(exc).__name__})"
        )
    if response.status_code >= 500:
        return Line(
            "Model server", WARN, f"{settings.llm_base_url} answered {response.status_code}"
        )
    return Line(
        "Model server",
        OK,
        f"{settings.llm_backend} at {settings.llm_base_url}, model {settings.llm_model}",
    )


def _queue(conn: Conn) -> tuple[Line, tuple[str, ...]]:
    counts = _query(
        conn, "select status, count(*) as n from public.campaign_request group by status"
    )
    if counts is None:
        return Line("Requests", FAIL, "table not readable: apply the migrations"), ()
    by = {str(r["status"]): int(r["n"]) for r in counts}  # type: ignore[call-overload]
    pending, running, failed = by.get("pending", 0), by.get("processing", 0), by.get("failed", 0)
    state = WARN if failed else OK
    recent = _query(
        conn,
        "select request_type, status, requested_by, extract(epoch from now() - created_at) as age, error_code "
        "from public.campaign_request order by created_at desc limit 6",
    )
    rows = tuple(
        f"{_ago(float(r['age'])):>8}  {r['request_type']!s:<11} {r['status']!s:<10} {r['requested_by']}"  # type: ignore[arg-type]
        + (f"  [{r['error_code']}]" if r["error_code"] else "")
        for r in recent or []
    )
    return Line(
        "Requests",
        state,
        f"{pending} waiting, {running} running, {failed} failed, {by.get('done', 0)} done",
    ), rows


def _campaign(conn: Conn) -> Line:
    rows = _query(
        conn,
        "select v.version_no, v.approved_by, extract(epoch from now() - v.approved_at) as age "
        "from public.campaign_state s join public.campaign_version v on v.id = s.active_version_id "
        "where v.approved_at is not null",
    )
    if rows is None:
        return Line("Live campaign", FAIL, "table not readable: apply the migrations")
    if not rows:
        return Line("Live campaign", OFF, "none is live; the Marketing Agent runs its base config")
    r = rows[0]
    return Line(
        "Live campaign",
        OK,
        f"version {r['version_no']}, approved by {r['approved_by']} {_ago(float(r['age']))}",
    )  # type: ignore[arg-type]


def _link(settings: Settings) -> Line:
    from campaign_agent.ma_link.auth import MIN_TOKEN_CHARS
    from campaign_agent.ma_link.server import resolve_bind

    if not settings.ma_link_enabled:
        return Line("Link to MA", OFF, "off (MA_LINK_ENABLED is not set)")
    token = settings.ma_link_token.get_secret_value() if settings.ma_link_token else ""
    if len(token) < MIN_TOKEN_CHARS:
        return Line(
            "Link to MA",
            FAIL,
            f"MA_LINK_TOKEN is missing or shorter than {MIN_TOKEN_CHARS} characters",
        )
    try:
        address = resolve_bind(settings.ma_link_bind)
    except ValueError as exc:
        return Line("Link to MA", FAIL, str(exc))
    if address is None:
        return Line(
            "Link to MA",
            WARN,
            "Tailscale has no address yet: is `tailscaled` up? (it retries every 15 s)",
        )
    try:
        with socket.create_connection((address, settings.ma_link_port), timeout=2):
            pass
    except OSError:
        return Line(
            "Link to MA",
            FAIL,
            f"nothing is listening on {address}:{settings.ma_link_port}: is `start` running?",
        )
    return Line("Link to MA", OK, f"listening on {address}:{settings.ma_link_port}")


def _marketing_agent(conn: Conn) -> tuple[Line, tuple[str, ...]]:
    status = _query(
        conn,
        "select extract(epoch from now() - reported_at) as age, current_campaign from public.ma_campaign_status",
    )
    events = _query(
        conn,
        "select kind, severity, code, message, repeat_count, to_char(occurred_at at time zone 'UTC', 'HH24:MI:SS') as at "
        "from public.ma_event where (kind = 'blocker' and resolved_at is null) or occurred_at > now() - interval '1 day' "
        "order by occurred_at desc limit 6",
    )
    lines = tuple(
        f"{e['at']}  {e['kind']!s:<9} {e['severity']!s:<8} {e['code']}  {e['message']}"
        + (f"  (x{e['repeat_count']})" if int(e["repeat_count"]) > 1 else "")  # type: ignore[call-overload]
        for e in events or []
    )
    running = _query(
        conn,
        "select code, message, extract(epoch from now() - occurred_at) as age from public.ma_event "
        "where code in ('MA_PASS_STARTED', 'MA_PASS_FINISHED', 'MA_STEP') and occurred_at > now() - interval '3 hours' "
        "order by occurred_at desc limit 1",
    )
    working = (
        f"running a pass ({running[0]['message'].removeprefix('Step: ')}, {_ago(float(running[0]['age']))})"  # type: ignore[union-attr, arg-type]
        if running and running[0]["code"] != "MA_PASS_FINISHED"
        else None
    )
    counted = _query(
        conn,
        "select count(*) as n from public.ma_event where kind = 'blocker' and resolved_at is null",
    )
    open_blockers = int(counted[0]["n"]) if counted else 0  # type: ignore[call-overload]
    if not status:
        return Line("Marketing Agent", OFF, "has not reported yet"), lines
    age = float(status[0]["age"])  # type: ignore[arg-type]
    detail = working or f"reported {_ago(age)}"
    if open_blockers:
        return Line("Marketing Agent", WARN, f"{detail}, {open_blockers} open blocker(s)"), lines
    return Line("Marketing Agent", OK if working or age < 3600 else WARN, detail), lines


def collect(db: Database, settings: Settings) -> Snapshot:
    db_line = _database(db)
    lines = [db_line]
    requests: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    if db_line.state == OK:
        try:
            with db.transaction() as conn:
                lines.append(_worker(conn, settings.llm_model))
                lines.append(_model(settings))
                queue, requests = _queue(conn)
                lines.append(queue)
                lines.append(_campaign(conn))
                lines.append(_link(settings))
                ma, events = _marketing_agent(conn)
                lines.append(ma)
        except Exception as exc:
            lines.append(Line("Checks", FAIL, f"stopped: {type(exc).__name__}"))
    else:
        lines.append(_model(settings))
        lines.append(_link(settings))
    return Snapshot(tuple(lines), requests, events)


def render(snap: Snapshot, *, banner: str, stamp: str) -> str:
    width = max(len(line.name) for line in snap.lines)
    out = [f"\033[1mCampaign Agent\033[0m  {banner}  {stamp}", ""]
    out += [f" {_MARK[line.state]} {line.name:<{width}}  {line.detail}" for line in snap.lines]
    out += ["", "\033[1mRecent requests\033[0m"] + [f"   {r}" for r in snap.requests or ("none",)]
    out += ["", "\033[1mMarketing Agent events\033[0m"] + [
        f"   {e}" for e in snap.events or ("none",)
    ]
    return "\n".join(out)


def run_console(
    db: Database | None,
    settings: Settings,
    *,
    interval_s: float = 2.0,
    once: bool = False,
    stream: Callable[[str], None] = lambda text: print(text, flush=True),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if db is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    live = sys.stdout.isatty() and not once
    try:
        while True:
            snap = collect(db, settings)
            screen = render(snap, banner="LIVE", stamp=time.strftime("%H:%M:%S"))
            stream((_CLEAR if live else "") + screen + ("\n\nCtrl+C to leave" if live else ""))
            if not live:
                return 0 if all(line.state != FAIL for line in snap.lines) else 1
            sleep(interval_s)
    except KeyboardInterrupt:
        return 0
    finally:
        db.close()

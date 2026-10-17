"""Both agents, end to end, with nothing real outside: the real Campaign Agent and the real Marketing Agent code.

    dashboard request -> CA proposal -> an edit -> new proposal -> a stale approval refused -> the right approval (twice)
    -> the CA's link serves only the approved version -> the MA picks it up over HTTP, checks it, reports it
    -> discovery and qualification over a fixed fake web -> a lead and a draft tied to the campaign -> nothing sent
    before approval -> Slack approval (twice) -> exactly one email to the outbox -> the CA sees "delivered"
    -> the MA restarts and runs again: no new lead, no new email, nothing sent again.

The model is the CA's stub, the MA's web, model and email writer are its own test fakes, Slack is its dry-run approver and
the transport is its outbox: no website, no real record, no email leaves the machine.

Needs the throwaway PostgreSQL (TEST_DATABASE_URL) and a Marketing Agent checkout with its virtual environment
(REAL_MA_CHECKOUT, REAL_MA_PYTHON); CI job "Contract with the real Marketing Agent" sets both. Skipped otherwise.
"""

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from campaign_agent.api.workflow import workflow_status
from campaign_agent.jobs.manager import refresh_applied
from campaign_agent.ma_link.app import create_app
from campaign_agent.ma_link.server import LinkServer
from campaign_agent.ma_link.service import LinkService
from campaign_agent.store.db import Database
from campaign_agent.validators.downstream import Downstream
from tests.e2e.test_acceptance import worker_with
from tests.integration.helpers import enqueue, new_campaign, request_row, state_row, versions
from tests.integration.test_publish import approve, live_version, send, view_of
from tests.unit.helpers import op_json, reply

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

CHECKOUT = os.environ.get("REAL_MA_CHECKOUT", "")
PYTHON = os.environ.get("REAL_MA_PYTHON", "")
DRIVER = Path(__file__).with_name("ma_driver.py")
TOKEN = "e2e-link-token-" + "x" * 40

TEXT = "Find EV charging companies building DC fast chargers on embedded Linux"
FIRST = reply(
    op_json("audience.industries", ["EV charging"], "EV charging companies"),
    op_json("audience.products", ["DC fast chargers"], "DC fast chargers"),
    op_json("audience.technologies", ["embedded Linux"], "on embedded Linux"),
)
EDIT = reply(op_json("exclusions.countries", ["France"], "Skip anyone in France"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def ma_python() -> str:
    if not CHECKOUT or not PYTHON:
        pytest.skip("set REAL_MA_CHECKOUT and REAL_MA_PYTHON to run both agents together")
    return PYTHON


@pytest.fixture
def link(pg_url: str) -> Any:
    port = _free_port()
    server = LinkServer(
        create_app(LinkService(Database(pg_url)), token=TOKEN), bind="127.0.0.1", port=port
    )
    server.start()
    base = f"http://127.0.0.1:{port}/ma/v1"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield base
    server.stop()


def _run_ma(python: str, link_url: str, work: Path, pg_url: str) -> dict[str, Any]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(work),
        "MA_LINK_URL": link_url,
        "MA_LINK_TOKEN": TOKEN,
        "MA_LINK_CACHE_DIR": str(work / "cache"),
        "DATABASE_URL": pg_url,  # only for the MA's report of what it runs (ma_campaign_status)
        "APP_ENV": "dev",
        "EMAIL_SENDING_ENABLED": "false",
        "PYTHONPATH": CHECKOUT,
    }
    done = subprocess.run(  # noqa: S603
        [python, str(DRIVER), str(work)],
        cwd=CHECKOUT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-4000:]
    return dict(json.loads(done.stdout.strip().splitlines()[-1]))


def _status(db: Database, cid: str) -> str:
    row = state_row(db, cid)
    with db.transaction() as c:
        last = c.execute(
            "select request_type, status, error_detail from public.campaign_request where campaign_id = %s "
            "order by created_at desc limit 1",
            (cid,),
        ).fetchone()
    return workflow_status(row["current_state"], row["review_view"], dict(last) if last else None)[
        0
    ]


def _events(db: Database, cid: str, code: str) -> list[dict[str, Any]]:
    with db.transaction() as c:
        rows = c.execute(
            "select code, version_no, repeat_count from public.ma_event where campaign_id = %s and code = %s",
            (cid, code),
        ).fetchall()
    return [dict(r) for r in rows]


def test_both_agents_from_request_to_one_sent_email(
    db: Database, pg_url: str, tmp_path: Path, link: str, ma_python: str
) -> None:
    real_ma = Downstream(Path(CHECKOUT), python=ma_python, timeout_s=120, required=True, isolate=())
    w = worker_with(db, real_ma, FIRST, EDIT)

    # Phase A-B: the dashboard's request; the CA's proposal (checked by the real MA's loader, level 6).
    cid = new_campaign(db, "Two agents", TEXT)
    enqueue(db, cid, "interpret", {"message": TEXT}, key=f"i-{cid}")
    w.run_once()
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW", view_of(db, cid)
    assert _status(db, cid) == "PENDING_CAMPAIGN_APPROVAL"
    first = view_of(db, cid).candidate
    assert first is not None and first.version == 1

    # Nothing approved: the link serves nothing.
    assert (
        httpx.get(
            f"{link}/campaign/active", headers={"Authorization": f"Bearer {TOKEN}"}
        ).status_code
        == 204
    )
    assert httpx.get(f"{link}/campaign/active").status_code == 401

    # Phase C: an edit makes a new version; approving the old one is refused as stale.
    edited = send(
        db,
        w,
        cid,
        "edit",
        "e1",
        {"text": "Skip anyone in France"},
        base=state_row(db, cid)["revision"],
    )
    assert edited["status"] == "done"
    second = view_of(db, cid).candidate
    assert second is not None and second.version == 2 and second.sha256 != first.sha256
    stale = approve(db, w, cid, "a-old", version=1, sha=first.sha256)
    assert (stale["status"], stale["error_code"]) == ("failed", "stale_approval")
    assert live_version(db, cid) is None

    # The right approval, twice (a double click): one request, one live version, who and when recorded.
    ok = approve(db, w, cid, "a2")
    again = approve(db, w, cid, "a2")
    assert ok["status"] == "done" and again["id"] == ok["id"]
    assert live_version(db, cid) == 2
    v2 = next(v for v in versions(db, cid) if v["version_no"] == 2)
    assert v2["approved_by"] == "priya@example.com" and v2["approved_at"] is not None
    assert _status(db, cid) == "CONFIG_READY"  # the MA has not confirmed it yet

    # Phases D-J on the Marketing Agent.
    work = tmp_path / "ma"
    out = _run_ma(ma_python, link, work, pg_url)
    assert (out["campaign_id"], out["version"]) == (cid, 2), out
    assert out["same_config_twice"] is True
    assert out["live_campaign"] == cid
    assert out["lead_campaign"] == [cid, 2], out  # the lead is tied to the exact campaign version
    assert out["lead_status"] in {"PENDING_APPROVAL", "EMAIL_DRAFTED"}, out
    assert out["email_status_before"] == "drafted"
    assert out["sent_before_approval"] == 0  # not approved yet
    assert out["decisions"] == [
        "approved",
        "already_decided",
    ]  # a repeated Slack click changes nothing
    assert out["sent_other_campaign_live"] == 0  # another campaign live: it waits
    assert out["sent"] == [1, 0]  # exactly once
    assert out["email_status_after"] == "sent" and out["outbox_files"] == 1
    assert out["counts"]["companies"] == 1 and out["counts"]["emails"] == 1

    # Back on the CA: the MA's confirmation of the exact version, and its events, arrived.
    refresh_applied(db)
    assert view_of(db, cid).applied is True
    assert _status(db, cid) == "DELIVERED_TO_MA"
    applied = _events(db, cid, "MA_CAMPAIGN_APPLIED")
    assert len(applied) == 1 and applied[0]["version_no"] == 2  # picked up twice, recorded once

    # The MA restarts and runs again (the same delivery seen again): nothing new, nothing sent twice.
    rerun = _run_ma(ma_python, link, work, pg_url)
    assert (rerun["campaign_id"], rerun["version"]) == (cid, 2)
    assert rerun["counts"] == out["counts"]
    assert rerun["decisions"] == ["already_decided", "already_decided"]
    assert rerun["sent"] == [0, 0] and rerun["outbox_files"] == 1
    assert len(_events(db, cid, "MA_CAMPAIGN_APPLIED")) == 1

    # Switching the campaign off: the link serves nothing, and the MA stops running it.
    off = send(db, w, cid, "deactivate", "d1")
    assert off["status"] == "done" and live_version(db, cid) is None
    assert _status(db, cid) == "CANCELLED"
    stopped = _run_ma(ma_python, link, tmp_path / "ma", pg_url)
    assert stopped["campaign_id"] is None and stopped["stopped"] == "no approved campaign is live"
    assert request_row(db, ok["id"])["status"] == "done"

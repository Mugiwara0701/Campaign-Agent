"""The gateway's SQL, run as the real limited `campaign_dashboard` role: it only works if its grants are enough."""

import uuid
from typing import Any

import pytest

from campaign_agent.api.service import Gateway, NotFound
from campaign_agent.store.db import Database
from tests.integration.helpers import new_campaign, worker
from tests.unit.helpers import COMPLETE_REPLY


class AsDashboard(Database):
    """A database connection that acts as the campaign_dashboard role, whatever login the test database uses."""

    def _connection(self) -> Any:
        fresh = self._conn is None or self._conn.closed or self._conn.broken
        conn = super()._connection()
        if fresh:
            conn.execute("set role campaign_dashboard")
            conn.commit()
        return conn


@pytest.fixture
def gateway(pg_url: str, db: Database) -> Gateway:
    return Gateway(pg_url, connect=AsDashboard)


def test_an_interpret_request_is_recorded_and_the_campaign_agent_can_take_it(
    gateway: Gateway, db: Database
) -> None:
    cid = new_campaign(db, detail="Find automotive companies that need Yocto help")
    sent = gateway.send_request(cid, "interpret", {}, None, "priya@example.com", "k1")
    assert sent["status"] == "pending" and sent["duplicate"] is False
    worker(db, COMPLETE_REPLY).run_once()
    view = gateway.campaign(cid)
    assert view["state"] != "NEW" and view["review_view"] is not None
    assert view["last_request"]["status"] == "done"
    assert gateway.request_status(sent["request_id"])["status"] == "done"


def test_the_same_key_is_the_same_request(gateway: Gateway, db: Database) -> None:
    cid = new_campaign(db)
    first = gateway.send_request(cid, "interpret", {}, None, "priya@example.com", "same")
    again = gateway.send_request(cid, "interpret", {}, None, "priya@example.com", "same")
    assert again["duplicate"] is True and again["request_id"] == first["request_id"]


def test_an_unknown_campaign_or_request_is_not_found(gateway: Gateway) -> None:
    with pytest.raises(NotFound):
        gateway.send_request(str(uuid.uuid4()), "interpret", {}, None, "x", None)
    with pytest.raises(NotFound):
        gateway.campaign(str(uuid.uuid4()))
    with pytest.raises(NotFound):
        gateway.request_status(str(uuid.uuid4()))


def test_a_campaign_nobody_started_reads_as_new(gateway: Gateway, db: Database) -> None:
    cid = new_campaign(db)
    assert gateway.campaign(cid)["state"] == "NEW"


def test_pause_and_resume_and_the_status(gateway: Gateway, db: Database) -> None:
    assert gateway.set_ca_state("paused", "priya@example.com")["desired_state"] == "paused"
    status = gateway.ca_status()
    assert (
        status["control"]["desired_state"] == "paused" and status["online"] is False
    )  # no heartbeat in a test database
    assert gateway.set_ca_state("running", "priya@example.com")["desired_state"] == "running"


def test_the_workflow_status_and_the_list_as_the_dashboard_role(
    gateway: Gateway, db: Database
) -> None:
    cid = new_campaign(db, detail="Find automotive companies that need Yocto help")
    fresh = gateway.campaign(cid)
    assert fresh["workflow_status"] == "RECEIVED" and fresh["execution"] is None
    gateway.send_request(cid, "interpret", {}, None, "priya@example.com", "wf")
    worker(db, COMPLETE_REPLY).run_once()
    after = gateway.campaign(cid)
    assert after["workflow_status"] in {"PENDING_CAMPAIGN_APPROVAL", "NEEDS_INPUT"}
    listed = {c["campaign_id"]: c for c in gateway.campaigns(200)}
    assert listed[cid]["workflow_status"] == after["workflow_status"]
    with db.transaction() as conn:
        conn.execute("update public.campaign set deleted_at = now() where id = %s", (cid,))
    assert cid not in {c["campaign_id"] for c in gateway.campaigns(200)}

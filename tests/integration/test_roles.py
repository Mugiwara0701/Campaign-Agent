"""What the two limited roles can and cannot do. Run as the real roles, not by reading grants."""

from contextlib import contextmanager

import psycopg
import pytest

from tests.integration.helpers import enqueue, new_campaign, worker
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT

pytestmark = pytest.mark.integration

DENIED = psycopg.errors.InsufficientPrivilege


@contextmanager
def as_role(pg_url: str, role: str):  # type: ignore[no-untyped-def]
    with psycopg.connect(pg_url) as conn:
        conn.execute(f"set role {role}")
        try:
            yield conn
        finally:
            conn.rollback()


@pytest.fixture
def seeded(db):  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    enqueue(db, cid)
    worker(db, COMPLETE_REPLY).run_once()
    return cid


# --- the dashboard role ---------------------------------------------------------------------------------------------------


def test_the_dashboard_can_ask_and_read_what_is_meant_for_display(pg_url, seeded) -> None:  # type: ignore[no-untyped-def]
    with as_role(pg_url, "campaign_dashboard") as c:
        c.execute(
            "insert into public.campaign_request (campaign_id, request_type, requested_by, idempotency_key, payload)"
            " values (%s, 'discard', 'user', 'from-dashboard', '{}')",
            (seeded,),
        )
        state = c.execute(
            "select current_state, revision, review_view from public.campaign_state where campaign_id = %s",
            (seeded,),
        ).fetchone()
        assert state[0] == "AWAITING_REVIEW"
        assert state[2]["candidate"]["version"] == 1
        assert c.execute("select count(*) from public.campaign_version").fetchone()[0] == 1
        assert c.execute("select count(*) from public.ca_heartbeat").fetchone()[0] == 1
        assert c.execute("select status, error_code from public.campaign_request").fetchall()
        assert c.execute("select campaign_name from public.campaign").fetchone()


@pytest.mark.parametrize(
    "sql",
    [
        "select ir from public.campaign_state",  # the working copy is internal
        "select conversation from public.campaign_state",
        "select payload from public.campaign_request",
        "select * from public.campaign_state",
        "select * from public.emails",  # the Marketing Agent's tables
        "select * from public.pipeline_control",
        "update public.campaign_state set current_state = 'PUBLISHED'",
        "update public.campaign_request set status = 'done'",
        "update public.campaign_version set approved_by = 'me', approved_at = now()",
        "update public.campaign set campaign_name = 'x'",
        "update public.pipeline_control set status = 'running'",
        "delete from public.campaign_request",
        "delete from public.campaign_version",
        "insert into public.campaign_state (campaign_id) select id from public.campaign",
        "insert into public.campaign_version (campaign_id, version_no, overlay_yaml, sha256, ir_hash, contract_version)"
        " select id, 9, 'x', repeat('a', 64), 'h', '1.0' from public.campaign",
        "insert into public.pipeline_control values (2, 'running')",
        "truncate public.campaign_request",
    ],
)
def test_the_dashboard_role_cannot_do_anything_else(pg_url, seeded, sql: str) -> None:  # type: ignore[no-untyped-def]
    with as_role(pg_url, "campaign_dashboard") as c, pytest.raises(DENIED):
        c.execute(sql)


def test_the_dashboard_cannot_forge_a_finished_request(pg_url, seeded) -> None:  # type: ignore[no-untyped-def]
    """Only the columns it needs are grantable: it cannot insert a request that is already 'done'."""
    with as_role(pg_url, "campaign_dashboard") as c, pytest.raises(DENIED):
        c.execute(
            "insert into public.campaign_request (campaign_id, request_type, requested_by, idempotency_key, status)"
            " values (%s, 'discard', 'u', 'forged', 'done')",
            (seeded,),
        )


# --- the agent role ---------------------------------------------------------------------------------------------------------


def test_the_agent_can_do_its_work(pg_url, seeded) -> None:  # type: ignore[no-untyped-def]
    with as_role(pg_url, "campaign_agent") as c:
        assert c.execute("select count(*) from public.campaign").fetchone()[0] == 1
        c.execute("update public.campaign_request set status = 'pending' where false")
        c.execute("update public.campaign_state set current_state = 'AWAITING_REVIEW'")
        c.execute("update public.ca_heartbeat set last_seen = now()")
        row = c.execute(
            "insert into public.campaign_version (campaign_id, version_no, overlay_yaml, sha256, ir_hash, contract_version)"
            " select id, 7, 'x', repeat('b', 64), 'h', '1.0' from public.campaign returning id"
        ).fetchone()
        assert row


@pytest.mark.parametrize(
    "sql",
    [
        "select * from public.emails",
        "select * from public.pipeline_control",
        "update public.pipeline_control set status = 'running'",
        "update public.campaign set campaign_name = 'x'",
        "delete from public.campaign",
        "update public.campaign_version set overlay_yaml = 'tampered'",
        "update public.campaign_version set sha256 = repeat('c', 64)",
        "delete from public.campaign_version",
        "delete from public.campaign_request",
        "drop table public.campaign_state",
        "truncate public.campaign_state",
    ],
)
def test_the_agent_role_cannot_touch_anything_outside_its_job(pg_url, seeded, sql: str) -> None:  # type: ignore[no-untyped-def]
    with (
        as_role(pg_url, "campaign_agent") as c,
        pytest.raises((DENIED, psycopg.errors.UndefinedTable)),
    ):
        c.execute(sql)


def test_an_approved_version_cannot_be_changed_even_by_the_agent(pg_url, seeded) -> None:  # type: ignore[no-untyped-def]
    with as_role(pg_url, "campaign_agent") as c:
        c.execute("update public.campaign_version set approved_by = 'u', approved_at = now()")
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("update public.campaign_version set approved_by = 'someone else'")

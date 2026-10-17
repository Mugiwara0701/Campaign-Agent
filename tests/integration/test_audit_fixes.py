"""Fixes from the 2026-10-17 audit, on a real database: versions are approved only through ca_publish (F-14), and a
campaign the dashboard deleted is never served and is switched off (F-10)."""

import psycopg
import pytest

from campaign_agent.jobs import manager
from campaign_agent.jobs.manager import reconcile_deleted
from campaign_agent.ma_link.service import LinkService
from campaign_agent.store import repositories as repo
from tests.integration.helpers import (
    enqueue,
    new_campaign,
    request_row,
    state_row,
    versions,
    worker,
)
from tests.integration.test_publish import approve, reach_review
from tests.integration.test_roles import DENIED, as_role, seeded  # noqa: F401 - the fixture is used by name
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT

pytestmark = pytest.mark.integration


def test_the_agent_role_cannot_insert_an_approved_version(pg_url, seeded) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    with as_role(pg_url, "campaign_agent") as c, pytest.raises(DENIED):
        c.execute(
            "insert into public.campaign_version (campaign_id, version_no, overlay_yaml, sha256, ir_hash,"
            " contract_version, approved_by, approved_at)"
            " select id, 8, 'x', repeat('d', 64), 'h', '1.0', 'nobody', now() from public.campaign"
        )


def test_nobody_inserts_an_approved_version(pg_url, seeded) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    with psycopg.connect(pg_url) as owner, pytest.raises(psycopg.errors.RaiseException):
        owner.execute(
            "insert into public.campaign_version (campaign_id, version_no, overlay_yaml, sha256, ir_hash,"
            " contract_version, approved_at) select id, 8, 'x', repeat('d', 64), 'h', '1.0', now() from public.campaign"
        )


def test_a_deleted_campaign_is_not_served_and_is_switched_off(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    service = LinkService(db)
    assert service.live_campaign() is not None
    with db.transaction() as conn:
        conn.execute("update public.campaign set deleted_at = now() where id = %s", (cid,))
    assert service.live_campaign() is None  # never served, even before the reconcile
    assert reconcile_deleted(db) == [cid]
    assert state_row(db, cid)["active_version_id"] is None
    assert reconcile_deleted(db) == []  # once


def test_a_request_taken_over_by_another_worker_is_not_stored_twice(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """F-20: the lease ran out and another worker claimed the request while this one was still on it."""
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    req = enqueue(db, cid)
    real = manager.handle

    def slow_then_taken_over(r, ws, svc):  # type: ignore[no-untyped-def]
        with db.transaction() as conn:
            conn.execute(
                "update public.campaign_request set claimed_by = 'other-worker' where id = %s",
                (r.id,),
            )
        return real(r, ws, svc)

    monkeypatch.setattr(manager, "handle", slow_then_taken_over)
    assert worker(db, COMPLETE_REPLY).run_once()
    row = request_row(db, req["id"])
    assert (
        row["status"] == "processing" and row["claimed_by"] == "other-worker"
    )  # left to the new owner
    assert versions(db, cid) == []  # nothing stored by the worker that lost it


def test_the_heartbeat_renews_the_lease_of_the_running_request(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    req = enqueue(db, cid)
    with db.transaction() as conn:
        claimed = repo.claim_request(conn, "w1", 60)
        assert claimed is not None and str(claimed["id"]) == str(req["id"])
        before = claimed["lease_until"]
        assert repo.renew_lease(conn, "w1", 3600) == 1
        assert repo.renew_lease(conn, "someone-else", 3600) == 0
    assert request_row(db, req["id"])["lease_until"] > before


def test_the_office_window_works_only_on_its_own_test_campaigns(db) -> None:  # type: ignore[no-untyped-def]
    """F-13: a real campaign is changed and approved on the dashboard, with the signed-in person on every request."""
    from campaign_agent import console_flow
    from campaign_agent.devclient import TEST_PREFIX

    real = new_campaign(db, name="Real campaign")
    with pytest.raises(ValueError, match="dashboard"):
        console_flow._enqueue(db, real, "discard")
    test = new_campaign(db, name=f"{TEST_PREFIX}window test")
    console_flow._enqueue(db, test, "discard")  # its own test campaign: fine

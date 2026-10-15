"""The Campaign Agent starts on a campaign the dashboard saved but sent no request for (migration 0021)."""

from typing import Any

from campaign_agent.store import repositories as repo
from tests.integration.helpers import enqueue, new_campaign, state_row, worker
from tests.unit.helpers import COMPLETE_REPLY


def sweep(db, grace_s: int = 0, max_age_h: int = 168) -> list[str]:  # type: ignore[no-untyped-def]
    with db.transaction() as conn:
        found = repo.auto_interpret_new_campaigns(conn, grace_s, max_age_h)
    assert found is not None
    return found


def requests_of(db, cid: str) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    with db.transaction() as conn:
        return conn.execute(
            "select request_type, requested_by, status from public.campaign_request where campaign_id = %s",
            (cid,),
        ).fetchall()


def test_a_new_campaign_with_no_request_gets_one_interpret_request_and_only_one(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail="find companies that build EV chargers")
    assert sweep(db) == [cid]
    assert sweep(db) == []  # asked once: it now has a request
    rows = requests_of(db, cid)
    assert (
        len(rows) == 1
        and rows[0]["request_type"] == "interpret"
        and rows[0]["requested_by"].startswith("campaign-agent")
    )


def test_a_campaign_that_already_has_a_request_is_left_alone(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    enqueue(db, cid, "interpret", {"message": "Find robotics companies"}, key="dashboard-1")
    assert sweep(db) == []
    assert len(requests_of(db, cid)) == 1


def test_a_discarded_or_finished_campaign_is_never_revived(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    w = worker(db, COMPLETE_REPLY)
    enqueue(db, cid, "interpret", {"message": "Find automotive companies"}, key="k1")
    w.run_once()
    with (
        db.transaction() as conn
    ):  # the person's own request rows are gone (for example cleaned up), the state is not
        conn.execute("delete from public.campaign_request where campaign_id = %s", (cid,))
    assert sweep(db) == []


def test_the_dashboards_own_request_wins_inside_the_grace_period(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    assert sweep(db, grace_s=600) == []  # saved a moment ago: wait for the dashboard's own request
    enqueue(db, cid, "interpret", {}, key="dashboard-1")
    assert sweep(db, grace_s=0) == []
    assert len(requests_of(db, cid)) == 1


def test_a_campaign_without_text_is_skipped(db) -> None:  # type: ignore[no-untyped-def]
    new_campaign(db, detail="   ")
    assert sweep(db) == []


def test_an_old_campaign_is_not_suddenly_interpreted(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    with db.transaction() as conn:
        conn.execute(
            "update public.campaign set created_at = now() - interval '30 days' where id = %s",
            (cid,),
        )
    assert sweep(db, max_age_h=168) == []
    assert sweep(db, max_age_h=24 * 60) == [cid]


def test_a_campaign_the_dashboard_deleted_is_not_started(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    with db.transaction() as conn:
        conn.execute("update public.campaign set deleted_at = now() where id = %s", (cid,))
    assert sweep(db) == []
    assert requests_of(db, cid) == []


def test_the_worker_starts_on_it_by_itself_and_the_manager_reads_it(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(
        db,
        detail="Find automotive and industrial automation companies in Germany that need Yocto help",
    )
    w = worker(db, COMPLETE_REPLY, cfg=_cfg(grace=0))
    w.run_once()  # idle: notices the campaign, sends the request
    w.run_once()  # takes it
    assert state_row(db, cid)["current_state"] != "DRAFT"
    assert any(r["request_type"] == "interpret" for r in requests_of(db, cid))


def _cfg(grace: int):  # type: ignore[no-untyped-def]
    from campaign_agent.jobs.manager import ManagerConfig

    return ManagerConfig(
        worker_id="t", lease_s=60, max_attempts=3, auto_interpret=True, auto_interpret_grace_s=grace
    )

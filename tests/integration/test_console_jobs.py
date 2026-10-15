"""The office window shows requests that come from the dashboard: the newest ones, with the campaign's name and state."""

from campaign_agent import console
from tests.integration.helpers import enqueue, new_campaign, worker
from tests.unit.helpers import COMPLETE_REPLY


def jobs(db):  # type: ignore[no-untyped-def]
    with db.transaction() as conn:
        return console._jobs(conn)


def test_a_request_from_the_dashboard_is_listed_with_the_campaign_name(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, name="EV charger", detail="find companies that build EV chargers")
    with db.transaction() as conn:
        conn.execute(
            "insert into public.campaign_request (campaign_id, request_type, payload, requested_by, idempotency_key) "
            "values (%s, 'interpret', '{}', 'priya@example.com', 'k1')",
            (cid,),
        )
    [job] = jobs(db)
    assert (job["type"], job["status"], job["by"], job["name"]) == (
        "interpret",
        "pending",
        "priya@example.com",
        "EV charger",
    )
    assert job["state"] is None and job["age_s"] >= 0 and job["error"] is None


def test_the_job_shows_where_the_campaign_got_to(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    enqueue(db, cid, "interpret", {"message": "Find automotive companies"}, key="k1")
    worker(db, COMPLETE_REPLY).run_once()
    [job] = jobs(db)
    assert job["status"] == "done" and job["state"] in ("AWAITING_REVIEW", "CLARIFICATION_REQUIRED")


def test_only_the_newest_are_listed(db) -> None:  # type: ignore[no-untyped-def]
    for n in range(8):
        enqueue(db, new_campaign(db, name=f"c{n}"), "interpret", {}, key=f"k{n}")
    assert len(jobs(db)) == 6

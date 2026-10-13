"""The rate limit and the retention clean-up, on a real database."""

import pytest

from campaign_agent.jobs.manager import ManagerConfig
from campaign_agent.store import repositories as repo
from campaign_agent.store.retention import clear_old_payloads, count_clearable, run_retention
from tests.integration.helpers import enqueue, new_campaign, request_row, worker
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT

pytestmark = pytest.mark.integration


def cfg(limit: int) -> ManagerConfig:
    return ManagerConfig("test-worker", 60, 3, rate_limit_per_minute=limit)


def three_requests(db):  # type: ignore[no-untyped-def]
    ids = []
    for i in range(3):
        cid = new_campaign(db, f"Auto {i}", COMPLETE_TEXT)
        ids.append(enqueue(db, cid, key=f"k{i}")["id"])
    return ids


def test_the_third_request_in_a_minute_is_refused_when_the_limit_is_two(db) -> None:  # type: ignore[no-untyped-def]
    ids = three_requests(db)
    w = worker(db, COMPLETE_REPLY, cfg=cfg(2))
    for _ in range(3):
        w.run_once()
    rows = [request_row(db, i) for i in ids]
    assert [r["status"] for r in rows] == ["done", "done", "failed"]
    assert rows[2]["error_code"] == "rate_limited"
    assert "wait a minute" in rows[2]["error_detail"]


def test_the_limit_is_per_requester(db) -> None:  # type: ignore[no-untyped-def]
    ids = three_requests(db)
    other = new_campaign(db, "Other", COMPLETE_TEXT)
    with db.transaction() as c:
        row = repo.enqueue_request(
            c, repo.NewRequest(other, "interpret", "someone-else@example.com", "o1", {}, None)
        )
    w = worker(db, COMPLETE_REPLY, cfg=cfg(2))
    for _ in range(4):
        w.run_once()
    assert request_row(db, ids[2])["error_code"] == "rate_limited"
    assert request_row(db, row["id"])["status"] == "done"


def test_a_limit_of_zero_means_no_limit(db) -> None:  # type: ignore[no-untyped-def]
    ids = three_requests(db)
    w = worker(db, COMPLETE_REPLY, cfg=cfg(0))
    for _ in range(3):
        w.run_once()
    assert [request_row(db, i)["status"] for i in ids] == ["done"] * 3


def old(db, request_id, days=100):  # type: ignore[no-untyped-def]
    with db.transaction() as c:
        c.execute(
            "update public.campaign_request set finished_at = now() - make_interval(days => %s) where id = %s",
            (days, request_id),
        )


def test_old_request_text_is_cleared_and_everything_else_is_kept(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, "Auto", COMPLETE_TEXT)
    old_text = enqueue(db, cid, "interpret", {"message": COMPLETE_TEXT}, key="a")
    recent = enqueue(db, cid, "edit", {"text": "also skip France"}, key="b")
    w = worker(db, COMPLETE_REPLY, COMPLETE_REPLY, cfg=cfg(0))
    w.run_once()
    w.run_once()
    with db.transaction() as c:
        rollback = repo.enqueue_request(
            c, repo.NewRequest(cid, "rollback", "u", "r", {"version": 1}, None)
        )
        c.execute(
            "update public.campaign_request set status = 'done', finished_at = now() - interval '200 days'"
            " where id = %s",
            (rollback["id"],),
        )
    old(db, old_text["id"])

    assert count_clearable(db, 90) == 1
    assert clear_old_payloads(db, 90) == 1
    assert count_clearable(db, 90) == 0
    assert request_row(db, old_text["id"])["payload"] == {}
    assert request_row(db, old_text["id"])["status"] == "done"  # the row and its outcome stay
    assert request_row(db, recent["id"])["payload"] == {"text": "also skip France"}
    assert request_row(db, rollback["id"])["payload"] == {"version": 1}  # audit rows keep theirs


def test_the_command_only_counts_without_yes_and_clears_with_it(db, pg_url) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, "Auto", COMPLETE_TEXT)
    req = enqueue(db, cid, "interpret", {"message": COMPLETE_TEXT}, key="a")
    worker(db, COMPLETE_REPLY, cfg=cfg(0)).run_once()
    old(db, req["id"])
    lines: list[str] = []
    assert run_retention(pg_url, 90, apply=False, out=lines.append) == 0
    assert any("1 finished" in ln for ln in lines) and any("Nothing changed" in ln for ln in lines)
    assert request_row(db, req["id"])["payload"] != {}
    assert run_retention(pg_url, 90, apply=True, out=lines.append) == 0
    assert request_row(db, req["id"])["payload"] == {}
    assert not any("postgres:" in ln and ":test" in ln for ln in lines)  # the password is never printed

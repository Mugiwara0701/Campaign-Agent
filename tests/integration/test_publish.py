"""Approval, going live, stopping and going back, on a real database."""

import psycopg
import pytest

from campaign_agent.store import repositories as repo
from campaign_agent.view import ReviewView
from tests.integration.helpers import (
    enqueue,
    new_campaign,
    request_row,
    state_row,
    versions,
    worker,
)
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT, op_json, reply

pytestmark = pytest.mark.integration

SECOND = reply(op_json("qualification.min_qualify", 55, "minimum score 55", "set"))
THIRD = reply(op_json("exclusions.companies", ["acme"], "skip acme", "add"))


def view_of(db, cid) -> ReviewView:  # type: ignore[no-untyped-def]
    return ReviewView.model_validate(state_row(db, cid)["review_view"])


def reach_review(db, w, name="Auto", text=COMPLETE_TEXT):  # type: ignore[no-untyped-def]
    cid = new_campaign(db, name, text)
    enqueue(db, cid, key=f"interpret-{cid}")
    w.run_once()
    return cid


def approve(db, w, cid, key, version=None, sha=None, by="priya@example.com"):  # type: ignore[no-untyped-def]
    cand = view_of(db, cid).candidate
    payload = {"version": version or cand.version, "sha256": sha or cand.sha256}
    with db.transaction() as c:
        row = repo.enqueue_request(c, repo.NewRequest(cid, "approve", by, key, payload))
    w.run_once()
    return request_row(db, row["id"])


def send(db, w, cid, kind, key, payload=None, base=None):  # type: ignore[no-untyped-def]
    row = enqueue(db, cid, kind, payload or {}, key=key, base=base)
    w.run_once()
    return request_row(db, row["id"])


def live_version(db, cid):  # type: ignore[no-untyped-def]
    row = state_row(db, cid)
    if row["active_version_id"] is None:
        return None
    return next(v["version_no"] for v in versions(db, cid) if v["id"] == row["active_version_id"])


# --- publishing -----------------------------------------------------------------------------------------------------------


def test_approving_the_reviewed_candidate_makes_it_live_and_records_who(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    assert live_version(db, cid) is None  # reviewed, not live
    row = approve(db, w, cid, "a1")
    assert (row["status"], row["error_code"]) == ("done", None)
    assert row["result"]["published"] is True and row["result"]["effective_from"] == "next_pass"

    state = state_row(db, cid)
    assert state["current_state"] == "PUBLISHED"
    assert live_version(db, cid) == 1
    v = versions(db, cid)[0]
    assert (v["approved_by"], v["approved_at"] is not None) == ("priya@example.com", True)
    view = view_of(db, cid)
    assert (view.live, view.live_version, view.state) == (True, 1, "PUBLISHED")
    assert [(x.version, x.live, x.approved_by) for x in view.versions] == [
        (1, True, "priya@example.com")
    ]
    assert (
        view.candidate is not None and view.candidate.sha256 == v["sha256"]
    )  # the live YAML stays visible
    assert v["ir_snapshot"]["audience"]["industries"] == ["automotive"]


def test_a_repeated_approval_does_not_approve_twice(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1", by="priya@example.com")
    again = approve(db, w, cid, "a2", by="someone-else@example.com")
    assert (again["status"], again["result"]["already"]) == ("done", True)
    v = versions(db, cid)[0]
    assert v["approved_by"] == "priya@example.com"  # the first approver stands
    assert live_version(db, cid) == 1


def test_an_approval_of_an_old_candidate_is_refused_and_shows_the_current_one(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, SECOND)
    cid = reach_review(db, w)
    old = view_of(db, cid).candidate
    send(
        db, w, cid, "edit", "e1", {"text": "minimum score 55"}, base=state_row(db, cid)["revision"]
    )
    new = view_of(db, cid).candidate
    assert (old.version, new.version) == (1, 2)

    row = approve(db, w, cid, "a1", version=1, sha=old.sha256)
    assert (row["status"], row["error_code"]) == ("failed", "stale_approval")
    assert row["result"]["current"] == {"version": 2, "sha256": new.sha256}
    assert live_version(db, cid) is None
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"
    assert view_of(db, cid).candidate.version == 2  # type: ignore[union-attr]
    assert versions(db, cid)[0]["approved_at"] is None  # nothing was approved

    assert approve(db, w, cid, "a2")["status"] == "done"  # the current one goes through
    assert live_version(db, cid) == 2


def test_a_changed_text_is_refused_even_for_the_right_version(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    row = approve(db, w, cid, "a1", sha="0" * 64)
    assert row["error_code"] == "stale_approval"
    assert live_version(db, cid) is None


def test_approving_after_a_discard_is_refused(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    cand = view_of(db, cid).candidate
    send(db, w, cid, "discard", "d1")
    row = approve(db, w, cid, "a1", version=cand.version, sha=cand.sha256)
    assert row["error_code"] == "wrong_state"
    assert live_version(db, cid) is None


def test_a_failed_store_leaves_nothing_half_done_and_can_be_retried(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    real = repo.publish
    calls = {"n": 0}

    def flaky(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError("connection lost")
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "publish", flaky)
    failed = approve(db, w, cid, "a1")
    assert (failed["status"], failed["error_code"]) == ("failed", "publish_failed")
    assert state_row(db, cid)["current_state"] == "PUBLISH_FAILED"
    assert live_version(db, cid) is None
    assert versions(db, cid)[0]["approved_at"] is None  # the approval rolled back with it
    assert view_of(db, cid).last_request.code == "publish_failed"  # type: ignore[union-attr]

    retried = approve(db, w, cid, "a2")  # the same approval, again
    assert retried["status"] == "done"
    assert state_row(db, cid)["current_state"] == "PUBLISHED"
    assert live_version(db, cid) == 1


def test_a_candidate_that_stops_passing_is_blocked_and_nothing_goes_live(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    cand = view_of(db, cid).candidate
    with db.transaction() as c:  # the base config moved on: a stored overlay that breaks a rule now
        c.execute("alter table public.campaign_version disable trigger campaign_version_guard")
        c.execute(
            "update public.campaign_version set overlay_yaml = %s, sha256 = %s where campaign_id = %s",
            ("config_version: '1.0'\nqualification:\n    min_qualify: 90\n", "d" * 64, cid),
        )
        c.execute("alter table public.campaign_version enable trigger campaign_version_guard")
    row = approve(db, w, cid, "a1", version=cand.version, sha="d" * 64)
    assert (row["status"], row["error_code"]) == ("failed", "publish_blocked")
    assert state_row(db, cid)["current_state"] == "VALIDATION_FAILED"
    assert live_version(db, cid) is None


def test_a_change_after_publishing_leaves_the_live_version_running(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, SECOND)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    send(
        db, w, cid, "edit", "e1", {"text": "minimum score 55"}, base=state_row(db, cid)["revision"]
    )
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"
    assert live_version(db, cid) == 1  # version 1 runs until version 2 is approved
    view = view_of(db, cid)
    assert (view.live_version, view.candidate.version) == (1, 2)  # type: ignore[union-attr]
    approve(db, w, cid, "a2")
    assert live_version(db, cid) == 2
    assert [v.approved_by for v in view_of(db, cid).versions] == ["priya@example.com"] * 2

    discarded = send(
        db,
        w,
        cid,
        "edit",
        "e2",
        {"ops": [{"path": "audience.products", "op": "add", "value": ["kiosk"]}]},
        base=state_row(db, cid)["revision"],
    )
    assert discarded["status"] == "done"
    send(db, w, cid, "discard", "d1")
    assert (
        state_row(db, cid)["current_state"] == "PUBLISHED"
    )  # a pending change is dropped, the live one stays
    assert live_version(db, cid) == 2


# --- one live campaign --------------------------------------------------------------------------------------------------------


def test_publishing_a_second_campaign_replaces_the_first(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, COMPLETE_REPLY)
    a = reach_review(db, w, "A")
    b = reach_review(db, w, "B")
    approve(db, w, a, "a1")
    assert (live_version(db, a), live_version(db, b)) == (1, None)
    approve(db, w, b, "b1")
    assert (live_version(db, a), live_version(db, b)) == (None, 1)
    assert (view_of(db, a).live, view_of(db, b).live) == (
        False,
        True,
    )  # A's screen no longer says "live"
    assert (
        state_row(db, a)["current_state"] == "PUBLISHED"
    )  # still has a published version, just not live
    assert versions(db, a)[0]["approved_at"] is not None  # and its history is intact


def test_the_database_itself_allows_only_one_live_campaign(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, COMPLETE_REPLY)
    a, b = reach_review(db, w, "A"), reach_review(db, w, "B")
    with db.transaction() as c:  # even as the owner, bypassing the functions
        c.execute(
            "update public.campaign_state set active_version_id = (select id from public.campaign_version where campaign_id = %s)"
            " where campaign_id = %s",
            (a, a),
        )
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction() as c:
        c.execute(
            "update public.campaign_state set active_version_id = (select id from public.campaign_version where campaign_id = %s)"
            " where campaign_id = %s",
            (b, b),
        )


# --- going back and stopping --------------------------------------------------------------------------------------------------


def test_an_earlier_approved_version_can_go_live_again(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, SECOND)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    send(
        db, w, cid, "edit", "e1", {"text": "minimum score 55"}, base=state_row(db, cid)["revision"]
    )
    approve(db, w, cid, "a2")
    assert live_version(db, cid) == 2

    back = send(db, w, cid, "rollback", "r1", {"version": 1})
    assert (back["status"], back["result"]["live_version"]) == ("done", 1)
    assert live_version(db, cid) == 1
    assert view_of(db, cid).live_version == 1
    assert state_row(db, cid)["current_state"] == "PUBLISHED"  # the conversation is untouched
    assert send(db, w, cid, "rollback", "r2", {"version": 1})["result"]["already"] is True


def test_a_version_that_was_never_approved_cannot_be_made_live(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, SECOND)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    send(
        db, w, cid, "edit", "e1", {"text": "minimum score 55"}, base=state_row(db, cid)["revision"]
    )  # v2: a candidate
    row = send(db, w, cid, "rollback", "r1", {"version": 2})
    assert row["error_code"] == "wrong_state"
    assert live_version(db, cid) == 1


def test_going_back_to_another_campaign_takes_over_from_the_live_one(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, COMPLETE_REPLY)
    a, b = reach_review(db, w, "A"), reach_review(db, w, "B")
    approve(db, w, a, "a1")
    approve(db, w, b, "b1")
    send(db, w, a, "rollback", "r1", {"version": 1})
    assert (live_version(db, a), live_version(db, b)) == (1, None)
    assert (view_of(db, a).live, view_of(db, b).live) == (True, False)


def test_stopping_a_campaign_keeps_its_versions(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    row = send(db, w, cid, "deactivate", "s1")
    assert (row["status"], row["result"]["live_version"]) == ("done", None)
    assert live_version(db, cid) is None
    view = view_of(db, cid)
    assert (view.live, view.versions[0].approved_by) == (False, "priya@example.com")
    assert send(db, w, cid, "deactivate", "s2")["result"]["already"] is True
    assert (
        send(db, w, cid, "rollback", "r1", {"version": 1})["status"] == "done"
    )  # and it can start again
    assert live_version(db, cid) == 1


# --- audit and immutability ---------------------------------------------------------------------------------------------------


def test_every_change_of_what_is_live_leaves_a_request_row_as_the_audit_trail(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1", by="priya@example.com")
    send(db, w, cid, "deactivate", "s1")
    with db.transaction() as c:
        rows = c.execute(
            "select request_type, requested_by, status, finished_at is not null as finished"
            " from public.campaign_request where request_type in ('approve', 'deactivate') order by created_at"
        ).fetchall()
    assert [(r["request_type"], r["status"], r["finished"]) for r in rows] == [
        ("approve", "done", True),
        ("deactivate", "done", True),
    ]
    assert rows[0]["requested_by"] == "priya@example.com"


def test_a_stored_version_cannot_be_changed_by_anyone(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    for column, value in (
        ("overlay_yaml", "'x'"),
        ("sha256", "repeat('e', 64)"),
        ("ir_snapshot", "'{}'::jsonb"),
        ("version_no", "9"),
    ):
        with pytest.raises(psycopg.errors.RaiseException), db.transaction() as c:
            c.execute(
                f"update public.campaign_version set {column} = {value} where campaign_id = %s",  # noqa: S608
                (cid,),
            )
    approve(db, w, cid, "a1")
    with pytest.raises(psycopg.errors.RaiseException), db.transaction() as c:
        c.execute(
            "update public.campaign_version set approved_by = 'someone else' where campaign_id = %s",
            (cid,),
        )

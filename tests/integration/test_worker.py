"""The whole path on a real database: request in, state, versions and result out."""

import pytest

from campaign_agent.conversation.state import ConversationState
from campaign_agent.ir.models import CampaignIR
from campaign_agent.llm.adapter import LLMTimeout
from campaign_agent.view import ReviewView
from tests.integration.helpers import (
    CFG,
    enqueue,
    new_campaign,
    request_row,
    state_row,
    versions,
    worker,
)
from tests.unit import fake_secrets as fs
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT, VAGUE_TEXT, op_json, reply

pytestmark = pytest.mark.integration


def test_a_clear_message_is_stored_as_a_reviewable_candidate(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    req = enqueue(db, cid)
    assert worker(db, COMPLETE_REPLY).run_once() is True

    row = request_row(db, req["id"])
    assert (row["status"], row["error_code"]) == ("done", None)
    assert row["result"]["state"] == "AWAITING_REVIEW"
    assert row["finished_at"] is not None and row["lease_until"] is None

    state = state_row(db, cid)
    assert state["current_state"] == "AWAITING_REVIEW"
    ir = CampaignIR.model_validate(state["ir"])
    assert state["revision"] == ir.meta.revision >= 1
    assert ir.audience.industries == ["automotive"]
    assert ConversationState.model_validate(state["conversation"]).state.value == "AWAITING_REVIEW"

    stored = versions(db, cid)
    assert len(stored) == 1
    v = stored[0]
    assert v["version_no"] == 1 and v["approved_at"] is None  # a candidate, not approved
    assert v["sha256"] == row["result"]["candidate_sha256"]
    assert state["candidate_version_id"] == v["id"]
    assert state["active_version_id"] is None  # nothing is published
    assert "automotive" in v["overlay_yaml"]
    assert [lv["status"] for lv in v["validation_report"]["levels"]] == [
        "passed",
        "passed",
        "passed",
        "skipped",
    ]

    view = ReviewView.model_validate(state["review_view"])
    assert view.candidate is not None and view.candidate.version == 1
    assert view.candidate.sha256 == v["sha256"]
    assert view.state == "AWAITING_REVIEW"


def test_the_dashboard_sees_the_state_while_the_model_works(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    enqueue(db, cid)
    seen: list[str] = []

    def peek(_messages) -> str:  # type: ignore[no-untyped-def]
        seen.append(
            state_row(db, cid)["current_state"]
        )  # read through another transaction mid-request
        return COMPLETE_REPLY

    worker(db, peek).run_once()
    assert seen == ["INTERPRETING"]
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"


def test_a_vague_message_stores_the_questions_and_no_version(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=VAGUE_TEXT)
    enqueue(db, cid)
    worker(db, reply()).run_once()
    state = state_row(db, cid)
    assert state["current_state"] == "CLARIFICATION_REQUIRED"
    view = ReviewView.model_validate(state["review_view"])
    assert len(view.questions) == 2
    assert versions(db, cid) == []
    assert state["candidate_version_id"] is None


def test_answering_then_editing_builds_versions_in_order(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=VAGUE_TEXT)
    enqueue(db, cid, key="first")
    w = worker(
        db, reply(), reply(op_json("qualification.min_qualify", 55, "minimum score 55", "set"))
    )
    w.run_once()
    questions = ReviewView.model_validate(state_row(db, cid)["review_view"]).questions
    answers = [{"question_id": q.id, "value": "c0"} for q in questions]
    enqueue(db, cid, "answer", {"answers": answers}, key="answers")
    w.run_once()
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"
    assert [v["version_no"] for v in versions(db, cid)] == [1]

    revision = state_row(db, cid)["revision"]
    enqueue(db, cid, "edit", {"text": "minimum score 55"}, key="edit", base=revision)
    w.run_once()
    stored = versions(db, cid)
    assert [v["version_no"] for v in stored] == [1, 2]
    assert "min_qualify: 55" in stored[1]["overlay_yaml"]
    assert "min_qualify" not in stored[0]["overlay_yaml"]
    assert state_row(db, cid)["candidate_version_id"] == stored[1]["id"]
    assert state_row(db, cid)["revision"] > revision


def test_a_change_that_does_not_change_the_yaml_does_not_add_a_version(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    enqueue(db, cid, key="1")
    w = worker(db, COMPLETE_REPLY)
    w.run_once()
    revision = state_row(db, cid)["revision"]
    ops = [{"path": "audience.industries", "op": "add", "value": ["automotive"]}]  # already there
    enqueue(db, cid, "edit", {"ops": ops}, key="2", base=revision)
    w.run_once()
    assert len(versions(db, cid)) == 1
    assert state_row(db, cid)["revision"] == revision


# --- refusals leave the campaign alone ------------------------------------------------------------------------------


def test_a_stale_edit_fails_with_a_code_and_changes_nothing(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    enqueue(db, cid, key="1")
    w = worker(db, COMPLETE_REPLY)
    w.run_once()
    before = state_row(db, cid)
    stale = enqueue(db, cid, "edit", {"text": "x"}, key="2", base=before["revision"] - 1)
    w.run_once()
    row = request_row(db, stale["id"])
    assert (row["status"], row["error_code"]) == ("failed", "stale_revision")
    assert row["result"]["revision"] == before["revision"]
    after = state_row(db, cid)
    assert (after["current_state"], after["revision"], after["ir"]) == (
        before["current_state"],
        before["revision"],
        before["ir"],
    )
    assert ReviewView.model_validate(after["review_view"]).candidate is not None  # preview kept
    assert ReviewView.model_validate(after["review_view"]).last_request.code == "stale_revision"  # type: ignore[union-attr]


def test_an_edit_without_a_revision_fails(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    enqueue(db, cid, key="1")
    w = worker(db, COMPLETE_REPLY)
    w.run_once()
    req = enqueue(db, cid, "edit", {"text": "x"}, key="2")
    w.run_once()
    assert request_row(db, req["id"])["error_code"] == "missing_base_revision"


def test_a_secret_in_the_message_fails_without_calling_the_model(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail="my key " + fs.AWS)
    req = enqueue(db, cid)
    w = worker(db, COMPLETE_REPLY)
    w.run_once()
    row = request_row(db, req["id"])
    assert (row["status"], row["error_code"]) == ("failed", "input_rejected")
    assert fs.AWS not in str(row["error_detail"]) + str(row["result"])
    assert w._svc.extractor._adapter.calls == []  # type: ignore[attr-defined]
    assert state_row(db, cid)["current_state"] == "DRAFT"


def test_a_model_that_is_down_fails_the_request_and_can_be_retried(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    first = enqueue(db, cid, key="1")
    w = worker(db, LLMTimeout("t"), LLMTimeout("t"), COMPLETE_REPLY)
    w.run_once()
    assert request_row(db, first["id"])["error_code"] == "model_unavailable"
    assert state_row(db, cid)["current_state"] == "MODEL_UNAVAILABLE"
    again = enqueue(db, cid, key="2")
    w.run_once()
    assert request_row(db, again["id"])["status"] == "done"
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"


def test_discard_and_a_discarded_campaign(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    enqueue(db, cid, key="1")
    w = worker(db, COMPLETE_REPLY)
    w.run_once()
    enqueue(db, cid, "discard", key="2")
    w.run_once()
    assert state_row(db, cid)["current_state"] == "DISCARDED"
    late = enqueue(db, cid, "edit", {"ops": []}, key="3", base=state_row(db, cid)["revision"])
    w.run_once()
    assert request_row(db, late["id"])["error_code"] == "wrong_state"


# --- crashes ---------------------------------------------------------------------------------------------------------


def test_a_request_abandoned_by_a_crashed_worker_is_run_by_the_next_one(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    req = enqueue(db, cid)
    with db.transaction() as c:  # a worker claimed it, showed "working", then died
        c.execute(
            "update public.campaign_request set status='processing', claimed_by='dead', attempts=1,"
            " lease_until = now() - interval '1 minute' where id = %s",
            (req["id"],),
        )
        c.execute(
            "insert into public.campaign_state (campaign_id, current_state) values (%s, 'INTERPRETING')",
            (cid,),
        )
    worker(db, COMPLETE_REPLY).run_once()
    row = request_row(db, req["id"])
    assert (row["status"], row["attempts"]) == ("done", 2)
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"
    assert len(versions(db, cid)) == 1  # exactly one candidate, nothing half-applied


def test_a_bug_inside_a_request_never_leaves_it_half_applied(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    req = enqueue(db, cid)

    def boom(_messages) -> str:  # type: ignore[no-untyped-def]
        raise RuntimeError("bug")

    w = worker(db, boom)
    for expected in ("pending", "pending", "failed"):
        w.run_once()
        assert request_row(db, req["id"])["status"] == expected
    row = request_row(db, req["id"])
    assert (row["error_code"], row["attempts"]) == ("internal_error", CFG.max_attempts)
    assert versions(db, cid) == []
    assert state_row(db, cid)["revision"] == 0


def test_a_campaign_that_is_deleted_takes_its_requests_and_state_with_it(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db, detail=COMPLETE_TEXT)
    enqueue(db, cid)
    worker(db, COMPLETE_REPLY).run_once()
    with db.transaction() as c:
        c.execute("delete from public.campaign where id = %s", (cid,))
        counts = [
            c.execute(f"select count(*) as n from public.{t}").fetchone()["n"]  # noqa: S608
            for t in ("campaign_request", "campaign_state", "campaign_version")
        ]
    assert counts == [0, 0, 0]


# --- heartbeat and the service loop ------------------------------------------------------------------------------------


def test_the_heartbeat_is_one_row_that_keeps_moving(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db)
    w.run_once()
    with db.transaction() as c:
        first = c.execute("select * from public.ca_heartbeat").fetchone()
    w.run_once()
    with db.transaction() as c:
        rows = c.execute("select * from public.ca_heartbeat").fetchall()
    assert len(rows) == 1
    assert rows[0]["last_seen"] > first["last_seen"]
    assert (rows[0]["agent_status"], rows[0]["model_name"]) == ("idle", "stub")
    assert rows[0]["agent_version"]


def test_run_forever_works_through_the_queue_and_stops_on_request(db) -> None:  # type: ignore[no-untyped-def]
    ids = [
        enqueue(db, new_campaign(db, f"c{i}", COMPLETE_TEXT), key=str(i))["id"] for i in range(3)
    ]
    w = worker(db, COMPLETE_REPLY)
    idle = 0

    def stop_when_idle(_seconds: float) -> None:
        nonlocal idle
        idle += 1
        w.stop()

    w._sleep = stop_when_idle  # type: ignore[assignment]
    w.run_forever()
    assert [request_row(db, i)["status"] for i in ids] == ["done"] * 3
    assert idle == 1

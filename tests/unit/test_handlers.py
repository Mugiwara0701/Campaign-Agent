from typing import Any

import pytest

from campaign_agent.conversation.state import ConversationState
from campaign_agent.conversation.state_machine import State
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.ir.patch import Suggestion
from campaign_agent.jobs.handlers import Services, Workspace, handle, progress_state
from campaign_agent.jobs.requests import ErrorCode, Request
from campaign_agent.llm.adapter import LLMTimeout
from campaign_agent.llm.stub import StubAdapter
from campaign_agent.merger.patch_merger import add_suggestions
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT, VAGUE_TEXT, op_json, reply


def svc(*replies: Any) -> Services:
    return Services(Extractor(StubAdapter(list(replies)), sleep=lambda _s: None))


def request(kind: str, payload: dict[str, Any] | None = None, base: int | None = None) -> Request:
    return Request("r1", "c1", kind, payload or {}, base, "dashboard-user")


def workspace(
    state: State = State.DRAFT, detail: str = COMPLETE_TEXT, ir=None, conv=None
) -> Workspace:  # type: ignore[no-untyped-def]
    return Workspace("c1", "Auto campaign", detail, ir, conv or ConversationState(state=state))


def after(outcome) -> Workspace:  # type: ignore[no-untyped-def]
    """The workspace the next request will see."""
    return Workspace("c1", "Auto campaign", COMPLETE_TEXT, outcome.ir, outcome.conv, outcome.view)


# --- first message ------------------------------------------------------------------------------------------------


def test_a_clear_message_goes_straight_to_a_reviewable_candidate() -> None:
    out = handle(request("interpret"), workspace(), svc(COMPLETE_REPLY))
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.candidate is not None and out.candidate.can_freeze
    assert out.ir.audience.industries == ["automotive"]
    assert out.ir.meta.name == "Auto campaign"
    assert out.result["state"] == "AWAITING_REVIEW"
    assert out.result["candidate_sha256"] == out.candidate.sha256
    assert out.view.candidate is not None and out.view.candidate.yaml == out.candidate.yaml
    assert out.view.state == "AWAITING_REVIEW"
    assert "Slack" in out.view.email_approval


def test_the_message_in_the_request_wins_over_the_stored_detail() -> None:
    s = svc(COMPLETE_REPLY)
    handle(request("interpret", {"message": COMPLETE_TEXT}), workspace(detail="something else"), s)
    prompt = s.extractor._adapter.calls[0].messages[1].content  # type: ignore[attr-defined]
    assert COMPLETE_TEXT in prompt


def test_a_vague_message_asks_questions_and_produces_no_candidate() -> None:
    out = handle(request("interpret"), workspace(detail=VAGUE_TEXT), svc(reply()))
    assert out.error is None
    assert out.conv.state is State.CLARIFICATION_REQUIRED
    assert out.candidate is None
    assert len(out.conv.open) == 2
    assert out.view.questions == out.conv.open
    assert out.result["open_questions"] == [q.id for q in out.conv.open]


def test_defaults_are_filled_and_labelled_before_review() -> None:
    out = handle(request("interpret"), workspace(), svc(COMPLETE_REPLY))
    statuses = {f.status for s in out.view.sections for f in s.fields}
    assert "defaulted" in statuses
    assert "inferred" in statuses
    quote = next(f for s in out.view.sections for f in s.fields if f.path == "audience.products")
    assert quote.evidence_quote == "digital cockpit"


def test_what_cannot_be_applied_is_listed_not_dropped() -> None:
    text = "Find US automotive companies building a digital cockpit"
    r = reply(
        op_json("audience.industries", ["automotive"], "automotive companies"),
        op_json("audience.products", ["digital cockpit"], "digital cockpit"),
        op_json("audience.include_countries", ["US"], "Find US"),
        unsupported=[{"request": "and message them on LinkedIn", "kind": "channel"}],
    )
    out = handle(request("interpret"), workspace(detail=text), svc(r))
    proposals = {n.proposal for n in out.view.not_applied}
    assert "targeting.include_countries" in proposals
    assert any(n.kind == "channel" for n in out.view.not_applied)
    yaml_text = out.candidate.yaml.split("config_version")[1]  # type: ignore[union-attr]
    assert "query_suffix: US" in yaml_text  # only as a search hint...
    assert (
        "include_countries" not in yaml_text
    )  # ...never as a country list the Marketing Agent would enforce


# --- failures of the first message ----------------------------------------------------------------------------------


def test_a_secret_in_the_message_is_refused_and_the_model_is_never_called() -> None:
    from tests.unit import fake_secrets as fs

    s = svc(COMPLETE_REPLY)
    out = handle(request("interpret", {"message": "use key " + fs.AWS}), workspace(), s)
    assert out.error is not None and out.error.code is ErrorCode.INPUT_REJECTED
    assert out.conv.state is State.DRAFT
    assert s.extractor._adapter.calls == []  # type: ignore[attr-defined]
    assert out.candidate is None
    assert out.view.last_request is not None and out.view.last_request.outcome == "failed"


def test_an_unreadable_reply_asks_the_user_to_rephrase_and_changes_nothing() -> None:
    out = handle(request("interpret"), workspace(), svc("junk", "still junk"))
    assert out.error is not None and out.error.code is ErrorCode.NEEDS_REPHRASE
    assert out.conv.state is State.DRAFT


def test_a_model_that_is_down_is_remembered_and_the_request_can_be_retried() -> None:
    down = handle(request("interpret"), workspace(), svc(LLMTimeout("t")))
    assert down.error is not None and down.error.code is ErrorCode.MODEL_UNAVAILABLE
    assert down.conv.state is State.MODEL_UNAVAILABLE
    again = handle(request("interpret"), after(down), svc(COMPLETE_REPLY))
    assert again.error is None
    assert again.conv.state is State.AWAITING_REVIEW


def test_an_empty_message_is_an_invalid_request() -> None:
    out = handle(request("interpret"), workspace(detail=""), svc(COMPLETE_REPLY))
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST


def test_interpret_is_refused_once_the_conversation_has_started() -> None:
    first = handle(request("interpret"), workspace(), svc(COMPLETE_REPLY))
    again = handle(request("interpret"), after(first), svc(COMPLETE_REPLY))
    assert again.error is not None and again.error.code is ErrorCode.WRONG_STATE
    assert again.conv.state is State.AWAITING_REVIEW


# --- answers ---------------------------------------------------------------------------------------------------------


def clarifying() -> Workspace:
    out = handle(request("interpret"), workspace(detail=VAGUE_TEXT), svc(reply()))
    return after(out)


def test_answering_the_questions_leads_to_a_candidate() -> None:
    ws = clarifying()
    answers = [{"question_id": q.id, "value": "c0"} for q in ws.conv.open]
    out = handle(request("answer", {"answers": answers}), ws, svc())
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.ir.provenance["audience.industries"].status.value == "confirmed"
    assert out.candidate is not None


def test_skipping_leaves_the_defaults_and_still_reaches_review() -> None:
    ws = clarifying()
    answers = [{"question_id": q.id, "skip": True} for q in ws.conv.open]
    out = handle(request("answer", {"answers": answers}), ws, svc())
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW
    warnings = [i.code for i in out.candidate.report.warnings]  # type: ignore[union-attr]
    assert "no_audience" in warnings  # the user was told the campaign is only the base vocabulary


def test_one_bad_answer_rejects_the_whole_request() -> None:
    ws = clarifying()
    good, _ = ws.conv.open
    answers = [
        {"question_id": good.id, "value": "c0"},
        {"question_id": "q_nope", "value": "c0"},
    ]
    out = handle(request("answer", {"answers": answers}), ws, svc())
    assert out.error is not None and out.error.code is ErrorCode.INVALID_ANSWER
    assert out.result["errors"][0]["question_id"] == "q_nope"
    assert out.ir == ws.ir  # nothing from the good answer is kept
    assert out.conv.state is State.CLARIFICATION_REQUIRED


def test_answers_are_refused_when_no_question_is_open() -> None:
    first = handle(request("interpret"), workspace(), svc(COMPLETE_REPLY))
    out = handle(
        request("answer", {"answers": [{"question_id": "q_x", "value": "c0"}]}), after(first), svc()
    )
    assert out.error is not None and out.error.code is ErrorCode.WRONG_STATE


@pytest.mark.parametrize(
    "payload", [{}, {"answers": []}, {"answers": "c0"}, {"answers": [{"nope": 1}]}]
)
def test_malformed_answers_are_invalid_requests(payload: dict[str, Any]) -> None:
    out = handle(request("answer", payload), clarifying(), svc())
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST


def test_a_preference_answered_as_this_campaign_is_read_again() -> None:
    text = "Find kiosk builders. We prefer small companies."
    r = reply(
        op_json("audience.products", ["kiosk"], "kiosk builders"),
        op_json("organisation_context", ["we prefer small companies"], "We prefer small companies"),
    )
    first = handle(request("interpret"), workspace(detail=text), svc(r))
    assert first.conv.state is State.CLARIFICATION_REQUIRED
    question = first.conv.open[0]
    reread = reply(op_json("audience.signals", ["small"], "we prefer small companies"))
    out = handle(
        request("answer", {"answers": [{"question_id": question.id, "value": "c0"}]}),
        after(first),
        svc(reread),
    )
    assert out.error is None
    assert out.ir.audience.signals == ["small"]


# --- edits ---------------------------------------------------------------------------------------------------------------


def reviewing() -> Workspace:
    return after(handle(request("interpret"), workspace(), svc(COMPLETE_REPLY)))


def test_an_edit_needs_the_revision_it_is_based_on() -> None:
    out = handle(request("edit", {"text": "raise the score"}), reviewing(), svc())
    assert out.error is not None and out.error.code is ErrorCode.MISSING_BASE_REVISION


def test_a_stale_edit_is_refused_with_the_current_revision() -> None:
    ws = reviewing()
    out = handle(request("edit", {"text": "x"}, base=ws.ir.meta.revision - 1), ws, svc())  # type: ignore[union-attr]
    assert out.error is not None and out.error.code is ErrorCode.STALE_REVISION
    assert out.result["revision"] == ws.ir.meta.revision  # type: ignore[union-attr]
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.view.candidate is not None  # the dashboard still shows the candidate


def test_an_edit_in_words_makes_a_new_candidate() -> None:
    ws = reviewing()
    text = "only companies with 500 to 5000 employees, minimum score 55"
    r = reply(
        op_json("qualification.min_qualify", 55, "minimum score 55", "set"),
        op_json("audience.employee_min", 500, "500 to 5000 employees", "set"),
        op_json("audience.employee_max", 5000, "500 to 5000 employees", "set"),
    )
    out = handle(request("edit", {"text": text}, base=ws.ir.meta.revision), ws, svc(r))  # type: ignore[union-attr]
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.ir.qualification.min_qualify == 55
    assert out.ir.provenance["qualification.min_qualify"].status.value == "confirmed"
    assert out.candidate is not None and "min_qualify: 55" in out.candidate.yaml
    assert out.ir.meta.revision > ws.ir.meta.revision  # type: ignore[union-attr]


def test_an_edit_in_words_cannot_turn_approval_off() -> None:
    ws = reviewing()
    r = reply(op_json("approval.required", False, "stop asking for approval", "set"))
    out = handle(
        request("edit", {"text": "stop asking for approval"}, base=ws.ir.meta.revision), ws, svc(r)
    )  # type: ignore[union-attr]
    assert out.error is None
    assert out.ir.approval.required is True
    assert "approval" not in out.candidate.yaml  # type: ignore[union-attr]
    assert any(n.kind == "approval" for n in out.view.not_applied)


def test_a_direct_edit_skips_the_model() -> None:
    ws = reviewing()
    s = svc()
    ops = [
        {"path": "qualification.min_qualify", "value": 55},
        {"path": "exclusions.companies", "op": "add", "value": ["acme"]},
    ]
    out = handle(request("edit", {"ops": ops}, base=ws.ir.meta.revision), ws, s)  # type: ignore[union-attr]
    assert out.error is None
    assert s.extractor._adapter.calls == []  # type: ignore[attr-defined]
    assert out.ir.qualification.min_qualify == 55
    assert out.ir.exclusions.companies == ["acme"]
    assert out.conv.state is State.AWAITING_REVIEW


def test_a_direct_edit_is_all_or_nothing() -> None:
    ws = reviewing()
    ops = [
        {"path": "qualification.min_qualify", "value": 55},
        {"path": "qualification.min_confidence", "value": 5},
    ]
    out = handle(request("edit", {"ops": ops}, base=ws.ir.meta.revision), ws, svc())  # type: ignore[union-attr]
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST
    assert out.result["rejected"][0]["path"] == "qualification.min_confidence"
    assert out.ir == ws.ir


def test_a_direct_edit_that_creates_a_conflict_becomes_a_question() -> None:
    ws = reviewing()
    ops = [{"path": "qualification.min_qualify", "value": 90}]  # the default draft score is 60
    out = handle(request("edit", {"ops": ops}, base=ws.ir.meta.revision), ws, svc())  # type: ignore[union-attr]
    assert out.error is None
    assert out.conv.state is State.CLARIFICATION_REQUIRED
    assert out.conv.open[0].kind == "conflict"


def test_a_direct_edit_still_works_while_the_model_is_down() -> None:
    down = handle(request("interpret"), workspace(), svc(LLMTimeout("t")))
    ws = after(down)
    out = handle(
        request(
            "edit",
            {"ops": [{"path": "audience.products", "op": "add", "value": ["kiosk"]}]},
            base=0,
        ),
        ws,
        svc(),
    )
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW


@pytest.mark.parametrize(
    "payload", [{}, {"text": "x", "ops": []}, {"ops": "no"}, {"ops": [{"value": 1}]}]
)
def test_edit_payload_must_be_text_or_ops(payload: dict[str, Any]) -> None:
    out = handle(request("edit", payload, base=reviewing().ir.meta.revision), reviewing(), svc())  # type: ignore[union-attr]
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST


# --- suggestions -----------------------------------------------------------------------------------------------------


def with_suggestion() -> tuple[Workspace, str]:
    ws = reviewing()
    sug = Suggestion(
        path="audience.products", value="instrument cluster", sources=["https://example.org/a"]
    )
    ir = add_suggestions(ws.ir, [sug], 5).ir  # type: ignore[arg-type]
    ws = Workspace("c1", "x", "y", ir, ws.conv, ws.previous_view)
    return ws, ir.suggestions[0].id


def test_accepting_a_suggestion_makes_a_new_candidate() -> None:
    ws, sid = with_suggestion()
    out = handle(request("suggestion", {"suggestion_id": sid, "decision": "accept"}), ws, svc())
    assert out.error is None
    assert "instrument cluster" in out.ir.audience.products
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.candidate is not None and "instrument cluster" in out.candidate.yaml


def test_rejecting_a_suggestion_keeps_the_candidate() -> None:
    ws, sid = with_suggestion()
    out = handle(request("suggestion", {"suggestion_id": sid, "decision": "reject"}), ws, svc())
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW
    assert "instrument cluster" not in out.ir.audience.products
    assert out.view.suggestions == ()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"suggestion_id": "s_nope", "decision": "accept"},
        {"suggestion_id": "s", "decision": "maybe"},
    ],
)
def test_bad_suggestion_requests(payload: dict[str, Any]) -> None:
    ws, _ = with_suggestion()
    out = handle(request("suggestion", payload), ws, svc())
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST


# --- discard, publish, unknown -----------------------------------------------------------------------------------------


def test_discarding_a_campaign_that_was_never_published() -> None:
    out = handle(request("discard"), reviewing(), svc())
    assert out.error is None
    assert out.conv.state is State.DISCARDED


def test_discarding_a_pending_change_keeps_the_published_campaign() -> None:
    ws = reviewing()
    ws = Workspace(
        "c1", "x", "y", ws.ir, ws.conv.model_copy(update={"has_published": True}), ws.previous_view
    )
    assert handle(request("discard"), ws, svc()).conv.state is State.PUBLISHED


def test_a_discarded_campaign_accepts_nothing() -> None:
    gone = after(handle(request("discard"), reviewing(), svc()))
    out = handle(request("edit", {"ops": []}, base=gone.ir.meta.revision), gone, svc())  # type: ignore[union-attr]
    assert out.error is not None and out.error.code is ErrorCode.WRONG_STATE


def test_an_unknown_request_type() -> None:
    out = handle(request("delete_everything"), reviewing(), svc())
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST


# --- progress and purity -------------------------------------------------------------------------------------------------


def test_progress_state_tells_the_dashboard_work_has_started() -> None:
    assert progress_state(request("interpret"), workspace()) is State.INTERPRETING
    assert progress_state(request("edit", {"text": "x"}), reviewing()) is State.INTERPRETING
    assert progress_state(request("edit", {"ops": []}), reviewing()) is None
    assert progress_state(request("discard"), reviewing()) is None
    assert progress_state(request("answer"), clarifying()) is State.INTERPRETING
    assert progress_state(request("answer"), reviewing()) is None  # not allowed in this state


def test_a_failed_request_never_changes_the_campaign() -> None:
    ws = reviewing()
    before = ws.ir.model_dump()  # type: ignore[union-attr]
    for bad in (
        request("edit", {"text": "x"}),
        request("answer", {"answers": [{"question_id": "q", "value": "c0"}]}),
    ):
        out = handle(bad, ws, svc())
        assert out.error is not None
        assert out.ir.model_dump() == before


def test_handle_does_not_modify_its_inputs() -> None:
    ws = reviewing()
    ir_before, conv_before = ws.ir.model_dump(), ws.conv.model_dump()  # type: ignore[union-attr]
    handle(
        request(
            "edit",
            {"ops": [{"path": "qualification.min_qualify", "value": 55}]},
            base=ws.ir.meta.revision,
        ),
        ws,
        svc(),
    )  # type: ignore[union-attr]
    assert ws.ir.model_dump() == ir_before  # type: ignore[union-attr]
    assert ws.conv.model_dump() == conv_before


def test_a_view_round_trips_through_json_for_storage() -> None:
    from campaign_agent.view import ReviewView

    out = handle(request("interpret"), workspace(), svc(COMPLETE_REPLY))
    assert ReviewView.model_validate_json(out.view.model_dump_json()) == out.view


def test_what_the_user_typed_in_the_dashboard_is_confirmed_not_inferred() -> None:
    out = handle(request("interpret"), workspace(), svc(COMPLETE_REPLY))
    campaign = next(s for s in out.view.sections if s.name == "campaign")
    assert {f.path: f.status for f in campaign.fields} == {
        "meta.name": "confirmed",
        "meta.description": "confirmed",
    }

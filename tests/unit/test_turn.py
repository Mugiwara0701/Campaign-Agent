import json

import pytest

from campaign_agent.conversation.state import MAX_OPEN, ConversationState
from campaign_agent.conversation.state_machine import Event, State
from campaign_agent.conversation.turn import plan_turn
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.ir.models import new_ir
from campaign_agent.ir.provenance import Status
from campaign_agent.llm.stub import StubAdapter
from campaign_agent.merger.patch_merger import Reason, Rejected, Source, TurnContext, merge_patch
from campaign_agent.questions.builder import answer_question
from campaign_agent.questions.models import Answer
from tests.unit.helpers import ir_with


def conv(state: State = State.INTERPRETING) -> ConversationState:
    return ConversationState(state=state)


def test_a_complete_campaign_has_nothing_to_ask() -> None:
    ir = ir_with(
        ("audience.industries", ["automotive"]), ("audience.products", ["digital cockpit"])
    )
    plan = plan_turn(ir, conv(), turn=1)
    assert plan.event is Event.COMPLETE
    assert plan.new_questions == ()
    assert plan.findings == ()


def test_open_questions_mean_gaps_found() -> None:
    plan = plan_turn(new_ir("c1"), conv(), turn=1)
    assert plan.event is Event.GAPS_FOUND
    assert len(plan.new_questions) == 2
    assert plan.conv.open == plan.new_questions


def test_the_event_moves_the_state_machine() -> None:
    gaps = plan_turn(new_ir("c1"), conv(), turn=1)
    assert gaps.conv.after(gaps.event).state is State.CLARIFICATION_REQUIRED
    ok = plan_turn(ir_with(("audience.products", ["kiosk"])), conv(), turn=1)
    assert ok.conv.after(ok.event).state is State.READY_FOR_VALIDATION


def test_a_whole_conversation_from_vague_to_ready() -> None:
    ir = new_ir("c1", "Auto")
    state = conv(State.INTERPRETING)
    turn = 1
    plan = plan_turn(ir, state, turn=turn)
    state = plan.conv.after(plan.event)
    assert state.state is State.CLARIFICATION_REQUIRED
    for _ in range(5):
        if state.state is not State.CLARIFICATION_REQUIRED:
            break
        turn += 1
        question = state.open[0]
        out = answer_question(plan.ir, state, Answer(question_id=question.id, value="c0"), turn)
        assert out.error is None
        state = out.conv.after(Event.ANSWER)
        plan = plan_turn(out.ir, state, turn=turn)
        state = plan.conv.after(plan.event)
    assert state.state is State.READY_FOR_VALIDATION
    assert plan.ir.audience.industries
    assert plan.ir.audience.capabilities_needed


# --- automatic fix ------------------------------------------------------------------------------------------------------------


def test_a_page_budget_shortfall_is_fixed_without_asking_and_shown() -> None:
    ir = ir_with(("limits.queries_per_run", 30), ("audience.products", ["kiosk"]))
    plan = plan_turn(ir, conv(), turn=1)
    assert plan.event is Event.COMPLETE
    assert plan.ir.limits.max_pages_per_run == 180
    assert plan.ir.provenance["limits.max_pages_per_run"].status is Status.INFERRED
    assert [n.code for n in plan.notices] == ["auto_fix"]
    assert "180" in plan.notices[0].message
    assert plan.new_questions == ()


def test_a_confirmed_page_budget_is_not_overridden_so_the_user_is_asked() -> None:
    ir = ir_with(
        ("limits.queries_per_run", 30),
        ("limits.max_pages_per_run", 100),
        ("audience.products", ["kiosk"]),
    )
    plan = plan_turn(ir, conv(), turn=1)
    assert plan.ir.limits.max_pages_per_run == 100
    assert plan.event is Event.GAPS_FOUND
    question = plan.new_questions[0]
    assert question.kind == "conflict"
    labels = [c.label for c in question.choices]
    assert labels == ["Run 16 queries per run", "Open 3 pages per query"]
    out = answer_question(plan.ir, plan.conv, Answer(question_id=question.id, value="c0"), turn=2)
    assert out.ir.limits.queries_per_run == 16
    assert plan_turn(out.ir, out.conv, turn=2).event is Event.COMPLETE


def test_a_budget_over_the_cap_is_a_question_not_a_fix() -> None:
    ir = ir_with(
        ("limits.queries_per_run", 40),
        ("limits.pages_per_query", 10),
        ("limits.results_per_query", 15),
        ("audience.products", ["k"]),
    )
    plan = plan_turn(ir, conv(), turn=1)
    assert plan.notices == ()
    assert plan.new_questions[0].kind == "conflict"


# --- notices ---------------------------------------------------------------------------------------------------------------------


def test_a_kept_confirmed_value_is_reported() -> None:
    ir = ir_with(("qualification.min_qualify", 55), ("audience.products", ["k"]))
    rejected = [
        Rejected(
            "qualification.min_qualify",
            "set",
            Reason.LOCKED,
            "qualification.min_qualify was confirmed by the user and was kept",
            55,
        )
    ]
    plan = plan_turn(ir, conv(), rejected=rejected, turn=2)
    assert [(n.code, n.path) for n in plan.notices] == [
        ("kept_confirmed", "qualification.min_qualify")
    ]
    assert "55" in plan.notices[0].message
    assert plan.event is Event.COMPLETE


# --- stale questions -----------------------------------------------------------------------------------------------------------------


def test_a_question_the_user_fixed_another_way_is_closed() -> None:
    first = plan_turn(ir_with(("audience.technologies", ["Android"])), conv(), turn=1)
    assert first.event is Event.GAPS_FOUND
    fixed = ir_with(("audience.technologies", ["Android"]), ("audience.products", ["kiosk"]))
    second = plan_turn(fixed, first.conv, turn=2)
    assert second.conv.open == ()
    assert second.event is Event.COMPLETE


def test_the_same_situation_is_not_asked_again_next_turn() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    first = plan_turn(ir, conv(), turn=1)
    second = plan_turn(ir, first.conv, turn=2)
    assert second.new_questions == ()
    assert second.conv.open == first.conv.open
    assert second.event is Event.GAPS_FOUND


def test_never_more_than_three_open_across_turns() -> None:
    ir = ir_with(
        ("qualification.min_qualify", 80),
        ("qualification.min_draft", 60),
        ("audience.employee_min", 9),
        ("audience.employee_max", 3),
        ("audience.products", ["kiosk makers"]),
        ("organisation_context", ["we prefer small companies"]),
    )
    state = conv()
    for turn in range(1, 5):
        state = plan_turn(ir, state, turn=turn).conv
        assert len(state.open) <= MAX_OPEN


# --- the real-model case: roles that could not be grounded ---------------------------------------------------------------------------


def test_decision_makers_without_named_roles_becomes_a_question() -> None:
    text = "Find US companies building automotive infotainment on Android and find the right decision makers"
    roles = [
        {
            "path": "contacts.preferred_roles",
            "op": "add",
            "value": [r],
            "evidence_quote": "find the right decision makers",
        }
        for r in ("cto", "vp_engineering", "founder")
    ]
    products = {
        "path": "audience.products",
        "op": "add",
        "value": ["infotainment"],
        "evidence_quote": "automotive infotainment",
    }
    reply = json.dumps({"ops": [products, *roles], "unsupported": [], "research_needs": []})
    result = Extractor(StubAdapter([reply]), sleep=lambda _s: None).run(text)
    assert result.patch is not None
    merged = merge_patch(new_ir("c1"), result.patch, TurnContext(1, Source.EXTRACTION, result.text))
    plan = plan_turn(merged.ir, conv(), rejected=merged.rejected, turn=1)
    assert plan.event is Event.GAPS_FOUND
    question = plan.new_questions[0]
    assert question.key.startswith("contacts.preferred_roles|value_unclear")
    assert question.text == "Which decision-maker roles should we look for?"
    out = answer_question(
        plan.ir, plan.conv, Answer(question_id=question.id, value=["c0", "c5"]), turn=2
    )
    assert out.ir.contacts.preferred_roles == ["cto", "procurement"]
    assert out.ir.provenance["contacts.preferred_roles"].status is Status.CONFIRMED


@pytest.mark.parametrize("unscoped", ["we prefer small companies", "we usually avoid defence work"])
def test_an_unscoped_preference_from_the_extractor_is_asked_about(unscoped: str) -> None:
    text = f"Find kiosk builders. {unscoped}."
    reply = json.dumps(
        {
            "ops": [
                {
                    "path": "audience.products",
                    "op": "add",
                    "value": ["kiosk"],
                    "evidence_quote": "kiosk builders",
                },
                {
                    "path": "organisation_context",
                    "op": "add",
                    "value": [unscoped],
                    "evidence_quote": unscoped,
                },
            ],
            "unsupported": [],
            "research_needs": [],
        }
    )
    result = Extractor(StubAdapter([reply]), sleep=lambda _s: None).run(text)
    assert result.patch is not None
    merged = merge_patch(new_ir("c1"), result.patch, TurnContext(1, Source.EXTRACTION, result.text))
    plan = plan_turn(merged.ir, conv(), turn=1)
    assert [q.kind for q in plan.new_questions] == ["preference"]
    assert unscoped in plan.new_questions[0].text

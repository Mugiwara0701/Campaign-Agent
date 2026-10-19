import json

import pytest

from campaign_agent.conversation.state import MAX_OPEN, ConversationState
from campaign_agent.gap.engine import analyze
from campaign_agent.guard.input_guard import check_input
from campaign_agent.ir.models import CampaignIR, new_ir
from campaign_agent.ir.provenance import Status
from campaign_agent.questions.builder import (
    answer_question,
    build_question,
    close_resolved,
    new_questions,
    register,
)
from campaign_agent.questions.models import Answer, Question
from tests.unit import fake_secrets as fs
from tests.unit.helpers import ir_with


def open_questions(ir: CampaignIR, conv: ConversationState | None = None, turn: int = 1):  # type: ignore[no-untyped-def]
    conv = conv or ConversationState()
    qs = new_questions(analyze(ir), ir, conv)
    return qs, register(conv, qs, turn)


def ids(question: Question) -> list[str]:
    return [c.id for c in question.choices]


# --- shape of the questions ------------------------------------------------------------------------------------------


def test_questions_are_templated_with_stable_ids() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    a = build_question(analyze(ir)[0], ir)
    b = build_question(analyze(ir)[0], ir)
    assert a == b
    assert a.id.startswith("q_")
    assert a.key == "audience.products|tech_without_product|"
    assert "Android" in a.text
    assert a.kind == "ambiguity"
    assert a.multi
    assert a.allow_text
    assert len(a.choices) == 12
    assert ids(a)[:3] == ["c0", "c1", "c2"]


def test_questions_survive_a_json_round_trip() -> None:
    qs, _ = open_questions(ir_with(("audience.industries", ["automotive"])))
    assert Question.model_validate_json(qs[0].model_dump_json()) == qs[0]
    json.dumps(qs[0].model_dump(mode="json"))


def test_choices_come_from_the_base_vocabulary() -> None:
    qs, _ = open_questions(new_ir("c1"))
    industries, areas = qs
    assert "automotive" in [c.label for c in industries.choices]
    assert "AOSP customization" in [c.label for c in areas.choices]
    assert len(areas.choices) == 14


# --- limits: three at a time, never repeated -----------------------------------------------------------------------------


def test_at_most_three_questions_are_open_and_the_rest_wait() -> None:
    ir = ir_with(
        ("qualification.min_qualify", 80),
        ("qualification.min_draft", 60),
        ("audience.employee_min", 9),
        ("audience.employee_max", 3),
        ("audience.products", ["kiosk makers"]),
        ("organisation_context", ["we prefer small companies"]),
    )
    assert len(analyze(ir)) == 4
    qs, conv = open_questions(ir)
    assert len(qs) == MAX_OPEN == len(conv.open)
    assert [q.kind for q in qs] == ["conflict", "conflict", "drift"]
    more, conv = open_questions(ir, conv, turn=2)
    assert more == []  # three are still open


def test_answering_makes_room_for_the_next_question() -> None:
    ir = ir_with(
        ("qualification.min_qualify", 80),
        ("qualification.min_draft", 60),
        ("audience.employee_min", 9),
        ("audience.employee_max", 3),
        ("audience.products", ["kiosk makers"]),
        ("organisation_context", ["we prefer small companies"]),
    )
    _, conv = open_questions(ir)
    first = conv.open[0]
    out = answer_question(ir, conv, Answer(question_id=first.id, value="c0"), turn=2)
    nxt, _ = open_questions(out.ir, out.conv, turn=2)
    assert len(nxt) == 1
    assert nxt[0].kind == "preference"
    assert nxt[0].key not in {q.key for q in conv.open}


def test_a_question_is_never_asked_twice_even_after_it_was_skipped_or_answered() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    qs, conv = open_questions(ir)
    out = answer_question(ir, conv, Answer(question_id=qs[0].id, skip=True), turn=2)
    again, _ = open_questions(out.ir, out.conv, turn=3)
    assert again == []
    assert out.conv.asked[qs[0].key].status == "skipped"


# --- skip --------------------------------------------------------------------------------------------------------------------


def test_skip_is_allowed_for_gaps_and_leaves_the_campaign_alone() -> None:
    ir = new_ir("c1")
    qs, conv = open_questions(ir)
    for q in qs:
        assert q.skippable
    out = answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="Skip"), turn=2
    )  # the word "skip" works too
    assert out.error is None
    assert out.ir == ir
    assert len(out.conv.open) == 1


@pytest.mark.parametrize(
    "ir",
    [
        ir_with(
            ("qualification.min_qualify", 80),
            ("qualification.min_draft", 60),
            ("audience.products", ["k"]),
        ),
        ir_with(("audience.products", ["kiosk makers"])),
        ir_with(
            ("audience.employee_min", 9), ("audience.employee_max", 3), ("audience.products", ["k"])
        ),
        ir_with(
            ("audience.industries", ["auto"]),
            ("exclusions.terms", ["auto"]),
            ("audience.products", ["k"]),
        ),
    ],
)
def test_conflicts_and_off_target_wording_cannot_be_skipped(ir: CampaignIR) -> None:
    qs, conv = open_questions(ir)
    assert not qs[0].skippable
    out = answer_question(ir, conv, Answer(question_id=qs[0].id, skip=True), turn=2)
    assert out.error is not None
    assert out.error.code == "not_skippable"
    assert out.conv == conv


# --- answering with a choice ------------------------------------------------------------------------------------------


def test_a_choice_changes_the_campaign_as_a_confirmed_answer() -> None:
    ir = ir_with(
        ("qualification.min_qualify", 80),
        ("qualification.min_draft", 60),
        ("audience.products", ["k"]),
    )
    qs, conv = open_questions(ir)
    out = answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="c0"), turn=2
    )  # raise draft to 80
    assert out.ir.qualification.min_draft == 80
    assert out.ir.provenance["qualification.min_draft"].status is Status.CONFIRMED
    assert out.conv.open == ()
    assert analyze(out.ir) == []
    other = answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="c1"), turn=2
    )  # lower minimum to 60
    assert other.ir.qualification.min_qualify == 60


def test_several_choices_can_be_picked_when_the_question_allows_it() -> None:
    qs, conv = open_questions(new_ir("c1"))
    out = answer_question(
        new_ir("c1"), conv, Answer(question_id=qs[0].id, value=["c0", "c3"]), turn=2
    )
    assert len(out.ir.audience.industries) == 2


def test_a_single_choice_question_refuses_two_picks() -> None:
    ir = ir_with(
        ("audience.employee_min", 9), ("audience.employee_max", 3), ("audience.products", ["k"])
    )
    qs, conv = open_questions(ir)
    out = answer_question(ir, conv, Answer(question_id=qs[0].id, value=["c0", "c1"]), turn=2)
    assert out.error is not None
    assert out.error.code == "invalid_choice"


@pytest.mark.parametrize("value", ["c99", ["c0", "nope"], [], None, 42])
def test_a_bad_choice_is_refused_and_the_question_stays_open(value: object) -> None:
    ir = ir_with(
        ("audience.employee_min", 9), ("audience.employee_max", 3), ("audience.products", ["k"])
    )
    qs, conv = open_questions(ir)
    answer = Answer.model_construct(question_id=qs[0].id, value=value, skip=False)
    out = answer_question(ir, conv, answer, turn=2)
    assert out.error is not None
    assert out.error.code == "invalid_choice"
    assert out.conv == conv
    assert out.ir == ir


def test_text_that_looks_like_a_choice_id_is_still_just_text_where_text_is_allowed() -> None:
    ir = ir_with(("audience.industries", ["automotive"]))
    qs, conv = open_questions(ir)
    assert qs[0].allow_text
    assert answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="c99"), turn=2
    ).ir.audience.capabilities_needed == ["c99"]


def test_contradiction_choices_remove_the_other_side() -> None:
    ir = ir_with(
        ("audience.industries", ["automotive"]),
        ("exclusions.terms", ["automotive"]),
        ("audience.products", ["k"]),
    )
    qs, conv = open_questions(ir)
    keep_target = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c0"), turn=2)
    assert keep_target.ir.audience.industries == ["automotive"]
    assert keep_target.ir.exclusions.terms == []
    keep_excluded = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c1"), turn=2)
    assert keep_excluded.ir.audience.industries == []
    assert keep_excluded.ir.exclusions.terms == ["automotive"]


def test_employee_range_choices() -> None:
    ir = ir_with(
        ("audience.employee_min", 5000),
        ("audience.employee_max", 500),
        ("audience.products", ["k"]),
    )
    qs, conv = open_questions(ir)
    swap = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c0"), turn=2)
    assert (swap.ir.audience.employee_min, swap.ir.audience.employee_max) == (500, 5000)
    drop = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c1"), turn=2)
    assert drop.ir.audience.employee_max is None


# --- answering with text ------------------------------------------------------------------------------------------------


def test_a_free_text_answer_is_added_to_the_right_field() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    qs, conv = open_questions(ir)
    out = answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="in-vehicle infotainment system"), turn=2
    )
    assert out.ir.audience.products == ["in-vehicle infotainment system"]
    assert out.ir.provenance["audience.products"].status is Status.CONFIRMED
    assert analyze(out.ir) == []


def test_text_answers_go_through_the_input_guard() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    qs, conv = open_questions(ir)
    out = answer_question(ir, conv, Answer(question_id=qs[0].id, value="my key " + fs.AWS), turn=2)
    assert out.error is not None
    assert out.error.code == "invalid_text"
    assert fs.AWS not in out.error.message
    assert out.ir == ir


def test_a_text_answer_that_breaks_the_contract_is_refused() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    qs, conv = open_questions(ir)
    out = answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="a {{ template }} injection"), turn=2
    )
    assert out.error is not None
    assert out.error.code == "invalid_value"
    assert out.conv == conv  # still open: the user can try again


def test_text_is_refused_where_only_choices_are_allowed() -> None:
    ir = ir_with(
        ("audience.employee_min", 9), ("audience.employee_max", 3), ("audience.products", ["k"])
    )
    qs, conv = open_questions(ir)
    assert (
        answer_question(ir, conv, Answer(question_id=qs[0].id, value="whatever"), turn=2).error.code
        == "invalid_choice"
    )  # type: ignore[union-attr]


def test_supplier_wording_can_be_removed_or_replaced_by_text() -> None:
    ir = ir_with(("audience.products", ["kiosk makers", "EV charger"]))
    qs, conv = open_questions(ir)
    removed = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c0"), turn=2)
    assert removed.ir.audience.products == ["EV charger"]
    replaced = answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="self-service kiosk"), turn=2
    )
    assert replaced.ir.audience.products == ["EV charger", "self-service kiosk"]
    assert analyze(replaced.ir) == []
    again = answer_question(
        ir, conv, Answer(question_id=qs[0].id, value="kiosk manufacturer"), turn=2
    )
    assert again.ir.audience.products == [
        "EV charger",
        "kiosk manufacturer",
    ]  # accepted here, caught again by analysis
    assert analyze(again.ir)[0].detail["word"] == "manufacturer"


def test_a_supplier_word_in_the_query_suffix_is_cleared_or_replaced() -> None:
    ir = ir_with(("limits.query_suffix", "best price"), ("audience.products", ["k"]))
    qs, conv = open_questions(ir)
    assert (
        answer_question(
            ir, conv, Answer(question_id=qs[0].id, value="c0"), turn=2
        ).ir.limits.query_suffix
        is None
    )
    assert (
        answer_question(
            ir, conv, Answer(question_id=qs[0].id, value="embedded"), turn=2
        ).ir.limits.query_suffix
        == "embedded"
    )


# --- unclear values --------------------------------------------------------------------------------------------------------


def test_a_numeric_text_answer_becomes_a_number() -> None:
    from campaign_agent.gap.engine import Finding, Kind, Reason

    ir = ir_with(("audience.products", ["k"]))
    q = build_question(Finding(Kind.UNCLEAR, "audience.employee_min", Reason.VALUE_UNCLEAR), ir)
    conv = register(ConversationState(), [q], 1)
    out = answer_question(ir, conv, Answer(question_id=q.id, value="500"), turn=2)
    assert out.ir.audience.employee_min == 500
    bad = answer_question(ir, conv, Answer(question_id=q.id, value="lots"), turn=2)
    assert bad.error is not None
    assert bad.error.code == "invalid_value"


# --- preferences -------------------------------------------------------------------------------------------------------------


def test_a_preference_for_this_campaign_asks_the_caller_to_read_it_again() -> None:
    ir = ir_with(
        ("audience.products", ["k"]), ("organisation_context", ["we prefer small companies"])
    )
    qs, conv = open_questions(ir)
    out = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c0"), turn=2)
    assert out.follow_up is not None
    assert out.follow_up.reextract == "we prefer small companies"
    assert out.error is None


def test_a_preference_for_all_campaigns_is_reported_as_not_applied() -> None:
    ir = ir_with(
        ("audience.products", ["k"]), ("organisation_context", ["we prefer small companies"])
    )
    qs, conv = open_questions(ir)
    out = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c1"), turn=2)
    assert out.follow_up is None
    assert "for all campaigns" in out.ir.unsupported[0].request


# --- bookkeeping ---------------------------------------------------------------------------------------------------------------


def test_answering_an_unknown_or_finished_question() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    qs, conv = open_questions(ir)
    assert (
        answer_question(ir, conv, Answer(question_id="q_nope", value="c0"), turn=2).error.code
        == "unknown_question"
    )  # type: ignore[union-attr]
    done = answer_question(ir, conv, Answer(question_id=qs[0].id, value="c0"), turn=2)
    assert (
        answer_question(
            done.ir, done.conv, Answer(question_id=qs[0].id, value="c1"), turn=3
        ).error.code
        == "already_answered"
    )  # type: ignore[union-attr]


def test_open_questions_whose_situation_is_gone_are_closed() -> None:
    ir = ir_with(("audience.technologies", ["Android"]))
    _, conv = open_questions(ir)
    closed = close_resolved(conv, set(), turn=2)
    assert closed.open == ()
    assert next(iter(closed.asked.values())).status == "answered"


def test_the_answer_text_limit_and_guard_agree() -> None:
    assert check_input("x" * 501, "edit").ok
    assert Answer(question_id="q", value="x" * 500).value == "x" * 500
    with pytest.raises(ValueError, match="at most 500"):
        Answer(question_id="q", value="x" * 501)


def test_a_country_that_is_never_pitched_can_be_removed_or_replaced() -> None:
    ir = ir_with(
        ("audience.include_countries", ["Germany", "Austria"]),
        ("audience.products", ["digital cockpit"]),
        ("audience.capabilities_needed", ["embedded Linux"]),
    )
    qs, conv = open_questions(ir)
    q = next(q for q in qs if "never pitched" in q.text)
    assert "Germany" in q.text and "Austria" in q.text
    assert not q.skippable
    removed = answer_question(ir, conv, Answer(question_id=q.id, value="c0"), turn=2)
    assert removed.ir.audience.include_countries == ["Austria"]
    replaced = answer_question(ir, conv, Answer(question_id=q.id, value="Switzerland"), turn=2)
    assert replaced.ir.audience.include_countries == ["Austria", "Switzerland"]

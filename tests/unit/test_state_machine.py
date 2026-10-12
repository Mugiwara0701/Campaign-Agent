import itertools
import re
from pathlib import Path

import pytest

from campaign_agent.conversation.state_machine import (
    Event,
    IllegalTransitionError,
    State,
    allowed_events,
    next_state,
    transitions,
)

S, E = State, Event

# Written out by hand from the design, independent of the table in the code.
EXPECTED = [
    (S.DRAFT, E.MESSAGE, S.INTERPRETING),
    (S.INTERPRETING, E.RESEARCH_NEEDED, S.RESEARCHING),
    (S.INTERPRETING, E.GAPS_FOUND, S.CLARIFICATION_REQUIRED),
    (S.INTERPRETING, E.COMPLETE, S.READY_FOR_VALIDATION),
    (S.INTERPRETING, E.MODEL_DOWN, S.MODEL_UNAVAILABLE),
    (S.RESEARCHING, E.GAPS_FOUND, S.CLARIFICATION_REQUIRED),
    (S.RESEARCHING, E.COMPLETE, S.READY_FOR_VALIDATION),
    (S.MODEL_UNAVAILABLE, E.RETRY, S.INTERPRETING),
    (S.MODEL_UNAVAILABLE, E.FIELD_EDIT_COMPLETE, S.READY_FOR_VALIDATION),
    (S.MODEL_UNAVAILABLE, E.FIELD_EDIT_GAPS, S.CLARIFICATION_REQUIRED),
    (S.CLARIFICATION_REQUIRED, E.ANSWER, S.INTERPRETING),
    (S.CLARIFICATION_REQUIRED, E.EDIT_WORDS, S.INTERPRETING),
    (S.CLARIFICATION_REQUIRED, E.FIELD_EDIT_COMPLETE, S.READY_FOR_VALIDATION),
    (S.CLARIFICATION_REQUIRED, E.FIELD_EDIT_GAPS, S.CLARIFICATION_REQUIRED),
    (S.CLARIFICATION_REQUIRED, E.SUGGESTION_ACCEPTED, S.INTERPRETING),
    (S.CLARIFICATION_REQUIRED, E.SUGGESTION_REJECTED, S.CLARIFICATION_REQUIRED),
    (S.READY_FOR_VALIDATION, E.VALIDATE, S.VALIDATING),
    (S.VALIDATING, E.VALIDATION_PASSED, S.VALIDATED),
    (S.VALIDATING, E.VALIDATION_FAILED, S.VALIDATION_FAILED),
    (S.VALIDATION_FAILED, E.EDIT_WORDS, S.INTERPRETING),
    (S.VALIDATION_FAILED, E.DECISION_NEEDED, S.CLARIFICATION_REQUIRED),
    (S.VALIDATION_FAILED, E.FIELD_EDIT_COMPLETE, S.READY_FOR_VALIDATION),
    (S.VALIDATION_FAILED, E.FIELD_EDIT_GAPS, S.CLARIFICATION_REQUIRED),
    (S.VALIDATION_FAILED, E.SUGGESTION_REJECTED, S.VALIDATION_FAILED),
    (S.VALIDATED, E.FROZEN, S.AWAITING_REVIEW),
    (S.AWAITING_REVIEW, E.EDIT_WORDS, S.INTERPRETING),
    (S.AWAITING_REVIEW, E.FIELD_EDIT_COMPLETE, S.READY_FOR_VALIDATION),
    (S.AWAITING_REVIEW, E.FIELD_EDIT_GAPS, S.CLARIFICATION_REQUIRED),
    (S.AWAITING_REVIEW, E.SUGGESTION_ACCEPTED, S.READY_FOR_VALIDATION),
    (S.AWAITING_REVIEW, E.SUGGESTION_REJECTED, S.AWAITING_REVIEW),
    (S.AWAITING_REVIEW, E.APPROVE, S.APPROVED),
    (S.AWAITING_REVIEW, E.PUBLISH_BLOCKED, S.VALIDATION_FAILED),
    (S.APPROVED, E.PUBLISHED, S.PUBLISHED),
    (S.APPROVED, E.PUBLISH_FAILED, S.PUBLISH_FAILED),
    (S.APPROVED, E.PUBLISH_BLOCKED, S.VALIDATION_FAILED),
    (S.PUBLISH_FAILED, E.RETRY_PUBLISH, S.APPROVED),
    (S.PUBLISH_FAILED, E.PUBLISH_BLOCKED, S.VALIDATION_FAILED),
    (S.PUBLISHED, E.CHANGE_REQUEST, S.INTERPRETING),
]


@pytest.mark.parametrize(("state", "event", "target"), EXPECTED)
def test_every_listed_transition(state: State, event: Event, target: State) -> None:
    assert next_state(state, event) == target
    assert event in allowed_events(state)


def test_the_table_has_exactly_the_listed_transitions() -> None:
    assert {(s, e): t for s, e, t in EXPECTED} == transitions()


def test_every_other_pair_is_rejected() -> None:
    legal = {(s, e) for s, e, _ in EXPECTED}
    for state, event in itertools.product(State, Event):
        if event is E.DISCARD or (state, event) in legal:
            continue
        with pytest.raises(IllegalTransitionError):
            next_state(state, event)


# --- discard ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", [s for s in State if s not in (S.PUBLISHED, S.DISCARDED)])
def test_an_unpublished_campaign_can_be_discarded_from_any_state(state: State) -> None:
    assert next_state(state, E.DISCARD) is S.DISCARDED
    assert E.DISCARD in allowed_events(state)


@pytest.mark.parametrize("state", [s for s in State if s not in (S.PUBLISHED, S.DISCARDED)])
def test_discarding_a_pending_change_keeps_the_published_campaign(state: State) -> None:
    assert next_state(state, E.DISCARD, has_published=True) is S.PUBLISHED


@pytest.mark.parametrize("state", [S.PUBLISHED, S.DISCARDED])
def test_a_live_or_discarded_campaign_cannot_be_discarded(state: State) -> None:
    for published in (False, True):
        with pytest.raises(IllegalTransitionError):
            next_state(state, E.DISCARD, has_published=published)
    assert E.DISCARD not in allowed_events(state)


# --- shape of the machine -----------------------------------------------------------------------------------------


def test_discarded_is_terminal() -> None:
    assert allowed_events(S.DISCARDED) == frozenset()


def test_every_state_is_reachable_from_draft() -> None:
    seen = {S.DRAFT}
    todo = [S.DRAFT]
    while todo:
        state = todo.pop()
        for event in allowed_events(state):
            target = next_state(state, event)
            if target not in seen:
                seen.add(target)
                todo.append(target)
    assert seen == set(State) - {S.DISCARDED} | {S.DISCARDED}


def test_every_live_state_has_a_way_forward() -> None:
    for state in set(State) - {S.DISCARDED}:
        assert allowed_events(state), state


def test_a_failed_final_check_is_a_validation_failure_not_a_publish_failure() -> None:
    """The M0 correction: PUBLISH_FAILED is for store failures (safe to retry) only."""
    for state in (S.AWAITING_REVIEW, S.APPROVED, S.PUBLISH_FAILED):
        assert next_state(state, E.PUBLISH_BLOCKED) is S.VALIDATION_FAILED
    assert next_state(S.APPROVED, E.PUBLISH_FAILED) is S.PUBLISH_FAILED
    assert next_state(S.PUBLISH_FAILED, E.RETRY_PUBLISH) is S.APPROVED


def test_accepting_a_suggestion_needs_revalidation_but_rejecting_does_not() -> None:
    assert next_state(S.AWAITING_REVIEW, E.SUGGESTION_ACCEPTED) is S.READY_FOR_VALIDATION
    assert next_state(S.AWAITING_REVIEW, E.SUGGESTION_REJECTED) is S.AWAITING_REVIEW


def test_a_change_after_publication_starts_a_new_candidate() -> None:
    assert next_state(S.PUBLISHED, E.CHANGE_REQUEST, has_published=True) is S.INTERPRETING


def test_direct_edits_still_work_when_the_model_is_down() -> None:
    assert next_state(S.MODEL_UNAVAILABLE, E.FIELD_EDIT_COMPLETE) is S.READY_FOR_VALIDATION


def test_nothing_can_be_approved_before_review() -> None:
    for state in set(State) - {S.AWAITING_REVIEW}:
        with pytest.raises(IllegalTransitionError):
            next_state(state, E.APPROVE)


def test_illegal_transition_error_names_the_pair() -> None:
    with pytest.raises(IllegalTransitionError, match="approve is not allowed in DRAFT"):
        next_state(S.DRAFT, E.APPROVE)


def test_states_match_the_database_check_constraint() -> None:
    sql = (Path(__file__).parents[2] / "migrations" / "0013_campaign_agent_tables.sql").read_text()
    block = re.search(r"current_state in \((.*?)\)\s*\)", sql, re.DOTALL)
    assert block is not None
    assert set(re.findall(r"'([A-Z_]+)'", block.group(1))) == {s.value for s in State}

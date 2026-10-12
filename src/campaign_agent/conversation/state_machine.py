"""The campaign's review state machine (architecture doc section 8, with the M0 corrections).

`next_state(state, event, has_published=...)` is pure. A pair that is not in the table raises
IllegalTransitionError; nothing else decides what may happen next.

Rules worth knowing:
  * Only the latest candidate is approved; that check lives with the versions (M7).
  * Discard: from any state except PUBLISHED and DISCARDED. A campaign that was never published
    goes to DISCARDED. One that has a published version only loses its pending change and returns
    to PUBLISHED, so the live campaign is not affected.
  * If level 5 or 6 fails when the user approves (or when a failed publish is retried), the
    candidate is no longer valid: the state goes to VALIDATION_FAILED. PUBLISH_FAILED is only for
    a store failure, and a retry from it is safe.
  * Accepting a web suggestion changes a value, so it needs a new validation; rejecting one changes
    nothing and the state stays as it is.
"""

from enum import StrEnum


class State(StrEnum):
    DRAFT = "DRAFT"
    INTERPRETING = "INTERPRETING"
    RESEARCHING = "RESEARCHING"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    READY_FOR_VALIDATION = "READY_FOR_VALIDATION"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    DISCARDED = "DISCARDED"


class Event(StrEnum):
    MESSAGE = "message"  # the first message arrives
    RESEARCH_NEEDED = "research_needed"
    GAPS_FOUND = "gaps_found"  # there are open questions
    COMPLETE = "complete"  # nothing left to ask
    MODEL_DOWN = "model_down"
    RETRY = "retry"  # try the model again
    ANSWER = "answer"  # the user answered or skipped a question
    EDIT_WORDS = "edit_words"  # a change asked for in words
    FIELD_EDIT_COMPLETE = "field_edit_complete"  # a direct field edit; nothing left to ask
    FIELD_EDIT_GAPS = "field_edit_gaps"  # a direct field edit; questions remain
    SUGGESTION_ACCEPTED = "suggestion_accepted"
    SUGGESTION_REJECTED = "suggestion_rejected"
    VALIDATE = "validate"  # automatic after COMPLETE
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    DECISION_NEEDED = "decision_needed"  # a failure only the user can resolve
    FROZEN = "frozen"  # the candidate version is stored
    APPROVE = "approve"
    PUBLISH_BLOCKED = "publish_blocked"  # level 5 or 6 failed at approval time
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"  # store failure
    RETRY_PUBLISH = "retry_publish"
    CHANGE_REQUEST = "change_request"  # a change after publication
    DISCARD = "discard"


class IllegalTransitionError(ValueError):
    def __init__(self, state: State, event: Event) -> None:
        super().__init__(f"{event.value} is not allowed in {state.value}")
        self.state = state
        self.event = event


S, E = State, Event

_TABLE: dict[tuple[State, Event], State] = {
    (S.DRAFT, E.MESSAGE): S.INTERPRETING,
    # interpreting
    (S.INTERPRETING, E.RESEARCH_NEEDED): S.RESEARCHING,
    (S.INTERPRETING, E.GAPS_FOUND): S.CLARIFICATION_REQUIRED,
    (S.INTERPRETING, E.COMPLETE): S.READY_FOR_VALIDATION,
    (S.INTERPRETING, E.MODEL_DOWN): S.MODEL_UNAVAILABLE,
    (S.RESEARCHING, E.GAPS_FOUND): S.CLARIFICATION_REQUIRED,
    (S.RESEARCHING, E.COMPLETE): S.READY_FOR_VALIDATION,  # a failed search is not fatal
    (S.MODEL_UNAVAILABLE, E.RETRY): S.INTERPRETING,
    (S.MODEL_UNAVAILABLE, E.FIELD_EDIT_COMPLETE): S.READY_FOR_VALIDATION,  # edits need no model
    (S.MODEL_UNAVAILABLE, E.FIELD_EDIT_GAPS): S.CLARIFICATION_REQUIRED,
    # clarification
    (S.CLARIFICATION_REQUIRED, E.ANSWER): S.INTERPRETING,
    (S.CLARIFICATION_REQUIRED, E.EDIT_WORDS): S.INTERPRETING,
    (S.CLARIFICATION_REQUIRED, E.FIELD_EDIT_COMPLETE): S.READY_FOR_VALIDATION,
    (S.CLARIFICATION_REQUIRED, E.FIELD_EDIT_GAPS): S.CLARIFICATION_REQUIRED,
    (S.CLARIFICATION_REQUIRED, E.SUGGESTION_ACCEPTED): S.INTERPRETING,
    (S.CLARIFICATION_REQUIRED, E.SUGGESTION_REJECTED): S.CLARIFICATION_REQUIRED,
    # validation
    (S.READY_FOR_VALIDATION, E.VALIDATE): S.VALIDATING,
    (S.VALIDATING, E.VALIDATION_PASSED): S.VALIDATED,
    (S.VALIDATING, E.VALIDATION_FAILED): S.VALIDATION_FAILED,
    (S.VALIDATION_FAILED, E.EDIT_WORDS): S.INTERPRETING,
    (S.VALIDATION_FAILED, E.DECISION_NEEDED): S.CLARIFICATION_REQUIRED,
    (S.VALIDATION_FAILED, E.FIELD_EDIT_COMPLETE): S.READY_FOR_VALIDATION,
    (S.VALIDATION_FAILED, E.FIELD_EDIT_GAPS): S.CLARIFICATION_REQUIRED,
    (S.VALIDATION_FAILED, E.SUGGESTION_REJECTED): S.VALIDATION_FAILED,
    (S.VALIDATED, E.FROZEN): S.AWAITING_REVIEW,
    # review
    (S.AWAITING_REVIEW, E.EDIT_WORDS): S.INTERPRETING,
    (S.AWAITING_REVIEW, E.FIELD_EDIT_COMPLETE): S.READY_FOR_VALIDATION,
    (S.AWAITING_REVIEW, E.FIELD_EDIT_GAPS): S.CLARIFICATION_REQUIRED,
    (S.AWAITING_REVIEW, E.SUGGESTION_ACCEPTED): S.READY_FOR_VALIDATION,
    (S.AWAITING_REVIEW, E.SUGGESTION_REJECTED): S.AWAITING_REVIEW,
    (S.AWAITING_REVIEW, E.APPROVE): S.APPROVED,
    (S.AWAITING_REVIEW, E.PUBLISH_BLOCKED): S.VALIDATION_FAILED,
    # publishing
    (S.APPROVED, E.PUBLISHED): S.PUBLISHED,
    (S.APPROVED, E.PUBLISH_FAILED): S.PUBLISH_FAILED,
    (S.APPROVED, E.PUBLISH_BLOCKED): S.VALIDATION_FAILED,
    (S.PUBLISH_FAILED, E.RETRY_PUBLISH): S.APPROVED,
    (S.PUBLISH_FAILED, E.PUBLISH_BLOCKED): S.VALIDATION_FAILED,
    (S.PUBLISHED, E.CHANGE_REQUEST): S.INTERPRETING,
}

_DISCARDABLE = frozenset(State) - {S.PUBLISHED, S.DISCARDED}


def next_state(state: State, event: Event, *, has_published: bool = False) -> State:
    if event is E.DISCARD:
        if state not in _DISCARDABLE:
            raise IllegalTransitionError(state, event)
        return S.PUBLISHED if has_published else S.DISCARDED
    try:
        return _TABLE[(state, event)]
    except KeyError:
        raise IllegalTransitionError(state, event) from None


def allowed_events(state: State) -> frozenset[Event]:
    events = {event for (s, event) in _TABLE if s is state}
    if state in _DISCARDABLE:
        events.add(E.DISCARD)
    return frozenset(events)


def transitions() -> dict[tuple[State, Event], State]:
    """A copy of the table (without DISCARD, whose target depends on has_published)."""
    return dict(_TABLE)

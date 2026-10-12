"""What each kind of request does. Pure: a request and a loaded workspace in, an outcome out.

No database here. The model is called through the extractor; everything else is deterministic. The caller
(jobs/manager.py) loads the workspace and stores the outcome in one transaction, so a request that dies
half-way leaves nothing half-applied and can simply be run again.

Every request runs the conversation to a resting state: questions to answer, a candidate to review, a
validation failure to fix, or the model being unavailable.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from campaign_agent.compiler.build import Candidate, build_candidate
from campaign_agent.compiler.compiler import NotApplied, compile_overlay
from campaign_agent.conversation.state import ConversationState
from campaign_agent.conversation.state_machine import (
    Event,
    IllegalTransitionError,
    State,
    allowed_events,
)
from campaign_agent.conversation.turn import Notice, plan_turn
from campaign_agent.extractor.extractor import ExtractionResult, Extractor, Status
from campaign_agent.ir.models import CampaignIR, new_ir
from campaign_agent.ir.patch import IRPatch, PatchOp
from campaign_agent.jobs.requests import ErrorCode, Request, RequestError
from campaign_agent.merger.patch_merger import (
    MergeResult,
    Reason,
    Rejected,
    Source,
    SuggestionResolvedError,
    TurnContext,
    UnknownSuggestionError,
    apply_defaults,
    merge_patch,
    resolve_suggestion,
)
from campaign_agent.questions.builder import answer_question
from campaign_agent.questions.models import Answer
from campaign_agent.view import LastRequest, ReviewView, build_view


@dataclass(frozen=True)
class Workspace:
    """Everything a request needs, loaded from the database."""

    campaign_id: str
    name: str
    detail: str
    ir: CampaignIR | None
    conv: ConversationState
    previous_view: ReviewView | None = None


@dataclass(frozen=True)
class Services:
    extractor: Extractor


@dataclass
class Outcome:
    ir: CampaignIR
    conv: ConversationState
    view: ReviewView
    candidate: Candidate | None = None  # to store as a new version, when it can be frozen
    result: dict[str, Any] = field(default_factory=dict)
    error: RequestError | None = None


# --- the turn after the campaign changed -----------------------------------------------------------------------


@dataclass
class _Turn:
    ir: CampaignIR
    conv: ConversationState
    turn: int
    rejected: list[Rejected] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    candidate: Candidate | None = None

    def absorb(self, merge: MergeResult) -> None:
        self.ir = merge.ir
        self.rejected.extend(merge.rejected)


def _settle(t: _Turn, *, direct_edit: bool = False) -> None:
    """From 'the campaign changed' to a resting state: ask, or validate and freeze a candidate."""
    plan = plan_turn(t.ir, t.conv, rejected=t.rejected, turn=t.turn)
    t.ir, t.conv, t.notices = plan.ir, plan.conv, list(plan.notices)
    if plan.event is Event.GAPS_FOUND:
        t.conv = t.conv.after(Event.FIELD_EDIT_GAPS if direct_edit else Event.GAPS_FOUND)
        return
    t.conv = t.conv.after(Event.FIELD_EDIT_COMPLETE if direct_edit else Event.COMPLETE)
    _validate(t)


def _validate(t: _Turn) -> None:
    t.ir = apply_defaults(t.ir).ir
    t.conv = t.conv.after(Event.VALIDATE)
    candidate = build_candidate(t.ir)
    t.candidate = candidate
    if candidate.can_freeze:
        t.conv = t.conv.after(Event.VALIDATION_PASSED).after(Event.FROZEN)
    else:
        t.conv = t.conv.after(Event.VALIDATION_FAILED)


# --- helpers ------------------------------------------------------------------------------------------------------


def _fail(code: ErrorCode, detail: str, **result: Any) -> RequestError:
    return RequestError(code, detail, dict(result))


def _need_event(ws: Workspace, event: Event) -> None:
    if event not in allowed_events(ws.conv.state):
        raise _fail(
            ErrorCode.WRONG_STATE,
            f"{event.value} is not allowed while the campaign is {ws.conv.state.value}",
        )


def _text_extraction(svc: Services, text: str, source: Source, turn: _Turn) -> None:
    """Run the model on the text and merge what it found. Raises RequestError when it cannot continue."""
    result: ExtractionResult = svc.extractor.run(text, turn.ir, source=source)
    if result.status is Status.OK and result.patch is not None:
        turn.warnings.extend(result.warnings)
        turn.absorb(merge_patch(turn.ir, result.patch, TurnContext(turn.turn, source, result.text)))
        return
    detail = "; ".join(i.message for i in result.guard_issues) or result.status.value
    if result.status is Status.INPUT_REJECTED:
        raise _fail(ErrorCode.INPUT_REJECTED, detail, issues=[i.code for i in result.guard_issues])
    if result.status is Status.MODEL_UNAVAILABLE:
        raise _fail(
            ErrorCode.MODEL_UNAVAILABLE, "the language model did not answer; try again shortly"
        )
    if result.status is Status.TOO_LONG:
        raise _fail(
            ErrorCode.INPUT_REJECTED, "the text is too long for the model", issues=["too_long"]
        )
    raise _fail(ErrorCode.NEEDS_REPHRASE, "the reply could not be read; please say it another way")


# --- handlers -----------------------------------------------------------------------------------------------------


def _interpret(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    state = ws.conv.state
    if state is State.DRAFT:
        t.conv = ws.conv.after(Event.MESSAGE)
    elif state is State.MODEL_UNAVAILABLE:
        t.conv = ws.conv.after(Event.RETRY)
    else:
        raise _fail(ErrorCode.WRONG_STATE, f"cannot start interpreting while {state.value}")
    text = str(req.payload.get("message") or ws.detail or "").strip()
    if not text:
        raise _fail(ErrorCode.INVALID_REQUEST, "there is no message to read")
    _text_extraction(svc, text, Source.EXTRACTION, t)
    _settle(t)


def _edit(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    text, ops = req.payload.get("text"), req.payload.get("ops")
    if (text is None) == (ops is None):
        raise _fail(ErrorCode.INVALID_REQUEST, "send either text or ops")
    if text is not None:
        _need_event(ws, Event.EDIT_WORDS)
        t.conv = ws.conv.after(Event.EDIT_WORDS)
        _text_extraction(svc, str(text), Source.EDIT_REQUEST, t)
        _settle(t)
        return
    _need_event(ws, Event.FIELD_EDIT_COMPLETE)
    if not isinstance(ops, list):
        raise _fail(ErrorCode.INVALID_REQUEST, "ops must be a list")
    try:
        patch = IRPatch(
            ops=[PatchOp(path=o["path"], op=o.get("op", "set"), value=o.get("value")) for o in ops]
        )
    except (ValidationError, KeyError, TypeError, AttributeError) as exc:
        raise _fail(ErrorCode.INVALID_REQUEST, f"bad ops: {exc.__class__.__name__}") from exc
    merge = merge_patch(t.ir, patch, TurnContext(t.turn, Source.DIRECT))
    bad = [r for r in merge.rejected if r.reason is not Reason.NO_CHANGE]
    if bad:
        raise _fail(
            ErrorCode.INVALID_REQUEST,
            "; ".join(f"{r.path}: {r.message}" for r in bad),
            rejected=[
                {"path": r.path, "reason": r.reason.value, "message": r.message} for r in bad
            ],
        )
    t.absorb(merge)
    t.conv = ws.conv
    _settle(t, direct_edit=True)


def _answer(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    _need_event(ws, Event.ANSWER)
    raw = req.payload.get("answers")
    if not isinstance(raw, list) or not raw:
        raise _fail(ErrorCode.INVALID_REQUEST, "answers must be a non-empty list")
    try:
        answers = [Answer.model_validate(a) for a in raw]
    except ValidationError as exc:
        raise _fail(ErrorCode.INVALID_REQUEST, "an answer is malformed") from exc
    ir, conv = t.ir, ws.conv
    errors: list[dict[str, str]] = []
    follow_ups: list[str] = []
    for answer in answers:
        out = answer_question(ir, conv, answer, t.turn)
        if out.error is not None:
            errors.append(
                {
                    "question_id": answer.question_id,
                    "code": out.error.code,
                    "message": out.error.message,
                }
            )
            continue
        ir, conv = out.ir, out.conv
        if out.merge:
            t.rejected.extend(out.merge.rejected)
        if out.follow_up:
            follow_ups.append(out.follow_up.reextract)
    if errors:
        raise _fail(
            ErrorCode.INVALID_ANSWER, f"{len(errors)} answer(s) could not be used", errors=errors
        )
    t.ir, t.conv = ir, conv.after(Event.ANSWER)
    for text in follow_ups:
        try:
            _text_extraction(svc, text, Source.EDIT_REQUEST, t)
        except RequestError as exc:
            t.warnings.append(f"could not read “{text[:60]}” for this campaign: {exc.detail}")
    _settle(t)


def _suggestion(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    sid, decision = req.payload.get("suggestion_id"), req.payload.get("decision")
    if not isinstance(sid, str) or decision not in ("accept", "reject"):
        raise _fail(ErrorCode.INVALID_REQUEST, "send suggestion_id and decision (accept or reject)")
    event = Event.SUGGESTION_ACCEPTED if decision == "accept" else Event.SUGGESTION_REJECTED
    _need_event(ws, event)
    try:
        merge = resolve_suggestion(t.ir, sid, decision, t.turn)
    except UnknownSuggestionError as exc:
        raise _fail(ErrorCode.INVALID_REQUEST, "no such suggestion") from exc
    except SuggestionResolvedError as exc:
        raise _fail(ErrorCode.WRONG_STATE, str(exc)) from exc
    t.absorb(merge)
    t.conv = ws.conv.after(event)
    if decision == "reject":
        t.candidate = None
        return
    if t.conv.state is State.READY_FOR_VALIDATION:
        _validate(t)
    else:
        _settle(t)


def _discard(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    _need_event(ws, Event.DISCARD)
    t.conv = ws.conv.after(Event.DISCARD)


_HANDLERS = {
    "interpret": _interpret,
    "edit": _edit,
    "answer": _answer,
    "suggestion": _suggestion,
    "discard": _discard,
}


def progress_state(req: Request, ws: Workspace) -> State | None:
    """The state to show while the request is worked on, or None to leave it."""
    event = {
        "interpret": Event.MESSAGE if ws.conv.state is State.DRAFT else Event.RETRY,
        "answer": Event.ANSWER,
        "edit": Event.EDIT_WORDS if req.payload.get("text") is not None else None,
    }.get(req.request_type)
    if event is None:
        return None
    try:
        return ws.conv.after(event).state
    except IllegalTransitionError:
        return None


def handle(req: Request, ws: Workspace, svc: Services) -> Outcome:
    """Run one request. Never raises for a bad request: the error is in the outcome."""
    ir = ws.ir or new_ir(ws.campaign_id, ws.name[:200], ws.detail[:5000])
    t = _Turn(ir=ir, conv=ws.conv, turn=ws.conv.turn + 1)
    error: RequestError | None = None
    try:
        _check_revision(req, ir)
        handler = _HANDLERS.get(req.request_type)
        if handler is None:
            raise _fail(
                ErrorCode.NOT_AVAILABLE
                if req.request_type in ("approve", "rollback")
                else ErrorCode.INVALID_REQUEST,
                "publishing arrives in a later release"
                if req.request_type in ("approve", "rollback")
                else f"unknown request type {req.request_type!r}",
            )
        handler(req, ws, t, svc)
    except RequestError as exc:
        error = exc
    if error is not None:
        # Nothing the request tried is kept, except that a model that is down is remembered.
        conv = ws.conv
        if error.code is ErrorCode.MODEL_UNAVAILABLE and conv.state in (
            State.DRAFT,
            State.PUBLISHED,
        ):
            conv = conv.model_copy(update={"state": State.MODEL_UNAVAILABLE})
        elif error.code is ErrorCode.MODEL_UNAVAILABLE:
            conv = _model_down(ws.conv)
        t = _Turn(ir=ir, conv=conv, turn=ws.conv.turn)
        t.warnings = []
    return _outcome(req, ws, t, error)


def _model_down(conv: ConversationState) -> ConversationState:
    try:
        return conv.after(Event.MODEL_DOWN)
    except IllegalTransitionError:
        return conv.model_copy(update={"state": State.MODEL_UNAVAILABLE})


def _check_revision(req: Request, ir: CampaignIR) -> None:
    if req.request_type == "edit" and req.base_revision is None:
        raise _fail(
            ErrorCode.MISSING_BASE_REVISION, "an edit must say which revision it is based on"
        )
    if req.base_revision is not None and req.base_revision != ir.meta.revision:
        raise _fail(
            ErrorCode.STALE_REVISION,
            f"the campaign is at revision {ir.meta.revision}, not {req.base_revision}",
            revision=ir.meta.revision,
        )


def _outcome(req: Request, ws: Workspace, t: _Turn, error: RequestError | None) -> Outcome:
    not_applied: tuple[NotApplied, ...] = (
        t.candidate.not_applied if t.candidate else compile_overlay(t.ir).not_applied
    )
    last = LastRequest(
        id=req.id,
        type=req.request_type,
        outcome="failed" if error else "ok",
        code=error.code.value if error else None,
    )
    view = build_view(
        t.ir,
        t.conv,
        candidate=t.candidate,
        not_applied=not_applied,
        notices=tuple(t.notices),
        warnings=tuple(t.warnings),
        last_request=last,
    )
    prev = ws.previous_view
    if error is not None and prev is not None:
        # A refused request keeps what the dashboard already shows; only the outcome is new.
        view = prev.model_copy(
            update={
                "state": t.conv.state.value,
                "last_request": last,
                "warnings": view.warnings,
            }
        )
    elif view.candidate is None and t.conv.state is State.AWAITING_REVIEW and prev is not None:
        view = view.model_copy(update={"candidate": prev.candidate})  # e.g. a rejected suggestion
    frozen = t.candidate if (t.candidate and t.candidate.can_freeze and error is None) else None
    result: dict[str, Any] = {
        "state": t.conv.state.value,
        "revision": t.ir.meta.revision,
        "open_questions": [q.id for q in t.conv.open],
        "notices": [n.code for n in t.notices],
        "warnings": list(t.warnings),
        "candidate_sha256": t.candidate.sha256 if t.candidate else None,
    }
    if error is not None:
        result = {**result, **error.result}
    return Outcome(t.ir, t.conv, view, frozen, result, error)

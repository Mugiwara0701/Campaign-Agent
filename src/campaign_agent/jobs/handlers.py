"""What each kind of request does. Pure: a request and a loaded workspace in, an outcome out.

No database here. The model is called through the extractor; everything else is deterministic. The caller
(jobs/manager.py) loads the workspace and stores the outcome in one transaction, so a request that dies
half-way leaves nothing half-applied and can simply be run again.

Every request runs the conversation to a resting state: questions to answer, a candidate to review, a
validation failure to fix, or the model being unavailable.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from campaign_agent.compiler.build import Candidate, build_candidate, validate_overlay_text
from campaign_agent.compiler.compiler import NotApplied, compile_overlay
from campaign_agent.contract.model import CONFIG_VERSION, LeadgenFile
from campaign_agent.conversation.state import ConversationState
from campaign_agent.conversation.state_machine import (
    Event,
    IllegalTransitionError,
    State,
    allowed_events,
)
from campaign_agent.conversation.turn import Notice, plan_turn
from campaign_agent.extractor.extractor import ExtractionResult, Extractor, Status
from campaign_agent.guard.input_guard import looks_like_secret
from campaign_agent.ir.models import CampaignIR, new_ir
from campaign_agent.ir.patch import IRPatch, PatchOp, ResearchNeed
from campaign_agent.jobs.requests import ErrorCode, Request, RequestError
from campaign_agent.ma_status import MaStatus
from campaign_agent.merger.patch_merger import (
    MergeResult,
    Reason,
    Rejected,
    Source,
    SuggestionResolvedError,
    TurnContext,
    UnknownSuggestionError,
    add_suggestions,
    apply_defaults,
    merge_patch,
    resolve_suggestion,
)
from campaign_agent.questions.builder import answer_question
from campaign_agent.questions.models import Answer
from campaign_agent.research import Researcher
from campaign_agent.research.brief import Brief, BriefWriter
from campaign_agent.validators.downstream import Downstream
from campaign_agent.view import BriefItemView, BriefView, LastRequest, ReviewView, build_view

log = logging.getLogger("campaign_agent.handlers")


@dataclass(frozen=True)
class StoredCandidate:
    """The newest stored candidate: what an approval has to name exactly."""

    version_no: int
    sha256: str
    yaml: str


@dataclass(frozen=True)
class VersionInfo:
    version_no: int
    sha256: str
    approved: bool
    live: bool


@dataclass(frozen=True)
class Workspace:
    """Everything a request needs, loaded from the database."""

    campaign_id: str
    name: str
    detail: str
    ir: CampaignIR | None
    conv: ConversationState
    previous_view: ReviewView | None = None
    candidate: StoredCandidate | None = None
    versions: tuple[VersionInfo, ...] = ()


@dataclass(frozen=True)
class Services:
    extractor: Extractor
    base: LeadgenFile | None = (
        None  # the base config approvals are checked against; None = the packaged one
    )
    # Level 6: the Marketing Agent's own loader. None = not configured (the level shows as skipped).
    downstream: Downstream | None = None
    # What the Marketing Agent reports (read from the database when asked). None = it never reported.
    ma_status: Callable[[], MaStatus | None] | None = None
    # Bounded concept research. None = switched off (the default): nothing is searched.
    researcher: Researcher | None = None
    # The research brief on each new version under review (research/brief.py). None = switched off.
    brief_writer: BriefWriter | None = None


@dataclass(frozen=True)
class Action:
    """A change to what is live. Done by the database functions, in the same transaction as the state."""

    kind: Literal["publish", "activate", "deactivate", "start_pipeline", "stop_pipeline"]
    version_no: int | None = None
    sha256: str | None = None
    approver: str = ""
    # After the publish is committed, also start (or restart) the Marketing Agent's pipeline, as asked in the approval.
    start_pipeline: bool = False
    # After the change is committed, stop the Marketing Agent's pipeline: a campaign that is switched off must not keep working.
    stop_pipeline: bool = False


@dataclass
class Outcome:
    ir: CampaignIR
    conv: ConversationState
    view: ReviewView
    candidate: Candidate | None = None  # to store as a new version, when it can be frozen
    result: dict[str, Any] = field(default_factory=dict)
    error: RequestError | None = None
    action: Action | None = None


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
    action: Action | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    downstream: Downstream | None = None

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
    candidate = build_candidate(t.ir, downstream=t.downstream)
    t.candidate = candidate
    if candidate.can_freeze:
        t.conv = t.conv.after(Event.VALIDATION_PASSED).after(Event.FROZEN)
    else:
        t.conv = t.conv.after(Event.VALIDATION_FAILED)


# --- helpers ------------------------------------------------------------------------------------------------------


MAX_PAYLOAD_CHARS = (
    100_000  # far above any real request; the text itself is limited further by the input guard
)


def _check_size(req: Request) -> None:
    """The dashboard's payload is untrusted input like any other: refuse an absurd one before reading it."""
    try:
        size = len(json.dumps(req.payload, default=str))
    except (TypeError, ValueError):
        size = MAX_PAYLOAD_CHARS + 1
    if size > MAX_PAYLOAD_CHARS:
        raise _fail(ErrorCode.INVALID_REQUEST, "the request is too large")


def _fail(code: ErrorCode, detail: str, **result: Any) -> RequestError:
    return RequestError(code, detail, dict(result))


def _need_event(ws: Workspace, event: Event) -> None:
    if event not in allowed_events(ws.conv.state):
        raise _fail(
            ErrorCode.WRONG_STATE,
            f"{event.value} is not allowed while the campaign is {ws.conv.state.value}",
        )


def _research(svc: Services, turn: _Turn, needs: list[ResearchNeed]) -> None:
    """Look up what the model did not know, if research is on. Fail-soft: the campaign goes on either way.

    What comes back is only ever stored as a pending suggestion; it never changes a value.
    """
    if not needs or svc.researcher is None:
        return
    turn.conv = turn.conv.after(Event.RESEARCH_NEEDED)
    found = svc.researcher.run(needs, turn.ir)
    if found.suggestions:
        # Only the stored suggestions are kept: a refused one is not a problem with the user's text.
        turn.ir = add_suggestions(turn.ir, list(found.suggestions), turn.turn).ir


def _text_extraction(svc: Services, text: str, source: Source, turn: _Turn) -> None:
    """Run the model on the text and merge what it found. Raises RequestError when it cannot continue."""
    result: ExtractionResult = svc.extractor.run(text, turn.ir, source=source)
    if result.status is Status.OK and result.patch is not None:
        turn.warnings.extend(result.warnings)
        turn.absorb(merge_patch(turn.ir, result.patch, TurnContext(turn.turn, source, result.text)))
        _research(svc, turn, result.patch.research_needs)
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
        event = _edit_event(ws)
        _need_event(ws, event)
        t.conv = ws.conv.after(event)
        _text_extraction(svc, str(text), Source.EDIT_REQUEST, t)
        _settle(t)
        return
    if ws.conv.state is State.PUBLISHED:
        # A change after publication starts a new candidate; the published version keeps running.
        t.conv = ws.conv.after(Event.CHANGE_REQUEST)
        direct_edit = False
    else:
        _need_event(ws, Event.FIELD_EDIT_COMPLETE)
        t.conv = ws.conv
        direct_edit = True
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
    _settle(t, direct_edit=direct_edit)


def _edit_event(ws: Workspace) -> Event:
    """A change in words: in review it is an edit; after publication it is a change request."""
    return Event.CHANGE_REQUEST if ws.conv.state is State.PUBLISHED else Event.EDIT_WORDS


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


def _approve(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    """Approve exactly the candidate the user reviewed, and make it live."""
    payload = req.payload
    start_pipeline = payload.get("start_pipeline", False)
    if not isinstance(start_pipeline, bool):
        raise _fail(ErrorCode.INVALID_REQUEST, "start_pipeline must be true or false")
    version, sha = payload.get("version"), payload.get("sha256")
    if not isinstance(version, int) or isinstance(version, bool) or not isinstance(sha, str):
        raise _fail(
            ErrorCode.INVALID_REQUEST, "send version (a number) and sha256 (the text you reviewed)"
        )
    live = next((v for v in ws.versions if v.live), None)
    if (
        ws.conv.state is State.PUBLISHED
        and live
        and (live.version_no, live.sha256) == (version, sha)
    ):
        t.conv = ws.conv  # a repeated approval of what is already live: nothing to do
        t.extra = {
            "published": True,
            "version": version,
            "effective_from": "next_pass",
            "already": True,
        }
        if (
            start_pipeline
        ):  # approving again with the flag still starts it: the first attempt may have failed
            t.action = Action("start_pipeline", version, sha, req.requested_by, start_pipeline=True)
        return
    if ws.conv.state is State.AWAITING_REVIEW:
        event = Event.APPROVE
    elif ws.conv.state is State.PUBLISH_FAILED:
        event = Event.RETRY_PUBLISH
    else:
        raise _fail(
            ErrorCode.WRONG_STATE, f"nothing to approve while the campaign is {ws.conv.state.value}"
        )
    candidate = ws.candidate
    if candidate is None:
        raise _fail(ErrorCode.WRONG_STATE, "there is no candidate to approve")
    if (candidate.version_no, candidate.sha256) != (version, sha):
        raise _fail(
            ErrorCode.STALE_APPROVAL,
            "that is not the latest candidate, or its text changed; review the current one",
            current={"version": candidate.version_no, "sha256": candidate.sha256},
        )
    report = validate_overlay_text(candidate.yaml, svc.base, svc.downstream)
    if not report.passed:
        raise RequestError(
            ErrorCode.PUBLISH_BLOCKED,
            "the candidate no longer passes the checks: " + report.errors[0].message,
            {
                "issues": [
                    {"level": i.level, "code": i.code, "path": i.path, "message": i.message}
                    for i in report.errors
                ]
            },
            conv=ws.conv.after(Event.PUBLISH_BLOCKED),
        )
    _refuse_unloadable_contract(svc, CONFIG_VERSION)
    t.conv = ws.conv.after(event).after(Event.PUBLISHED)
    t.action = Action("publish", version, sha, req.requested_by, start_pipeline)
    t.extra = {"published": True, "version": version, "effective_from": "next_pass"}


def _refuse_unloadable_contract(svc: Services, contract_version: str) -> None:
    """Do not publish what the Marketing Agent has said it cannot load. Unknown (never reported, or offline) is allowed."""
    status = svc.ma_status() if svc.ma_status else None
    if status is None or status.supports(contract_version) is not False:
        return
    raise RequestError(
        ErrorCode.PUBLISH_BLOCKED,
        f"the Marketing Agent loads contract version(s) "
        f"{', '.join(status.supported_contract_versions)}, "
        f"not {contract_version}; update the Marketing Agent first",
        {
            "contract_version": contract_version,
            "ma_supported": list(status.supported_contract_versions),
        },
    )


def _rollback(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    """Make a version that was approved before live again. Does not change the conversation. Like an approval it also
    starts the Marketing Agent on it ("Make live" after a switch-off must bring the work back); `start_pipeline: false`
    only makes it live."""
    start_pipeline = req.payload.get("start_pipeline", True)
    if not isinstance(start_pipeline, bool):
        raise _fail(ErrorCode.INVALID_REQUEST, "start_pipeline must be true or false")
    version = req.payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise _fail(ErrorCode.INVALID_REQUEST, "send version (a number)")
    match = next((v for v in ws.versions if v.version_no == version), None)
    if match is None:
        raise _fail(ErrorCode.INVALID_REQUEST, f"there is no version {version}")
    if not match.approved:
        raise _fail(
            ErrorCode.WRONG_STATE,
            f"version {version} was never approved; only approved versions can go live",
        )
    t.conv = ws.conv
    t.extra = {"live_version": version, "effective_from": "next_pass"}
    if match.live:
        t.extra["already"] = True
    else:
        t.action = Action("activate", version, start_pipeline=start_pipeline)


def _deactivate(req: Request, ws: Workspace, t: _Turn, svc: Services) -> None:
    """Stop: nothing of this campaign is live. Its versions stay and can be made live again."""
    t.conv = ws.conv
    t.extra = {"live_version": None}
    if any(v.live for v in ws.versions):
        t.action = Action("deactivate", stop_pipeline=True)
    else:
        t.extra["already"] = True
        # Nothing was live, but the Marketing Agent may still be running (it falls back to its own settings): switching
        # off again stops it.
        t.action = Action("stop_pipeline", stop_pipeline=True)


_HANDLERS = {
    "interpret": _interpret,
    "edit": _edit,
    "answer": _answer,
    "suggestion": _suggestion,
    "discard": _discard,
    "approve": _approve,
    "rollback": _rollback,
    "deactivate": _deactivate,
}


def progress_state(req: Request, ws: Workspace) -> State | None:
    """The state to show while the request is worked on, or None to leave it."""
    event = {
        "interpret": Event.MESSAGE if ws.conv.state is State.DRAFT else Event.RETRY,
        "answer": Event.ANSWER,
        "edit": _edit_event(ws) if req.payload.get("text") is not None else None,
    }.get(req.request_type)
    if event is None:
        return None
    try:
        return ws.conv.after(event).state
    except IllegalTransitionError:
        return None


def handle(req: Request, ws: Workspace, svc: Services) -> Outcome:
    """Run one request. Never raises for a bad request: the error is in the outcome."""
    # The campaign's own name and message are copied into the stored campaign and shown back in the review.
    # If one of them holds something shaped like a secret, it is left out: the text itself is refused later.
    name = "" if looks_like_secret(ws.name) else ws.name[:200]
    detail = "" if looks_like_secret(ws.detail) else ws.detail[:5000]
    ir = ws.ir or new_ir(ws.campaign_id, name, detail)
    t = _Turn(ir=ir, conv=ws.conv, turn=ws.conv.turn + 1, downstream=svc.downstream)
    error: RequestError | None = None
    try:
        _check_size(req)
        _check_revision(req, ir)
        handler = _HANDLERS.get(req.request_type)
        if handler is None:
            raise _fail(ErrorCode.INVALID_REQUEST, f"unknown request type {req.request_type!r}")
        handler(req, ws, t, svc)
    except RequestError as exc:
        error = exc
    if error is not None:
        # Nothing the request tried is kept, except that a model that is down is remembered.
        conv = ws.conv
        if error.conv is not None:
            conv = error.conv
        elif error.code is ErrorCode.MODEL_UNAVAILABLE and conv.state in (
            State.DRAFT,
            State.PUBLISHED,
        ):
            conv = conv.model_copy(update={"state": State.MODEL_UNAVAILABLE})
        elif error.code is ErrorCode.MODEL_UNAVAILABLE:
            conv = _model_down(ws.conv)
        t = _Turn(ir=ir, conv=conv, turn=ws.conv.turn, downstream=svc.downstream)
        t.warnings = []
    return _outcome(req, ws, t, error, svc)


_KEEPS_PREVIEW = frozenset({State.AWAITING_REVIEW, State.PUBLISHED, State.PUBLISH_FAILED})


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


def _brief_view(brief: Brief, sha256: str) -> BriefView:
    def items(found: tuple[Any, ...]) -> tuple[BriefItemView, ...]:
        return tuple(BriefItemView(text=i.text, sources=i.sources) for i in found)

    return BriefView(
        for_sha256=sha256,
        status=brief.status,
        company_types=items(brief.company_types),
        buying_signals=items(brief.buying_signals),
        key_terms=items(brief.key_terms),
        not_confirmed=brief.not_confirmed,
        queries=brief.queries,
        searched_at=brief.searched_at,
    )


def _brief(
    svc: Services | None,
    t: _Turn,
    view: ReviewView,
    prev: ReviewView | None,
    error: RequestError | None,
) -> BriefView | None:
    """The brief for the version on screen: the one already written for it, or a new one for a version just frozen.
    Never written for a refused request or an unvalidated candidate; research off means no brief."""
    shown = view.candidate.sha256 if view.candidate else None
    old = prev.brief if prev is not None else None
    if shown is None:
        return None
    if old is not None and old.for_sha256 == shown:
        return old
    fresh = t.candidate is not None and t.candidate.can_freeze and t.candidate.sha256 == shown
    if error is not None or not fresh or svc is None or svc.brief_writer is None:
        return None
    brief = svc.brief_writer.write(t.ir)
    for note in brief.notes:
        log.info("research brief note", extra={"note": note})
    return _brief_view(brief, shown)


def _outcome(
    req: Request,
    ws: Workspace,
    t: _Turn,
    error: RequestError | None,
    svc: Services | None = None,
) -> Outcome:
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
    elif view.candidate is None and prev is not None and t.conv.state in _KEEPS_PREVIEW:
        # The version under review, or the one just published, stays on screen.
        view = view.model_copy(update={"candidate": prev.candidate})
    view = view.model_copy(update={"brief": _brief(svc, t, view, prev, error)})
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
    else:
        result.update(t.extra)
    return Outcome(t.ir, t.conv, view, frozen, result, error, t.action if error is None else None)

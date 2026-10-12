"""One turn after the IR changed: what to fix, what to tell, what to ask, and where to go next."""

from collections.abc import Sequence
from dataclasses import dataclass

from campaign_agent.conversation.state import ConversationState
from campaign_agent.conversation.state_machine import Event
from campaign_agent.gap.engine import Finding, Reason, analyze
from campaign_agent.ir.models import CampaignIR
from campaign_agent.ir.patch import IRPatch, PatchOp
from campaign_agent.merger.patch_merger import Reason as MergeReason
from campaign_agent.merger.patch_merger import Rejected, Source, TurnContext, merge_patch
from campaign_agent.questions.builder import close_resolved, new_questions, register
from campaign_agent.questions.models import Question


@dataclass(frozen=True)
class Notice:
    """Something to tell the user that is not a question."""

    code: str  # kept_confirmed | auto_fix
    path: str
    message: str


@dataclass(frozen=True)
class TurnPlan:
    ir: CampaignIR  # after any automatic fix
    conv: ConversationState  # with the new questions registered
    findings: tuple[Finding, ...]
    new_questions: tuple[Question, ...]
    notices: tuple[Notice, ...]
    event: Event  # GAPS_FOUND while any question is open, otherwise COMPLETE


def _auto_fix(
    ir: CampaignIR, findings: list[Finding], turn: int
) -> tuple[CampaignIR, list[Notice]]:
    """One deterministic change is made without asking, and shown. Confirmed values are kept."""
    notices: list[Notice] = []
    for f in findings:
        fix = f.detail.get("fix") if f.reason is Reason.PAGES_STARVED else None
        if not fix:
            continue
        path, value = fix
        result = merge_patch(
            ir,
            IRPatch(ops=[PatchOp(path=path, op="set", value=value)]),
            TurnContext(turn, Source.SYSTEM),
        )
        if result.applied:
            ir = result.ir
            notices.append(
                Notice(
                    "auto_fix", path, f"Raised {path} to {value} so every query gets pages to open."
                )
            )
    return ir, notices


def plan_turn(
    ir: CampaignIR, conv: ConversationState, *, rejected: Sequence[Rejected] = (), turn: int
) -> TurnPlan:
    findings = analyze(ir, rejected)
    ir, notices = _auto_fix(ir, findings, turn)
    if notices:
        findings = analyze(ir, rejected)
    notices.extend(
        Notice("kept_confirmed", r.path, f"{r.message} ({r.kept_value!r}).")
        for r in rejected
        if r.reason is MergeReason.LOCKED
    )
    conv = close_resolved(conv, {f.key for f in findings}, turn)
    questions = new_questions(findings, ir, conv)
    conv = register(conv, questions, turn)
    event = Event.GAPS_FOUND if conv.open else Event.COMPLETE
    return TurnPlan(ir, conv, tuple(findings), tuple(questions), tuple(notices), event)

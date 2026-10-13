"""Runs benchmark cases through the real extractor, merger and gap engine and records what happened.

Scoring is outcome-based: the CHANGES a model made to the campaign (values added or removed, per field) are compared
with the changes the gold operations make. Wording, order and how many operations it took do not matter.
"""

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from campaign_agent.conversation.state import ConversationState
from campaign_agent.conversation.turn import plan_turn
from campaign_agent.evaluation.dataset import Case, GoldOp
from campaign_agent.extractor.extractor import Extractor, Status
from campaign_agent.ir.models import CampaignIR, new_ir
from campaign_agent.ir.patch import IRPatch, PatchOp
from campaign_agent.ir.registry import REGISTRY, get_value
from campaign_agent.merger.patch_merger import (
    Reason,
    Rejected,
    Source,
    TurnContext,
    add_suggestions,
    merge_patch,
)
from campaign_agent.research import Researcher

_NEVER_WRITABLE = {Reason.UNKNOWN_PATH, Reason.NOT_PATCHABLE, Reason.APPROVAL_FIXED}
_REPAIRING = {"retry", "step_down", "repair", "fallback"}


def _norm(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def field_values(ir: CampaignIR) -> set[str]:
    """Every value the campaign holds, as 'path=value' (one entry per list item)."""
    out: set[str] = set()
    for path in REGISTRY:
        value = get_value(ir, path)
        if value is None or value == []:
            continue
        for item in value if isinstance(value, list) else [value]:
            out.add(f"{path}={_norm(item)}")
    return out


def changes(before: CampaignIR, after: CampaignIR) -> set[str]:
    a, b = field_values(before), field_values(after)
    return {f"+{x}" for x in b - a} | {f"-{x}" for x in a - b}


def _direct(ir: CampaignIR, ops: list[GoldOp], turn: int) -> CampaignIR:
    patch = IRPatch(ops=[PatchOp(path=o.path, op=o.op, value=o.value) for o in ops])
    return merge_patch(ir, patch, TurnContext(turn, Source.DIRECT)).ir


def start_ir(case: Case) -> CampaignIR:
    return _direct(new_ir(case.id), case.base, 0)


def gold_ir(case: Case) -> CampaignIR:
    ir = start_ir(case)
    for i, turn in enumerate(case.turns, 1):
        ir = _direct(ir, turn.ops, i)
    return ir


@dataclass
class ResearchRun:
    needs: int = 0
    suggestions_produced: int = 0
    suggestions_stored: int = 0
    good: int = 0
    latency_s: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class CaseResult:
    id: str
    category: str
    split: str
    expect_status: str
    statuses: list[str]
    status_matched: bool
    first_try_valid: bool = False
    repairs: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    exact: bool = False
    forbidden_applied: bool = False
    unsupported_generated: bool = False
    unsupported_expected: int = 0
    unsupported_found: int = 0
    must_ask: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    asked_ok: bool = True
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    changes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    research: ResearchRun | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _research(
    researcher: Researcher, case: Case, ir: CampaignIR, needs: list[Any], turn: int
) -> tuple[CampaignIR, ResearchRun]:
    run = ResearchRun(needs=len(needs))
    started = time.perf_counter()
    found = researcher.run(needs, ir)
    run.latency_s = time.perf_counter() - started
    run.notes = list(found.notes)
    run.suggestions_produced = len(found.suggestions)
    if found.suggestions:
        ir = add_suggestions(ir, list(found.suggestions), turn).ir
    run.suggestions_stored = len(ir.suggestions)
    good = {_norm(g) for r in case.research for g in r.good}
    for s in ir.suggestions:
        values = s.value if isinstance(s.value, list) else [s.value]
        run.good += sum(1 for v in values if _norm(v) in good)
    return ir, run


def run_case(
    case: Case, extractor: Extractor, *, researcher: Researcher | None = None
) -> CaseResult:
    ir0 = start_ir(case)
    ir = ir0
    result = CaseResult(
        case.id, case.category, case.split, case.expect_status, [], False, must_ask=case.must_ask
    )
    rejected: list[Rejected] = []
    all_rejected: list[Rejected] = []
    applied_paths: list[str] = []
    attempts_all = []
    for n, turn in enumerate(case.turns, 1):
        source = Source.EXTRACTION if turn.source == "message" else Source.EDIT_REQUEST
        extraction = extractor.run(turn.text, ir, source=source)
        result.statuses.append(extraction.status.value)
        attempts_all.extend(extraction.attempts)
        if extraction.status is not Status.OK or extraction.patch is None:
            break
        merged = merge_patch(ir, extraction.patch, TurnContext(n, source, extraction.text))
        ir, rejected = merged.ir, merged.rejected
        all_rejected.extend(rejected)
        applied_paths.extend(a.path for a in merged.applied)
        if researcher is not None and n == 1 and extraction.patch.research_needs:
            ir, result.research = _research(
                researcher, case, ir, list(extraction.patch.research_needs), n
            )

    result.status_matched = result.statuses[-1] == case.expect_status
    result.calls = len(attempts_all)
    result.latency_s = sum(a.latency_s for a in attempts_all)
    result.prompt_tokens = sum(a.prompt_tokens or 0 for a in attempts_all)
    result.completion_tokens = sum(a.completion_tokens or 0 for a in attempts_all)
    result.repairs = sum(1 for a in attempts_all if a.kind == "repair")
    result.first_try_valid = (
        result.statuses == ["ok"] * len(case.turns)
        and not any(a.kind in _REPAIRING for a in attempts_all)
    )
    if case.expect_status != "ok" or result.statuses[-1] != "ok":
        return result

    actual, gold = changes(ir0, ir), changes(ir0, gold_ir(case))
    result.tp, result.fp, result.fn = len(actual & gold), len(actual - gold), len(gold - actual)
    result.exact = actual == gold
    result.changes = sorted(actual)
    result.extra, result.missing = sorted(actual - gold), sorted(gold - actual)
    result.forbidden_applied = any(
        p == f or p.startswith(f + ".") for p in applied_paths for f in case.forbidden_paths
    )
    result.unsupported_generated = any(r.reason in _NEVER_WRITABLE for r in all_rejected)
    expected_kinds = {u.kind for t in case.turns for u in t.unsupported}
    found_kinds = {u.kind for u in ir.unsupported}
    result.unsupported_expected = len(expected_kinds)
    result.unsupported_found = len(expected_kinds & found_kinds)
    plan = plan_turn(ir, ConversationState(), rejected=rejected, turn=len(case.turns) + 1)
    result.asked = sorted({f.reason.value for f in plan.findings})
    result.asked_ok = set(case.must_ask) <= set(result.asked)
    return result


def run_cases(
    cases: list[Case],
    make_extractor: Callable[[Case], Extractor],
    *,
    researcher: Researcher | None = None,
    progress: Callable[[int, int, CaseResult], None] | None = None,
) -> list[CaseResult]:
    """`make_extractor(case)` returns the Extractor for that case (a fresh scripted one for the oracle)."""
    out: list[CaseResult] = []
    for i, case in enumerate(cases, 1):
        out.append(run_case(case, make_extractor(case), researcher=researcher))
        if progress:
            progress(i, len(cases), out[-1])
    return out

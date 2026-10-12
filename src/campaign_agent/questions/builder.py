"""Questions from findings, and what an answer does. Templated: no model writes a question.

Rules:
  * at most MAX_OPEN (3) unanswered questions at a time;
  * a question is asked once per key; after it is answered or skipped it is not asked again;
  * "skip" is allowed for gaps and unclear values (the default applies later). Conflicts and off-target
    wording cannot be skipped: the campaign cannot be validated until they are resolved;
  * an answer is the user's own change and goes through the merger as `Source.ANSWER` (confirmed).
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, get_args

from campaign_agent.contract.vocabularies import base_config
from campaign_agent.conversation.state import MAX_OPEN, AskedQuestion, ConversationState
from campaign_agent.extractor.prompts import DESCRIPTIONS
from campaign_agent.gap.engine import PRIORITY, Finding, Kind, Reason
from campaign_agent.guard.input_guard import check_input
from campaign_agent.ir.models import CampaignIR, Role
from campaign_agent.ir.patch import IRPatch, PatchOp, UnsupportedClaim
from campaign_agent.ir.registry import REGISTRY
from campaign_agent.merger.patch_merger import MergeResult, Source, TurnContext, merge_patch
from campaign_agent.merger.patch_merger import Reason as MergeReason
from campaign_agent.questions.models import (
    PLACEHOLDER,
    Answer,
    AnswerError,
    Choice,
    Effect,
    FollowUp,
    Question,
)

_QUOTE_LEN = 120


def _qid(key: str) -> str:
    return "q_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def _short(text: str) -> str:
    return text if len(text) <= _QUOTE_LEN else text[: _QUOTE_LEN - 1] + "…"


def _join(items: Any) -> str:
    return ", ".join(str(i) for i in items)


def _op_add(path: str, value: str) -> PatchOp:
    return PatchOp(path=path, op="add", value=[value])


def _op_set(path: str, value: Any) -> PatchOp:
    return PatchOp(path=path, op="set", value=value)


def _op_remove(path: str, value: str) -> PatchOp:
    return PatchOp(path=path, op="remove", value=[value])


def _op_clear(path: str) -> PatchOp:
    return PatchOp(path=path, op="clear")


def _choices(*labels_effects: tuple[str, Effect]) -> tuple[Choice, ...]:
    return tuple(
        Choice(id=f"c{i}", label=label, effect=eff) for i, (label, eff) in enumerate(labels_effects)
    )


def _from_list(path: str, labels: list[str]) -> tuple[Choice, ...]:
    return _choices(*((label, Effect(ops=(_op_add(path, label),))) for label in labels))


def _text_add(path: str) -> Effect:
    return Effect(ops=(PatchOp(path=path, op="add", value=PLACEHOLDER),))


# --- one question per finding ----------------------------------------------------------------------------------


def build_question(f: Finding, ir: CampaignIR) -> Question:
    maker = _MAKERS[f.reason]
    q = maker(f, ir)
    return q.model_copy(update={"id": _qid(f.key), "key": f.key, "priority": PRIORITY[f.kind]})


def _q(kind: Kind, text: str, **kw: Any) -> Question:
    return Question(id="", key="", kind=kind.value, text=text, **kw)


def _draft_below_qualify(f: Finding, ir: CampaignIR) -> Question:
    d = f.detail
    return _q(
        Kind.CONFLICT,
        f"These two settings conflict: the draft score ({d['min_draft']}) is below the minimum score "
        f"({d['min_qualify']}). A company must score at least the draft score to get an email. "
        "Which should change?",
        choices=_choices(
            (
                f"Raise the draft score to {d['min_qualify']}",
                Effect(ops=(_op_set("qualification.min_draft", d["min_qualify"]),)),
            ),
            (
                f"Lower the minimum score to {d['min_draft']}",
                Effect(ops=(_op_set("qualification.min_qualify", d["min_draft"]),)),
            ),
        ),
        skippable=False,
    )


def _results_below_pages(f: Finding, ir: CampaignIR) -> Question:
    d = f.detail
    return _q(
        Kind.CONFLICT,
        f"Each query reads {d['results_per_query']} results but is told to open {d['pages_per_query']} pages. "
        "It cannot open more pages than it reads. Which should change?",
        choices=_choices(
            (
                f"Read {d['pages_per_query']} results per query",
                Effect(ops=(_op_set("limits.results_per_query", d["pages_per_query"]),)),
            ),
            (
                f"Open {d['results_per_query']} pages per query",
                Effect(ops=(_op_set("limits.pages_per_query", d["results_per_query"]),)),
            ),
        ),
        skippable=False,
    )


def _pages_starved(f: Finding, ir: CampaignIR) -> Question:
    d = f.detail
    cap, queries, pages = d["max_pages_per_run"], d["queries_per_run"], d["pages_per_query"]
    options: list[tuple[str, Effect]] = []
    if (fewer := cap // pages) >= 1:
        options.append(
            (
                f"Run {fewer} queries per run",
                Effect(ops=(_op_set("limits.queries_per_run", fewer),)),
            )
        )
    if (shallower := cap // queries) >= 1:
        options.append(
            (
                f"Open {shallower} pages per query",
                Effect(ops=(_op_set("limits.pages_per_query", shallower),)),
            )
        )
    return _q(
        Kind.CONFLICT,
        f"{queries} queries x {pages} pages needs {d['needed']} pages, but a run may open only {cap}, so the "
        "last queries would get no pages. Which should change?",
        choices=_choices(*options),
        skippable=False,
    )


def _employee_range(f: Finding, ir: CampaignIR) -> Question:
    lo, hi = f.detail["min"], f.detail["max"]
    return _q(
        Kind.CONFLICT,
        f"The company size minimum ({lo}) is above the maximum ({hi}). What did you mean?",
        choices=_choices(
            (
                f"Between {hi} and {lo} employees",
                Effect(
                    ops=(_op_set("audience.employee_min", hi), _op_set("audience.employee_max", lo))
                ),
            ),
            ("Ignore the maximum", Effect(ops=(_op_clear("audience.employee_max"),))),
            ("Ignore the minimum", Effect(ops=(_op_clear("audience.employee_min"),))),
        ),
        skippable=False,
    )


def _target_excluded(f: Finding, ir: CampaignIR) -> Question:
    d = f.detail
    said = f" (you said: “{_short(d['target_quote'])}”)" if d["target_quote"] else ""
    also = f" (you said: “{_short(d['excluded_quote'])}”)" if d["excluded_quote"] else ""
    return _q(
        Kind.CONFLICT,
        f"“{d['item']}” is both something to look for{said} and something to exclude{also}. Which should we keep?",
        choices=_choices(
            ("Look for it", Effect(ops=(_op_remove(d["excluded_path"], d["excluded_item"]),))),
            ("Exclude it", Effect(ops=(_op_remove(d["target_path"], d["item"]),))),
        ),
        skippable=False,
    )


def _supplier_wording(f: Finding, ir: CampaignIR) -> Question:
    item, word, path = f.detail["item"], f.detail["word"], f.path
    scalar = REGISTRY[path].kind == "scalar"
    drop = _op_clear(path) if scalar else _op_remove(path, item)
    add = PatchOp(path=path, op="set" if scalar else "add", value=PLACEHOLDER)
    return _q(
        Kind.DRIFT,
        f"“{item}” describes sellers (it contains “{word}”). We look for companies that build products and "
        "may need outside engineering, not for companies that sell them. Remove it, or name what they build.",
        choices=_choices(("Remove it", Effect(ops=(drop,)))),
        text_effect=Effect(ops=(drop, add)),
        skippable=False,
    )


def _value_unclear(f: Finding, ir: CampaignIR) -> Question:
    path = f.path
    what = DESCRIPTIONS[path]
    text = f"I could not read a clear value for: {what}. What should it be?"
    if path == "contacts.preferred_roles":
        roles = list(get_args(Role))
        return _q(
            Kind.UNCLEAR,
            "Which decision-maker roles should we look for?",
            choices=_from_list(path, roles),
            multi=True,
        )
    op = PatchOp(path=path, op="add" if REGISTRY[path].kind == "list" else "set", value=PLACEHOLDER)
    return _q(Kind.UNCLEAR, text, text_effect=Effect(ops=(op,)))


def _no_industry(f: Finding, ir: CampaignIR) -> Question:
    return _q(
        Kind.GAP,
        "Which industries should this campaign look at?",
        choices=_from_list("audience.industries", list(base_config().search.industries)),
        multi=True,
        text_effect=_text_add("audience.industries"),
    )


def _no_area(f: Finding, ir: CampaignIR) -> Question:
    return _q(
        Kind.GAP,
        "Which engineering areas should the companies need help with?",
        choices=_from_list(
            "audience.capabilities_needed", list(base_config().search.capabilities)[:14]
        ),
        multi=True,
        text_effect=_text_add("audience.capabilities_needed"),
    )


def _area_without_audience(f: Finding, ir: CampaignIR) -> Question:
    return _q(
        Kind.AMBIGUITY,
        f"You mentioned {_join(f.detail['areas'])}. Which industries should the companies work in?",
        choices=_from_list("audience.industries", list(base_config().search.industries)),
        multi=True,
        text_effect=_text_add("audience.industries"),
    )


def _industry_without_area(f: Finding, ir: CampaignIR) -> Question:
    return _q(
        Kind.AMBIGUITY,
        f"You mentioned {_join(f.detail['industries'])}. Which engineering area are you looking for?",
        choices=_from_list(
            "audience.capabilities_needed", list(base_config().search.capabilities)[:14]
        ),
        multi=True,
        text_effect=_text_add("audience.capabilities_needed"),
    )


def _tech_without_product(f: Finding, ir: CampaignIR) -> Question:
    return _q(
        Kind.AMBIGUITY,
        f"You mentioned {_join(f.detail['technologies'])}. What product do the companies build with it? "
        "A technology alone is not something we can search for.",
        choices=_from_list("audience.products", list(base_config().search.products)[:12]),
        multi=True,
        text_effect=_text_add("audience.products"),
    )


def _preference_scope(f: Finding, ir: CampaignIR) -> Question:
    statement = f.detail["statement"]
    return _q(
        Kind.PREFERENCE,
        f"You said “{_short(statement)}”. Is that for this campaign only, or for all your campaigns?",
        choices=_choices(
            ("This campaign only", Effect(reextract=statement)),
            (
                "All campaigns",
                Effect(
                    unsupported=UnsupportedClaim(
                        request=f"for all campaigns: {statement}"[:300], kind="other"
                    )
                ),
            ),
        ),
    )


_MAKERS = {
    Reason.DRAFT_BELOW_QUALIFY: _draft_below_qualify,
    Reason.RESULTS_BELOW_PAGES: _results_below_pages,
    Reason.PAGES_STARVED: _pages_starved,
    Reason.EMPLOYEE_RANGE: _employee_range,
    Reason.TARGET_EXCLUDED: _target_excluded,
    Reason.SUPPLIER_WORDING: _supplier_wording,
    Reason.VALUE_UNCLEAR: _value_unclear,
    Reason.NO_INDUSTRY: _no_industry,
    Reason.NO_AREA: _no_area,
    Reason.AREA_WITHOUT_AUDIENCE: _area_without_audience,
    Reason.INDUSTRY_WITHOUT_AREA: _industry_without_area,
    Reason.TECH_WITHOUT_PRODUCT: _tech_without_product,
    Reason.PREFERENCE_SCOPE: _preference_scope,
}


# --- asking ------------------------------------------------------------------------------------------------------


def new_questions(
    findings: list[Finding], ir: CampaignIR, conv: ConversationState
) -> list[Question]:
    """Questions for findings not asked before, filling up to MAX_OPEN unanswered in total."""
    room = MAX_OPEN - len(conv.open)
    fresh = [f for f in findings if f.key not in conv.asked]
    return [build_question(f, ir) for f in fresh[: max(room, 0)]]


def register(conv: ConversationState, questions: list[Question], turn: int) -> ConversationState:
    asked = dict(conv.asked)
    for q in questions:
        asked[q.key] = AskedQuestion(key=q.key, question_id=q.id, status="open", turn=turn)
    return conv.model_copy(update={"asked": asked, "open": (*conv.open, *questions), "turn": turn})


def _close(conv: ConversationState, q: Question, status: str, turn: int) -> ConversationState:
    asked = dict(conv.asked)
    asked[q.key] = AskedQuestion(key=q.key, question_id=q.id, status=status, turn=turn)
    return conv.model_copy(
        update={"asked": asked, "open": tuple(o for o in conv.open if o.id != q.id)}
    )


def close_resolved(conv: ConversationState, live_keys: set[str], turn: int) -> ConversationState:
    """Questions whose situation no longer exists (the user fixed it another way) are closed."""
    for q in conv.open:
        if q.key not in live_keys:
            conv = _close(conv, q, "answered", turn)
    return conv


# --- answering ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerOutcome:
    ir: CampaignIR
    conv: ConversationState
    merge: MergeResult | None = None
    follow_up: FollowUp | None = None
    error: AnswerError | None = None


def _fail(ir: CampaignIR, conv: ConversationState, code: str, message: str) -> AnswerOutcome:
    return AnswerOutcome(ir, conv, error=AnswerError(code=code, message=message))


def _coerce(text: str) -> Any:
    try:
        value = json.loads(text)
    except ValueError:
        return text
    return value if isinstance(value, int | float | bool) else text


def _substitute(effect: Effect, text: str) -> Effect:
    ops = []
    for op in effect.ops:
        if op.value == PLACEHOLDER:
            spec = REGISTRY[op.path]
            value: Any = [text] if spec.kind == "list" else _coerce(text)
            op = op.model_copy(update={"value": value})
        ops.append(op)
    return effect.model_copy(update={"ops": tuple(ops)})


def answer_question(
    ir: CampaignIR, conv: ConversationState, answer: Answer, turn: int
) -> AnswerOutcome:
    question = next((q for q in conv.open if q.id == answer.question_id), None)
    if question is None:
        known = any(a.question_id == answer.question_id for a in conv.asked.values())
        if known:
            return _fail(
                ir, conv, "already_answered", "that question was already answered or skipped"
            )
        return _fail(ir, conv, "unknown_question", "no such question")

    if answer.is_skip:
        if not question.skippable:
            return _fail(
                ir,
                conv,
                "not_skippable",
                "this one has to be resolved before the campaign can be checked",
            )
        return AnswerOutcome(ir, _close(conv, question, "skipped", turn))

    by_id = {c.id: c for c in question.choices}
    if isinstance(answer.value, list) or (isinstance(answer.value, str) and answer.value in by_id):
        ids = answer.value if isinstance(answer.value, list) else [answer.value]
        if not ids or any(i not in by_id for i in ids):
            return _fail(ir, conv, "invalid_choice", "pick one of the listed options")
        if len(ids) > 1 and not question.multi:
            return _fail(ir, conv, "invalid_choice", "pick only one option")
        effects = [by_id[i].effect for i in ids]
    elif isinstance(answer.value, str) and question.allow_text and question.text_effect is not None:
        guard = check_input(answer.value, "edit")
        if not guard.ok:
            return _fail(ir, conv, "invalid_text", guard.issues[0].message)
        effects = [_substitute(question.text_effect, guard.text)]
    else:
        return _fail(ir, conv, "invalid_choice", "pick one of the listed options")

    ops = [op for e in effects for op in e.ops]
    claims = [e.unsupported for e in effects if e.unsupported is not None]
    merge = merge_patch(ir, IRPatch(ops=ops, unsupported=claims), TurnContext(turn, Source.ANSWER))
    bad = next((r for r in merge.rejected if r.reason is MergeReason.INVALID_VALUE), None)
    if bad is not None:
        return _fail(ir, conv, "invalid_value", f"{bad.path}: {bad.message}")
    reextract = next((e.reextract for e in effects if e.reextract), None)
    return AnswerOutcome(
        merge.ir,
        _close(conv, question, "answered", turn),
        merge=merge,
        follow_up=FollowUp(reextract=reextract) if reextract else None,
    )

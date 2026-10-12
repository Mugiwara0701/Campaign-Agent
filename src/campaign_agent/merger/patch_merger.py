"""Applies patches and suggestions to the IR under provenance and lock rules. Pure.

Rules (architecture doc sections 8 and 10):
  * The model never writes `confirmed`. Values read from the user's text are `inferred`, and only
    when the evidence quote is literally in that text. The user (answer, direct edit, edit
    request) confirms.
  * A confirmed field is locked against plain extraction. Only the user can change or clear it.
  * Lock granularity is the whole field: any change to a confirmed list by extraction is dropped.
  * An explicit null from the model means "missing". Only a user source can clear a field.
  * Unknown paths are never written; they become "unsupported" items. `approval.*` is never
    writable.
  * Web suggestions never change a value. They are stored pending; accepting one confirms the value.
  * Defaults come from the contract and only fill fields nobody has set.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import ValidationError

from campaign_agent.contract.field_metadata import defaults
from campaign_agent.ir.models import CampaignIR, StoredSuggestion, UnsupportedItem
from campaign_agent.ir.patch import IRPatch, PatchOp, Suggestion, UnsupportedKind
from campaign_agent.ir.provenance import Provenance, Status
from campaign_agent.ir.registry import NOT_PATCHABLE, REGISTRY, FieldSpec, get_value

MAX_SUGGESTIONS = 20


class Source(StrEnum):
    EXTRACTION = "extraction"  # the model read the user's message
    EDIT_REQUEST = "edit_request"  # the model read an edit written in words
    DIRECT = "direct"  # the dashboard sent {path, value}
    ANSWER = "answer"  # the user answered a question


_USER_SOURCES = {Source.DIRECT, Source.ANSWER}
_CONFIRMING = {Source.DIRECT, Source.ANSWER, Source.EDIT_REQUEST}
_MAY_CLEAR = {Source.DIRECT, Source.ANSWER, Source.EDIT_REQUEST}
_NEEDS_QUOTE = {Source.EXTRACTION, Source.EDIT_REQUEST}


class Reason(StrEnum):
    UNKNOWN_PATH = "unknown_path"
    NOT_PATCHABLE = "not_patchable"
    APPROVAL_FIXED = "approval_fixed"
    INVALID_VALUE = "invalid_value"
    LOCKED = "locked"
    NULL_IGNORED = "null_ignored"
    CLEAR_NOT_ALLOWED = "clear_not_allowed"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    VALUE_NOT_IN_QUOTE = "value_not_in_quote"
    WRONG_OP = "wrong_op"
    NO_CHANGE = "no_change"
    NOT_SUGGESTIBLE = "not_suggestible"
    DUPLICATE_SUGGESTION = "duplicate_suggestion"
    TOO_MANY_SUGGESTIONS = "too_many_suggestions"


class UnknownSuggestionError(LookupError):
    pass


class SuggestionResolvedError(ValueError):
    pass


@dataclass(frozen=True)
class TurnContext:
    turn: int
    source: Source
    text: str = ""  # the user's text this turn; evidence quotes are checked against it


@dataclass(frozen=True)
class Applied:
    path: str
    op: str
    value: Any
    status: Status


@dataclass(frozen=True)
class Rejected:
    path: str
    op: str
    reason: Reason
    message: str
    kept_value: Any = None  # for LOCKED: the confirmed value that was kept


@dataclass(frozen=True)
class MergeResult:
    ir: CampaignIR
    applied: list[Applied] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


_KIND_REASON: dict[str, str] = {
    "channel": "The Marketing Agent sends email only; other channels do not exist.",
    "approval": "Email approval is enforced by the Marketing Agent per email; not configurable.",
    "outreach_content": "Email wording is set in the Marketing Agent's prompt files, not here.",
    "other": "Not expressible in contract 1.0.",
}


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


_WORD = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _stem(word: str) -> str:
    return word if len(word) < 5 else word[:5]


def _item_grounded(item: str, quote_words: set[str]) -> bool:
    """A word of the value shares its first five letters with a word of the quote."""
    stems = {_stem(w) for w in quote_words}
    return any(_stem(token) in stems for token in _WORD.findall(item.casefold()))


def value_in_quote(value: Any, quote: str) -> bool:
    """Is the value something the quote actually says? Catches a real quote with an invented value.

    Numbers must appear in the quote (commas in 5,000 are ignored; a fraction may be written as a
    percentage). Words must share a stem with a word of the quote. Booleans cannot be checked.
    Number words ("five hundred") do not count: such a value is rejected rather than guessed.
    """
    if isinstance(value, bool) or value is None:
        return True
    text = _norm(quote)
    if isinstance(value, int | float):
        numbers = set(_NUMBER.findall(re.sub(r"(?<=\d),(?=\d{3})", "", text)))
        candidates = {f"{value:g}"}
        if isinstance(value, float) and 0 < value < 1:
            candidates.add(f"{round(value * 100):g}")
        return bool(candidates & numbers)
    words = set(_WORD.findall(text))
    items = value if isinstance(value, list) else [value]
    return all(_item_grounded(str(item), words) for item in items)


class _Work:
    """Mutable scratch space for one merge; the input IR is never touched."""

    def __init__(self, ir: CampaignIR, ctx: TurnContext) -> None:
        self.data: dict[str, Any] = ir.model_dump(mode="python")
        self.ir = ir
        self.ctx = ctx
        self.applied: list[Applied] = []
        self.rejected: list[Rejected] = []
        self.unsupported_seen = {(u.kind, _norm(u.request)) for u in ir.unsupported}
        self.extra_changes = False

    def current(self, path: str) -> Any:
        node: Any = self.data
        for part in path.split("."):
            node = node[part]
        return node

    def write(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value

    def provenance_of(self, path: str) -> Status:
        entry = self.data["provenance"].get(path)
        return Status(entry["status"]) if entry else Status.UNSET

    def reject(self, op: PatchOp, reason: Reason, message: str, kept: Any = None) -> None:
        self.rejected.append(Rejected(op.path, op.op, reason, message, kept))

    def add_unsupported(
        self, request: str, kind: UnsupportedKind, reason: str | None = None
    ) -> None:
        key = (kind, _norm(request))
        if key in self.unsupported_seen:
            return
        self.unsupported_seen.add(key)
        item = UnsupportedItem(
            request=request[:300],
            kind=kind,
            reason=reason or _KIND_REASON[kind],
            source_turn=self.ctx.turn,
        )
        self.data["unsupported"].append(item.model_dump(mode="python"))
        self.extra_changes = True

    def finish(self) -> MergeResult:
        if self.applied or self.extra_changes:
            self.data["meta"]["revision"] += 1
        return MergeResult(CampaignIR.model_validate(self.data), self.applied, self.rejected)


def _short(exc: ValidationError) -> str:
    first = exc.errors()[0]
    return f"{first['msg']}"


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else [value]


def _apply_op(w: _Work, op: PatchOp) -> None:
    source = w.ctx.source
    path = op.path

    if path == "approval" or path.startswith("approval."):
        w.reject(op, Reason.APPROVAL_FIXED, _KIND_REASON["approval"])
        w.add_unsupported(f"{path} = {op.value!r}", "approval")
        return
    if path in NOT_PATCHABLE:
        w.reject(op, Reason.NOT_PATCHABLE, f"{path} is set by the system, not by patches")
        return
    spec = REGISTRY.get(path)
    if spec is None:
        w.reject(op, Reason.UNKNOWN_PATH, f"no such field: {path}")
        w.add_unsupported(f"{path} = {op.value!r}", "other", f"No field {path} in contract 1.0.")
        return

    if source in _NEEDS_QUOTE:
        if not op.evidence_quote:
            w.reject(
                op, Reason.EVIDENCE_MISSING, "an evidence quote from the user's text is required"
            )
            return
        if _norm(op.evidence_quote) not in _norm(w.ctx.text):
            w.reject(op, Reason.EVIDENCE_NOT_FOUND, "the evidence quote is not in the user's text")
            return

    kind = op.op
    if kind == "set" and op.value is None:
        if source in _USER_SOURCES:
            kind = "clear"
        else:
            w.reject(op, Reason.NULL_IGNORED, "null from the model means missing")
            return
    if kind in ("add", "remove") and op.value is None:
        w.reject(op, Reason.NULL_IGNORED, "null from the model means missing")
        return
    if kind in ("add", "remove") and spec.kind != "list":
        w.reject(op, Reason.WRONG_OP, f"{path} is a single value; use set")
        return
    if kind == "clear" and source not in _MAY_CLEAR:
        w.reject(op, Reason.CLEAR_NOT_ALLOWED, "only the user can clear a field")
        return

    if (
        source in _NEEDS_QUOTE
        and kind != "clear"
        and not value_in_quote(op.value, op.evidence_quote or "")
    ):
        w.reject(op, Reason.VALUE_NOT_IN_QUOTE, "the value is not supported by the quoted words")
        return

    current = w.current(path)
    status = w.provenance_of(path)
    if status is Status.CONFIRMED and source is Source.EXTRACTION:
        w.reject(
            op,
            Reason.LOCKED,
            f"{path} was confirmed by the user and was kept",
            kept=current,
        )
        return

    new_value = _compute(w, op, spec, kind, current)
    if new_value is _REJECTED:
        return
    if new_value == current:
        w.reject(op, Reason.NO_CHANGE, "value is already set")
        return

    w.write(path, new_value)
    if kind == "clear":
        w.data["provenance"].pop(path, None)
        w.applied.append(Applied(path, kind, new_value, Status.UNSET))
        return
    new_status = Status.CONFIRMED if source in _CONFIRMING else Status.INFERRED
    w.data["provenance"][path] = Provenance(
        status=new_status, source_turn=w.ctx.turn, evidence_quote=op.evidence_quote
    ).model_dump(mode="python")
    w.applied.append(Applied(path, kind, new_value, new_status))


_REJECTED = object()


def _compute(w: _Work, op: PatchOp, spec: FieldSpec, kind: str, current: Any) -> Any:
    if kind == "clear":
        return [] if spec.kind == "list" else None
    try:
        if spec.kind == "scalar":
            return spec.adapter.validate_python(op.value)
        incoming = _items(op.value)
        if kind == "set":
            candidate = incoming
        else:
            have = {_norm(str(x)): x for x in current}
            if kind == "add":
                candidate = list(current)
                for item in incoming:
                    if _norm(str(item)) not in have and item not in candidate:
                        candidate.append(item)
                        have[_norm(str(item))] = item
            else:  # remove
                drop = {_norm(str(x)) for x in incoming}
                candidate = [x for x in current if _norm(str(x)) not in drop]
        return spec.adapter.validate_python(_dedupe(candidate))
    except ValidationError as exc:
        w.reject(op, Reason.INVALID_VALUE, _short(exc))
        return _REJECTED


def _dedupe(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        key = _norm(str(item))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def merge_patch(ir: CampaignIR, patch: IRPatch, ctx: TurnContext) -> MergeResult:
    w = _Work(ir, ctx)
    for op in patch.ops:
        _apply_op(w, op)
    for claim in patch.unsupported:
        w.add_unsupported(claim.request, claim.kind)
    return w.finish()


# --- defaults ---

# IR path -> contract default path, where the two differ.
_DEFAULT_PATH = {
    f"limits.{k}": f"search.{k}"
    for k in (
        "queries_per_run",
        "repeat_after_days",
        "results_per_query",
        "pages_per_query",
        "use_job_feeds",
        "feed_items",
        "query_suffix",
    )
}


def apply_defaults(ir: CampaignIR) -> MergeResult:
    """Fill fields nobody set with the contract default, labelled `defaulted`. Never overwrites."""
    contract = defaults()
    w = _Work(ir, TurnContext(turn=0, source=Source.EXTRACTION))
    for path, spec in REGISTRY.items():
        if spec.kind != "scalar" or spec.mapping != "supported":
            continue
        key = _DEFAULT_PATH.get(path, path)
        if key not in contract or w.current(path) is not None:
            continue
        value = contract[key]
        w.write(path, value)
        w.data["provenance"][path] = Provenance(status=Status.DEFAULTED).model_dump(mode="python")
        w.applied.append(Applied(path, "set", value, Status.DEFAULTED))
    return w.finish()


# --- web suggestions ---


def suggestion_id(path: str, value: Any) -> str:
    raw = f"{path}|{json.dumps(value, sort_keys=True, ensure_ascii=False)}"
    return "s_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def add_suggestions(ir: CampaignIR, suggestions: list[Suggestion], turn: int) -> MergeResult:
    """Store web proposals as pending. Values are never changed here."""
    w = _Work(ir, TurnContext(turn=turn, source=Source.EXTRACTION))
    known = {s.id for s in ir.suggestions}
    for sug in suggestions:
        op = PatchOp(path=sug.path, op="add", value=sug.value)
        spec = REGISTRY.get(sug.path)
        if spec is None or not spec.suggestible:
            w.reject(op, Reason.NOT_SUGGESTIBLE, f"research may not propose values for {sug.path}")
            continue
        try:
            if spec.kind == "list":
                value: Any = spec.adapter.validate_python(_items(sug.value))
                present = {_norm(str(x)) for x in w.current(sug.path)}
                value = [x for x in value if _norm(str(x)) not in present]
                if not value:
                    w.reject(op, Reason.NO_CHANGE, "already in the campaign")
                    continue
            else:
                value = spec.adapter.validate_python(sug.value)
        except ValidationError as exc:
            w.reject(op, Reason.INVALID_VALUE, _short(exc))
            continue
        sid = suggestion_id(sug.path, value)
        if sid in known:
            w.reject(op, Reason.DUPLICATE_SUGGESTION, "already suggested (or decided)")
            continue
        if len(w.data["suggestions"]) >= MAX_SUGGESTIONS:
            w.reject(op, Reason.TOO_MANY_SUGGESTIONS, f"at most {MAX_SUGGESTIONS} suggestions")
            continue
        known.add(sid)
        stored = StoredSuggestion(
            id=sid,
            path=sug.path,
            value=value,
            sources=tuple(sug.sources),
            rationale=sug.rationale,
            source_turn=turn,
        )
        w.data["suggestions"].append(stored.model_dump(mode="python"))
        w.applied.append(Applied(sug.path, "suggest", value, Status.SUGGESTED))
    return w.finish()


def resolve_suggestion(
    ir: CampaignIR, suggestion_id_: str, decision: Literal["accept", "reject"], turn: int
) -> MergeResult:
    """The user accepts or rejects a suggestion. Accepting writes the value as confirmed."""
    w = _Work(ir, TurnContext(turn=turn, source=Source.DIRECT))
    index = next((i for i, s in enumerate(ir.suggestions) if s.id == suggestion_id_), None)
    if index is None:
        raise UnknownSuggestionError(suggestion_id_)
    sug = ir.suggestions[index]
    if sug.status != "suggested":
        raise SuggestionResolvedError(f"{suggestion_id_} is already {sug.status}")
    if decision == "accept":
        spec = REGISTRY[sug.path]
        op = PatchOp(path=sug.path, op="add" if spec.kind == "list" else "set", value=sug.value)
        _apply_op(w, op)
        if w.rejected and w.rejected[-1].reason is not Reason.NO_CHANGE:
            return w.finish()  # could not apply: the suggestion stays pending
        prov = w.data["provenance"].get(sug.path)
        if prov is not None:
            prov["sources"] = tuple(sug.sources)
    w.data["suggestions"][index]["status"] = "accepted" if decision == "accept" else "rejected"
    w.applied.append(
        Applied(
            sug.path,
            decision,
            sug.value,
            Status.CONFIRMED if decision == "accept" else Status.UNSET,
        )
    )
    return w.finish()


__all__ = [
    "Applied",
    "MergeResult",
    "Reason",
    "Rejected",
    "Source",
    "SuggestionResolvedError",
    "TurnContext",
    "UnknownSuggestionError",
    "add_suggestions",
    "apply_defaults",
    "get_value",
    "merge_patch",
    "resolve_suggestion",
]

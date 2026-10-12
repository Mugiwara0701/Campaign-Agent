"""L2: what is missing, contradictory, off-target or unclear in the campaign. No model involved.

Each finding has a key (path, reason, instance) that identifies the situation. The question
builder asks once per key, so a question is never repeated; a conflict that comes back with
different values has a different instance and is a new question.

Kinds, in the order they are asked: conflict, drift, unclear, gap, ambiguity, preference.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from campaign_agent.contract.field_metadata import defaults
from campaign_agent.contract.model import rule_violations
from campaign_agent.contract.supplier_words import supplier_word
from campaign_agent.ir.models import CampaignIR
from campaign_agent.ir.registry import REGISTRY, get_value
from campaign_agent.merger.patch_merger import Reason as MergeReason
from campaign_agent.merger.patch_merger import Rejected

PAGES_CAP = 300  # upper bound of limits.max_pages_per_run in the contract


class Kind(StrEnum):
    CONFLICT = "conflict"
    DRIFT = "drift"
    UNCLEAR = "unclear"
    GAP = "gap"
    AMBIGUITY = "ambiguity"
    PREFERENCE = "preference"


PRIORITY = {k: i for i, k in enumerate(Kind)}


class Reason(StrEnum):
    DRAFT_BELOW_QUALIFY = "draft_below_qualify"
    RESULTS_BELOW_PAGES = "results_below_pages"
    PAGES_STARVED = "pages_starved"
    EMPLOYEE_RANGE = "employee_range"
    TARGET_EXCLUDED = "target_excluded"
    SUPPLIER_WORDING = "supplier_wording"
    VALUE_UNCLEAR = "value_unclear"
    NO_INDUSTRY = "no_industry"
    NO_AREA = "no_area"
    AREA_WITHOUT_AUDIENCE = "area_without_audience"
    INDUSTRY_WITHOUT_AREA = "industry_without_area"
    TECH_WITHOUT_PRODUCT = "tech_without_product"
    PREFERENCE_SCOPE = "preference_scope"


@dataclass(frozen=True)
class Finding:
    kind: Kind
    path: str
    reason: Reason
    instance: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.path}|{self.reason.value}|{self.instance}"


def _norm(text: str) -> str:
    return " ".join(str(text).casefold().split())


def effective(ir: CampaignIR, ir_path: str, contract_path: str | None = None) -> Any:
    """The IR's value, or the contract default when nobody set it."""
    value = get_value(ir, ir_path)
    return value if value is not None else defaults().get(contract_path or ir_path)


def _quote(ir: CampaignIR, path: str) -> str:
    prov = ir.provenance.get(path)
    return (prov.evidence_quote or "") if prov else ""


# --- conflicts ---


def _rule_values(ir: CampaignIR) -> dict[str, Any]:
    return {
        "min_qualify": effective(ir, "qualification.min_qualify"),
        "min_draft": effective(ir, "qualification.min_draft"),
        "queries_per_run": effective(ir, "limits.queries_per_run", "search.queries_per_run"),
        "pages_per_query": effective(ir, "limits.pages_per_query", "search.pages_per_query"),
        "results_per_query": effective(ir, "limits.results_per_query", "search.results_per_query"),
        "max_pages_per_run": effective(ir, "limits.max_pages_per_run"),
    }


def _conflicts(ir: CampaignIR) -> list[Finding]:
    values = _rule_values(ir)
    out: list[Finding] = []
    for v in rule_violations(**values):
        reason = Reason(v.code)
        detail: dict[str, Any] = {**values, "message": v.message}
        instance = "-".join(str(values[k]) for k in sorted(values))
        if reason is Reason.PAGES_STARVED:
            needed = values["queries_per_run"] * values["pages_per_query"]
            detail["needed"] = needed
            detail["fix"] = ("limits.max_pages_per_run", needed) if needed <= PAGES_CAP else None
        out.append(Finding(Kind.CONFLICT, v.path, reason, instance, detail))
    lo, hi = ir.audience.employee_min, ir.audience.employee_max
    if lo is not None and hi is not None and lo > hi:
        out.append(
            Finding(
                Kind.CONFLICT,
                "audience.employee_min",
                Reason.EMPLOYEE_RANGE,
                f"{lo}-{hi}",
                {"min": lo, "max": hi},
            )
        )
    return out


_CONTRADICTIONS = (
    ("audience.industries", "exclusions.terms"),
    ("audience.products", "exclusions.terms"),
    ("audience.technologies", "exclusions.terms"),
    ("audience.include_countries", "exclusions.countries"),
)


def _contradictions(ir: CampaignIR) -> list[Finding]:
    out: list[Finding] = []
    for target, excluded in _CONTRADICTIONS:
        excl = {_norm(x): x for x in get_value(ir, excluded)}
        for item in get_value(ir, target):
            if _norm(item) in excl:
                detail = {
                    "item": item,
                    "excluded_item": excl[_norm(item)],
                    "target_path": target,
                    "excluded_path": excluded,
                    "target_quote": _quote(ir, target),
                    "excluded_quote": _quote(ir, excluded),
                }
                out.append(
                    Finding(Kind.CONFLICT, target, Reason.TARGET_EXCLUDED, _norm(item), detail)
                )
    return out


# --- target drift ---

QUERY_LISTS = (
    "audience.industries",
    "audience.products",
    "audience.technologies",
    "audience.capabilities_needed",
)


def _drift(ir: CampaignIR) -> list[Finding]:
    out: list[Finding] = []
    for path in QUERY_LISTS:
        for item in get_value(ir, path):
            if word := supplier_word(item):
                out.append(
                    Finding(
                        Kind.DRIFT,
                        path,
                        Reason.SUPPLIER_WORDING,
                        _norm(item),
                        {"item": item, "word": word},
                    )
                )
    suffix = ir.limits.query_suffix
    if suffix and (word := supplier_word(suffix)):
        out.append(
            Finding(
                Kind.DRIFT,
                "limits.query_suffix",
                Reason.SUPPLIER_WORDING,
                _norm(suffix),
                {"item": suffix, "word": word},
            )
        )
    return out


# --- values that were rejected ---

# A value that did not match its quote is usually a model slip, not a request. These are fields
# people name directly ("US only", "decision makers", "500 to 5,000 employees"), so ask for them.
_ASK_WHEN_UNGROUNDED = {
    "contacts.preferred_roles",
    "audience.employee_min",
    "audience.employee_max",
    "audience.include_countries",
}


def _unclear(rejected: Sequence[Rejected]) -> list[Finding]:
    out: dict[str, Finding] = {}
    for r in rejected:
        spec = REGISTRY.get(r.path)
        if spec is None or spec.mapping == "not_emitted":
            continue
        asks = r.reason is MergeReason.INVALID_VALUE or (
            r.reason is MergeReason.VALUE_NOT_IN_QUOTE and r.path in _ASK_WHEN_UNGROUNDED
        )
        if asks:
            out.setdefault(
                r.path,
                Finding(Kind.UNCLEAR, r.path, Reason.VALUE_UNCLEAR, "", {"why": r.reason.value}),
            )
    return list(out.values())


# --- what the campaign is about ---


def _audience(ir: CampaignIR) -> list[Finding]:
    a = ir.audience
    out: list[Finding] = []
    if not (a.industries or a.products or a.technologies or a.capabilities_needed):
        return [
            Finding(Kind.GAP, "audience.industries", Reason.NO_INDUSTRY),
            Finding(Kind.GAP, "audience.capabilities_needed", Reason.NO_AREA),
        ]
    if a.capabilities_needed and not a.industries and not a.products:
        out.append(
            Finding(
                Kind.AMBIGUITY,
                "audience.industries",
                Reason.AREA_WITHOUT_AUDIENCE,
                detail={"areas": a.capabilities_needed},
            )
        )
    if a.industries and not a.capabilities_needed and not a.products:
        out.append(
            Finding(
                Kind.AMBIGUITY,
                "audience.capabilities_needed",
                Reason.INDUSTRY_WITHOUT_AREA,
                detail={"industries": a.industries},
            )
        )
    if a.technologies and not a.products:
        out.append(
            Finding(
                Kind.AMBIGUITY,
                "audience.products",
                Reason.TECH_WITHOUT_PRODUCT,
                detail={"technologies": a.technologies},
            )
        )
    return out


_PREFERENCE = re.compile(
    r"\b(we|i|our team)\s+(?:\w+\s+)?(prefer|like|always|usually|never|tend to|typically|avoid)\b"
    r"|\bprefer(?:red|ence|s)?\b",
    re.IGNORECASE,
)


def _preferences(ir: CampaignIR) -> list[Finding]:
    return [
        Finding(
            Kind.PREFERENCE,
            "organisation_context",
            Reason.PREFERENCE_SCOPE,
            _norm(s),
            {"statement": s},
        )
        for s in ir.organisation_context
        if _PREFERENCE.search(s)
    ]


def analyze(ir: CampaignIR, rejected: Sequence[Rejected] = ()) -> list[Finding]:
    """All findings, most urgent first. `rejected` is what the last merge turned down."""
    found = [
        *_conflicts(ir),
        *_contradictions(ir),
        *_drift(ir),
        *_unclear(rejected),
        *_audience(ir),
        *_preferences(ir),
    ]
    return sorted(found, key=lambda f: PRIORITY[f.kind])

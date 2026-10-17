"""Pure functions: IR -> campaign overlay (an ordered dict), and overlay + base -> effective config.

The overlay holds only what the user set. Nothing is emitted for:
  * `defaulted` values: the base config already supplies them; pinning a default would freeze it;
  * fields the contract has no key for (reported as "not applied" instead).
`audience.include_countries` becomes the top-level `include_countries` (the Marketing Agent rejects a company it can
place elsewhere) and, as before, a `search.query_suffix` hint so the searches lean that way;
  * `search.capabilities`: in the MA, project_context.engineering_requirements replaces it when set.

Merge semantics (overlay over base):
  * scalars and project_context fields: the overlay value replaces the base value;
  * exclusion lists: base first, then the overlay's items, case-insensitive duplicates dropped.
    An overlay can add exclusions but never remove one.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from campaign_agent.contract.model import CONFIG_VERSION, LeadgenFile
from campaign_agent.ir.models import CampaignIR
from campaign_agent.ir.provenance import Status
from campaign_agent.ir.registry import REGISTRY, get_value

EXCLUDE_KEYS = ("exclude_terms", "exclude_companies", "exclude_countries", "exclude_tlds")

# IR path -> (section, key) in the overlay. Section "" is top level.
_AUDIENCE = {
    "audience.industries": ("project_context", "industries"),
    "audience.products": ("project_context", "products"),
    "audience.technologies": ("project_context", "technologies"),
    "audience.capabilities_needed": ("project_context", "engineering_requirements"),
}
_QUALIFICATION = {
    f"qualification.{k}": ("qualification", k)
    for k in (
        "min_qualify",
        "min_draft",
        "min_confidence",
        "accept_hiring_signals",
        "accept_service_companies",
        "max_age_days",
        "min_relevance_terms",
    )
}
_SEARCH_KEYS = (
    "queries_per_run",
    "repeat_after_days",
    "results_per_query",
    "pages_per_query",
    "use_job_feeds",
    "feed_items",
    "query_suffix",
)
_LIMIT_KEYS = (
    "max_new_leads_per_run",
    "max_pages_per_run",
    "max_pages_per_domain",
    "engine_rest_minutes",
)
_LIMITS = {
    **{f"limits.{k}": ("search", k) for k in _SEARCH_KEYS},
    **{f"limits.{k}": ("limits", k) for k in _LIMIT_KEYS},
}
_EXCLUSIONS = {
    "exclusions.terms": ("", "exclude_terms"),
    "exclusions.companies": ("", "exclude_companies"),
    "exclusions.countries": ("", "exclude_countries"),
    "exclusions.tlds": ("", "exclude_tlds"),
    "audience.include_countries": ("", "include_countries"),
    "contacts.preferred_roles": ("", "preferred_roles"),
}
_MAPPING = {**_AUDIENCE, **_QUALIFICATION, **_LIMITS, **_EXCLUSIONS}

_PROPOSALS = {
    "audience.employee_min": (
        "The Marketing Agent does not extract company size.",
        "targeting.employee_range",
    ),
    "audience.employee_max": (
        "The Marketing Agent does not extract company size.",
        "targeting.employee_range",
    ),
    "audience.signals": (
        "No contract key for custom signals; hiring and RFP signals are set by "
        "qualification.accept_hiring_signals and search.use_job_feeds.",
        None,
    ),
}


_SUFFIX_MAX_CHARS = 80  # the contract's QuerySuffix
_SUFFIX_OK = re.compile(r"^[\w .,'&/+()#-]*$")
PARTLY_APPLIED = "partially_applied"


def _country_hint(ir: CampaignIR, search: Mapping[str, Any]) -> str | None:
    """The countries as a search suffix ("Germany Austria"), or None. A hint, not a filter: the search leans that way.
    Never overwrites a suffix the user set, and only takes the countries that fit the contract's limits."""
    if not _emit(ir, "audience.include_countries") or "query_suffix" in search:
        return None
    text = ""
    for country in get_value(ir, "audience.include_countries"):
        candidate = f"{text} {country}".strip()
        if len(candidate) > _SUFFIX_MAX_CHARS or not _SUFFIX_OK.match(candidate):
            break
        text = candidate
    return text or None


@dataclass(frozen=True)
class NotApplied:
    """A request that could not be expressed in the configuration. Never dropped silently."""

    request: str
    reason: str
    proposal: str | None = None
    kind: str = "unsupported_field"


@dataclass(frozen=True)
class CompiledOverlay:
    data: dict[str, Any]
    not_applied: tuple[NotApplied, ...]


def _is_set(value: Any) -> bool:
    return value is not None and value != []


def _emit(ir: CampaignIR, path: str) -> bool:
    entry = ir.provenance.get(path)
    return _is_set(get_value(ir, path)) and not (entry and entry.status is Status.DEFAULTED)


def _clean_name(name: str) -> str:
    return " ".join(re.sub(r"[^\w .,'&/+()#-]", " ", name).split())[:200]


def compile_overlay(ir: CampaignIR) -> CompiledOverlay:
    data: dict[str, Any] = {"config_version": CONFIG_VERSION}
    sections: dict[str, dict[str, Any]] = {
        "project_context": {},
        "search": {},
        "qualification": {},
        "limits": {},
    }
    name = _clean_name(ir.meta.name)
    if name:
        sections["project_context"]["name"] = name
    top: dict[str, Any] = {}
    for path, (section, key) in _MAPPING.items():
        if not _emit(ir, path):
            continue
        value = get_value(ir, path)
        value = list(value) if isinstance(value, list) else value
        (sections[section] if section else top)[key] = value
    for section in ("project_context", "search", "qualification"):
        if sections[section]:
            data[section] = sections[section]
    for key in EXCLUDE_KEYS:
        if key in top:
            data[key] = top[key]
    for key in ("include_countries", "preferred_roles"):
        if key in top:
            data[key] = top[key]
    hint = _country_hint(ir, sections["search"])
    if hint:
        sections["search"]["query_suffix"] = hint
        data["search"] = sections["search"]
    if sections["limits"]:
        data["limits"] = sections["limits"]
    return CompiledOverlay(data, tuple(_not_applied(ir, hint)))


def _not_applied(ir: CampaignIR, country_hint: str | None = None) -> list[NotApplied]:
    out: list[NotApplied] = []
    if _is_set(get_value(ir, "audience.include_countries")):
        # Applied, with one honest limit: a company whose country cannot be told from its page or domain is kept.
        out.append(
            NotApplied(
                f"audience.include_countries = {get_value(ir, 'audience.include_countries')!r}",
                "Applied: the Marketing Agent rejects a company it can place outside these countries (by the location "
                "on its page or its country domain). A company whose country it cannot tell is kept, and noted."
                + (f' The searches also lean that way ("{country_hint}").' if country_hint else ""),
                None,
                kind=PARTLY_APPLIED,
            )
        )
    for path, (reason, proposal) in _PROPOSALS.items():
        if path in REGISTRY and _is_set(get_value(ir, path)):
            request = f"{path} = {get_value(ir, path)!r}"
            out.append(NotApplied(request, reason, proposal))
    for item in ir.unsupported:
        out.append(NotApplied(item.request, item.reason, item.proposal, kind=item.kind))
    return out


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _union(base: list[str], extra: list[str]) -> list[str]:
    seen = {_norm(x) for x in base}
    merged = list(base)
    for item in extra:
        if _norm(item) not in seen:
            seen.add(_norm(item))
            merged.append(item)
    return merged


def merge_onto_base(overlay: Mapping[str, Any], base: LeadgenFile) -> dict[str, Any]:
    """The configuration the MA would run: the base with the overlay applied. Base is untouched."""
    effective = base.model_dump(mode="python")
    effective["config_version"] = overlay.get("config_version", CONFIG_VERSION)
    for section in ("project_context", "search", "qualification", "limits"):
        for key, value in (overlay.get(section) or {}).items():
            effective[section][key] = list(value) if isinstance(value, list) else value
    for key in EXCLUDE_KEYS:
        effective[key] = _union(effective[key], list(overlay.get(key) or []))
    for key in ("include_countries", "preferred_roles"):
        if overlay.get(key):
            effective[key] = list(overlay[key])
    return effective


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def effective_config_hash(effective: Mapping[str, Any]) -> str:
    canonical = json.dumps(effective, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(canonical)

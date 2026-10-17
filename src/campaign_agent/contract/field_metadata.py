"""What the contract knows about each key besides its type: owner, MA readers, notes.

Defaults and bounds are NOT repeated here; they are read from the models (single source).
Readers come from a trace of the MA code at the commit in model.BASE_CONFIG_SOURCE
(changes doc B1 / question U6).
"""

from dataclasses import dataclass
from typing import Any, Literal, get_args

from annotated_types import Ge, Gt, Le, Lt
from pydantic import BaseModel

from campaign_agent.contract.model import LeadgenFile

Owner = Literal["campaign", "base", "infra", "dead"]
Mapping = Literal["supported", "base_only", "never_emitted"]


@dataclass(frozen=True)
class FieldMeta:
    # campaign: the CA may set it. base: kept in the base config. infra: machine setting.
    owner: Owner
    mapping: Mapping
    readers: tuple[str, ...]  # MA modules that read the key
    blocking: bool = False  # the CA must ask the user when it is missing
    ma_code_default: Any = (
        None  # the MA's inline default when the key is absent, if it differs from the file
    )
    help: str = ""


def _c(readers: tuple[str, ...], help: str, **kw: Any) -> FieldMeta:
    return FieldMeta("campaign", "supported", readers, help=help, **kw)


def _b(readers: tuple[str, ...], help: str) -> FieldMeta:
    return FieldMeta("base", "base_only", readers, help=help)


METADATA: dict[str, FieldMeta] = {
    "config_version": FieldMeta(
        "campaign", "supported", (), help="Contract version the file targets."
    ),
    # search: volume (campaign), vocabulary (base)
    "search.queries_per_run": _c(("strategy.py",), "Queries taken per run.", ma_code_default=12),
    "search.repeat_after_days": _c(
        ("strategy.py",), "A query is not repeated within this many days.", ma_code_default=14
    ),
    "search.results_per_query": _c(("pipeline.py",), "Results read per query.", ma_code_default=10),
    "search.use_job_feeds": _c(
        ("pipeline.py",), "Also read job/project feeds (hiring signals).", ma_code_default=False
    ),
    "search.feed_items": _c(("pipeline.py",), "Items read from the feeds.", ma_code_default=40),
    "search.pages_per_query": _c(("pipeline.py",), "Results opened per query.", ma_code_default=4),
    "search.query_suffix": _c(("pipeline.py",), "Text added to every query.", ma_code_default=""),
    "search.capabilities": _c(
        ("strategy.py",), "Our capabilities; the CA selects a subset of the base list."
    ),
    "search.products": _b(
        ("strategy.py",), "Product vocabulary; project_context.products replaces it when set."
    ),
    "search.industries": _b(
        ("strategy.py",), "Industry vocabulary; project_context.industries replaces it when set."
    ),
    "search.platforms": _b(("strategy.py",), "Platform modifiers (Android, embedded Linux)."),
    "search.builder_phrases": _b(
        ("strategy.py",), "Customer-signal phrases; length and stride rules apply."
    ),
    "search.need_phrases": _b(
        ("strategy.py",), "Customer-signal phrases; 19 phrases, stride rules apply."
    ),
    "search.rfp_words": _b(("strategy.py",), "RFP vocabulary."),
    # project context: audience (campaign)
    "project_context.name": _c(("strategy.py", "service.py"), "Campaign name."),
    "project_context.industries": _c(
        ("strategy.py",), "Industries; replaces search.industries when set."
    ),
    "project_context.products": _c(
        ("strategy.py",), "Products customers build; replaces search.products when set."
    ),
    "project_context.technologies": _c(("strategy.py",), "Technologies that modify a product."),
    "project_context.hardware": FieldMeta(
        "base", "never_emitted", ("strategy.py",), help="Alias merged into technologies."
    ),
    "project_context.engineering_requirements": _c(
        ("strategy.py",),
        "Our capabilities the project needs; replaces search.capabilities when set.",
    ),
    # qualification (campaign)
    "qualification.min_qualify": _c(
        ("pipeline.py", "qualify.py"), "Below this score a company is rejected.", ma_code_default=50
    ),
    "qualification.min_draft": _c(
        ("pipeline.py",), "Below this score no email is drafted.", ma_code_default=60
    ),
    "qualification.min_confidence": _c(
        ("qualify.py",), "Minimum model confidence.", ma_code_default=0.5
    ),
    "qualification.accept_hiring_signals": _c(
        ("qualify.py", "strategy.py"), "Accept hiring signals.", ma_code_default=True
    ),
    "qualification.accept_service_companies": _c(
        ("qualify.py", "pipeline.py", "contacts.py", "strategy.py"),
        "Keep engineering-services firms as partner leads. The MA code disagrees on the default "
        "(True in qualify.py and pipeline.py, False elsewhere and in the file); contract: False.",
        ma_code_default="True in qualify.py/pipeline.py, False in contacts.py/strategy.py",
    ),
    "qualification.max_age_days": _c(
        ("qualify.py",), "Pages with only older dates are skipped.", ma_code_default=120
    ),
    "qualification.min_relevance_terms": _c(
        ("qualify.py",), "Technology areas a page must name.", ma_code_default=2
    ),
    # exclusions (campaign, additive)
    "exclude_terms": _c(
        ("pipeline.py", "qualify.py"), "Extra excluded terms, added to the base list."
    ),
    "exclude_companies": _c(
        ("pipeline.py", "qualify.py"), "Extra excluded companies, added to the base list."
    ),
    "exclude_countries": _c(
        ("pipeline.py", "qualify.py"), "Extra excluded countries, added to the base list."
    ),
    "exclude_tlds": _c(
        ("pipeline.py", "qualify.py"), "Extra excluded domain endings, added to the base list."
    ),
    "include_countries": _c(
        ("qualify.py", "pipeline.py", "intent.py"),
        "Only companies in these countries: one placed elsewhere (by the location on its page or its country "
        "domain) is rejected. Replaces; empty means any country that is not excluded.",
    ),
    "preferred_roles": _c(
        ("contacts.py",),
        "People in these roles are contacted first, in this order; other approachable roles follow. Replaces; "
        "empty means the Marketing Agent's own order.",
    ),
    # contacts (base)
    "contacts.max_pages": _b(("contacts.py",), "Pages read on a company's own site."),
    "contacts.max_attempts": _b(("pipeline.py",), "Contact attempts before a lead is rejected."),
    "contacts.link_labels": _b(("contacts.py",), "Link labels followed on a company site."),
    # limits
    "limits.max_new_leads_per_run": _c(
        ("pipeline.py",), "Leads that reach EMAIL_DRAFTED in one run.", ma_code_default=10
    ),
    "limits.max_pages_per_run": _c(
        ("browser/__init__.py",), "Pages opened per run.", ma_code_default=60
    ),
    "limits.max_pages_per_domain": _c(
        ("browser/__init__.py",), "Pages opened per domain.", ma_code_default=6
    ),
    "limits.host_delay_seconds": FieldMeta(
        "dead", "never_emitted", (), help="Read by no MA code (changes doc A5). Never emitted."
    ),
    "limits.engine_rest_minutes": _c(
        ("browser/__init__.py",), "Rest after a search-engine CAPTCHA.", ma_code_default=30
    ),
    "desktop_engines": FieldMeta(
        "infra", "never_emitted", ("browser/__init__.py", "config.py"), help="Browser setting."
    ),
}


def iter_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    """Dotted paths of every leaf field of a model."""
    paths: list[str] = []
    for name, info in model.model_fields.items():
        sub = _submodel(info.annotation)
        if sub is None:
            paths.append(f"{prefix}{name}")
        else:
            paths.extend(iter_paths(sub, f"{prefix}{name}."))
    return paths


def _submodel(annotation: Any) -> type[BaseModel] | None:
    candidates = [annotation, *get_args(annotation)]
    for c in candidates:
        if isinstance(c, type) and issubclass(c, BaseModel):
            return c
    return None


def defaults() -> dict[str, Any]:
    """Contract defaults for every field that has one, read from LeadgenFile."""
    out: dict[str, Any] = {}

    def walk(model: type[BaseModel], prefix: str) -> None:
        for name, info in model.model_fields.items():
            sub = _submodel(info.annotation)
            if sub is not None:
                walk(sub, f"{prefix}{name}.")
            elif not info.is_required():
                out[f"{prefix}{name}"] = info.get_default(call_default_factory=True)

    walk(LeadgenFile, "")
    return out


def bounds() -> dict[str, dict[str, Any]]:
    """Numeric bounds of every field, read from LeadgenFile."""
    out: dict[str, dict[str, Any]] = {}

    def walk(model: type[BaseModel], prefix: str) -> None:
        for name, info in model.model_fields.items():
            sub = _submodel(info.annotation)
            if sub is not None:
                walk(sub, f"{prefix}{name}.")
                continue
            found = {
                {Ge: "ge", Gt: "gt", Le: "le", Lt: "lt"}[type(m)]: getattr(
                    m, type(m).__name__.lower()
                )
                for m in info.metadata
                if isinstance(m, (Ge, Gt, Le, Lt))
            }
            if found:
                out[f"{prefix}{name}"] = found

    walk(LeadgenFile, "")
    return out

"""L4: business rules, checked on the effective configuration (base + overlay).

An overlay that sets only `queries_per_run` can still break `max_pages_per_run`, which comes from
the base, so the rules must run on the merged values. A fix is attached only when exactly one
deterministic change clears the problem; otherwise the user is asked (gap engine, M5).
"""

from collections.abc import Mapping
from typing import Any

from campaign_agent.contract.model import MaxPagesPerRun, rule_violations
from campaign_agent.contract.supplier_words import supplier_word
from campaign_agent.contract.vocabularies import base_capabilities
from campaign_agent.validators.report import Fix, Issue, LevelResult, level_result

LEVEL = 4
_PAGES_CAP = 300  # upper bound of MaxPagesPerRun in the contract
_ = MaxPagesPerRun  # the cap above mirrors this alias


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _query_text(effective: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every value the MA writes into a search query."""
    project = effective["project_context"]
    found = [
        (f"project_context.{key}", item)
        for key in (
            "industries",
            "products",
            "technologies",
            "hardware",
            "engineering_requirements",
        )
        for item in project[key]
    ]
    found.append(("search.query_suffix", effective["search"]["query_suffix"]))
    return found


def check_business_rules(
    overlay: Mapping[str, Any], effective: Mapping[str, Any], base: Mapping[str, Any]
) -> LevelResult:
    issues: list[Issue] = []
    search, qual, limits = effective["search"], effective["qualification"], effective["limits"]

    for v in rule_violations(
        min_qualify=qual["min_qualify"],
        min_draft=qual["min_draft"],
        queries_per_run=search["queries_per_run"],
        pages_per_query=search["pages_per_query"],
        results_per_query=search["results_per_query"],
        max_pages_per_run=limits["max_pages_per_run"],
    ):
        fix = None
        if v.code == "pages_starved":
            needed = search["queries_per_run"] * search["pages_per_query"]
            if needed <= _PAGES_CAP:
                fix = Fix(v.path, needed, "raise the page budget to cover every query")
        issues.append(Issue(LEVEL, v.code, v.path, v.message, fix=fix))

    unknown = sorted(
        set((overlay.get("search") or {}).get("capabilities") or []) - set(base_capabilities())
    )
    if unknown:
        issues.append(
            Issue(
                LEVEL,
                "capability_not_in_base",
                "search.capabilities",
                f"not in the base list: {unknown}",
            )
        )

    for path, value in _query_text(effective):
        if word := supplier_word(value):
            issues.append(
                Issue(
                    LEVEL,
                    "supplier_wording",
                    path,
                    f"'{value}' contains the supplier word '{word}'; the Marketing Agent "
                    "refuses queries that look for sellers, so this would fail mid-run",
                )
            )

    for key in ("exclude_terms", "exclude_companies", "exclude_countries", "exclude_tlds"):
        missing = {_norm(x) for x in base[key]} - {_norm(x) for x in effective[key]}
        if missing:
            issues.append(
                Issue(
                    LEVEL,
                    "exclusions_shrunk",
                    key,
                    f"base exclusions would be removed: {sorted(missing)}",
                )
            )

    if search["use_job_feeds"] and not qual["accept_hiring_signals"]:
        issues.append(
            Issue(
                LEVEL,
                "feeds_without_hiring",
                "search.use_job_feeds",
                "job feeds produce hiring signals, but hiring signals are not accepted",
                severity="warning",
            )
        )
    if qual["accept_service_companies"]:
        issues.append(
            Issue(
                LEVEL,
                "service_companies_accepted",
                "qualification.accept_service_companies",
                "engineering-services firms are competitors; they would be kept as partner leads",
                severity="warning",
            )
        )
    excluded = {_norm(x) for x in effective.get("exclude_countries") or []}
    clash = sorted(c for c in effective.get("include_countries") or [] if _norm(c) in excluded)
    if clash:
        issues.append(
            Issue(
                LEVEL,
                "country_included_and_excluded",
                "include_countries",
                f"these countries are both wanted and excluded (the exclusion wins): {clash}",
            )
        )
    project = effective["project_context"]
    if not any(
        project[k] for k in ("industries", "products", "technologies", "engineering_requirements")
    ):
        issues.append(
            Issue(
                LEVEL,
                "no_audience",
                "project_context",
                "no industry, product, technology or capability set: base vocabulary only",
                severity="warning",
            )
        )
    return level_result(LEVEL, "business", issues)

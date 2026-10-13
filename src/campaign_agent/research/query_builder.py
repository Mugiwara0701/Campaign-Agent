"""Queries from the model's research needs. The model proposes WHAT to look up; these templates decide HOW."""

from collections.abc import Iterable
from dataclasses import dataclass

from campaign_agent.ir.patch import ResearchKind, ResearchNeed
from campaign_agent.research.query_policy import check_query, check_term

_TEMPLATES: dict[ResearchKind, tuple[str, ...]] = {
    "term_meaning": ("{term} meaning embedded engineering", "{term} explained"),
    "product_categories_for_technology": ("{term} product types", "products that use {term}"),
    "industries_for_capability": ("{term} industries", "{term} applications by industry"),
}


@dataclass(frozen=True)
class BuiltNeed:
    need: ResearchNeed
    queries: tuple[str, ...]


@dataclass(frozen=True)
class Dropped:
    term: str
    kind: str
    reason: str


def build_queries(
    needs: Iterable[ResearchNeed], exclusions: Iterable[str] = ()
) -> tuple[list[BuiltNeed], list[Dropped]]:
    """Policy-checked queries per need, and every need or query that was blocked, with the reason."""
    excluded = list(exclusions)
    built: list[BuiltNeed] = []
    dropped: list[Dropped] = []
    for need in needs:
        verdict = check_term(need.term, excluded)
        if not verdict.allowed:
            dropped.append(Dropped(need.term, need.kind, verdict.reason))
            continue
        queries: list[str] = []
        for template in _TEMPLATES[need.kind]:
            query = template.format(term=need.term.strip())
            gate = check_query(query, excluded)
            if gate.allowed:
                queries.append(query)
            else:
                dropped.append(Dropped(query, need.kind, gate.reason))
        if queries:
            built.append(BuiltNeed(need, tuple(queries)))
    return built, dropped

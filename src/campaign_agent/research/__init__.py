"""Bounded concept research behind a feature flag.

A fixed pipeline, not an agent loop:
  needs (closed kinds, from the extractor) -> query builder + policy -> search -> fetch -> summarizer -> Suggestions
At most `max_searches` searches and `max_fetches` page fetches per job. Anything that goes wrong is fail-soft:
the campaign continues without suggestions, and the reason is kept for the logs.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from campaign_agent.ir.models import CampaignIR
from campaign_agent.ir.patch import ResearchNeed, Suggestion
from campaign_agent.research.fetcher import FetchError, Page, PageFetcher
from campaign_agent.research.query_builder import build_queries
from campaign_agent.research.search_client import SearchClient, SearchError
from campaign_agent.research.summarizer import Summarizer

log = logging.getLogger("campaign_agent.research")

MAX_SEARCHES = 6
MAX_FETCHES = 5


@dataclass(frozen=True)
class ResearchResult:
    suggestions: tuple[Suggestion, ...] = ()
    searches: int = 0
    fetches: int = 0
    notes: tuple[str, ...] = field(
        default=()
    )  # why something was dropped or failed; never user text


class Researcher:
    def __init__(
        self,
        search: SearchClient,
        fetcher: PageFetcher,
        summarizer: Summarizer,
        *,
        max_searches: int = MAX_SEARCHES,
        max_fetches: int = MAX_FETCHES,
    ) -> None:
        self._search = search
        self._fetcher = fetcher
        self._summarizer = summarizer
        self._max_searches = max_searches
        self._max_fetches = max_fetches

    def run(self, needs: Iterable[ResearchNeed], ir: CampaignIR) -> ResearchResult:
        """Never raises. An empty result means nothing was found, or research could not run."""
        try:
            return self._run(list(needs), ir)
        except Exception:
            log.exception("research failed")
            return ResearchResult(notes=("research_failed",))

    def _run(self, needs: list[ResearchNeed], ir: CampaignIR) -> ResearchResult:
        exclusions = [*ir.exclusions.companies, *ir.exclusions.terms, *ir.exclusions.countries]
        built, dropped = build_queries(needs, exclusions)
        notes = [f"dropped:{d.kind}:{d.reason}" for d in dropped]
        for d in dropped:
            log.info("research need blocked", extra={"kind": d.kind, "reason": d.reason})
        searches = fetches = 0
        suggestions: list[Suggestion] = []
        for item in built:
            pages: list[Page] = []
            for query in item.queries:
                if searches >= self._max_searches:
                    notes.append("search_budget_used")
                    break
                searches += 1
                try:
                    hits = self._search.search(query)
                except SearchError as exc:
                    notes.append(f"search_failed:{exc}")
                    continue
                for hit in hits:
                    if fetches >= self._max_fetches:
                        notes.append("fetch_budget_used")
                        break
                    if any(p.url == hit.url for p in pages) or len(pages) >= 2:
                        continue
                    fetches += 1
                    try:
                        pages.append(self._fetcher.fetch(hit.url))
                    except FetchError as exc:
                        notes.append(f"fetch_failed:{exc}")
            summary = self._summarizer.summarize(item.need, pages)
            suggestions.extend(summary.suggestions)
            notes.extend(f"summary_dropped:{d}" for d in summary.dropped)
        return ResearchResult(tuple(suggestions), searches, fetches, tuple(notes))

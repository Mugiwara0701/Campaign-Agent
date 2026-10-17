"""Builds the research module from the settings. Returns None while research is switched off (the default)."""

from collections.abc import Callable

from campaign_agent.llm.adapter import GenParams, LLMAdapter
from campaign_agent.research import Researcher
from campaign_agent.research.brief import BriefWriter
from campaign_agent.research.fetcher import PageFetcher
from campaign_agent.research.search_client import (
    DEFAULT_DENY,
    DomainPolicy,
    SearchClient,
    SearxngClient,
)
from campaign_agent.research.summarizer import Summarizer
from campaign_agent.settings import Settings


def _split(raw: str) -> tuple[str, ...]:
    return tuple(d.strip().lower() for d in raw.split(",") if d.strip())


def build_researcher(
    settings: Settings,
    adapter: LLMAdapter,
    *,
    force: bool = False,
    wrap_search: Callable[[SearchClient], SearchClient] | None = None,
) -> Researcher | None:
    """`force` and `wrap_search` are for the benchmark: it switches research on for one run and watches the queries."""
    if not (settings.research_enabled or force):
        return None
    domains = DomainPolicy(
        allow=_split(settings.research_allow_domains),
        deny=(*DEFAULT_DENY, *_split(settings.research_deny_domains)),
    )
    search: SearchClient = SearxngClient(
        settings.searxng_url, domains, timeout_s=settings.research_timeout_s
    )
    return Researcher(
        wrap_search(search) if wrap_search else search,
        PageFetcher(domains, timeout_s=settings.research_timeout_s),
        Summarizer(adapter, GenParams(timeout_s=settings.llm_timeout_s, max_tokens=800)),
    )


def build_brief_writer(settings: Settings, adapter: LLMAdapter) -> BriefWriter | None:
    """The research brief on each proposal. Needs research on (RESEARCH_ENABLED) and RESEARCH_BRIEF (default true)."""
    if not (settings.research_enabled and settings.research_brief):
        return None
    domains = DomainPolicy(
        allow=_split(settings.research_allow_domains),
        deny=(*DEFAULT_DENY, *_split(settings.research_deny_domains)),
    )
    return BriefWriter(
        SearxngClient(settings.searxng_url, domains, timeout_s=settings.research_timeout_s),
        PageFetcher(domains, timeout_s=settings.research_timeout_s),
        adapter,
        GenParams(timeout_s=settings.llm_timeout_s, max_tokens=1200),
    )

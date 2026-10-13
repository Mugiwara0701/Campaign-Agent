"""Builds the research module from the settings. Returns None while research is switched off (the default)."""

from campaign_agent.llm.adapter import GenParams, LLMAdapter
from campaign_agent.research import Researcher
from campaign_agent.research.fetcher import PageFetcher
from campaign_agent.research.search_client import DEFAULT_DENY, DomainPolicy, SearxngClient
from campaign_agent.research.summarizer import Summarizer
from campaign_agent.settings import Settings


def _split(raw: str) -> tuple[str, ...]:
    return tuple(d.strip().lower() for d in raw.split(",") if d.strip())


def build_researcher(settings: Settings, adapter: LLMAdapter) -> Researcher | None:
    if not settings.research_enabled:
        return None
    domains = DomainPolicy(
        allow=_split(settings.research_allow_domains),
        deny=(*DEFAULT_DENY, *_split(settings.research_deny_domains)),
    )
    return Researcher(
        SearxngClient(settings.searxng_url, domains, timeout_s=settings.research_timeout_s),
        PageFetcher(domains, timeout_s=settings.research_timeout_s),
        Summarizer(adapter, GenParams(timeout_s=settings.llm_timeout_s, max_tokens=800)),
    )

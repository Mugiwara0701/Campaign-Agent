"""Model access: an adapter interface, two backends, and a scripted stub for tests."""

import httpx

from campaign_agent.llm.adapter import LLMAdapter
from campaign_agent.llm.ollama import OllamaAdapter
from campaign_agent.llm.openai_compat import OpenAICompatAdapter
from campaign_agent.settings import Settings


def make_adapter(settings: Settings, client: httpx.Client | None = None) -> LLMAdapter:
    if settings.llm_backend == "ollama":
        return OllamaAdapter(
            settings.llm_base_url,
            settings.llm_model,
            max_context=settings.llm_max_context,
            think=settings.llm_think,
            client=client,
        )
    forced = settings.llm_json_mode
    return OpenAICompatAdapter(
        settings.llm_base_url,
        settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        max_context=settings.llm_max_context,
        json_schema=forced in ("auto", "schema"),
        json_mode=forced in ("auto", "schema", "json"),
        client=client,
    )

"""Controlled vocabularies, read from the packaged base config."""

from functools import lru_cache
from importlib import resources

from campaign_agent.contract.loader import load_full
from campaign_agent.contract.model import LeadgenFile


@lru_cache(maxsize=1)
def base_config() -> LeadgenFile:
    text = (
        resources.files("campaign_agent.contract")
        .joinpath("base_config.yaml")
        .read_text(encoding="utf-8")
    )
    return load_full(text)


def base_capabilities() -> tuple[str, ...]:
    return tuple(base_config().search.capabilities)

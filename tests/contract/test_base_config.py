"""The packaged copy of the MA's config/leadgen.yaml is the first golden file."""

import hashlib
from importlib import resources

from campaign_agent.contract.field_metadata import defaults
from campaign_agent.contract.model import BASE_CONFIG_SOURCE
from campaign_agent.contract.vocabularies import base_capabilities, base_config


def _text() -> str:
    return (
        resources.files("campaign_agent.contract")
        .joinpath("base_config.yaml")
        .read_text(encoding="utf-8")
    )


def test_base_config_is_the_recorded_copy() -> None:
    assert hashlib.sha256(_text().encode()).hexdigest() == BASE_CONFIG_SOURCE["sha256"]


def test_base_config_loads_under_the_strict_model() -> None:
    cfg = base_config()
    assert cfg.search.queries_per_run == 16
    assert cfg.qualification.accept_service_companies is False


def test_contract_defaults_equal_the_values_in_the_base_file() -> None:
    """The defaults declared once in the model must be the values the MA file actually uses."""
    cfg = base_config().model_dump()
    for path, default in defaults().items():
        if path.startswith(("exclude_", "project_context.")) or path in (
            "config_version",
            "desktop_engines",
        ):
            continue  # lists the base legitimately fills, or keys the file does not carry
        node = cfg
        for part in path.split("."):
            node = node[part]
        assert node == default, f"{path}: file has {node!r}, contract default is {default!r}"


def test_capabilities_are_available_as_a_vocabulary() -> None:
    assert "AOSP customization" in base_capabilities()

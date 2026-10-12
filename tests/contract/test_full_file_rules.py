"""Rules that need the whole file: mutate the golden base config and check each one fails."""

import copy
from typing import Any

import pytest
import yaml

from campaign_agent.contract.loader import ContractViolation, load_full
from campaign_agent.contract.vocabularies import base_config


def _base() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(
        (
            __import__("importlib.resources").resources.files("campaign_agent.contract")
            / "base_config.yaml"
        ).read_text()
    )
    return copy.deepcopy(raw)


def _load(data: dict[str, Any]) -> None:
    load_full(yaml.safe_dump(data))


def test_base_loads() -> None:
    _load(_base())
    assert base_config().search.capabilities


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d.update(surprise=1), "surprise"),
        (lambda d: d["qualification"].update(min_qualfy=70), "qualification.min_qualfy"),
        (lambda d: d["search"].update(queries_per_run="16"), "search.queries_per_run"),
        (lambda d: d["search"].update(queries_per_run=True), "search.queries_per_run"),
        (lambda d: d["qualification"].update(min_draft=10), "min_draft"),
        (lambda d: d["limits"].update(max_pages_per_run=50), "max_pages_per_run"),
        (lambda d: d["search"].update(results_per_query=2), "results_per_query"),
        (lambda d: d["search"].update(industries=[]), "search.industries"),
        (lambda d: d["search"].pop("builder_phrases"), "search.builder_phrases"),
        (lambda d: d.update(config_version="2.0"), "config_version"),
        (lambda d: d.pop("contacts"), "contacts"),
    ],
)
def test_broken_full_file_is_rejected(mutate: Any, expected: str) -> None:
    data = _base()
    mutate(data)
    with pytest.raises(ContractViolation) as err:
        _load(data)
    assert expected in str(err.value)

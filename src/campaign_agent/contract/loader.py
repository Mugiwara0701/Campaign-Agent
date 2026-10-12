"""Parse and validate contract files. Every failure is a ContractViolation listing key paths."""

from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from campaign_agent.contract.model import CampaignOverlay, LeadgenFile

MAX_BYTES = 256_000


@dataclass(frozen=True)
class Violation:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class ContractViolation(ValueError):
    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__("; ".join(str(v) for v in violations))


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load keeps the last of two equal keys silently; a typo-sized trap, so refuse it."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_yaml(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ContractViolation([Violation("(file)", f"larger than {MAX_BYTES} bytes")])
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 (SafeLoader subclass)
    except yaml.YAMLError as exc:
        raise ContractViolation([Violation("(file)", f"not valid YAML: {exc}")]) from exc
    if not isinstance(data, dict):
        raise ContractViolation([Violation("(file)", "top level must be a mapping")])
    return data


def _validate[M: BaseModel](model: type[M], data: dict[str, Any]) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ContractViolation(
            [
                Violation(".".join(str(p) for p in e["loc"]) or "(file)", e["msg"])
                for e in exc.errors()
            ]
        ) from exc


def load_full(text: str) -> LeadgenFile:
    return _validate(LeadgenFile, parse_yaml(text))


def load_overlay(text: str) -> CampaignOverlay:
    return _validate(CampaignOverlay, parse_yaml(text))

"""The closed set of IR paths a patch may touch, built from the IR models (one source of truth)."""

from dataclasses import dataclass
from types import NoneType
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, TypeAdapter

from campaign_agent.ir.models import CampaignIR

Kind = Literal["scalar", "list"]
Mapping = Literal["supported", "proposed", "not_emitted"]

_SECTIONS = ("meta", "objective", "audience", "qualification", "contacts", "exclusions", "limits")
_NOT_PATCHABLE = {"meta.id", "meta.contract_version", "meta.revision", "objective.type"}

# Not emitted into the YAML at all.
_NOT_EMITTED = {"meta.name", "meta.description", "objective.summary", "organisation_context"}
# Stored, but the contract has no key for them yet: reported as "not applied".
_PROPOSED = {
    "audience.include_countries",
    "audience.employee_min",
    "audience.employee_max",
    "contacts.preferred_roles",
    "audience.signals",
}
# Web research may propose values for these only (concept research).
_SUGGESTIBLE = {
    "audience.industries",
    "audience.products",
    "audience.technologies",
    "audience.capabilities_needed",
}


@dataclass(frozen=True)
class FieldSpec:
    path: str
    kind: Kind
    adapter: TypeAdapter[Any]
    mapping: Mapping
    suggestible: bool


def _strip_none(tp: Any) -> Any:
    args = get_args(tp)
    if NoneType in args:
        rest = [a for a in args if a is not NoneType]
        return rest[0]
    return tp


def _spec(path: str, annotation: Any, metadata: list[Any]) -> FieldSpec:
    inner = _strip_none(annotation)
    kind: Kind = "list" if get_origin(inner) is list else "scalar"
    typed: Any = Annotated[(inner, *metadata)] if metadata else inner
    adapter: TypeAdapter[Any] = TypeAdapter(typed, config=ConfigDict(strict=True))
    mapping: Mapping = (
        "not_emitted" if path in _NOT_EMITTED else "proposed" if path in _PROPOSED else "supported"
    )
    return FieldSpec(path, kind, adapter, mapping, path in _SUGGESTIBLE)


def _build() -> dict[str, FieldSpec]:
    out: dict[str, FieldSpec] = {}
    for section in _SECTIONS:
        sub = CampaignIR.model_fields[section].annotation
        if not (isinstance(sub, type) and issubclass(sub, BaseModel)):
            raise TypeError(f"{section} is not a model")
        for name, info in sub.model_fields.items():
            path = f"{section}.{name}"
            if path not in _NOT_PATCHABLE:
                out[path] = _spec(path, info.annotation, list(info.metadata))
    info = CampaignIR.model_fields["organisation_context"]
    out["organisation_context"] = _spec(
        "organisation_context", info.annotation, list(info.metadata)
    )
    return out


REGISTRY: dict[str, FieldSpec] = _build()
NOT_PATCHABLE = frozenset(_NOT_PATCHABLE) | {"provenance", "suggestions", "unsupported"}


def get_value(ir: CampaignIR, path: str) -> Any:
    node: Any = ir
    for part in path.split("."):
        node = getattr(node, part)
    return node

"""The JSON schema the model must follow, derived from the IR patch models and the field registry.

Paths are an enum of the registry's patchable paths, so a small model cannot wander off to a key
that does not exist; `approval` and system fields are not in the list. Values are a flat union (no
recursive $ref), which constrained decoders handle better than the general JSON value type.
"""

from typing import Any

from campaign_agent.ir.patch import IRPatch
from campaign_agent.ir.registry import REGISTRY

_VALUE: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "null"},
    ]
}


def closed_patch_schema() -> dict[str, Any]:
    schema = IRPatch.model_json_schema()
    defs = schema["$defs"]
    op = defs["PatchOp"]["properties"]
    op["path"] = {"type": "string", "enum": sorted(REGISTRY)}
    op["value"] = _VALUE
    defs.pop("JsonValue", None)
    op.pop("evidence_quote", None)
    op["evidence_quote"] = {"anyOf": [{"type": "string", "maxLength": 300}, {"type": "null"}]}
    defs["PatchOp"]["required"] = ["path", "op", "value", "evidence_quote"]
    schema["required"] = ["ops", "unsupported", "research_needs"]
    return schema

"""L3: the overlay and the effective configuration match the contract's types and bounds.

Only field-level errors are reported here (type, range, pattern, unknown key). Errors from
whole-model rules (min_draft >= min_qualify ...) belong to L4, which attaches a fix.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from campaign_agent.contract.model import CampaignOverlay, LeadgenFile
from campaign_agent.validators.report import Issue, LevelResult, level_result

LEVEL = 3


def _field_errors(model: type[BaseModel], data: Mapping[str, Any], where: str) -> list[Issue]:
    try:
        model.model_validate(dict(data))
    except ValidationError as exc:
        return [
            Issue(LEVEL, "schema", ".".join(str(p) for p in e["loc"]), f"{where}: {e['msg']}")
            for e in exc.errors()
            if e["loc"]
        ]
    return []


def check_schema(overlay: Mapping[str, Any], effective: Mapping[str, Any]) -> LevelResult:
    issues = _field_errors(CampaignOverlay, overlay, "overlay")
    if not issues:  # a broken overlay would only repeat itself in the merged file
        issues = _field_errors(LeadgenFile, effective, "effective config")
    return level_result(LEVEL, "schema", issues)

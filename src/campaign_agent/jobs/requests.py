"""The request contract between the dashboard and the agent, as data."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# What the dashboard may ask for. approve and rollback arrive with publishing (M7).
REQUEST_TYPES = ("interpret", "answer", "edit", "suggestion", "approve", "discard", "rollback")


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNKNOWN_CAMPAIGN = "unknown_campaign"
    STALE_REVISION = "stale_revision"
    MISSING_BASE_REVISION = "missing_base_revision"
    WRONG_STATE = "wrong_state"
    INPUT_REJECTED = "input_rejected"
    NEEDS_REPHRASE = "needs_rephrase"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_ANSWER = "invalid_answer"
    NOT_AVAILABLE = "not_available"
    INTERNAL_ERROR = "internal_error"
    WORKER_CRASHED = "worker_crashed"


@dataclass(frozen=True)
class Request:
    id: str
    campaign_id: str
    request_type: str
    payload: dict[str, Any]
    base_revision: int | None
    requested_by: str


@dataclass(frozen=True)
class RequestError(Exception):
    code: ErrorCode
    detail: str
    result: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"

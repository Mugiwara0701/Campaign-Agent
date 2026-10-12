"""The request contract between the dashboard and the agent, as data."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# What the dashboard may ask for.
REQUEST_TYPES = (
    "interpret",
    "answer",
    "edit",
    "suggestion",
    "approve",
    "discard",
    "rollback",
    "deactivate",
)


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
    STALE_APPROVAL = "stale_approval"
    PUBLISH_BLOCKED = "publish_blocked"
    PUBLISH_FAILED = "publish_failed"
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
    # Set when the refusal itself changes the conversation (a publish that was blocked). Otherwise a
    # refused request leaves the conversation as it was.
    conv: Any = None

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"

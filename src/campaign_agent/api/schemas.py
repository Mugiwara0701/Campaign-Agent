"""What the dashboard's server sends to the gateway. Strict: unknown fields are refused."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PAYLOAD_BYTES = 64 * 1024

RequestType = Literal[
    "interpret", "answer", "edit", "suggestion", "discard", "approve", "rollback", "deactivate"
]


class RequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: RequestType
    payload: dict[str, Any] = Field(default_factory=dict)
    # The `revision` the user was looking at. Required by the Campaign Agent for `edit`; it says so itself if missing.
    base_revision: int | None = Field(default=None, ge=0)
    # The signed-in dashboard user, as the dashboard's server verified it. It becomes the approval record.
    requested_by: str = Field(min_length=1, max_length=100)
    # A repeat for the same campaign is the same request. Strongly recommended; a random one is made when missing.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("requested_by")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("requested_by must not be empty")
        return value

    @field_validator("payload")
    @classmethod
    def _not_huge(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload is too large")
        return value


class StateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["running", "paused"]
    requested_by: str = Field(min_length=1, max_length=200)

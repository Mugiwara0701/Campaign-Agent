"""What the Marketing Agent may send as an event, and the checks every event passes before it is stored."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from campaign_agent.guard.input_guard import looks_like_secret

MAX_EVENTS_PER_BATCH = 50
MAX_BODY_BYTES = 64 * 1024
MAX_DETAILS_BYTES = 2048

Kind = Literal["applied", "rejected", "blocker", "resolved", "info"]
Severity = Literal["info", "warning", "error", "critical"]


class EventIn(BaseModel):
    """One event. Unknown fields are refused, so nothing unexpected is stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    occurred_at: AwareDatetime
    kind: Kind
    severity: Severity
    # Always MA_...: an event can never pose as something the Campaign Agent itself reports.
    code: str = Field(pattern=r"^MA_[A-Z0-9_]{1,60}$")
    message: str = Field(min_length=1, max_length=300)
    campaign_id: UUID | None = None
    version: int | None = Field(default=None, ge=1, le=1_000_000)
    pass_id: str | None = Field(default=None, max_length=100)
    # A repeating failure reuses its key, so one open blocker is updated instead of a flood. Default: the code.
    dedupe_key: str | None = Field(default=None, min_length=1, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.dedupe_key or self.code


class Rejected(BaseModel):
    event_id: str | None
    reason: str


def _details_problem(details: dict[str, Any]) -> str | None:
    try:
        text = json.dumps(details, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return "details_not_json"
    if len(text.encode("utf-8")) > MAX_DETAILS_BYTES:
        return "details_too_big"
    if looks_like_secret(text):
        return "secret_like"
    return None


def parse_event(raw: Any) -> tuple[EventIn | None, Rejected | None]:
    """Validate one event. Never raises; a bad event comes back as a rejection with a short reason."""
    event_id = raw.get("event_id") if isinstance(raw, dict) else None
    shown = str(event_id) if isinstance(event_id, str) else None
    if not isinstance(raw, dict):
        return None, Rejected(event_id=None, reason="not_an_object")
    try:
        event = EventIn.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or "event"
        return None, Rejected(event_id=shown, reason=f"invalid:{field}")
    if looks_like_secret(event.message):
        return None, Rejected(event_id=shown, reason="secret_like")
    problem = _details_problem(event.details)
    if problem is not None:
        return None, Rejected(event_id=shown, reason=problem)
    return event, None


def parse_batch(body: Any) -> tuple[list[EventIn], list[Rejected]] | None:
    """None when the body is not {"events": [1..50 items]}: the whole request is invalid (422)."""
    if not isinstance(body, dict) or set(body) != {"events"}:
        return None
    items = body["events"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_EVENTS_PER_BATCH:
        return None
    good: list[EventIn] = []
    bad: list[Rejected] = []
    for item in items:
        event, rejected = parse_event(item)
        if event is not None:
            good.append(event)
        elif rejected is not None:
            bad.append(rejected)
    return good, bad


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

"""A scripted model for tests and CI: deterministic, no network. Records every call."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from campaign_agent.llm.adapter import (
    Capabilities,
    GenParams,
    LLMError,
    Message,
    Mode,
    RawResult,
    Usage,
)

Reply = str | Exception | Callable[[Sequence[Message]], str]


@dataclass
class Call:
    messages: list[Message]
    mode: Mode
    schema: dict[str, Any] | None
    params: GenParams


@dataclass
class StubAdapter:
    """Replies are consumed in order; the last one repeats if the script runs out."""

    replies: list[Reply] = field(default_factory=list)
    caps: Capabilities = field(default_factory=lambda: Capabilities(True, True, 8192))
    name: str = "stub"
    calls: list[Call] = field(default_factory=list)

    def capabilities(self) -> Capabilities:
        return self.caps

    def generate(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None,
        mode: Mode,
        params: GenParams,
    ) -> RawResult:
        self.calls.append(Call(list(messages), mode, json_schema, params))
        if not self.replies:
            raise LLMError("stub has no reply")
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        text = reply(messages) if callable(reply) else reply
        return RawResult(text, Usage(10, 5), 0.0, self.name, "stub", mode)

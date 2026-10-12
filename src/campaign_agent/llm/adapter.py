"""Provider-neutral interface to a language model.

The extractor talks only to this. Backends differ in what they can enforce, so each reports its
capabilities and the caller uses the strongest mode available:
  schema  the backend constrains output to a JSON schema
  json    the backend guarantees syntactically valid JSON
  prompt  nothing is enforced; the prompt asks for JSON and the reply is parsed strictly
Whatever the mode, the reply is validated against the schema by the caller.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Mode = Literal["schema", "json", "prompt"]
Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class GenParams:
    temperature: float = 0.0
    seed: int | None = 0
    max_tokens: int = 1500
    timeout_s: float = 300.0


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class RawResult:
    text: str
    usage: Usage
    latency_s: float
    backend: str
    model: str
    mode: Mode


@dataclass(frozen=True)
class Capabilities:
    json_schema: bool
    json_mode: bool
    max_context: int

    def strongest(self) -> Mode:
        if self.json_schema:
            return "schema"
        return "json" if self.json_mode else "prompt"

    def supports(self, mode: Mode) -> bool:
        return {"schema": self.json_schema, "json": self.json_mode, "prompt": True}[mode]


class LLMError(Exception):
    """Base class. Not retried unless it is one of the transient subclasses."""


class LLMTimeout(LLMError):
    pass


class LLMConnectionError(LLMError):
    pass


class LLMServerError(LLMError):
    pass


class LLMUnsupportedModeError(LLMError):
    """The backend refused the requested mode (for example no schema support)."""


class LLMBadResponseError(LLMError):
    pass


TRANSIENT = (LLMTimeout, LLMConnectionError, LLMServerError)

_DOWNGRADE: dict[Mode, Mode | None] = {"schema": "json", "json": "prompt", "prompt": None}


def weaker(mode: Mode) -> Mode | None:
    return _DOWNGRADE[mode]


class LLMAdapter(Protocol):
    name: str

    def capabilities(self) -> Capabilities: ...

    def generate(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None,
        mode: Mode,
        params: GenParams,
    ) -> RawResult: ...

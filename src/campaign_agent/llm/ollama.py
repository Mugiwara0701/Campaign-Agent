"""Ollama's native chat API (POST /api/chat). Supports schema-constrained output."""

import time
from collections.abc import Sequence
from typing import Any

import httpx

from campaign_agent.llm.adapter import (
    Capabilities,
    GenParams,
    LLMBadResponseError,
    LLMConnectionError,
    LLMError,
    LLMServerError,
    LLMTimeout,
    LLMUnsupportedModeError,
    Message,
    Mode,
    RawResult,
    Usage,
)


def classify_http_error(status: int, body: str, *, label: str) -> LLMError:
    if status >= 500:
        return LLMServerError(f"{label}: server error {status}")
    lowered = body.lower()
    if status in (400, 422) and any(w in lowered for w in ("format", "schema", "response_format")):
        return LLMUnsupportedModeError(f"{label}: {body[:200]}")
    return LLMError(f"{label}: HTTP {status}: {body[:200]}")


class OllamaAdapter:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        max_context: int = 8192,
        think: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._max_context = max_context
        self._think = think
        self._client = client or httpx.Client()

    def capabilities(self) -> Capabilities:
        return Capabilities(json_schema=True, json_mode=True, max_context=self._max_context)

    def generate(
        self,
        messages: Sequence[Message],
        *,
        json_schema: dict[str, Any] | None,
        mode: Mode,
        params: GenParams,
    ) -> RawResult:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "think": self._think,
            "options": {
                "temperature": params.temperature,
                "num_predict": params.max_tokens,
                "num_ctx": self._max_context,
                **({"seed": params.seed} if params.seed is not None else {}),
            },
        }
        if mode == "schema" and json_schema is not None:
            body["format"] = json_schema
        elif mode in ("schema", "json"):
            body["format"] = "json"
        started = time.monotonic()
        try:
            response = self._client.post(
                f"{self._base}/api/chat", json=body, timeout=params.timeout_s
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"ollama: no answer within {params.timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise LLMConnectionError(f"ollama: cannot reach {self._base}") from exc
        if response.status_code != 200:
            raise classify_http_error(response.status_code, response.text, label="ollama")
        try:
            data = response.json()
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMBadResponseError("ollama: unexpected response shape") from exc
        return RawResult(
            text=text,
            usage=Usage(data.get("prompt_eval_count"), data.get("eval_count")),
            latency_s=time.monotonic() - started,
            backend=self.name,
            model=self._model,
            mode=mode,
        )

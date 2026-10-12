"""OpenAI-compatible chat API (vLLM, hosted endpoints): POST {base}/chat/completions."""

import time
from collections.abc import Sequence
from typing import Any

import httpx

from campaign_agent.llm.adapter import (
    Capabilities,
    GenParams,
    LLMBadResponseError,
    LLMConnectionError,
    LLMTimeout,
    Message,
    Mode,
    RawResult,
    Usage,
)
from campaign_agent.llm.ollama import classify_http_error


class OpenAICompatAdapter:
    name = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        max_context: int = 8192,
        json_schema: bool = True,
        json_mode: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._key = api_key
        self._caps = Capabilities(json_schema, json_mode, max_context)
        self._client = client or httpx.Client()

    def capabilities(self) -> Capabilities:
        return self._caps

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
            "temperature": params.temperature,
            "max_tokens": params.max_tokens,
            **({"seed": params.seed} if params.seed is not None else {}),
        }
        if mode == "schema" and json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "campaign_patch", "schema": json_schema, "strict": True},
            }
        elif mode in ("schema", "json"):
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        started = time.monotonic()
        try:
            response = self._client.post(
                f"{self._base}/chat/completions",
                json=body,
                headers=headers,
                timeout=params.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"openai: no answer within {params.timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise LLMConnectionError(f"openai: cannot reach {self._base}") from exc
        if response.status_code != 200:
            raise classify_http_error(response.status_code, response.text, label="openai")
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponseError("openai: unexpected response shape") from exc
        return RawResult(
            text=text or "",
            usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
            latency_s=time.monotonic() - started,
            backend=self.name,
            model=self._model,
            mode=mode,
        )

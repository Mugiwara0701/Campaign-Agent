import json
from typing import Any

import httpx
import pytest

from campaign_agent.llm import make_adapter
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
    weaker,
)
from campaign_agent.llm.ollama import OllamaAdapter
from campaign_agent.llm.openai_compat import OpenAICompatAdapter
from campaign_agent.settings import Settings

MSGS = [Message("system", "s"), Message("user", "u")]
SCHEMA = {"type": "object", "properties": {"ops": {"type": "array"}}}


def client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def ollama_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"message": {"content": '{"ops": []}'}, "prompt_eval_count": 11, "eval_count": 4}
    )


def openai_ok(request: httpx.Request) -> httpx.Response:
    body = {
        "choices": [{"message": {"content": '{"ops": []}'}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3},
    }
    return httpx.Response(200, json=body)


# --- capabilities ---------------------------------------------------------------------------------------------


def test_strongest_mode_and_downgrade_order() -> None:
    assert Capabilities(True, True, 1).strongest() == "schema"
    assert Capabilities(False, True, 1).strongest() == "json"
    assert Capabilities(False, False, 1).strongest() == "prompt"
    assert Capabilities(False, True, 1).supports("schema") is False
    assert [weaker("schema"), weaker("json"), weaker("prompt")] == ["json", "prompt", None]


# --- ollama -----------------------------------------------------------------------------------------------------


def test_ollama_request_is_deterministic_and_constrained() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return ollama_ok(request)

    adapter = OllamaAdapter(
        "http://host:11434/", "qwen3.5:9b", max_context=4096, client=client(handler)
    )
    result = adapter.generate(
        MSGS, json_schema=SCHEMA, mode="schema", params=GenParams(seed=7, max_tokens=300)
    )
    body = seen["body"]
    assert seen["url"] == "http://host:11434/api/chat"
    assert body["stream"] is False
    assert body["think"] is False
    assert body["format"] == SCHEMA
    assert body["options"] == {"temperature": 0.0, "num_predict": 300, "num_ctx": 4096, "seed": 7}
    assert body["messages"][1] == {"role": "user", "content": "u"}
    assert result.text == '{"ops": []}'
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (11, 4)
    assert result.mode == "schema"


def test_ollama_modes_json_and_prompt() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ollama_ok(request)

    adapter = OllamaAdapter("http://h", "m", client=client(handler))
    adapter.generate(MSGS, json_schema=None, mode="json", params=GenParams())
    adapter.generate(MSGS, json_schema=None, mode="prompt", params=GenParams())
    assert bodies[0]["format"] == "json"
    assert "format" not in bodies[1]


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), LLMTimeout),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), LLMConnectionError),
        (lambda r: httpx.Response(503, text="busy"), LLMServerError),
        (
            lambda r: httpx.Response(400, text="invalid format: schema not supported"),
            LLMUnsupportedModeError,
        ),
        (lambda r: httpx.Response(404, text="model not found"), LLMError),
        (lambda r: httpx.Response(200, text="not json"), LLMBadResponseError),
        (lambda r: httpx.Response(200, json={"unexpected": 1}), LLMBadResponseError),
    ],
)
def test_ollama_errors_are_classified(handler: Any, error: type[Exception]) -> None:
    adapter = OllamaAdapter("http://h", "m", client=client(handler))
    with pytest.raises(error) as caught:
        adapter.generate(MSGS, json_schema=None, mode="prompt", params=GenParams())
    assert type(caught.value) is error


# --- openai-compatible ----------------------------------------------------------------------------------------


def test_openai_request_and_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return openai_ok(request)

    adapter = OpenAICompatAdapter("http://h/v1", "m", api_key="k", client=client(handler))
    result = adapter.generate(MSGS, json_schema=SCHEMA, mode="schema", params=GenParams(seed=None))
    assert seen["url"] == "http://h/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "seed" not in seen["body"]
    assert result.text == '{"ops": []}'
    assert result.usage.completion_tokens == 3


def test_openai_json_object_and_prompt_modes_and_no_key() -> None:
    seen: list[dict[str, Any]] = []
    auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        auth.append(request.headers.get("authorization"))
        return openai_ok(request)

    adapter = OpenAICompatAdapter("http://h", "m", client=client(handler))
    adapter.generate(MSGS, json_schema=None, mode="json", params=GenParams())
    adapter.generate(MSGS, json_schema=None, mode="prompt", params=GenParams())
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in seen[1]
    assert auth == [None, None]


def test_openai_errors_are_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="response_format json_schema is not supported")

    adapter = OpenAICompatAdapter("http://h", "m", client=client(handler))
    with pytest.raises(LLMUnsupportedModeError):
        adapter.generate(MSGS, json_schema=SCHEMA, mode="schema", params=GenParams())


def test_openai_capabilities_follow_the_flags() -> None:
    caps = OpenAICompatAdapter("http://h", "m", json_schema=False, json_mode=True).capabilities()
    assert caps.strongest() == "json"


# --- settings and factory -------------------------------------------------------------------------------------


def test_settings_read_a_dotenv_file_and_hide_secrets(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "DATABASE_URL", "LLM_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "LLM_BASE_URL=http://x:1\nLLM_API_KEY=topsecret\nDATABASE_URL=postgresql://u:pw@h/db\nOTHER=1\n"
    )
    settings = Settings(_env_file=env)  # type: ignore[call-arg]
    assert settings.llm_base_url == "http://x:1"
    assert settings.llm_api_key.get_secret_value() == "topsecret"
    assert "topsecret" not in repr(settings)
    assert "pw@h" not in repr(settings)
    assert settings.llm_think is False


def test_factory_picks_the_backend() -> None:
    assert make_adapter(Settings(llm_backend="ollama")).name == "ollama"  # type: ignore[call-arg]
    openai = make_adapter(Settings(llm_backend="openai", llm_json_mode="json"))  # type: ignore[call-arg]
    assert openai.name == "openai"
    assert openai.capabilities().strongest() == "json"

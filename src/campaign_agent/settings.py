"""Settings from the environment or a git-ignored .env file. Secrets are never printed."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    llm_backend: Literal["ollama", "openai"] = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "qwen3.5:9b"
    llm_timeout_s: float = Field(default=300.0, gt=0, le=1800)
    # the extraction prompt alone is about 1,300 tokens
    llm_max_context: int = Field(default=8192, ge=2048)
    # auto = the strongest mode the backend supports; or force one.
    llm_json_mode: Literal["auto", "schema", "json", "prompt"] = "auto"
    # Qwen3-style models think before answering; for extraction that only costs time.
    llm_think: bool = False
    database_url: SecretStr | None = None
    poll_interval_s: float = Field(default=2.0, gt=0, le=60)
    # How long a request may stay "processing" before another worker may take it over. It must be longer
    # than the slowest request: a few model calls at LLM_TIMEOUT_S each.
    lease_s: int = Field(default=1800, ge=60)
    max_attempts: int = Field(default=3, ge=1, le=10)
    worker_id: str = ""


def load_settings() -> Settings:
    return Settings()

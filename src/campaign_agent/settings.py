"""Settings from the environment or a git-ignored .env file. Secrets are never printed."""

from pathlib import Path
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
    # Only for `migrate` and `agent-password`, which need the database OWNER. Optional: when it is not set they
    # use DATABASE_URL. Keep it out of the .env of a machine where the worker runs, once the migrations are done.
    migrate_database_url: SecretStr | None = None
    poll_interval_s: float = Field(default=2.0, gt=0, le=60)
    # How long a request may stay "processing" before another worker may take it over. It must be longer
    # than the slowest request: a few model calls at LLM_TIMEOUT_S each.
    lease_s: int = Field(default=1800, ge=60)
    max_attempts: int = Field(default=3, ge=1, le=10)
    # Requests per requester per minute (0 = no limit). See docs/security-review.md.
    rate_limit_per_minute: int = Field(default=30, ge=0, le=10_000)
    # A new campaign with no request is started by the service itself (it sends the `interpret` request). Only a campaign
    # with text, older than the grace period (the dashboard's own request wins), younger than the max age.
    auto_interpret: bool = True
    auto_interpret_grace_s: int = Field(default=20, ge=0, le=3600)
    auto_interpret_max_age_h: int = Field(default=168, ge=1, le=24 * 365)
    worker_id: str = ""
    # Level 6: a pinned checkout of the Marketing Agent on this machine, run with no network (see downstream.py).
    ma_checkout: Path | None = None
    ma_python: str = "python3"
    ma_timeout_s: float = Field(default=60.0, gt=0, le=600)
    # true: with no usable checkout nothing can be approved. false: level 6 is "skipped", visibly.
    ma_required: bool = False
    # Concept research (what a term means). Off unless switched on; nothing is searched while it is off.
    research_enabled: bool = False
    searxng_url: str = (
        "http://127.0.0.1:8080"  # our own instance on this machine, never a shared one
    )
    # Comma-separated domains. Allow empty = any public site except the denied ones (and the built-in deny list).
    research_allow_domains: str = ""
    research_deny_domains: str = ""
    research_timeout_s: float = Field(default=10.0, gt=0, le=60)
    # With research on: a research brief (target profile with sources) on each proposal (research/brief.py).
    research_brief: bool = True
    # The dashboard gateway (docs/dashboard-api.md), run by `python -m campaign_agent api`, normally on Render. It logs in
    # to the database as the limited `campaign_dashboard` role (never the owner) and checks one shared token (32+ characters).
    api_database_url: SecretStr | None = None
    api_token: SecretStr | None = None
    # The signed-in person behind each request (api/users.py): the Supabase project URL (its signing keys), or the
    # legacy shared JWT secret. API_REQUIRE_USER=true refuses requests that carry no valid user token.
    api_supabase_url: str | None = None
    api_supabase_jwt_secret: SecretStr | None = None
    api_require_user: bool = False
    # The direct link to the Marketing Agent over Tailscale (docs/ca-ma-link.md). Off unless switched on.
    ma_link_enabled: bool = False
    # "tailscale" = this machine's Tailscale address (found when the listener starts); or one literal address.
    # 0.0.0.0 is refused: the listener must never be reachable on the LAN.
    ma_link_bind: str = "tailscale"
    ma_link_port: int = Field(default=8787, ge=1024, le=65535)
    # At least 32 characters. The previous one is accepted too, for one rollover.
    ma_link_token: SecretStr | None = None
    ma_link_token_previous: SecretStr | None = None
    # true: the Marketing Agent is given the live campaign only while the dashboard's own settings allow it (the campaign
    # is switched on and today is between its start and end dates). false (default): approval alone makes it live.
    ma_link_respect_schedule: bool = False


def load_settings() -> Settings:
    return Settings()

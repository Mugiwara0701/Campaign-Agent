"""What the MA-side tests share. They run on the Marketing Agent's machine, in the Marketing Agent's Python
(`~/Desktop/Marketing-AI-Agent/.venv/bin/python -m qa.soak --side ma`), so its libraries and settings are the real ones."""

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from qa.lib.core import Ctx, Resp, Skip, http, read_env_file

MA_DIR = Path(os.environ.get("QA_MA_DIR") or Path.home() / "Desktop" / "Marketing-AI-Agent")
QA_MARK = "[QA TEST]"


def load_ma_env(ctx: Ctx) -> None:
    """Put the Marketing Agent's own .env into the environment (as its service has it) and make its code importable."""
    env = read_env_file(MA_DIR / ".env")
    if not env:
        raise Skip(f"no .env in {MA_DIR}")
    for key, value in env.items():
        os.environ.setdefault(key, value)
    ctx.env.update(env)
    os.environ.setdefault(
        "MA_LINK_CACHE_DIR", str(MA_DIR / ".cache" / "campaign")
    )  # the folder the running service drains
    for path in (MA_DIR, MA_DIR / "libs" / "agentkit"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def run_agent(ctx: Ctx, *args: str, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    """`python -m agent <args>` in the Marketing Agent's folder, with its settings."""
    # The CPU model needs minutes for one answer; the leadgen commands set this themselves, the others do not.
    # MA_ON_OFFICE_MACHINE: the qa suite runs on the Marketing Agent machine itself (qa/run.sh deploys it there).
    env = {"LLM_MIN_TIMEOUT": "420", **os.environ, **ctx.env, "MA_ON_OFFICE_MACHINE": "1"}
    return subprocess.run(
        [str(MA_DIR / ".venv" / "bin" / "python"), "-m", "agent", *args],
        cwd=MA_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def systemd_active(unit: str) -> str:
    done = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return done.stdout.strip() or done.stderr.strip()


class Pg:
    """A small synchronous view of the Marketing Agent's database (its own asyncpg, its own event loop, no pool).
    One per thread: the sampler has its own."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.conn: Any = None
        self.lock = threading.Lock()

    def _connect(self) -> Any:
        import asyncpg
        from agentkit.db import parse_dsn

        return self.loop.run_until_complete(
            asyncpg.connect(
                **parse_dsn(os.environ["DATABASE_URL"]), statement_cache_size=0, timeout=30
            )
        )

    def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        with self.lock:
            if self.conn is None or self.conn.is_closed():
                self.conn = self._connect()
            rows = self.loop.run_until_complete(self.conn.fetch(sql, *args, timeout=60))
            return [dict(r) for r in rows]

    def one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        rows = self.fetch(sql, *args)
        return rows[0] if rows else None

    def execute(self, sql: str, *args: Any) -> str:
        with self.lock:
            if self.conn is None or self.conn.is_closed():
                self.conn = self._connect()
            return str(self.loop.run_until_complete(self.conn.execute(sql, *args, timeout=60)))


def pg(ctx: Ctx) -> Pg:
    if "pg" not in ctx.data:
        ctx.data["pg"] = Pg()
    return ctx.data["pg"]  # type: ignore[no-any-return]


def slack(ctx: Ctx, method: str, **payload: Any) -> dict[str, Any]:
    token = ctx.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise Skip("SLACK_BOT_TOKEN is not set")
    r: Resp = http(
        "POST",
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        body=payload,
        timeout=30,
    )
    if r.status != 200:
        raise AssertionError(f"Slack {method} answered HTTP {r.status}: {r.text[:100]}")
    return r.json()  # type: ignore[no-any-return]


def gmail(ctx: Ctx) -> Any:
    from agentkit import gmail as g

    # The runner's working folder is not the Marketing Agent's: its Gmail files are found by absolute path.
    os.environ.setdefault("GMAIL_TOKEN_PATH", str(MA_DIR / "token.json"))
    os.environ.setdefault("GMAIL_CREDENTIALS_PATH", str(MA_DIR / "credentials.json"))
    return g.get_service()


def test_recipient(ctx: Ctx) -> str:
    value = ctx.getenv("TEST_RECIPIENT")
    if not value:
        raise Skip("TEST_RECIPIENT is not set: nothing may be sent")
    return value.strip()


def cache_dir() -> Path:
    """Where the Marketing Agent keeps its link outbox: the running service drains the same folder."""
    return Path(os.environ["MA_LINK_CACHE_DIR"])

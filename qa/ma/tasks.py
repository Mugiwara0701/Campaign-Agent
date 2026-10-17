"""The Marketing Agent side of the QA run: which tests, how often, and what is sampled."""

import os
import time
from pathlib import Path
from typing import Any

from qa.lib.core import Ctx, http
from qa.lib.sched import Task
from qa.ma import email_tests, link_roundtrip, preflight, search_tests, slack_tests
from qa.ma.common import MA_DIR, load_ma_env, systemd_active


def env_files() -> list[Path]:
    return [Path(__file__).resolve().parents[1] / ".env.qa"]


def describe(ctx: Ctx) -> dict[str, Any]:
    return {
        "role": "Marketing Agent machine",
        "ma_dir": str(MA_DIR),
        "expected_recipient": ctx.getenv("QA_EXPECTED_RECIPIENT") or "(not set)",
    }


def setup(ctx: Ctx) -> None:
    load_ma_env(ctx)
    ctx.log(
        "the Marketing Agent's settings are loaded; warming the model (the first call after a boot can take minutes)"
    )
    preflight.warm_model(ctx)


def teardown(ctx: Ctx) -> None:
    try:
        email_tests.email_cleanup(ctx)
    except Exception as exc:
        ctx.log(f"clean-up problem: {type(exc).__name__}: {exc}")


def sample(ctx: Ctx) -> dict[str, Any]:
    """Runs on the sampler thread, with its own database connection."""
    from qa.ma.common import Pg

    if "sampler_pg" not in ctx.data:
        ctx.data["sampler_pg"] = Pg()
    t0 = time.monotonic()
    row = (
        ctx.data["sampler_pg"].one(
            "select desired_state, actual_state, current_step, extract(epoch from now() - heartbeat_at) as hb_age, "
            "(select count(*) from public.approvals where status = 'pending') as approvals_open, "
            "(select count(*) from public.emails where status = 'sent' and sent_at > now() - interval '24 hours') as sent_24h from public.pipeline_control where id = 1"
        )
        or {}
    )
    out = {
        f"ma_{k}" if k in ("desired_state", "actual_state", "current_step") else k: v
        for k, v in row.items()
    }
    out["db_ms"] = round((time.monotonic() - t0) * 1000)
    if out.get("hb_age") is not None:
        out["hb_age"] = round(float(out["hb_age"]), 1)
    out["service"] = systemd_active("marketing-agent")
    out["load1"] = round(os.getloadavg()[0], 2)
    meminfo = {
        k: int(v.split()[0])
        for k, v in (line.split(":") for line in open("/proc/meminfo").read().splitlines())
    }
    out["mem_available_mb"] = meminfo["MemAvailable"] // 1024
    ps = http(
        "GET",
        ctx.getenv("LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/ps",
        timeout=5,
    )
    out["model_loaded"] = (
        [m["name"] for m in ps.json().get("models", [])] if ps.status == 200 else None
    )
    chrome = os.popen("pgrep -c -f 'chrome' 2>/dev/null").read().strip()
    out["chrome_processes"] = int(chrome or 0)
    return out


def build_tasks(quick: bool) -> list[Task]:
    def every(minutes: float) -> float | None:
        return None if quick else minutes * 60

    return [
        Task(
            "ma.preflight",
            preflight.preflight,
            every(30),
            0,
            8 * 60,
            needs="runs `agent check` (the model, database, Slack, search)",
        ),
        Task(
            "ma.slack.messages",
            slack_tests.slack_messages,
            every(30),
            180,
            4 * 60,
            needs="posts test messages to the alerts channel and deletes them",
        ),
        Task(
            "ma.slack.alert",
            slack_tests.slack_alert,
            every(40),
            240,
            2 * 60,
            needs="one alert through the incoming webhook",
        ),
        Task(
            "ma.link",
            link_roundtrip.emit_events,
            every(20),
            120,
            4 * 60,
            needs="sends test events to the Campaign Agent",
        ),
        Task(
            "ma.search.queries",
            search_tests.search_queries,
            every(45),
            300,
            14 * 60,
            needs="the model writes search queries",
        ),
        Task(
            "ma.email.send",
            email_tests.email_approval_and_send,
            every(50),
            600,
            25 * 60,
            needs="a made-up lead, one e-mail to the TEST recipient only",
        ),
        Task(
            "ma.email.reject",
            email_tests.email_reject,
            every(80),
            2400,
            20 * 60,
            needs="a made-up lead that is rejected",
        ),
        Task(
            "ma.search.discovery",
            search_tests.discovery_real,
            every(100),
            4200,
            35 * 60,
            needs="a real visible-Chrome search; companies and drafts are saved, drafts go to Slack",
        ),
        Task(
            "ma.search.log_audit",
            search_tests.log_audit,
            every(15),
            900,
            3 * 60,
            needs="reads the service log",
        ),
        Task(
            "ma.inbox", search_tests.inbox_poll, every(30), 600, 8 * 60, needs="polls Gmail (read)"
        ),
        Task(
            "ma.email.cleanup",
            email_tests.email_cleanup,
            every(120),
            3000,
            4 * 60,
            needs="removes the made-up leads",
        ),
    ]

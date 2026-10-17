"""The Campaign Agent side of the QA run: which tests, how often, and what is sampled."""

import os
import time
from pathlib import Path
from typing import Any

from campaign_agent import console_flow
from qa.ca import control, gateway, health, lifecycle, link
from qa.ca.common import (
    QA_PREFIX,
    REPO,
    admin_db,
    delete_campaigns,
    live_now,
    pipeline,
    settings,
    systemd_active,
)  # fmt: skip
from qa.lib.core import Ctx, http
from qa.lib.sched import Task


def env_files() -> list[Path]:
    return [REPO / "qa" / ".env.qa"]  # the gateway token and address, if the operator gives them


def describe(ctx: Ctx) -> dict[str, Any]:
    s = settings(ctx)
    return {
        "role": "Campaign Agent machine",
        "model": s.llm_model,
        "link_enabled": s.ma_link_enabled,
        "gateway_tests": "on" if ctx.getenv("QA_GATEWAY_TOKEN") else "off (no QA_GATEWAY_TOKEN)",
        "gateway_url": ctx.getenv("QA_GATEWAY_URL", "https://campaign-agent-maha.onrender.com"),
    }


def setup(ctx: Ctx) -> None:
    ctx.data["live_before"] = live_now(ctx)
    ctx.data["pipeline_before"] = pipeline(ctx)
    with admin_db(ctx).transaction() as conn:  # leftovers of an earlier run that was killed
        cur = conn.execute(
            "delete from public.campaign where campaign_name like %s", (QA_PREFIX + "%",)
        )
        if cur.rowcount:
            ctx.log(f"removed {cur.rowcount} test campaign(s) left over from an earlier run")
    ctx.log(
        f"before the tests: live={ctx.data['live_before']} pipeline={ctx.data['pipeline_before']}"
    )


def teardown(ctx: Ctx) -> None:
    try:
        delete_campaigns(ctx)
        with admin_db(ctx).transaction() as conn:
            conn.execute("delete from public.ma_event where code like 'MA_QA_%%'")
        lifecycle.restore_live(ctx)
        before = ctx.data.get("pipeline_before") or {}
        now = pipeline(ctx) or {}
        if before.get("desired_state") == "running" and now.get("desired_state") != "running":
            console_flow.pipeline(admin_db(ctx), "running")
            ctx.log("the Marketing Agent was running before the tests: started again")
        elif before.get("desired_state") == "stopped" and now.get("desired_state") == "running":
            console_flow.pipeline(admin_db(ctx), "stopped")
            ctx.log("the Marketing Agent was stopped before the tests: stopped again")
    except Exception as exc:
        ctx.log(f"clean-up problem: {type(exc).__name__}: {exc}")


_SAMPLE_SQL = (
    "select (select agent_status from public.ca_heartbeat) as hb_status, "
    "(select extract(epoch from now() - last_seen) from public.ca_heartbeat) as hb_age, "
    "(select actual_state from public.ca_control) as ca_state, "
    "(select desired_state from public.pipeline_control where id = 1) as ma_desired, "
    "(select actual_state from public.pipeline_control where id = 1) as ma_actual, "
    "(select current_step from public.pipeline_control where id = 1) as ma_step, "
    "(select extract(epoch from now() - heartbeat_at) from public.pipeline_control where id = 1) as ma_hb_age, "
    "(select count(*) from public.campaign_request where status in ('pending', 'processing')) as queue, "
    "(select count(*) from public.ma_event where kind = 'blocker' and resolved_at is null) as open_blockers"
)


def sample(ctx: Ctx) -> dict[str, Any]:
    """Runs on the sampler thread: its own database connection (one connection is not shared between threads)."""
    from campaign_agent.store.db import Database

    if "sampler_db" not in ctx.data:
        s = settings(ctx)
        url = s.migrate_database_url or s.database_url
        ctx.data["sampler_db"] = Database(url.get_secret_value())  # type: ignore[union-attr]
    t0 = time.monotonic()
    with ctx.data["sampler_db"].transaction() as conn:
        row = dict(conn.execute(_SAMPLE_SQL).fetchone())
    row["db_ms"] = round((time.monotonic() - t0) * 1000)
    for key in ("hb_age", "ma_hb_age"):
        if row.get(key) is not None:
            row[key] = round(float(row[key]), 1)
    row["service"] = systemd_active("campaign-agent")
    load = os.getloadavg()
    row["load1"] = round(load[0], 2)
    meminfo = {
        k: int(v.split()[0])
        for k, v in (line.split(":") for line in open("/proc/meminfo").read().splitlines())
    }
    row["mem_available_mb"] = meminfo["MemAvailable"] // 1024
    ps = http("GET", settings(ctx).llm_base_url.rstrip("/") + "/api/ps", timeout=5)
    row["model_loaded"] = (
        [m["name"] for m in ps.json().get("models", [])] if ps.status == 200 else None
    )
    return row


def build_tasks(quick: bool) -> list[Task]:
    def every(minutes: float) -> float | None:
        return None if quick else minutes * 60

    def health_all(ctx: Ctx) -> None:
        health.service_health(ctx)
        health.resources(ctx)
        health.surfaces(ctx)

    return [
        Task(
            "ca.health",
            health_all,
            every(10),
            0,
            4 * 60,
            needs="reads the database, the link, the window",
        ),
        Task(
            "ca.link",
            lambda c: (link.link_contract(c), link.link_cleanup(c)),
            every(40),
            120,
            6 * 60,
            needs="sends test events to the link",
        ),
        Task(
            "ca.link.reconcile",
            link.reconcile_ma_events,
            every(20),
            900,
            2 * 60,
            needs="counts what the Marketing Agent sent",
        ),
        Task(
            "ca.gateway.contract",
            gateway.gateway_contract,
            every(60),
            300,
            8 * 60,
            needs="the gateway on Render (needs QA_GATEWAY_TOKEN)",
        ),
        Task(
            "ca.gateway.load",
            gateway.gateway_load,
            every(45),
            600,
            6 * 60,
            needs="the gateway on Render (needs QA_GATEWAY_TOKEN)",
        ),
        Task(
            "ca.gateway.pause_resume",
            gateway.gateway_pause_resume,
            every(90),
            1500,
            8 * 60,
            needs="pauses the Campaign Agent for a minute or two",
        ),
        Task(
            "ca.bad_inputs",
            lifecycle.bad_inputs,
            every(60),
            900,
            12 * 60,
            needs="test campaigns that must be refused",
        ),
        Task(
            "ca.auto_start_rules",
            lifecycle.auto_start_rules,
            every(75),
            1200,
            4 * 60,
            needs="test campaigns",
        ),
        Task(
            "ca.lifecycle",
            lifecycle.campaign_lifecycle,
            every(50),
            60,
            30 * 60,
            needs="uses the model for several minutes; makes a test campaign live",
        ),
        Task(
            "ca.pause_resume",
            control.pause_resume_ca,
            every(80),
            2400,
            12 * 60,
            needs="pauses the Campaign Agent; stops and restarts the Marketing Agent if it runs",
        ),
        Task(
            "ca.e2e_real_pass",
            control.e2e_real_pass,
            every(100),
            600,
            45 * 60,
            needs="a real pass of the Marketing Agent: Chrome, searches, drafts, Slack",
        ),
    ]

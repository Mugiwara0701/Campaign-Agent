"""Searching: the strategy that writes the queries, the search engine, and a bounded real discovery run in a throw-away store."""

import re
import subprocess
import time

from qa.lib.core import Ctx, Skip, expect
from qa.ma.common import pg, run_agent

T = "ma.search"


def search_queries(ctx: Ctx) -> None:
    with ctx.case(T, "the strategy writes search queries for the next pass", timeout_s=900) as info:
        t0 = time.time()
        done = run_agent(ctx, "leads", "queries", "--count", "6", timeout=840)
        expect(
            done.returncode == 0, f"exit {done.returncode}: {(done.stdout + done.stderr)[-250:]}"
        )
        lines = [ln for ln in done.stdout.splitlines() if re.match(r"\[\w+\] \S", ln)]
        info.update(seconds=round(time.time() - t0), lines=len(lines), sample=lines[:2])
        expect(len(lines) >= 3, f"only {len(lines)} output lines: {done.stdout[-200:]}")
        expect("Traceback" not in done.stdout + done.stderr, "a traceback was printed")


def discovery_real(ctx: Ctx) -> None:
    """The real thing, as in the demo: the pipeline is started like the dashboard's Start button does, so the service
    opens the visible Chrome, searches, judges companies and drafts to Slack; then it is stopped again.
    Mail still goes to the test recipient only (the Marketing Agent's own safety switch, checked by ma.preflight)."""
    minutes = 6 if ctx.quick else 15
    state_sql = (
        "select desired_state, actual_state, current_step from public.pipeline_control where id = 1"
    )
    state = pg(ctx).one(state_sql)
    if state is None:
        raise Skip("the pipeline switch cannot be read")
    if "running" in (state["desired_state"], state["actual_state"]):
        raise Skip(
            "the Marketing Agent is already running a pass (the Campaign Agent's end to end test)"
        )
    count_sql = "select (select count(*) from public.companies) as companies, (select count(*) from public.emails) as drafts"
    with ctx.case(
        T,
        f"a real pass of {minutes} minutes in the visible Chrome finds and judges companies",
        timeout_s=(minutes + 20) * 60,
    ) as info:
        t0 = time.time()
        before = pg(ctx).one(count_sql) or {}
        steps: list[str] = []
        try:
            started = run_agent(ctx, "pipeline", "start", timeout=120)
            expect(
                started.returncode == 0,
                f"pipeline start: {(started.stdout + started.stderr)[-200:]}",
            )
            up = ctx.poll(
                lambda: (r := pg(ctx).one(state_sql)) and r["actual_state"] == "running", 300, 5.0
            )
            expect(up, f"the pipeline did not start: {pg(ctx).one(state_sql)}")
            end = time.time() + minutes * 60
            while time.time() < end and not ctx.stopping.is_set():
                step = (pg(ctx).one(state_sql) or {}).get("current_step")
                if step and (not steps or steps[-1] != step):
                    steps.append(step)
                time.sleep(20)
        finally:
            run_agent(ctx, "pipeline", "stop", timeout=120)
            ctx.poll(
                lambda: (r := pg(ctx).one(state_sql)) and r["actual_state"] == "stopped", 300, 5.0
            )
        after = pg(ctx).one(count_sql) or {}
        failed = pg(ctx).one(
            "select count(*) as n from public.agent_runs where status = 'failed' and started_at > to_timestamp($1)",
            t0,
        )
        info.update(
            seconds=round(time.time() - t0),
            steps=steps,
            new_companies=after.get("companies", 0) - before.get("companies", 0),
            new_drafts=after.get("drafts", 0) - before.get("drafts", 0),
            failed_steps=(failed or {}).get("n", 0),
        )
        expect("leads" in steps, f"the pass never reached the leads step (saw {steps})")
        expect(not (failed or {}).get("n"), f"{(failed or {}).get('n')} step(s) of the pass failed")
        if info["new_companies"] == 0:
            info["warn"] = "the search found no new company in this time"


def log_audit(ctx: Ctx) -> None:
    """What the running service wrote since the last audit: errors, searches, leads, Chrome trouble, model trouble."""
    since = ctx.data.get("audit_since", time.time() - 3600)
    ctx.data["audit_since"] = time.time()
    with ctx.case(
        T, "the service log since the last look has no repeated failure", timeout_s=120
    ) as info:
        done = subprocess.run(
            [
                "journalctl",
                "--user",
                "-u",
                "marketing-agent",
                "--since",
                f"@{int(since)}",
                "--no-pager",
                "-o",
                "cat",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        text = done.stdout
        counts = {
            "searches": text.count('"Search executed"'),
            "results_found": text.count('"Result discovered"'),
            "leads_qualified": text.count('"Lead qualified"'),
            "errors": text.count('"level": "ERROR"'),
            "warnings": text.count('"level": "WARNING"'),
            "chrome_no_window": text.count("Chrome has no window"),
            "llm_invalid_output": text.count("invalid structured output"),
            "gmail_auth": text.count("GmailAuthError"),
            "tracebacks": text.count("Traceback"),
        }
        info.update(counts)
        expect(
            counts["chrome_no_window"] <= 2,
            f"Chrome lost its window {counts['chrome_no_window']} times",
        )
        expect(counts["gmail_auth"] == 0, "the Gmail login failed (the token expired?)")
        expect(counts["errors"] <= 15, f"{counts['errors']} errors in the service log")
        if counts["llm_invalid_output"] > 5:
            info["warn"] = (
                f"the model gave invalid structured output {counts['llm_invalid_output']} times"
            )


def inbox_poll(ctx: Ctx) -> None:
    with ctx.case(T, "the Gmail inbox is polled without error", timeout_s=400) as info:
        t0 = time.time()
        done = run_agent(ctx, "inbox", timeout=360)
        info.update(seconds=round(time.time() - t0), exit=done.returncode)
        text = done.stdout + done.stderr
        expect(
            "GmailAuthError" not in text and "Gmail token" not in text,
            "the Gmail login is not valid",
        )
        expect(done.returncode == 0, f"exit {done.returncode}: {text[-250:]}")

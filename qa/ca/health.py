"""Is the Campaign Agent's machine healthy: the service, the database, its roles, the model, the link, the window."""

import shutil

from campaign_agent.ma_link.server import tailscale_address
from qa.ca.common import (
    REPO,
    admin_db,
    agent_db,
    link_base,
    link_headers,
    one,
    q,
    settings,
    systemd_active,
)
from qa.lib.core import Ctx, Skip, expect, http

T = "ca.health"


def service_health(ctx: Ctx) -> None:
    with ctx.case(T, "the heartbeat is fresh", timeout_s=60) as info:
        row = one(
            admin_db(ctx),
            "select agent_status, extract(epoch from now() - last_seen) as age from public.ca_heartbeat",
        )
        expect(row is not None, "there is no heartbeat row")
        info.update(status=row["agent_status"], age_s=round(float(row["age"]), 1))  # type: ignore[index]
        expect(
            float(row["age"]) < 45,
            f"the heartbeat is {float(row['age']):.0f}s old: the Campaign Agent is not beating",
        )  # type: ignore[index]
    with ctx.case(T, "the systemd service is active", timeout_s=30):
        state = systemd_active("campaign-agent")
        expect(state == "active", f"campaign-agent is {state!r}")
    with ctx.case(T, "pause control is in sync", timeout_s=60) as info:
        row = one(
            admin_db(ctx),
            "select desired_state, actual_state, extract(epoch from now() - updated_at) as age from public.ca_control",
        )
        expect(row is not None, "no ca_control row")
        info.update(desired=row["desired_state"], actual=row["actual_state"])  # type: ignore[index]
        expect(
            row["desired_state"] == row["actual_state"] or float(row["age"]) < 90,
            "desired and actual state differ for over 90 s",
        )  # type: ignore[index]
    with ctx.case(T, "no abandoned transaction holds a lock", timeout_s=60) as info:
        rows = q(
            admin_db(ctx),
            "select pid, extract(epoch from now() - state_change)::int as s from pg_stat_activity where usename like 'campaign_%%' and state = 'idle in transaction' and now() - state_change > interval '75 seconds'",
        )
        info["stuck"] = len(rows)
        expect(
            not rows,
            f"{len(rows)} session(s) idle in transaction for over 75 s (longest {max(r['s'] for r in rows) if rows else 0}s)",
        )
    with ctx.case(T, "the roles end stuck transactions and lock waits", timeout_s=60):
        for role in ("campaign_agent", "campaign_dashboard"):
            row = one(admin_db(ctx), "select rolconfig from pg_roles where rolname = %s", role)
            config = (row or {}).get("rolconfig") or []
            expect(
                "idle_in_transaction_session_timeout=60s" in config
                and "lock_timeout=30s" in config,
                f"{role} has {config}",
            )
    with ctx.case(T, "every migration file is applied", timeout_s=60) as info:
        files = sorted(p.name for p in (REPO / "migrations").glob("*.sql"))
        done = {r["name"] for r in q(admin_db(ctx), "select name from public.schema_migrations")}
        missing = [f for f in files if f not in done]
        info["files"] = len(files)
        expect(not missing, f"not applied: {missing}")
    with ctx.case(T, "the Campaign Agent's own login works and sees the queue", timeout_s=60):
        row = one(agent_db(ctx), "select count(*) as n from public.campaign_request")
        expect(row is not None, "the limited role cannot read the request table")


def resources(ctx: Ctx) -> None:
    with ctx.case(T, "disk and memory are not running out", timeout_s=30) as info:
        free_gb = shutil.disk_usage("/").free / 1e9
        meminfo = {
            k: int(v.split()[0])
            for k, v in (line.split(":") for line in open("/proc/meminfo").read().splitlines())
        }
        avail_gb = meminfo["MemAvailable"] / 1e6
        info.update(disk_free_gb=round(free_gb, 1), mem_available_gb=round(avail_gb, 1))
        expect(free_gb > 2, f"only {free_gb:.1f} GB of disk left")
        if avail_gb < 1.5:
            info["warn"] = f"only {avail_gb:.1f} GB of memory available"
    with ctx.case(T, "the model server answers and has the model", timeout_s=60) as info:
        s = settings(ctx)
        r = http("GET", s.llm_base_url.rstrip("/") + "/api/tags", timeout=15)
        expect(r.status == 200, f"model server answered {r.status}: {r.text[:100]}")
        names = [m["name"] for m in r.json().get("models", [])]
        info["models"] = names
        expect(
            any(n.split(":")[0].lower() == s.llm_model.split(":")[0].lower() for n in names),
            f"{s.llm_model} is not among {names}",
        )


def surfaces(ctx: Ctx) -> None:
    with ctx.case(
        T,
        "the link listener refuses a caller without the token and answers one with it",
        timeout_s=60,
    ) as info:
        base = link_base(ctx)
        denied = http("GET", base + "/health", timeout=15)
        expect(denied.status == 401, f"no token: answered {denied.status}, wanted 401")
        ok = http("GET", base + "/health", headers=link_headers(ctx), timeout=15)
        expect(
            ok.status == 200 and ok.json().get("status") == "ok",
            f"with the token: {ok.status} {ok.text[:80]}",
        )
        info["latency_ms"] = round(ok.elapsed * 1000)
    with ctx.case(T, "the link is bound to Tailscale and not to every address", timeout_s=30):
        address = tailscale_address()
        expect(address is not None, "Tailscale has no address")
        wide = http("GET", f"http://127.0.0.1:{settings(ctx).ma_link_port}/ma/v1/health", timeout=5)
        expect(
            wide.status == 0,
            f"the link also answers on 127.0.0.1 (status {wide.status}): it listens on more than Tailscale",
        )
    with ctx.case(T, "the office window server answers", timeout_s=90) as info:
        r = http("GET", "http://127.0.0.1:8765/", timeout=15)
        if r.status == 0:
            raise Skip("the window is not running on this machine")
        expect(r.status == 200, f"window page answered {r.status}")
        state = http("GET", "http://127.0.0.1:8765/api/state", timeout=60)
        expect(state.status == 200, f"/api/state answered {state.status}")
        body = state.json()
        expect(
            {"lines", "requests", "events", "jobs"} <= set(body),
            f"/api/state misses keys: {sorted(body)}",
        )
        info["state_seconds"] = round(state.elapsed, 1)
        if state.elapsed > 20:
            info["warn"] = f"/api/state took {state.elapsed:.0f}s"
    with ctx.case(T, "the window refuses a request from another host", timeout_s=30):
        r = http(
            "GET", "http://127.0.0.1:8765/api/state", headers={"Host": "evil.example"}, timeout=15
        )
        if r.status == 0:
            raise Skip("the window is not running on this machine")
        expect(r.status == 403, f"a foreign Host header got {r.status}, wanted 403")

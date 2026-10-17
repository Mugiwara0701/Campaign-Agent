"""Is the Marketing Agent's machine ready: model, database, Slack, search, e-mail safety, link to the Campaign Agent, desktop."""

import re
import shutil
import subprocess
import time

from qa.lib.core import Ctx, Skip, expect, http, percentile
from qa.ma.common import MA_DIR, pg, run_agent, systemd_active, test_recipient

T = "ma.preflight"


def warm_model(ctx: Ctx) -> None:
    """Called once at the start: the first call after a reboot loads the model and can fail. How long it takes is a result."""
    t0 = time.time()
    first_ok = False
    attempts = 0
    for attempts in range(1, 4):
        done = run_agent(ctx, "check", timeout=240)
        first_ok = bool(re.search(r"\[OK\s*\]\s*LLM structured reply", done.stdout))
        if first_ok:
            break
        time.sleep(15)
    ctx.note(
        T,
        "model warm-up",
        "pass" if first_ok else "fail",
        f"{attempts} attempt(s), {time.time() - t0:.0f}s",
        attempts=attempts,
        seconds=round(time.time() - t0),
        ok=first_ok,
    )


def preflight(ctx: Ctx) -> None:
    with ctx.case(T, "the model answers a structured request", timeout_s=900) as info:
        t0 = time.time()
        results = []
        for attempt in range(1, 4):
            done = run_agent(ctx, "check", timeout=240)
            ok = bool(re.search(r"\[OK\s*\]\s*LLM structured reply", done.stdout))
            results.append(ok)
            if ok:
                break
            time.sleep(15)
        info.update(attempts=len(results), seconds=round(time.time() - t0), first_try=results[0])
        expect(
            any(results),
            "the model never gave a structured reply in 3 tries: drafting e-mails and judging leads would fail",
        )
        if not results[0]:
            info["warn"] = (
                "the first try failed and a later one worked: a cold model takes minutes to load"
            )
    with ctx.case(
        T, "the other preflight checks pass (database, Slack, search, e-mail switch)", timeout_s=300
    ) as info:
        done = run_agent(ctx, "check", timeout=240)
        lines = [ln for ln in done.stdout.splitlines() if ln.startswith("[")]
        info["lines"] = [re.sub(r"\s+", " ", ln)[:90] for ln in lines]
        bad = [ln for ln in lines if ln.startswith("[FAIL")]
        expect(lines, f"no preflight output: {done.stderr[-200:]}")
        expect(not bad, "; ".join(bad))
    with ctx.case(
        T, "every e-mail is redirected to the test recipient and the system says so", timeout_s=120
    ) as info:
        recipient = test_recipient(ctx)
        info["recipient"] = recipient.replace("@", " at ")
        wanted = ctx.getenv("QA_EXPECTED_RECIPIENT")
        expect(
            not wanted or recipient.lower() == wanted.lower(),
            f"TEST_RECIPIENT is {recipient}, expected {wanted}",
        )
        row = pg(ctx).one(
            "select test_mode, sending_enabled from public.pipeline_control where id = 1"
        )
        expect(row is not None, "no pipeline_control row")
        info.update(test_mode=row["test_mode"], sending_enabled=row["sending_enabled"])  # type: ignore[index]
        expect(
            row["test_mode"] is True,
            "the Marketing Agent does not report test mode: mail might go to real contacts",
        )  # type: ignore[index]
    with ctx.case(T, "the systemd service is active", timeout_s=30):
        state = systemd_active("marketing-agent")
        expect(state == "active", f"marketing-agent is {state!r}")
    with ctx.case(
        T,
        "the Campaign Agent's link answers from here, quickly, and refuses a wrong token",
        timeout_s=180,
    ) as info:
        url, token = ctx.getenv("MA_LINK_URL").rstrip("/"), ctx.getenv("MA_LINK_TOKEN")
        if not url or not token:
            raise Skip("MA_LINK_URL or MA_LINK_TOKEN is not set")
        times = []
        for _ in range(10):
            r = http(
                "GET", url + "/health", headers={"Authorization": f"Bearer {token}"}, timeout=15
            )
            expect(r.status == 200, f"the link answered {r.status}: {r.text[:80]}")
            times.append(r.elapsed * 1000)
        info.update(p50_ms=round(percentile(times, 50)), p95_ms=round(percentile(times, 95)))
        wrong = http(
            "GET", url + "/health", headers={"Authorization": "Bearer " + "x" * 40}, timeout=15
        )
        expect(wrong.status == 401, f"a wrong token got {wrong.status}")
    with ctx.case(T, "the desktop and Chrome are available for the visible search", timeout_s=60):
        expect(
            shutil.which("google-chrome") or shutil.which("chromium"), "no Chrome on this machine"
        )
        done = subprocess.run(
            ["xdpyinfo", "-display", ":0"], capture_output=True, text=True, timeout=20, check=False
        )
        expect(done.returncode == 0, "no desktop session on :0: Chrome cannot open visibly")
    with ctx.case(T, "the search engine answers with results", timeout_s=90) as info:
        base = ctx.getenv("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
        r = http(
            "GET",
            f"{base}/search?q=embedded+linux+yocto+engineering+services&format=json",
            timeout=60,
        )
        expect(r.status == 200, f"SearXNG answered {r.status}: {r.text[:100]}")
        results = r.json().get("results", [])
        info.update(results=len(results), seconds=round(r.elapsed, 1))
        expect(len(results) >= 3, f"only {len(results)} results")
    with ctx.case(T, "the Marketing Agent's folder is intact", timeout_s=30):
        expect((MA_DIR / "config" / "leadgen.yaml").is_file(), "config/leadgen.yaml is missing")

"""The dashboard gateway on Render, called the way the dashboard's server calls it (needs QA_GATEWAY_TOKEN)."""

import threading
import uuid

from qa.ca.common import (
    QA_USER,
    admin_db,
    delete_campaigns,
    gateway,
    make_campaign,
    one,
    wait_idle_worker,
)
from qa.lib.core import Ctx, expect, percentile

T = "ca.gateway"


def gateway_contract(ctx: Ctx) -> None:
    gw = gateway(ctx)
    with ctx.case(T, "health answers without a token", timeout_s=120) as info:
        r = gw.call("GET", "/health", token=None)
        expect(r.status == 200 and r.json() == {"status": "ok"}, f"{r.status} {r.text[:100]}")
        info["seconds"] = round(r.elapsed, 1)
    with ctx.case(T, "every other route refuses a missing or wrong token", timeout_s=120):
        for method, path in (
            ("GET", "/v1/ca"),
            ("GET", f"/v1/campaigns/{uuid.uuid4()}"),
            ("POST", "/v1/ca/state"),
        ):
            for token in (None, "wrong-token"):
                r = gw.call(method, path, token=token)
                expect(r.status == 401, f"{method} {path} token={token}: {r.status}")
    with ctx.case(T, "docs and the schema are not public", timeout_s=60):
        for path in ("/docs", "/openapi.json", "/redoc"):
            expect(gw.call("GET", path, token=None).status in (401, 404), f"{path} is reachable")
    with ctx.case(T, "the Campaign Agent's status comes through", timeout_s=120) as info:
        r = gw.call("GET", "/v1/ca")
        expect(r.status == 200, f"{r.status} {r.text[:100]}")
        body = r.json()
        info.update(online=body.get("online"), status=body.get("status"))
        expect(
            {"online", "status", "last_seen_s_ago", "control"} <= set(body), f"keys: {sorted(body)}"
        )
        expect(body["online"] is True, "the gateway says the Campaign Agent is offline")
    with ctx.case(T, "bad requests are refused before anything is written", timeout_s=120):
        cid = make_campaign(ctx, "gateway contract test campaign about EV chargers", "gw-contract")
        bad = [
            {"request_type": "start_the_marketing_agent", "requested_by": QA_USER},
            {"request_type": "interpret"},
            {"request_type": "interpret", "requested_by": "   "},
            {"request_type": "interpret", "requested_by": "x" * 101},
            {"request_type": "interpret", "requested_by": QA_USER, "surprise": 1},
            {"request_type": "edit", "requested_by": QA_USER, "base_revision": -1},
        ]
        for body in bad:
            r = gw.call("POST", f"/v1/campaigns/{cid}/requests", body)
            expect(r.status == 422, f"{body} answered {r.status}, wanted 422")
        expect(
            gw.call("GET", "/v1/campaigns/not-a-uuid").status == 422,
            "a campaign id that is not a UUID was accepted",
        )
        expect(
            gw.call("GET", f"/v1/campaigns/{uuid.uuid4()}").status == 404,
            "an unknown campaign is not a 404",
        )
        row = one(
            admin_db(ctx),
            "select count(*) as n from public.campaign_request where campaign_id = %s",
            cid,
        )
        expect(row and row["n"] == 0, "a refused request was written to the database")
    with ctx.case(
        T, "the same key is the same request, and the request is worked on", timeout_s=240
    ) as info:
        cid = make_campaign(ctx, "idempotency check campaign", "gw-idem")
        body = {
            "request_type": "discard",
            "requested_by": QA_USER,
            "idempotency_key": f"qa-{ctx.run_id}-discard",
        }
        first = gw.call("POST", f"/v1/campaigns/{cid}/requests", body)
        again = gw.call("POST", f"/v1/campaigns/{cid}/requests", body)
        expect(first.status == 202 and again.status == 202, f"{first.status}/{again.status}")
        expect(
            again.json()["duplicate"] is True
            and again.json()["request_id"] == first.json()["request_id"],
            f"the repeat made a new request: {again.text}",
        )
        rid = first.json()["request_id"]
        done = ctx.poll(
            lambda: (
                (r := gw.call("GET", f"/v1/requests/{rid}")).status == 200
                and r.json()["status"] in ("done", "failed")
                and r.json()
            ),
            150,
            4.0,
        )
        expect(done, "the request was not worked on within 150 s")
        info["request_seconds"] = done["finished_at"] and "ok"
        expect(
            done["status"] == "done",
            f"the request ended {done['status']}: {done.get('error_code')}",
        )
        view = gw.call("GET", f"/v1/campaigns/{cid}").json()
        expect(view["state"] == "DISCARDED", f"the campaign is {view['state']}, wanted DISCARDED")
    delete_campaigns(ctx)


def gateway_load(ctx: Ctx) -> None:
    gw = gateway(ctx)
    with ctx.case(T, "20 sequential calls are quick and steady", timeout_s=300) as info:
        times = [gw.call("GET", "/v1/ca").elapsed * 1000 for _ in range(20)]
        info.update(
            p50_ms=round(percentile(times, 50)),
            p95_ms=round(percentile(times, 95)),
            max_ms=round(max(times)),
        )
        expect(percentile(times, 95) < 8000, f"p95 {percentile(times, 95):.0f} ms")
    with ctx.case(T, "15 parallel calls all succeed", timeout_s=300) as info:
        results: list[int] = []
        lock = threading.Lock()

        def one_call() -> None:
            status = gw.call("GET", "/v1/ca").status
            with lock:
                results.append(status)

        threads = [threading.Thread(target=one_call) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        info["statuses"] = sorted(set(results))
        expect(results.count(200) == 15, f"statuses {sorted(results)}")


def gateway_pause_resume(ctx: Ctx) -> None:
    gw = gateway(ctx)
    with ctx.case(
        T,
        "pausing through the gateway pauses the Campaign Agent, resuming brings it back",
        timeout_s=420,
        fatal=True,
    ) as info:
        expect(wait_idle_worker(ctx), "the Campaign Agent is not up")
        r = gw.call("POST", "/v1/ca/state", {"state": "paused", "requested_by": QA_USER})
        expect(r.status == 200, f"pause answered {r.status} {r.text[:100]}")
        paused = ctx.poll(
            lambda: (s := gw.call("GET", "/v1/ca").json()) and s["status"] == "paused" and s,
            150,
            5.0,
        )
        expect(paused, "the Campaign Agent did not report paused within 150 s")
        info["paused_status"] = paused["status"]
        r = gw.call("POST", "/v1/ca/state", {"state": "running", "requested_by": QA_USER})
        expect(r.status == 200, f"resume answered {r.status}")
        back = ctx.poll(
            lambda: (
                (s := gw.call("GET", "/v1/ca").json()) and s["status"] in ("idle", "busy") and s
            ),
            150,
            5.0,
        )
        expect(back, "the Campaign Agent did not come back within 150 s")

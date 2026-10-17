"""Controlling the Marketing Agent: pause and resume of the Campaign Agent, and a real pass of the Marketing Agent on a campaign."""

import time

from campaign_agent import console_flow
from qa.ca.common import (
    admin_db,
    delete_campaigns,
    make_campaign,
    one,
    pipeline,
    q,
    send,
    wait_idle_worker,
    wait_request,
    wait_state,
)
from qa.ca.lifecycle import MODEL_WAIT_S, READY, TEXTS, _answers, restore_live
from qa.lib.core import Ctx, Skip, expect

T_PAUSE = "ca.pause_resume"
T_E2E = "ca.e2e_real_pass"


def pause_resume_ca(ctx: Ctx) -> None:
    expect(wait_idle_worker(ctx), "the Campaign Agent is not up or is paused")
    prior = pipeline(ctx) or {}
    was_running = prior.get("desired_state") == "running"
    ctx.note(
        T_PAUSE,
        "the Marketing Agent before the test",
        "pass",
        "",
        desired=prior.get("desired_state"),
        actual=prior.get("actual_state"),
    )
    cid = make_campaign(ctx, "pause test campaign", "pause")
    rid = ""
    with ctx.case(
        T_PAUSE, "pausing is acknowledged by the Campaign Agent", timeout_s=300, fatal=True
    ) as info:
        console_flow.control_request(admin_db(ctx), "paused")
        t0 = time.time()
        ok = ctx.poll(
            lambda: (
                (r := one(admin_db(ctx), "select actual_state from public.ca_control"))
                and r["actual_state"] == "paused"
            ),
            150,
            4.0,
        )
        expect(ok, "ca_control never showed paused")
        beat = ctx.poll(
            lambda: (
                (r := one(admin_db(ctx), "select agent_status from public.ca_heartbeat"))
                and r["agent_status"] == "paused"
            ),
            90,
            4.0,
        )
        expect(beat, "the heartbeat never said paused")
        info["seconds"] = round(time.time() - t0)
    with ctx.case(
        T_PAUSE, "a request sent while paused waits, and is not lost", timeout_s=120
    ) as info:
        rid = send(ctx, cid, "discard")
        time.sleep(30)
        row = one(admin_db(ctx), "select status from public.campaign_request where id = %s", rid)
        info["status_after_30s"] = row["status"]  # type: ignore[index]
        expect(
            row["status"] == "pending",
            f"the request is {row['status']} while the Campaign Agent is paused",
        )  # type: ignore[index]
    if was_running:
        with ctx.case(
            T_PAUSE, "pausing the Campaign Agent stops the Marketing Agent", timeout_s=420
        ) as info:
            t0 = time.time()
            stopped = ctx.poll(
                lambda: (
                    (p := pipeline(ctx))
                    and p["desired_state"] == "stopped"
                    and p["actual_state"] == "stopped"
                ),
                300,
                5.0,
            )
            info["seconds"] = round(time.time() - t0)
            expect(stopped, f"the Marketing Agent is {pipeline(ctx)} after 5 min of pause")
            row = one(admin_db(ctx), "select ma_resume from public.ca_control")
            expect(
                row and row["ma_resume"],
                "the Campaign Agent did not remember that the Marketing Agent was running",
            )
    with ctx.case(T_PAUSE, "resuming runs the waiting request", timeout_s=420, fatal=True) as info:
        console_flow.control_request(admin_db(ctx), "running")
        t0 = time.time()
        done = wait_request(ctx, rid, 300)
        info["seconds"] = round(time.time() - t0)
        expect(
            done and done["status"] == "done",
            f"after resume the request is {done and done['status']}",
        )
    if was_running:
        with ctx.case(
            T_PAUSE,
            "resuming starts the Marketing Agent again, because it was running",
            timeout_s=420,
        ) as info:
            t0 = time.time()
            back = ctx.poll(
                lambda: (p := pipeline(ctx)) and p["desired_state"] == "running", 240, 5.0
            )
            info["seconds"] = round(time.time() - t0)
            expect(back, "the Marketing Agent was not started again after the resume")
            row = one(admin_db(ctx), "select ma_resume from public.ca_control")
            expect(row and not row["ma_resume"], "ma_resume was not cleared")
    else:
        with ctx.case(
            T_PAUSE, "resuming leaves a Marketing Agent that was stopped, stopped", timeout_s=120
        ):
            time.sleep(40)
            p = pipeline(ctx) or {}
            expect(
                p.get("desired_state") == "stopped",
                f"the Marketing Agent was started by the resume ({p})",
            )
    delete_campaigns(ctx)


def e2e_real_pass(ctx: Ctx) -> None:
    """A campaign made and approved, the Marketing Agent started on it, a real pass run for a while, then switched off."""
    expect(wait_idle_worker(ctx), "the Campaign Agent is not up or is paused")
    p0 = pipeline(ctx)
    if p0 is None:
        raise Skip("this role cannot read the Marketing Agent's pipeline switch")
    cid = ""
    t_start = time.time()
    n = ctx.data["e2e_runs"] = ctx.data.get("e2e_runs", 0) + 1
    with ctx.case(
        T_E2E, "the Marketing Agent's service is alive", timeout_s=60, fatal=True
    ) as info:
        beat = one(
            admin_db(ctx),
            "select extract(epoch from now() - heartbeat_at) as age from public.pipeline_control where id = 1",
        )
        info["heartbeat_age_s"] = (
            round(float(beat["age"])) if beat and beat["age"] is not None else None
        )  # type: ignore[index]
        expect(
            beat and beat["age"] is not None and float(beat["age"]) < 90,
            f"the Marketing Agent's heartbeat is {beat and beat['age']} s old",
        )

    with ctx.case(
        T_E2E, "a campaign reaches its review", timeout_s=3 * MODEL_WAIT_S, fatal=True
    ) as info:
        cid = make_campaign(ctx, TEXTS[(n - 1) % len(TEXTS)], f"e2e {n}", request=True)
        for _ in range(4):
            v = wait_state(ctx, cid, READY, MODEL_WAIT_S)
            expect(v, "no progress")
            if v["state"] == "CLARIFICATION_REQUIRED":  # type: ignore[index]
                console_flow.answer(admin_db(ctx), cid, _answers(v["questions"]))  # type: ignore[index]
                continue
            break
        info["state"] = v["state"]  # type: ignore[index]
        expect(v["state"] == "AWAITING_REVIEW", f"ended in {v['state']}")  # type: ignore[index]

    with ctx.case(
        T_E2E, "approving with 'start the Marketing Agent' starts it", timeout_s=900, fatal=True
    ) as info:
        console_flow.approve(admin_db(ctx), cid, start_pipeline=True)
        got = wait_state(ctx, cid, ("PUBLISHED",), 360)
        expect(got and got["live"], "the campaign did not go live")

        def _note() -> dict:
            last = one(
                admin_db(ctx),
                "select result from public.campaign_request where campaign_id = %s and request_type = 'approve' order by created_at desc limit 1",
                cid,
            )
            return ((last or {}).get("result") or {}).get("pipeline") or {}

        # The note is written after the publish is committed (a running pipeline is stopped and started first).
        note = ctx.poll(_note, 180, 3.0) or {}
        info["pipeline_note"] = note
        expect(
            note.get("asked") is True,
            f"the Campaign Agent did not ask for the Marketing Agent to start: {note}",
        )
        up = ctx.poll(
            lambda: (
                (p := pipeline(ctx))
                and p["desired_state"] == "running"
                and p["actual_state"] == "running"
            ),
            420,
            5.0,
        )
        expect(up, f"the Marketing Agent is not running: {pipeline(ctx)}")

    t_run = time.time()
    with ctx.case(
        T_E2E, "the Marketing Agent takes the campaign and starts a pass", timeout_s=900
    ) as info:
        applied = ctx.poll(
            lambda: one(
                admin_db(ctx),
                "select occurred_at from public.ma_event where campaign_id = %s and code = 'MA_CAMPAIGN_APPLIED' limit 1",
                cid,
            ),
            600,
            10.0,
        )
        expect(
            applied,
            "no MA_CAMPAIGN_APPLIED event within 10 min: the Marketing Agent never took the file",
        )
        info["seconds"] = round(time.time() - t_run)
        started = ctx.poll(
            lambda: one(
                admin_db(ctx),
                "select 1 as x from public.ma_event where code = 'MA_PASS_STARTED' and received_at > to_timestamp(%s)",
                t_start,
            ),
            300,
            10.0,
        )
        expect(started, "no MA_PASS_STARTED event")

    minutes = 5 if ctx.quick else 25
    steps: list[str] = []
    with ctx.case(
        T_E2E,
        f"the pass runs for {minutes} minutes without a blocker",
        timeout_s=(minutes + 8) * 60,
    ) as info:
        end = time.time() + minutes * 60
        while time.time() < end:
            p = pipeline(ctx) or {}
            if p.get("current_step") and (not steps or steps[-1] != p["current_step"]):
                steps.append(p["current_step"])
            if ctx.stopping.is_set():
                break
            time.sleep(20)
        blockers = q(
            admin_db(ctx),
            "select code, message, repeat_count from public.ma_event where kind = 'blocker' and resolved_at is null and received_at > to_timestamp(%s)",
            t_start,
        )
        info.update(
            steps=steps, open_blockers=[f"{b['code']} x{b['repeat_count']}" for b in blockers]
        )
        expect("leads" in steps, f"the pass never reached the leads step (saw {steps})")
        expect(
            not blockers,
            "open blocker(s): " + "; ".join(f"{b['code']}: {b['message'][:80]}" for b in blockers),
        )

    with ctx.case(
        T_E2E, "the search found companies and the pass left a record", timeout_s=120
    ) as info:
        found = one(
            admin_db(ctx),
            "select count(*) as n from public.companies where created_at > to_timestamp(%s)",
            t_start,
        )["n"]  # type: ignore[index]
        runs = q(
            admin_db(ctx),
            "select job, status, count(*) as n from public.agent_runs where started_at > to_timestamp(%s) group by 1, 2",
            t_start,
        )
        drafts = one(
            admin_db(ctx),
            "select count(*) as n from public.emails where created_at > to_timestamp(%s)",
            t_start,
        )["n"]  # type: ignore[index]
        info.update(
            companies_found=found,
            drafts=drafts,
            runs={f"{r['job']}:{r['status']}": r["n"] for r in runs},
        )
        failed = [r for r in runs if r["status"] == "failed"]
        expect(not failed, f"steps failed: {[(r['job'], r['n']) for r in failed]}")
        if found == 0:
            info["warn"] = "the search found no new company in this time"

    with ctx.case(
        T_E2E, "switching the campaign off stops the Marketing Agent", timeout_s=600, fatal=False
    ) as info:
        rid = send(ctx, cid, "deactivate")
        done = wait_request(ctx, rid, 240)
        expect(
            done and done["status"] == "done",
            f"deactivate: {done and (done['status'], done['error_code'])}",
        )
        info["pipeline"] = (done["result"] or {}).get("pipeline")  # type: ignore[index]
        t0 = time.time()
        stopped = ctx.poll(
            lambda: (
                (p := pipeline(ctx))
                and p["desired_state"] == "stopped"
                and p["actual_state"] == "stopped"
            ),
            300,
            5.0,
        )
        info["seconds"] = round(time.time() - t0)
        expect(stopped, f"the Marketing Agent is still {pipeline(ctx)}")

    with ctx.case(
        T_E2E, "the test campaign is removed and the earlier state is restored", timeout_s=300
    ):
        delete_campaigns(ctx)
        restore_live(ctx)

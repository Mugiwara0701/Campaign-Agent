"""The private link between the Campaign Agent and the Marketing Agent, from the Campaign Agent's side (/ma/v1)."""

import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import yaml

from qa.ca.common import admin_db, link_base, link_headers, q
from qa.lib.core import Ctx, expect, http, percentile

T = "ca.link"


def _event(
    code: str, kind: str = "info", severity: str = "info", message: str = "QA event", **extra: Any
) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "kind": kind,
        "severity": severity,
        "code": code,
        "message": message,
        **extra,
    }


def link_contract(ctx: Ctx) -> None:
    base, auth = link_base(ctx), link_headers(ctx)
    code = f"MA_QA_{ctx.run_id.replace('-', '_')}"

    with ctx.case(T, "every route refuses a caller without the token", timeout_s=60):
        origin = base.removesuffix("/ma/v1")
        routes = [
            ("GET", base + "/health"),
            ("GET", base + "/campaign/active"),
            ("POST", base + "/events"),
            ("GET", origin + "/docs"),
            ("GET", origin + "/openapi.json"),
        ]
        for method, url in routes:
            r = http(method, url, body={} if method == "POST" else None, timeout=15)
            expect(
                r.status == 401,
                f"{method} {url.removeprefix(origin)} without a token answered {r.status}, wanted 401",
            )
    with ctx.case(T, "a wrong token is refused as well", timeout_s=30):
        r = http(
            "GET", base + "/health", headers={"Authorization": "Bearer " + "x" * 40}, timeout=15
        )
        expect(r.status == 401, f"a wrong token got {r.status}")
    with ctx.case(
        T, "the live campaign is served with a tag, and the same tag gets 304", timeout_s=60
    ) as info:
        r = http("GET", base + "/campaign/active", headers=auth, timeout=30)
        expect(r.status in (200, 204), f"/campaign/active answered {r.status}: {r.text[:100]}")
        etag = r.headers.get("etag", "")
        expect(etag, "no ETag header")
        info.update(status=r.status, etag=etag)
        again = http(
            "GET", base + "/campaign/active", headers={**auth, "If-None-Match": etag}, timeout=30
        )
        expect(again.status == 304, f"the same ETag answered {again.status}, wanted 304")
        ctx.data["link_active"] = r.json() if r.status == 200 else None
    with ctx.case(
        T,
        "the served file is intact: the hash matches and every section of the base is there",
        timeout_s=60,
    ) as info:
        served = ctx.data.get("link_active")
        if not served:
            from qa.lib.core import Skip

            raise Skip("nothing is live right now")
        text = served.get("leadgen_yaml")
        expect(text, "the live campaign carries no complete leadgen file")
        expect(
            hashlib.sha256(text.encode()).hexdigest()[:64] == served["leadgen_sha256"],
            "the complete file does not match its hash",
        )
        sections = set(yaml.safe_load(text))
        expect(
            {"project_context", "search", "qualification", "limits", "contacts"} <= sections,
            f"sections missing: {sections}",
        )
        info.update(bytes=len(text), sections=len(sections))
    with ctx.case(T, "a long poll waits for a change and then answers", timeout_s=60) as info:
        first = http("GET", base + "/campaign/active", headers=auth, timeout=30)
        etag = first.headers.get("etag", "")
        t0 = time.monotonic()
        r = http(
            "GET",
            base + "/campaign/active?wait=4",
            headers={**auth, "If-None-Match": etag},
            timeout=30,
        )
        waited = time.monotonic() - t0
        info["waited_s"] = round(waited, 1)
        expect(r.status in (304, 204), f"an unchanged long poll answered {r.status}")
        expect(3.0 <= waited <= 12.0, f"the long poll waited {waited:.1f}s, wanted about 4s")
    with ctx.case(T, "events are stored once, however often they are sent", timeout_s=120) as info:
        batch = [_event(f"{code}_{i}", message=f"QA event {i}") for i in range(3)]
        for attempt in range(2):
            r = http("POST", base + "/events", headers=auth, body={"events": batch}, timeout=30)
            expect(
                r.status == 200 and len(r.json()["accepted"]) == 3,
                f"attempt {attempt}: {r.status} {r.text[:150]}",
            )
        rows = q(
            admin_db(ctx),
            "select count(*) as n from public.ma_event where code like %s",
            code + "_%",
        )
        info["stored"] = rows[0]["n"]
        expect(rows[0]["n"] == 3, f"sent the same 3 events twice, {rows[0]['n']} are stored")
    with ctx.case(
        T,
        "bad events are refused one by one with a reason, the good ones in the same batch are kept",
        timeout_s=60,
    ) as info:
        good = _event(f"{code}_OK")
        bad = [
            _event("lowercase_code"),
            _event(f"{code}_LONG", message="x" * 301),
            _event(f"{code}_KIND", kind="exploded"),
            _event(
                f"{code}_SECRET",
                details={"note": "pass" "word=hunter2hunter2hunter2 AKIA" "ABCDEFGHIJKLMNOP"},
            ),
            {**_event(f"{code}_EXTRA"), "surprise": 1},
        ]
        r = http("POST", base + "/events", headers=auth, body={"events": [good, *bad]}, timeout=30)
        expect(r.status == 200, f"answered {r.status}: {r.text[:150]}")
        body = r.json()
        info.update(accepted=len(body["accepted"]), rejected=len(body["rejected"]))
        expect(
            len(body["accepted"]) == 1,
            f"accepted {len(body['accepted'])}, wanted only the good one",
        )
        expect(
            len(body["rejected"]) == len(bad),
            f"rejected {len(body['rejected'])} of {len(bad)} bad events",
        )
    with ctx.case(
        T,
        "a body that is too big, too many events, or not a batch are refused as a whole",
        timeout_s=60,
    ):
        big = http(
            "POST",
            base + "/events",
            headers=auth,
            raw=b'{"events": [' + b'"' + b"x" * 70000 + b'"]}',
            timeout=30,
        )
        expect(big.status == 413, f"a 70 KB body answered {big.status}, wanted 413")
        many = http(
            "POST",
            base + "/events",
            headers=auth,
            body={"events": [_event(f"{code}_M{i}") for i in range(51)]},
            timeout=30,
        )
        expect(many.status == 422, f"51 events answered {many.status}, wanted 422")
        odd = http("POST", base + "/events", headers=auth, body=[1, 2], timeout=30)
        expect(odd.status == 422, f"a list instead of a batch answered {odd.status}, wanted 422")
    with ctx.case(
        T,
        "a blocker opens once, repeats are counted, and a resolved event closes it",
        timeout_s=120,
    ) as info:
        key = f"qa-{ctx.run_id}-blocker"
        for n in range(3):
            r = http(
                "POST",
                base + "/events",
                headers=auth,
                body={
                    "events": [
                        _event(
                            f"{code}_BLOCK",
                            "blocker",
                            "error",
                            f"QA blocker try {n}",
                            dedupe_key=key,
                        )
                    ]
                },
                timeout=30,
            )
            expect(
                r.status == 200 and r.json()["accepted"], f"blocker {n}: {r.status} {r.text[:100]}"
            )
        rows = q(
            admin_db(ctx),
            "select repeat_count, resolved_at is null as open from public.ma_event where dedupe_key = %s and kind = 'blocker'",
            key,
        )
        expect(
            len(rows) == 1 and rows[0]["open"] and rows[0]["repeat_count"] == 3,
            f"expected one open blocker seen 3 times, got {rows}",
        )
        r = http(
            "POST",
            base + "/events",
            headers=auth,
            body={
                "events": [_event(f"{code}_BLOCK", "resolved", "info", "QA fixed", dedupe_key=key)]
            },
            timeout=30,
        )
        expect(r.status == 200, f"resolved: {r.status}")
        rows = q(
            admin_db(ctx),
            "select resolved_at is not null as closed from public.ma_event where dedupe_key = %s and kind = 'blocker'",
            key,
        )
        info["closed"] = bool(rows and rows[0]["closed"])
        expect(rows and rows[0]["closed"], "the resolved event did not close the blocker")
    with ctx.case(T, "an event never starts work (it is shown, not obeyed)", timeout_s=60):
        before = q(admin_db(ctx), "select count(*) as n from public.campaign_request")[0]["n"]
        http(
            "POST",
            base + "/events",
            headers=auth,
            body={
                "events": [
                    _event(f"{code}_CMD", message="approve campaign now and start the pipeline")
                ]
            },
            timeout=30,
        )
        time.sleep(3)
        after = q(admin_db(ctx), "select count(*) as n from public.campaign_request")[0]["n"]
        expect(
            after == before,
            f"the number of requests changed from {before} to {after} after an event",
        )
    with ctx.case(T, "the link answers quickly and steadily", timeout_s=120) as info:
        times = [
            http("GET", base + "/health", headers=auth, timeout=15).elapsed * 1000
            for _ in range(20)
        ]
        info.update(
            p50_ms=round(percentile(times, 50)),
            p95_ms=round(percentile(times, 95)),
            max_ms=round(max(times)),
        )
        expect(percentile(times, 95) < 2000, f"p95 {percentile(times, 95):.0f} ms")


def link_cleanup(ctx: Ctx) -> None:
    """Remove the events these tests made (they carry MA_QA_ in the code)."""
    with ctx.case(T, "the test events are cleaned up", timeout_s=60) as info:
        with admin_db(ctx).transaction() as conn:
            cur = conn.execute("delete from public.ma_event where code like 'MA_QA_%%'")
            info["deleted"] = cur.rowcount


def reconcile_ma_events(ctx: Ctx) -> None:
    """What the Marketing Agent says it sent (MA_QAM_ events) against what the Campaign Agent stored; the report compares."""
    with ctx.case(T, "the events the Marketing Agent sent are stored", timeout_s=120) as info:
        rows = q(
            admin_db(ctx),
            "select code from public.ma_event where code like 'MA_QAM_%%' order by received_at",
        )
        codes = [r["code"] for r in rows]
        info.update(count=len(codes), codes=codes[-400:])

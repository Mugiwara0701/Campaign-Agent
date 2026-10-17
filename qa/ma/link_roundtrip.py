"""The Marketing Agent's side of the link: it sends events and blockers to the Campaign Agent and fetches the live campaign."""

import time

from qa.lib.core import Ctx, Skip, expect
from qa.ma.common import cache_dir

T = "ma.link"


def emit_events(ctx: Ctx) -> None:
    from agent import campaign_link

    cfg = campaign_link.link_config()
    if cfg is None:
        raise Skip("MA_LINK_URL / MA_LINK_TOKEN are not set")
    outbox = campaign_link.Outbox(cache_dir() / "outbox.jsonl")
    n = ctx.data["emit_round"] = ctx.data.get("emit_round", 0) + 1
    prefix = f"MA_QAM_{ctx.run_id.replace('-', '_')}_{n}"
    emitted: list[str] = []
    with ctx.case(
        T, "events and a blocker are queued and delivered to the Campaign Agent", timeout_s=300
    ) as info:
        for i in range(4):
            code = f"{prefix}_{i}"
            campaign_link.emit("info", "info", code, f"QA event {i} of round {n}", cfg=cfg)
            emitted.append(code)
        key = f"qa-{ctx.run_id}-{n}"
        campaign_link.emit(
            "blocker", "warning", f"{prefix}_B", "QA blocker (a test)", dedupe_key=key, cfg=cfg
        )
        campaign_link.emit(
            "resolved", "info", f"{prefix}_B", "QA blocker fixed", dedupe_key=key, cfg=cfg
        )
        emitted += [f"{prefix}_B", f"{prefix}_B"]
        t0 = time.time()
        drained = ctx.poll(lambda: not outbox.pending(), 120, 3.0)
        info.update(emitted=len(emitted), seconds_to_deliver=round(time.time() - t0), codes=emitted)
        expect(
            drained,
            f"{len(outbox.pending())} event(s) are still waiting to be sent after 2 min: the sender is not delivering",
        )
    with ctx.case(
        T, "the live campaign can be fetched and, when there is one, arrives intact", timeout_s=180
    ) as info:
        import asyncio

        state, campaign = asyncio.run(campaign_link.fetch_active(cfg, None))
        info["state"] = state
        expect(state in ("changed", "none"), f"unexpected answer {state}")
        if campaign is not None:
            info.update(
                version=campaign.version,
                bytes=len(campaign.leadgen_yaml or campaign.overlay_yaml or ""),
            )
            expect(
                campaign.intact(),
                "the campaign fetched from the Campaign Agent fails its own hash check",
            )

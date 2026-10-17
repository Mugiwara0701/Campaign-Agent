"""Slack as the Marketing Agent uses it: messages, alerts, approval buttons, escaping. Every message says it is a test and is deleted again."""

import time

from qa.lib.core import Ctx, Skip, expect, http
from qa.ma.common import QA_MARK, slack

T = "ma.slack"


def _channel(ctx: Ctx) -> str:
    channel = ctx.getenv("SLACK_CHANNEL_ALERTS") or ctx.getenv("SLACK_CHANNEL_MANUAL")
    if not channel:
        raise Skip("no SLACK_CHANNEL_ALERTS configured")
    return channel


def slack_messages(ctx: Ctx) -> None:
    channel = _channel(ctx)
    posted: list[tuple[str, str]] = []
    with ctx.case(T, "the bot token is valid and the workspace answers", timeout_s=60) as info:
        r = slack(ctx, "auth.test")
        expect(r.get("ok"), f"auth.test: {r.get('error')}")
        info.update(team=r.get("team"), bot=r.get("user"))
    with ctx.case(
        T, "a plain message is posted and Slack hands back its time stamp", timeout_s=60
    ) as info:
        t0 = time.time()
        r = slack(
            ctx,
            "chat.postMessage",
            channel=channel,
            text=f"{QA_MARK} run {ctx.run_id}: a plain message from the Marketing Agent's QA suite. It deletes itself.",
        )
        expect(r.get("ok"), f"chat.postMessage: {r.get('error')} (channel {channel})")
        posted.append((r["channel"], r["ts"]))
        info["seconds"] = round(time.time() - t0, 2)
        expect(
            r["message"]["text"].startswith(QA_MARK), "the stored text differs from what was sent"
        )
    with ctx.case(
        T,
        "an approval message with buttons is accepted (the same shape the real approvals use)",
        timeout_s=60,
    ) as info:
        from agentkit import slack as agent_slack

        blocks = agent_slack.approval_blocks(
            f"{QA_MARK} run {ctx.run_id}: approve this? (a test; the buttons do nothing)",
            "email:qa-test",
            [("Approve", "approve_email"), ("Reject", "skip_email")],
        )
        r = slack(
            ctx, "chat.postMessage", channel=channel, text=f"{QA_MARK} approval test", blocks=blocks
        )
        expect(r.get("ok"), f"chat.postMessage with buttons: {r.get('error')}")
        posted.append((r["channel"], r["ts"]))
        expect(
            any(b.get("type") == "actions" for b in r["message"].get("blocks", [])),
            "Slack did not keep the buttons",
        )
        info["blocks"] = len(r["message"].get("blocks", []))
    with ctx.case(T, "scraped text cannot ping people or inject links (escaping)", timeout_s=60):
        from agent.notify import esc

        nasty = "<!channel> <@U12345678> <https://evil.example|click me> & more"
        r = slack(
            ctx, "chat.postMessage", channel=channel, text=f"{QA_MARK} escaping: {esc(nasty)}"
        )
        expect(r.get("ok"), f"{r.get('error')}")
        posted.append((r["channel"], r["ts"]))
        stored = r["message"]["text"]
        expect(
            "<!channel>" not in stored and "<@U12345678>" not in stored,
            f"a mention survived: {stored[:120]}",
        )
        expect(
            "&lt;!channel&gt;" in stored,
            f"the text was changed in an unexpected way: {stored[:160]}",
        )
    with ctx.case(T, "a burst of five messages is not refused", timeout_s=120) as info:
        statuses = []
        for i in range(5):
            r = slack(ctx, "chat.postMessage", channel=channel, text=f"{QA_MARK} burst {i + 1}/5")
            statuses.append(bool(r.get("ok")))
            if r.get("ok"):
                posted.append((r["channel"], r["ts"]))
        info["ok"] = sum(statuses)
        expect(all(statuses), f"only {sum(statuses)} of 5 were accepted")
    with ctx.case(T, "the messages can be read back and are then deleted", timeout_s=180) as info:
        gone = 0
        readable = None
        for ch, ts in posted:
            hist = slack(
                ctx,
                "conversations.history",
                channel=ch,
                latest=ts,
                oldest=ts,
                inclusive=True,
                limit=1,
            )
            if hist.get("ok"):
                readable = readable is not False
            elif hist.get("error") == "missing_scope":
                readable = None
            if slack(ctx, "chat.delete", channel=ch, ts=ts).get("ok"):
                gone += 1
        info.update(posted=len(posted), deleted=gone, readable=readable)
        expect(
            gone == len(posted),
            f"deleted {gone} of {len(posted)} test messages: some are left in the channel",
        )
        if readable is None:
            info["warn"] = (
                "the bot may not read channel history (no scope), so reading back was not checked"
            )


def slack_alert(ctx: Ctx) -> None:
    with ctx.case(
        T, "an alert reaches the alerts channel through the incoming webhook", timeout_s=60
    ) as info:
        url = ctx.getenv("SLACK_ALERTS_WEBHOOK")
        expect(
            url,
            "SLACK_ALERTS_WEBHOOK is not set: the Marketing Agent's critical alerts (a refused token, a failure) are silently dropped",
        )
        r = http(
            "POST",
            url,
            body={
                "text": f"{QA_MARK} run {ctx.run_id}: an alert test from the Marketing Agent's QA suite. Nothing is wrong."
            },
            timeout=30,
        )
        info.update(status=r.status, body=r.text[:40])
        expect(
            r.status == 200 and r.text.strip() == "ok",
            f"the webhook answered {r.status} {r.text[:60]}",
        )
    with ctx.case(T, "the agent's own alert function posts without raising", timeout_s=60):
        import asyncio

        from agentkit import slack as agent_slack

        if not ctx.getenv("SLACK_ALERTS_WEBHOOK"):
            raise Skip("no webhook: the function does nothing, as the case above reports")
        asyncio.run(agent_slack.alert(f"{QA_MARK} run {ctx.run_id}: via agentkit.slack.alert"))

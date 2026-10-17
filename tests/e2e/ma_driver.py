"""The Marketing Agent's half of the two-agent test (tests/e2e/test_two_agents.py). Not a test itself.

Run with the Marketing Agent's own Python, from its checkout (CI job "Contract with the real Marketing Agent"):

    cd <MA checkout> && <MA python> <this file> <workdir>

Everything here is the real Marketing Agent code: the link client pulls the approved campaign from the Campaign Agent
(MA_LINK_URL, MA_LINK_TOKEN), checks it and reports it; the real pipeline, rules, SQLite store, approval and sender run on
it. Only the web, the model and the email writer are the Marketing Agent's own test fakes (a fixed fake web), Slack is
its dry-run approver (files), and the transport is the outbox (nothing is sent). One JSON object is printed at the end.

<workdir>/ma.sqlite3 and the link cache persist between runs, so a second run is the same agent after a restart.
"""

import asyncio
import functools
import json
import sys
from pathlib import Path
from typing import Any


async def main(work: Path) -> dict[str, Any]:
    from agent import campaign_link
    from agent.leadgen import approval, config, contacts, sender
    from agent.leadgen.pipeline import Pipeline, Services
    from agent.leadgen.repository.sqlite import SqliteRepository
    from agent.tests.leadgen_fakes import FakeBrowser, ScriptedModel, fake_draft
    from agent.tests.test_leadgen_pipeline import EV, EV_HOME, RESULTS, WEB, fake_people

    async def deliverable(_address: str) -> bool:  # no DNS lookups for the fake web's domains
        return True

    contacts.mail_domain_ok = deliverable  # type: ignore[assignment]

    out: dict[str, Any] = {}
    link = campaign_link.link_config()
    assert link is not None, "MA_LINK_URL and MA_LINK_TOKEN must be set"
    base = config.load().raw

    # 1. Pick the campaign up (twice: the second is a "nothing changed" answer), report it, send the events.
    effective = await campaign_link.apply_campaign(base, pass_id="e2e-1")  # noqa: S106 - a pass name
    again = await campaign_link.apply_campaign(base, pass_id="e2e-2")  # noqa: S106 - a pass name
    out["same_config_twice"] = again == effective
    sent_events = 0
    while n := await campaign_link.send_pending(link):
        sent_events += n
    out["events_sent"] = sent_events
    running = campaign_link.describe_pass() or {}
    out["campaign_id"], out["version"] = running.get("campaign_id"), running.get("version")
    out["live_campaign"] = campaign_link.live_campaign_id(link)
    if not out["campaign_id"]:
        out["stopped"] = "no approved campaign is live"
        return out
    campaign = (str(out["campaign_id"]), int(out["version"]))

    # 2. Discovery and qualification on the campaign's config, over the fixed fake web.
    cfg = config.from_raw(effective)
    out["include_countries"] = cfg.include_countries
    cfg.search.update({"queries_per_run": 3, "pages_per_query": 4, "query_suffix": ""})
    repo = SqliteRepository(str(work / "ma.sqlite3"))
    model = ScriptedModel({EV_HOME: EV})
    svc = Services(
        repo=repo,
        browser=FakeBrowser(WEB, RESULTS),
        approver=approval.SimulatedApprover(work / "slack"),
        assess=model,
        draft=fake_draft,
        find_contacts=functools.partial(contacts.discover, extract=fake_people),
    )
    out["pipeline"] = await Pipeline(svc, cfg, campaign=campaign).run()

    lead = await repo.find_lead(domain="voltgrid.com")
    out["lead_status"] = str(lead.status.value) if lead else None
    email = await repo.email_for_lead(lead.lead_id or "") if lead else None
    row = repo.db.execute(
        "select source_campaign_id, source_campaign_version from companies where domain = 'voltgrid.com'"
    ).fetchone()
    out["lead_campaign"] = [row[0], row[1]] if row else None
    out["email_status_before"] = email.status if email else None
    out["email_to"] = email.to if email else None
    if email is not None:
        erow = repo.db.execute(
            "select source_campaign_id, source_campaign_version from emails where id = ?",
            (email.email_id,),
        ).fetchone()
        out["email_campaign"] = [erow[0], erow[1]]
        # The Slack card (the dry-run approver writes it to a file): campaign, recipient, subject, whole body.
        card = (work / "slack" / "approvals" / f"{email.email_id}.md").read_text(encoding="utf-8")
        out["card_shows"] = {
            "campaign": campaign[0] in card,
            "draft": email.email_id in card,
            "recipient": email.to in card,
            "subject": email.subject in card,
            "body": email.body in card,
        }

    # 3. Nothing is sent before a person approves; then exactly one, once.
    outbox = sender.OutboxTransport(work / "out")
    send = functools.partial(sender.send_approved, repo, outbox, gap_seconds=0)
    out["sent_before_approval"] = (await send(live_campaign=campaign[0]))["sent"]
    if email is not None:
        out["decisions"] = [
            await approval.decide(repo, email.email_id, True, "slack:U-E2E"),
            await approval.decide(repo, email.email_id, True, "slack:U-E2E"),  # a repeated click
        ]
        out["sent_other_campaign_live"] = (
            await send(live_campaign="00000000-0000-0000-0000-000000000000")
        )["sent"]
        out["sent"] = [(await send(live_campaign=campaign[0]))["sent"] for _ in range(2)]
        out["email_status_after"] = (await repo.get_email(email.email_id)).status  # type: ignore[union-attr]
    out["counts"] = {
        t: repo.db.execute(f"select count(*) from {t}").fetchone()[0]  # noqa: S608 - fixed names
        for t in ("companies", "contacts", "emails", "approvals")
    }
    files = sorted((work / "out" / "outbox").glob("*.txt"))
    out["outbox_files"] = len(files)
    if files and email is not None:
        mail = files[0].read_text(encoding="utf-8")
        out["sent_matches_approval"] = (
            f"To: {email.to}" in mail and f"Subject: {email.subject}" in mail and email.body in mail
        )
    while await campaign_link.send_pending(link):
        pass
    await repo.close()
    return out


if __name__ == "__main__":
    workdir = Path(sys.argv[1])
    workdir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(main(workdir))
    print(json.dumps(result, default=str))

"""E-mail: a made-up lead is drafted, shown in Slack for approval, never sent unapproved, and once approved goes only to the test recipient.
The made-up contacts live on *.test.example, a domain that cannot receive mail."""

import re
import time

from qa.lib.core import Ctx, Skip, expect
from qa.ma.common import gmail, pg, run_agent, test_recipient

T = "ma.email"


def _draft(ctx: Ctx) -> str:
    done = run_agent(ctx, "test-email", "--count", "1", timeout=900)
    match = re.search(r"\(email id (\S+)\)", done.stdout)
    expect(
        match,
        f"test-email made no draft (exit {done.returncode}): {(done.stdout + done.stderr)[-250:]}",
    )
    return match.group(1).rstrip(")")  # type: ignore[union-attr]


def _row(ctx: Ctx, email_id: str) -> dict | None:
    return pg(ctx).one(
        "select e.status, e.subject, e.sent_at, c.email as contact_email, a.status as approval, a.slack_ts, a.decided_by "
        "from public.emails e join public.contacts c on c.id = e.contact_id "
        "left join public.approvals a on a.kind = 'email' and a.ref_id::text = e.id::text where e.id::text = $1 order by a.created_at desc limit 1",
        email_id,
    )


def email_approval_and_send(ctx: Ctx) -> None:
    recipient = test_recipient(ctx)
    eid = ""
    with ctx.case(
        T,
        "a made-up lead is drafted by the model and put up for approval in Slack",
        timeout_s=1000,
        fatal=True,
    ) as info:
        t0 = time.time()
        eid = _draft(ctx)
        row = _row(ctx, eid)
        expect(row, f"email {eid} is not in the database")
        info.update(
            email_id=eid,
            status=row["status"],
            approval=row["approval"],
            seconds=round(time.time() - t0),
            posted_to_slack=bool(row["slack_ts"]),
        )  # type: ignore[index]
        expect(row["approval"] is not None, "no approval row was opened for the draft")  # type: ignore[index]
        expect(
            row["slack_ts"],
            "the approval message was not posted to Slack (no message time stamp stored)",
        )  # type: ignore[index]
        expect(
            row["contact_email"].endswith(".test.example"),
            f"the made-up contact is {row['contact_email']}",
        )  # type: ignore[index]
    with ctx.case(
        T, "nothing is sent while the draft waits for a decision", timeout_s=200, fatal=True
    ) as info:
        done = run_agent(ctx, "send", timeout=120)
        time.sleep(40)  # the running service also sends approved mail every 20 s
        row = _row(ctx, eid)
        info.update(status=row["status"], send_exit=done.returncode)  # type: ignore[index]
        expect(
            row["status"] != "sent" and row["sent_at"] is None,
            f"the unapproved draft is {row['status']}",
        )  # type: ignore[index]
    with ctx.case(
        T, "after approval it is sent, and only to the test recipient", timeout_s=600
    ) as info:
        t_approved = time.time()
        approved = run_agent(ctx, "leads", "approve", eid, timeout=120)
        expect(
            approved.returncode == 0,
            f"approve failed: {(approved.stdout + approved.stderr)[-200:]}",
        )
        row = _row(ctx, eid)
        expect(
            row and row["approval"] in ("approved", "APPROVED"),
            f"approval is {row and row['approval']}",
        )
        run_agent(ctx, "send", timeout=180)
        sent = ctx.poll(lambda: (r := _row(ctx, eid)) and r["status"] == "sent" and r, 240, 6.0)
        expect(
            sent,
            f"the approved e-mail was not sent within 4 min (status {_row(ctx, eid)['status']})",
        )  # type: ignore[index]
        info.update(status=sent["status"], seconds_to_send=round(time.time() - t_approved))
        contact = sent["contact_email"]
        service = gmail(ctx)
        query = f'in:sent subject:"{contact}"'
        found = ctx.poll(
            lambda: (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=5)
                .execute()
                .get("messages")
            ),
            120,
            8.0,
        )
        expect(found, f"no sent message about {contact} in the Gmail sent folder")
        message = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=found[0]["id"],
                format="metadata",
                metadataHeaders=["To", "Cc", "Bcc", "Subject"],
            )
            .execute()
        )
        headers = {h["name"].lower(): h["value"] for h in message["payload"]["headers"]}
        info.update(
            to=headers.get("to", "").replace("@", " at "), subject=headers.get("subject", "")[:60]
        )
        expect(
            recipient.lower() in headers.get("to", "").lower(),
            f"the message was addressed to {headers.get('to')}, not the test recipient",
        )
        expect(
            contact.lower()
            not in (headers.get("to", "") + headers.get("cc", "") + headers.get("bcc", "")).lower(),
            "the made-up real contact is among the recipients",
        )
        expect(
            headers.get("subject", "").startswith("[TEST for"),
            f"the subject does not say it is a test: {headers.get('subject')}",
        )
        expect("cc" not in headers and "bcc" not in headers, "the message has Cc or Bcc recipients")


def email_reject(ctx: Ctx) -> None:
    test_recipient(ctx)
    eid = ""
    with ctx.case(T, "a second draft is rejected", timeout_s=1000, fatal=True) as info:
        eid = _draft(ctx)
        done = run_agent(ctx, "leads", "reject", eid, timeout=120)
        expect(done.returncode == 0, f"reject failed: {(done.stdout + done.stderr)[-200:]}")
        row = _row(ctx, eid)
        info.update(approval=row["approval"], status=row["status"])  # type: ignore[index]
        expect(row["approval"] in ("rejected", "REJECTED"), f"approval is {row['approval']}")  # type: ignore[index]
    with ctx.case(T, "a rejected e-mail is never sent", timeout_s=300) as info:
        run_agent(ctx, "send", timeout=180)
        time.sleep(45)
        row = _row(ctx, eid)
        info["status"] = row["status"]  # type: ignore[index]
        expect(
            row["status"] != "sent" and row["sent_at"] is None,
            f"the rejected e-mail is {row['status']}",
        )  # type: ignore[index]


def email_cleanup(ctx: Ctx) -> None:
    """Remove the made-up leads (their domain ends in .test.example) so that the dashboard does not fill up with them."""
    with ctx.case(T, "the made-up test leads are removed", timeout_s=200) as info:
        db = pg(ctx)
        try:
            ids = [
                r["id"]
                for r in db.fetch(
                    "select id from public.companies where domain like '%.test.example'"
                )
            ]
            if not ids:
                raise Skip("nothing to remove")
            db.execute(
                "delete from public.approvals where kind = 'email' and ref_id::text in (select e.id::text from public.emails e join public.contacts c on c.id = e.contact_id where c.company_id = any($1))",
                ids,
            )
            db.execute(
                "delete from public.emails where contact_id in (select id from public.contacts where company_id = any($1))",
                ids,
            )
            db.execute("delete from public.contacts where company_id = any($1)", ids)
            db.execute("delete from public.companies where id = any($1)", ids)
            info["removed"] = len(ids)
        except Skip:
            raise
        except Exception as exc:
            info["warn"] = (
                f"could not remove the test leads ({type(exc).__name__}): they stay marked with .test.example"
            )

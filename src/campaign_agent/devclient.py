"""A stand-in for the dashboard, for testing the Campaign Agent before the dashboard is connected.

`bash deploy/remote.sh try "Find robotics companies building warehouse robots on Linux"` creates a test
campaign, sends the same requests the dashboard will send (interpret, answer, edit, approve), waits for the
worker, and prints what the dashboard would show. It talks to the database only, never to the worker directly.

Safety:
  * test campaigns are named "[CA-TEST] ..." and are created inactive, so nothing mistakes them for real ones;
  * `try --clean` deletes only campaigns with that prefix (their requests and versions go with them);
  * approving a test campaign makes it THE live campaign (only one can be live), so it first says so, and asks
    for a typed word if another campaign is already live.
"""

import time
import uuid
from collections.abc import Callable
from typing import Any

from campaign_agent.questions.models import Question
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Database
from campaign_agent.view import ReviewView

TEST_PREFIX = "[CA-TEST] "
REQUESTED_BY = "dev-client"
_QUIT = {"q", "quit", "exit", ""}


def parse_answer(question: Question, raw: str) -> dict[str, Any]:
    """What the dashboard would send for what the person typed: a choice, several choices, text, or skip."""
    raw = raw.strip()
    if raw.lower() == "skip":
        return {"question_id": question.id, "skip": True}
    valid = {c.id for c in question.choices}
    if raw in valid:
        return {"question_id": question.id, "value": raw}
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) > 1 and all(p in valid for p in parts):
        return {"question_id": question.id, "value": parts}
    return {"question_id": question.id, "value": raw}


def format_view(view: ReviewView) -> list[str]:
    """The review as plain text, the way a person reads it."""
    lines = [f"State: {view.state}   revision {view.revision}   live: {view.live_version or 'no'}"]
    if view.live:
        seen = "not reported" if view.applied is None else ("yes" if view.applied else "not yet")
        lines[0] += f"   applied by the Marketing Agent: {seen}"
        if view.ma_error:
            lines.append(f"The Marketing Agent refused it: {view.ma_error}")
    for section in view.sections:
        lines.append(f"{section.title}:")
        for f in section.fields:
            quote = f'   "{f.evidence_quote}"' if f.evidence_quote else ""
            note = "" if f.mapping == "supported" else f"   ({f.mapping}: not applied yet)"
            lines.append(f"  - {f.label}: {f.value}   [{f.status}]{quote}{note}")
    if view.not_applied:
        lines.append("Could not be applied:")
        lines.extend(f"  - {n.request}: {n.reason}" for n in view.not_applied)
    for notice in view.notices:
        lines.append(f"Notice: {notice.message}")
    lines.extend(f"Warning: {w}" for w in view.warnings)
    if view.questions:
        lines.append("Questions:")
        for q in view.questions:
            skip = "" if q.skippable else "   (cannot be skipped)"
            lines.append(f"  [{q.id}] {q.text}{skip}")
            for choice in q.choices:
                lines.append(f"      {choice.id}: {choice.label}")
            if q.allow_text:
                lines.append("      or type your own answer")
    if view.candidate:
        c = view.candidate
        checks = ", ".join(f"L{lv.level} {lv.status}" for lv in c.levels)
        lines.append(
            f"Candidate: version {c.version}   sha256 {c.sha256[:12]}...   checks: {checks}"
        )
        issues = [i for lv in c.levels for i in lv.issues]
        lines.extend(f"  {i.severity}: {i.message}" for i in issues)
        # A level that did not run says why (for L6: no checkout, no sandbox, an earlier level failed)
        lines.extend(
            f"  L{lv.level} skipped: {lv.note}" for lv in c.levels if lv.status == "skipped"
        )
        lines.append("--- the YAML the Marketing Agent would get ---")
        lines.extend(c.yaml.rstrip().splitlines())
        lines.append("---")
    return lines


class DevClient:
    def __init__(
        self,
        db: Database,
        *,
        out: Callable[[str], None] = print,
        ask: Callable[[str], str] = input,
        tick: Callable[[], None] | None = None,
        timeout_s: float = 900.0,
        poll_s: float = 3.0,
    ) -> None:
        self.db = db
        self.out = out
        self.ask = ask
        self.tick = tick or (lambda: time.sleep(poll_s))
        self.timeout_s = timeout_s

    # --- talking to the database, as the dashboard will ---------------------------------------------------------------

    def create(self, message: str) -> str:
        name = (TEST_PREFIX + " ".join(message.split()))[:80]
        with self.db.transaction() as conn:
            row = conn.execute(
                "insert into public.campaign (campaign_name, campaign_detail, start_date, end_date, is_active) "
                "values (%s, %s, current_date, current_date + 30, false) returning id",
                (name, message[:5000]),
            ).fetchone()
        assert row is not None  # noqa: S101
        return str(row["id"])

    def send(
        self,
        campaign_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        base: int | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            request = repo.enqueue_request(
                conn,
                repo.NewRequest(
                    campaign_id, kind, REQUESTED_BY, uuid.uuid4().hex, payload or {}, base
                ),
            )
        return self._wait(str(request["id"]), campaign_id)

    def _wait(self, request_id: str, campaign_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_s
        shown = None
        while True:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "select * from public.campaign_request where id = %s", (request_id,)
                ).fetchone()
                state = conn.execute(
                    "select current_state from public.campaign_state where campaign_id = %s",
                    (campaign_id,),
                ).fetchone()
            assert row is not None  # noqa: S101
            if state and state["current_state"] != shown:
                shown = state["current_state"]
                self.out(f"  ... {shown}")
            if row["status"] in ("done", "failed"):
                return dict(row)
            if time.monotonic() > deadline:
                self.out(
                    f"Still waiting after {int(self.timeout_s)} seconds. The worker may be busy or stopped: "
                    "bash deploy/remote.sh status"
                )
                return dict(row)
            self.tick()

    def view(self, campaign_id: str) -> ReviewView | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "select review_view from public.campaign_state where campaign_id = %s",
                (campaign_id,),
            ).fetchone()
        return ReviewView.model_validate(row["review_view"]) if row and row["review_view"] else None

    def _live_elsewhere(self, campaign_id: str) -> str | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "select c.campaign_name from public.campaign_state s join public.campaign c on c.id = s.campaign_id "
                "where s.active_version_id is not null and s.campaign_id <> %s",
                (campaign_id,),
            ).fetchone()
        return str(row["campaign_name"]) if row else None

    # --- what a person does with it -------------------------------------------------------------------------------------

    def _report(self, row: dict[str, Any]) -> None:
        if row["status"] == "failed":
            self.out(f"The request failed: {row['error_code']}: {row['error_detail']}")
            result = row.get("result") or {}
            for key in ("errors", "rejected", "issues", "current"):
                if key in result:
                    self.out(f"  {key}: {result[key]}")
        elif row["status"] != "done":
            self.out("The request has not finished.")

    def _answers(self, view: ReviewView) -> list[dict[str, Any]]:
        answers = []
        for q in view.questions:
            self.out("")
            self.out(f"[{q.id}] {q.text}")
            for c in q.choices:
                self.out(f"    {c.id}: {c.label}")
            hint = "a choice id, several separated by commas, " if q.choices else ""
            if q.allow_text:
                hint += "your own words, "
            raw = self.ask(
                f"answer ({hint}{'or skip' if q.skippable else 'it cannot be skipped'}): "
            )
            answers.append(parse_answer(q, raw))
        return answers

    def _approve(self, campaign_id: str, view: ReviewView) -> dict[str, Any] | None:
        assert view.candidate is not None  # noqa: S101
        other = self._live_elsewhere(campaign_id)
        if other:
            self.out(
                f"Another campaign is live: {other!r}. Approving replaces it (you can bring it back with rollback)."
            )
            if self.ask("Type 'replace' to continue: ").strip() != "replace":
                self.out("Not approved.")
                return None
        elif (
            self.ask(
                "Approving makes this campaign the live one. Type 'approve' to continue: "
            ).strip()
            != "approve"
        ):
            self.out("Not approved.")
            return None
        return self.send(
            campaign_id,
            "approve",
            {"version": view.candidate.version, "sha256": view.candidate.sha256},
        )

    def session(self, message: str) -> int:
        """Send a message, then follow the conversation until it is live, stopped or left. Returns an exit code."""
        campaign_id = self.create(message)
        self.out(f"Test campaign {campaign_id}. Sending the message and waiting for the agent...")
        row: dict[str, Any] | None = self.send(campaign_id, "interpret")
        while row is not None:
            self._report(row)
            view = self.view(campaign_id)
            if view is None:
                self.out("The agent has not stored anything yet.")
                return 1
            self.out("")
            for line in format_view(view):
                self.out(line)
            row = self._next(campaign_id, view)
        self.out("")
        self.out(
            f"Test campaign {campaign_id} stays in the database. Remove all test campaigns: bash deploy/remote.sh try --clean"
        )
        return 0

    def _next(self, campaign_id: str, view: ReviewView) -> dict[str, Any] | None:
        """The next request for the state we are in, or None to stop."""
        if view.state == "CLARIFICATION_REQUIRED":
            return self.send(campaign_id, "answer", {"answers": self._answers(view)})
        if view.state == "AWAITING_REVIEW":
            choice = self.ask("\n[a]pprove, [e]dit in words, [d]iscard, [q]uit: ").strip().lower()
            if choice in ("a", "approve"):
                return self._approve(campaign_id, view)
            if choice in ("e", "edit"):
                text = self.ask("What should change? ").strip()
                return (
                    self.send(campaign_id, "edit", {"text": text}, base=view.revision)
                    if text
                    else None
                )
            if choice in ("d", "discard"):
                return self.send(campaign_id, "discard")
            return None
        if view.state == "PUBLISHED":
            self.out(
                f"\nVersion {view.live_version} is live."
                if view.live
                else "\nPublished, but not live."
            )
            return None
        if view.state == "MODEL_UNAVAILABLE":
            if self.ask("\nThe model did not answer. Try again? [y/N]: ").strip().lower() == "y":
                return self.send(campaign_id, "interpret")
        elif view.state == "DRAFT":
            self.out(
                "\nNothing was read from that message. Run the command again with another one."
            )
        return None

    def clean(self) -> int:
        with self.db.transaction() as conn:
            found = conn.execute(
                "select campaign_name from public.campaign where campaign_name like %s order by campaign_name",
                (TEST_PREFIX + "%",),
            ).fetchall()
        if not found:
            self.out("No test campaigns to remove.")
            return 0
        for r in found:
            self.out(f"  {r['campaign_name']}")
        if (
            self.ask(
                f"Delete these {len(found)} test campaign(s), with their requests and versions? Type 'clean': "
            ).strip()
            != "clean"
        ):
            self.out("Cancelled. Nothing was deleted.")
            return 2
        with self.db.transaction() as conn:
            conn.execute(
                "delete from public.campaign where campaign_name like %s", (TEST_PREFIX + "%",)
            )
        self.out(f"Removed {len(found)}.")
        return 0

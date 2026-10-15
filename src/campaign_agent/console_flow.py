"""What the window does for the person at the keyboard (the "CEO"): send a campaign message, answer the manager's
questions, approve the leadgen file, and ask for the Marketing Agent to start. Every step is a normal request to the
Campaign Agent worker (the same rows the dashboard writes); nothing here bypasses its checks.

Campaigns made here are named "[CA-TEST] ..." like the dev client's, so `try --clean` removes them.
"""

import uuid
from typing import Any

import psycopg

from campaign_agent.devclient import REQUESTED_BY as _DEV
from campaign_agent.devclient import TEST_PREFIX, parse_answer
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Database
from campaign_agent.view import ReviewView

REQUESTED_BY = "ceo-window"
MAX_MESSAGE_CHARS = 2000
PIPELINE_BY = "campaign-agent-window"


def short_name(text: str, limit: int = 60) -> str:
    """The campaign's short name: the first words of the message, cut between words, never in the middle of one."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (cut or text[:limit]) + "..."


def _enqueue(
    db: Database,
    campaign_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    base_revision: int | None = None,
) -> None:
    with db.transaction() as conn:
        repo.enqueue_request(
            conn,
            repo.NewRequest(
                campaign_id, kind, REQUESTED_BY, uuid.uuid4().hex, payload or {}, base_revision
            ),
        )


def start(db: Database, message: str) -> str:
    """A new test campaign from the CEO's words, and the request that makes the manager read it."""
    text = " ".join(message.split())[:MAX_MESSAGE_CHARS]
    if not text:
        raise ValueError("empty message")
    with db.transaction() as conn:
        row = conn.execute(
            "insert into public.campaign (campaign_name, campaign_detail, start_date, end_date, is_active) "
            "values (%s, %s, current_date, current_date + 30, false) returning id",
            ((TEST_PREFIX + short_name(text))[:80], text[:5000]),
        ).fetchone()
    assert row is not None  # noqa: S101
    campaign_id = str(row["id"])
    _enqueue(db, campaign_id, "interpret")
    return campaign_id


def _view(db: Database, campaign_id: str) -> ReviewView | None:
    with db.transaction() as conn:
        row = conn.execute(
            "select review_view from public.campaign_state where campaign_id = %s", (campaign_id,)
        ).fetchone()
    return ReviewView.model_validate(row["review_view"]) if row and row["review_view"] else None


def status(db: Database, campaign_id: str) -> dict[str, Any]:
    """What the window needs to show and to animate: the state, the questions, the file, whether it is live."""
    with db.transaction() as conn:
        state = conn.execute(
            "select current_state from public.campaign_state where campaign_id = %s", (campaign_id,)
        ).fetchone()
        request = conn.execute(
            "select request_type, status, error_code, result from public.campaign_request where campaign_id = %s "
            "order by created_at desc limit 1",
            (campaign_id,),
        ).fetchone()
    view = _view(db, campaign_id)
    out: dict[str, Any] = {
        "campaign_id": campaign_id,
        "state": state["current_state"] if state else "NEW",
        "busy": bool(request and request["status"] in ("pending", "processing")),
        "failed": request["error_code"] if request and request["status"] == "failed" else None,
        "gone": state is None and request is None,  # no state and no request: the campaign was deleted
        "questions": [],
        "candidate": None,
        "notes": [],
        "live": False,
        "applied": None,
        "pipeline_note": ((request or {}).get("result") or {}).get("pipeline") if request else None,
    }
    if view is not None:
        out["questions"] = [
            {
                "id": q.id,
                "text": q.text,
                "choices": [{"id": c.id, "label": c.label} for c in q.choices],
                "multi": q.multi,
                "allow_text": q.allow_text,
                "skippable": q.skippable,
            }
            for q in view.questions
        ]
        if view.candidate is not None:
            out["candidate"] = {
                "version": view.candidate.version,
                "levels": [{"level": lv.level, "status": lv.status} for lv in view.candidate.levels],
                "yaml": view.candidate.yaml[:4000],
            }
        # what the request asked for but the Marketing Agent cannot fully do (a country is only a search hint)
        out["notes"] = [{"kind": n.kind, "reason": n.reason} for n in view.not_applied][:6]
        out["live"], out["applied"] = view.live, view.applied
    return out


def latest(db: Database) -> dict[str, Any] | None:
    """The campaign this window (or the dev client) worked on most recently, in the same shape as `status`, so a window
    that was closed or restarted picks up where it was: the state lives in the database, not in the page."""
    with db.transaction() as conn:
        row = conn.execute(
            "select r.campaign_id, c.campaign_name, c.campaign_detail from public.campaign_request r "
            "join public.campaign c on c.id = r.campaign_id where r.requested_by in (%s, %s) "
            "order by r.created_at desc limit 1",
            (REQUESTED_BY, _DEV),
        ).fetchone()
    if row is None:
        return None
    out = status(db, str(row["campaign_id"]))
    out["name"] = str(row["campaign_name"]).removeprefix(TEST_PREFIX)
    out["message"] = str(row["campaign_detail"] or "")[:300]
    return out


def answer(db: Database, campaign_id: str, raw: dict[str, str]) -> None:
    view = _view(db, campaign_id)
    if view is None:
        raise LookupError("nothing to answer yet")
    answers = [parse_answer(q, raw.get(q.id, "skip")) for q in view.questions]
    _enqueue(db, campaign_id, "answer", {"answers": answers})


def edit(db: Database, campaign_id: str, text: str) -> None:
    """The CEO asks for a change in words ("only Germany and Austria"). It goes against the revision they were shown, so
    a change made to a file they have not seen is refused by the worker, as for the dashboard."""
    change = " ".join(text.split())[:MAX_MESSAGE_CHARS]
    if not change:
        raise ValueError("empty change")
    view = _view(db, campaign_id)
    if view is None or view.candidate is None:
        raise LookupError("there is no leadgen file to change yet")
    _enqueue(db, campaign_id, "edit", {"text": change}, view.revision)


def approve(db: Database, campaign_id: str, start_pipeline: bool = False) -> None:
    """The CEO's yes. It publishes exactly the file that was shown (version and hash), as the dashboard's button does;
    with `start_pipeline` the Campaign Agent also starts (or restarts) the Marketing Agent once the publish is committed."""
    view = _view(db, campaign_id)
    if view is None or view.candidate is None:
        raise LookupError("there is no leadgen file to approve yet")
    _enqueue(
        db,
        campaign_id,
        "approve",
        {"version": view.candidate.version, "sha256": view.candidate.sha256, "start_pipeline": start_pipeline},
    )


def pipeline(db: Database, state: str) -> dict[str, Any]:
    """Ask for the Marketing Agent's pipeline to run or stop, through the function the dashboard's button uses."""
    if state not in ("running", "stopped"):
        raise ValueError("state must be running or stopped")
    try:
        with db.transaction() as conn:
            conn.execute("select * from public.request_pipeline(%s, %s)", (state, PIPELINE_BY))
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedFunction):
        return {"ok": False, "reason": "needs_grant"}
    return {"ok": True}


def pipeline_state(db: Database) -> dict[str, Any] | None:
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "select desired_state, actual_state, current_step, extract(epoch from now() - heartbeat_at) as heartbeat_age "
                "from public.pipeline_control where id = 1"
            ).fetchone()
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable):
        return None
    if not row:
        return None
    out = dict(row)
    if out.get("heartbeat_age") is not None:
        out["heartbeat_age"] = round(float(out["heartbeat_age"]))
    return out


def control_state(db: Database) -> dict[str, Any] | None:
    """What the dashboard asked of the Campaign Agent and what it is doing (migration 0019); None before the migration."""
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "select desired_state, actual_state, requested_by from public.ca_control where id = 1"
            ).fetchone()
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable):
        return None
    return dict(row) if row else None


def control_request(db: Database, state: str) -> dict[str, Any]:
    """Pause or resume the Campaign Agent through the function the dashboard calls (the same row, the same rules)."""
    if state not in ("running", "paused"):
        raise ValueError("state must be running or paused")
    try:
        with db.transaction() as conn:
            conn.execute("select * from public.ca_request_state(%s, %s)", (state, REQUESTED_BY))
    except (psycopg.errors.UndefinedFunction, psycopg.errors.UndefinedTable):
        return {"ok": False, "reason": "needs_migration_0019"}
    return {"ok": True}

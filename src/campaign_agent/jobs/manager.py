"""Takes one request from the inbox, runs it, stores the outcome.

Three short transactions with no model call inside any of them:
  1. claim the oldest request that may run (and recover requests whose worker vanished);
  2. load the campaign and show "working" in the dashboard;
  ... run the request (the model may take minutes) ...
  3. store the new state, the candidate version, any change of what is live, and the result together,
     or release the request.
A crash anywhere before step 3 changes nothing but a "processing" label that the lease then clears.

What is live (the version the Marketing Agent runs) changes only through the database functions
ca_publish, ca_activate and ca_deactivate, called in step 3 inside the same transaction as the state.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg

from campaign_agent.compiler.build import Candidate
from campaign_agent.conversation.state import ConversationState
from campaign_agent.conversation.state_machine import Event
from campaign_agent.ir.models import CampaignIR
from campaign_agent.jobs.handlers import (
    Action,
    Outcome,
    Services,
    StoredCandidate,
    VersionInfo,
    Workspace,
    handle,
    progress_state,
)
from campaign_agent.jobs.requests import ErrorCode, Request, RequestError
from campaign_agent.ma_status import MaStatus, fetch_status
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Conn, Database
from campaign_agent.view import LastRequest, MaEventView, ReviewView, VersionView

log = logging.getLogger("campaign_agent.manager")

# SQLSTATEs raised by the publishing functions (migrations/0015_campaign_agent_publish.sql).
_DB_REFUSALS = {
    "CA001": (ErrorCode.STALE_APPROVAL, "that is not the latest candidate, or its text changed"),
    "CA002": (ErrorCode.INVALID_REQUEST, "no such version or campaign"),
    "CA003": (ErrorCode.WRONG_STATE, "that version was never approved"),
    "CA004": (ErrorCode.INVALID_REQUEST, "an approver is required"),
}


@dataclass(frozen=True)
class ManagerConfig:
    worker_id: str
    lease_s: int
    max_attempts: int
    # Requests one requester may have picked up per minute; the rest are refused with rate_limited.
    # It keeps one noisy or hostile requester from using up the model. 0 turns the limit off.
    rate_limit_per_minute: int = 30
    # Start on a new campaign by itself, when the dashboard saved it but sent no request (migration 0021).
    auto_interpret: bool = True
    auto_interpret_grace_s: int = 20
    auto_interpret_max_age_h: int = 168


def _request(row: dict[str, Any]) -> Request:
    return Request(
        id=str(row["id"]),
        campaign_id=str(row["campaign_id"]),
        request_type=row["request_type"],
        payload=row["payload"] or {},
        base_revision=row["base_revision"],
        requested_by=row["requested_by"],
    )


def load_workspace(conn: Conn, campaign: dict[str, Any]) -> Workspace:
    campaign_id = str(campaign["id"])
    state = repo.get_state(conn, campaign_id)
    ir = conv = view = None
    candidate = None
    if state is not None:
        ir = CampaignIR.model_validate(state["ir"]) if state["ir"] else None
        conv = state["conversation"] or None
        view = ReviewView.model_validate(state["review_view"]) if state["review_view"] else None
        if state["candidate_version_id"] is not None:
            row = repo.get_version(conn, str(state["candidate_version_id"]))
            if row is not None:
                candidate = StoredCandidate(row["version_no"], row["sha256"], row["overlay_yaml"])
    # The saved conversation is the truth. campaign_state.current_state is only what the dashboard shows,
    # and it says "INTERPRETING" while a request runs; after a crash it must not be read back as the state.
    conversation = ConversationState.model_validate(conv) if conv else ConversationState()
    versions = tuple(
        VersionInfo(v["version_no"], v["sha256"], v["approved_at"] is not None, bool(v["live"]))
        for v in repo.versions_summary(conn, campaign_id)
    )
    return Workspace(
        campaign_id=campaign_id,
        name=campaign["campaign_name"] or "",
        detail=campaign["campaign_detail"] or "",
        ir=ir,
        conv=conversation,
        previous_view=view,
        candidate=candidate,
        versions=versions,
    )


def _candidate_rows(candidate: Candidate, ir: CampaignIR) -> dict[str, Any]:
    return {
        "overlay_yaml": candidate.yaml,
        "sha256": candidate.sha256,
        "ir_hash": candidate.ir_hash,
        "contract_version": candidate.contract_version,
        "ir_snapshot": ir.model_dump(mode="json"),
        "validation_report": {
            "levels": [
                {
                    "level": lv.level,
                    "name": lv.name,
                    "status": lv.status,
                    "issues": [
                        {
                            "code": i.code,
                            "path": i.path,
                            "severity": i.severity,
                            "message": i.message,
                        }
                        for i in lv.issues
                    ],
                }
                for lv in candidate.report.levels
            ]
        },
        "not_applied": [
            {"request": n.request, "reason": n.reason, "proposal": n.proposal, "kind": n.kind}
            for n in candidate.not_applied
        ],
    }


def _with_versions(
    view: ReviewView, rows: list[dict[str, Any]], version_no: int | None = None
) -> ReviewView:
    """The stored view, with the version list and what is live read from the database."""
    if version_no is not None and view.candidate is not None:
        view = view.with_candidate_version(version_no)
    versions = tuple(
        VersionView(
            version=r["version_no"],
            sha256=r["sha256"],
            approved_by=r["approved_by"],
            approved_at=r["approved_at"].isoformat() if r["approved_at"] else None,
            live=bool(r["live"]),
        )
        for r in rows
    )
    live = next((v.version for v in versions if v.live), None)
    return view.model_copy(
        update={"versions": versions, "live": live is not None, "live_version": live}
    )


def with_applied(
    view: ReviewView, status: MaStatus | None, campaign_id: str, rows: list[dict[str, Any]]
) -> ReviewView:
    """Whether the Marketing Agent runs the live version. Unknown until it has reported."""
    live = next((r for r in rows if r["live"]), None)
    if live is None or status is None or status.current is None:
        return view.model_copy(update={"applied": None, "ma_error": None})
    applied = status.applied(campaign_id, live["version_no"], live["sha256"])
    error = None if applied else status.rejection(campaign_id, live["version_no"])
    return view.model_copy(update={"applied": applied, "ma_error": error})


def with_ma_events(view: ReviewView, rows: list[dict[str, Any]]) -> ReviewView:
    """The events the Marketing Agent reported over the link, as the dashboard shows them."""
    events = tuple(
        MaEventView(
            kind=r["kind"],
            severity=r["severity"],
            code=r["code"],
            message=r["message"],
            occurred_at=r["occurred_at"].isoformat(),
            version=r["version_no"],
            repeat_count=r["repeat_count"],
        )
        for r in rows
    )
    return view.model_copy(update={"ma_events": events})


def _fetch_events(conn: Conn, campaign_id: str) -> list[dict[str, Any]] | None:
    """None when migration 0017 is not applied yet (inside a savepoint, so the caller's transaction survives)."""
    try:
        with conn.transaction():
            return repo.events_for_view(conn, campaign_id)
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        return None


def refresh_applied(db: Database) -> bool:
    """Bring the live campaign's view up to date with what the Marketing Agent reports. Returns whether it changed."""
    with db.transaction() as conn:
        row = conn.execute(
            "select campaign_id from public.campaign_state where active_version_id is not null"
        ).fetchone()
        if row is None:
            return False
        cid = str(row["campaign_id"])
        state = repo.get_state(conn, cid, lock=True)
        if state is None or not state["review_view"]:
            return False
        before = ReviewView.model_validate(state["review_view"])
        after = with_applied(before, fetch_status(conn), cid, repo.versions_summary(conn, cid))
        events = _fetch_events(conn, cid)
        if events is not None:
            after = with_ma_events(after, events)
        if after == before:
            return False
        repo.update_view(conn, cid, after.model_dump(mode="json"))
        return True


def _run_action(conn: Conn, req: Request, action: Action) -> list[str]:
    """Change what is live. Returns the campaigns that stopped being live because of it."""
    if action.kind == "publish":
        assert action.version_no is not None  # noqa: S101
        assert action.sha256 is not None  # noqa: S101
        done = repo.publish(
            conn, req.campaign_id, action.version_no, action.sha256, action.approver
        )
    elif action.kind == "activate":
        assert action.version_no is not None  # noqa: S101
        done = repo.activate(conn, req.campaign_id, action.version_no)
    elif action.kind == "start_pipeline":
        return []  # nothing to change in the store: the pipeline is asked for after the commit
    else:
        repo.deactivate(conn, req.campaign_id)
        return []
    return [str(c) for c in done.get("superseded", [])]


PIPELINE_STOP_WAIT_S = 90.0  # how long a restart waits for a running pass to end before it asks for "running" again


def start_marketing_agent(
    db: Database,
    approver: str,
    *,
    wait_s: float = PIPELINE_STOP_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """After an approval was committed: run the Marketing Agent's pipeline on the new campaign, as its Start button does.

    A pipeline that is already running only takes a campaign at its next pass, so it is stopped and started again (the
    two requests need a gap, or the Marketing Agent, which looks every few seconds, would never see the stop). Never
    raises into the caller's request: the campaign is live either way, and the answer says what happened."""
    by = f"approved by {approver}"[:200]
    with db.transaction() as conn:
        state = repo.pipeline_state(conn)
    if state is None:
        return {"asked": False, "reason": "pipeline_not_readable"}
    restarted = False
    if "running" in (state["desired_state"], state["actual_state"]):
        with db.transaction() as conn:
            if not repo.request_pipeline(conn, "stopped", by):
                return {"asked": False, "reason": "not_allowed"}
        restarted = True
        deadline = clock() + wait_s
        stopped = False
        while clock() < deadline:
            with db.transaction() as conn:
                now = repo.pipeline_state(conn)
            if now is not None and now["actual_state"] == "stopped":
                stopped = True
                break
            sleep(2.0)
        if not stopped:
            log.warning("the pipeline did not stop in time; asking for it to run again")
    with db.transaction() as conn:
        asked = repo.request_pipeline(conn, "running", by)
    return {"asked": asked, "restarted": restarted, **({} if asked else {"reason": "not_allowed"})}


def _refresh_superseded(conn: Conn, campaign_ids: list[str]) -> None:
    """A campaign that is no longer live must not keep showing "live" in its dashboard view."""
    for cid in campaign_ids:
        state = repo.get_state(conn, cid)
        if state is None or not state["review_view"]:
            continue
        view = _with_versions(
            ReviewView.model_validate(state["review_view"]), repo.versions_summary(conn, cid)
        )
        repo.update_view(conn, cid, view.model_dump(mode="json"))


def process_next(db: Database, svc: Services, cfg: ManagerConfig) -> bool:
    """Run one request if there is one. Returns whether it did."""
    with db.transaction() as conn:
        repo.requeue_expired(conn, cfg.max_attempts)
        row = repo.claim_request(conn, cfg.worker_id, cfg.lease_s)
    if row is None:
        return False
    req = _request(row)
    context = {"request_id": req.id, "campaign_id": req.campaign_id, "type": req.request_type}
    log.info("request started", extra=context)
    try:
        with db.transaction() as conn:
            if cfg.rate_limit_per_minute and (
                repo.count_recent_requests(conn, req.requested_by, 60, except_id=req.id)
                >= cfg.rate_limit_per_minute
            ):
                repo.fail_request(
                    conn,
                    req.id,
                    ErrorCode.RATE_LIMITED.value,
                    f"too many requests from {req.requested_by[:40]}; wait a minute and try again",
                )
                log.warning("request refused", extra={**context, "code": "rate_limited"})
                return True
            campaign = repo.get_campaign(conn, req.campaign_id)
            if campaign is None:
                repo.fail_request(
                    conn, req.id, ErrorCode.UNKNOWN_CAMPAIGN.value, "no such campaign"
                )
                log.warning("request failed", extra={**context, "code": "unknown_campaign"})
                return True
            ws = load_workspace(conn, campaign)
            shown = progress_state(req, ws)
            if shown is not None:
                repo.set_progress(conn, req.campaign_id, shown.value)
        outcome = handle(req, ws, svc)
        outcome = _store_or_refuse(db, req, ws, outcome)
    except Exception as exc:
        log.exception("request crashed", extra=context)
        try:
            with db.transaction() as conn:
                status = repo.release_request(
                    conn, req.id, f"{type(exc).__name__}", cfg.max_attempts
                )
        except Exception:  # the clean-up must never take the worker down: the next request is still waiting
            log.exception("could not release the request", extra=context)
            return True
        if status == "gone":
            log.warning(
                "request dropped: it no longer exists (its campaign was deleted)", extra=context
            )
        else:
            log.warning("request released", extra={**context, "status": status})
        return True
    if outcome.error is None and outcome.action is not None and outcome.action.start_pipeline:
        try:
            note = start_marketing_agent(db, req.requested_by)
        except Exception:
            log.exception("could not ask for the pipeline to start", extra=context)
            note = {"asked": False, "reason": "error"}
        try:
            with db.transaction() as conn:
                repo.merge_request_result(conn, req.id, {"pipeline": note})
        except Exception:
            log.exception("could not record the pipeline request", extra=context)
        log.info("pipeline requested after approval", extra={**context, **note})
    code = outcome.error.code.value if outcome.error else None
    log.info("request finished", extra={**context, "state": outcome.conv.state.value, "code": code})
    return True


def _store_or_refuse(db: Database, req: Request, ws: Workspace, outcome: Outcome) -> Outcome:
    """Store the outcome. If the database refuses the change of what is live, record that instead."""
    try:
        with db.transaction() as conn:
            _store(conn, req, ws, outcome)
        return outcome
    except psycopg.Error as exc:
        if outcome.action is None:
            raise
        code, detail = _DB_REFUSALS.get(
            getattr(exc, "sqlstate", None) or "",
            (
                ErrorCode.PUBLISH_FAILED,
                "the store did not accept the change; it is safe to try again",
            ),
        )
        log.warning("publish refused", extra={"request_id": req.id, "code": code.value})
        conv = ws.conv
        if code is ErrorCode.PUBLISH_FAILED and outcome.action.kind == "publish":
            first = (
                Event.APPROVE if ws.conv.state.value == "AWAITING_REVIEW" else Event.RETRY_PUBLISH
            )
            conv = ws.conv.after(first).after(Event.PUBLISH_FAILED)
        last = LastRequest(id=req.id, type=req.request_type, outcome="failed", code=code.value)
        shown = (ws.previous_view or outcome.view).model_copy(
            update={"state": conv.state.value, "last_request": last}
        )
        refused = Outcome(
            ws.ir or outcome.ir,
            conv,
            shown,
            None,
            {"state": conv.state.value},
            RequestError(code, detail),
        )
        with db.transaction() as conn:
            _store(conn, req, ws, refused)
        return refused


def _store(conn: Conn, req: Request, ws: Workspace, outcome: Outcome) -> None:
    current = repo.get_state(conn, req.campaign_id, lock=True)
    candidate_id = (
        str(current["candidate_version_id"])
        if current and current["candidate_version_id"]
        else None
    )
    version_no: int | None = None
    if outcome.candidate is not None:
        version = repo.insert_candidate(
            conn, req.campaign_id, **_candidate_rows(outcome.candidate, outcome.ir)
        )
        candidate_id, version_no = str(version["id"]), version["version_no"]
    superseded: list[str] = []
    if outcome.action is not None and outcome.error is None:
        superseded = _run_action(conn, req, outcome.action)
    rows = repo.versions_summary(conn, req.campaign_id)
    view = with_applied(
        _with_versions(outcome.view, rows, version_no), fetch_status(conn), req.campaign_id, rows
    )
    repo.save_state(
        conn,
        req.campaign_id,
        state=outcome.conv.state.value,
        revision=outcome.ir.meta.revision,
        ir=outcome.ir.model_dump(mode="json"),
        conversation=outcome.conv.model_dump(mode="json"),
        review_view=view.model_dump(mode="json"),
        candidate_version_id=candidate_id,
    )
    _refresh_superseded(conn, superseded)
    if outcome.error is not None:
        repo.fail_request(
            conn, req.id, outcome.error.code.value, outcome.error.detail, outcome.result
        )
    else:
        repo.complete_request(conn, req.id, outcome.result)

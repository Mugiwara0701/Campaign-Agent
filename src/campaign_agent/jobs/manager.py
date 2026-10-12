"""Takes one request from the inbox, runs it, stores the outcome.

Three short transactions with no model call inside any of them:
  1. claim the oldest request that may run (and recover requests whose worker vanished);
  2. load the campaign and show "working" in the dashboard;
  ... run the request (the model may take minutes) ...
  3. store the new state, the candidate version and the result together, or release the request.
A crash anywhere before step 3 changes nothing but a "processing" label that the lease then clears.
"""

import logging
from dataclasses import dataclass
from typing import Any

from campaign_agent.compiler.build import Candidate
from campaign_agent.conversation.state import ConversationState
from campaign_agent.ir.models import CampaignIR
from campaign_agent.jobs.handlers import Outcome, Services, Workspace, handle, progress_state
from campaign_agent.jobs.requests import ErrorCode, Request
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Conn, Database
from campaign_agent.view import ReviewView

log = logging.getLogger("campaign_agent.manager")


@dataclass(frozen=True)
class ManagerConfig:
    worker_id: str
    lease_s: int
    max_attempts: int


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
    state = repo.get_state(conn, str(campaign["id"]))
    ir = conv = view = None
    if state is not None:
        ir = CampaignIR.model_validate(state["ir"]) if state["ir"] else None
        conv = state["conversation"] or None
        view = ReviewView.model_validate(state["review_view"]) if state["review_view"] else None
    # The saved conversation is the truth. campaign_state.current_state is only what the dashboard shows,
    # and it says "INTERPRETING" while a request runs; after a crash it must not be read back as the state.
    conversation = ConversationState.model_validate(conv) if conv else ConversationState()
    return Workspace(
        campaign_id=str(campaign["id"]),
        name=campaign["campaign_name"] or "",
        detail=campaign["campaign_detail"] or "",
        ir=ir,
        conv=conversation,
        previous_view=view,
    )


def _candidate_rows(candidate: Candidate) -> dict[str, Any]:
    return {
        "overlay_yaml": candidate.yaml,
        "sha256": candidate.sha256,
        "ir_hash": candidate.ir_hash,
        "contract_version": candidate.contract_version,
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


def _final_view(outcome: Outcome, version_no: int | None) -> ReviewView:
    """The view to store: the handler's, with the version number of a candidate stored just now."""
    if outcome.candidate is not None and version_no is not None:
        return outcome.view.with_candidate_version(version_no)
    return outcome.view


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
        with db.transaction() as conn:
            _store(conn, req, ws, outcome)
    except Exception as exc:
        log.exception("request crashed", extra=context)
        with db.transaction() as conn:
            status = repo.release_request(conn, req.id, f"{type(exc).__name__}", cfg.max_attempts)
        log.warning("request released", extra={**context, "status": status})
        return True
    code = outcome.error.code.value if outcome.error else None
    log.info("request finished", extra={**context, "state": outcome.conv.state.value, "code": code})
    return True


def _store(conn: Conn, req: Request, ws: Workspace, outcome: Outcome) -> None:
    current = repo.get_state(conn, req.campaign_id, lock=True)
    candidate_id = (
        str(current["candidate_version_id"])
        if current and current["candidate_version_id"]
        else None
    )
    version_no: int | None = None
    if outcome.candidate is not None:
        version = repo.insert_candidate(conn, req.campaign_id, **_candidate_rows(outcome.candidate))
        candidate_id, version_no = str(version["id"]), version["version_no"]
    view = _final_view(outcome, version_no)
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
    if outcome.error is not None:
        repo.fail_request(
            conn, req.id, outcome.error.code.value, outcome.error.detail, outcome.result
        )
    else:
        repo.complete_request(conn, req.id, outcome.result)

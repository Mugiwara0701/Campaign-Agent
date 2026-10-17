"""One workflow status for the dashboard, across both agents.

The Campaign Agent's own review state (conversation/state_machine.py) is detailed and is still returned as `state`. The
dashboard also needs one answer to "where is this campaign in the whole workflow?", including what the Marketing Agent
did with it. `workflow_status()` derives that from what is already stored; nothing new is written, so it can never
disagree with the records it is read from.

    RECEIVED                    the campaign exists; the Campaign Agent has not started on it yet
    RESEARCHING                 reading the request, researching, compiling and validating a proposal
    NEEDS_INPUT                 the Campaign Agent asks the person a question, or validation needs a decision
    CHANGES_REQUESTED           the person asked for a change; a new version is being prepared
    PENDING_CAMPAIGN_APPROVAL   a validated, frozen version waits for approval
    APPROVED                    approved; being published
    REJECTED                    discarded before it was ever live
    CONFIG_READY                live and approved; the Marketing Agent has not confirmed it took this version yet
    DELIVERED_TO_MA             the Marketing Agent confirmed it runs exactly this version (id, version and sha256)
    PROCESSING                  delivered, and the Marketing Agent has found leads for it
    FAILED                      something needs a person: the model is down, the publish failed, or the Marketing Agent
                                refused the version (see `error`)
    CANCELLED                   it was live once and is not any more (switched off, or another campaign replaced it)

There is no COMPLETED: a campaign runs until it is switched off (then CANCELLED), the Marketing Agent has no end state.
"""

from typing import Any

RESEARCHING_STATES = {
    "DRAFT",
    "INTERPRETING",
    "RESEARCHING",
    "READY_FOR_VALIDATION",
    "VALIDATING",
    "VALIDATED",
}
INPUT_STATES = {"CLARIFICATION_REQUIRED", "VALIDATION_FAILED"}
CHANGE_REQUESTS = {"edit", "answer", "suggestion", "rollback"}
OPEN = {"pending", "queued", "processing"}


def workflow_status(
    state: str | None,
    review_view: dict[str, Any] | None,
    last_request: dict[str, Any] | None,
    progress: dict[str, Any] | None = None,
) -> tuple[str, str | None]:
    """(status, error). `error` says what a person must look at, for FAILED (and an MA refusal); otherwise None."""
    view = review_view or {}
    req = last_request or {}
    pending_change = req.get("request_type") in CHANGE_REQUESTS and req.get("status") in OPEN
    if state in (None, "NEW"):
        return "RECEIVED", None
    if state == "MODEL_UNAVAILABLE":
        return "FAILED", "the language model is not available; the Campaign Agent retries"
    if state == "PUBLISH_FAILED":
        return "FAILED", req.get("error_detail") or "the approved version could not be published"
    if state == "DISCARDED":
        return "REJECTED", None
    if state == "APPROVED":
        return "APPROVED", None
    if state in RESEARCHING_STATES or state in INPUT_STATES:
        if pending_change:
            return "CHANGES_REQUESTED", None
        return ("NEEDS_INPUT" if state in INPUT_STATES else "RESEARCHING"), None
    if state == "AWAITING_REVIEW":
        return ("CHANGES_REQUESTED" if pending_change else "PENDING_CAMPAIGN_APPROVAL"), None
    if state == "PUBLISHED":
        return _after_publish(view, progress)
    return "RESEARCHING", None  # a state added later: never claim more than "being worked on"


def _after_publish(view: dict[str, Any], progress: dict[str, Any] | None) -> tuple[str, str | None]:
    if not view.get("live"):
        return "CANCELLED", None
    applied = view.get("applied")
    if applied is False:
        return "FAILED", view.get("ma_error") or "the Marketing Agent refused this version"
    if applied is None:
        return "CONFIG_READY", None
    leads = (progress or {}).get("leads") or {}
    if any(int(n or 0) > 0 for n in leads.values()):
        return "PROCESSING", None
    return "DELIVERED_TO_MA", None

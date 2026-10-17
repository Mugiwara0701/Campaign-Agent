"""The one workflow status the dashboard shows (api/workflow.py)."""

from typing import Any

import pytest

from campaign_agent.api.workflow import workflow_status


def _req(kind: str, status: str = "processing") -> dict[str, Any]:
    return {"request_type": kind, "status": status, "error_detail": None}


@pytest.mark.parametrize(
    ("state", "last", "expected"),
    [
        (None, None, "RECEIVED"),
        ("NEW", _req("interpret", "pending"), "RECEIVED"),
        ("INTERPRETING", _req("interpret"), "RESEARCHING"),
        ("RESEARCHING", _req("interpret"), "RESEARCHING"),
        ("VALIDATING", None, "RESEARCHING"),
        ("CLARIFICATION_REQUIRED", _req("interpret", "done"), "NEEDS_INPUT"),
        ("VALIDATION_FAILED", None, "NEEDS_INPUT"),
        ("AWAITING_REVIEW", _req("interpret", "done"), "PENDING_CAMPAIGN_APPROVAL"),
        ("AWAITING_REVIEW", _req("edit", "pending"), "CHANGES_REQUESTED"),
        ("INTERPRETING", _req("edit"), "CHANGES_REQUESTED"),
        ("APPROVED", _req("approve"), "APPROVED"),
        ("DISCARDED", None, "REJECTED"),
        ("SOMETHING_NEW", None, "RESEARCHING"),
    ],
)
def test_review_states(state: str | None, last: dict[str, Any] | None, expected: str) -> None:
    assert workflow_status(state, None, last) == (expected, None)


def test_failures_say_what_to_look_at() -> None:
    status, error = workflow_status("MODEL_UNAVAILABLE", None, None)
    assert status == "FAILED" and error and "model" in error
    status, error = workflow_status(
        "PUBLISH_FAILED", None, {**_req("approve", "failed"), "error_detail": "db said no"}
    )
    assert (status, error) == ("FAILED", "db said no")


def test_after_publication_the_marketing_agent_decides_the_status() -> None:
    live = {"live": True, "applied": None}
    assert workflow_status("PUBLISHED", live, None) == ("CONFIG_READY", None)
    assert workflow_status("PUBLISHED", {**live, "applied": True}, None) == ("DELIVERED_TO_MA", None)
    found = {"leads": {"DISCOVERED": 2}, "emails": {}}
    assert workflow_status("PUBLISHED", {**live, "applied": True}, None, found) == ("PROCESSING", None)
    none_yet = {"leads": {}, "emails": {}}
    assert workflow_status("PUBLISHED", {**live, "applied": True}, None, none_yet)[0] == "DELIVERED_TO_MA"
    refused = {**live, "applied": False, "ma_error": "config_version 9 is not supported"}
    assert workflow_status("PUBLISHED", refused, None) == (
        "FAILED",
        "config_version 9 is not supported",
    )
    assert workflow_status("PUBLISHED", {"live": False, "applied": True}, None) == (
        "CANCELLED",
        None,
    )


def test_never_delivered_without_the_marketing_agent_confirming() -> None:
    """Leads alone are not proof: without the MA's own confirmation of this version it stays CONFIG_READY."""
    found = {"leads": {"QUALIFIED": 5}}
    assert workflow_status("PUBLISHED", {"live": True}, None, found)[0] == "CONFIG_READY"

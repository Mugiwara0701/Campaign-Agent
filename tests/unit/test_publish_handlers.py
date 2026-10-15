from typing import Any

import pytest

from campaign_agent.compiler.build import validate_overlay_text
from campaign_agent.conversation.state import ConversationState
from campaign_agent.conversation.state_machine import State
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.jobs.handlers import (
    Services,
    StoredCandidate,
    VersionInfo,
    Workspace,
    handle,
)
from campaign_agent.jobs.requests import ErrorCode, Request
from campaign_agent.llm.stub import StubAdapter
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT


def svc() -> Services:
    return Services(Extractor(StubAdapter([COMPLETE_REPLY]), sleep=lambda _s: None))


def request(
    kind: str, payload: dict[str, Any] | None = None, by: str = "dashboard-user"
) -> Request:
    return Request("r1", "c1", kind, payload or {}, None, by)


def reviewing() -> Workspace:
    first = handle(
        Request("r0", "c1", "interpret", {"message": COMPLETE_TEXT}, None, "u"),
        Workspace("c1", "Auto", COMPLETE_TEXT, None, ConversationState()),
        svc(),
    )
    assert first.candidate is not None
    c = first.candidate
    return Workspace(
        "c1",
        "Auto",
        COMPLETE_TEXT,
        first.ir,
        first.conv,
        first.view,
        candidate=StoredCandidate(1, c.sha256, c.yaml),
        versions=(VersionInfo(1, c.sha256, approved=False, live=False),),
    )


def approve(ws: Workspace, **override: Any):  # type: ignore[no-untyped-def]
    c = ws.candidate
    assert c is not None
    payload = {"version": c.version_no, "sha256": c.sha256, **override}
    return handle(request("approve", payload), ws, svc())


def with_state(ws: Workspace, state: State, **changes: Any) -> Workspace:
    conv = ws.conv.model_copy(update={"state": state, **changes})
    return Workspace(
        ws.campaign_id, ws.name, ws.detail, ws.ir, conv, ws.previous_view, ws.candidate, ws.versions
    )


# --- approve ---------------------------------------------------------------------------------------------------------


def test_approving_the_reviewed_candidate_publishes_it() -> None:
    ws = reviewing()
    out = approve(ws)
    assert out.error is None
    assert out.conv.state is State.PUBLISHED
    assert out.conv.has_published
    assert out.action is not None
    assert (out.action.kind, out.action.version_no, out.action.sha256) == (
        "publish",
        1,
        ws.candidate.sha256,
    )  # type: ignore[union-attr]
    assert out.action.approver == "dashboard-user"
    assert out.result["published"] is True
    assert out.result["effective_from"] == "next_pass"
    assert out.view.candidate is not None  # the published YAML stays on screen
    assert out.ir == ws.ir  # approving never changes the campaign itself


def test_the_approver_is_whoever_the_dashboard_says_signed_in() -> None:
    ws = reviewing()
    out = handle(
        request("approve", {"version": 1, "sha256": ws.candidate.sha256}, by="priya@example.com"),
        ws,
        svc(),
    )  # type: ignore[union-attr]
    assert out.action is not None and out.action.approver == "priya@example.com"


def test_an_approval_can_ask_for_the_pipeline_to_start_after_the_publish() -> None:
    ws = reviewing()
    out = approve(ws, start_pipeline=True)
    assert out.error is None and out.conv.state is State.PUBLISHED
    assert (
        out.action is not None
        and out.action.kind == "publish"
        and out.action.start_pipeline is True
    )


def test_without_the_flag_an_approval_starts_nothing() -> None:
    out = approve(reviewing())
    assert out.action is not None and out.action.start_pipeline is False


@pytest.mark.parametrize("flag", ["yes", 1, "true", None])
def test_start_pipeline_must_be_true_or_false(flag: object) -> None:
    out = approve(reviewing(), start_pipeline=flag)
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST
    assert out.action is None and out.conv.state is State.AWAITING_REVIEW


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 1},
        {"sha256": "x"},
        {"version": "1", "sha256": "x"},
        {"version": True, "sha256": "x"},
    ],
)
def test_an_approval_must_name_the_version_and_the_text(payload: dict[str, Any]) -> None:
    out = handle(request("approve", payload), reviewing(), svc())
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST


def test_an_old_version_is_a_stale_approval_and_the_current_one_is_returned() -> None:
    ws = reviewing()
    newer = StoredCandidate(2, "b" * 64, ws.candidate.yaml)  # type: ignore[union-attr]
    ws = Workspace(
        ws.campaign_id, ws.name, ws.detail, ws.ir, ws.conv, ws.previous_view, newer, ws.versions
    )
    out = handle(request("approve", {"version": 1, "sha256": "a" * 64}), ws, svc())
    assert out.error is not None and out.error.code is ErrorCode.STALE_APPROVAL
    assert out.result["current"] == {"version": 2, "sha256": "b" * 64}
    assert out.action is None
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.view.candidate is not None  # the dashboard still shows the candidate to review


def test_a_changed_text_is_a_stale_approval_even_for_the_right_version() -> None:
    out = approve(reviewing(), sha256="f" * 64)
    assert out.error is not None and out.error.code is ErrorCode.STALE_APPROVAL
    assert out.action is None


@pytest.mark.parametrize(
    "state",
    [
        State.DRAFT,
        State.CLARIFICATION_REQUIRED,
        State.INTERPRETING,
        State.VALIDATION_FAILED,
        State.MODEL_UNAVAILABLE,
        State.DISCARDED,
    ],
)
def test_nothing_can_be_approved_outside_review(state: State) -> None:
    out = approve(with_state(reviewing(), state))
    assert out.error is not None and out.error.code is ErrorCode.WRONG_STATE
    assert out.action is None


def test_approving_after_a_discard_is_refused() -> None:
    ws = reviewing()
    gone = handle(request("discard"), ws, svc())
    out = approve(
        Workspace(
            ws.campaign_id,
            ws.name,
            ws.detail,
            gone.ir,
            gone.conv,
            gone.view,
            ws.candidate,
            ws.versions,
        )
    )
    assert out.error is not None and out.error.code is ErrorCode.WRONG_STATE


def test_approving_what_is_already_live_is_a_harmless_repeat() -> None:
    ws = reviewing()
    live = Workspace(
        ws.campaign_id,
        ws.name,
        ws.detail,
        ws.ir,
        ws.conv.model_copy(update={"state": State.PUBLISHED, "has_published": True}),
        ws.previous_view,
        ws.candidate,
        (VersionInfo(1, ws.candidate.sha256, True, True),),  # type: ignore[union-attr]
    )
    out = approve(live)
    assert out.error is None
    assert out.action is None  # nothing to do twice
    assert out.result["already"] is True
    assert out.conv.state is State.PUBLISHED


def test_a_failed_store_can_be_retried_with_the_same_approval() -> None:
    out = approve(with_state(reviewing(), State.PUBLISH_FAILED))
    assert out.error is None
    assert out.conv.state is State.PUBLISHED
    assert out.action is not None and out.action.kind == "publish"


# --- the check right before publishing ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("yaml_text", "code"),
    [
        ("config_version: '1.0'\nqualification:\n    min_qualify: 90\n", "draft_below_qualify"),
        ("config_version: '1.0'\nsearch:\n    queries_per_run: 30\n", "pages_starved"),
        (
            "config_version: '1.0'\nproject_context:\n    products:\n        - kiosk makers\n",
            "supplier_wording",
        ),
        ("config_version: '1.0'\nexclude_terms: &x\n    - a\nexclude_companies: *x\n", "alias"),
        ("config_version: '1.0'\nexclude_terms: [unclosed", "parse"),
    ],
)
def test_a_candidate_that_no_longer_passes_is_blocked_and_marked_failed(
    yaml_text: str, code: str
) -> None:
    ws = reviewing()
    tampered = Workspace(
        ws.campaign_id,
        ws.name,
        ws.detail,
        ws.ir,
        ws.conv,
        ws.previous_view,
        StoredCandidate(1, "c" * 64, yaml_text),
        ws.versions,
    )
    out = handle(request("approve", {"version": 1, "sha256": "c" * 64}), tampered, svc())
    assert out.error is not None and out.error.code is ErrorCode.PUBLISH_BLOCKED
    assert out.action is None
    assert out.conv.state is State.VALIDATION_FAILED  # the M0 correction: not PUBLISH_FAILED
    assert code in {i["code"] for i in out.result["issues"]}


def test_a_stored_overlay_is_rechecked_from_its_text() -> None:
    ws = reviewing()
    assert validate_overlay_text(ws.candidate.yaml).passed  # type: ignore[union-attr]
    assert not validate_overlay_text("config_version: '9.9'\n").passed
    assert not validate_overlay_text("a: 1\na: 2\n").passed
    report = validate_overlay_text("config_version: '1.0'\n")
    assert [lv.status for lv in report.levels] == ["passed", "passed", "passed", "skipped"]


# --- rollback and stop ------------------------------------------------------------------------------------------------------


def published(*versions: VersionInfo) -> Workspace:
    ws = reviewing()
    return Workspace(
        ws.campaign_id,
        ws.name,
        ws.detail,
        ws.ir,
        ws.conv.model_copy(update={"state": State.PUBLISHED, "has_published": True}),
        ws.previous_view,
        ws.candidate,
        versions,
    )


def test_an_earlier_approved_version_can_be_made_live_again() -> None:
    ws = published(VersionInfo(1, "a" * 64, True, False), VersionInfo(2, "b" * 64, True, True))
    out = handle(request("rollback", {"version": 1}), ws, svc())
    assert out.error is None
    assert out.action is not None and (out.action.kind, out.action.version_no) == ("activate", 1)
    assert out.conv == ws.conv  # the conversation is untouched
    assert out.result["live_version"] == 1


def test_a_version_that_is_already_live_is_a_repeat() -> None:
    ws = published(VersionInfo(1, "a" * 64, True, True))
    out = handle(request("rollback", {"version": 1}), ws, svc())
    assert out.error is None and out.action is None
    assert out.result["already"] is True


def test_a_version_that_was_never_approved_cannot_go_live() -> None:
    ws = published(VersionInfo(1, "a" * 64, True, True), VersionInfo(2, "b" * 64, False, False))
    out = handle(request("rollback", {"version": 2}), ws, svc())
    assert out.error is not None and out.error.code is ErrorCode.WRONG_STATE
    assert out.action is None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"version": 9}, ErrorCode.INVALID_REQUEST),
        ({}, ErrorCode.INVALID_REQUEST),
        ({"version": "1"}, ErrorCode.INVALID_REQUEST),
    ],
)
def test_bad_rollback_requests(payload: dict[str, Any], code: ErrorCode) -> None:
    ws = published(VersionInfo(1, "a" * 64, True, True))
    out = handle(request("rollback", payload), ws, svc())
    assert out.error is not None and out.error.code is code


def test_stopping_a_live_campaign() -> None:
    out = handle(request("deactivate"), published(VersionInfo(1, "a" * 64, True, True)), svc())
    assert out.error is None
    assert out.action is not None and out.action.kind == "deactivate"
    assert out.result["live_version"] is None


def test_stopping_a_campaign_that_is_not_live_is_a_repeat() -> None:
    out = handle(request("deactivate"), published(VersionInfo(1, "a" * 64, True, False)), svc())
    assert out.error is None and out.action is None
    assert out.result["already"] is True


# --- changing a campaign that is already published -------------------------------------------------------------------------


def test_an_edit_in_words_after_publishing_starts_a_new_candidate() -> None:
    from tests.unit.helpers import op_json, reply

    live = published(VersionInfo(1, "a" * 64, True, True))
    edit = reply(op_json("qualification.min_qualify", 55, "minimum score 55", "set"))
    s = Services(Extractor(StubAdapter([edit]), sleep=lambda _s: None))
    out = handle(
        Request("r9", "c1", "edit", {"text": "minimum score 55"}, live.ir.meta.revision, "u"),
        live,
        s,
    )  # type: ignore[union-attr]
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.conv.has_published
    assert out.candidate is not None and "min_qualify: 55" in out.candidate.yaml
    assert out.action is None  # nothing changes what is live until the new candidate is approved


def test_a_direct_edit_after_publishing_also_starts_a_new_candidate() -> None:
    live = published(VersionInfo(1, "a" * 64, True, True))
    ops = [{"path": "exclusions.companies", "op": "add", "value": ["acme"]}]
    out = handle(Request("r9", "c1", "edit", {"ops": ops}, live.ir.meta.revision, "u"), live, svc())  # type: ignore[union-attr]
    assert out.error is None
    assert out.conv.state is State.AWAITING_REVIEW
    assert out.candidate is not None and "acme" in out.candidate.yaml


# --- the Marketing Agent's contract version (M8) ---------------------------------------------------------------


def with_ma(versions: list[str]) -> Services:
    from campaign_agent.ma_status import parse_status

    status = parse_status({"supported_contract_versions": versions, "current_campaign": None})
    return Services(
        Extractor(StubAdapter([COMPLETE_REPLY]), sleep=lambda _s: None), ma_status=lambda: status
    )


def approve_with(svc_: Services):  # type: ignore[no-untyped-def]
    ws = reviewing()
    assert ws.candidate is not None
    payload = {"version": 1, "sha256": ws.candidate.sha256}
    return ws, handle(request("approve", payload), ws, svc_)


def test_a_version_the_marketing_agent_cannot_load_is_not_published() -> None:
    ws, out = approve_with(with_ma(["2.0"]))
    assert out.error is not None and out.error.code is ErrorCode.PUBLISH_BLOCKED
    assert "2.0" in out.error.detail and out.error.result["ma_supported"] == ["2.0"]
    assert out.action is None
    # nothing about the campaign changed; try again after the update
    assert out.conv.state is ws.conv.state


def test_a_version_the_marketing_agent_loads_is_published() -> None:
    _, out = approve_with(with_ma(["1.0", "1.1"]))
    assert out.error is None and out.action is not None


def test_an_unknown_marketing_agent_never_blocks_a_publish() -> None:
    _, out = approve_with(with_ma([]))
    assert out.error is None and out.action is not None

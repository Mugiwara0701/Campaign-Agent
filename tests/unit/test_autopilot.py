from typing import Any

import httpx
from fastapi.testclient import TestClient

from campaign_agent import console_flow
from campaign_agent.autopilot import PRESET_EDIT, PRESET_MESSAGE, run_autopilot
from campaign_agent.console_web import create_app

HDR = {"x-ca-window": "1", "origin": "http://127.0.0.1:8765"}


def _app(monkeypatch: Any) -> TestClient:
    monkeypatch.setattr("campaign_agent.console_web.collect", lambda db, settings: None)
    return TestClient(create_app(object(), object(), 8765), base_url="http://127.0.0.1:8765")  # type: ignore[arg-type]


def test_a_plan_is_handed_to_the_window_once_and_progress_ends_the_run(monkeypatch: Any) -> None:
    c = _app(monkeypatch)
    started = c.post("/api/autopilot", json={"message": "Find robots", "edit": "only Germany"}, headers=HDR).json()
    first = c.get("/api/autopilot").json()
    assert first["state"] == "waiting" and first["plan"]["message"] == "Find robots" and first["plan"]["approve"] is True
    assert first["plan"]["answers"] == "first"
    c.post("/api/autopilot/progress", json={"id": started["id"], "line": "pressed Send"}, headers=HDR)
    after = c.get("/api/autopilot").json()
    assert after["plan"] is None and after["state"] == "running"  # the window has it: it is not handed out again
    assert after["log"][0]["line"] == "pressed Send"
    c.post("/api/autopilot/progress", json={"id": started["id"], "line": "live", "state": "done"}, headers=HDR)
    assert c.get("/api/autopilot").json()["state"] == "done"


def test_a_plan_needs_a_message_and_only_one_runs_at_a_time(monkeypatch: Any) -> None:
    c = _app(monkeypatch)
    assert c.post("/api/autopilot", json={"message": "  "}, headers=HDR).status_code == 400
    assert c.post("/api/autopilot", json={"message": "one"}, headers=HDR).status_code == 200
    assert c.post("/api/autopilot", json={"message": "two"}, headers=HDR).status_code == 409
    assert c.post("/api/autopilot/progress", json={"id": "nope", "line": "x"}, headers=HDR).status_code == 404


def test_the_autopilot_endpoints_need_the_window_header(monkeypatch: Any) -> None:
    assert _app(monkeypatch).post("/api/autopilot", json={"message": "x"}).status_code == 403


def test_the_command_prints_each_step_and_exits_by_the_outcome() -> None:
    log: list[dict[str, str]] = []
    state = {"v": "waiting"}
    posted: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.update(__import__("json").loads(request.content))
            return httpx.Response(200, json={"id": "ab12"})
        log.append({"at": "10:00:00", "line": f"step {len(log) + 1}", "state": "running"})
        if len(log) == 3:
            state["v"] = "done"
        return httpx.Response(200, json={"state": state["v"], "log": list(log)})

    out: list[str] = []
    client = httpx.Client(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(handler))
    code = run_autopilot(
        message=None, edit=None, approve=True, answers=None, timeout_s=60, out=out.append, sleep=lambda _s: None, client=client
    )
    assert code == 0
    assert posted["message"] == PRESET_MESSAGE and posted["edit"] == PRESET_EDIT and posted["approve"] is True
    assert posted["start"] is False  # starting the Marketing Agent is something you ask for (--start-ma)
    assert [line for line in out if "step" in line] == ["  10:00:00  step 1", "  10:00:00  step 2", "  10:00:00  step 3"]


def test_a_message_without_an_edit_sends_no_edit_and_a_failed_step_exits_1() -> None:
    sent: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.update(__import__("json").loads(request.content))
            return httpx.Response(200, json={"id": "x"})
        return httpx.Response(200, json={"state": "failed", "log": [{"at": "1", "line": "the request failed", "state": "failed"}]})

    client = httpx.Client(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(handler))
    code = run_autopilot(
        message="my own text", edit=None, approve=False, answers=["EV charging"], timeout_s=60, out=lambda _t: None,
        sleep=lambda _s: None, client=client,
    )
    assert code == 1 and sent["edit"] is None and sent["approve"] is False and sent["answers"] == ["EV charging"]


def test_no_window_is_said_plainly_and_exits_2() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    out: list[str] = []
    client = httpx.Client(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(handler))
    code = run_autopilot(message=None, edit=None, approve=True, answers=None, timeout_s=60, out=out.append, client=client)
    assert code == 2 and "campaign_agent ui" in out[0]


def test_an_edit_goes_against_the_revision_the_ceo_was_shown(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    class View:
        revision = 4
        candidate = object()

    monkeypatch.setattr(console_flow, "_view", lambda db, cid: View())
    monkeypatch.setattr(console_flow, "_enqueue", lambda db, cid, kind, payload=None, base_revision=None: seen.update(kind=kind, payload=payload, base=base_revision))
    console_flow.edit(object(), "cid", "  also   Austria  ")  # type: ignore[arg-type]
    assert seen == {"kind": "edit", "payload": {"text": "also Austria"}, "base": 4}
    import pytest

    with pytest.raises(ValueError, match="empty"):
        console_flow.edit(object(), "cid", "   ")  # type: ignore[arg-type]


def test_a_restarted_window_resumes_the_latest_campaign_from_the_database(monkeypatch: Any) -> None:
    """The state lives in the database: a new page asks for the latest campaign instead of starting blank."""

    class Conn:
        def execute(self, sql: str, params: Any = ()) -> Any:
            row = {"campaign_id": "cid-1", "campaign_name": "[CA-TEST] Find robots", "campaign_detail": "Find robots in Germany"}
            return type("R", (), {"fetchone": lambda self: row})()

    class Db:
        def transaction(self) -> Any:
            from contextlib import contextmanager

            @contextmanager
            def cm() -> Any:
                yield Conn()

            return cm()

    monkeypatch.setattr(console_flow, "status", lambda db, cid: {"campaign_id": cid, "state": "PUBLISHED", "live": True})
    out = console_flow.latest(Db())  # type: ignore[arg-type]
    assert out == {"campaign_id": "cid-1", "state": "PUBLISHED", "live": True, "name": "Find robots", "message": "Find robots in Germany"}


def test_no_earlier_campaign_means_a_fresh_start(monkeypatch: Any) -> None:
    class Conn:
        def execute(self, sql: str, params: Any = ()) -> Any:
            return type("R", (), {"fetchone": lambda self: None})()

    class Db:
        def transaction(self) -> Any:
            from contextlib import contextmanager

            @contextmanager
            def cm() -> Any:
                yield Conn()

            return cm()

    assert console_flow.latest(Db()) is None  # type: ignore[arg-type]

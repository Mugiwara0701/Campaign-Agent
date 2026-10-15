from typing import Any

from fastapi.testclient import TestClient

from campaign_agent.console import Line, Snapshot
from campaign_agent.console_page import PAGE
from campaign_agent.console_web import create_app, find_chromium


def _client(monkeypatch: Any) -> TestClient:
    snap = Snapshot((Line("Database", "ok", "reachable"),), ("a request",), ())
    monkeypatch.setattr("campaign_agent.console_web.collect", lambda db, settings: snap)
    return TestClient(create_app(object(), object(), 8765), base_url="http://127.0.0.1:8765")  # type: ignore[arg-type]


def test_state_and_page_on_this_machine(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    assert c.get("/").text == PAGE
    body = c.get("/api/state").json()
    assert body["lines"][0]["name"] == "Database" and body["requests"] == ["a request"]
    assert len(c.get("/api/demo").json()) > 5
    assert c.get("/docs").status_code == 404


def test_another_host_header_is_refused(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    assert c.get("/api/state", headers={"host": "evil.example"}).status_code == 403


def test_page_marks_the_demo_as_simulated() -> None:
    assert "SIMULATED" in PAGE


def test_find_chromium_takes_the_first_installed() -> None:
    assert (
        find_chromium(lambda n: "/usr/bin/chromium-browser" if n == "chromium-browser" else None)
        == "/usr/bin/chromium-browser"
    )
    assert find_chromium(lambda n: None) is None


def test_page_has_the_office_scene_and_no_outside_loads() -> None:
    assert "<canvas" in PAGE and "MARKETING FLOOR" in PAGE and "CAMPAIGN OFFICE" in PAGE
    assert "http://" not in PAGE and "https://" not in PAGE


def test_window_check_passes_the_pattern_after_a_double_dash() -> None:
    from types import SimpleNamespace

    from campaign_agent.console_web import window_is_open

    seen: list[list[str]] = []

    def run(cmd: list[str], **_k: object) -> SimpleNamespace:
        seen.append(cmd)
        return SimpleNamespace(returncode=0)

    assert window_is_open("http://127.0.0.1:8765/", run)
    assert seen[0][:3] == ["pgrep", "-f", "--"]


def test_closing_the_window_quits_but_a_quick_handoff_does_not() -> None:
    from types import SimpleNamespace

    from campaign_agent.console_web import window_closed

    open_window = SimpleNamespace(poll=lambda: None)
    gone = SimpleNamespace(poll=lambda: 0)
    assert not window_closed(None, 0.0, 100.0)
    assert not window_closed(open_window, 0.0, 100.0)  # type: ignore[arg-type]
    assert not window_closed(gone, 0.0, 2.0)  # type: ignore[arg-type]  # handed over to an existing window
    assert window_closed(gone, 0.0, 60.0)  # type: ignore[arg-type]  # the person closed it


def test_writes_need_the_window_header_and_the_same_origin(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    monkeypatch.setattr(
        "campaign_agent.console_web.console_flow.start", lambda db, message: "cid-1"
    )
    assert c.post("/api/campaigns", json={"message": "x"}).status_code == 403
    assert (
        c.post(
            "/api/campaigns",
            json={"message": "x"},
            headers={"x-ca-window": "1", "origin": "http://evil.example"},
        ).status_code
        == 403
    )
    ok = c.post(
        "/api/campaigns",
        json={"message": "x"},
        headers={"x-ca-window": "1", "origin": "http://127.0.0.1:8765"},
    )
    assert ok.status_code == 200 and ok.json() == {"campaign_id": "cid-1"}


def test_the_ceo_window_cannot_start_the_pipeline_without_the_grant() -> None:
    import psycopg

    from campaign_agent import console_flow

    class Db:
        def transaction(self) -> Any:
            raise psycopg.errors.InsufficientPrivilege("no")

    assert console_flow.pipeline(Db(), "running") == {"ok": False, "reason": "needs_grant"}  # type: ignore[arg-type]


def test_a_long_campaign_message_gets_a_short_name_cut_between_words() -> None:
    from campaign_agent.console_flow import short_name

    text = "Find automotive and industrial automation companies in Germany that need embedded Linux and Yocto help."
    name = short_name(text)
    assert len(name) <= 63 and name.endswith("...") and not name.removesuffix("...").endswith(" ")
    assert text.startswith(name.removesuffix("..."))
    assert text.split(name.removesuffix("..."))[1].startswith(
        " "
    )  # it stopped at a space, not inside "Germany"
    assert short_name("short one") == "short one"


def test_the_window_opens_full_screen_unless_told_otherwise() -> None:
    from campaign_agent.console_web import window_command

    full = window_command("/usr/bin/google-chrome", "http://127.0.0.1:8765/")
    assert "--start-fullscreen" in full and "--app=http://127.0.0.1:8765/" in full
    assert "--start-fullscreen" not in window_command(
        "chrome", "http://127.0.0.1:8765/", fullscreen=False
    )


def test_the_state_carries_the_jobs_for_the_office_window(monkeypatch: Any) -> None:
    job = {
        "id": "r1",
        "type": "interpret",
        "status": "pending",
        "by": "priya@example.com",
        "name": "EV charger",
        "state": None,
        "age_s": 1,
        "error": None,
    }
    snap = Snapshot((Line("Database", "ok", "reachable"),), (), (), (job,))
    monkeypatch.setattr("campaign_agent.console_web.collect", lambda db, settings: snap)
    c = TestClient(create_app(object(), object(), 8765), base_url="http://127.0.0.1:8765")  # type: ignore[arg-type]
    assert c.get("/api/state").json()["jobs"] == [job]

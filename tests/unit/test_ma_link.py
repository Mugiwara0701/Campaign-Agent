"""The link routes the Marketing Agent calls: token, size limits, the live campaign (with the long poll) and events."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from campaign_agent.ma_link.app import create_app
from campaign_agent.ma_link.auth import token_ok
from campaign_agent.ma_link.events import MAX_BODY_BYTES, EventIn, parse_batch, parse_event
from campaign_agent.ma_link.server import resolve_bind, validate_bind
from campaign_agent.ma_link.service import LiveCampaign
from tests.unit.fake_secrets import AWS

TOKEN = "a" * 40
PREVIOUS = "b" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SHA = "0123456789abcdef" * 4


def live(version: int = 1, sha: str = SHA) -> LiveCampaign:
    return LiveCampaign(
        campaign_id="11111111-1111-1111-1111-111111111111",
        version=version,
        sha256=sha,
        contract_version="1.0",
        overlay_yaml="config_version: '1.0'\n",
        approved_by="priya@example.com",
        approved_at="2026-10-20T09:15:00+00:00",
    )


class FakeBackend:
    def __init__(self, states: list[LiveCampaign | None]) -> None:
        self.states = states
        self.calls = 0
        self.stored: list[EventIn] = []

    def live_campaign(self) -> LiveCampaign | None:
        value = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        return value

    def store_events(self, events: list[EventIn]) -> list[str]:
        self.stored.extend(events)
        return [str(e.event_id) for e in events]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.01)


def client(backend: FakeBackend, clock: Clock | None = None) -> TestClient:
    clock = clock or Clock()
    app = create_app(backend, token=TOKEN, token_previous=PREVIOUS, sleep=clock.sleep, clock=clock)
    return TestClient(app)


def event(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": "2026-10-20T09:20:11Z",
        "kind": "blocker",
        "severity": "error",
        "code": "MA_CHROME_FAILED",
        "message": "Chrome did not start after 3 tries",
    }
    base.update(over)
    return base


# --- the token ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header", [None, "", "Bearer", "Bearer ", "Basic " + TOKEN, "Bearer " + "c" * 40, TOKEN]
)
def test_a_wrong_or_missing_token_is_refused(header: str | None) -> None:
    assert not token_ok(header, TOKEN, PREVIOUS)


def test_the_current_and_the_previous_token_are_accepted() -> None:
    assert token_ok(f"Bearer {TOKEN}", TOKEN, PREVIOUS)
    assert token_ok(f"bearer {PREVIOUS}", TOKEN, PREVIOUS)
    assert not token_ok(f"Bearer {PREVIOUS}", TOKEN, None)


def test_every_route_needs_the_token_even_an_unknown_one() -> None:
    c = client(FakeBackend([None]))
    for path in (
        "/ma/v1/health",
        "/ma/v1/campaign/active",
        "/ma/v1/nothing",
        "/docs",
        "/openapi.json",
    ):
        assert c.get(path).status_code == 401
    assert c.post("/ma/v1/events", json={"events": [event()]}).status_code == 401


def test_health_with_the_token() -> None:
    r = client(FakeBackend([None])).get("/ma/v1/health", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["api"] == "1"
    assert r.headers["cache-control"] == "no-store"


def test_there_is_no_documentation_page_even_with_the_token() -> None:
    c = client(FakeBackend([None]))
    assert c.get("/docs", headers=AUTH).status_code == 404
    assert c.get("/openapi.json", headers=AUTH).status_code == 404


# --- the live campaign -------------------------------------------------------------------------------------------


def test_nothing_live_is_204_with_the_none_tag() -> None:
    r = client(FakeBackend([None])).get("/ma/v1/campaign/active", headers=AUTH)
    assert r.status_code == 204
    assert r.headers["etag"] == '"none"'


def test_the_live_campaign_is_served_with_its_hash_and_tag() -> None:
    r = client(FakeBackend([live()])).get("/ma/v1/campaign/active", headers=AUTH)
    body = r.json()
    assert r.status_code == 200
    assert (body["version"], body["sha256"], body["contract_version"]) == (1, SHA, "1.0")
    assert body["overlay_yaml"].startswith("config_version")
    assert r.headers["etag"] == f'"{live().tag}"'


def test_an_unchanged_campaign_is_304_without_waiting() -> None:
    backend = FakeBackend([live()])
    headers = {**AUTH, "If-None-Match": f'"{live().tag}"'}
    r = client(backend).get("/ma/v1/campaign/active", headers=headers)
    assert r.status_code == 304
    assert backend.calls == 1


def test_a_rollback_to_a_version_with_other_text_changes_the_tag() -> None:
    assert live(1, SHA).tag != live(2, SHA).tag
    assert live(1, SHA).tag != live(1, "f" * 64).tag


def test_the_long_poll_returns_when_the_live_version_changes() -> None:
    backend = FakeBackend([live(1), live(1), live(2)])
    headers = {**AUTH, "If-None-Match": f'"{live(1).tag}"'}
    r = client(backend).get("/ma/v1/campaign/active?wait=25", headers=headers)
    assert r.status_code == 200 and r.json()["version"] == 2


def test_the_long_poll_returns_204_when_the_campaign_is_stopped() -> None:
    backend = FakeBackend([live(1), None])
    headers = {**AUTH, "If-None-Match": f'"{live(1).tag}"'}
    assert client(backend).get("/ma/v1/campaign/active?wait=10", headers=headers).status_code == 204


def test_the_long_poll_gives_up_with_304_and_never_waits_past_the_limit() -> None:
    clock = Clock()
    backend = FakeBackend([live()])
    headers = {**AUTH, "If-None-Match": f'"{live().tag}"'}
    r = client(backend, clock).get("/ma/v1/campaign/active?wait=9999", headers=headers)
    assert r.status_code == 304
    assert 29 <= clock.now <= 32  # capped at 30 seconds


def test_without_a_known_tag_the_answer_is_immediate() -> None:
    clock = Clock()
    r = client(FakeBackend([live()]), clock).get("/ma/v1/campaign/active?wait=25", headers=AUTH)
    assert r.status_code == 200 and clock.now == 0


# --- events ------------------------------------------------------------------------------------------------------


def test_events_are_stored_and_listed_as_accepted() -> None:
    backend = FakeBackend([None])
    first, second = event(), event(kind="info", severity="info", code="MA_CAMPAIGN_APPLIED")
    r = client(backend).post("/ma/v1/events", headers=AUTH, json={"events": [first, second]})
    assert r.status_code == 200
    assert r.json() == {"accepted": [first["event_id"], second["event_id"]], "rejected": []}
    assert [e.code for e in backend.stored] == ["MA_CHROME_FAILED", "MA_CAMPAIGN_APPLIED"]


def test_the_dedupe_key_defaults_to_the_code() -> None:
    parsed, _ = parse_event(event())
    assert parsed is not None and parsed.key == "MA_CHROME_FAILED"
    parsed, _ = parse_event(event(dedupe_key="chrome-1"))
    assert parsed is not None and parsed.key == "chrome-1"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"code": "CA_ANYTHING"}, "invalid:code"),
        ({"code": "ma_lower"}, "invalid:code"),
        ({"kind": "command"}, "invalid:kind"),
        ({"severity": "fatal"}, "invalid:severity"),
        ({"message": ""}, "invalid:message"),
        ({"message": "x" * 301}, "invalid:message"),
        ({"event_id": "not-a-uuid"}, "invalid:event_id"),
        ({"occurred_at": "2026-10-20T09:20:11"}, "invalid:occurred_at"),  # no time zone
        ({"unknown_field": 1}, "invalid:unknown_field"),
        ({"message": "key is " + AWS}, "secret_like"),
        ({"details": {"note": AWS}}, "secret_like"),
        ({"details": {"blob": "x" * 3000}}, "details_too_big"),
    ],
)
def test_a_bad_event_is_rejected_with_a_reason_and_the_others_still_stored(
    over: dict[str, Any], reason: str
) -> None:
    backend = FakeBackend([None])
    bad, good = event(**over), event()
    r = client(backend).post("/ma/v1/events", headers=AUTH, json={"events": [bad, good]})
    assert r.status_code == 200
    assert r.json()["accepted"] == [good["event_id"]]
    assert [x["reason"] for x in r.json()["rejected"]] == [reason]
    assert len(backend.stored) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"events": []},
        {"events": "x"},
        {"events": [event()] * 51},
        {"events": [event()], "extra": 1},
        [event()],
    ],
)
def test_a_malformed_batch_is_422_and_nothing_is_stored(body: Any) -> None:
    backend = FakeBackend([None])
    r = client(backend).post("/ma/v1/events", headers=AUTH, json=body)
    assert r.status_code == 422 and backend.stored == []


def test_text_that_is_not_json_is_422() -> None:
    r = client(FakeBackend([None])).post("/ma/v1/events", headers=AUTH, content=b"{nope")
    assert r.status_code == 422


def test_a_body_over_the_limit_is_413_and_nothing_is_stored() -> None:
    backend = FakeBackend([None])
    big = b'{"events": [], "pad": "' + b"x" * (MAX_BODY_BYTES + 10) + b'"}'
    r = client(backend).post("/ma/v1/events", headers=AUTH, content=big)
    assert r.status_code == 413 and backend.stored == []


def test_exactly_fifty_events_are_allowed() -> None:
    parsed = parse_batch({"events": [event() for _ in range(50)]})
    assert parsed is not None and len(parsed[0]) == 50 and parsed[1] == []


# --- where the listener binds ------------------------------------------------------------------------------------


class Done:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode, self.stdout = returncode, stdout


def test_a_literal_address_is_used_and_the_wildcard_is_refused() -> None:
    assert validate_bind("100.101.102.103") == "100.101.102.103"
    assert validate_bind("tailscale") is None
    for bad in ("0.0.0.0", "::", "", "not-an-address", "example.com"):  # noqa: S104
        with pytest.raises(ValueError, match="MA_LINK_BIND"):
            validate_bind(bad)


def test_the_tailscale_address_comes_from_the_tailscale_command() -> None:
    assert resolve_bind("tailscale", run=lambda *a, **k: Done(0, "100.64.0.7\n")) == "100.64.0.7"


def test_it_falls_back_to_the_interface_when_the_command_is_refused() -> None:
    def run(command: list[str], **_k: Any) -> Done:
        if command[0] == "tailscale":
            return Done(1, "")
        return Done(0, "5: tailscale0    inet 100.64.0.9/32 scope global tailscale0\n")

    assert resolve_bind("tailscale", run=run) == "100.64.0.9"


def test_no_tailscale_address_yet_is_none_not_an_error() -> None:
    def run(*_a: Any, **_k: Any) -> Done:
        raise FileNotFoundError

    assert resolve_bind("tailscale", run=run) is None

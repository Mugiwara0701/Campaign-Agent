from typing import Any

import pytest
from fastapi.testclient import TestClient

from campaign_agent.api.auth import bearer, token_ok
from campaign_agent.api.service import NotFound, Unavailable
from campaign_agent.main import create_app

TOKEN = "t" * 40
CID = "6f1d3a52-0b0e-4f6e-8c52-1f1d9f0f9a11"
GOOD = {"Authorization": f"Bearer {TOKEN}"}


class FakeGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, ...]] = []
        self.fail: Exception | None = None

    def _maybe(self) -> None:
        if self.fail:
            raise self.fail

    def send_request(self, *args: Any) -> dict[str, Any]:
        self._maybe()
        self.sent.append(args)
        return {"request_id": "r1", "status": "pending", "duplicate": False}

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        self._maybe()
        return {
            "campaign_id": campaign_id,
            "state": "NEW",
            "revision": None,
            "review_view": None,
            "last_request": None,
        }

    def request_status(self, request_id: str) -> dict[str, Any]:
        self._maybe()
        return {"id": request_id, "status": "done"}

    def ca_status(self) -> dict[str, Any]:
        self._maybe()
        return {"online": True, "status": "idle", "last_seen_s_ago": 1.0, "control": None}

    def set_ca_state(self, state: str, by: str) -> dict[str, Any]:
        self._maybe()
        return {"desired_state": state, "actual_state": "running"}


@pytest.fixture
def gw() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def client(gw: FakeGateway) -> TestClient:
    return TestClient(create_app(gw, token=TOKEN))  # type: ignore[arg-type]


def test_the_token_helpers() -> None:
    assert token_ok(TOKEN, TOKEN) and not token_ok("x" * 40, TOKEN) and not token_ok(None, TOKEN)
    assert not token_ok("short", "short")  # a token too short to be a secret never opens anything
    assert (
        bearer("Bearer abc") == "abc"
        and bearer("bearer  abc ") == "abc"
        and bearer("Basic abc") is None
        and bearer(None) is None
    )


def test_health_needs_no_token_and_everything_else_does(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    for method, path in [
        ("get", f"/v1/campaigns/{CID}"),
        ("get", "/v1/ca"),
        ("get", "/v1/requests/" + CID),
        ("post", f"/v1/campaigns/{CID}/requests"),
        ("post", "/v1/ca/state"),
    ]:
        assert getattr(client, method)(path).status_code == 401
        assert (
            getattr(client, method)(path, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )


def test_a_short_configured_token_refuses_everyone(gw: FakeGateway) -> None:
    c = TestClient(create_app(gw, token="short"))  # type: ignore[arg-type]  # noqa: S106
    assert c.get("/v1/ca", headers={"Authorization": "Bearer short"}).status_code == 401


def test_docs_and_the_schema_are_not_public(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_an_interpret_request_is_recorded_and_returns_at_once(
    client: TestClient, gw: FakeGateway
) -> None:
    r = client.post(
        f"/v1/campaigns/{CID}/requests",
        headers=GOOD,
        json={
            "request_type": "interpret",
            "requested_by": " priya@example.com ",
            "idempotency_key": "k1",
        },
    )
    assert r.status_code == 202 and r.json()["request_id"] == "r1"
    assert gw.sent == [(CID, "interpret", {}, None, "priya@example.com", "k1")]


def test_approve_carries_its_payload_untouched(client: TestClient, gw: FakeGateway) -> None:
    body = {
        "request_type": "approve",
        "requested_by": "priya@example.com",
        "payload": {"version": 2, "sha256": "ab" * 32, "start_pipeline": True},
    }
    assert client.post(f"/v1/campaigns/{CID}/requests", headers=GOOD, json=body).status_code == 202
    assert gw.sent[0][2] == {"version": 2, "sha256": "ab" * 32, "start_pipeline": True}


@pytest.mark.parametrize(
    "body",
    [
        {
            "request_type": "start_the_marketing_agent",
            "requested_by": "x",
        },  # not a request the Campaign Agent knows
        {"request_type": "interpret"},  # nobody to blame
        {"request_type": "interpret", "requested_by": "   "},
        {"request_type": "interpret", "requested_by": "x" * 101},
        {
            "request_type": "interpret",
            "requested_by": "x",
            "surprise": 1,
        },  # unknown fields are refused
        {"request_type": "edit", "requested_by": "x", "base_revision": -1},
        {"request_type": "answer", "requested_by": "x", "payload": {"a": "x" * 70_000}},
    ],
)
def test_bad_requests_are_refused_before_anything_is_written(
    client: TestClient, gw: FakeGateway, body: dict[str, Any]
) -> None:
    assert client.post(f"/v1/campaigns/{CID}/requests", headers=GOOD, json=body).status_code == 422
    assert gw.sent == []


def test_a_campaign_id_must_be_a_uuid(client: TestClient) -> None:
    assert client.get("/v1/campaigns/not-a-uuid", headers=GOOD).status_code == 422


def test_not_found_is_404_and_a_database_outage_is_503_without_details(
    client: TestClient, gw: FakeGateway
) -> None:
    gw.fail = NotFound()
    assert client.get(f"/v1/campaigns/{CID}", headers=GOOD).status_code == 404
    gw.fail = Unavailable()
    r = client.get("/v1/ca", headers=GOOD)
    assert (
        r.status_code == 503 and "host" not in r.text.lower() and "password" not in r.text.lower()
    )


def test_pausing_goes_through(client: TestClient) -> None:
    r = client.post(
        "/v1/ca/state", headers=GOOD, json={"state": "paused", "requested_by": "priya@example.com"}
    )
    assert r.status_code == 200 and r.json()["desired_state"] == "paused"
    assert (
        client.post(
            "/v1/ca/state", headers=GOOD, json={"state": "exploding", "requested_by": "x"}
        ).status_code
        == 422
    )


# --- the signed-in person (api/users.py, audit F-18) ---------------------------------------------------------------------

SECRET = "s" * 40


def _user_token(role: str | None = "admin", email: str = "Priya@Example.com", **over: Any) -> str:
    import time

    import jwt

    claims: dict[str, Any] = {
        "sub": "u1",
        "email": email,
        "aud": "authenticated",
        "exp": int(time.time()) + 600,
        "app_metadata": {"role": role} if role else {},
        **over,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


@pytest.fixture
def strict(gw: FakeGateway) -> TestClient:
    from campaign_agent.api.users import UserVerifier

    return TestClient(
        create_app(gw, token=TOKEN, users=UserVerifier(jwt_secret=SECRET), require_user=True)  # type: ignore[arg-type]
    )


def test_the_approval_record_is_the_verified_person_not_what_the_body_says(
    strict: TestClient, gw: FakeGateway
) -> None:
    body = {
        "request_type": "interpret",
        "requested_by": "someone-else@example.com",
        "idempotency_key": "k",
    }
    r = strict.post(
        f"/v1/campaigns/{CID}/requests", headers={**GOOD, "X-User-Token": _user_token()}, json=body
    )
    assert r.status_code == 202
    assert gw.sent[0][4] == "priya@example.com"


@pytest.mark.parametrize(
    "token",
    [
        None,  # required, and missing
        "not-a-jwt",
        "EXPIRED",
        "NO_ROLE",
    ],
)
def test_a_missing_or_bad_user_token_is_refused(
    strict: TestClient, gw: FakeGateway, token: str | None
) -> None:
    value = {
        "EXPIRED": _user_token(exp=1),
        "NO_ROLE": _user_token(role=None),
    }.get(token or "", token)
    headers = {**GOOD, **({"X-User-Token": value} if value else {})}
    body = {"request_type": "interpret", "requested_by": "x"}
    assert (
        strict.post(f"/v1/campaigns/{CID}/requests", headers=headers, json=body).status_code == 401
    )
    assert gw.sent == []


def test_only_an_admin_changes_a_campaign_or_resumes_but_anyone_may_pause(
    strict: TestClient, gw: FakeGateway
) -> None:
    employee = {**GOOD, "X-User-Token": _user_token(role="employee")}
    body = {
        "request_type": "approve",
        "requested_by": "x",
        "payload": {"version": 1, "sha256": "a" * 64},
    }
    assert (
        strict.post(f"/v1/campaigns/{CID}/requests", headers=employee, json=body).status_code == 403
    )
    assert gw.sent == []
    pause = {"state": "paused", "requested_by": "x"}
    assert strict.post("/v1/ca/state", headers=employee, json=pause).status_code == 200
    resume = {"state": "running", "requested_by": "x"}
    assert strict.post("/v1/ca/state", headers=employee, json=resume).status_code == 403


def test_without_require_user_the_old_dashboard_still_works(
    client: TestClient, gw: FakeGateway
) -> None:
    body = {"request_type": "interpret", "requested_by": "priya@example.com"}
    assert client.post(f"/v1/campaigns/{CID}/requests", headers=GOOD, json=body).status_code == 202

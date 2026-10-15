"""A campaign deleted while its request is being worked on must not take the worker down."""

from contextlib import contextmanager
from typing import Any

import pytest

from campaign_agent.jobs import manager
from campaign_agent.store import repositories as repo


class _Conn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def execute(self, sql: str, params: Any = ()) -> Any:
        return type("R", (), {"fetchone": lambda _self: self.row})()


def test_releasing_a_request_that_no_longer_exists_says_gone_instead_of_asserting() -> None:
    assert repo.release_request(_Conn(None), "rid", "ForeignKeyViolation", 3) == "gone"  # type: ignore[arg-type]
    assert repo.release_request(_Conn({"status": "pending"}), "rid", "x", 3) == "pending"  # type: ignore[arg-type]


class _Db:
    @contextmanager
    def transaction(self) -> Any:
        yield object()


def _claim(monkeypatch: pytest.MonkeyPatch, released: Any) -> list[Any]:
    """One claimed request whose handling crashes; `released` is what releasing it does."""
    row = {"id": "r1", "campaign_id": "c1", "request_type": "interpret", "payload": {}, "base_revision": None, "requested_by": "ceo"}
    calls: list[Any] = [row]
    monkeypatch.setattr(manager.repo, "requeue_expired", lambda conn, n: None)
    monkeypatch.setattr(manager.repo, "claim_request", lambda conn, w, lease: calls.pop() if calls else None)
    monkeypatch.setattr(manager.repo, "get_campaign", lambda conn, cid: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(manager.repo, "release_request", released)
    return calls


def _cfg() -> Any:
    return manager.ManagerConfig(worker_id="w", lease_s=60, max_attempts=3, rate_limit_per_minute=0)


def test_a_request_whose_campaign_was_deleted_is_dropped_and_the_worker_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _claim(monkeypatch, lambda conn, rid, detail, n: "gone")
    assert manager.process_next(_Db(), object(), _cfg()) is True  # type: ignore[arg-type]  # no exception: the loop continues


def test_even_a_failing_release_does_not_end_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(conn: Any, rid: str, detail: str, n: int) -> str:
        raise AssertionError("the old behaviour")

    _claim(monkeypatch, broken)
    assert manager.process_next(_Db(), object(), _cfg()) is True  # type: ignore[arg-type]

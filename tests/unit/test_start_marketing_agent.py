from typing import Any

from campaign_agent.jobs import manager


class _Db:
    def transaction(self) -> Any:
        from contextlib import contextmanager

        @contextmanager
        def cm() -> Any:
            yield object()

        return cm()


class _Pipeline:
    """The Marketing Agent's switch, as the database shows it: a wish and what it does about it."""

    def __init__(
        self, desired: str, actual: str, *, stops_after: int | None = 0, allowed: bool = True
    ) -> None:
        self.desired, self.actual = desired, actual
        self.stops_after, self.allowed = stops_after, allowed
        self.asked: list[str] = []
        self.reads = 0

    def state(self, conn: Any) -> dict[str, str]:
        self.reads += 1
        if (
            self.desired == "stopped"
            and self.stops_after is not None
            and self.reads > self.stops_after
        ):
            self.actual = "stopped"
        return {"desired_state": self.desired, "actual_state": self.actual}

    def request(self, conn: Any, state: str, by: str) -> bool:
        if not self.allowed:
            return False
        self.asked.append(state)
        self.desired = state
        return True


def _run(monkeypatch: Any, p: _Pipeline, **kw: Any) -> dict[str, Any]:
    monkeypatch.setattr(manager.repo, "pipeline_state", p.state)
    monkeypatch.setattr(manager.repo, "request_pipeline", p.request)
    clock = iter(range(0, 10_000, 3))
    return manager.start_marketing_agent(
        _Db(), "priya@example.com", sleep=lambda s: None, clock=lambda: float(next(clock)), **kw
    )  # type: ignore[arg-type]


def test_a_stopped_pipeline_is_simply_started(monkeypatch: Any) -> None:
    p = _Pipeline("stopped", "stopped")
    assert _run(monkeypatch, p) == {"asked": True, "restarted": False}
    assert p.asked == ["running"]


def test_a_running_pipeline_is_stopped_then_started_so_it_takes_the_new_campaign(
    monkeypatch: Any,
) -> None:
    p = _Pipeline("running", "running", stops_after=2)
    assert _run(monkeypatch, p) == {"asked": True, "restarted": True}
    assert p.asked == ["stopped", "running"]  # in this order, with the stop seen in between


def test_if_it_never_stops_it_is_asked_to_run_again_not_left_stopped(monkeypatch: Any) -> None:
    p = _Pipeline("running", "running", stops_after=None)
    out = _run(monkeypatch, p, wait_s=30.0)
    assert p.asked == ["stopped", "running"] and p.desired == "running"
    assert out["asked"] is True and out["restarted"] is True


def test_without_the_grant_nothing_is_asked_and_it_says_why(monkeypatch: Any) -> None:
    p = _Pipeline("stopped", "stopped", allowed=False)
    assert _run(monkeypatch, p) == {"asked": False, "restarted": False, "reason": "not_allowed"}


def test_an_unreadable_pipeline_table_is_reported(monkeypatch: Any) -> None:
    monkeypatch.setattr(manager.repo, "pipeline_state", lambda conn: None)
    assert manager.start_marketing_agent(_Db(), "x") == {
        "asked": False,
        "reason": "pipeline_not_readable",
    }  # type: ignore[arg-type]


def test_switching_a_campaign_off_stops_a_running_marketing_agent(monkeypatch: Any) -> None:
    p = _Pipeline("running", "running")
    monkeypatch.setattr(manager.repo, "pipeline_state", p.state)
    monkeypatch.setattr(manager.repo, "request_pipeline", p.request)
    assert manager.stop_marketing_agent(_Db(), "priya@example.com") == {"asked": True}  # type: ignore[arg-type]
    assert p.asked == ["stopped"]


def test_a_marketing_agent_that_is_already_stopped_is_left_alone(monkeypatch: Any) -> None:
    p = _Pipeline("stopped", "stopped")
    monkeypatch.setattr(manager.repo, "pipeline_state", p.state)
    monkeypatch.setattr(manager.repo, "request_pipeline", p.request)
    assert manager.stop_marketing_agent(_Db(), "x") == {"asked": False, "already_stopped": True}  # type: ignore[arg-type]
    assert p.asked == []


def test_stopping_without_the_grant_or_the_table_says_why(monkeypatch: Any) -> None:
    p = _Pipeline("running", "running", allowed=False)
    monkeypatch.setattr(manager.repo, "pipeline_state", p.state)
    monkeypatch.setattr(manager.repo, "request_pipeline", p.request)
    assert manager.stop_marketing_agent(_Db(), "x") == {"asked": False, "reason": "not_allowed"}  # type: ignore[arg-type]
    monkeypatch.setattr(manager.repo, "pipeline_state", lambda conn: None)
    assert manager.stop_marketing_agent(_Db(), "x") == {
        "asked": False,
        "reason": "pipeline_not_readable",
    }  # type: ignore[arg-type]

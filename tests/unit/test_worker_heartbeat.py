import threading
import time
from contextlib import contextmanager
from typing import Any

import pytest

from campaign_agent.jobs import worker as worker_module
from campaign_agent.jobs.worker import Worker
from campaign_agent.store.repositories import Control


@pytest.fixture(autouse=True)
def _running_unless_a_test_says_otherwise(monkeypatch: Any) -> None:
    monkeypatch.setattr(worker_module.repo, "read_control", lambda conn: Control("running", None))
    monkeypatch.setattr(worker_module.repo, "report_actual_state", lambda conn, state: None)


class _RunCfg:
    """What the worker reads from its config while it runs: a name for the log, and no automatic start."""

    worker_id = "test-worker"
    auto_interpret = False


class _Db:
    def __init__(self) -> None:
        self.closed = False

    @contextmanager
    def transaction(self) -> Any:
        yield object()

    def clone(self) -> "_Db":
        return _Db()

    def close(self) -> None:
        self.closed = True


def test_the_heartbeat_keeps_beating_while_a_long_request_runs(monkeypatch: Any) -> None:
    """A request that takes minutes must not make the Campaign Agent look stopped: the beat comes from its own thread."""
    beats: list[str] = []
    monkeypatch.setattr(worker_module, "HEARTBEAT_S", 0.02)
    monkeypatch.setattr(worker_module.repo, "beat", lambda conn, **kw: beats.append(kw["status"]))
    started, release = threading.Event(), threading.Event()

    def slow_request(db: Any, svc: Any, cfg: Any) -> bool:
        started.set()
        release.wait(5)
        return True

    monkeypatch.setattr(worker_module, "process_next", slow_request)
    w = Worker(_Db(), None, _RunCfg(), model_name="m", poll_interval_s=0.01)  # type: ignore[arg-type]
    runner = threading.Thread(target=w.run_forever)
    runner.start()
    assert started.wait(5)
    during = len(beats)
    time.sleep(0.2)  # the request is still running: only the heartbeat thread can add beats
    assert len(beats) > during and "busy" in beats[during:]
    w.stop()
    release.set()
    runner.join(5)
    assert not runner.is_alive()


def test_stop_does_not_wait_for_a_heartbeat_stuck_in_a_network_call(monkeypatch: Any) -> None:
    """The Campaign Agent was seen hanging in `stop` because the heartbeat thread sat in a dead connection and the main
    thread waited to close it. A stop must finish anyway."""
    monkeypatch.setattr(worker_module, "HEARTBEAT_S", 0.01)
    monkeypatch.setattr(worker_module, "BEAT_JOIN_S", 0.2)
    stuck = threading.Event()

    class StuckDb(_Db):
        closed_by_main = False

        def clone(self) -> "StuckDb":
            return StuckDb()

        @contextmanager
        def transaction(self) -> Any:
            stuck.wait(10)  # a call that never answers
            yield object()

        def close(self) -> None:
            StuckDb.closed_by_main = True

    monkeypatch.setattr(worker_module, "process_next", lambda db, svc, cfg: False)
    monkeypatch.setattr(worker_module, "refresh_applied", lambda db: None)  # an idle cycle looks at the MA's report
    monkeypatch.setattr(worker_module.repo, "beat", lambda conn, **kw: None)
    w = Worker(_Db(), None, _RunCfg(), model_name="m", poll_interval_s=0.01)  # type: ignore[arg-type]
    w._db = type("Main", (_Db,), {"clone": lambda self: StuckDb()})()  # type: ignore[assignment]
    runner = threading.Thread(target=w.run_forever)
    runner.start()
    time.sleep(0.2)
    w.stop()
    runner.join(3)
    stuck.set()
    assert not runner.is_alive()
    assert not StuckDb.closed_by_main


def test_a_paused_worker_takes_no_request_and_says_so(monkeypatch: Any) -> None:
    """The dashboard asked for a pause: no request is claimed, the heartbeat says "paused", and the dashboard is told once."""
    beats: list[str] = []
    told: list[str] = []
    monkeypatch.setattr(worker_module.repo, "read_control", lambda conn: Control("paused", None))
    monkeypatch.setattr(worker_module.repo, "report_actual_state", lambda conn, state: told.append(state))
    monkeypatch.setattr(worker_module.repo, "beat", lambda conn, **kw: beats.append(kw["status"]))
    monkeypatch.setattr(worker_module, "process_next", lambda db, svc, cfg: pytest.fail("a request was taken while paused"))
    w = Worker(_Db(), None, _RunCfg(), model_name="m", poll_interval_s=0.01)  # type: ignore[arg-type]
    assert [w.run_once(), w.run_once(), w.run_once()] == [False, False, False]
    assert beats == ["paused"] * 3
    assert told == ["paused"]  # once, not every cycle


def test_resuming_takes_requests_again_and_tells_the_dashboard(monkeypatch: Any) -> None:
    wish = {"state": "paused"}
    told: list[str] = []
    taken: list[int] = []
    monkeypatch.setattr(worker_module.repo, "read_control", lambda conn: Control(wish["state"], None))
    monkeypatch.setattr(worker_module.repo, "report_actual_state", lambda conn, state: told.append(state))
    monkeypatch.setattr(worker_module.repo, "beat", lambda conn, **kw: None)
    monkeypatch.setattr(worker_module, "process_next", lambda db, svc, cfg: bool(taken.append(1) or True))
    w = Worker(_Db(), None, _RunCfg(), model_name="m", poll_interval_s=0.01)  # type: ignore[arg-type]
    w.run_once()
    assert taken == []
    wish["state"] = "running"
    assert w.run_once() is True and taken == [1]
    assert told == ["paused", "running"]


def test_without_the_migration_the_worker_just_runs() -> None:
    """A database that has no ca_control table yet (0019 not applied): the wish reads as "running"."""
    import psycopg

    from campaign_agent.store import repositories as repo

    class Conn:
        @contextmanager
        def transaction(self) -> Any:
            yield

        def execute(self, *_a: Any) -> Any:
            raise psycopg.errors.UndefinedTable("no ca_control")

    assert repo.read_control(Conn()) == Control("running", None)  # type: ignore[arg-type]
    repo.report_actual_state(Conn(), "paused")  # type: ignore[arg-type]  # no error either


class _Marketing:
    """The Marketing Agent's switch and the column that remembers it was running, as the database holds them."""

    def __init__(self, running: bool, resume: bool = False, allowed: bool = True) -> None:
        self.running, self.resume, self.allowed = running, resume, allowed
        self.asked: list[str] = []

    def state(self, conn: Any) -> dict[str, str]:
        s = "running" if self.running else "stopped"
        return {"desired_state": s, "actual_state": s}

    def request(self, conn: Any, state: str, by: str) -> bool:
        if not self.allowed:
            return False
        self.asked.append(state)
        self.running = state == "running"
        return True

    def set_resume(self, conn: Any, value: bool) -> None:
        self.resume = value


def _worker_with(monkeypatch: Any, ma: _Marketing, wish: dict[str, str], clock: Any = None) -> Worker:
    monkeypatch.setattr(worker_module.repo, "read_control", lambda conn: Control(wish["state"], ma.resume))
    monkeypatch.setattr(worker_module.repo, "pipeline_state", ma.state)
    monkeypatch.setattr(worker_module.repo, "request_pipeline", ma.request)
    monkeypatch.setattr(worker_module.repo, "set_ma_resume", lambda conn, v: ma.set_resume(conn, v))
    monkeypatch.setattr(worker_module.repo, "beat", lambda conn, **kw: None)
    monkeypatch.setattr(worker_module, "process_next", lambda db, svc, cfg: False)
    monkeypatch.setattr(worker_module, "refresh_applied", lambda db: None)
    return Worker(_Db(), None, None, model_name="m", poll_interval_s=0.01, clock=clock or time.monotonic)  # type: ignore[arg-type]


def test_pausing_the_campaign_agent_stops_a_running_marketing_agent_and_remembers_it(monkeypatch: Any) -> None:
    ma, wish = _Marketing(running=True), {"state": "paused"}
    w = _worker_with(monkeypatch, ma, wish)
    w.run_once()
    assert ma.asked == ["stopped"] and ma.resume is True


def test_resuming_starts_it_again_once_and_only_if_it_was_running(monkeypatch: Any) -> None:
    ma, wish = _Marketing(running=True), {"state": "paused"}
    w = _worker_with(monkeypatch, ma, wish)
    w.run_once()
    wish["state"] = "running"
    w.run_once()
    w.run_once()
    assert ma.asked == ["stopped", "running"] and ma.resume is False  # started once, not on every cycle

    idle, wish2 = _Marketing(running=False), {"state": "paused"}
    w2 = _worker_with(monkeypatch, idle, wish2)
    w2.run_once()
    wish2["state"] = "running"
    w2.run_once()
    assert idle.asked == [] and idle.running is False  # it was not running, so resuming leaves it stopped


def test_while_paused_someone_starting_the_marketing_agent_is_undone(monkeypatch: Any) -> None:
    ma, wish = _Marketing(running=False), {"state": "paused"}
    now = {"t": 0.0}
    w = _worker_with(monkeypatch, ma, wish, clock=lambda: now["t"])
    w.run_once()
    assert ma.asked == []
    ma.running = True  # started by hand while the Campaign Agent is paused
    now["t"] = 5.0
    w.run_once()
    assert ma.asked == []  # checked every 30 s, not every cycle
    now["t"] = 40.0
    w.run_once()
    assert ma.asked == ["stopped"]


def test_without_the_grant_a_resume_does_not_retry_in_a_loop(monkeypatch: Any) -> None:
    ma, wish = _Marketing(running=False, resume=True, allowed=False), {"state": "running"}
    w = _worker_with(monkeypatch, ma, wish)
    w.run_once()
    assert ma.resume is False  # asked once, then forgotten


class _Cfg:
    auto_interpret = True
    auto_interpret_grace_s = 20
    auto_interpret_max_age_h = 168


def _idle_worker(monkeypatch: Any, found: Any, now: dict[str, float], paused: bool = False) -> tuple[Worker, list[tuple[int, int]]]:
    calls: list[tuple[int, int]] = []

    def sweep(conn: Any, grace: int, max_age: int) -> Any:
        calls.append((grace, max_age))
        return found

    monkeypatch.setattr(worker_module.repo, "auto_interpret_new_campaigns", sweep)
    monkeypatch.setattr(worker_module.repo, "read_control", lambda conn: Control("paused" if paused else "running", None))
    monkeypatch.setattr(worker_module.repo, "beat", lambda conn, **kw: None)
    monkeypatch.setattr(worker_module, "process_next", lambda db, svc, cfg: False)
    monkeypatch.setattr(worker_module, "refresh_applied", lambda db: None)
    w = Worker(_Db(), None, _Cfg(), model_name="m", poll_interval_s=0.01, clock=lambda: now["t"])  # type: ignore[arg-type]
    return w, calls


def test_an_idle_worker_looks_for_new_campaigns_every_ten_seconds_not_every_cycle(monkeypatch: Any) -> None:
    now = {"t": 0.0}
    w, calls = _idle_worker(monkeypatch, [], now)
    w.run_once()
    now["t"] = 4.0
    w.run_once()
    assert calls == [(20, 168)]
    now["t"] = 11.0
    w.run_once()
    assert len(calls) == 2


def test_a_paused_worker_does_not_start_on_anything(monkeypatch: Any) -> None:
    w, calls = _idle_worker(monkeypatch, ["c1"], {"t": 0.0}, paused=True)
    w.run_once()
    assert calls == []


def test_without_the_migration_it_says_so_once_and_keeps_working(monkeypatch: Any, caplog: Any) -> None:
    now = {"t": 0.0}
    w, calls = _idle_worker(monkeypatch, None, now)
    for t in (0.0, 11.0, 22.0):
        now["t"] = t
        assert w.run_once() is False
    assert len(calls) == 3
    assert sum("migration 0021" in r.getMessage() for r in caplog.records) == 1

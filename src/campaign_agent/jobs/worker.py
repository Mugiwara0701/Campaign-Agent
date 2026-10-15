"""The service loop: heartbeat, take a request, run it, repeat. Outbound connections only."""

import logging
import threading
import time
from collections.abc import Callable
from importlib import metadata

import psycopg

from campaign_agent.jobs.handlers import Services
from campaign_agent.jobs.manager import ManagerConfig, process_next, refresh_applied
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Conn, Database

log = logging.getLogger("campaign_agent.worker")


# When the database refuses the LOGIN (wrong password, unknown user, or the pooler's circuit breaker), trying
# again quickly cannot help and can make it worse: repeated failures get a client blocked, sometimes everyone
# behind the same address. So wait long, and keep the service quiet until someone fixes the URL.
LOGIN_BACKOFF_S = 600.0
# How often an idle worker looks at what the Marketing Agent reports and updates the live campaign's view.
APPLIED_CHECK_S = 30.0
_LOGIN_MARKERS = (
    "authentication failed",
    "tenant or user not found",
    "ecircuitbreaker",
    "too many authentication failures",
    "no pg_hba.conf entry",
)


def is_login_problem(detail: str) -> bool:
    text = detail.lower()
    return any(marker in text for marker in _LOGIN_MARKERS)


def backoff_for(detail: str, failures: int) -> float:
    """Seconds to wait after a database error: short and growing for glitches, long for a refused login."""
    return LOGIN_BACKOFF_S if is_login_problem(detail) else min(60.0, 2.0**failures)


AUTO_START_CHECK_S = 10.0  # how often an idle worker looks for a new campaign nobody has started
PAUSE_ENFORCE_S = 30.0  # while paused, how often to make sure the Marketing Agent is still stopped
BEAT_JOIN_S = 5.0  # how long a stop waits for the heartbeat thread
HEARTBEAT_S = 15.0  # a request can take minutes (a model on a CPU): the heartbeat must not wait for it


def agent_version() -> str:
    try:
        return metadata.version("campaign-agent")
    except metadata.PackageNotFoundError:
        return "unknown"


class Worker:
    def __init__(
        self,
        db: Database,
        svc: Services,
        cfg: ManagerConfig,
        *,
        model_name: str,
        poll_interval_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self._svc = svc
        self._cfg = cfg
        self._model = model_name
        self._poll = poll_interval_s
        self._sleep = sleep
        self._stop = False
        self._clock = clock
        self._last_applied_check = float("-inf")
        self._busy = False
        self._paused: bool | None = None  # None = the dashboard has not been told yet
        self._last_enforce = float("-inf")
        self._last_auto_start = float("-inf")
        self._auto_start_warned = False
        self._beat_stop = threading.Event()

    def stop(self) -> None:
        self._stop = True
        self._beat_stop.set()

    def _beat(self, status: str) -> None:
        with self._db.transaction() as conn:
            repo.beat(
                conn,
                status=status,
                agent_version=agent_version(),
                model_name=self._model,
                request_id=None,
            )

    def _paused_by_dashboard(self) -> bool:
        """Has the dashboard asked for a pause (migration 0019)? Reports each change back, so it shows both, and keeps the
        Marketing Agent in step: paused here means stopped there, and a resume starts it again if it was running."""
        with self._db.transaction() as conn:
            ctl = repo.read_control(conn)
            paused = ctl.desired == "paused"
            if paused != self._paused:
                repo.report_actual_state(conn, "paused" if paused else "running")
                log.info("worker %s", "paused from the dashboard" if paused else "running")
                self._paused = paused
                self._last_enforce = float("-inf")
            if ctl.ma_resume is not None:  # migration 0020 applied
                self._keep_marketing_agent_in_step(conn, paused, ctl.ma_resume)
        return paused

    def _keep_marketing_agent_in_step(self, conn: Conn, paused: bool, ma_resume: bool) -> None:
        if paused:
            if self._clock() - self._last_enforce < PAUSE_ENFORCE_S:
                return
            self._last_enforce = self._clock()
            state = repo.pipeline_state(conn)
            if state is not None and "running" in (state["desired_state"], state["actual_state"]):
                if not ma_resume:
                    repo.set_ma_resume(conn, True)  # it was running: start it again on resume
                if repo.request_pipeline(conn, "stopped", "campaign agent paused"):
                    log.info("the Marketing Agent was asked to stop because the Campaign Agent is paused")
                else:
                    log.warning("the Campaign Agent is paused but may not stop the Marketing Agent (migration 0018?)")
        elif ma_resume:
            if repo.request_pipeline(conn, "running", "campaign agent resumed"):
                log.info("the Marketing Agent was asked to run again because the Campaign Agent resumed")
            else:
                log.warning("the Campaign Agent resumed but may not start the Marketing Agent (migration 0018?)")
            repo.set_ma_resume(conn, False)  # asked once; never retried in a loop

    def _auto_start_new_campaigns(self) -> bool:
        """An idle worker starts on a campaign the dashboard saved but sent no request for. Returns whether it did."""
        cfg = self._cfg
        if cfg is None or not getattr(cfg, "auto_interpret", False):
            return False
        if self._clock() - self._last_auto_start < AUTO_START_CHECK_S:
            return False
        self._last_auto_start = self._clock()
        with self._db.transaction() as conn:
            started = repo.auto_interpret_new_campaigns(conn, cfg.auto_interpret_grace_s, cfg.auto_interpret_max_age_h)
        if started is None:
            if not self._auto_start_warned:
                self._auto_start_warned = True
                log.warning("cannot start on new campaigns by itself: migration 0021 is not applied")
            return False
        for campaign_id in started:
            log.info("started on a new campaign by itself", extra={"campaign_id": campaign_id})
        return bool(started)

    def run_once(self) -> bool:
        """One cycle: heartbeat, then at most one request. Returns whether a request was run.
        While the dashboard has paused the worker it takes none (a request already running was finished)."""
        if self._paused_by_dashboard():
            self._beat("paused")
            return False
        self._beat("idle")
        self._busy = True
        try:
            did = process_next(self._db, self._svc, self._cfg)
        finally:
            self._busy = False
        if did:
            self._beat("idle")
        elif self._auto_start_new_campaigns():
            return False  # a request was just made; the next cycle takes it
        elif self._clock() - self._last_applied_check >= APPLIED_CHECK_S:
            self._last_applied_check = self._clock()
            refresh_applied(self._db)
        return did

    def _heartbeat_loop(self, db: Database) -> None:
        """Beats on its own connection every HEARTBEAT_S, "busy" while a request runs, so a long request does not make
        the Campaign Agent look stopped."""
        while not self._beat_stop.wait(HEARTBEAT_S):
            try:
                with db.transaction() as conn:
                    repo.beat(
                        conn,
                        status="paused" if self._paused and not self._busy else ("busy" if self._busy else "idle"),
                        agent_version=agent_version(),
                        model_name=self._model,
                        request_id=None,
                    )
            except Exception:
                log.warning("heartbeat failed", exc_info=False)

    def _wait(self, seconds: float) -> None:
        """Sleep in one-second slices so a stop request (SIGTERM) is honoured within a second."""
        left = seconds
        while left > 0 and not self._stop:
            self._sleep(min(1.0, left))
            left -= 1.0

    def run_forever(self) -> None:
        log.info("worker started", extra={"worker_id": self._cfg.worker_id})
        beat_db = self._db.clone()
        beater = threading.Thread(target=self._heartbeat_loop, args=(beat_db,), daemon=True, name="heartbeat")
        beater.start()
        try:
            self._loop()
        finally:
            self._beat_stop.set()
            beater.join(timeout=BEAT_JOIN_S)
            if not beater.is_alive():  # a thread stuck in a network call holds its connection: closing it would wait too
                beat_db.close()
        log.info("worker stopped", extra={"worker_id": self._cfg.worker_id})

    def _loop(self) -> None:
        failures = 0
        while not self._stop:
            try:
                did = self.run_once()
                failures = 0
            except psycopg.Error as exc:
                failures += 1
                # The first line of the database's own message says why (wrong password, host unreachable,
                # missing table). libpq never puts the password in it.
                detail = str(exc).splitlines()[0][:240] if str(exc) else ""
                wait = backoff_for(detail, failures)
                log.exception(
                    "database error",
                    extra={"failures": failures, "detail": detail, "retry_in_s": wait},
                )
                self._wait(wait)
                continue
            if not did:
                self._sleep(self._poll)

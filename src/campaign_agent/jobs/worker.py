"""The service loop: heartbeat, take a request, run it, repeat. Outbound connections only."""

import logging
import time
from collections.abc import Callable
from importlib import metadata

import psycopg

from campaign_agent.jobs.handlers import Services
from campaign_agent.jobs.manager import ManagerConfig, process_next
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Database

log = logging.getLogger("campaign_agent.worker")


# When the database refuses the LOGIN (wrong password, unknown user, or the pooler's circuit breaker), trying
# again quickly cannot help and can make it worse: repeated failures get a client blocked, sometimes everyone
# behind the same address. So wait long, and keep the service quiet until someone fixes the URL.
LOGIN_BACKOFF_S = 600.0
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
    ) -> None:
        self._db = db
        self._svc = svc
        self._cfg = cfg
        self._model = model_name
        self._poll = poll_interval_s
        self._sleep = sleep
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _beat(self, status: str) -> None:
        with self._db.transaction() as conn:
            repo.beat(
                conn,
                status=status,
                agent_version=agent_version(),
                model_name=self._model,
                request_id=None,
            )

    def run_once(self) -> bool:
        """One cycle: heartbeat, then at most one request. Returns whether a request was run."""
        self._beat("idle")
        did = process_next(self._db, self._svc, self._cfg)
        if did:
            self._beat("idle")
        return did

    def _wait(self, seconds: float) -> None:
        """Sleep in one-second slices so a stop request (SIGTERM) is honoured within a second."""
        left = seconds
        while left > 0 and not self._stop:
            self._sleep(min(1.0, left))
            left -= 1.0

    def run_forever(self) -> None:
        log.info("worker started", extra={"worker_id": self._cfg.worker_id})
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
        log.info("worker stopped", extra={"worker_id": self._cfg.worker_id})

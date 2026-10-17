"""Shared pieces of the QA suite: recording results, timeouts, a tiny HTTP client, settings files.

Standard library only, so the same code runs on both office machines (the CA's and the MA's own Python).
A *case* is one check. A test function holds several cases; a failing case never stops the others, unless it is `fatal`.
"""

import contextlib
import json
import os
import signal
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PASS, FAIL, WARN, SKIP, ERROR = "pass", "fail", "warn", "skip", "error"
BAD = (FAIL, ERROR)


class Skip(Exception):
    """Raised inside a case: this check cannot run here, and the message says why."""


class StopTest(Exception):
    """A fatal case failed: the rest of the test makes no sense."""


class CaseTimeout(Exception):
    pass


def stamp(ts: float | None = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S%z", time.localtime(ts if ts is not None else time.time())
    )


# --- results --------------------------------------------------------------------------------------------------------


class Recorder:
    """Appends one JSON line per case to results.jsonl. Thread safe, flushed at once so a crash loses nothing."""

    def __init__(self, path: Path, run_id: str, side: str) -> None:
        self.path, self.run_id, self.side = path, run_id, side
        self._lock = threading.Lock()
        self.bad = 0  # failed or errored cases so far
        path.parent.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        test: str,
        case: str,
        status: str,
        dur_s: float,
        detail: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "ts": time.time(), "at": stamp(), "run": self.run_id, "side": self.side, "test": test, "case": case,
            "status": status, "dur_s": round(dur_s, 3), "detail": detail[:1500], "data": data or {},
        }  # fmt: skip
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
            if status in BAD:
                self.bad += 1


@dataclass
class Ctx:
    run_id: str
    side: str
    out_dir: Path
    rec: Recorder
    env: dict[str, str]
    quick: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    deadline: float | None = None
    log: Callable[[str], None] = print
    stopping: threading.Event = field(default_factory=threading.Event)

    def time_left(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - time.time()

    def getenv(self, key: str, default: str = "") -> str:
        return self.env.get(key) or os.environ.get(key) or default

    def poll(self, fn: Callable[[], Any], timeout_s: float, every_s: float = 2.0) -> Any:
        """The first truthy answer of fn(), or None when it never comes in time."""
        end = time.monotonic() + timeout_s
        while True:
            if self.stopping.is_set():
                raise StopTest("stopped by the operator")
            value = fn()
            if value:
                return value
            if time.monotonic() >= end:
                return None
            time.sleep(every_s)

    def note(self, test: str, case: str, status: str, detail: str = "", **data: Any) -> None:
        self.rec.add(test, case, status, 0.0, detail, data)

    @contextmanager
    def case(
        self, test: str, name: str, *, timeout_s: float | None = 120, fatal: bool = False
    ) -> Iterator[dict[str, Any]]:
        """One check. `with ctx.case(...) as info:`: assert inside it; put numbers into `info` to keep them in the report."""
        info: dict[str, Any] = {}
        status, detail = PASS, ""
        started = time.monotonic()
        use_alarm = bool(timeout_s) and threading.current_thread() is threading.main_thread()
        previous = None
        if use_alarm:

            def on_alarm(_signum: int, _frame: Any) -> None:
                raise CaseTimeout

            previous = signal.signal(signal.SIGALRM, on_alarm)
            signal.setitimer(signal.ITIMER_REAL, float(timeout_s or 0))
        try:
            yield info
        except Skip as exc:
            status, detail = SKIP, str(exc)
        except AssertionError as exc:
            status, detail = FAIL, str(exc) or "assertion failed"
        except CaseTimeout:
            status, detail = ERROR, f"timed out after {timeout_s}s"
        except StopTest:
            raise
        except Exception as exc:
            tail = "".join(traceback.format_exception(exc)[-3:]).strip().replace("\n", " | ")
            status, detail = ERROR, f"{type(exc).__name__}: {exc} [{tail[-400:]}]"
        finally:
            if use_alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
                if previous is not None:
                    signal.signal(signal.SIGALRM, previous)
        if info.get("warn") and status == PASS:
            status, detail = WARN, str(info["warn"])
        self.rec.add(
            test,
            name,
            status,
            time.monotonic() - started,
            detail,
            {k: v for k, v in info.items() if k != "warn"},
        )
        self.log(
            f"  [{status.upper():5}] {test} :: {name}"
            + (f"  ({detail[:140]})" if detail and status != PASS else "")
        )
        if fatal and status in BAD:
            raise StopTest(f"{test}: {name}")


def expect(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --- http -----------------------------------------------------------------------------------------------------------


@dataclass
class Resp:
    status: int  # 0 = no answer at all (refused, timed out)
    headers: dict[str, str]
    text: str
    elapsed: float

    def json(self) -> Any:
        return json.loads(self.text)


def http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    raw: bytes | None = None,
    timeout: float = 30.0,
) -> Resp:
    """Never raises for an HTTP error status. Status 0 means the server did not answer."""
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if body is not None and "Content-Type" not in request.headers:
        request.add_header("Content-Type", "application/json")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Resp(
                response.status,
                {k.lower(): v for k, v in response.headers.items()},
                response.read().decode("utf-8", "replace"),
                time.monotonic() - started,
            )
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return Resp(
            exc.code,
            {k.lower(): v for k, v in exc.headers.items()},
            text,
            time.monotonic() - started,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Resp(0, {}, f"{type(exc).__name__}: {exc}", time.monotonic() - started)


# --- settings files ------------------------------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (quotes and trailing comments removed). Missing file: nothing."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #")[0].strip()
        out[key.strip()] = value
    return out


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[index]


# --- metrics --------------------------------------------------------------------------------------------------------


class Sampler(threading.Thread):
    """Calls `read()` every `interval_s` and appends the numbers to metrics.jsonl, for the charts of the report."""

    def __init__(self, path: Path, interval_s: float, read: Callable[[], dict[str, Any]]) -> None:
        super().__init__(daemon=True, name="sampler")
        self.path, self.interval_s, self.read = path, interval_s, read
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.is_set():
            row: dict[str, Any] = {"ts": time.time()}
            try:
                row.update(self.read())
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"[:200]
            with contextlib.suppress(OSError), self.path.open("a") as f:
                f.write(json.dumps(row, default=str) + "\n")
            self.stop_event.wait(self.interval_s)

    def stop(self) -> None:
        self.stop_event.set()

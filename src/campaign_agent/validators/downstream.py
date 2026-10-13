"""L6: the Marketing Agent's own loader accepts the overlay, and generates queries from it, with no network.

The overlay text is written to a temporary file and handed to a pinned checkout of the MA:

    <python> -m agent leads queries --campaign <file>

in a child process with an empty environment, its own session (so a timeout kills everything it started) and, where
the machine allows it, no network at all (`unshare --net`). Exit 0 means the loader accepted the file; the output must
contain at least one query. The command and flag are the contract with the MA (docs/marketing-agent-m8-work.md).

When L6 cannot run (no checkout configured, or no way to cut the network) the level is "skipped", visibly, unless
`required` is set, in which case that is a failure: nothing is published that was not checked.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from campaign_agent.validators.report import Issue, LevelResult, level_result

LEVEL = 6
NAME = "downstream"
_OUTPUT_CAP = 200_000
_NO_NETWORK = ("unshare", "--net", "--map-root-user")


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


# (argv, working directory, environment, timeout seconds) -> result
Runner = Callable[[Sequence[str], Path, dict[str, str], float], RunResult]


def subprocess_runner(
    argv: Sequence[str], cwd: Path, env: dict[str, str], timeout_s: float
) -> RunResult:
    proc = subprocess.Popen(  # noqa: S603
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        return RunResult(-9, (out or "")[:_OUTPUT_CAP], (err or "")[:_OUTPUT_CAP], timed_out=True)
    return RunResult(proc.returncode, (out or "")[:_OUTPUT_CAP], (err or "")[:_OUTPUT_CAP])


def network_cut_available(
    runner: Runner, cwd: Path, isolate: tuple[str, ...] = _NO_NETWORK
) -> bool:
    """Can a child process be started with no network on this machine?"""
    if not isolate:
        return True  # tests only: the caller chose to run without isolation
    if shutil.which(isolate[0]) is None:
        return False
    try:
        result = runner((*isolate, "true"), cwd, {"PATH": os.defpath}, 10.0)
    except OSError:
        return False
    return result.returncode == 0 and not result.timed_out


def _message(result: RunResult) -> str:
    lines = [ln.strip() for ln in (result.stderr or result.stdout).splitlines() if ln.strip()]
    text = lines[-1] if lines else f"exit code {result.returncode}"
    return text[:300]


@dataclass(frozen=True)
class Downstream:
    checkout: Path | None
    python: str = "python3"
    timeout_s: float = 60.0
    required: bool = False
    runner: Runner = subprocess_runner
    # The command that cuts the network. Empty means no isolation: for the end-to-end tests, where the stand-in
    # Marketing Agent runs on a CI machine that may not allow network namespaces. Never empty in the service.
    isolate: tuple[str, ...] = _NO_NETWORK

    def _unavailable(self, note: str) -> LevelResult:
        if self.required:
            return level_result(
                LEVEL, NAME, [Issue(LEVEL, "unavailable", "(marketing agent)", note)]
            )
        return LevelResult(LEVEL, NAME, "skipped", note=note)

    def check(self, overlay_yaml: str) -> LevelResult:
        if self.checkout is None:
            return self._unavailable("no Marketing Agent checkout is configured (MA_CHECKOUT)")
        if not self.checkout.is_dir():
            return self._unavailable(f"the Marketing Agent checkout {self.checkout} does not exist")
        if not network_cut_available(self.runner, self.checkout, self.isolate):
            return self._unavailable("this machine cannot start a process without network access")
        with tempfile.TemporaryDirectory(prefix="ca-l6-") as tmp:
            path = Path(tmp) / "campaign.yaml"
            path.write_text(overlay_yaml, encoding="utf-8")
            env = {
                "PATH": os.defpath,
                "HOME": tmp,
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            argv = (
                *self.isolate,
                self.python,
                *("-m", "agent", "leads", "queries", "--campaign", str(path)),
            )
            result = self.runner(argv, self.checkout, env, self.timeout_s)
        if result.timed_out:
            return self._failed(
                "timeout", f"the Marketing Agent did not answer within {self.timeout_s:g} s"
            )
        if result.returncode != 0:
            return self._failed("loader_rejected", _message(result))
        if not any(ln.strip() for ln in result.stdout.splitlines()):
            return self._failed(
                "no_queries", "the Marketing Agent generated no search queries from it"
            )
        return level_result(LEVEL, NAME, [])

    @staticmethod
    def _failed(code: str, message: str) -> LevelResult:
        return level_result(LEVEL, NAME, [Issue(LEVEL, code, "(marketing agent)", message)])

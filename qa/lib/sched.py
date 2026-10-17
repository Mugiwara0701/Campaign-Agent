"""Runs the tests of one side for hours, each on its own rhythm, one at a time."""

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from qa.lib.core import ERROR, FAIL, PASS, SKIP, Ctx, Skip, StopTest


@dataclass
class Task:
    name: str
    fn: Callable[[Ctx], None]
    every_s: float | None  # None: once
    first_s: float = 0.0  # wait this long after the start before the first run
    est_s: float = 60.0  # a run is not started when less than this is left of the time budget
    max_runs: int | None = None
    needs: str = ""  # a note for the list command: what it touches


def run_once(ctx: Ctx, task: Task) -> None:
    ctx.log(f"== {task.name} starts")
    bad_before = ctx.rec.bad
    started = time.monotonic()
    status, detail = PASS, ""
    try:
        task.fn(ctx)
    except Skip as exc:
        status, detail = SKIP, str(exc)
    except StopTest as exc:
        status, detail = FAIL, f"stopped early: {exc}"
    except Exception as exc:
        status, detail = (
            ERROR,
            f"{type(exc).__name__}: {exc} [{traceback.format_exc()[-300:]}]".replace("\n", " | "),
        )
    if status == PASS and ctx.rec.bad > bad_before:
        status, detail = FAIL, f"{ctx.rec.bad - bad_before} case(s) failed"
    ctx.rec.add(task.name, "(whole run)", status, time.monotonic() - started, detail)
    ctx.log(f"== {task.name} done: {status} in {time.monotonic() - started:.0f}s")


def run_schedule(ctx: Ctx, tasks: list[Task], hours: float, stop_file: Path, quick: bool) -> None:
    start = time.time()
    end = start + hours * 3600
    ctx.deadline = end
    if quick:  # every test once, in order
        for task in tasks:
            if stop_file.exists() or ctx.stopping.is_set():
                break
            run_once(ctx, task)
        return
    due = {t.name: start + t.first_s for t in tasks}
    runs = {t.name: 0 for t in tasks}
    while not (stop_file.exists() or ctx.stopping.is_set()):
        now = time.time()
        if now >= end:
            break
        ready = [
            t for t in tasks
            if now >= due[t.name] and (t.max_runs is None or runs[t.name] < t.max_runs) and (end - now) >= t.est_s
        ]  # fmt: skip
        if not ready:
            time.sleep(2)
            continue
        task = min(ready, key=lambda t: due[t.name])
        began = time.time()
        run_once(ctx, task)
        runs[task.name] += 1
        due[task.name] = (began + task.every_s) if task.every_s else float("inf")
        if (
            task.every_s and due[task.name] < time.time()
        ):  # it took longer than its rhythm: next one soon, not in a burst
            due[task.name] = time.time() + 30

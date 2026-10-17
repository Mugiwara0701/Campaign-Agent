"""The QA runner for one machine:  python -m qa.soak --side ca|ma  [--hours 4.5 | --quick]  [--only a,b]  [--list]

`ca` runs on the Campaign Agent's machine (database, gateway, link, campaign life cycle, controlling the Marketing Agent).
`ma` runs on the Marketing Agent's machine (Slack, e-mail to the test recipient, search, Gmail, link round trip).
Both write results.jsonl and metrics.jsonl to --out; qa/make_report.py joins them into one PDF.
"""

import argparse
import importlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from qa.lib.core import Ctx, Recorder, Sampler, read_env_file, stamp
from qa.lib.sched import Task, run_schedule

SIDES = {"ca": "qa.ca.tasks", "ma": "qa.ma.tasks"}


def git_rev(path: Path) -> str:
    try:
        return (
            subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
            or "?"
        )
    except (OSError, subprocess.SubprocessError):
        return "?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qa.soak")
    ap.add_argument("--side", choices=sorted(SIDES), required=True)
    ap.add_argument("--hours", type=float, default=4.5, help="how long to keep running (the soak)")
    ap.add_argument(
        "--quick", action="store_true", help="every test once, with short waits (a smoke run)"
    )
    ap.add_argument("--only", default="", help="comma separated test names")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--stop-file", type=Path, default=None, help="create this file to end the run gracefully"
    )
    args = ap.parse_args(argv)

    module = importlib.import_module(SIDES[args.side])
    tasks: list[Task] = module.build_tasks(args.quick)
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        tasks = [t for t in tasks if t.name in wanted or any(t.name.startswith(w) for w in wanted)]
    if args.list:
        for t in tasks:
            every = "once" if not t.every_s else f"every {t.every_s / 60:.0f} min"
            print(f"{t.name:34} {every:16} ~{t.est_s / 60:4.0f} min   {t.needs}")
        return 0

    out = args.out or Path("qa/results") / f"{args.run_id}-{args.side}"
    out.mkdir(parents=True, exist_ok=True)
    stop_file = args.stop_file or out / "STOP"
    env = {}
    for path in module.env_files():
        env.update(read_env_file(path))
    log_file = (out / "soak.log").open("a", buffering=1)

    def log(message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        log_file.write(line + "\n")

    rec = Recorder(out / "results.jsonl", args.run_id, args.side)
    ctx = Ctx(
        run_id=args.run_id, side=args.side, out_dir=out, rec=rec, env=env, quick=args.quick, log=log
    )
    meta: dict[str, Any] = {
        "run": args.run_id, "side": args.side, "started": stamp(), "hours": args.hours, "quick": args.quick,
        "host": platform.node(), "python": platform.python_version(), "argv": sys.argv[1:], "tests": [t.name for t in tasks],
        "git": git_rev(Path.cwd()),
    }  # fmt: skip
    meta.update(module.describe(ctx))
    (out / "run.json").write_text(json.dumps(meta, indent=2, default=str))

    def on_signal(_signum: int, _frame: Any) -> None:
        ctx.stopping.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    sampler = Sampler(out / "metrics.jsonl", 30 if args.quick else 60, lambda: module.sample(ctx))
    sampler.start()
    log(
        f"QA run {args.run_id} ({args.side}) starts: {len(tasks)} tests, {'quick' if args.quick else f'{args.hours} h'}; results in {out}"
    )
    try:
        module.setup(ctx)
        run_schedule(ctx, tasks, args.hours, stop_file, args.quick)
    finally:
        try:
            module.teardown(ctx)
        finally:
            sampler.stop()
            meta["ended"] = stamp()
            meta["cases_failed"] = rec.bad
            (out / "run.json").write_text(json.dumps(meta, indent=2, default=str))
            log(f"QA run {args.run_id} ({args.side}) ended: {rec.bad} case(s) failed or errored")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())

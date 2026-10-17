"""python -m campaign_agent start           run the service
python -m campaign_agent once            take at most one request, then exit (useful to test a setup)
python -m campaign_agent migrate         apply the SQL files in migrations/ to the database (asks first)
python -m campaign_agent agent-password  set the login password of the limited campaign_agent role
python -m campaign_agent dashboard-password  the same for the campaign_dashboard role (the gateway on Render uses it)
python -m campaign_agent api             the dashboard gateway: a small web service the dashboard's server calls (docs/dashboard-api.md)
python -m campaign_agent try "message"   act as the dashboard to test the agent (office machine only)
python -m campaign_agent try --clean     remove the test campaigns
python -m campaign_agent bench           measure a model on the benchmark (docs/evaluation.md)
python -m campaign_agent retention       clear old request text (docs/retention.md; asks for --yes)
python -m campaign_agent autopilot      type a campaign into the open window, answer, ask for a change, approve: no clicking (--message, --edit, --answer, --no-approve, --start-ma)
python -m campaign_agent ui              the office window in Chromium, full screen (F11 leaves it; --windowed for a normal window; --no-window just serves it)
python -m campaign_agent console         live view of what is working now (read-only; --snapshot once, --demo a simulation)"""

import argparse
import os
import signal
import socket
import sys
import time
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING

from campaign_agent.settings import Settings, load_settings

if TYPE_CHECKING:
    from campaign_agent.ma_link.server import LinkServer


def _admin_url(settings: Settings) -> str | None:
    url = settings.migrate_database_url or settings.database_url
    return url.get_secret_value() if url else None


def _admin(command: str, args: argparse.Namespace, settings: Settings) -> int:
    from campaign_agent.store.migrate import run_agent_password, run_migrate

    url = _admin_url(settings)
    if url is None:
        print("MIGRATE_DATABASE_URL (or DATABASE_URL) is not set", file=sys.stderr)
        return 2
    if command == "migrate":
        return run_migrate(url, directory=args.dir, plan_only=args.plan, assume_yes=args.yes)
    return run_agent_password(
        url, role="campaign_dashboard" if command == "dashboard-password" else "campaign_agent"
    )


def _try(args: argparse.Namespace, settings: Settings) -> int:
    from campaign_agent.devclient import DevClient
    from campaign_agent.store.db import Database

    if os.environ.get("CA_ON_OFFICE_MACHINE") != "1":
        print(
            'Run this on the office machine: bash deploy/remote.sh try "your message"',
            file=sys.stderr,
        )
        return 2
    url = _admin_url(settings)
    if url is None:
        print("MIGRATE_DATABASE_URL (or DATABASE_URL) is not set", file=sys.stderr)
        return 2
    if not args.clean and not args.message:
        print(
            'Give a message: bash deploy/remote.sh try "Find ..."   (or --clean)', file=sys.stderr
        )
        return 2
    db = Database(url)
    try:
        client = DevClient(db)
        return client.clean() if args.clean else client.session(args.message)
    finally:
        db.close()


def _console(args: argparse.Namespace, settings: Settings) -> int:
    from campaign_agent.console import run_console
    from campaign_agent.console_demo import run_demo
    from campaign_agent.store.db import Database

    if args.demo:
        return run_demo()
    db = Database(settings.database_url.get_secret_value()) if settings.database_url else None
    return run_console(db, settings, once=args.snapshot)


def _ui(args: argparse.Namespace, settings: Settings) -> int:
    from campaign_agent.console_web import run_ui
    from campaign_agent.store.db import Database

    db = Database(settings.database_url.get_secret_value()) if settings.database_url else None
    return run_ui(
        db,
        settings,
        port=args.ui_port,
        open_window=not args.no_window,
        fullscreen=not args.windowed,
    )


def _api(settings: Settings) -> int:
    """The dashboard gateway. Binds all addresses on $PORT (what Render gives it): it is meant to be public, behind the token."""
    import uvicorn

    from campaign_agent.api.auth import MIN_TOKEN_CHARS
    from campaign_agent.api.service import Gateway
    from campaign_agent.logs import setup_logging
    from campaign_agent.main import create_app

    setup_logging()
    token = settings.api_token.get_secret_value() if settings.api_token else ""
    if settings.api_database_url is None or len(token) < MIN_TOKEN_CHARS:
        print(
            f"API_DATABASE_URL and API_TOKEN (at least {MIN_TOKEN_CHARS} characters) must be set",
            file=sys.stderr,
        )
        return 2
    from campaign_agent.api.users import UserVerifier

    secret = settings.api_supabase_jwt_secret
    users = (
        UserVerifier(settings.api_supabase_url, secret.get_secret_value() if secret else None)
        if settings.api_supabase_url or secret
        else None
    )
    if settings.api_require_user and users is None:
        print(
            "API_REQUIRE_USER needs API_SUPABASE_URL (or API_SUPABASE_JWT_SECRET) to check user tokens",
            file=sys.stderr,
        )
        return 2
    app = create_app(
        Gateway(settings.api_database_url.get_secret_value()),
        token=token,
        users=users,
        require_user=settings.api_require_user,
    )
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104
        port=int(os.environ.get("PORT", "8000")),
        log_level="warning",
        access_log=False,
    )
    return 0


def _retention(args: argparse.Namespace, settings: Settings) -> int:
    from campaign_agent.store.retention import run_retention

    url = _admin_url(settings)
    if url is None:
        print("MIGRATE_DATABASE_URL (or DATABASE_URL) is not set", file=sys.stderr)
        return 2
    return run_retention(url, args.days, apply=args.yes)


def _bench(args: argparse.Namespace, settings: Settings) -> int:
    from campaign_agent.evaluation.run import run_benchmark, write_report

    report = run_benchmark(
        settings,
        split=args.split,
        runs=max(1, args.runs),
        research=args.research,
        oracle=args.oracle,
        limit=args.limit,
        progress=lambda line: print(line, flush=True),
    )
    model = str(report["meta"]["model"].get("model", "model")).replace("/", "_").replace(":", "_")
    out = args.out or Path("benchmarks/results") / f"{time.strftime('%Y%m%d-%H%M%S')}-{model}"
    json_path, md_path = write_report(report, out)
    print(f"report: {md_path}\ndata:   {json_path}")
    if args.check:
        missed = [
            f"research {label}: {t['metric']}"
            for label, block in report["runs"].items()
            for t in block["targets"]
            if t["status"] == "NOT MET"
        ]
        for line in missed:
            print(f"NOT MET  {line}", file=sys.stderr)
        return 1 if missed else 0
    return 0


def _serve(command: str, settings: Settings) -> int:
    from campaign_agent.extractor.extractor import Extractor
    from campaign_agent.jobs.handlers import Services
    from campaign_agent.jobs.manager import ManagerConfig
    from campaign_agent.jobs.worker import Worker
    from campaign_agent.llm import make_adapter
    from campaign_agent.llm.adapter import GenParams
    from campaign_agent.logs import setup_logging
    from campaign_agent.ma_status import MaStatus, fetch_status
    from campaign_agent.research.factory import build_researcher
    from campaign_agent.store.db import Database
    from campaign_agent.validators.downstream import Downstream

    setup_logging()
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    extractor_adapter = make_adapter(settings)
    extractor = Extractor(extractor_adapter, params=GenParams(timeout_s=settings.llm_timeout_s))
    db = Database(settings.database_url.get_secret_value())
    cfg = ManagerConfig(
        worker_id=settings.worker_id or f"{socket.gethostname()}-{os.getpid()}",
        lease_s=settings.lease_s,
        max_attempts=settings.max_attempts,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        auto_interpret=settings.auto_interpret,
        auto_interpret_grace_s=settings.auto_interpret_grace_s,
        auto_interpret_max_age_h=settings.auto_interpret_max_age_h,
    )

    def ma_status() -> MaStatus | None:
        with db.transaction() as conn:
            return fetch_status(conn)

    downstream = Downstream(
        checkout=settings.ma_checkout,
        python=settings.ma_python,
        timeout_s=settings.ma_timeout_s,
        required=settings.ma_required,
    )
    worker = Worker(
        db,
        Services(
            extractor,
            downstream=downstream,
            ma_status=ma_status,
            researcher=build_researcher(settings, extractor_adapter),
        ),
        cfg,
        model_name=settings.llm_model,
        poll_interval_s=settings.poll_interval_s,
    )

    def stop(_signum: int, _frame: FrameType | None) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    link = _start_link(settings) if command == "start" else None
    try:
        if command == "once":
            worker.run_once()
        else:
            worker.run_forever()
    finally:
        if link is not None:
            link.stop()
        db.close()
    return 0


def _start_link(settings: Settings) -> "LinkServer | None":
    """The direct link to the Marketing Agent (docs/ca-ma-link.md). Off unless MA_LINK_ENABLED; never stops the worker."""
    from campaign_agent.ma_link.app import create_app
    from campaign_agent.ma_link.auth import MIN_TOKEN_CHARS
    from campaign_agent.ma_link.server import LinkServer, validate_bind
    from campaign_agent.ma_link.service import LinkService
    from campaign_agent.store.db import Database

    if not settings.ma_link_enabled:
        return None
    token = settings.ma_link_token.get_secret_value() if settings.ma_link_token else ""
    if len(token) < MIN_TOKEN_CHARS or settings.database_url is None:
        print(
            f"MA_LINK_ENABLED needs MA_LINK_TOKEN of at least {MIN_TOKEN_CHARS} characters; the link is off",
            file=sys.stderr,
        )
        return None
    previous = settings.ma_link_token_previous
    try:
        validate_bind(settings.ma_link_bind)
    except ValueError as exc:
        print(f"{exc}; the link is off", file=sys.stderr)
        return None
    app = create_app(
        LinkService(
            Database(settings.database_url.get_secret_value()),
            respect_schedule=settings.ma_link_respect_schedule,
        ),
        token=token,
        token_previous=previous.get_secret_value() if previous else None,
    )
    server = LinkServer(app, bind=settings.ma_link_bind, port=settings.ma_link_port)
    server.start()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="campaign_agent", description="Campaign Agent")
    parser.add_argument(
        "command",
        choices=[
            "start",
            "once",
            "migrate",
            "agent-password",
            "dashboard-password",
            "api",
            "try",
            "bench",
            "retention",
            "console",
            "ui",
            "autopilot",
        ],
    )
    parser.add_argument(
        "message", nargs="?", default=None, help="try, autopilot: the campaign message to send"
    )
    parser.add_argument("--clean", action="store_true", help="try: remove the [CA-TEST] campaigns")
    parser.add_argument(
        "--plan", action="store_true", help="migrate: only show what would be applied"
    )
    parser.add_argument("--yes", action="store_true", help="migrate, retention: really do it")
    parser.add_argument(
        "--days", type=int, default=90, help="retention: clear request text older than this"
    )
    parser.add_argument(
        "--dir", type=Path, default=None, help="migrate: the folder with the .sql files"
    )
    parser.add_argument(
        "--demo", action="store_true", help="console: play a simulation, not the real state"
    )
    parser.add_argument("--snapshot", action="store_true", help="console: print once and exit")
    parser.add_argument(
        "--message",
        dest="message_flag",
        default=None,
        help="autopilot: the campaign message (the same as giving it as the plain argument)",
    )
    parser.add_argument(
        "--edit", default=None, help="autopilot: the change to ask for once the file is ready"
    )
    parser.add_argument(
        "--answer",
        action="append",
        help="autopilot: choose the answer containing this text (repeatable; default: the first choice)",
    )
    parser.add_argument(
        "--start-ma",
        action="store_true",
        help="autopilot: once live, press 'Tell the Marketing Agent to start'",
    )
    parser.add_argument(
        "--no-approve",
        action="store_true",
        help="autopilot: stop at the finished file, do not approve",
    )
    parser.add_argument(
        "--timeout", type=int, default=3600, help="autopilot: give up after this many seconds"
    )
    parser.add_argument(
        "--no-window", action="store_true", help="ui: only serve the page, do not open Chromium"
    )
    parser.add_argument(
        "--windowed", action="store_true", help="ui: a normal window instead of full screen"
    )
    parser.add_argument("--ui-port", type=int, default=8765, help="ui: the local port")
    bench = parser.add_argument_group("bench")
    bench.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    bench.add_argument("--runs", type=int, default=1, help="repeat the run to measure consistency")
    bench.add_argument("--research", choices=["off", "on", "both"], default="off")
    bench.add_argument("--oracle", action="store_true", help="answer with the gold patch, no model")
    bench.add_argument("--limit", type=int, default=None, help="only the first N cases")
    bench.add_argument("--out", type=Path, default=None, help="report path, without extension")
    bench.add_argument(
        "--check", action="store_true", help="exit 1 when a target with a fixed limit is not met"
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "bench":
        return _bench(args, settings)
    if args.command == "autopilot":
        from campaign_agent.autopilot import main as autopilot_main

        return autopilot_main(args)
    if args.command == "ui":
        return _ui(args, settings)
    if args.command == "console":
        return _console(args, settings)
    if args.command == "retention":
        return _retention(args, settings)
    if args.command == "api":
        return _api(settings)
    if args.command in ("migrate", "agent-password", "dashboard-password"):
        return _admin(args.command, args, settings)
    if args.command == "try":
        return _try(args, settings)
    return _serve(args.command, settings)


if __name__ == "__main__":
    raise SystemExit(main())

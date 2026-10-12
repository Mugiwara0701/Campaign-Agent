"""python -m campaign_agent start           run the service
python -m campaign_agent once            take at most one request, then exit (useful to test a setup)
python -m campaign_agent migrate         apply the SQL files in migrations/ to the database (asks first)
python -m campaign_agent agent-password  set the login password of the limited campaign_agent role
python -m campaign_agent try "message"   act as the dashboard to test the agent (office machine only)
python -m campaign_agent try --clean     remove the test campaigns"""

import argparse
import os
import signal
import socket
import sys
from pathlib import Path
from types import FrameType

from campaign_agent.settings import Settings, load_settings


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
    return run_agent_password(url)


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


def _serve(command: str, settings: Settings) -> int:
    from campaign_agent.extractor.extractor import Extractor
    from campaign_agent.jobs.handlers import Services
    from campaign_agent.jobs.manager import ManagerConfig
    from campaign_agent.jobs.worker import Worker
    from campaign_agent.llm import make_adapter
    from campaign_agent.llm.adapter import GenParams
    from campaign_agent.logs import setup_logging
    from campaign_agent.ma_status import MaStatus, fetch_status
    from campaign_agent.store.db import Database
    from campaign_agent.validators.downstream import Downstream

    setup_logging()
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    extractor = Extractor(
        make_adapter(settings), params=GenParams(timeout_s=settings.llm_timeout_s)
    )
    db = Database(settings.database_url.get_secret_value())
    cfg = ManagerConfig(
        worker_id=settings.worker_id or f"{socket.gethostname()}-{os.getpid()}",
        lease_s=settings.lease_s,
        max_attempts=settings.max_attempts,
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
        Services(extractor, downstream=downstream, ma_status=ma_status),
        cfg,
        model_name=settings.llm_model,
        poll_interval_s=settings.poll_interval_s,
    )

    def stop(_signum: int, _frame: FrameType | None) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if command == "once":
            worker.run_once()
        else:
            worker.run_forever()
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="campaign_agent", description="Campaign Agent")
    parser.add_argument("command", choices=["start", "once", "migrate", "agent-password", "try"])
    parser.add_argument(
        "message", nargs="?", default=None, help="try: the campaign message to send"
    )
    parser.add_argument("--clean", action="store_true", help="try: remove the [CA-TEST] campaigns")
    parser.add_argument(
        "--plan", action="store_true", help="migrate: only show what would be applied"
    )
    parser.add_argument("--yes", action="store_true", help="migrate: apply without asking")
    parser.add_argument(
        "--dir", type=Path, default=None, help="migrate: the folder with the .sql files"
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command in ("migrate", "agent-password"):
        return _admin(args.command, args, settings)
    if args.command == "try":
        return _try(args, settings)
    return _serve(args.command, settings)


if __name__ == "__main__":
    raise SystemExit(main())

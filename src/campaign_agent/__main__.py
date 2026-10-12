"""python -m campaign_agent start   run the service
python -m campaign_agent once    take at most one request, then exit (useful to test a setup)"""

import argparse
import os
import signal
import socket
import sys
from types import FrameType

from campaign_agent.extractor.extractor import Extractor
from campaign_agent.jobs.handlers import Services
from campaign_agent.jobs.manager import ManagerConfig
from campaign_agent.jobs.worker import Worker
from campaign_agent.llm import make_adapter
from campaign_agent.logs import setup_logging
from campaign_agent.settings import load_settings
from campaign_agent.store.db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="campaign_agent", description="Campaign Agent worker")
    parser.add_argument("command", choices=["start", "once"])
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging()
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    from campaign_agent.llm.adapter import GenParams

    extractor = Extractor(
        make_adapter(settings), params=GenParams(timeout_s=settings.llm_timeout_s)
    )
    db = Database(settings.database_url.get_secret_value())
    cfg = ManagerConfig(
        worker_id=settings.worker_id or f"{socket.gethostname()}-{os.getpid()}",
        lease_s=settings.lease_s,
        max_attempts=settings.max_attempts,
    )
    worker = Worker(
        db,
        Services(extractor),
        cfg,
        model_name=settings.llm_model,
        poll_interval_s=settings.poll_interval_s,
    )

    def stop(_signum: int, _frame: FrameType | None) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if args.command == "once":
            worker.run_once()
        else:
            worker.run_forever()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from typing import Any

from campaign_agent.extractor.extractor import Extractor
from campaign_agent.jobs.handlers import Services
from campaign_agent.jobs.manager import ManagerConfig
from campaign_agent.jobs.worker import Worker
from campaign_agent.llm.stub import StubAdapter
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Database

CFG = ManagerConfig(worker_id="test-worker", lease_s=60, max_attempts=3)


def new_campaign(
    db: Database, name: str = "Auto campaign", detail: str = "Find automotive companies"
) -> str:
    with db.transaction() as c:
        row = c.execute(
            "insert into public.campaign (campaign_name, campaign_detail) values (%s, %s) returning id",
            (name, detail),
        ).fetchone()
    assert row is not None
    return str(row["id"])


def enqueue(
    db: Database,
    campaign_id: str,
    kind: str = "interpret",
    payload: dict[str, Any] | None = None,
    key: str | None = None,
    base: int | None = None,
) -> dict[str, Any]:
    with db.transaction() as c:
        return repo.enqueue_request(
            c,
            repo.NewRequest(
                campaign_id=campaign_id,
                request_type=kind,
                requested_by="dashboard-user",
                idempotency_key=key or f"{kind}-{id(payload)}-{base}",
                payload=payload or {},
                base_revision=base,
            ),
        )


def worker(db: Database, *replies: Any, cfg: ManagerConfig = CFG) -> Worker:
    extractor = Extractor(StubAdapter(list(replies)), sleep=lambda _s: None)
    return Worker(db, Services(extractor), cfg, model_name="stub", sleep=lambda _s: None)


def request_row(db: Database, request_id: Any) -> dict[str, Any]:
    with db.transaction() as c:
        row = c.execute(
            "select * from public.campaign_request where id = %s", (request_id,)
        ).fetchone()
    assert row is not None
    return row


def state_row(db: Database, campaign_id: str) -> dict[str, Any]:
    with db.transaction() as c:
        row = c.execute(
            "select * from public.campaign_state where campaign_id = %s", (campaign_id,)
        ).fetchone()
    assert row is not None
    return row


def versions(db: Database, campaign_id: str) -> list[dict[str, Any]]:
    with db.transaction() as c:
        return c.execute(
            "select * from public.campaign_version where campaign_id = %s order by version_no",
            (campaign_id,),
        ).fetchall()

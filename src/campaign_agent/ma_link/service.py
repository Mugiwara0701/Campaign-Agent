"""What the link routes do with the database. One connection, so every call holds a lock."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from campaign_agent.compiler.complete import complete_file
from campaign_agent.ma_link.events import EventIn, Rejected, iso
from campaign_agent.store import repositories as repo
from campaign_agent.store.db import Database

log = logging.getLogger("campaign_agent.ma_link")

PRUNE_EVERY_S = 3600.0


@dataclass(frozen=True)
class LiveCampaign:
    campaign_id: str
    version: int
    sha256: str
    contract_version: str
    overlay_yaml: str
    approved_by: str | None
    approved_at: str | None
    # The whole leadgen.yaml (compiler/complete.py), its hash and the fingerprint of the base it was built on. None when
    # it could not be built: the Marketing Agent then uses the overlay on its own base, as before.
    leadgen_yaml: str | None = None
    leadgen_sha256: str | None = None
    base_sha256: str | None = None

    @property
    def tag(self) -> str:
        """Changes whenever what the Marketing Agent should run changes (also for a rollback, or a re-synced base)."""
        base = f".{self.base_sha256[:8]}" if self.base_sha256 else ""
        return f"{self.campaign_id}.{self.version}.{self.sha256[:16]}{base}"

    def payload(self) -> dict[str, Any]:
        extra = (
            {
                "leadgen_yaml": self.leadgen_yaml,
                "leadgen_sha256": self.leadgen_sha256,
                "base_sha256": self.base_sha256,
            }
            if self.leadgen_yaml
            else {}
        )
        return {
            **extra,
            "campaign_id": self.campaign_id,
            "version": self.version,
            "sha256": self.sha256,
            "contract_version": self.contract_version,
            "overlay_yaml": self.overlay_yaml,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }


NO_CAMPAIGN_TAG = "none"


class LinkBackend(Protocol):
    def live_campaign(self) -> LiveCampaign | None: ...

    def store_events(self, events: list[EventIn]) -> list[str]:
        """Store them; returns the ids of the events stored (all of them, unless the database refused)."""
        ...


class LinkService:
    def __init__(
        self,
        db: Database,
        *,
        clock: Callable[[], float] = time.monotonic,
        respect_schedule: bool = False,
    ) -> None:
        self._db = db
        self._respect_schedule = respect_schedule
        self._lock = threading.Lock()
        self._clock = clock
        self._last_prune = float("-inf")
        self._unbuildable: set[tuple[str, int, str]] = set()  # versions whose complete file failed: say so once

    def live_campaign(self) -> LiveCampaign | None:
        with self._lock, self._db.transaction() as conn:
            row = repo.live_version_for_ma(conn, self._respect_schedule)
        if row is None:
            return None
        campaign = LiveCampaign(
            campaign_id=str(row["campaign_id"]),
            version=int(row["version_no"]),
            sha256=str(row["sha256"]),
            contract_version=str(row["contract_version"]),
            overlay_yaml=str(row["overlay_yaml"]),
            approved_by=row["approved_by"],
            approved_at=iso(row["approved_at"]),
        )
        key = (campaign.campaign_id, campaign.version, campaign.sha256)
        if key in self._unbuildable:
            return campaign
        try:
            built = complete_file(campaign.overlay_yaml, campaign.campaign_id, campaign.version)
        except ValueError:  # includes ContractViolation: serve the overlay alone rather than a file that is not valid
            self._unbuildable.add(key)
            log.warning("the complete file could not be built", exc_info=True)
            return campaign
        return replace(campaign, leadgen_yaml=built.text, leadgen_sha256=built.sha256, base_sha256=built.base_sha256)

    def store_events(self, events: list[EventIn]) -> list[str]:
        with self._lock, self._db.transaction() as conn:
            for e in events:
                repo.store_event(
                    conn,
                    event_id=str(e.event_id),
                    occurred_at=e.occurred_at,
                    kind=e.kind,
                    severity=e.severity,
                    code=e.code,
                    message=e.message,
                    campaign_id=str(e.campaign_id) if e.campaign_id else None,
                    version_no=e.version,
                    pass_id=e.pass_id,
                    dedupe_key=e.key,
                    details=e.details,
                )
            self._maybe_prune(conn)
        return [str(e.event_id) for e in events]

    def _maybe_prune(self, conn: Any) -> None:
        if self._clock() - self._last_prune < PRUNE_EVERY_S:
            return
        self._last_prune = self._clock()
        removed = repo.prune_events(conn)
        if removed:
            log.info("old events removed", extra={"removed": removed})


def rejected_payload(items: list[Rejected]) -> list[dict[str, str | None]]:
    return [{"event_id": r.event_id, "reason": r.reason} for r in items]

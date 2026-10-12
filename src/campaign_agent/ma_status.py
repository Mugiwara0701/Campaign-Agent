"""What the Marketing Agent (MA) reports about campaigns: the contract versions it can load, and what it runs now.

The MA writes one row (public.ma_campaign_status) with its heartbeat. The Campaign Agent only reads it.
No row, or an empty version list, means "not known" (the MA never reported, or is offline): that never blocks a
publish, because a campaign published while the MA is offline is applied when it returns.
"""

from dataclasses import dataclass
from typing import Any

import psycopg

from campaign_agent.store.db import Conn


@dataclass(frozen=True)
class CurrentCampaign:
    campaign_id: str | None
    version: int | None
    sha256: str | None
    applied_at: str | None
    error: str | None


@dataclass(frozen=True)
class MaStatus:
    supported_contract_versions: tuple[str, ...]
    current: CurrentCampaign | None
    reported_at: str | None

    def supports(self, contract_version: str) -> bool | None:
        """True or False when the MA told us which versions it loads; None when it did not."""
        if not self.supported_contract_versions:
            return None
        return contract_version in self.supported_contract_versions

    def applied(self, campaign_id: str, version: int, sha256: str) -> bool:
        """Is this exact version what the MA runs now? A rejection of it is not "applied"."""
        c = self.current
        return (
            c is not None
            and c.error is None
            and c.campaign_id == campaign_id
            and c.version == version
            and c.sha256 == sha256
        )

    def rejection(self, campaign_id: str, version: int) -> str | None:
        """Why the MA refused this version, if it did."""
        c = self.current
        if c and c.error and c.campaign_id == campaign_id and c.version == version:
            return c.error
        return None


def parse_status(row: dict[str, Any] | None) -> MaStatus | None:
    if row is None:
        return None
    raw = row.get("current_campaign")
    current = None
    if isinstance(raw, dict):
        version = raw.get("version")
        current = CurrentCampaign(
            campaign_id=str(raw["id"]) if raw.get("id") is not None else None,
            version=version if isinstance(version, int) and not isinstance(version, bool) else None,
            sha256=raw.get("sha256") if isinstance(raw.get("sha256"), str) else None,
            applied_at=raw.get("applied_at") if isinstance(raw.get("applied_at"), str) else None,
            error=str(raw["error"]) if raw.get("error") else None,
        )
    reported = row.get("reported_at")
    return MaStatus(
        supported_contract_versions=tuple(str(v) for v in row.get("supported_contract_versions") or ()),
        current=current,
        reported_at=reported.isoformat() if hasattr(reported, "isoformat") else None,
    )


def fetch_status(conn: Conn) -> MaStatus | None:
    """None when the MA never reported, or when migration 0016 is not applied yet (inside a savepoint, so the
    caller's transaction survives the missing table)."""
    try:
        with conn.transaction():
            row = conn.execute(
                "select supported_contract_versions, current_campaign, reported_at "
                "from public.ma_campaign_status"
            ).fetchone()
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        return None
    return parse_status(row)

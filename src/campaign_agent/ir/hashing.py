"""Stable hash of an IR: the same content gives the same hash; the revision counter is left out."""

import hashlib
import json

from campaign_agent.ir.models import CampaignIR


def ir_hash(ir: CampaignIR) -> str:
    data = ir.model_dump(mode="json")
    data["meta"].pop("revision", None)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

"""Small helpers to build IRs for tests through the real merger."""

from typing import Any

from campaign_agent.ir.models import CampaignIR, new_ir
from campaign_agent.ir.patch import IRPatch, PatchOp
from campaign_agent.merger.patch_merger import Source, TurnContext, merge_patch


def ir_with(
    *ops: tuple[str, Any], campaign_id: str = "c1", name: str = "Test campaign"
) -> CampaignIR:
    patch = IRPatch(
        ops=[PatchOp(path=p, op="add" if isinstance(v, list) else "set", value=v) for p, v in ops]
    )
    return merge_patch(new_ir(campaign_id, name), patch, TurnContext(1, Source.DIRECT)).ir

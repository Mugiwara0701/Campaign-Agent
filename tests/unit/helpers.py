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


# --- model replies for the extractor stub ------------------------------------------------------------------


def op_json(path: str, value: Any, quote: str, kind: str = "add") -> dict[str, Any]:
    return {"path": path, "op": kind, "value": value, "evidence_quote": quote}


def reply(*ops: dict[str, Any], **extra: Any) -> str:
    import json

    return json.dumps({"ops": list(ops), "unsupported": [], "research_needs": [], **extra})


COMPLETE_TEXT = "Find automotive companies building a digital cockpit on Android"
COMPLETE_REPLY = reply(
    op_json("audience.industries", ["automotive"], "automotive companies"),
    op_json("audience.products", ["digital cockpit"], "digital cockpit"),
    op_json("audience.technologies", ["Android"], "on Android"),
)
VAGUE_TEXT = "Generate a campaign for our services"

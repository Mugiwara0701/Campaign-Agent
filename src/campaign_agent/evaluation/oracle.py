"""A model that always gives the gold answer. For checking the labels and the harness, never for measuring a model."""

import json
from typing import Any

from campaign_agent.evaluation.dataset import Case, GoldOp
from campaign_agent.llm.stub import Reply, StubAdapter


def _op(op: GoldOp) -> dict[str, Any]:
    return {"path": op.path, "op": op.op, "value": op.value, "evidence_quote": op.quote}


def oracle_adapter(case: Case) -> StubAdapter:
    """One scripted reply per turn, in order. Turns the input guard rejects never reach the model."""
    needs = [{"kind": r.kind, "term": r.term} for r in case.research]
    replies: list[Reply] = []
    for i, turn in enumerate(case.turns):
        replies.append(
            json.dumps(
                {
                    "ops": [_op(o) for o in turn.ops],
                    "unsupported": [
                        {"request": u.request, "kind": u.kind} for u in turn.unsupported
                    ],
                    "research_needs": needs if i == 0 else [],
                }
            )
        )
    return StubAdapter(replies)

"""Smoke test against a real local model. Not part of CI: run with LLM_LIVE=1 pytest -m integration."""

import os

import pytest

from campaign_agent.extractor.extractor import Extractor, Status
from campaign_agent.ir.models import new_ir
from campaign_agent.llm import make_adapter
from campaign_agent.merger.patch_merger import Source, TurnContext, merge_patch
from campaign_agent.settings import load_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("LLM_LIVE") != "1", reason="set LLM_LIVE=1 to call the local model"
    ),
]

TEXT = "Find robotics companies building warehouse robots on Linux. Skip anyone in France."


def test_a_real_model_produces_a_valid_patch_for_a_simple_campaign() -> None:
    result = Extractor(make_adapter(load_settings())).run(TEXT)
    assert result.status is Status.OK, result.attempts
    assert result.patch is not None
    merged = merge_patch(
        new_ir("live"), result.patch, TurnContext(1, Source.EXTRACTION, result.text)
    )
    assert merged.ir.approval.required is True
    assert merged.applied, merged.rejected
    print(merged.ir.audience, merged.ir.exclusions, merged.rejected, result.attempts)

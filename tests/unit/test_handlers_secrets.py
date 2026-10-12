"""A secret pasted into a campaign must not be copied into the stored campaign or shown back."""

import json

from campaign_agent.conversation.state import ConversationState
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.guard.input_guard import looks_like_secret
from campaign_agent.jobs.handlers import Services, Workspace, handle
from campaign_agent.jobs.requests import ErrorCode, Request
from campaign_agent.llm.stub import StubAdapter
from tests.unit import fake_secrets as fs
from tests.unit.helpers import COMPLETE_REPLY


def run(name: str, detail: str):  # type: ignore[no-untyped-def]
    services = Services(Extractor(StubAdapter([COMPLETE_REPLY]), sleep=lambda _s: None))
    request = Request("r1", "c1", "interpret", {}, None, "u")
    return handle(
        request, Workspace("c1", name, detail, None, ConversationState()), services
    ), services


def test_a_secret_in_the_message_is_refused_and_not_copied_anywhere() -> None:
    out, services = run("Campaign", "my key is " + fs.AWS)
    assert out.error is not None
    assert out.error.code is ErrorCode.INPUT_REJECTED
    assert out.ir.meta.description == ""
    stored = json.dumps(out.ir.model_dump(mode="json")) + out.view.model_dump_json()
    assert fs.AWS not in stored
    assert services.extractor._adapter.calls == []  # type: ignore[attr-defined]


def test_a_secret_in_the_name_is_left_out_too() -> None:
    out, _ = run("[CA-TEST] key " + fs.GITHUB, "Find kiosk builders in retail")
    assert out.ir.meta.name == ""
    assert fs.GITHUB not in json.dumps(out.ir.model_dump(mode="json")) + out.view.model_dump_json()


def test_an_ordinary_name_and_message_are_kept() -> None:
    out, _ = run("Retail kiosks", "Find kiosk builders in retail")
    assert (out.ir.meta.name, out.ir.meta.description) == (
        "Retail kiosks",
        "Find kiosk builders in retail",
    )


def test_the_secret_check_alone() -> None:
    assert looks_like_secret("token " + fs.GITHUB)
    assert looks_like_secret(fs.API_KEY_LINE)
    assert not looks_like_secret("Find companies that make password managers")

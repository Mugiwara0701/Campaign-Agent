import json
import logging
import sys
from pathlib import Path

from campaign_agent.conversation.state import ConversationState
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.jobs.handlers import MAX_PAYLOAD_CHARS, Services, Workspace, handle
from campaign_agent.jobs.manager import ManagerConfig
from campaign_agent.jobs.requests import ErrorCode, Request
from campaign_agent.llm.stub import StubAdapter
from campaign_agent.logs import JsonFormatter
from campaign_agent.settings import Settings
from campaign_agent.store.retention import MIN_DAYS, run_retention
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT

ROOT = Path(__file__).resolve().parents[2]


def svc() -> Services:
    return Services(Extractor(StubAdapter([COMPLETE_REPLY]), sleep=lambda _s: None))


def test_an_absurdly_large_request_is_refused_before_it_is_read() -> None:
    req = Request("r1", "c1", "interpret", {"message": "a" * (MAX_PAYLOAD_CHARS + 1)}, None, "u")
    out = handle(req, Workspace("c1", "Auto", "", None, ConversationState()), svc())
    assert out.error is not None and out.error.code is ErrorCode.INVALID_REQUEST
    assert "too large" in out.error.detail


def test_a_normal_request_is_not_refused_for_its_size() -> None:
    req = Request("r1", "c1", "interpret", {"message": COMPLETE_TEXT}, None, "u")
    out = handle(req, Workspace("c1", "Auto", COMPLETE_TEXT, None, ConversationState()), svc())
    assert out.error is None


def test_the_limits_have_safe_defaults() -> None:
    assert Settings(_env_file=None).rate_limit_per_minute == 30  # type: ignore[call-arg]
    assert ManagerConfig("w", 60, 3).rate_limit_per_minute == 30


def test_the_service_unit_is_sandboxed_and_does_not_break_the_downstream_check() -> None:
    unit = (ROOT / "deploy" / "campaign-agent.service").read_text(encoding="utf-8")
    lines = {ln.split("=")[0].strip() for ln in unit.splitlines() if "=" in ln and ln[0] != "#"}
    for needed in (
        "MemoryMax",
        "TasksMax",
        "LimitNOFILE",
        "NoNewPrivileges",
        "PrivateTmp",
        "UMask",
        "StartLimitBurst",
        "TimeoutStopSec",
    ):
        assert needed in lines, needed
    # level 6 starts the Marketing Agent's loader in a new network namespace
    assert "RestrictNamespaces" not in lines and "PrivateNetwork" not in lines


def test_the_scripts_split_the_work_that_needs_sudo() -> None:
    text = (ROOT / "deploy" / "bootstrap-ubuntu.sh").read_text(encoding="utf-8")
    assert 'PART="${PART:-all}"' in text and "all | system | user" in text
    create = (ROOT / "deploy" / "create-service-user.sh").read_text(encoding="utf-8")
    assert "disabled-password" in create and "enable-linger" in create


def test_retention_refuses_a_period_that_is_too_short_without_touching_the_database() -> None:
    lines: list[str] = []
    assert (
        run_retention("postgresql://x:y@127.0.0.1/none", MIN_DAYS - 1, apply=True, out=lines.append)
        == 2
    )
    assert lines and "at least" in lines[0]


def test_an_error_is_logged_by_class_name_only() -> None:
    try:
        raise ValueError("Find robotics companies and my password is hunter2")
    except ValueError:
        record = logging.LogRecord(
            "campaign_agent.x", logging.ERROR, "f", 1, "request crashed", (), sys.exc_info()
        )
    entry = json.loads(JsonFormatter().format(record))
    assert entry["error"] == "ValueError"
    assert "hunter2" not in json.dumps(entry) and "robotics" not in json.dumps(entry)


def test_the_bench_command_can_gate_on_the_targets(tmp_path: Path) -> None:
    from campaign_agent.__main__ import main

    out = tmp_path / "gate"
    assert main(["bench", "--oracle", "--split", "heldout", "--check", "--out", str(out)]) == 0
    assert out.with_suffix(".md").exists() and out.with_suffix(".json").exists()

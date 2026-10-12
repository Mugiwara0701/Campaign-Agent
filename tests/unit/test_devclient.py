import pytest

from campaign_agent.__main__ import main
from campaign_agent.conversation.state import ConversationState
from campaign_agent.devclient import TEST_PREFIX, format_view, parse_answer
from campaign_agent.ir.models import new_ir
from campaign_agent.questions.models import Choice, Effect, Question
from campaign_agent.view import build_view

QUESTION = Question(
    id="q_1",
    key="k",
    kind="gap",
    text="Which industries?",
    choices=(Choice(id="c0", label="automotive"), Choice(id="c1", label="healthcare")),
    multi=True,
    text_effect=Effect(),
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("c0", {"question_id": "q_1", "value": "c0"}),
        (" c1 ", {"question_id": "q_1", "value": "c1"}),
        ("c0, c1", {"question_id": "q_1", "value": ["c0", "c1"]}),
        ("skip", {"question_id": "q_1", "skip": True}),
        ("SKIP", {"question_id": "q_1", "skip": True}),
        ("in-vehicle infotainment", {"question_id": "q_1", "value": "in-vehicle infotainment"}),
        ("c0, nonsense", {"question_id": "q_1", "value": "c0, nonsense"}),
    ],
)
def test_what_a_person_types_becomes_the_answer_the_dashboard_would_send(raw: str, expected: dict) -> None:  # type: ignore[type-arg]
    assert parse_answer(QUESTION, raw) == expected


def test_the_review_is_printed_as_plain_text() -> None:
    ir = new_ir("c1", "Test", "d")
    lines = format_view(build_view(ir, ConversationState(), candidate=None, not_applied=()))
    assert lines[0].startswith("State: DRAFT")
    assert any("Test" in line for line in lines)


def test_test_campaigns_are_recognisable() -> None:
    assert TEST_PREFIX == "[CA-TEST] "


def test_the_test_client_refuses_to_run_off_the_office_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CA_ON_OFFICE_MACHINE", raising=False)
    assert main(["try", "hello"]) == 2
    assert "office machine" in capsys.readouterr().err


def test_the_test_client_needs_a_database_and_a_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CA_ON_OFFICE_MACHINE", "1")
    for key in ("DATABASE_URL", "MIGRATE_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    assert main(["try", "hello"]) == 2
    assert "is not set" in capsys.readouterr().err
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    assert main(["try"]) == 2
    assert "Give a message" in capsys.readouterr().err

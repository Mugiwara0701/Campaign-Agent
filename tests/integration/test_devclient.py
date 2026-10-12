"""The test client against a real database and a real worker (with a scripted model)."""

import pytest

from campaign_agent.devclient import TEST_PREFIX, DevClient
from tests.integration.helpers import new_campaign, state_row, versions, worker
from tests.unit import fake_secrets as fs
from tests.unit.helpers import COMPLETE_REPLY, COMPLETE_TEXT, VAGUE_TEXT, op_json, reply

pytestmark = pytest.mark.integration


def make(db, w, answers):  # type: ignore[no-untyped-def]
    lines: list[str] = []
    typed = iter(answers)
    client = DevClient(db, out=lines.append, ask=lambda _p: next(typed), tick=w.run_once, timeout_s=60)
    return client, lines


def text(lines) -> str:  # type: ignore[no-untyped-def]
    return "\n".join(lines)


def only_campaign_id(db):  # type: ignore[no-untyped-def]
    with db.transaction() as c:
        return str(c.execute("select id from public.campaign").fetchone()["id"])


def test_a_clear_message_goes_from_text_to_live_in_one_session(db) -> None:  # type: ignore[no-untyped-def]
    client, lines = make(db, worker(db, COMPLETE_REPLY), ["a", "approve"])
    assert client.session(COMPLETE_TEXT) == 0
    out = text(lines)
    assert "AWAITING_REVIEW" in out and "Candidate: version 1" in out
    assert "config_version" in out  # the YAML is shown
    assert "Version 1 is live." in out
    cid = only_campaign_id(db)
    assert state_row(db, cid)["current_state"] == "PUBLISHED"
    assert state_row(db, cid)["active_version_id"] is not None
    with db.transaction() as c:
        row = c.execute("select campaign_name, is_active from public.campaign").fetchone()
    assert row["campaign_name"].startswith(TEST_PREFIX)
    assert row["is_active"] is False  # a test campaign never looks active to anything else


def test_a_vague_message_is_asked_about_and_the_answers_are_sent(db) -> None:  # type: ignore[no-untyped-def]
    client, lines = make(db, worker(db, reply()), ["c0", "c0", "q"])
    assert client.session(VAGUE_TEXT) == 0
    out = text(lines)
    assert "CLARIFICATION_REQUIRED" in out
    assert "Which industries" in out
    assert "Candidate: version 1" in out  # after the answers
    assert versions(db, only_campaign_id(db))[0]["version_no"] == 1


def test_an_edit_in_words_makes_a_new_version(db) -> None:  # type: ignore[no-untyped-def]
    edit = reply(op_json("qualification.min_qualify", 55, "minimum score 55", "set"))
    client, lines = make(db, worker(db, COMPLETE_REPLY, edit), ["e", "minimum score 55", "q"])
    assert client.session(COMPLETE_TEXT) == 0
    cid = only_campaign_id(db)
    assert [v["version_no"] for v in versions(db, cid)] == [1, 2]
    assert "min_qualify: 55" in text(lines)


def test_declining_the_approval_changes_nothing(db) -> None:  # type: ignore[no-untyped-def]
    client, lines = make(db, worker(db, COMPLETE_REPLY), ["a", "no"])
    client.session(COMPLETE_TEXT)
    assert "Not approved." in text(lines)
    cid = only_campaign_id(db)
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"
    assert state_row(db, cid)["active_version_id"] is None


def test_approving_over_another_live_campaign_needs_a_typed_word(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY, COMPLETE_REPLY)
    first, _ = make(db, w, ["a", "approve"])
    first.session(COMPLETE_TEXT)
    second, lines = make(db, w, ["a", "yes please", "q"])
    second.session(COMPLETE_TEXT)
    out = text(lines)
    assert "Another campaign is live" in out and "Not approved." in out
    assert "Version" not in out.split("Not approved.")[-1] or "is live" not in out.split("Not approved.")[-1]
    third, lines = make(db, w, ["a", "replace"])
    third.session(COMPLETE_TEXT)
    with db.transaction() as c:
        live = c.execute("select count(*) as n from public.campaign_state where active_version_id is not null").fetchone()
    assert live["n"] == 1  # still only one live campaign, now the newest


def test_a_refused_message_is_reported_with_its_reason(db) -> None:  # type: ignore[no-untyped-def]
    client, lines = make(db, worker(db, COMPLETE_REPLY), [])
    client.session("my key is " + fs.AWS)
    out = text(lines)
    assert "input_rejected" in out
    assert fs.AWS not in out
    assert "Nothing was read" in out


def test_it_gives_up_waiting_and_says_how_to_check_the_worker(db) -> None:  # type: ignore[no-untyped-def]
    lines: list[str] = []
    client = DevClient(db, out=lines.append, ask=lambda _p: "q", tick=lambda: None, timeout_s=0.05)
    client.send(client.create(COMPLETE_TEXT), "interpret")  # nobody is working
    assert "Still waiting" in text(lines)
    assert "remote.sh status" in text(lines)


# --- cleaning up ----------------------------------------------------------------------------------------------------


def test_clean_removes_only_the_test_campaigns_and_everything_under_them(db) -> None:  # type: ignore[no-untyped-def]
    real = new_campaign(db, "Real campaign", "something real")
    client, lines = make(db, worker(db, COMPLETE_REPLY), ["a", "approve"])
    client.session(COMPLETE_TEXT)  # a test campaign, published
    cleaner, lines = make(db, worker(db), ["clean"])
    assert cleaner.clean() == 0
    assert "Removed 1." in text(lines)
    with db.transaction() as c:
        names = [r["campaign_name"] for r in c.execute("select campaign_name from public.campaign")]
        leftovers = [
            c.execute(f"select count(*) as n from public.{t}").fetchone()["n"]  # noqa: S608
            for t in ("campaign_state", "campaign_version", "campaign_request")
        ]
    assert names == ["Real campaign"]
    assert leftovers == [0, 0, 0]
    assert real


def test_clean_asks_first(db) -> None:  # type: ignore[no-untyped-def]
    client = DevClient(db, out=lambda _l: None, ask=lambda _p: "y", tick=lambda: None)
    client.create("a message")
    assert client.clean() == 2
    with db.transaction() as c:
        assert c.execute("select count(*) as n from public.campaign").fetchone()["n"] == 1


def test_clean_with_nothing_to_clean(db) -> None:  # type: ignore[no-untyped-def]
    lines: list[str] = []
    client = DevClient(db, out=lines.append, ask=lambda _p: "clean", tick=lambda: None)
    assert client.clean() == 0
    assert "No test campaigns" in text(lines)

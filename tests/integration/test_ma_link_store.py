"""Events and the live campaign on a real database, and what each role may do with them."""

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from campaign_agent.jobs.manager import refresh_applied
from campaign_agent.ma_link.events import EventIn, parse_event
from campaign_agent.ma_link.service import LinkService
from campaign_agent.view import ReviewView
from tests.integration.helpers import enqueue, new_campaign, state_row, worker
from tests.integration.test_publish import approve, reach_review
from tests.unit.helpers import COMPLETE_REPLY

pytestmark = pytest.mark.integration

DENIED = psycopg.errors.InsufficientPrivilege


def make(**over: Any) -> EventIn:
    raw: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "kind": "blocker",
        "severity": "error",
        "code": "MA_CHROME_FAILED",
        "message": "Chrome did not start",
    }
    raw.update(over)
    event, rejected = parse_event(raw)
    assert event is not None, rejected
    return event


def rows(db, sql="select * from public.ma_event order by received_at"):  # type: ignore[no-untyped-def]
    with db.transaction() as c:
        return c.execute(sql).fetchall()


def test_nothing_is_live_until_a_version_is_approved(db) -> None:  # type: ignore[no-untyped-def]
    service = LinkService(db)
    assert service.live_campaign() is None
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    assert service.live_campaign() is None  # reviewed, not live
    approve(db, w, cid, "a1")
    live = service.live_campaign()
    assert live is not None and live.campaign_id == cid and live.version == 1
    assert len(live.sha256) == 64 and live.overlay_yaml and live.approved_by == "priya@example.com"


def test_the_dashboards_switch_and_dates_gate_the_live_campaign_only_when_asked(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    default, strict = LinkService(db), LinkService(db, respect_schedule=True)

    def campaign(sql: str) -> None:
        with db.transaction() as conn:
            conn.execute(f"update public.campaign set {sql} where id = %s", (cid,))  # noqa: S608

    campaign("is_active = false")
    assert default.live_campaign() is not None  # approval alone makes it live
    assert strict.live_campaign() is None  # switched off
    campaign("is_active = true, start_date = current_date, end_date = current_date + 5")
    assert strict.live_campaign() is not None
    campaign("start_date = current_date + 1, end_date = current_date + 5")
    assert strict.live_campaign() is None  # not started yet
    campaign("start_date = current_date - 9, end_date = current_date - 1")
    assert strict.live_campaign() is None  # ended
    assert default.live_campaign() is not None


def test_the_same_event_id_is_stored_once(db) -> None:  # type: ignore[no-untyped-def]
    service = LinkService(db)
    e = make()
    service.store_events([e])
    service.store_events([e])
    assert len(rows(db)) == 1


def test_a_repeating_blocker_updates_one_row_and_counts(db) -> None:  # type: ignore[no-untyped-def]
    service = LinkService(db)
    service.store_events([make(dedupe_key="chrome", message="first try")])
    service.store_events([make(dedupe_key="chrome", message="third try", severity="critical")])
    [row] = rows(db)
    assert (row["message"], row["severity"], row["repeat_count"]) == ("third try", "critical", 2)


def test_a_resolved_event_closes_the_open_blocker_and_a_new_one_opens_again(db) -> None:  # type: ignore[no-untyped-def]
    service = LinkService(db)
    service.store_events([make(dedupe_key="chrome")])
    service.store_events([make(kind="resolved", severity="info", message="back", dedupe_key="chrome")])
    blockers = rows(db, "select * from public.ma_event where kind = 'blocker'")
    assert blockers[0]["resolved_at"] is not None
    service.store_events([make(dedupe_key="chrome", message="again")])
    open_now = rows(db, "select * from public.ma_event where kind = 'blocker' and resolved_at is null")
    assert [r["message"] for r in open_now] == ["again"]


def test_different_dedupe_keys_are_different_blockers(db) -> None:  # type: ignore[no-untyped-def]
    service = LinkService(db)
    service.store_events([make(dedupe_key="a"), make(dedupe_key="b")])
    assert len(rows(db)) == 2


def test_old_events_are_pruned_and_open_blockers_are_kept(db) -> None:  # type: ignore[no-untyped-def]
    service = LinkService(db)
    service.store_events(
        [
            make(kind="info", severity="info", code="MA_NOTE", dedupe_key="info-old"),
            make(dedupe_key="open-old"),
            make(kind="applied", severity="info", code="MA_CAMPAIGN_APPLIED", dedupe_key="applied-old"),
        ]
    )
    with db.transaction() as c:
        c.execute("update public.ma_event set received_at = now() - interval '40 days'")
    service._last_prune = float("-inf")
    service.store_events([make(kind="info", severity="info", code="MA_NOTE", dedupe_key="fresh")])
    kept = {r["dedupe_key"] for r in rows(db)}
    assert kept == {"open-old", "fresh"}


def test_the_database_refuses_what_the_validator_would_have_stopped(db) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction() as c:
        c.execute(
            "insert into public.ma_event (event_id, occurred_at, kind, severity, code, message, dedupe_key) "
            "values (gen_random_uuid(), now(), 'command', 'info', 'MA_X', 'm', 'k')"
        )
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction() as c:
        c.execute(
            "insert into public.ma_event (event_id, occurred_at, kind, severity, code, message, dedupe_key) "
            "values (gen_random_uuid(), now(), 'info', 'info', 'CA_X', 'm', 'k')"
        )


def test_the_live_campaigns_view_lists_open_blockers_and_what_happened_to_it(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    service = LinkService(db)
    service.store_events(
        [
            make(dedupe_key="chrome"),
            make(
                kind="applied",
                severity="info",
                code="MA_CAMPAIGN_APPLIED",
                message="version 1 is running",
                campaign_id=cid,
                version=1,
                dedupe_key="applied-1",
            ),
            make(
                kind="applied",
                severity="info",
                code="MA_CAMPAIGN_APPLIED",
                message="another campaign",
                campaign_id=str(uuid.uuid4()),
                dedupe_key="applied-other",
            ),
        ]
    )
    assert refresh_applied(db) is True
    view = ReviewView.model_validate(state_row(db, cid)["review_view"])
    assert sorted(e.code for e in view.ma_events) == ["MA_CAMPAIGN_APPLIED", "MA_CHROME_FAILED"]
    assert all(e.message != "another campaign" for e in view.ma_events)
    assert refresh_applied(db) is False  # nothing new: the view is not rewritten


def test_a_closed_blocker_leaves_the_view(db) -> None:  # type: ignore[no-untyped-def]
    w = worker(db, COMPLETE_REPLY)
    cid = reach_review(db, w)
    approve(db, w, cid, "a1")
    service = LinkService(db)
    service.store_events([make(dedupe_key="chrome")])
    refresh_applied(db)
    service.store_events([make(kind="resolved", severity="info", message="ok", dedupe_key="chrome")])
    refresh_applied(db)
    view = ReviewView.model_validate(state_row(db, cid)["review_view"])
    assert view.ma_events == ()


# --- the roles ---------------------------------------------------------------------------------------------------


def as_role(pg_url: str, role: str):  # type: ignore[no-untyped-def]
    conn = psycopg.connect(pg_url)
    conn.execute(f"set role {role}")
    return conn


def test_the_dashboard_can_read_events_and_nothing_else(pg_url, db) -> None:  # type: ignore[no-untyped-def]
    LinkService(db).store_events([make()])
    conn = as_role(pg_url, "campaign_dashboard")
    try:
        assert conn.execute("select count(*) from public.ma_event").fetchone()[0] == 1
        conn.rollback()
        for sql in (
            "insert into public.ma_event (event_id, occurred_at, kind, severity, code, message, dedupe_key) "
            "values (gen_random_uuid(), now(), 'info', 'info', 'MA_X', 'm', 'k')",
            "update public.ma_event set message = 'x'",
            "delete from public.ma_event",
        ):
            with pytest.raises(DENIED):
                conn.execute(sql)
            conn.rollback()
    finally:
        conn.close()


def test_the_agent_may_only_change_the_columns_it_needs(pg_url, db) -> None:  # type: ignore[no-untyped-def]
    LinkService(db).store_events([make()])
    conn = as_role(pg_url, "campaign_agent")
    try:
        conn.execute("update public.ma_event set message = 'ok', resolved_at = now()")
        conn.rollback()
        for column in ("event_id = gen_random_uuid()", "kind = 'info'", "code = 'MA_OTHER'", "received_at = now()"):
            with pytest.raises(DENIED):
                conn.execute(f"update public.ma_event set {column}")  # noqa: S608
            conn.rollback()
    finally:
        conn.close()


def test_an_event_never_starts_work(db) -> None:  # type: ignore[no-untyped-def]
    """Storing an event adds no request: events are shown, never acted on."""
    cid = new_campaign(db)
    enqueue(db, cid)
    before = rows(db, "select count(*) as n from public.campaign_request")[0]["n"]
    LinkService(db).store_events([make(), make(kind="info", severity="info", code="MA_NOTE")])
    assert rows(db, "select count(*) as n from public.campaign_request")[0]["n"] == before

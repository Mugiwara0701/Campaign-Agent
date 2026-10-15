"""All SQL. Each function takes the connection of an open transaction.

Table design is in docs/supabase-tables.md. Nothing here knows about campaigns' meaning: it moves rows.
"""

from dataclasses import dataclass
from typing import Any, NamedTuple

import psycopg
from psycopg.types.json import Jsonb

from campaign_agent.store.db import Conn

Row = dict[str, Any]


# --- requests (the inbox) ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewRequest:
    campaign_id: str
    request_type: str
    requested_by: str
    idempotency_key: str
    payload: dict[str, Any]
    base_revision: int | None = None


def enqueue_request(conn: Conn, req: NewRequest) -> Row:
    """What the dashboard does (tests and the dev CLI use this too). A repeated key returns the first row."""
    row = conn.execute(
        """
        insert into public.campaign_request
            (campaign_id, request_type, payload, base_revision, requested_by, idempotency_key)
        values (%s, %s, %s, %s, %s, %s)
        on conflict (campaign_id, idempotency_key) do nothing
        returning *
        """,
        (
            req.campaign_id,
            req.request_type,
            Jsonb(req.payload),
            req.base_revision,
            req.requested_by,
            req.idempotency_key,
        ),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "select * from public.campaign_request where campaign_id = %s and idempotency_key = %s",
            (req.campaign_id, req.idempotency_key),
        ).fetchone()
    assert row is not None  # noqa: S101
    return row


def requeue_expired(conn: Conn, max_attempts: int) -> tuple[int, int]:
    """Requests whose worker vanished: retried, or failed once they have used all their attempts."""
    failed = conn.execute(
        """
        update public.campaign_request
        set status = 'failed', error_code = 'worker_crashed',
            error_detail = 'the worker stopped before finishing, too many times',
            finished_at = now(), lease_until = null
        where status = 'processing' and lease_until < now() and attempts >= %s
        """,
        (max_attempts,),
    ).rowcount
    requeued = conn.execute(
        """
        update public.campaign_request
        set status = 'pending', claimed_by = null, claimed_at = null, lease_until = null
        where status = 'processing' and lease_until < now() and attempts < %s
        """,
        (max_attempts,),
    ).rowcount
    return requeued, failed


_CLAIM = """
with next as (
    select r.id
    from public.campaign_request r
    where r.status = 'pending'
      -- Strictly in order per campaign: nothing older is waiting and nothing is running. This alone
      -- keeps two workers off the same campaign (the second always sees the first pending or
      -- processing); skip locked keeps them off the same request.
      and not exists (
          select 1 from public.campaign_request o
          where o.campaign_id = r.campaign_id and o.id <> r.id
            and (o.status = 'processing'
                 or (o.status = 'pending' and (o.created_at, o.id) < (r.created_at, r.id)))
      )
    order by r.created_at, r.id
    limit 1
    for update skip locked
)
update public.campaign_request c
set status = 'processing', claimed_by = %s, claimed_at = now(),
    lease_until = now() + make_interval(secs => %s), attempts = c.attempts + 1
from next
where c.id = next.id
returning c.*
"""


def count_recent_requests(conn: Conn, requested_by: str, window_s: int, *, except_id: str) -> int:
    """How many requests this requester had picked up in the last `window_s` seconds (this one not counted)."""
    row = conn.execute(
        """
        select count(*) as n from public.campaign_request
        where requested_by = %s and id <> %s
          and claimed_at > now() - make_interval(secs => %s)
        """,
        (requested_by, except_id, window_s),
    ).fetchone()
    assert row is not None  # noqa: S101
    return int(row["n"])


def claim_request(conn: Conn, worker_id: str, lease_seconds: int) -> Row | None:
    return conn.execute(_CLAIM, (worker_id, lease_seconds)).fetchone()


def complete_request(conn: Conn, request_id: str, result: dict[str, Any]) -> None:
    conn.execute(
        """
        update public.campaign_request
        set status = 'done', result = %s, error_code = null, error_detail = null,
            finished_at = now(), lease_until = null
        where id = %s
        """,
        (Jsonb(result), request_id),
    )


def fail_request(
    conn: Conn,
    request_id: str,
    code: str,
    detail: str,
    result: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        update public.campaign_request
        set status = 'failed', error_code = %s, error_detail = %s, result = %s,
            finished_at = now(), lease_until = null
        where id = %s
        """,
        (code, detail[:500], Jsonb(result) if result is not None else None, request_id),
    )


def release_request(conn: Conn, request_id: str, detail: str, max_attempts: int) -> str:
    """An unexpected error: try again later, until the attempts run out. Returns the new status, or "gone" when the request
    no longer exists (its campaign was deleted while it was being worked on): there is nothing left to release."""
    row = conn.execute(
        """
        update public.campaign_request
        set status = case when attempts >= %s then 'failed' else 'pending' end,
            error_code = case when attempts >= %s then 'internal_error' else error_code end,
            error_detail = %s,
            finished_at = case when attempts >= %s then now() else null end,
            claimed_by = null, claimed_at = null, lease_until = null
        where id = %s
        returning status
        """,
        (max_attempts, max_attempts, detail[:500], max_attempts, request_id),
    ).fetchone()
    return str(row["status"]) if row is not None else "gone"


# --- campaign and working state --------------------------------------------------------------------------------


def get_campaign(conn: Conn, campaign_id: str) -> Row | None:
    return conn.execute(
        "select id, campaign_name, campaign_detail, is_active from public.campaign where id = %s",
        (campaign_id,),
    ).fetchone()


def get_state(conn: Conn, campaign_id: str, *, lock: bool = False) -> Row | None:
    if lock:
        query = "select * from public.campaign_state where campaign_id = %s for update"
    else:
        query = "select * from public.campaign_state where campaign_id = %s"
    return conn.execute(query, (campaign_id,)).fetchone()


def set_progress(conn: Conn, campaign_id: str, state: str) -> None:
    """Show the dashboard that work started (INTERPRETING ...). Creates the row for a new campaign."""
    conn.execute(
        """
        insert into public.campaign_state (campaign_id, current_state) values (%s, %s)
        on conflict (campaign_id) do update set current_state = excluded.current_state, updated_at = now()
        """,
        (campaign_id, state),
    )


def save_state(
    conn: Conn,
    campaign_id: str,
    *,
    state: str,
    revision: int,
    ir: dict[str, Any],
    conversation: dict[str, Any],
    review_view: dict[str, Any],
    candidate_version_id: str | None,
) -> None:
    conn.execute(
        """
        insert into public.campaign_state
            (campaign_id, current_state, revision, ir, conversation, review_view, candidate_version_id)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (campaign_id) do update set
            current_state = excluded.current_state, revision = excluded.revision, ir = excluded.ir,
            conversation = excluded.conversation, review_view = excluded.review_view,
            candidate_version_id = excluded.candidate_version_id, updated_at = now()
        """,
        (
            campaign_id,
            state,
            revision,
            Jsonb(ir),
            Jsonb(conversation),
            Jsonb(review_view),
            candidate_version_id,
        ),
    )


# --- versions ------------------------------------------------------------------------------------------------------


def latest_version(conn: Conn, campaign_id: str) -> Row | None:
    return conn.execute(
        "select * from public.campaign_version where campaign_id = %s order by version_no desc limit 1",
        (campaign_id,),
    ).fetchone()


def insert_candidate(
    conn: Conn,
    campaign_id: str,
    *,
    overlay_yaml: str,
    sha256: str,
    ir_hash: str,
    contract_version: str,
    validation_report: dict[str, Any],
    not_applied: list[dict[str, Any]],
    ir_snapshot: dict[str, Any] | None = None,
) -> Row:
    """Freeze a candidate. The same text as the newest unapproved candidate is reused, not stored again."""
    latest = latest_version(conn, campaign_id)
    if latest is not None and latest["sha256"] == sha256 and latest["approved_at"] is None:
        return latest
    row = conn.execute(
        """
        insert into public.campaign_version
            (campaign_id, version_no, overlay_yaml, sha256, ir_hash, contract_version,
             validation_report, not_applied, ir_snapshot)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning *
        """,
        (
            campaign_id,
            (latest["version_no"] if latest else 0) + 1,
            overlay_yaml,
            sha256,
            ir_hash,
            contract_version,
            Jsonb(validation_report),
            Jsonb(not_applied),
            Jsonb(ir_snapshot) if ir_snapshot is not None else None,
        ),
    ).fetchone()
    assert row is not None  # noqa: S101
    return row


def get_version(conn: Conn, version_id: str) -> Row | None:
    return conn.execute(
        "select * from public.campaign_version where id = %s", (version_id,)
    ).fetchone()


def versions_summary(conn: Conn, campaign_id: str) -> list[Row]:
    """Every version of a campaign, oldest first, with whether it is approved and whether it is live."""
    return conn.execute(
        """
        select v.version_no, v.sha256, v.approved_by, v.approved_at, v.created_at,
               (s.active_version_id = v.id) as live
        from public.campaign_version v
        left join public.campaign_state s on s.campaign_id = v.campaign_id
        where v.campaign_id = %s
        order by v.version_no
        """,
        (campaign_id,),
    ).fetchall()


def update_view(conn: Conn, campaign_id: str, review_view: dict[str, Any]) -> None:
    conn.execute(
        "update public.campaign_state set review_view = %s, updated_at = now() where campaign_id = %s",
        (Jsonb(review_view), campaign_id),
    )


# --- publishing: only through the database functions, which enforce the rules ----------------------------------------


def publish(
    conn: Conn, campaign_id: str, version_no: int, sha256: str, approver: str
) -> dict[str, Any]:
    row = conn.execute(
        "select public.ca_publish(%s, %s, %s, %s) as result",
        (campaign_id, version_no, sha256, approver),
    ).fetchone()
    assert row is not None  # noqa: S101
    return dict(row["result"])


def activate(conn: Conn, campaign_id: str, version_no: int) -> dict[str, Any]:
    row = conn.execute(
        "select public.ca_activate(%s, %s) as result", (campaign_id, version_no)
    ).fetchone()
    assert row is not None  # noqa: S101
    return dict(row["result"])


def deactivate(conn: Conn, campaign_id: str) -> dict[str, Any]:
    row = conn.execute("select public.ca_deactivate(%s) as result", (campaign_id,)).fetchone()
    assert row is not None  # noqa: S101
    return dict(row["result"])


# --- heartbeat --------------------------------------------------------------------------------------------------------


def beat(
    conn: Conn,
    *,
    status: str,
    agent_version: str,
    model_name: str,
    request_id: str | None,
) -> None:
    conn.execute(
        """
        insert into public.ca_heartbeat (id, last_seen, agent_status, agent_version, model_name, current_request_id)
        values (1, now(), %s, %s, %s, %s)
        on conflict (id) do update set last_seen = now(), agent_status = excluded.agent_status,
            agent_version = excluded.agent_version, model_name = excluded.model_name,
            current_request_id = excluded.current_request_id
        """,
        (status, agent_version, model_name, request_id),
    )


# --- pause and resume from the dashboard (migration 0019) -------------------------------------------------------------


class Control(NamedTuple):
    desired: str  # "running" or "paused": what the dashboard asked
    ma_resume: (
        bool | None
    )  # the Marketing Agent was running when the pause came; None = migration 0020 not applied


def read_control(conn: Conn) -> Control:
    """What the dashboard asked of the Campaign Agent (migration 0019) and whether the Marketing Agent is to be started
    again on resume (0020). A missing table or column reads as "running" / "not tracked", so a database without the
    migrations behaves as before. Inside a savepoint, so the caller's transaction survives."""
    try:
        with conn.transaction():
            row = conn.execute(
                "select desired_state, ma_resume from public.ca_control where id = 1"
            ).fetchone()
        ma_resume: bool | None = bool(row["ma_resume"]) if row else None
    except psycopg.errors.UndefinedColumn:
        try:
            with conn.transaction():
                row = conn.execute(
                    "select desired_state from public.ca_control where id = 1"
                ).fetchone()
        except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
            return Control("running", None)
        ma_resume = None
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        return Control("running", None)
    return Control("paused" if row and row["desired_state"] == "paused" else "running", ma_resume)


def read_desired_state(conn: Conn) -> str:
    return read_control(conn).desired


def set_ma_resume(conn: Conn, value: bool) -> None:
    with conn.transaction():
        conn.execute(
            "update public.ca_control set ma_resume = %s, updated_at = now() where id = 1", (value,)
        )


def report_actual_state(conn: Conn, state: str) -> None:
    """Tell the dashboard what the worker is doing now. Quiet when the table is not there."""
    try:
        with conn.transaction():
            conn.execute(
                "update public.ca_control set actual_state = %s, state_since = now(), updated_at = now() "
                "where id = 1 and actual_state <> %s",
                (state, state),
            )
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        return


# --- starting on a new campaign by itself (migration 0021) -------------------------------------------------------------


def auto_interpret_new_campaigns(conn: Conn, grace_s: int, max_age_h: int) -> list[str] | None:
    """Send the `interpret` request for each new campaign nobody has started (see the migration for exactly which).
    Returns their ids, or None when the function is not there or this role may not call it (0021 not applied)."""
    try:
        with conn.transaction():
            rows = conn.execute(
                "select started_campaign_id from public.ca_auto_interpret(%s, %s)",
                (grace_s, max_age_h),
            ).fetchall()
    except (psycopg.errors.UndefinedFunction, psycopg.errors.InsufficientPrivilege):
        return None
    return [str(r["started_campaign_id"]) for r in rows]


# --- starting the Marketing Agent's pipeline after an approval (migration 0018) ---------------------------------------


def pipeline_state(conn: Conn) -> dict[str, Any] | None:
    """The Marketing Agent's pipeline switch: what was asked and what it does. None when the table is not there or
    this role may not read it. Inside a savepoint, so the caller's transaction survives."""
    try:
        with conn.transaction():
            row = conn.execute(
                "select desired_state, actual_state from public.pipeline_control where id = 1"
            ).fetchone()
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
        return None
    return dict(row) if row else None


def request_pipeline(conn: Conn, state: str, by: str) -> bool:
    """Ask for the pipeline to run or stop, with the function the dashboard's Start button calls. False when this role
    may not (migration 0018 not applied)."""
    try:
        with conn.transaction():
            conn.execute("select * from public.request_pipeline(%s, %s)", (state, by[:200]))
    except (psycopg.errors.UndefinedFunction, psycopg.errors.InsufficientPrivilege):
        return False
    return True


def merge_request_result(conn: Conn, request_id: str, extra: dict[str, Any]) -> None:
    """Add facts to a finished request's result (what happened after it was committed)."""
    conn.execute(
        "update public.campaign_request set result = coalesce(result, '{}'::jsonb) || %s where id = %s",
        (Jsonb(extra), request_id),
    )


# --- the link to the Marketing Agent (docs/ca-ma-link.md) -------------------------------------------------------------


def live_version_for_ma(conn: Conn, respect_schedule: bool = False) -> Row | None:
    """The one live, approved version the Marketing Agent should run, or None when nothing is live.
    With `respect_schedule` the dashboard's own settings apply as well: the campaign must be switched on
    (`is_active`) and today must lie between its start and end dates."""
    return conn.execute(
        """
        select s.campaign_id, v.version_no, v.sha256, v.contract_version, v.overlay_yaml,
               v.approved_by, v.approved_at
        from public.campaign_state s
        join public.campaign_version v on v.id = s.active_version_id
        join public.campaign c on c.id = s.campaign_id
        where v.approved_at is not null
          and (not %s or (c.is_active and current_date between c.start_date and c.end_date))
        """,
        (respect_schedule,),
    ).fetchone()


def store_event(
    conn: Conn,
    *,
    event_id: str,
    occurred_at: Any,
    kind: str,
    severity: str,
    code: str,
    message: str,
    campaign_id: str | None,
    version_no: int | None,
    pass_id: str | None,
    dedupe_key: str,
    details: dict[str, Any],
) -> None:
    """Store one event. Safe to repeat: the same event_id is stored once.

    A blocker whose dedupe_key already has an open blocker updates that one (and counts the repeat) instead of adding a
    row. A `resolved` event closes the open blockers with its dedupe_key.
    """
    if conn.execute("select 1 from public.ma_event where event_id = %s", (event_id,)).fetchone():
        return
    if kind == "blocker":
        updated = conn.execute(
            """
            update public.ma_event
            set occurred_at = greatest(occurred_at, %s), severity = %s, message = %s, details = %s,
                repeat_count = repeat_count + 1
            where kind = 'blocker' and resolved_at is null and dedupe_key = %s
            returning event_id
            """,
            (occurred_at, severity, message, Jsonb(details), dedupe_key),
        ).fetchone()
        if updated is not None:
            return
    conn.execute(
        """
        insert into public.ma_event (event_id, occurred_at, kind, severity, code, message, campaign_id,
                                     version_no, pass_id, dedupe_key, details)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (event_id) do nothing
        """,
        (
            event_id,
            occurred_at,
            kind,
            severity,
            code,
            message,
            campaign_id,
            version_no,
            pass_id,
            dedupe_key,
            Jsonb(details),
        ),
    )
    if kind == "resolved":
        conn.execute(
            """
            update public.ma_event set resolved_at = %s
            where kind = 'blocker' and resolved_at is null and dedupe_key = %s
            """,
            (occurred_at, dedupe_key),
        )


def events_for_view(conn: Conn, campaign_id: str) -> list[Row]:
    """What the dashboard shows: every open blocker (newest first, at most 20) and the last few applied or rejected
    events of this campaign."""
    return conn.execute(
        """
        (select kind, severity, code, message, occurred_at, campaign_id, version_no, repeat_count,
                (resolved_at is not null) as resolved
         from public.ma_event where kind = 'blocker' and resolved_at is null
         order by occurred_at desc limit 20)
        union all
        (select kind, severity, code, message, occurred_at, campaign_id, version_no, repeat_count,
                (resolved_at is not null) as resolved
         from public.ma_event where kind in ('applied', 'rejected') and campaign_id = %s
         order by occurred_at desc limit 5)
        order by occurred_at desc
        """,
        (campaign_id,),
    ).fetchall()


def prune_events(conn: Conn) -> int:
    """Delete old events: info after 7 days, applied/rejected/resolved and closed blockers after 30. Open blockers stay."""
    return conn.execute(
        """
        delete from public.ma_event
        where (kind = 'info' and received_at < now() - interval '7 days')
           or (kind in ('applied', 'rejected', 'resolved') and received_at < now() - interval '30 days')
           or (kind = 'blocker' and resolved_at is not null and resolved_at < now() - interval '30 days')
        """
    ).rowcount

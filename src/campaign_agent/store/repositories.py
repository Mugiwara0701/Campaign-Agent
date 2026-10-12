"""All SQL. Each function takes the connection of an open transaction.

Table design is in docs/supabase-tables.md. Nothing here knows about campaigns' meaning: it moves rows.
"""

from dataclasses import dataclass
from typing import Any

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
    """An unexpected error: try again later, until the attempts run out. Returns the new status."""
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
    assert row is not None  # noqa: S101
    return str(row["status"])


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
) -> Row:
    """Freeze a candidate. The same text as the newest unapproved candidate is reused, not stored again."""
    latest = latest_version(conn, campaign_id)
    if latest is not None and latest["sha256"] == sha256 and latest["approved_at"] is None:
        return latest
    row = conn.execute(
        """
        insert into public.campaign_version
            (campaign_id, version_no, overlay_yaml, sha256, ir_hash, contract_version,
             validation_report, not_applied)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
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
        ),
    ).fetchone()
    assert row is not None  # noqa: S101
    return row


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

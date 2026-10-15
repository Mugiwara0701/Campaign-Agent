"""The gateway's database work, as the limited `campaign_dashboard` role (migrations 0014, 0019, 0022).

It writes exactly what the dashboard would write itself (a request row, a pause) and reads exactly what the dashboard may
read. A fresh connection per call: the web server answers several callers at once and one shared connection would not be safe.
"""

import uuid
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from campaign_agent.store.db import Database

ONLINE_WITHIN_S = 60  # a heartbeat older than this means the Campaign Agent is not running


class NotFound(Exception):
    pass


class Unavailable(Exception):
    """The database did not answer or refused the login. Never carries the database's own message (it can name hosts)."""


class Gateway:
    def __init__(self, url: str, *, connect: Callable[[str], Database] = Database) -> None:
        self._url = url
        self._connect = connect

    def _run(self, work: Callable[[Any], Any]) -> Any:
        db = self._connect(self._url)
        try:
            with db.transaction() as conn:
                return work(conn)
        except psycopg.OperationalError as exc:
            raise Unavailable from exc
        finally:
            db.close()

    # --- writes ---------------------------------------------------------------------------------------------------

    def send_request(
        self,
        campaign_id: str,
        request_type: str,
        payload: dict[str, Any],
        base_revision: int | None,
        requested_by: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        key = idempotency_key or uuid.uuid4().hex

        def work(conn: Any) -> dict[str, Any]:
            if (
                conn.execute(
                    "select 1 from public.campaign where id = %s", (campaign_id,)
                ).fetchone()
                is None
            ):
                raise NotFound
            row = conn.execute(
                "insert into public.campaign_request "
                "(campaign_id, request_type, payload, base_revision, requested_by, idempotency_key) "
                "values (%s, %s, %s, %s, %s, %s) on conflict (campaign_id, idempotency_key) do nothing "
                "returning id, status",
                (campaign_id, request_type, Jsonb(payload), base_revision, requested_by, key),
            ).fetchone()
            duplicate = row is None
            if duplicate:
                row = conn.execute(
                    "select id, status from public.campaign_request where campaign_id = %s and idempotency_key = %s",
                    (campaign_id, key),
                ).fetchone()
            assert row is not None  # noqa: S101
            return {"request_id": str(row["id"]), "status": row["status"], "duplicate": duplicate}

        return self._run(work)  # type: ignore[no-any-return]

    def set_ca_state(self, state: str, requested_by: str) -> dict[str, Any]:
        def work(conn: Any) -> dict[str, Any]:
            row = conn.execute(
                "select desired_state, actual_state from public.ca_request_state(%s, %s)",
                (state, requested_by),
            ).fetchone()
            return dict(row) if row else {}

        return self._run(work)  # type: ignore[no-any-return]

    # --- reads ----------------------------------------------------------------------------------------------------

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        def work(conn: Any) -> dict[str, Any]:
            if (
                conn.execute(
                    "select 1 from public.campaign where id = %s", (campaign_id,)
                ).fetchone()
                is None
            ):
                raise NotFound
            state = conn.execute(
                "select current_state, revision, review_view, updated_at from public.campaign_state where campaign_id = %s",
                (campaign_id,),
            ).fetchone()
            last = conn.execute(
                "select id, request_type, status, error_code, error_detail, result, created_at, finished_at "
                "from public.campaign_request where campaign_id = %s order by created_at desc limit 1",
                (campaign_id,),
            ).fetchone()
            return {
                "campaign_id": campaign_id,
                "state": state["current_state"] if state else "NEW",
                "revision": state["revision"] if state else None,
                "review_view": state["review_view"] if state else None,
                "last_request": dict(last) if last else None,
            }

        return self._run(work)  # type: ignore[no-any-return]

    def request_status(self, request_id: str) -> dict[str, Any]:
        def work(conn: Any) -> dict[str, Any]:
            row = conn.execute(
                "select id, campaign_id, request_type, status, error_code, error_detail, result, created_at, finished_at "
                "from public.campaign_request where id = %s",
                (request_id,),
            ).fetchone()
            if row is None:
                raise NotFound
            return dict(row)

        return self._run(work)  # type: ignore[no-any-return]

    def ca_status(self) -> dict[str, Any]:
        def work(conn: Any) -> dict[str, Any]:
            beat = conn.execute(
                "select agent_status, extract(epoch from now() - last_seen) as age_s from public.ca_heartbeat"
            ).fetchone()
            control = conn.execute(
                "select desired_state, actual_state, requested_by from public.ca_control where id = 1"
            ).fetchone()
            age = float(beat["age_s"]) if beat else None
            return {
                "online": age is not None and age <= ONLINE_WITHIN_S,
                "status": beat["agent_status"] if beat else None,
                "last_seen_s_ago": round(age, 1) if age is not None else None,
                "control": dict(control) if control else None,
            }

        return self._run(work)  # type: ignore[no-any-return]

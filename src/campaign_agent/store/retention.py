"""What the Campaign Agent keeps and what it clears after a time (policy: docs/retention.md).

Cleared: the text the dashboard sent with finished interpret, answer and edit requests (the user's words, which may
name companies or contain things people should not have pasted). The row stays, so who asked for what, and when, is
still on record. Everything else is kept: versions are the audit trail and cannot be changed, and a campaign's working
state is the campaign. Fetched web pages are never stored at all. Deleting a campaign in the dashboard deletes all of
its rows (the tables reference it with `on delete cascade`).

Run as `python -m campaign_agent retention [--days N] [--yes]`. It needs the database OWNER, because the limited
`campaign_agent` role may not change request payloads. Without `--yes` it only counts.
"""

from collections.abc import Callable

from campaign_agent.store.db import Database
from campaign_agent.store.migrate import describe

DEFAULT_DAYS = 90
MIN_DAYS = 7
CLEARED_TYPES = ("interpret", "answer", "edit")

_WHERE = """
    where status in ('done', 'failed')
      and request_type = any(%s)
      and finished_at < now() - make_interval(days => %s)
      and payload <> '{}'::jsonb
"""


def count_clearable(db: Database, days: int) -> int:
    with db.transaction() as conn:
        row = conn.execute(
            f"select count(*) as n from public.campaign_request {_WHERE}",  # noqa: S608
            (list(CLEARED_TYPES), days),
        ).fetchone()
    assert row is not None  # noqa: S101
    return int(row["n"])


def clear_old_payloads(db: Database, days: int) -> int:
    with db.transaction() as conn:
        cur = conn.execute(
            f"update public.campaign_request set payload = '{{}}'::jsonb {_WHERE}",  # noqa: S608
            (list(CLEARED_TYPES), days),
        )
        return cur.rowcount


def run_retention(url: str, days: int, *, apply: bool, out: Callable[[str], None] = print) -> int:
    if days < MIN_DAYS:
        out(
            f"Not run: keep at least {MIN_DAYS} days (a request may still be retried or investigated)."
        )
        return 2
    out(f"database: {describe(url)}")
    db = Database(url)
    try:
        n = count_clearable(db, days)
        out(
            f"{n} finished interpret/answer/edit request(s) older than {days} days still hold their text."
        )
        if not apply:
            out("Nothing changed. Add --yes to clear that text (the rows and their results stay).")
            return 0
        done = clear_old_payloads(db, days)
        out(f"Cleared the text of {done} request(s).")
    finally:
        db.close()
    return 0

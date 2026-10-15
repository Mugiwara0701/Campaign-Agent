"""The limited roles give up on a stuck transaction or lock quickly (migration 0024)."""

import psycopg
import pytest

ROLES = ("campaign_agent", "campaign_dashboard")


@pytest.mark.parametrize("role", ROLES)
def test_the_role_ends_an_abandoned_transaction_and_a_long_lock_wait(
    pg_url: str, db, role: str
) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(pg_url, autocommit=True) as conn:
        settings = (
            conn.execute("select rolconfig from pg_roles where rolname = %s", (role,)).fetchone()[0]
            or []
        )
    assert "idle_in_transaction_session_timeout=60s" in settings
    assert "lock_timeout=30s" in settings


def test_a_session_of_the_role_really_gets_them(pg_url: str, db) -> None:  # type: ignore[no-untyped-def]
    """A role's setting applies to a new login as that role. Switching role in a session does not apply it, so this
    checks the stored setting is what a login would receive."""
    with psycopg.connect(pg_url, autocommit=True) as conn:
        value = conn.execute(
            "select pg_catalog.pg_db_role_setting.setconfig from pg_catalog.pg_db_role_setting "
            "join pg_roles r on r.oid = setrole where r.rolname = 'campaign_agent'"
        ).fetchone()
    assert value is not None and "lock_timeout=30s" in value[0]

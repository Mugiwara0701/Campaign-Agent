"""The migration runner, against a throwaway database that looks like the live one: it already has the
Marketing Agent's schema_migrations rows, which must come out untouched."""

import secrets
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

from campaign_agent.__main__ import main
from campaign_agent.store.migrate import default_dir, discover, run_agent_password, run_migrate

pytestmark = pytest.mark.integration

DB = "ca_migrate_test"
MA_ROWS = [f"{i:04d}_marketing_agent_{i}.sql" for i in range(1, 13)]


@pytest.fixture
def url(pg_url: str) -> Iterator[str]:
    base = pg_url.rsplit("/", 1)[0]
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(f"drop database if exists {DB} with (force)")
        admin.execute(f"create database {DB}")
    target = f"{base}/{DB}"
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute(
            "create table public.campaign (id uuid primary key default gen_random_uuid(), campaign_name text not null, campaign_detail text)"
        )
        conn.execute(
            "create table public.schema_migrations (name text primary key, applied_at timestamptz not null default now())"
        )
        for name in MA_ROWS:
            conn.execute("insert into public.schema_migrations (name) values (%s)", (name,))
    yield target
    with psycopg.connect(pg_url, autocommit=True) as admin:
        admin.execute(f"drop database if exists {DB} with (force)")


def query(url: str, sql: str, *params: object) -> list[tuple]:
    with psycopg.connect(url) as conn:
        return conn.execute(sql, params).fetchall()


def run(url: str, **kw):  # type: ignore[no-untyped-def]
    lines: list[str] = []
    code = run_migrate(url, out=lines.append, **kw)
    return code, "\n".join(lines)


REAL = [m.name for m in discover(default_dir())]


def test_a_plan_only_reads(url: str) -> None:
    code, text = run(url, plan_only=True)
    assert code == 0
    for name in REAL:
        assert name in text
    assert query(url, "select to_regclass('public.campaign_request')") == [
        (None,)
    ]  # nothing was created
    assert len(query(url, "select name from public.schema_migrations")) == len(MA_ROWS)


def test_applying_creates_the_tables_and_records_each_file(url: str) -> None:
    code, text = run(url, assume_yes=True)
    assert code == 0 and "Done." in text
    tables = {
        r[0] for r in query(url, "select tablename from pg_tables where schemaname = 'public'")
    }
    assert {"campaign_request", "campaign_state", "campaign_version", "ca_heartbeat"} <= tables
    recorded = [r[0] for r in query(url, "select name from public.schema_migrations order by name")]
    assert recorded == sorted(MA_ROWS + REAL)
    assert all(
        r[0] is not None for r in query(url, "select applied_at from public.schema_migrations")
    )


def test_the_marketing_agents_rows_are_left_alone(url: str) -> None:
    before = query(
        url,
        "select name, applied_at from public.schema_migrations where name = any(%s) order by name",
        MA_ROWS,
    )
    run(url, assume_yes=True)
    after = query(
        url,
        "select name, applied_at from public.schema_migrations where name = any(%s) order by name",
        MA_ROWS,
    )
    assert before == after


def test_running_it_again_does_nothing(url: str) -> None:
    run(url, assume_yes=True)
    counts = query(url, "select count(*) from public.schema_migrations")
    code, text = run(url, assume_yes=True)
    assert code == 0 and "Up to date" in text
    assert query(url, "select count(*) from public.schema_migrations") == counts


def test_files_that_were_applied_by_hand_are_just_recorded(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for m in discover(default_dir()):
            conn.execute(m.sql)  # what someone pasting them into the SQL editor does
    code, _ = run(url, assume_yes=True)
    assert code == 0
    assert [
        r[0]
        for r in query(url, "select name from public.schema_migrations where name = any(%s)", REAL)
    ] != []
    assert len(query(url, "select name from public.schema_migrations")) == len(MA_ROWS) + len(REAL)


def test_a_failing_migration_is_undone_as_a_whole_and_stops_the_rest(
    url: str, tmp_path: Path
) -> None:
    (tmp_path / "0001_ok.sql").write_text("create table public.t1 (a int);")
    (tmp_path / "0002_bad.sql").write_text("create table public.t2 (a int); select 1 / 0;")
    (tmp_path / "0003_never.sql").write_text("create table public.t3 (a int);")
    code, text = run(url, directory=tmp_path, assume_yes=True)
    assert code == 1
    assert "FAILED" in text and "0002_bad.sql" in text and "DivisionByZero" in text
    assert query(url, "select to_regclass('public.t1')") == [("t1",)]  # the one before stays
    assert query(url, "select to_regclass('public.t2')") == [
        (None,)
    ]  # the failing one left nothing behind
    assert query(url, "select to_regclass('public.t3')") == [(None,)]  # and nothing after it ran
    recorded = {r[0] for r in query(url, "select name from public.schema_migrations")}
    assert "0001_ok.sql" in recorded
    assert not recorded & {"0002_bad.sql", "0003_never.sql"}


def test_it_asks_first_and_only_a_typed_word_counts(url: str) -> None:
    for answer in ("", "y", "yes", "MIGRATE", "migrat"):
        code, text = run(url, ask=lambda _p, a=answer: a, interactive=True)
        assert code == 2 and "Cancelled" in text
    assert query(url, "select to_regclass('public.campaign_request')") == [(None,)]
    code, text = run(url, ask=lambda _p: "migrate", interactive=True)
    assert code == 0 and "Done." in text
    assert query(url, "select to_regclass('public.campaign_request')") == [("campaign_request",)]


def test_without_a_terminal_it_refuses_unless_told_yes(url: str) -> None:
    code, text = run(url, interactive=False)
    assert code == 2 and "Not applying" in text
    assert query(url, "select to_regclass('public.campaign_request')") == [(None,)]


def test_the_password_is_never_printed(url: str) -> None:
    password = urlparse(url).password or ""
    _, text = run(url, plan_only=True)
    assert password
    assert f":{password}@" not in text
    assert (
        f"{password}@" not in text
    )  # (the database is called ca_migrate_test, so the bare word may appear)


# --- the password of the limited role -----------------------------------------------------------------------------------------


def test_the_limited_role_gets_a_working_login(url: str) -> None:
    run(url, assume_yes=True)
    password = secrets.token_urlsafe(24)  # random, URL-safe, and valid for the role
    lines: list[str] = []
    assert run_agent_password(url, prompt=lambda _p: password, out=lines.append) == 0
    text = "\n".join(lines)
    assert password not in text  # not echoed
    assert "campaign_agent" in text and "<the-password>" in text
    parsed = urlparse(url)
    login = f"postgresql://campaign_agent:{password}@{parsed.hostname}:{parsed.port}/{DB}"
    with psycopg.connect(login) as conn:
        assert conn.execute("select current_user").fetchone() == ("campaign_agent",)
        assert conn.execute("select count(*) from public.campaign").fetchone() == (
            0,
        )  # it can do its job
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from public.schema_migrations")  # and nothing else


@pytest.mark.parametrize("entries", [["short", "short"], ["a" * 24, "b" * 24]])
def test_a_weak_or_mismatched_password_is_refused(url: str, entries: list[str]) -> None:
    run(url, assume_yes=True)
    answers = iter(entries)
    lines: list[str] = []
    assert run_agent_password(url, prompt=lambda _p: next(answers), out=lines.append) == 1
    assert "Not set" in "\n".join(lines)


# --- the command line ----------------------------------------------------------------------------------------------------------


def test_the_command_reads_migrate_database_url_before_database_url(
    url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none"
    )  # would fail if it were used
    monkeypatch.setenv("MIGRATE_DATABASE_URL", url)
    assert main(["migrate", "--plan"]) == 0
    out = capsys.readouterr().out
    assert DB in out and "to apply" in out
    assert main(["migrate", "--yes"]) == 0
    assert query(url, "select to_regclass('public.campaign_request')") == [("campaign_request",)]
    assert main(["migrate", "--yes"]) == 0
    assert "Up to date" in capsys.readouterr().out

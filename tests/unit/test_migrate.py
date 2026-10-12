import secrets
from pathlib import Path

import pytest

from campaign_agent.__main__ import main
from campaign_agent.store.migrate import (
    agent_url_hint,
    default_dir,
    describe,
    discover,
    valid_password,
)

POOLER = "postgresql://postgres.abcdefgh:p%40ssw0rd-secret@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres"


def test_the_real_migrations_are_found_in_order() -> None:
    names = [m.name for m in discover(default_dir())]
    assert names == sorted(names)
    assert names.index("0013_campaign_agent_tables.sql") < names.index("0014_campaign_agent_worker.sql")
    assert {"0013_campaign_agent_tables.sql", "0014_campaign_agent_worker.sql"} <= set(names)


def test_a_misnamed_file_is_refused_instead_of_being_skipped_silently(tmp_path: Path) -> None:
    (tmp_path / "0001_ok.sql").write_text("select 1;")
    (tmp_path / "notes.sql").write_text("select 1;")
    with pytest.raises(ValueError, match="notes.sql"):
        discover(tmp_path)


def test_other_files_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "0001_ok.sql").write_text("select 1;")
    (tmp_path / "README.md").write_text("x")
    assert [m.name for m in discover(tmp_path)] == ["0001_ok.sql"]


def test_the_target_has_no_password() -> None:
    target = describe(POOLER)
    assert (target.user, target.host, target.port, target.database) == (
        "postgres.abcdefgh",
        "aws-0-ap-southeast-2.pooler.supabase.com",
        6543,
        "postgres",
    )
    assert "ssw0rd" not in str(target) + repr(target)
    assert str(target) == "postgres.abcdefgh@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres"


def test_the_login_hint_for_the_limited_role() -> None:
    hint = agent_url_hint(POOLER)
    assert hint == (
        "postgresql://campaign_agent.abcdefgh:<the-password>@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres"
    )
    assert "ssw0rd" not in hint
    assert agent_url_hint("postgresql://postgres:x@db.example.com/mydb").startswith("postgresql://campaign_agent:<")


@pytest.mark.parametrize("good", [secrets.token_urlsafe(24), "a" * 20, "A1._~-" * 4])
def test_good_passwords(good: str) -> None:
    assert valid_password(good)


@pytest.mark.parametrize("bad", ["short", "a" * 19, "a" * 101, "has space " * 3, "quote'" + "a" * 20, "at@" + "a" * 20, "slash/" + "a" * 20, ""])
def test_bad_passwords(bad: str) -> None:
    assert not valid_password(bad)


def test_the_commands_need_a_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    for key in ("DATABASE_URL", "MIGRATE_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    assert main(["migrate", "--plan"]) == 2
    assert main(["agent-password"]) == 2
    assert "is not set" in capsys.readouterr().err


def test_a_missing_role_is_reported_as_run_migrate_first() -> None:
    from contextlib import contextmanager

    import psycopg

    from campaign_agent.store.migrate import MigrationError, set_agent_password

    class NoRole:
        @contextmanager
        def transaction(self):  # type: ignore[no-untyped-def]
            raise psycopg.errors.UndefinedObject('role "campaign_agent" does not exist')
            yield

    with pytest.raises(MigrationError, match="run migrate first"):
        set_agent_password(NoRole(), "a" * 24)  # type: ignore[arg-type]


def test_a_bad_password_is_refused_before_the_database_is_touched() -> None:
    from campaign_agent.store.migrate import set_agent_password

    class Untouchable:
        def transaction(self):  # type: ignore[no-untyped-def]
            raise AssertionError("must not connect")

    with pytest.raises(ValueError, match="20 to 100"):
        set_agent_password(Untouchable(), "weak")  # type: ignore[arg-type]

"""Applies the SQL files in migrations/ to a database, in order, once each.

Used as `python -m campaign_agent migrate` (from the laptop: `bash deploy/remote.sh migrate`).

Rules:
  * Applied migrations are recorded in public.schema_migrations (name, applied_at), the table the Marketing
    Agent's own migrations already use, so one list says what the database has. Existing rows are never touched.
  * Each migration runs in ONE transaction together with its record: it is applied completely or not at all.
    It stops at the first failure; later files are not tried.
  * Every file is written to be safe to run twice, so a migration that was run by hand before is simply
    recorded when it runs here.
  * Two runners at once cannot interleave: each migration takes a transaction-level advisory lock first.
  * `--plan` only reads. The connection string's password is never printed.
"""

import getpass
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg
from psycopg import sql

from campaign_agent.store.db import Conn, Database

LOCK_KEY = 727_274  # arbitrary; the same for every runner of this project's migrations
_NAME = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")
_PASSWORD = re.compile(
    r"^[A-Za-z0-9._~-]{20,100}$"
)  # URL-safe, so it can go into DATABASE_URL as it is


class MigrationError(RuntimeError):
    def __init__(self, name: str, kind: str, detail: str) -> None:
        super().__init__(f"{name}: {kind}: {detail}")
        self.name = name


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str


@dataclass(frozen=True)
class Target:
    """Where the migrations would go. Deliberately has no password."""

    user: str
    host: str
    port: int | None
    database: str

    def __str__(self) -> str:
        port = f":{self.port}" if self.port else ""
        return f"{self.user}@{self.host}{port}/{self.database}"


def default_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "migrations"


def discover(directory: Path) -> list[Migration]:
    files = sorted(directory.glob("*.sql"))
    bad = [f.name for f in files if not _NAME.match(f.name)]
    if bad:
        raise ValueError(f"migration files must be named 0001_some_name.sql: {bad}")
    return [Migration(f.name, f.read_text(encoding="utf-8")) for f in files]


def describe(url: str) -> Target:
    parsed = urlparse(url)
    return Target(
        user=unquote(parsed.username or ""),
        host=parsed.hostname or "",
        port=parsed.port,
        database=(parsed.path or "/").lstrip("/") or "postgres",
    )


def agent_url_hint(owner_url: str, role: str = "campaign_agent") -> str:
    """The DATABASE_URL shape for the limited role, with a placeholder where the password goes."""
    target = describe(owner_url)
    suffix = target.user.split(".", 1)[1] if "." in target.user else ""
    user = f"{role}.{suffix}" if suffix else role
    port = f":{target.port}" if target.port else ""
    return f"postgresql://{user}:<the-password>@{target.host}{port}/{target.database}"


def _ensure_table(conn: Conn) -> None:
    conn.execute(
        "create table if not exists public.schema_migrations "
        "(name text primary key, applied_at timestamptz not null default now())"
    )


def applied_names(db: Database) -> list[str]:
    """What the database already has. Reads only: creates nothing."""
    with db.transaction() as conn:
        row = conn.execute("select to_regclass('public.schema_migrations') as t").fetchone()
        if row is None or row["t"] is None:
            return []
        return [
            r["name"]
            for r in conn.execute("select name from public.schema_migrations order by name")
        ]


def pending(db: Database, migrations: list[Migration]) -> list[Migration]:
    done = set(applied_names(db))
    return [m for m in migrations if m.name not in done]


def apply_one(db: Database, migration: Migration) -> bool:
    """Apply one migration and record it, in one transaction. Returns False if it was already recorded."""
    try:
        with db.transaction() as conn:
            conn.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
            _ensure_table(conn)
            if conn.execute(
                "select 1 from public.schema_migrations where name = %s", (migration.name,)
            ).fetchone():
                return False
            conn.execute(migration.sql)  # no parameters: the files may contain % and $$
            conn.execute(
                "insert into public.schema_migrations (name, applied_at) values (%s, now())",
                (migration.name,),
            )
    except psycopg.Error as exc:
        raise MigrationError(
            migration.name, type(exc).__name__, str(exc).splitlines()[0][:300]
        ) from exc
    return True


def valid_password(password: str) -> bool:
    return bool(_PASSWORD.match(password))


def set_agent_password(db: Database, password: str, role: str = "campaign_agent") -> None:
    """Give a limited role a login and this password: campaign_agent (migration 0013) or campaign_dashboard (0014)."""
    if role not in ("campaign_agent", "campaign_dashboard"):
        raise ValueError("only the two limited roles can be given a login here")
    if not valid_password(password):
        raise ValueError(
            "the password must be 20 to 100 characters from A-Z a-z 0-9 . _ ~ - (it goes into a URL)"
        )
    try:
        with db.transaction() as conn:
            conn.execute(
                sql.SQL("alter role {} with login password {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
    except psycopg.errors.UndefinedObject as exc:
        raise MigrationError(
            role, "UndefinedObject", "the role does not exist: run migrate first"
        ) from exc


# --- command line ----------------------------------------------------------------------------------------------------------


def run_migrate(
    url: str,
    *,
    directory: Path | None = None,
    plan_only: bool = False,
    assume_yes: bool = False,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    interactive: bool | None = None,
) -> int:
    """0 = done, up to date, or planned; 1 = a migration failed; 2 = cancelled or cannot ask."""
    db = Database(url)
    try:
        migrations = discover(directory or default_dir())
        todo = pending(db, migrations)
        have = applied_names(db)
        out(f"database: {describe(url)}")
        out(f"already recorded: {len(have)}; files here: {len(migrations)}")
        if not todo:
            out("Up to date: nothing to apply.")
            return 0
        out("to apply, in this order:")
        for m in todo:
            out(f"  {m.name}")
        if plan_only:
            return 0
        if not assume_yes:
            if interactive is None:
                interactive = sys.stdin.isatty()
            if not interactive:
                out(
                    "Not applying: nothing can be asked here. Use --yes, or run it from a terminal."
                )
                return 2
            if ask("This changes that database. Type 'migrate' to apply: ").strip() != "migrate":
                out("Cancelled. Nothing was changed.")
                return 2
        for m in todo:
            try:
                applied = apply_one(db, m)
            except MigrationError as exc:
                out(f"FAILED  {exc}")
                out(
                    "That migration was rolled back as a whole; the ones before it stay applied. Nothing after it ran."
                )
                return 1
            out(f"applied {m.name}" if applied else f"skipped {m.name} (recorded meanwhile)")
        out("Done.")
        return 0
    finally:
        db.close()


def run_agent_password(
    url: str,
    *,
    prompt: Callable[[str], str] = getpass.getpass,
    out: Callable[[str], None] = print,
    role: str = "campaign_agent",
) -> int:
    out(f"database: {describe(url)}")
    out(
        f"Sets the login password of the limited '{role}' role "
        + ("(the worker uses it, not the owner)." if role == "campaign_agent" else "(the dashboard gateway uses it, not the owner).")
    )
    out('Make a good one with:  python3 -c "import secrets; print(secrets.token_urlsafe(24))"')
    first = prompt("New password (not shown): ")
    if not valid_password(first):
        out("Not set: use 20 to 100 characters from A-Z a-z 0-9 . _ ~ -")
        return 1
    if prompt("Again: ") != first:
        out("Not set: the two entries differ.")
        return 1
    db = Database(url)
    try:
        set_agent_password(db, first, role)
    except MigrationError as exc:
        out(f"FAILED  {exc}")
        return 1
    finally:
        db.close()
    if role == "campaign_agent":
        out(
            "Done. Put it in the .env that goes to the office machine, then: bash deploy/remote.sh push-env && bash deploy/remote.sh up"
        )
        out(f"  DATABASE_URL={agent_url_hint(url)}")
    else:
        out("Done. Put it in the gateway's environment on Render (never in the dashboard's browser code):")
        out(f"  API_DATABASE_URL={agent_url_hint(url, role)}")
    return 0

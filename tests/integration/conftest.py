"""A real PostgreSQL for the store and worker tests.

Set TEST_DATABASE_URL to a throwaway local database, for example:
    docker run -d --rm -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=x postgres:16
    TEST_DATABASE_URL=postgresql://postgres:x@127.0.0.1:55432/postgres pytest

The fixture DROPS the public schema and rebuilds it, so it refuses anything that is not on this machine.
It is deliberately a different variable from DATABASE_URL, which may point at the live database.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

from campaign_agent.store.db import Database

ROOT = Path(__file__).parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}

# The dashboard's table (dashboardbackend/migrations/003_campaign.sql) and two tables of the Marketing
# Agent, so the role tests can prove the new roles cannot touch them.
STUB_SQL = """
create table public.campaign (
    id uuid primary key default gen_random_uuid(),
    campaign_name text not null,
    campaign_detail text,
    start_date date not null default current_date,
    end_date date not null default current_date + 30,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    deleted_at timestamptz
);
alter table public.campaign enable row level security;
create table public.emails (id serial primary key, body text);
create table public.pipeline_control (id int primary key, status text);
insert into public.pipeline_control values (1, 'stopped');
"""


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "set TEST_DATABASE_URL to a throwaway local PostgreSQL to run the database tests"
        )
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        pytest.fail(
            f"TEST_DATABASE_URL points at {host!r}; these tests drop the schema, so only local is allowed"
        )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("drop schema if exists public cascade")
        conn.execute("create schema public")
        conn.execute(STUB_SQL)
        for migration in sorted((ROOT / "migrations").glob("*.sql")):
            conn.execute(migration.read_text())
    return url


@pytest.fixture
def db(pg_url: str) -> Iterator[Database]:
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("truncate public.campaign cascade")
        conn.execute("truncate public.ca_heartbeat")
        conn.execute("truncate public.ma_event")
        conn.execute(
            "update public.ca_control set desired_state = 'running', actual_state = 'running', "
            "requested_by = null, requested_at = null"
        )
        conn.execute("truncate public.emails")
    database = Database(pg_url)
    yield database
    database.close()

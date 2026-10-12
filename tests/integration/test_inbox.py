"""The inbox: enqueueing, claiming in order, one running request per campaign, crash recovery."""

import threading

import psycopg
import pytest
from psycopg.rows import dict_row

from campaign_agent.store import repositories as repo
from tests.integration.helpers import enqueue, new_campaign, request_row

pytestmark = pytest.mark.integration


def claim(db, worker="w1", lease=60):  # type: ignore[no-untyped-def]
    with db.transaction() as c:
        return repo.claim_request(c, worker, lease)


def test_the_same_idempotency_key_is_one_request(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    first = enqueue(db, cid, key="k1")
    again = enqueue(db, cid, key="k1")
    assert first["id"] == again["id"]
    with db.transaction() as c:
        assert c.execute("select count(*) as n from public.campaign_request").fetchone()["n"] == 1
    other = new_campaign(db, "Other")
    assert enqueue(db, other, key="k1")["id"] != first["id"]  # keys are per campaign


def test_requests_are_claimed_oldest_first(db) -> None:  # type: ignore[no-untyped-def]
    a, b = new_campaign(db, "A"), new_campaign(db, "B")
    first = enqueue(db, a, key="1")
    second = enqueue(db, b, key="2")
    assert claim(db)["id"] == first["id"]
    assert claim(db)["id"] == second["id"]
    assert claim(db) is None


def test_a_claim_marks_the_request_and_counts_the_attempt(db) -> None:  # type: ignore[no-untyped-def]
    req = enqueue(db, new_campaign(db))
    got = claim(db, worker="w9", lease=120)
    assert (got["status"], got["claimed_by"], got["attempts"]) == ("processing", "w9", 1)
    assert got["lease_until"] > got["claimed_at"]
    assert request_row(db, req["id"])["status"] == "processing"


def test_only_one_request_per_campaign_runs_at_a_time_and_in_order(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    first = enqueue(db, cid, key="1")
    second = enqueue(db, cid, "answer", key="2")
    assert claim(db)["id"] == first["id"]
    assert claim(db) is None  # the second waits for the first
    with db.transaction() as c:
        repo.complete_request(c, first["id"], {})
    assert claim(db)["id"] == second["id"]


def test_other_campaigns_are_not_held_up_by_a_running_one(db) -> None:  # type: ignore[no-untyped-def]
    busy, free = new_campaign(db, "busy"), new_campaign(db, "free")
    enqueue(db, busy, key="1")
    enqueue(db, busy, "answer", key="2")
    other = enqueue(db, free, key="3")
    assert claim(db) is not None  # the first of "busy"
    assert claim(db)["id"] == other["id"]


def test_two_workers_never_claim_the_same_request(pg_url, db) -> None:  # type: ignore[no-untyped-def]
    ids = {enqueue(db, new_campaign(db, f"c{i}"), key=f"k{i}")["id"] for i in range(6)}
    claimed: list[object] = []
    lock = threading.Lock()

    def run(name: str) -> None:
        with psycopg.connect(pg_url, row_factory=dict_row) as conn:  # type: ignore[arg-type]
            for _ in range(6):
                row = repo.claim_request(conn, name, 60)
                conn.commit()
                if row:
                    with lock:
                        claimed.append(row["id"])

    threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, claimed)) == sorted(map(str, ids))  # each exactly once


def test_a_worker_does_not_wait_for_a_locked_row(pg_url, db) -> None:  # type: ignore[no-untyped-def]
    one = enqueue(db, new_campaign(db, "one"), key="1")
    two = enqueue(db, new_campaign(db, "two"), key="2")
    with (
        psycopg.connect(pg_url, row_factory=dict_row) as a,
        psycopg.connect(pg_url, row_factory=dict_row) as b,
    ):  # type: ignore[arg-type]
        taken = repo.claim_request(a, "wa", 60)  # a's transaction stays open
        got = repo.claim_request(b, "wb", 60)  # must skip it, not block
        assert taken["id"] == one["id"]
        assert got["id"] == two["id"]
        a.rollback()
        b.rollback()


# --- crash recovery -----------------------------------------------------------------------------------------------------


def expire(db, request_id) -> None:  # type: ignore[no-untyped-def]
    with db.transaction() as c:
        c.execute(
            "update public.campaign_request set lease_until = now() - interval '1 minute' where id = %s",
            (request_id,),
        )


def test_a_request_whose_worker_vanished_is_run_again(db) -> None:  # type: ignore[no-untyped-def]
    req = enqueue(db, new_campaign(db))
    claim(db)
    with db.transaction() as c:
        assert repo.requeue_expired(c, 3) == (0, 0)  # the lease has not run out
    expire(db, req["id"])
    with db.transaction() as c:
        assert repo.requeue_expired(c, 3) == (1, 0)
    row = request_row(db, req["id"])
    assert (row["status"], row["claimed_by"], row["lease_until"]) == ("pending", None, None)
    assert claim(db, "w2")["attempts"] == 2


def test_a_request_that_keeps_killing_its_worker_is_failed(db) -> None:  # type: ignore[no-untyped-def]
    req = enqueue(db, new_campaign(db))
    for _ in range(3):
        assert claim(db) is not None
        expire(db, req["id"])
        with db.transaction() as c:
            repo.requeue_expired(c, 3)
    row = request_row(db, req["id"])
    assert (row["status"], row["error_code"], row["attempts"]) == ("failed", "worker_crashed", 3)
    assert claim(db) is None


def test_an_unexpected_error_releases_the_request_until_attempts_run_out(db) -> None:  # type: ignore[no-untyped-def]
    req = enqueue(db, new_campaign(db))
    states = []
    for _ in range(3):
        claim(db)
        with db.transaction() as c:
            states.append(repo.release_request(c, req["id"], "RuntimeError", 3))
    assert states == ["pending", "pending", "failed"]
    row = request_row(db, req["id"])
    assert (row["error_code"], row["error_detail"]) == ("internal_error", "RuntimeError")


def test_the_unique_key_and_status_are_enforced_by_the_database(db) -> None:  # type: ignore[no-untyped-def]
    cid = new_campaign(db)
    enqueue(db, cid, key="x")
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction() as c:
        c.execute("update public.campaign_request set status = 'nonsense'")
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction() as c:
        c.execute(
            "insert into public.campaign_request (campaign_id, request_type, requested_by, idempotency_key)"
            " values (%s, 'explode', 'u', 'z')",
            (cid,),
        )

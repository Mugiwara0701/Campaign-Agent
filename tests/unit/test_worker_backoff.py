import pytest

from campaign_agent.jobs.worker import LOGIN_BACKOFF_S, backoff_for, is_login_problem

REFUSED = [
    'connection failed: connection to server at "13.237.241.81", port 6543 failed: FATAL:  (ECIRCUITBREAKER) too many authentication failures, new connections are temporarily blocked',
    'connection failed: connection to server at "1.2.3.4", port 6543 failed: FATAL:  password authentication failed for user "campaign_agent.abc"',
    "connection failed: FATAL:  Tenant or user not found",
    'FATAL:  no pg_hba.conf entry for host "1.2.3.4", user "x", database "postgres"',
]
GLITCHES = [
    "connection failed: connection to server at 1.2.3.4, port 6543 failed: timeout expired",
    "could not translate host name",
    "server closed the connection unexpectedly",
    "",
]


@pytest.mark.parametrize("detail", REFUSED)
def test_a_refused_login_waits_ten_minutes_however_many_times_it_failed(detail: str) -> None:
    assert is_login_problem(detail)
    for failures in (1, 5, 50):
        assert backoff_for(detail, failures) == LOGIN_BACKOFF_S == 600.0


@pytest.mark.parametrize("detail", GLITCHES)
def test_other_database_errors_keep_the_short_growing_wait(detail: str) -> None:
    assert not is_login_problem(detail)
    assert [backoff_for(detail, n) for n in (1, 2, 3, 6, 7, 20)] == [2.0, 4.0, 8.0, 60.0, 60.0, 60.0]

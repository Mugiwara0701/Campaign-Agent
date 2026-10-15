from campaign_agent.console import FAIL, OK, Line, Snapshot, _ago, render
from campaign_agent.console_demo import STORY, run_demo


def test_demo_is_labelled_simulated_on_every_banner_and_touches_nothing() -> None:
    out: list[str] = []
    run_demo(out=out.append, sleep=lambda _s: None)
    assert "SIMULATED" in out[0] and "Nothing below is real" in out[0]
    assert "SIMULATED" in out[-1]
    assert len(out) == len(STORY) + 2


def test_render_shows_each_check_with_its_state() -> None:
    snap = Snapshot(
        (Line("Database", OK, "reachable"), Line("Worker", FAIL, "no heartbeat")),
        ("1m ago  interpret  done  priya",),
        (),
    )
    text = render(snap, banner="LIVE", stamp="10:00:00")
    assert "Database" in text and "no heartbeat" in text and "priya" in text
    assert "none" in text.split("Marketing Agent events")[1]


def test_ago_reads_naturally() -> None:
    assert _ago(None) == "never"
    assert _ago(5) == "5s ago"
    assert _ago(600) == "10m ago"
    assert _ago(7200) == "2h ago"

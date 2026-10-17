"""End to end, with a stub model and a stand-in Marketing Agent: the acceptance tests of M12.

The flow is the dashboard's: a message, questions, edits, approval; then the stand-in Marketing Agent (the command
`python -m agent leads queries --campaign <file>`) is run on what was published and its queries are read.
Needs a throwaway PostgreSQL (TEST_DATABASE_URL), like the integration tests.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from campaign_agent.compiler.build import build_candidate
from campaign_agent.evaluation.dataset import load_cases
from campaign_agent.evaluation.harness import gold_ir
from campaign_agent.evaluation.run import DEFAULT_DATASET
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.jobs.handlers import Services
from campaign_agent.jobs.manager import refresh_applied
from campaign_agent.jobs.worker import Worker
from campaign_agent.llm.stub import StubAdapter
from campaign_agent.validators.downstream import Downstream
from tests.integration.helpers import CFG, enqueue, new_campaign, state_row, versions
from tests.integration.test_publish import approve, live_version, send, view_of
from tests.unit.helpers import op_json, reply

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

HERE = Path(__file__).parent
FAKE_MA = HERE / "fake_ma"
ROOT = HERE.parents[1]
# The stand-in runs on the CI machine, which may not allow network namespaces: no isolation here. The isolation
# itself is covered by tests/unit/test_downstream.py.
MA = Downstream(FAKE_MA, python=sys.executable, isolate=())

ROBOTS_TEXT = "Find robotics companies building warehouse robots on Linux. Skip anyone in France."
ROBOTS = reply(
    op_json("audience.industries", ["robotics"], "robotics companies"),
    op_json("audience.products", ["warehouse robot"], "warehouse robots"),
    op_json("audience.technologies", ["Linux"], "on Linux"),
    op_json("exclusions.countries", ["France"], "Skip anyone in France"),
)
US_TEXT = "Find US companies with 500-5,000 employees building automotive infotainment on Android"
US_SIZE = reply(
    op_json("audience.industries", ["automotive"], "automotive infotainment"),
    op_json("audience.products", ["infotainment"], "automotive infotainment"),
    op_json("audience.technologies", ["Android"], "on Android"),
    op_json("audience.include_countries", ["US"], "US companies"),
    op_json("audience.employee_min", 500, "500-5,000 employees", "set"),
    op_json("audience.employee_max", 5000, "500-5,000 employees", "set"),
)
VAGUE_TEXT = "Generate a campaign for our services"
VAGUE = reply()
FINISH = reply(
    op_json("audience.industries", ["automotive"], "automotive companies"),
    op_json("audience.products", ["digital cockpit"], "digital cockpit"),
)


def worker_with(db, downstream, *replies):  # type: ignore[no-untyped-def]
    extractor = Extractor(StubAdapter(list(replies)), sleep=lambda _s: None)
    services = Services(extractor, downstream=downstream)
    return Worker(db, services, CFG, model_name="stub", sleep=lambda _s: None)


def stand_in_queries(tmp_path: Path, overlay_yaml: str) -> list[str]:
    """What the Marketing Agent prints for this campaign file at the start of a pass."""
    path = tmp_path / "campaign.yaml"
    path.write_text(overlay_yaml, encoding="utf-8")
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "agent", "leads", "queries", "--campaign", str(path)],
        cwd=FAKE_MA,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return [ln for ln in done.stdout.splitlines() if ln.strip()]


def start(db, w, text):  # type: ignore[no-untyped-def]
    cid = new_campaign(db, "Acceptance", text)
    enqueue(db, cid, "interpret", {"message": text}, key=f"i-{cid}")
    w.run_once()
    return cid


def candidate_yaml(db, cid):  # type: ignore[no-untyped-def]
    return versions(db, cid)[-1]["overlay_yaml"]


# --- the example campaigns of the design review -----------------------------------------------------------------


def test_the_robotics_example_goes_from_message_to_queries(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    w = worker_with(db, MA, ROBOTS)
    cid = start(db, w, ROBOTS_TEXT)
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"

    view = view_of(db, cid)
    assert [(lv.level, lv.status) for lv in view.candidate.levels] == [  # type: ignore[union-attr]
        (3, "passed"),
        (4, "passed"),
        (5, "passed"),
        (6, "passed"),  # the Marketing Agent's own loader accepted it
    ]
    yaml_text = candidate_yaml(db, cid)
    assert "- robotics" in yaml_text and "- warehouse robot" in yaml_text and "- Linux" in yaml_text
    assert "exclude_countries:" in yaml_text and "- France" in yaml_text
    assert view.email_approval  # every email is still approved by a person

    before = stand_in_queries(tmp_path, "config_version: '1.0'\n")
    assert approve(db, w, cid, "a1")["status"] == "done"
    assert live_version(db, cid) == 1
    published = versions(db, cid)[0]["overlay_yaml"]
    after = stand_in_queries(tmp_path, published)
    assert after != before  # the queries changed with the published campaign
    assert any("robotics" in q and "warehouse robot" in q and "Linux" in q for q in after)
    assert not any("France" in q for q in after)


def test_what_cannot_be_applied_is_reported_and_not_emitted(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    w = worker_with(db, MA, US_SIZE)
    cid = start(db, w, US_TEXT)
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"
    view = view_of(db, cid)
    reported = " ".join(n.request for n in view.not_applied)
    assert "include_countries" in reported and "employee_min" in reported
    assert "employee_max" in reported
    assert all(n.reason for n in view.not_applied)  # each one says why
    yaml_text = candidate_yaml(db, cid)
    assert "employee" not in yaml_text and "include_countries" in yaml_text  # countries are applied
    stored = versions(db, cid)[0]["not_applied"]
    assert len(stored) == len(view.not_applied)  # the same list is stored with the version
    assert any(
        "infotainment" in q and "Android" in q for q in stand_in_queries(tmp_path, yaml_text)
    )


def test_a_vague_message_asks_and_the_answer_leads_to_a_reviewable_campaign(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    w = worker_with(db, MA, VAGUE, FINISH)
    cid = start(db, w, VAGUE_TEXT)
    assert state_row(db, cid)["current_state"] == "CLARIFICATION_REQUIRED"
    view = view_of(db, cid)
    assert view.questions and view.candidate is None
    assert versions(db, cid) == []  # nothing to review, nothing stored yet

    send(
        db,
        w,
        cid,
        "edit",
        "e1",
        {"text": "automotive companies building a digital cockpit"},
        base=state_row(db, cid)["revision"],
    )
    assert state_row(db, cid)["current_state"] == "AWAITING_REVIEW"
    assert approve(db, w, cid, "a1")["status"] == "done"
    queries = stand_in_queries(tmp_path, candidate_yaml(db, cid))
    assert any("automotive" in q and "digital cockpit" in q for q in queries)


# --- invalid and offline ----------------------------------------------------------------------------------------


def rejecting_ma(tmp_path: Path) -> Downstream:
    pkg = tmp_path / "strict_ma" / "agent"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import sys\nprint('campaign file rejected: unknown key', file=sys.stderr)\nsys.exit(2)\n",
        encoding="utf-8",
    )
    return Downstream(tmp_path / "strict_ma", python=sys.executable, isolate=())


def test_a_campaign_the_marketing_agent_rejects_never_goes_live(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    w = worker_with(db, rejecting_ma(tmp_path), ROBOTS)
    cid = start(db, w, ROBOTS_TEXT)
    assert state_row(db, cid)["current_state"] == "VALIDATION_FAILED"
    view = view_of(db, cid)
    level6 = next(lv for lv in view.candidate.levels if lv.level == 6)  # type: ignore[union-attr]
    assert level6.status == "failed" and "rejected" in level6.issues[0].message
    assert view.candidate.can_freeze is False  # type: ignore[union-attr]
    assert live_version(db, cid) is None


def test_a_rejected_change_leaves_the_running_campaign_alone(db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    good = worker_with(db, MA, ROBOTS)
    cid = start(db, good, ROBOTS_TEXT)
    approve(db, good, cid, "a1")
    assert live_version(db, cid) == 1
    running = versions(db, cid)[0]["overlay_yaml"]

    strict = worker_with(
        db,
        rejecting_ma(tmp_path),
        reply(op_json("qualification.min_qualify", 55, "minimum score 55", "set")),
    )
    send(
        db,
        strict,
        cid,
        "edit",
        "e1",
        {"text": "minimum score 55"},
        base=state_row(db, cid)["revision"],
    )
    assert state_row(db, cid)["current_state"] == "VALIDATION_FAILED"
    assert live_version(db, cid) == 1  # version 1 still runs
    assert versions(db, cid)[0]["overlay_yaml"] == running
    assert view_of(db, cid).live_version == 1


def test_published_while_the_marketing_agent_is_offline_is_applied_when_it_returns(
    db, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    w = worker_with(db, MA, ROBOTS)
    cid = start(db, w, ROBOTS_TEXT)
    approve(db, w, cid, "a1")  # the Marketing Agent has never reported anything
    assert view_of(db, cid).applied is None  # unknown, not "no"
    sha = versions(db, cid)[0]["sha256"]
    with db.transaction() as c:  # it comes back and reports what it runs now
        c.execute(
            "insert into public.ma_campaign_status (supported_contract_versions, current_campaign) "
            "values (%s, %s::jsonb)",
            (
                ["1.0"],
                f'{{"id": "{cid}", "version": 1, "sha256": "{sha}", "applied_at": "2026-10-13T00:00:00Z"}}',
            ),
        )
    assert refresh_applied(db) is True
    assert view_of(db, cid).applied is True


# --- every publishable version is accepted ----------------------------------------------------------------------


def test_the_marketing_agent_accepts_every_campaign_that_passes_the_earlier_levels() -> None:
    """Downstream acceptance is 100%: no candidate that L3 to L5 let through is rejected by the loader."""
    cases = load_cases(ROOT / DEFAULT_DATASET)
    checked, rejected = 0, []
    for case in cases:
        if case.expect_status != "ok" or case.category == "adversarial":
            continue
        candidate = build_candidate(gold_ir(case), downstream=MA)
        early = [lv.status for lv in candidate.report.levels[:3]]
        if "failed" in early:
            continue  # blocked by our own checks: never reaches the Marketing Agent
        checked += 1
        if candidate.report.levels[3].status != "passed":
            rejected.append((case.id, [i.message for i in candidate.report.levels[3].issues]))
    assert checked >= 60
    assert not rejected, rejected

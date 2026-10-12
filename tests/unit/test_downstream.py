from collections.abc import Sequence
from pathlib import Path

from campaign_agent.compiler.build import build_candidate, validate_overlay_text
from campaign_agent.ma_status import MaStatus, parse_status
from campaign_agent.validators.downstream import Downstream, RunResult
from tests.unit.helpers import ir_with


class FakeRunner:
    """Stands in for the Marketing Agent's loader. Records what it was asked to run."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[tuple[Sequence[str], Path, dict[str, str], float]] = []
        self.seen_yaml = ""

    def __call__(
        self, argv: Sequence[str], cwd: Path, env: dict[str, str], timeout_s: float
    ) -> RunResult:
        self.calls.append((argv, cwd, env, timeout_s))
        if "--campaign" in argv:
            self.seen_yaml = Path(argv[list(argv).index("--campaign") + 1]).read_text()
            return self.result
        return RunResult(0, "", "")  # the "can the network be cut" probe


def downstream(tmp_path: Path, result: RunResult, **kw) -> tuple[Downstream, FakeRunner]:  # type: ignore[no-untyped-def]
    runner = FakeRunner(result)
    return Downstream(tmp_path, runner=runner, **kw), runner


def test_no_checkout_skips_level_6_visibly() -> None:
    level = Downstream(None).check("a: 1\n")
    assert level.status == "skipped" and "MA_CHECKOUT" in level.note


def test_no_checkout_fails_when_it_is_required() -> None:
    level = Downstream(None, required=True).check("a: 1\n")
    assert level.status == "failed" and level.issues[0].code == "unavailable"


def test_a_missing_directory_is_not_a_pass(tmp_path: Path) -> None:
    level = Downstream(tmp_path / "nope").check("a: 1\n")
    assert level.status == "skipped"


def test_the_loader_accepting_the_file_passes_and_gets_exact_text(tmp_path: Path) -> None:
    d, runner = downstream(tmp_path, RunResult(0, "query one\nquery two\n", ""))
    level = d.check("config_version: '1.0'\n")
    assert level.status == "passed"
    assert runner.seen_yaml == "config_version: '1.0'\n"
    argv, cwd, env, _ = runner.calls[-1]
    assert argv[:3] == ("unshare", "--net", "--map-root-user")
    assert argv[-4:-1] == ("leads", "queries", "--campaign") and cwd == tmp_path
    assert set(env) == {"PATH", "HOME", "LANG", "PYTHONDONTWRITEBYTECODE"}  # nothing secret leaks in


def test_a_rejection_shows_the_loaders_last_line(tmp_path: Path) -> None:
    d, _ = downstream(tmp_path, RunResult(2, "", "Traceback\nValidationError: unknown key 'x'\n"))
    level = d.check("x: 1\n")
    assert level.status == "failed"
    assert level.issues[0].code == "loader_rejected"
    assert "unknown key 'x'" in level.issues[0].message


def test_a_timeout_fails(tmp_path: Path) -> None:
    d, _ = downstream(tmp_path, RunResult(-9, "", "", timed_out=True))
    assert d.check("a: 1\n").issues[0].code == "timeout"


def test_accepting_but_producing_no_queries_fails(tmp_path: Path) -> None:
    d, _ = downstream(tmp_path, RunResult(0, "\n \n", ""))
    assert d.check("a: 1\n").issues[0].code == "no_queries"


def test_no_way_to_cut_the_network_is_not_a_pass(tmp_path: Path) -> None:
    class Cannot(FakeRunner):
        def __call__(self, argv, cwd, env, timeout_s):  # type: ignore[no-untyped-def]
            return RunResult(1, "", "unshare: unshare failed")

    d = Downstream(tmp_path, runner=Cannot(RunResult(0, "q", "")))
    assert d.check("a: 1\n").status == "skipped"
    assert Downstream(tmp_path, required=True, runner=Cannot(RunResult(0, "q", ""))).check(
        "a: 1\n"
    ).status == "failed"


def test_a_loader_rejection_stops_a_candidate_from_being_frozen(tmp_path: Path) -> None:
    d, _ = downstream(tmp_path, RunResult(1, "", "boom"))
    candidate = build_candidate(ir_with(("qualification.min_qualify", 55)), downstream=d)
    assert not candidate.can_freeze
    assert candidate.report.levels[-1].level == 6


def test_the_loader_is_not_run_when_an_earlier_level_failed(tmp_path: Path) -> None:
    d, runner = downstream(tmp_path, RunResult(0, "q", ""))
    report = validate_overlay_text("not: [valid", downstream=d)
    assert report.levels[-1].status == "skipped" and "earlier level" in report.levels[-1].note
    assert not any("--campaign" in c[0] for c in runner.calls)


def test_without_a_loader_the_candidate_still_freezes_with_l6_skipped() -> None:
    candidate = build_candidate(ir_with(("qualification.min_qualify", 55)))
    assert candidate.can_freeze and candidate.report.levels[-1].status == "skipped"


# --- what the Marketing Agent reports --------------------------------------------------------------------------


def status(**row: object) -> MaStatus:
    parsed = parse_status({"supported_contract_versions": ["1.0"], "current_campaign": None, **row})
    assert parsed is not None
    return parsed


def test_supported_versions() -> None:
    assert status().supports("1.0") is True
    assert status().supports("2.0") is False
    assert status(supported_contract_versions=[]).supports("1.0") is None
    assert parse_status(None) is None


def test_applied_needs_the_same_campaign_version_and_hash_and_no_error() -> None:
    s = status(current_campaign={"id": "c", "version": 2, "sha256": "h", "applied_at": "t"})
    assert s.applied("c", 2, "h")
    assert not s.applied("c", 1, "h") and not s.applied("c", 2, "other") and not s.applied("d", 2, "h")


def test_a_refused_version_is_not_applied_and_the_reason_is_kept() -> None:
    s = status(current_campaign={"id": "c", "version": 2, "sha256": "h", "error": "bad key"})
    assert not s.applied("c", 2, "h")
    assert s.rejection("c", 2) == "bad key" and s.rejection("c", 1) is None

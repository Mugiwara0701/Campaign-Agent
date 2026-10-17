"""The qa suite's own machinery: recording, case statuses, the scheduler, the findings and the PDF."""

import json
from pathlib import Path

import pytest
from qa.lib import results as R
from qa.lib.core import ERROR, FAIL, PASS, SKIP, WARN, Ctx, Recorder, Skip, StopTest, expect
from qa.lib.sched import Task, run_schedule


def make_ctx(tmp_path: Path, side: str = "ca") -> Ctx:
    rec = Recorder(tmp_path / "results.jsonl", "run1", side)
    return Ctx(run_id="run1", side=side, out_dir=tmp_path, rec=rec, env={}, log=lambda _m: None)


def rows(tmp_path: Path) -> list[dict]:
    return R.read_jsonl(tmp_path / "results.jsonl")


def test_case_statuses(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    with ctx.case("t", "good"):
        pass
    with ctx.case("t", "bad"):
        expect(False, "nope")
    with ctx.case("t", "boom"):
        raise RuntimeError("x")
    with ctx.case("t", "skipped"):
        raise Skip("no token")
    got = {r["case"]: r["status"] for r in rows(tmp_path)}
    assert got == {"good": PASS, "bad": FAIL, "boom": ERROR, "skipped": SKIP}
    assert ctx.rec.bad == 2


def test_case_timeout_is_a_failure_not_a_hang(tmp_path: Path):
    import time

    ctx = make_ctx(tmp_path)
    with ctx.case("t", "slow", timeout_s=1):
        time.sleep(5)
    assert rows(tmp_path)[0]["status"] in (FAIL, ERROR)


def test_fatal_case_stops_the_test(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(StopTest), ctx.case("t", "must work", fatal=True):
        expect(False, "broken")


def test_quick_schedule_runs_every_task_once(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    seen: list[str] = []

    def mk(name: str):
        def fn(c: Ctx) -> None:
            seen.append(name)
            with c.case(name, "c"):
                pass

        return fn

    tasks = [Task("a", mk("a"), 60), Task("b", mk("b"), None)]
    run_schedule(ctx, tasks, 1.0, tmp_path / "STOP", quick=True)
    assert seen == ["a", "b"]
    whole = [r for r in rows(tmp_path) if r["case"] == "(whole run)"]
    assert [r["status"] for r in whole] == [PASS, PASS]


def test_task_with_failed_case_is_marked_failed(tmp_path: Path):
    ctx = make_ctx(tmp_path)

    def fn(c: Ctx) -> None:
        with c.case("a", "x"):
            expect(False, "no")

    run_schedule(ctx, [Task("a", fn, None)], 1.0, tmp_path / "STOP", quick=True)
    assert next(r for r in rows(tmp_path) if r["case"] == "(whole run)")["status"] == FAIL


def test_stop_file_ends_the_schedule(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "STOP").write_text("")
    run_schedule(ctx, [Task("a", lambda _c: None, 10)], 1.0, tmp_path / "STOP", quick=False)
    assert rows(tmp_path) == []


def sample_rows(tmp_path: Path) -> list[dict]:
    ctx = make_ctx(tmp_path)
    for i in range(3):
        ctx.note("ca.x", "flaky", WARN if i else PASS, f"took {i + 10} seconds")
    ctx.note("ca.x", "broken", FAIL, "database refused connection after 31 s")
    ctx.note("ca.x", "(whole run)", FAIL, "1 case(s) failed")
    return rows(tmp_path)


def test_counts_verdict_and_findings(tmp_path: Path):
    r = sample_rows(tmp_path)
    c = R.counts(r)
    assert (c[PASS], c[WARN], c[FAIL], c["total"]) == (1, 2, 1, 4)
    assert R.verdict(c) == "FAIL"
    assert R.verdict(R.counts([x for x in r if x["status"] == PASS])) == "PASS"
    f = R.findings(r)
    assert f[0]["severity"] == "high" and f[0]["case"] == "broken"
    flaky = next(x for x in f if x["case"] == "flaky")
    assert flaky["times"] == 2  # numbers are ignored when grouping the same problem


def test_reconcile_finds_a_missing_event():
    ma = [
        {
            "test": "ma.link",
            "case": "sent",
            "status": PASS,
            "data": {"codes": ["MA_QAM_1", "MA_QAM_2"]},
        }
    ]
    ca = [{"test": "ca.link", "case": "stored", "status": PASS, "data": {"codes": ["MA_QAM_1"]}}]
    rec = R.reconcile(ca, ma)
    assert isinstance(rec, dict) and "missing" in rec


def test_load_side_tolerates_missing_and_cut_lines(tmp_path: Path):
    assert R.load_side(None)["results"] == []
    (tmp_path / "results.jsonl").write_text(json.dumps({"a": 1}) + "\n{cut")
    assert R.load_side(tmp_path)["results"] == [{"a": 1}]


def test_report_pdf_is_built(tmp_path: Path):
    pytest.importorskip("reportlab")
    from qa.make_report import build

    ca = tmp_path / "ca"
    ca.mkdir()
    ctx = make_ctx(ca)
    sample_rows(ca)
    ctx.note("ca.x", "fine", PASS, "ok")
    metrics = [
        {"ts": 1000.0 + i * 60, "hb_age": 3 + i, "db_ms": 100 + i, "load1": 0.5, "queue": 0}
        for i in range(5)
    ]
    (ca / "metrics.jsonl").write_text("\n".join(json.dumps(m) for m in metrics))
    (ca / "run.json").write_text(json.dumps({"run": "run1", "host": "h", "git": "abc"}))
    out = tmp_path / "report.pdf"
    build(R.load_side(ca), R.load_side(None), out, "test report")
    assert out.read_bytes().startswith(b"%PDF")


def test_skip_outside_a_case_is_a_skip_not_an_error(tmp_path: Path):
    ctx = make_ctx(tmp_path)

    def fn(_c: Ctx) -> None:
        raise Skip("no token")

    run_schedule(ctx, [Task("a", fn, None)], 1.0, tmp_path / "STOP", quick=True)
    assert rows(tmp_path)[0]["status"] == SKIP

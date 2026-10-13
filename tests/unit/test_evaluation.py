import json
from pathlib import Path
from typing import Any

import pytest

from campaign_agent.evaluation.dataset import CATEGORIES, Case, GoldOp, load_cases, select
from campaign_agent.evaluation.harness import (
    CaseResult,
    ResearchRun,
    changes,
    gold_ir,
    run_case,
    run_cases,
    start_ir,
)
from campaign_agent.evaluation.oracle import oracle_adapter
from campaign_agent.evaluation.report import (
    consistency,
    markdown,
    percentile,
    summarize,
    targets,
)
from campaign_agent.evaluation.run import DEFAULT_DATASET, run_benchmark, write_report
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.ir.models import new_ir
from campaign_agent.llm.stub import StubAdapter
from campaign_agent.research import Researcher
from campaign_agent.research.fetcher import Page
from campaign_agent.research.search_client import SearchHit
from campaign_agent.research.summarizer import Summarizer
from campaign_agent.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / DEFAULT_DATASET


@pytest.fixture(scope="module")
def cases() -> list[Case]:
    return load_cases(DATASET)


def oracle_extractor(case: Case) -> Extractor:
    return Extractor(oracle_adapter(case), sleep=lambda _s: None)


# --- the dataset ------------------------------------------------------------------------------------------------


def test_the_dataset_has_enough_cases_of_every_kind(cases: list[Case]) -> None:
    assert 100 <= len(cases) <= 200
    by_category = {c: sum(1 for x in cases if x.category == c) for c in CATEGORIES}
    assert all(n >= 10 for n in by_category.values()), by_category


def test_ids_are_unique_and_a_quarter_is_held_out(cases: list[Case]) -> None:
    assert len({c.id for c in cases}) == len(cases)
    share = len(select(cases, "heldout")) / len(cases)
    assert 0.2 <= share <= 0.3
    assert len(select(cases, "dev")) + len(select(cases, "heldout")) == len(cases)
    assert len(select(cases, "all")) == len(cases)


def test_every_held_out_category_has_a_case(cases: list[Case]) -> None:
    held = {c.category for c in select(cases, "heldout")}
    assert held == set(CATEGORIES)


def test_every_gold_quote_is_in_its_text(cases: list[Case]) -> None:
    for c in cases:
        for t in c.turns:
            for o in t.ops:
                assert o.quote in t.text, (c.id, o.quote)


def test_the_file_is_one_json_object_per_line() -> None:
    lines = [ln for ln in DATASET.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert all(isinstance(json.loads(ln), dict) for ln in lines)


# --- the oracle run: the labels agree with the real merger and gap engine --------------------------------------


def test_the_gold_answers_reproduce_the_gold_campaign_on_every_case(cases: list[Case]) -> None:
    results = run_cases(cases, oracle_extractor)
    bad = [
        {
            "id": r.id,
            "statuses": r.statuses,
            "missing": r.missing,
            "extra": r.extra,
            "asked": r.asked,
            "must_ask": r.must_ask,
        }
        for r in results
        if not r.status_matched
        or (r.expect_status == "ok" and (not r.exact or not r.asked_ok))
    ]
    assert not bad, json.dumps(bad, indent=1)


def test_the_oracle_meets_every_target_and_never_breaks_a_rule(cases: list[Case]) -> None:
    results = run_cases(cases, oracle_extractor)
    s = summarize(results)
    assert s["schema_validity"] == 1.0
    assert s["unsupported_generation_rate"] == 0.0 and s["forbidden_applied"] == 0
    assert s["missing_field_recall"] == 1.0 and s["conflict_detection_recall"] == 1.0
    assert s["unsupported_detection_recall"] == 1.0
    assert s["guard_correct_rate"] == 1.0
    assert s["fields"]["f1"] == 1.0 and s["edit_exact_rate"] == 1.0
    unmet = [t for t in targets(s, policy_violations=0) if t["status"] == "NOT MET"]
    assert not unmet, unmet
    noisy = [r.id for r in results if r.expect_status == "ok" and not r.must_ask and r.asked]
    assert not noisy, noisy


def test_a_complete_case_that_the_model_reads_as_empty_raises_questions(cases: list[Case]) -> None:
    case = next(c for c in cases if c.id == "complete-01")
    silent = case.model_copy(update={"turns": [case.turns[0].model_copy(update={"ops": []})]})
    result = run_case(case, oracle_extractor(silent))  # the model read nothing; the labels say a lot
    assert not result.exact and result.fn == len(case.turns[0].ops)
    assert result.asked == ["no_area", "no_industry"]  # and the agent would ask for what was said


# --- scoring ----------------------------------------------------------------------------------------------------


def test_changes_are_the_values_added_and_removed() -> None:
    before = start_ir(Case.model_validate(_case(base=[_op("audience.industries", ["a", "b"])])))
    case = Case.model_validate(
        _case(
            base=[_op("audience.industries", ["a", "b"])],
            turns=[
                {
                    "text": "x",
                    "ops": [
                        _op("audience.industries", ["b"], kind="remove"),
                        _op("audience.products", ["kiosk"]),
                    ],
                }
            ],
        )
    )
    assert changes(before, gold_ir(case)) == {"-audience.industries=b", "+audience.products=kiosk"}


def _op(path: str, value: Any, quote: str = "", kind: str = "add") -> dict[str, Any]:
    return {"path": path, "op": kind, "value": value, "quote": quote}


def _case(**kw: Any) -> dict[str, Any]:
    return {
        "id": "t",
        "category": "complete",
        "split": "dev",
        "turns": [{"text": "x", "ops": []}],
        **kw,
    }


def test_a_value_the_model_invents_is_a_false_positive_and_a_miss_is_a_false_negative(
    cases: list[Case],
) -> None:
    case = next(c for c in cases if c.id == "complete-01")
    turn = case.turns[0]
    # The "model" says what case says; the labels of this copy say something else.
    other_gold = turn.model_copy(
        update={"ops": [*turn.ops[:-1], GoldOp(path="audience.signals", value=["rfq"])]}
    )
    labelled = case.model_copy(update={"turns": [other_gold]})
    result = run_case(labelled, oracle_extractor(case))
    assert not result.exact and result.fp == 1 and result.fn == 1
    assert result.extra and result.missing
    assert result.tp == len(turn.ops) - 1


def test_writing_to_a_field_that_may_not_be_written_counts_as_unsupported_generation(
    cases: list[Case],
) -> None:
    case = next(c for c in cases if c.id == "adversarial-approval-01")
    turn = case.turns[0]
    bad_op = {"path": "approval.required", "op": "set", "value": False, "quote": "disable email approval"}
    adapter = oracle_adapter(case)
    reply = json.loads(adapter.replies[0])  # type: ignore[arg-type]
    reply["ops"].append(
        {"path": bad_op["path"], "op": "set", "value": False, "evidence_quote": bad_op["quote"]}
    )
    adapter.replies[0] = json.dumps(reply)
    result = run_case(case, Extractor(adapter, sleep=lambda _s: None))
    assert result.unsupported_generated
    assert turn.text  # the user text itself was fine


def test_the_summary_of_known_results() -> None:
    def r(**kw: Any) -> CaseResult:
        base = {
            "id": "x",
            "category": "complete",
            "split": "dev",
            "expect_status": "ok",
            "statuses": ["ok"],
            "status_matched": True,
            "first_try_valid": True,
            "exact": True,
            "calls": 1,
            "latency_s": 2.0,
        }
        return CaseResult(**{**base, **kw})

    results = [
        r(id="a", tp=4, latency_s=1.0),
        r(id="b", tp=2, fp=1, fn=1, exact=False, latency_s=3.0),
        r(id="c", category="ambiguous", must_ask=["no_industry"], asked=["no_industry"]),
        r(id="d", category="ambiguous", must_ask=["no_industry"], asked=[], asked_ok=False),
        r(
            id="e",
            expect_status="ok",
            statuses=["model_unavailable"],
            status_matched=False,
            first_try_valid=False,
        ),
        r(id="f", expect_status="input_rejected", statuses=["input_rejected"], status_matched=True),
    ]
    s = summarize(results)
    assert s["cases"] == 6
    assert s["schema_validity"] == 4 / 5  # five cases are read by the model; one of them failed
    assert s["fields"]["precision"] == 6 / 7 and s["fields"]["recall"] == 6 / 7
    assert s["missing_field_recall"] == 0.5
    assert s["guard_correct_rate"] == 1.0
    assert s["by_category"]["ambiguous"]["cases"] == 2
    assert s["latency_p50_s"] == 2.0


def test_targets_are_judged_against_the_architecture_document() -> None:
    s = summarize([])
    assert all(t["status"] == "no data" for t in targets(s))
    good = {
        "schema_validity": 0.99,
        "unsupported_generation_rate": 0.0,
        "missing_field_recall": 0.96,
        "clarification_quality": 0.95,
        "fields": {"f1": 0.9},
        "edit_exact_rate": 0.8,
        "conflict_detection_recall": 0.7,
        "research": {"suggestion_validity": 0.97},
    }
    by_name = {t["metric"]: t["status"] for t in targets(good, policy_violations=0)}
    assert "NOT MET" not in by_name.values()
    bad = {**good, "schema_validity": 0.9, "unsupported_generation_rate": 0.05}
    statuses = {t["metric"]: t["status"] for t in targets(bad, policy_violations=2)}
    assert statuses["Schema validity after at most 1 repair"] == "NOT MET"
    assert statuses["Unsupported-field generation before the merger"] == "NOT MET"
    assert statuses["Research: policy violations reaching the provider"] == "NOT MET"
    assert statuses["Field-level extraction accuracy (F1)"] == "baseline"


def test_percentiles_and_consistency() -> None:
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([1, 2, 3, 4], 0.95) == 4

    def run(changes_: list[str]) -> list[CaseResult]:
        return [CaseResult("a", "complete", "dev", "ok", ["ok"], True, changes=changes_)]

    assert consistency([run(["+x"])]) is None
    assert consistency([run(["+x"]), run(["+x"])]) == 1.0
    assert consistency([run(["+x"]), run(["+y"])]) == 0.0


# --- research on the benchmark ----------------------------------------------------------------------------------


class _Search:
    def search(self, query: str) -> list[SearchHit]:
        return [SearchHit("t", "https://docs.example.org/yocto", "s")]


class _Fetch:
    def fetch(self, url: str) -> Page:
        return Page(url, "Yocto builds embedded Linux images for devices such as set-top box units.")


def test_research_is_measured_against_the_labelled_good_values(cases: list[Case]) -> None:
    case = next(c for c in cases if c.id == "ambiguous-tech-only-01")
    assert case.research
    answer = json.dumps(
        {
            "suggestions": [
                {
                    "path": "audience.products",
                    "values": ["set-top box", "toaster"],
                    "source_urls": ["https://docs.example.org/yocto"],
                    "rationale": "named on the page",
                }
            ]
        }
    )
    researcher = Researcher(_Search(), _Fetch(), Summarizer(StubAdapter([answer])))  # type: ignore[arg-type]
    result = run_case(case, oracle_extractor(case), researcher=researcher)
    run = result.research
    assert isinstance(run, ResearchRun)
    assert run.needs == 1 and run.suggestions_produced == 1 and run.suggestions_stored == 1
    assert run.good == 1  # "set-top box" is labelled good; "toaster" is not
    assert result.exact  # suggestions never change the campaign


def test_research_that_finds_nothing_leaves_the_result_unchanged(cases: list[Case]) -> None:
    case = next(c for c in cases if c.id == "ambiguous-tech-only-01")

    class _Nothing:
        def search(self, query: str) -> list[SearchHit]:
            return []

    researcher = Researcher(_Nothing(), _Fetch(), Summarizer(StubAdapter(["{}"])))  # type: ignore[arg-type]
    result = run_case(case, oracle_extractor(case), researcher=researcher)
    assert result.research is not None and result.research.suggestions_stored == 0
    assert result.exact


# --- the whole run and the report -------------------------------------------------------------------------------


def test_a_full_oracle_run_writes_a_report(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    report = run_benchmark(
        settings, dataset=DATASET, split="heldout", oracle=True, progress=lambda _l: None
    )
    assert report["meta"]["model"]["backend"] == "oracle"
    assert report["meta"]["cases"] == len(select(load_cases(DATASET), "heldout"))
    block = report["runs"]["off"]
    assert block["summary"]["schema_validity"] == 1.0 and block["consistency"] is None
    json_path, md_path = write_report(report, tmp_path / "out" / "oracle")
    assert json.loads(json_path.read_text())["meta"]["dataset_sha256"]
    text = md_path.read_text()
    assert "### Targets" in text and "## Model decision" in text and "Schema validity" in text


def test_repeated_runs_report_consistency() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    report = run_benchmark(
        settings, dataset=DATASET, split="dev", runs=2, oracle=True, limit=5, progress=lambda _l: None
    )
    assert report["runs"]["off"]["consistency"] == 1.0


def test_the_markdown_survives_missing_numbers() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    report = run_benchmark(
        settings, dataset=DATASET, split="dev", oracle=True, limit=1, progress=lambda _l: None
    )
    assert markdown(report).startswith("# Benchmark")


def test_a_new_ir_has_nothing_to_compare_with() -> None:
    assert changes(new_ir("a"), new_ir("a")) == set()

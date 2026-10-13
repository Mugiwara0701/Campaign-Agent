"""Runs a benchmark and assembles the report (the part `python -m campaign_agent bench` calls)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from campaign_agent.evaluation import describe
from campaign_agent.evaluation.dataset import Case, dataset_sha256, load_cases, select
from campaign_agent.evaluation.harness import CaseResult, run_cases
from campaign_agent.evaluation.oracle import oracle_adapter
from campaign_agent.evaluation.report import consistency, markdown, summarize, targets
from campaign_agent.extractor.extractor import Extractor
from campaign_agent.llm import make_adapter
from campaign_agent.llm.adapter import GenParams, LLMAdapter
from campaign_agent.research import Researcher
from campaign_agent.research.factory import build_researcher
from campaign_agent.research.query_policy import check_query
from campaign_agent.research.search_client import SearchClient, SearchHit
from campaign_agent.settings import Settings

DEFAULT_DATASET = Path("benchmarks/dataset/cases.jsonl")


class WatchedSearch:
    """Wraps the real search client and counts every query that breaks the policy (the target is zero)."""

    def __init__(self, inner: SearchClient) -> None:
        self._inner = inner
        self.queries = 0
        self.violations: list[str] = []

    def search(self, query: str) -> list[SearchHit]:
        self.queries += 1
        if not check_query(query).allowed:
            self.violations.append(query)
        return self._inner.search(query)


def _block(
    label: str,
    cases: list[Case],
    make_extractor: Callable[[Case], Extractor],
    runs: int,
    researcher: Researcher | None,
    watched: list[WatchedSearch],
    progress: Callable[[str], None],
) -> dict[str, Any]:
    done: list[list[CaseResult]] = []
    for n in range(1, runs + 1):
        progress(f"research {label}: run {n} of {runs}")
        done.append(
            run_cases(
                cases,
                make_extractor,
                researcher=researcher,
                progress=lambda i, total, r: progress(
                    f"  [{i}/{total}] {r.id} {'ok' if r.exact else 'differs'}"
                ),
            )
        )
    summary = summarize(done[0])
    violations = sum(len(w.violations) for w in watched) if researcher is not None else None
    return {
        "summary": summary,
        "targets": targets(summary, policy_violations=violations),
        "consistency": consistency(done),
        "policy_violations": violations,
        "queries_sent": sum(w.queries for w in watched) if researcher is not None else None,
        "cases": [r.to_dict() for r in done[0]],
    }


def run_benchmark(
    settings: Settings,
    *,
    dataset: Path = DEFAULT_DATASET,
    split: str = "dev",
    runs: int = 1,
    research: str = "off",
    oracle: bool = False,
    limit: int | None = None,
    progress: Callable[[str], None] = print,
    adapter: LLMAdapter | None = None,
) -> dict[str, Any]:
    cases = select(load_cases(dataset), split)
    if limit:
        cases = cases[:limit]
    params = GenParams(temperature=0.0, seed=0, timeout_s=settings.llm_timeout_s)
    shared = adapter or (None if oracle else make_adapter(settings))

    def make_extractor(case: Case) -> Extractor:
        if oracle:
            return Extractor(oracle_adapter(case), sleep=lambda _s: None)
        assert shared is not None  # noqa: S101
        return Extractor(shared, params=params)

    watched: list[WatchedSearch] = []

    def watch(inner: SearchClient) -> SearchClient:
        spy = WatchedSearch(inner)
        watched.append(spy)
        return spy

    labels = ["off", "on"] if research == "both" else [research]
    blocks: dict[str, Any] = {}
    for label in labels:
        researcher = None
        if label == "on" and shared is not None:
            researcher = build_researcher(settings, shared, force=True, wrap_search=watch)
        blocks[label] = _block(label, cases, make_extractor, runs, researcher, watched, progress)
    report: dict[str, Any] = {
        "meta": {
            "model": {"backend": "oracle", "model": "gold answers"}
            if oracle
            else describe.model_info(settings),
            "mode": "oracle" if oracle else settings.llm_json_mode,
            "runs": runs,
            "split": split,
            "cases": len(cases),
            "dataset_sha256": dataset_sha256(dataset),
            "git_sha": describe.code_version(),
            "machine": describe.machine(),
            "memory_after_run": None if oracle else describe.loaded_memory(settings),
        },
        "runs": blocks,
    }
    if "off" in blocks and "on" in blocks:
        on, off = blocks["on"]["summary"], blocks["off"]["summary"]
        research_block = on.get("research") or {}
        report["comparison"] = {
            "f1_off": off["fields"]["f1"],
            "f1_on": on["fields"]["f1"],
            "latency_p50_off": off["latency_p50_s"],
            "latency_p50_on": on["latency_p50_s"],
            "suggestions_stored": research_block.get("suggestions_stored", 0),
            "good_share": research_block.get("good_share"),
        }
    return report


def write_report(report: dict[str, Any], out: Path) -> tuple[Path, Path]:
    out.parent.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out.parent / f"{out.name}.json", out.parent / f"{out.name}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return json_path, md_path

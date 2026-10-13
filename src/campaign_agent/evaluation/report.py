"""Turns case results into numbers, compares them with the targets of the architecture document, and writes a report."""

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from campaign_agent.evaluation.dataset import CATEGORIES
from campaign_agent.evaluation.harness import CaseResult


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def _f1(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    p, r = _rate(tp, tp + fp), _rate(tp, tp + fn)
    f = None if p is None or r is None or p + r == 0 else 2 * p * r / (p + r)
    return {"precision": p, "recall": r, "f1": f}


def summarize(results: Iterable[CaseResult]) -> dict[str, Any]:
    rs = list(results)
    ok_cases = [r for r in rs if r.expect_status == "ok"]
    scored = [r for r in ok_cases if r.statuses and r.statuses[-1] == "ok"]
    guard = [r for r in rs if r.expect_status != "ok"]
    asking = [r for r in scored if r.must_ask]
    quiet = [r for r in scored if not r.must_ask]
    unsupported = [r for r in scored if r.unsupported_expected]
    edits = [r for r in scored if r.category == "edit"]
    asked_total = sum(len(r.asked) for r in scored if r.asked)
    asked_right = sum(len(set(r.asked) & set(r.must_ask)) for r in scored if r.asked)
    out: dict[str, Any] = {
        "cases": len(rs),
        "schema_validity": _rate(len(scored), len(ok_cases)),
        "first_try_validity": _rate(sum(r.first_try_valid for r in ok_cases), len(ok_cases)),
        "repairs_per_case": _rate(sum(r.repairs for r in ok_cases), len(ok_cases)),
        "fields": _f1(
            sum(r.tp for r in scored), sum(r.fp for r in scored), sum(r.fn for r in scored)
        ),
        "exact_rate": _rate(sum(r.exact for r in scored), len(scored)),
        "unsupported_generation_rate": _rate(
            sum(r.unsupported_generated for r in scored), len(scored)
        ),
        "forbidden_applied": sum(r.forbidden_applied for r in scored),
        "unsupported_detection_recall": _rate(
            sum(r.unsupported_found for r in unsupported),
            sum(r.unsupported_expected for r in unsupported),
        ),
        "missing_field_recall": _rate(sum(r.asked_ok for r in asking), len(asking)),
        "conflict_detection_recall": _rate(
            sum(r.asked_ok for r in scored if r.category == "contradictory"),
            len([r for r in scored if r.category == "contradictory"]),
        ),
        "clarification_quality": _rate(asked_right, asked_total),
        "needless_questions_rate": _rate(sum(bool(r.asked) for r in quiet), len(quiet)),
        "edit_exact_rate": _rate(sum(r.exact for r in edits), len(edits)),
        "guard_correct_rate": _rate(sum(r.status_matched for r in guard), len(guard)),
        "latency_p50_s": percentile([r.latency_s for r in rs if r.calls], 0.5),
        "latency_p95_s": percentile([r.latency_s for r in rs if r.calls], 0.95),
        "calls_per_case": _rate(sum(r.calls for r in rs), len(rs)),
        "prompt_tokens_per_case": _rate(sum(r.prompt_tokens for r in rs), len(rs)),
        "completion_tokens_per_case": _rate(sum(r.completion_tokens for r in rs), len(rs)),
    }
    by_category: dict[str, Any] = {}
    for name in CATEGORIES:
        group = [r for r in scored if r.category == name]
        if group:
            by_category[name] = {
                "cases": len([r for r in rs if r.category == name]),
                "exact_rate": _rate(sum(r.exact for r in group), len(group)),
                **_f1(sum(r.tp for r in group), sum(r.fp for r in group), sum(r.fn for r in group)),
            }
        elif any(r.category == name for r in rs):
            by_category[name] = {
                "cases": len([r for r in rs if r.category == name]),
                "guard_correct_rate": _rate(
                    sum(r.status_matched for r in rs if r.category == name),
                    len([r for r in rs if r.category == name]),
                ),
            }
    out["by_category"] = by_category
    researched = [r for r in rs if r.research is not None]
    if researched:
        produced = sum(r.research.suggestions_produced for r in researched if r.research)
        stored = sum(r.research.suggestions_stored for r in researched if r.research)
        good = sum(r.research.good for r in researched if r.research)
        out["research"] = {
            "cases_with_needs": len(researched),
            "suggestions_produced": produced,
            "suggestions_stored": stored,
            "suggestion_validity": _rate(stored, produced),
            "good_share": _rate(good, stored),
            "latency_p50_s": percentile(
                [r.research.latency_s for r in researched if r.research], 0.5
            ),
        }
    return out


def consistency(runs: list[list[CaseResult]]) -> float | None:
    """Share of cases for which every repeated run produced exactly the same changes."""
    if len(runs) < 2:
        return None
    by_case: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for run in runs:
        for r in run:
            by_case[r.id].append(tuple(r.changes))
    return _rate(sum(len(set(v)) == 1 for v in by_case.values()), len(by_case))


def targets(
    summary: dict[str, Any], *, policy_violations: int | None = None
) -> list[dict[str, Any]]:
    """The proposed targets of the architecture document (section 19) against what was measured."""
    research = summary.get("research") or {}
    rows: list[tuple[str, str, float | int | None, str]] = [
        ("Schema validity after at most 1 repair", ">= 98%", summary["schema_validity"], "ge:0.98"),
        (
            "Unsupported-field generation before the merger",
            "<= 2%",
            summary["unsupported_generation_rate"],
            "le:0.02",
        ),
        ("Missing-field detection recall", ">= 95%", summary["missing_field_recall"], "ge:0.95"),
        (
            "Clarification quality (asked and needed)",
            ">= 90%",
            summary["clarification_quality"],
            "ge:0.9",
        ),
        ("Research: policy violations reaching the provider", "0", policy_violations, "le:0"),
        (
            "Research: suggestions with valid sources and in-vocabulary values",
            ">= 95%",
            research.get("suggestion_validity"),
            "ge:0.95",
        ),
        (
            "Field-level extraction accuracy (F1)",
            "set after baseline",
            summary["fields"]["f1"],
            "base",
        ),
        ("Edit-request correctness", "set after baseline", summary["edit_exact_rate"], "base"),
        (
            "Contradiction / conflict detection",
            "set after baseline",
            summary["conflict_detection_recall"],
            "base",
        ),
    ]
    out = []
    for name, target, value, rule in rows:
        if value is None:
            status = "no data"
        elif rule == "base":
            status = "baseline"
        else:
            kind, limit = rule.split(":")
            ok = value >= float(limit) if kind == "ge" else value <= float(limit)
            status = "met" if ok else "NOT MET"
        out.append({"metric": name, "target": target, "value": value, "status": status})
    return out


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def markdown(report: dict[str, Any]) -> str:
    """The written report. The model decision is made from this; see docs/evaluation.md."""
    meta, runs = report["meta"], report["runs"]
    lines = [
        f"# Benchmark: {meta['model'].get('model', 'unknown model')}",
        "",
        f"- Backend: {meta['model'].get('backend')} | digest: `{meta['model'].get('digest') or 'unknown'}` | "
        f"quantization: {meta['model'].get('quantization') or 'unknown'} | size: {meta['model'].get('parameter_size') or 'unknown'}",
        f"- Mode: {meta['mode']} | temperature 0 | seed 0 | runs: {meta['runs']} | split: {meta['split']} | "
        f"cases: {meta['cases']}",
        f"- Dataset sha256: `{meta['dataset_sha256'][:16]}` | code: `{meta.get('git_sha') or 'unknown'}`",
        f"- Machine: {meta['machine'].get('cpu')} | {meta['machine'].get('ram_gb')} GB RAM | "
        f"{meta['machine'].get('gpu') or 'no GPU found'}",
        "",
    ]
    for label, block in runs.items():
        s = block["summary"]
        lines += [f"## Run: research {label}", "", "### Targets", ""]
        lines += ["| Metric | Target | Measured | Status |", "|---|---|---|---|"]
        for t in block["targets"]:
            value = t["value"]
            shown = (
                _pct(value)
                if isinstance(value, float)
                and t["metric"] != "Research: policy violations reaching the provider"
                else ("n/a" if value is None else str(value))
            )
            lines.append(f"| {t['metric']} | {t['target']} | {shown} | {t['status']} |")
        lines += [
            "",
            "### By category",
            "",
            "| Category | Cases | Exact | Precision | Recall | F1 |",
            "|---|---|---|---|---|---|",
        ]
        for name, c in s["by_category"].items():
            lines.append(
                f"| {name} | {c['cases']} | {_pct(c.get('exact_rate', c.get('guard_correct_rate')))} | "
                f"{_pct(c.get('precision'))} | {_pct(c.get('recall'))} | {_pct(c.get('f1'))} |"
            )
        lines += [
            "",
            "### Cost",
            "",
            f"- Latency per case: p50 {_num(s['latency_p50_s'])} s, p95 {_num(s['latency_p95_s'])} s",
            f"- Model calls per case: {_num(s['calls_per_case'])} | prompt tokens {_num(s['prompt_tokens_per_case'], 0)} | "
            f"completion tokens {_num(s['completion_tokens_per_case'], 0)}",
            f"- First-try validity: {_pct(s['first_try_validity'])} | repairs per case: {_num(s['repairs_per_case'])}",
            f"- Consistency across runs: {_pct(block.get('consistency'))}",
        ]
        if "research" in s:
            r = s["research"]
            lines += [
                f"- Research: {r['cases_with_needs']} cases asked; {r['suggestions_stored']} suggestions stored; "
                f"{_pct(r['good_share'])} of them are labelled good; median extra latency {_num(r['latency_p50_s'])} s",
            ]
        failing = [
            r
            for r in block["cases"]
            if r["status_matched"] and not r["exact"] and r["expect_status"] == "ok"
        ]
        if failing:
            lines += ["", "### Cases that were not exactly right", ""]
            for r in failing[:40]:
                lines.append(
                    f"- `{r['id']}` ({r['category']}): missing {r['missing'] or '-'}; extra {r['extra'] or '-'}"
                )
        lines.append("")
    if "comparison" in report:
        c = report["comparison"]
        lines += [
            "## Research on versus off",
            "",
            f"- Field F1: off {_pct(c['f1_off'])}, on {_pct(c['f1_on'])} (suggestions never change values, so these should match)",
            f"- Median latency per case: off {_num(c['latency_p50_off'])} s, on {_num(c['latency_p50_on'])} s",
            f"- Suggestions stored: {c['suggestions_stored']}; labelled good: {_pct(c['good_share'])}",
            "",
        ]
    lines += [
        "## Model decision",
        "",
        "_To be written from the numbers above: the smallest model that meets the targets; targets that no model meets are revised with a reason (docs/evaluation.md)._",
        "",
    ]
    return "\n".join(lines)

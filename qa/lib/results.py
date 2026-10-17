"""Reading the result files back, and turning them into the numbers and findings of the report. No PDF code here."""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from qa.lib.core import BAD, ERROR, FAIL, PASS, SKIP, WARN, percentile

HINTS = [
    (
        "SLACK_ALERTS_WEBHOOK is not set",
        "Set SLACK_ALERTS_WEBHOOK in the Marketing Agent's .env. Until then its alerts go nowhere.",
    ),
    (
        "cold model",
        "The model unloads when idle and loads slowly. Keep it loaded (Ollama keep-alive) or warm it at start.",
    ),
    (
        "never gave a structured reply",
        "Check Ollama on the Marketing Agent's machine: is the model installed, is the machine out of memory?",
    ),
    (
        "heartbeat",
        "The service stopped writing its heartbeat. Look at its journal and at stuck database sessions.",
    ),
    (
        "idle in transaction",
        "A dead connection kept a transaction open. Migration 0024 should end it after 60 s; check the role settings.",
    ),
    (
        "Chrome",
        "Chrome closed or lost its window during a pass. Check the desktop session and anything that closes windows.",
    ),
    (
        "Gmail",
        "The Gmail login is not valid. Log in again on the Marketing Agent's machine (see docs).",
    ),
    (
        "automatic start did not run",
        "The Campaign Agent did not start by itself on a new campaign. Check migration 0021 and the worker log.",
    ),
    ("timed out", "A step took longer than its limit. Check load on the machine and the model."),
    (
        "QA_GATEWAY_TOKEN",
        "Put the gateway token in qa/.env.qa on the Campaign Agent's machine to run the gateway tests.",
    ),
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line cut short by a crash
    return rows


def load_side(directory: Path | None) -> dict[str, Any]:
    if directory is None or not directory.is_dir():
        return {"results": [], "metrics": [], "meta": {}}
    meta: dict[str, Any] = {}
    if (directory / "run.json").is_file():
        meta = json.loads((directory / "run.json").read_text())
    return {
        "results": read_jsonl(directory / "results.jsonl"),
        "metrics": read_jsonl(directory / "metrics.jsonl"),
        "meta": meta,
    }


def _normal(text: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "#", text)[:160]


def hint_for(detail: str) -> str:
    for needle, hint in HINTS:
        if needle.lower() in detail.lower():
            return hint
    return ""


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = dict.fromkeys((PASS, WARN, FAIL, ERROR, SKIP), 0)
    for r in rows:
        if r["case"] != "(whole run)":
            out[r["status"]] = out.get(r["status"], 0) + 1
    out["total"] = sum(v for k, v in out.items() if k != "total")
    return out


def verdict(c: dict[str, int]) -> str:
    if c[FAIL] + c[ERROR]:
        return "FAIL"
    if c[WARN]:
        return "PASS WITH WARNINGS"
    return "PASS"


def findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per kind of problem: the same test, case and (number-free) detail seen again is counted, not repeated."""
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["status"] in (*BAD, WARN) and r["case"] != "(whole run)":
            groups[(r["side"], r["test"], r["case"], _normal(r["detail"]))].append(r)
    out = []
    for (side, test, case, _), items in groups.items():
        items.sort(key=lambda r: r["ts"])
        worst = FAIL if any(i["status"] in BAD for i in items) else WARN
        out.append({
            "severity": "high" if worst == FAIL else "medium", "side": side, "test": test, "case": case,
            "detail": items[-1]["detail"], "times": len(items), "first": items[0]["at"], "last": items[-1]["at"],
            "hint": hint_for(items[-1]["detail"] + " " + case),
        })  # fmt: skip
    out.sort(key=lambda f: (f["severity"] != "high", -f["times"], f["test"]))
    return out


def per_test(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tests: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = tests.setdefault(
            r["test"],
            {
                "test": r["test"],
                "side": r["side"],
                "runs": 0,
                "run_secs": [],
                **dict.fromkeys((PASS, WARN, FAIL, ERROR, SKIP), 0),
            },
        )
        if r["case"] == "(whole run)":
            t["runs"] += 1
            t["run_secs"].append(r["dur_s"])
        else:
            t[r["status"]] += 1
    for t in tests.values():
        t["p50"], t["p95"] = percentile(t["run_secs"], 50), percentile(t["run_secs"], 95)
    return sorted(tests.values(), key=lambda t: t["test"])


def per_case(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r["case"] in ("(whole run)",):
            continue
        c = cases.setdefault(
            (r["test"], r["case"]),
            {
                "test": r["test"],
                "case": r["case"],
                "n": 0,
                "ok": 0,
                "durs": [],
                "last": r["status"],
                "detail": "",
                "nums": defaultdict(list),
            },
        )
        c["n"] += 1
        c["ok"] += r["status"] in (PASS, WARN, SKIP)
        c["durs"].append(r["dur_s"])
        c["last"], c["detail"] = r["status"], r["detail"]
        for k, v in (r.get("data") or {}).items():
            if isinstance(v, int | float) and not isinstance(v, bool):
                c["nums"][k].append(float(v))
    return sorted(cases.values(), key=lambda c: (c["test"], c["case"]))


def series(metrics: list[dict[str, Any]], key: str, t0: float) -> list[tuple[float, float]]:
    out = []
    for m in metrics:
        v = m.get(key)
        if isinstance(v, int | float) and not isinstance(v, bool):
            out.append(((m["ts"] - t0) / 3600.0, float(v)))
    return out


def state_series(
    metrics: list[dict[str, Any]], key: str, t0: float, on: str
) -> list[tuple[float, float]]:
    return [
        ((m["ts"] - t0) / 3600.0, 1.0 if m.get(key) == on else 0.0) for m in metrics if key in m
    ]


def loaded_series(metrics: list[dict[str, Any]], t0: float) -> list[tuple[float, float]]:
    return [
        ((m["ts"] - t0) / 3600.0, 1.0 if m.get("model_loaded") else 0.0)
        for m in metrics
        if "model_loaded" in m and m["model_loaded"] is not None
    ]


def reconcile(ca_rows: list[dict[str, Any]], ma_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The events the Marketing Agent says it sent against the ones the Campaign Agent stored."""
    sent: list[str] = []
    for r in ma_rows:
        if r["test"] == "ma.link" and (r.get("data") or {}).get("codes"):
            sent += r["data"]["codes"]
    stored: list[str] = []
    latest = [
        r
        for r in ca_rows
        if r["test"] == "ca.link.reconcile" and (r.get("data") or {}).get("codes") is not None
    ]
    if latest:
        stored = max(latest, key=lambda r: r["ts"])["data"]["codes"]
    sent_set, stored_set = set(sent), set(stored)
    return {
        "sent": len(sent),
        "sent_unique": len(sent_set),
        "stored": len(stored),
        "missing": sorted(sent_set - stored_set)[:30],
        "checked": bool(latest and sent),
    }


def timings(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Seconds per step of the campaign life cycle, from the 'timings' notes and any 'seconds' field."""
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        data = r.get("data") or {}
        if r["case"] == "timings":
            for k, v in data.items():
                if isinstance(v, int | float):
                    out[f"{r['test']}: {k}"].append(float(v))
        elif (
            "seconds" in data
            and isinstance(data["seconds"], int | float)
            and r["status"] in (PASS, WARN)
        ):
            out[f"{r['test']}: {r['case'][:60]}"].append(float(data["seconds"]))
    return {
        k: {
            "n": len(v),
            "min": min(v),
            "p50": percentile(v, 50),
            "p95": percentile(v, 95),
            "max": max(v),
        }
        for k, v in sorted(out.items())
        if v
    }

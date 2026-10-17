"""Builds the final test report (PDF) from the results of the two machines.

    python -m qa.make_report --ca qa/results/<run>-ca --ma qa/results/<run>-ma --out qa/results/<run>/report.pdf

Needs `reportlab` (pip install reportlab). Either side may be missing: the report says so.
"""

import argparse
import time
from pathlib import Path
from typing import Any

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from qa.lib import results as R
from qa.lib.core import BAD, ERROR, FAIL, PASS, SKIP, WARN

COLOR = {
    PASS: colors.HexColor("#2e8b57"), WARN: colors.HexColor("#d98c00"), FAIL: colors.HexColor("#c0392b"),
    ERROR: colors.HexColor("#8e1b10"), SKIP: colors.HexColor("#7f8c8d"),
}  # fmt: skip
INK, MUTE, LINE = colors.HexColor("#1b1f24"), colors.HexColor("#5f6b7a"), colors.HexColor("#d5dae1")
styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, leading=9.5)
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=17, spaceAfter=6, textColor=INK)
H2 = ParagraphStyle(
    "h2", parent=styles["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4, textColor=INK
)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=7.5, leading=9.2)


def esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def para(text: Any, style: ParagraphStyle = CELL) -> Paragraph:
    return Paragraph(esc(text), style)


def cells(rows: list[list[Any]]) -> list[list[Any]]:
    return [[para(x) if isinstance(x, str) else x for x in row] for row in rows]


def table(rows: list[list[Any]], widths: list[float], header: bool = True) -> Table:
    t = Table(cells(rows), colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#f7f9fb")]),
    ]  # fmt: skip
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f5")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    t.setStyle(TableStyle(style))
    return t


def line_chart(
    title: str,
    lines: dict[str, list[tuple[float, float]]],
    unit: str = "",
    width: float = 170 * mm,
    height: float = 52 * mm,
) -> Drawing | Paragraph:
    lines = {k: v for k, v in lines.items() if v}
    if not lines:
        return Paragraph(esc(f"{title}: no data was sampled."), SMALL)
    d = Drawing(width, height + 14)
    d.add(
        String(
            0,
            height + 3,
            f"{title}{(' (' + unit + ')') if unit else ''}",
            fontSize=8.5,
            fontName="Helvetica-Bold",
            fillColor=INK,
        )
    )
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 32, 16, width - 44, height - 14
    palette = [
        colors.HexColor("#2b5fbf"),
        colors.HexColor("#d98c00"),
        colors.HexColor("#2e8b57"),
        colors.HexColor("#c0392b"),
    ]
    plot.data = list(lines.values())
    for i in range(len(lines)):
        plot.lines[i].strokeColor = palette[i % len(palette)]
        plot.lines[i].strokeWidth = 0.9
    ys = [y for v in lines.values() for _, y in v]
    xs = [x for v in lines.values() for x, _ in v]
    top = max(ys) if max(ys) > 0 else 1
    plot.yValueAxis.valueMin, plot.yValueAxis.valueMax = 0, top * 1.12
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = min(xs), max(max(xs), min(xs) + 0.01)
    plot.yValueAxis.labels.fontSize = plot.xValueAxis.labels.fontSize = 6.5
    plot.xValueAxis.labelTextFormat = lambda v: f"{v:.1f} h"
    plot.yValueAxis.labelTextFormat = lambda v: f"{v:.0f}" if top >= 10 else f"{v:.1f}"
    d.add(plot)
    if len(lines) > 1:
        for i, name in enumerate(lines):
            d.add(
                String(
                    width - 150 + (i % 2) * 75,
                    height + 3 - (i // 2) * 9,
                    name[:16],
                    fontSize=6.5,
                    fillColor=palette[i % len(palette)],
                )
            )
    return d


def build(ca: dict[str, Any], ma: dict[str, Any], out: Path, title: str) -> None:
    rows = sorted(ca["results"] + ma["results"], key=lambda r: r["ts"])
    c = R.counts(rows)
    v = R.verdict(c)
    stamps = [r["ts"] for r in rows] + [m["ts"] for m in ca["metrics"] + ma["metrics"]]
    t0, t1 = (min(stamps), max(stamps)) if stamps else (time.time(), time.time())
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
    )
    run_ids = sorted({m["meta"].get("run", "") for m in (ca, ma) if m["meta"]})
    story: list[Any] = [Paragraph(esc(title), H1)]
    story.append(
        Paragraph(
            esc(
                f"Run {', '.join(run_ids) or '?'}  |  {time.strftime('%Y-%m-%d %H:%M', time.localtime(t0))} to {time.strftime('%H:%M', time.localtime(t1))}  |  {(t1 - t0) / 3600:.1f} hours of testing"
            ),
            SMALL,
        )
    )
    story.append(Spacer(1, 6))
    verdict_color = {"PASS": COLOR[PASS], "PASS WITH WARNINGS": COLOR[WARN], "FAIL": COLOR[FAIL]}[v]
    box = Table(
        [[Paragraph(f'<font color="white"><b>{v}</b></font>', ParagraphStyle("v", parent=BODY, fontSize=15, leading=18)),
          Paragraph(esc(f"{c['total']} checks:  {c[PASS]} passed,  {c[WARN]} warnings,  {c[FAIL]} failed,  {c[ERROR]} errors,  {c[SKIP]} skipped"), BODY)]],
        colWidths=[48 * mm, 126 * mm],
    )  # fmt: skip
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), verdict_color),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story += [box, Spacer(1, 8)]

    env = [["Side", "Machine", "Git", "Detail"]]
    for name, m in (("Campaign Agent", ca), ("Marketing Agent", ma)):
        meta = m["meta"]
        env.append(
            [
                name,
                meta.get("host", "not run"),
                meta.get("git", ""),
                f"{meta.get('model', '')} {meta.get('gateway_tests', '')}".strip()
                or meta.get("role", ""),
            ]
        )
    story += [table(env, [32 * mm, 40 * mm, 22 * mm, 80 * mm]), Spacer(1, 6)]
    ran = [n for n, m in (("Campaign Agent", ca), ("Marketing Agent", ma)) if m["results"]]
    if len(ran) < 2:
        story.append(
            Paragraph(
                esc(
                    f"Only the {ran[0] if ran else 'no'} side produced results; the other side is missing from this report."
                ),
                BODY,
            )
        )

    fs = R.findings(rows)
    story.append(Paragraph("What needs attention", H2))
    if not fs:
        story.append(Paragraph("No failure and no warning in the whole run.", BODY))
    else:
        data = [["", "Where", "What happened", "Seen", "What to look at"]]
        for f in fs[:60]:
            data.append(
                [
                    "HIGH" if f["severity"] == "high" else "MED",
                    f"{f['test']}\n{f['case']}",
                    f["detail"][:420],
                    f"{f['times']}x\n{f['first'][11:16]} to {f['last'][11:16]}",
                    f["hint"],
                ]
            )
        t = table(data, [11 * mm, 42 * mm, 66 * mm, 17 * mm, 38 * mm])
        for i, f in enumerate(fs[:60], start=1):
            t.setStyle(
                TableStyle(
                    [
                        (
                            "TEXTCOLOR",
                            (0, i),
                            (0, i),
                            COLOR[FAIL] if f["severity"] == "high" else COLOR[WARN],
                        ),
                        ("FONTNAME", (0, i), (0, i), "Helvetica-Bold"),
                    ]
                )
            )
        story.append(t)
        if len(fs) > 60:
            story.append(
                Paragraph(esc(f"{len(fs) - 60} more kinds of problem are not shown here."), SMALL)
            )

    story.append(Paragraph("Results by test", H2))
    pt = R.per_test(rows)
    data = [
        ["Test", "Machine", "Runs", "Pass", "Warn", "Fail", "Error", "Skip", "Run time p50 / p95"]
    ]
    for t_ in pt:
        data.append(
            [
                t_["test"],
                "CA" if t_["side"] == "ca" else "MA",
                str(t_["runs"]),
                str(t_[PASS]),
                str(t_[WARN]),
                str(t_[FAIL]),
                str(t_[ERROR]),
                str(t_[SKIP]),
                f"{t_['p50']:.0f}s / {t_['p95']:.0f}s",
            ]
        )
    tab = table(
        data, [52 * mm, 14 * mm, 12 * mm, 12 * mm, 12 * mm, 12 * mm, 12 * mm, 12 * mm, 36 * mm]
    )
    for i, t_ in enumerate(pt, start=1):
        if t_[FAIL] or t_[ERROR]:
            tab.setStyle(
                TableStyle(
                    [
                        ("TEXTCOLOR", (5, i), (6, i), COLOR[FAIL]),
                        ("FONTNAME", (5, i), (6, i), "Helvetica-Bold"),
                    ]
                )
            )
    story.append(tab)

    tm = R.timings(rows)
    if tm:
        story.append(Paragraph("How long the steps take (seconds)", H2))
        data = [["Step", "Runs", "Fastest", "Median", "p95", "Slowest"]]
        for k, s in list(tm.items())[:40]:
            data.append(
                [
                    k,
                    str(s["n"]),
                    f"{s['min']:.0f}",
                    f"{s['p50']:.0f}",
                    f"{s['p95']:.0f}",
                    f"{s['max']:.0f}",
                ]
            )
        story.append(table(data, [84 * mm, 14 * mm, 19 * mm, 19 * mm, 19 * mm, 19 * mm]))

    story.append(Paragraph("Between the two machines", H2))
    rec = R.reconcile(ca["results"], ma["results"])
    lines = []
    if rec["checked"]:
        lines.append(
            f"Events the Marketing Agent sent: {rec['sent_unique']} different ({rec['sent']} counting the repeated blocker). Stored by the Campaign Agent: {rec['stored']}. "
            + (
                "Nothing is missing."
                if not rec["missing"]
                else f"MISSING: {', '.join(rec['missing'][:8])}"
            )
        )
    else:
        lines.append(
            "The comparison of events sent against events stored could not be made (one side did not run the link tests)."
        )
    sends = [
        r
        for r in rows
        if r["test"] == "ma.email" and "sent, and only to the test recipient" in r["case"]
    ]
    if sends:
        lines.append(
            f"E-mails sent and checked in the Gmail sent folder: {len(sends)} run(s), {sum(1 for r in sends if r['status'] == PASS)} correct (to the test recipient only, never to the made-up contact)."
        )
    slack = [
        r
        for r in rows
        if r["test"] == "ma.slack" and r["case"].startswith("the messages can be read back")
    ]
    if slack:
        lines.append(
            f"Slack test messages posted: {sum((r.get('data') or {}).get('posted', 0) for r in slack)}, deleted again: {sum((r.get('data') or {}).get('deleted', 0) for r in slack)}."
        )
    story += [Paragraph(esc(line), BODY) for line in lines]

    story += [
        PageBreak(),
        Paragraph("How the system behaved over time (hours since the start)", H2),
    ]
    cm, mm_ = ca["metrics"], ma["metrics"]
    charts = [
        (
            "Campaign Agent heartbeat age",
            {"seconds since the last beat": R.series(cm, "hb_age", t0)},
            "s",
        ),
        (
            "Database round trip",
            {
                "Campaign Agent": R.series(cm, "db_ms", t0),
                "Marketing Agent": R.series(mm_, "db_ms", t0),
            },
            "ms",
        ),
        (
            "Requests waiting at the Campaign Agent, and open blockers",
            {
                "waiting": R.series(cm, "queue", t0),
                "open blockers": R.series(cm, "open_blockers", t0),
            },
            "",
        ),
        (
            "Marketing Agent running (1) or stopped (0), and Chrome processes",
            {
                "running": R.state_series(cm or mm_, "ma_actual", t0, "running"),
                "Chrome processes": R.series(mm_, "chrome_processes", t0),
            },
            "",
        ),
        (
            "Load average (1 minute)",
            {
                "Campaign Agent": R.series(cm, "load1", t0),
                "Marketing Agent": R.series(mm_, "load1", t0),
            },
            "",
        ),
        (
            "Free memory",
            {
                "Campaign Agent": R.series(cm, "mem_available_mb", t0),
                "Marketing Agent": R.series(mm_, "mem_available_mb", t0),
            },
            "MB",
        ),
        (
            "Model loaded in memory (1) or not (0)",
            {
                "Campaign Agent": R.loaded_series(cm, t0),
                "Marketing Agent": R.loaded_series(mm_, t0),
            },
            "",
        ),
    ]
    story += [KeepTogether([line_chart(title_, lines_, unit)]) for title_, lines_, unit in charts]
    unread = sum(1 for m in cm + mm_ if m.get("error"))
    story.append(
        Paragraph(
            esc(
                f"Samples taken: {len(cm)} on the Campaign Agent, {len(mm_)} on the Marketing Agent; {unread} could not be read."
            ),
            SMALL,
        )
    )

    story += [PageBreak(), Paragraph("Every check", H2)]
    data = [["Test / check", "Runs", "Pass rate", "Median time", "Last result"]]
    cases = R.per_case(rows)
    for k in cases:
        durs = sorted(k["durs"])
        data.append(
            [
                f"{k['test']}\n{k['case']}",
                str(k["n"]),
                f"{100 * k['ok'] // k['n']}%",
                f"{durs[len(durs) // 2]:.1f}s",
                f"{k['last'].upper()} {k['detail'][:90]}",
            ]
        )
    tab = table(data, [78 * mm, 11 * mm, 15 * mm, 18 * mm, 52 * mm])
    for i, k in enumerate(cases, start=1):
        tab.setStyle(TableStyle([("TEXTCOLOR", (4, i), (4, i), COLOR.get(k["last"], INK))]))
    story.append(tab)

    skipped = sorted({(r["test"], r["case"], r["detail"]) for r in rows if r["status"] == SKIP})
    if skipped:
        story += [
            Paragraph("Checks that were skipped, and why", H2),
            table([list(s) for s in skipped[:40]], [42 * mm, 62 * mm, 70 * mm], header=False),
        ]
    bad = [r for r in rows if r["status"] in BAD][:80]
    if bad:
        story += [
            Paragraph("Appendix: the failures, in time order", H2),
            table(
                [[r["at"][11:19], f"{r['test']}\n{r['case']}", r["detail"][:600]] for r in bad],
                [16 * mm, 54 * mm, 104 * mm],
                header=False,
            ),
        ]

    def footer(canvas: Any, _doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTE)
        canvas.drawString(18 * mm, 9 * mm, f"{title}  |  {', '.join(run_ids)}")
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main() -> int:
    ap = argparse.ArgumentParser(prog="qa.make_report")
    ap.add_argument("--ca", type=Path, help="results folder of the Campaign Agent side")
    ap.add_argument("--ma", type=Path, help="results folder of the Marketing Agent side")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--title", default="Campaign Agent and Marketing Agent: acceptance and soak test report"
    )
    args = ap.parse_args()
    ca, ma = R.load_side(args.ca), R.load_side(args.ma)
    if not ca["results"] and not ma["results"]:
        print("no results found in either folder")
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build(ca, ma, args.out, args.title)
    print(f"report written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

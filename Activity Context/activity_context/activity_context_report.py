#!/usr/bin/env python3
"""
Activity Context (PW-6968) — Excel reporting.

Turns a seed manifest + a results file into a single .xlsx a PM, a developer and a tester
can each read without needing the other two.

Sheets:
  Summary        headline pass rate, and the noise floor that bounds what is knowable
  Test Results   one row per check, with the expected value and what was reported
  Twin Pairs     A (with activity) vs B (without) side by side — the core comparison
  Judge          what the adapter reported and the span it quoted, for auditing
  Seeded Data    exactly what was created, so a failure can be reproduced in the UI
  Performance    response time per job
  Open Items     checks that could not be tested, with the reason

Blocked checks get their own sheet rather than being dropped. A coverage gap that is
written down is a known limitation; one that is silently omitted is a false pass rate.

    python3 activity_context_report.py --manifest m.json --results r.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))

HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=13)
LABEL_FONT = Font(bold=True)
PASS_FILL = PatternFill("solid", fgColor="C6EFCE")
FAIL_FILL = PatternFill("solid", fgColor="FFC7CE")
BLOCK_FILL = PatternFill("solid", fgColor="FFEB9C")
INFO_FILL = PatternFill("solid", fgColor="DDEBF7")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

XL_MAX = 32767
_ILLEGAL = {c: None for c in range(32) if c not in (9, 10, 13)}


def _clean(v):
    """openpyxl raises on control characters and corrupts silently past 32,767 chars."""
    if not isinstance(v, str):
        return v
    v = v.translate(_ILLEGAL)
    return v if len(v) <= XL_MAX else v[:XL_MAX - 3] + "..."


def _header(ws, headers, row=1):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font, c.fill = HDR_FONT, HDR_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
        c.border = BOX
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write(ws, row, values, wrap_cols=()):
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=_clean(v))
        c.alignment = Alignment(vertical="top", wrap_text=i in wrap_cols)
        c.border = BOX
    return row + 1


def _verdict_fill(v):
    if v == "PASS":
        return PASS_FILL
    if v == "BLOCKED":
        return BLOCK_FILL
    return FAIL_FILL


def sheet_summary(wb, manifest, results):
    ws = wb.active
    ws.title = "Summary"
    _widths(ws, [42, 22, 60])
    checks = results.get("checks", [])
    tally = {}
    for c in checks:
        tally[c["verdict"]] = tally.get(c["verdict"], 0) + 1
    testable = sum(v for k, v in tally.items() if k != "BLOCKED")
    passed = tally.get("PASS", 0)

    r = 1
    ws.cell(row=r, column=1, value="Activity Context — accuracy test").font = TITLE_FONT
    r += 2
    rows = [
        ("Environment", results.get("label") or results.get("env", "")),
        ("Manifest", results.get("manifest", "")),
        ("Run at", results.get("created_at", "")),
        ("", ""),
        ("Checks that could be tested", testable),
        ("Passed", passed),
        ("Pass rate", f"{100 * passed / testable:.0f}%" if testable else "n/a"),
        ("Blocked (see Open Items)", tally.get("BLOCKED", 0)),
        ("Failed", tally.get("FAIL", 0)),
        ("Failed silently (not_stated)", tally.get("FAIL (silent)", 0)),
    ]
    for label, val in rows:
        if label:
            ws.cell(row=r, column=1, value=label).font = LABEL_FONT
            ws.cell(row=r, column=2, value=val)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="How to read this").font = LABEL_FONT
    r += 1
    for line in [
        "The judge only reports what the reasoning SAYS. Pass/fail is decided by comparing "
        "that against the value recorded at seed time, so every failure names a concrete "
        "expectation.",
        "Blocked checks are counted separately and never in the pass rate — see Open Items "
        "for why each one could not be tested.",
        "Twin pairs are identical except for the activity, so any scoring difference "
        "between A and B has only one possible cause.",
        "The scorer is not deterministic: two runs of the same job differ by roughly "
        "+/- 11 points. Treat any smaller difference as noise.",
    ]:
        c = ws.cell(row=r, column=1, value=line)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        ws.row_dimensions[r].height = 30
        r += 1
    return ws


def sheet_test_results(wb, results):
    ws = wb.create_sheet("Test Results")
    hdr = ["Job", "Candidate", "Check", "Argument", "Expected", "Reported", "Verdict",
           "Score", "Quote"]
    _widths(ws, [14, 26, 18, 40, 14, 14, 16, 8, 62])
    _header(ws, hdr)
    r = 2
    for c in results.get("checks", []):
        r = _write(ws, r, [c.get("job"), c.get("candidate"), c.get("question"),
                           c.get("arg"), c.get("expected"), c.get("reported"),
                           c.get("verdict"), c.get("score"), c.get("quote")],
                   wrap_cols={4, 9})
        ws.cell(row=r - 1, column=7).fill = _verdict_fill(c.get("verdict"))
    ws.auto_filter.ref = f"A1:{get_column_letter(len(hdr))}{max(r - 1, 1)}"
    return ws


def sheet_twin_pairs(wb, results):
    """A (with activity) against B (without). The single-variable comparison."""
    ws = wb.create_sheet("Twin Pairs")
    hdr = ["Job", "Case", "What it tests", "A score (activity)", "B score (control)",
           "Delta", "A used the signal?", "Verdict"]
    _widths(ws, [14, 28, 46, 16, 16, 9, 16, 16])
    _header(ws, hdr)
    pairs = {}
    for c in results.get("checks", []):
        key = (c.get("job"), c.get("case") or c.get("arg"))
        pairs.setdefault(key, []).append(c)
    r = 2
    for (job, case), items in sorted(pairs.items(), key=lambda kv: str(kv[0])):
        a = next((x for x in items if str(x.get("candidate", "")).endswith("A")), None)
        b = next((x for x in items if str(x.get("candidate", "")).endswith("B")), None)
        if not a:
            continue
        sa, sb = a.get("score"), (b or {}).get("score")
        delta = (sa - sb) if isinstance(sa, (int, float)) and isinstance(sb, (int, float)) \
            else None
        r = _write(ws, r, [job, case, a.get("why", ""), sa, sb, delta,
                           a.get("reported"), a.get("verdict")], wrap_cols={3})
        ws.cell(row=r - 1, column=8).fill = _verdict_fill(a.get("verdict"))
    return ws


def sheet_judge(wb, results):
    ws = wb.create_sheet("Judge")
    hdr = ["Candidate", "Question", "Reported", "Confidence", "Value reported", "Quote"]
    _widths(ws, [26, 20, 14, 12, 22, 78])
    _header(ws, hdr)
    r = 2
    for c in results.get("checks", []):
        r = _write(ws, r, [c.get("candidate"), c.get("question"), c.get("reported"),
                           c.get("confidence"), c.get("value_reported"), c.get("quote")],
                   wrap_cols={6})
    u = results.get("judge_usage") or {}
    if u:
        r += 1
        ws.cell(row=r, column=1, value="Adapter usage").font = LABEL_FONT
        ws.cell(row=r, column=2, value=json.dumps(u))
    return ws


def _describe_setup(c):
    bits = []
    if c.get("conflict_value"):
        bits.append(f"resume: {c['conflict_value']}")
    if c.get("notes"):
        bits.append(f"{len(c['notes'])} note(s)")
    if c.get("calls"):
        bits.append(f"{len(c['calls'])} call log(s)")
    return "; ".join(bits) or "no activity"


def _describe_checks(c, blocked=False):
    out = []
    for ch in c.get("checks", []):
        if bool(ch.get("blocked_reason")) != blocked:
            continue
        out.append(f"{ch['question']}({ch.get('arg','')}) -> {ch['expected']}")
    return " | ".join(out)


def sheet_seeded_data(wb, manifest):
    """Exactly what was created, so any failure can be reproduced in the UI."""
    ws = wb.create_sheet("Seeded Data")
    hdr = ["Job", "Job slug", "Candidate", "Candidate slug", "Activity", "Setup",
           "Expected"]
    _widths(ws, [14, 28, 26, 28, 12, 44, 52])
    _header(ws, hdr)
    r = 2
    for job in manifest.get("jobs", []):
        for c in job.get("candidates", []):
            r = _write(ws, r, [job.get("key"), job.get("job_slug"), c.get("name"),
                               c.get("slug"),
                               "yes" if c.get("has_activity", bool(c.get("notes") or
                                                                   c.get("calls"))) else "no",
                               _describe_setup(c), _describe_checks(c)],
                       wrap_cols={6, 7})
    ws.auto_filter.ref = f"A1:{get_column_letter(len(hdr))}{max(r - 1, 1)}"
    return ws


def sheet_performance(wb, results):
    ws = wb.create_sheet("Performance")
    hdr = ["Job", "Candidates scored", "Time to all scored (s)", "First response (s)",
           "Polls"]
    _widths(ws, [26, 18, 22, 18, 10])
    _header(ws, hdr)
    r = 2
    for slug, h in (results.get("harvest") or {}).items():
        for t in h.get("timings", []) or [{}]:
            r = _write(ws, r, [h.get("title") or h.get("job_key") or slug,
                               t.get("scored"), t.get("time_to_scored_s"),
                               t.get("first_response_s"), t.get("polls")])
    r += 1
    c = ws.cell(row=r, column=1,
                value="Whichever deployment runs first builds the candidate pool and pays "
                      "for it; the second reads it warm from the shared database. Compare "
                      "like with like.")
    c.alignment = Alignment(wrap_text=True)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    return ws


def sheet_open_items(wb, results):
    """Blocked checks — a written-down gap, not a silent omission."""
    ws = wb.create_sheet("Open Items")
    hdr = ["Job", "Candidate", "Check", "Expected", "Why it could not be tested"]
    _widths(ws, [14, 26, 22, 14, 78])
    _header(ws, hdr)
    r = 2
    for c in results.get("checks", []):
        if not c.get("blocked_reason"):
            continue
        r = _write(ws, r, [c.get("job"), c.get("candidate"), c.get("question"),
                           c.get("expected"), c.get("blocked_reason")], wrap_cols={5})
        ws.cell(row=r - 1, column=5).fill = BLOCK_FILL
    if r == 2:
        _write(ws, r, ["", "", "", "", "No blocked checks in this run."])
    return ws


def results_from_edge_manifest(edge):
    """Build a skeleton results file from an edge manifest — useful before a run."""
    checks = []
    for job in edge.get("jobs", []):
        for c in job.get("candidates", []):
            for ch in c.get("checks", []):
                checks.append({"job": job["key"], "candidate": c["name"],
                               "slug": c["slug"], "case": c.get("case"),
                               "why": c.get("why"), "question": ch["question"],
                               "arg": ch.get("arg", ""), "expected": ch["expected"],
                               "reported": "not_run", "verdict": "BLOCKED"
                               if ch.get("blocked_reason") else "NOT RUN",
                               "blocked_reason": ch.get("blocked_reason")})
    return {"env": edge.get("env"), "manifest": "edge", "checks": checks}


def build_report(manifest, results, out_path):
    wb = Workbook()
    sheet_summary(wb, manifest, results)
    sheet_test_results(wb, results)
    sheet_twin_pairs(wb, results)
    sheet_judge(wb, results)
    sheet_seeded_data(wb, manifest)
    sheet_performance(wb, results)
    sheet_open_items(wb, results)
    wb.save(out_path)
    return out_path


def main():
    p = argparse.ArgumentParser(description="Build the Activity Context Excel report")
    p.add_argument("--manifest", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--out", default=os.path.join(HERE, "activity_context_report.xlsx"))
    a = p.parse_args()

    manifest = json.load(open(a.manifest))
    results = json.load(open(a.results))
    path = build_report(manifest, results, a.out)

    checks = results.get("checks", [])
    tally = {}
    for c in checks:
        tally[c["verdict"]] = tally.get(c["verdict"], 0) + 1
    testable = sum(v for k, v in tally.items() if k != "BLOCKED")
    print(f"report written to {path}")
    print(f"  checks {len(checks)}  testable {testable}  "
          f"passed {tally.get('PASS', 0)}  blocked {tally.get('BLOCKED', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

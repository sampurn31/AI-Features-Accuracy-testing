#!/usr/bin/env python3
"""
Build the final Excel report from results.jsonl.

  Sheet 1 - Activity Context Evaluation
    Job | Candidate | AI Match Score | AI Match Analysis | Judge Score (1-5) |
    Judge Reasoning | Activity Context Used (Yes/No) | Final Verdict

  Sheet 2 - AI2 vs DEV Comparison
    Job | Candidate | DEV Match Score | AI2 Match Score | DEV Reasoning | AI2 Reasoning |
    Judge Comparison | Winner | Did Activity Context Add Value? | Final Verdict

  Sheet 3 - Response Time
    One row per job. NOTE: whichever deployment runs FIRST builds the candidate pool and
    the second reads it warm from the shared database, so the raw dev-vs-ai2 column is
    mostly measuring run order. The run alternates who goes first and the Ran First column
    is here so that is visible rather than mistaken for a real difference.

Headline numbers also go to the console and summary.json.

    python3 report.py
    python3 report.py --out /path/to/report.xlsx
"""

import argparse
import json
import os
import sys
from collections import Counter
from statistics import mean

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "results.jsonl")
XLSX = os.path.join(HERE, "activity_context_report.xlsx")
SUMMARY = os.path.join(HERE, "summary.json")

SHEET1 = ["Job", "Candidate", "AI Match Score", "AI Match Analysis", "Judge Score (1-5)",
          "Judge Reasoning", "Activity Context Used (Yes/No)", "Final Verdict"]
SHEET2 = ["Job", "Candidate", "DEV Match Score", "AI2 Match Score", "DEV Reasoning",
          "AI2 Reasoning", "Judge Comparison", "Winner",
          "Did Activity Context Add Value? (Yes/No)", "Final Verdict"]
SHEET3 = ["Job", "Ran First", "Candidates (DEV)", "Candidates (AI2)",
          "DEV First Response (s)", "AI2 First Response (s)",
          "DEV Time to All Scored (s)", "AI2 Time to All Scored (s)",
          "Difference (AI2 - DEV) (s)", "Slower Environment"]

WIDTHS1 = [26, 22, 12, 62, 10, 62, 12, 26]
WIDTHS2 = [26, 22, 12, 12, 52, 52, 56, 12, 14, 30]
WIDTHS3 = [26, 10, 14, 14, 15, 15, 16, 16, 16, 18]

HEAD = PatternFill("solid", fgColor="1F3864")
GREEN = PatternFill("solid", fgColor="C6EFCE")
AMBER = PatternFill("solid", fgColor="FFEB9C")
RED = PatternFill("solid", fgColor="FFC7CE")
BLUE = PatternFill("solid", fgColor="DDEBF7")

XL_MAX = 32767
_ILLEGAL = {c: None for c in range(32) if c not in (9, 10, 13)}


def cell(v):
    """openpyxl raises on control characters and corrupts silently past 32,767 chars."""
    if not isinstance(v, str):
        return v
    v = v.translate(_ILLEGAL)
    return v if len(v) <= XL_MAX else v[:XL_MAX - 3] + "..."


def load():
    """Returns (flat candidate records, per-job blocks). Timings live on the job block."""
    if not os.path.exists(RAW):
        raise SystemExit(f"No results at {RAW} — run evaluate.py first")
    recs, jobs = [], []
    for line in open(RAW):
        line = line.strip()
        if line:
            j = json.loads(line)
            jobs.append(j)
            recs.extend(j["records"])
    if not recs:
        raise SystemExit(f"{RAW} has no records")
    return recs, jobs


def _t(job, env, key):
    v = (job.get("timings") or {}).get(env)
    return v.get(key) if isinstance(v, dict) else (v if key == "time_to_scored_s" else None)


def timing_rows(jobs):
    rows = []
    for j in jobs:
        ps = j.get("pool_sizes") or {}
        d, a = _t(j, "dev", "time_to_scored_s"), _t(j, "ai2", "time_to_scored_s")
        diff = slower = None
        if isinstance(d, (int, float)) and isinstance(a, (int, float)):
            diff = round(a - d, 2)
            slower = "AI2" if diff > 0.5 else ("DEV" if diff < -0.5 else "Comparable")
        rows.append([j.get("job_title") or j["job_key"], j.get("ran_first"),
                     ps.get("dev"), ps.get("ai2"),
                     _t(j, "dev", "first_response_s"), _t(j, "ai2", "first_response_s"),
                     d, a, diff, slower])
    return rows


def timing_summary(jobs):
    pairs = [(_t(j, "dev", "time_to_scored_s"), _t(j, "ai2", "time_to_scored_s"),
              j.get("ran_first")) for j in jobs]
    pairs = [(d, a, f) for d, a, f in pairs
             if isinstance(d, (int, float)) and isinstance(a, (int, float))]
    if not pairs:
        return {}
    out = {"jobs_timed": len(pairs),
           "dev_mean_s": round(mean(d for d, _, _ in pairs), 2),
           "ai2_mean_s": round(mean(a for _, a, _ in pairs), 2)}
    for slot in ("first", "second"):
        dv = [d for d, a, f in pairs if (f == "dev") == (slot == "first")]
        av = [a for d, a, f in pairs if (f == "ai2") == (slot == "first")]
        if dv and av:
            out[f"dev_when_{slot}_s"] = round(mean(dv), 2)
            out[f"ai2_when_{slot}_s"] = round(mean(av), 2)
    fr = [(_t(j, "dev", "first_response_s"), _t(j, "ai2", "first_response_s")) for j in jobs]
    fr = [(x, y) for x, y in fr if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    if fr:
        out["dev_first_response_s"] = round(mean(x for x, _ in fr), 3)
        out["ai2_first_response_s"] = round(mean(y for _, y in fr), 3)
    return out


def write_sheet(ws, headers, widths, rows, wrap_cols):
    ws.append(headers)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    ws.row_dimensions[1].height = 30
    for r in rows:
        ws.append([cell(v) for v in r])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in wrap_cols)
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"


def build(recs, out, jobs=()):
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Activity Context Evaluation"
    r1 = [r for r in recs if "flow1" in r]
    write_sheet(ws1, SHEET1, WIDTHS1,
                [[r["job_title"], r["candidate"], r["ai2_score"], r["ai2_analysis"],
                  r["flow1"]["judge_score"], r["flow1"]["detailed_reasoning"],
                  r["flow1"]["activity_context_used"], r["flow1"]["final_verdict"]]
                 for r in r1], wrap_cols={4, 6, 8})
    for i, r in enumerate(r1, start=2):
        s = r["flow1"]["judge_score"]
        ws1.cell(row=i, column=5).fill = GREEN if s >= 4 else AMBER if s == 3 else RED
        if r["flow1"]["activity_context_used"] == "Yes":
            ws1.cell(row=i, column=7).fill = BLUE

    ws2 = wb.create_sheet("AI2 vs DEV Comparison")
    r2 = [r for r in recs if "flow2" in r]
    write_sheet(ws2, SHEET2, WIDTHS2,
                [[r["job_title"], r["candidate"], r.get("dev_score"), r["ai2_score"],
                  r.get("dev_analysis", ""), r["ai2_analysis"],
                  r["flow2"]["comparison_reasoning"], r["flow2"]["winner"],
                  r["flow2"]["activity_added_measurable_value"],
                  r["flow2"]["final_verdict"]] for r in r2], wrap_cols={5, 6, 7, 10})
    for i, r in enumerate(r2, start=2):
        w = r["flow2"]["winner"]
        ws2.cell(row=i, column=8).fill = GREEN if w == "AI2" else RED if w == "DEV" else AMBER
        if r["flow2"]["activity_added_measurable_value"] == "Yes":
            ws2.cell(row=i, column=9).fill = BLUE

    ws3 = wb.create_sheet("Response Time")
    trows = timing_rows(jobs)
    write_sheet(ws3, SHEET3, WIDTHS3, trows, wrap_cols=set())
    for i, r in enumerate(trows, start=2):
        ws3.cell(row=i, column=10).fill = (RED if r[9] == "AI2" else
                                           GREEN if r[9] == "DEV" else AMBER)

    wb.save(out)
    return len(r1), len(r2), len(trows)


def summarise(recs):
    r1 = [r["flow1"] for r in recs if "flow1" in r]
    r2 = [r["flow2"] for r in recs if "flow2" in r]
    s = {"candidates": len(recs),
         "in_dataset": sum(1 for r in recs if r.get("in_dataset")),
         "jobs": len({r["job_key"] for r in recs}),
         # anything that reached neither sheet, named rather than silently dropped
         "not_scored_by_ai2": sum(1 for r in recs if r.get("no_analysis")),
         "errored": sum(1 for r in recs if r.get("error")),
         "not_returned_by_dev": sum(1 for r in recs if r.get("flow2_skipped"))}

    if r1:
        sc = [v["judge_score"] for v in r1]
        s["flow1"] = {
            "judged": len(r1),
            "mean_judge_score": round(mean(sc), 2),
            "acceptable_4_or_5": sum(1 for x in sc if x >= 4),
            "acceptable_pct": round(100 * sum(1 for x in sc if x >= 4) / len(sc), 1),
            "distribution": {str(k): v for k, v in sorted(Counter(sc).items())},
            "activity_context_used": sum(1 for v in r1 if v["activity_context_used"] == "Yes"),
            "activity_improved": sum(1 for v in r1 if v["activity_improved_reasoning"] == "Yes"),
            "activity_wrongly_influenced":
                sum(1 for v in r1 if v["activity_wrongly_influenced"] == "Yes"),
            "important_details_ignored":
                sum(1 for v in r1 if v["important_details_ignored"] == "Yes"),
            "reasoning_factually_correct":
                sum(1 for v in r1 if v["reasoning_factually_correct"] == "Yes"),
            "criterion_scored_correctly":
                sum(1 for v in r1 if v.get("activity_criterion_scored_correctly") == "Yes"),
            "verdict": dict(Counter(v["analysis_verdict"] for v in r1)),
        }
        with_act = [r for r in recs if "flow1" in r and (r.get("notes") or r.get("calls"))]
        if with_act:
            used = sum(1 for r in with_act if r["flow1"]["activity_context_used"] == "Yes")
            ok = sum(1 for r in with_act
                     if r["flow1"].get("activity_criterion_scored_correctly") == "Yes")
            s["flow1"]["with_activity"] = len(with_act)
            s["flow1"]["activity_used_pct_of_those_with_activity"] = \
                round(100 * used / len(with_act), 1)
            s["flow1"]["criterion_correct_pct_of_those_with_activity"] = \
                round(100 * ok / len(with_act), 1)
        planted = [r for r in recs if "flow1" in r and r.get("signal")]
        if planted:
            hit = sum(1 for r in planted if r["flow1"]["activity_context_used"] == "Yes")
            s["flow1"]["planted_signal_cases"] = len(planted)
            s["flow1"]["planted_signal_caught_pct"] = round(100 * hit / len(planted), 1)
        by = {}
        for r in recs:
            if "flow1" in r:
                by.setdefault(r["scenario"], []).append(r["flow1"]["judge_score"])
        s["flow1"]["by_scenario"] = {k: {"mean": round(mean(v), 2), "n": len(v)}
                                     for k, v in sorted(by.items())}

    if r2:
        w = Counter(v["winner"] for v in r2)
        s["flow2"] = {
            "compared": len(r2), "winner": dict(w),
            "ai2_win_pct": round(100 * w["AI2"] / len(r2), 1),
            "ai2_references_activity":
                sum(1 for v in r2 if v.get("ai2_references_activity") == "Yes"),
            "dev_references_activity":
                sum(1 for v in r2 if v.get("dev_references_activity") == "Yes"),
            "activity_added_value":
                sum(1 for v in r2 if v["activity_added_measurable_value"] == "Yes"),
            "regressions": sum(1 for v in r2 if v["any_regression"] == "Yes"),
            "incorrect_reasoning_introduced":
                sum(1 for v in r2 if v["incorrect_reasoning_introduced"] == "Yes"),
            "confidence": dict(Counter(v["confidence"] for v in r2)),
        }
        deltas = [r["ai2_score"] - r["dev_score"] for r in recs
                  if "flow2" in r and isinstance(r.get("ai2_score"), (int, float))
                  and isinstance(r.get("dev_score"), (int, float))]
        if deltas:
            s["flow2"]["mean_score_delta_ai2_minus_dev"] = round(mean(deltas), 2)
            s["flow2"]["scores_changed"] = sum(1 for d in deltas if d != 0)
    return s


def show(s):
    log = print
    log("\n" + "=" * 70)
    log(f"{s['jobs']} jobs, {s['candidates']} candidates matched "
        f"({s['in_dataset']} from this dataset)")
    for k, lbl in (("not_scored_by_ai2", "AI2 returned no analysis"),
                   ("not_returned_by_dev", "not in the DEV pool, no comparison"),
                   ("errored", "errored")):
        if s.get(k):
            log(f"  excluded: {s[k]} {lbl}")

    if "flow1" in s:
        f = s["flow1"]
        log("\nFLOW 1 — Activity Context Evaluation")
        log(f"  mean judge score            {f['mean_judge_score']} / 5")
        log(f"  acceptable (4-5)            {f['acceptable_4_or_5']}/{f['judged']}"
            f"  ({f['acceptable_pct']}%)")
        log(f"  distribution                {f['distribution']}")
        log(f"  reasoning factually correct {f['reasoning_factually_correct']}/{f['judged']}")
        log(f"  activity context used       {f['activity_context_used']}/{f['judged']}"
            + (f"  ({f['activity_used_pct_of_those_with_activity']}% of the "
               f"{f['with_activity']} that had any)" if "with_activity" in f else ""))
        if "criterion_correct_pct_of_those_with_activity" in f:
            log(f"  RIGHT CRITERION SCORED      {f['criterion_scored_correctly']}"
                f"  ({f['criterion_correct_pct_of_those_with_activity']}% of those with "
                f"activity)  <- the number that matters")
        log(f"  activity improved reasoning {f['activity_improved']}")
        log(f"  activity wrongly influenced {f['activity_wrongly_influenced']}")
        log(f"  important details ignored   {f['important_details_ignored']}")
        if "planted_signal_cases" in f:
            log(f"  planted signals caught      {f['planted_signal_caught_pct']}% of "
                f"{f['planted_signal_cases']}")
        log("  mean score by scenario:")
        for k, v in sorted(f["by_scenario"].items(), key=lambda kv: -kv[1]["mean"]):
            log(f"      {k:<20} {v['mean']:<6} n={v['n']}")

    if "flow2" in s:
        f = s["flow2"]
        log("\nFLOW 2 — AI2 vs DEV")
        log(f"  winner                      {f['winner']}  (AI2 {f['ai2_win_pct']}%)")
        log(f"  references activity         AI2 {f['ai2_references_activity']}"
            f"   DEV {f['dev_references_activity']}   of {f['compared']}")
        log(f"  activity added value        {f['activity_added_value']}/{f['compared']}")
        log(f"  regressions                 {f['regressions']}")
        log(f"  incorrect reasoning added   {f['incorrect_reasoning_introduced']}")
        if "mean_score_delta_ai2_minus_dev" in f:
            log(f"  mean score delta            {f['mean_score_delta_ai2_minus_dev']:+} "
                f"({f['scores_changed']} candidates changed)")

    t = s.get("timing") or {}
    if t:
        log("\nRESPONSE TIME")
        log(f"  time to all scored     DEV {t['dev_mean_s']}s   AI2 {t['ai2_mean_s']}s")
        if "dev_when_first_s" in t:
            log(f"  like-for-like, cold    DEV {t['dev_when_first_s']}s   "
                f"AI2 {t['ai2_when_first_s']}s")
        if "dev_when_second_s" in t:
            log(f"  like-for-like, warm    DEV {t['dev_when_second_s']}s   "
                f"AI2 {t['ai2_when_second_s']}s")
        if "dev_first_response_s" in t:
            log(f"  first response         DEV {t['dev_first_response_s']}s   "
                f"AI2 {t['ai2_first_response_s']}s")
        log("  (whoever runs first builds the pool; the second reads it warm)")
    log("=" * 70)


def main():
    p = argparse.ArgumentParser(description="Build the Excel report")
    p.add_argument("--out", default=XLSX)
    a = p.parse_args()

    recs, jobs = load()
    n1, n2, n3 = build(recs, a.out, jobs)
    s = summarise(recs)
    s["timing"] = timing_summary(jobs)
    json.dump(s, open(SUMMARY, "w"), indent=2)
    show(s)
    print(f"\nExcel  {a.out}")
    print(f"       sheet 1 'Activity Context Evaluation'  {n1} rows")
    print(f"       sheet 2 'AI2 vs DEV Comparison'       {n2} rows")
    print(f"       sheet 3 'Response Time'                {n3} rows")
    print(f"JSON   {SUMMARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

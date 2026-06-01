"""
run_all_tests.py
-----------------
Master runner: executes every test suite in order, applies an inter-suite
cool-down, collects all log entries, then writes a timestamped Excel report
under reports/.

Usage
-----
    python3 run_all_tests.py                 # run everything + write Excel
    python3 run_all_tests.py --no-excel      # run only (skip report)
    python3 run_all_tests.py --excel-only    # skip tests, write from last log

Existing test files and their run_all() functions are never touched.
"""

from __future__ import annotations

import sys
import os
import time
import argparse
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tests"))

SUITES: list[tuple[str, str]] = [
    ("Candidate Summary",         "test_candidate_summary"),
    ("Job Description",           "test_job_description"),
    ("Note Summary",              "test_note_summary"),
    ("Call Log Summary",          "test_call_log_summary"),
    ("Email Template",            "test_email_template"),
    ("Generate Email",            "test_generate_email"),
    ("Generate Email Sequence",   "test_generate_email_sequence"),
    ("Get Suggestions",           "test_get_suggestions"),
]

INTER_SUITE_DELAY = 3.0


def _banner(text: str, width: int = 70) -> None:
    print("\n" + "█" * width)
    print(f"  {text}")
    print("█" * width)


def _section(text: str) -> None:
    print(f"\n  ▶  {text}")


def run_all_suites() -> list[dict]:
    """Import and execute every suite's run_all(); return all log entries."""
    import importlib

    all_entries: list[dict] = []

    _banner("NEPTUNE LLM REPLACEMENT — FULL TEST RUN")
    print(f"  Started : {datetime.now(timezone.utc).isoformat()}")
    print(f"  Suites  : {len(SUITES)}")
    print(f"  Delay   : {INTER_SUITE_DELAY}s between suites\n")

    suite_summaries: list[dict] = []

    for i, (label, module_name) in enumerate(SUITES):
        _banner(f"SUITE {i + 1}/{len(SUITES)} — {label.upper()}")
        t0 = time.perf_counter()

        try:
            mod = importlib.import_module(module_name)
            entries: list[dict] = mod.run_all()
            all_entries.extend(entries)
        except Exception as exc:
            print(f"\n  [ERROR] Suite '{label}' raised: {exc}")
            import traceback
            traceback.print_exc()
            entries = []

        elapsed = round(time.perf_counter() - t0, 1)

        total   = len(entries)
        passed  = sum(1 for e in entries if e.get("final_status") == "PASS")
        review  = sum(1 for e in entries if e.get("final_status") == "REVIEW")
        failed  = sum(1 for e in entries if e.get("final_status") == "FAIL")
        errored = sum(1 for e in entries if e.get("final_status") == "ERROR")
        scores  = [e["judge_score"] for e in entries if e.get("judge_score") is not None]
        avg_sc  = round(sum(scores) / len(scores), 2) if scores else None

        suite_summaries.append({
            "label":   label,
            "module":  module_name,
            "total":   total,
            "passed":  passed,
            "review":  review,
            "failed":  failed,
            "errored": errored,
            "avg_score": avg_sc,
            "elapsed_s": elapsed,
        })

        _section(
            f"Suite done in {elapsed}s — "
            f"PASS:{passed}  REVIEW:{review}  FAIL:{failed}  ERROR:{errored}"
            + (f"  AvgScore:{avg_sc}" if avg_sc is not None else "")
        )

        if i < len(SUITES) - 1:
            _section(f"Cooling down {INTER_SUITE_DELAY}s …")
            time.sleep(INTER_SUITE_DELAY)

    _banner("GRAND SUMMARY")
    col_w = max(len(s["label"]) for s in suite_summaries) + 2
    header = f"  {'Suite':<{col_w}} {'Total':>5}  {'PASS':>4}  {'REVIEW':>6}  {'FAIL':>4}  {'ERROR':>5}  {'AvgScore':>8}  {'Time(s)':>7}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for s in suite_summaries:
        avg_s = f"{s['avg_score']:.2f}" if s["avg_score"] is not None else "  n/a"
        print(
            f"  {s['label']:<{col_w}} {s['total']:>5}  {s['passed']:>4}  "
            f"{s['review']:>6}  {s['failed']:>4}  {s['errored']:>5}  "
            f"{avg_s:>8}  {s['elapsed_s']:>7}"
        )
    grand_total   = sum(s["total"]   for s in suite_summaries)
    grand_pass    = sum(s["passed"]  for s in suite_summaries)
    grand_review  = sum(s["review"]  for s in suite_summaries)
    grand_fail    = sum(s["failed"]  for s in suite_summaries)
    grand_error   = sum(s["errored"] for s in suite_summaries)
    all_scores    = [e["judge_score"] for e in all_entries if e.get("judge_score") is not None]
    grand_avg     = round(sum(all_scores) / len(all_scores), 2) if all_scores else None
    print("  " + "-" * (len(header) - 2))
    print(
        f"  {'TOTAL':<{col_w}} {grand_total:>5}  {grand_pass:>4}  "
        f"{grand_review:>6}  {grand_fail:>4}  {grand_error:>5}  "
        f"{grand_avg if grand_avg else '  n/a':>8}"
    )
    print()

    return all_entries


def main():
    parser = argparse.ArgumentParser(description="Neptune LLM Replacement – full test runner")
    parser.add_argument("--no-excel",    action="store_true", help="Skip Excel report generation")
    parser.add_argument("--excel-only",  action="store_true", help="Skip tests; write Excel from last log file")
    args = parser.parse_args()

    entries: list[dict] = []

    if not args.excel_only:
        entries = run_all_suites()
    else:
        import json
        from pathlib import Path
        log_dir = Path(_ROOT) / "logs"
        log_files = sorted(log_dir.glob("run_*.jsonl"))
        if not log_files:
            print("No run_*.jsonl log files found in logs/. Run without --excel-only first.")
            sys.exit(1)
        last_log = log_files[-1]
        print(f"\n  Loading entries from {last_log.name} …")
        for line in last_log.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
        print(f"  Loaded {len(entries)} entries.\n")

    if not args.no_excel:
        from excel_reporter import write_excel_report
        report_path = write_excel_report(entries)
        print(f"\n  Excel report  →  {report_path}\n")


if __name__ == "__main__":
    main()

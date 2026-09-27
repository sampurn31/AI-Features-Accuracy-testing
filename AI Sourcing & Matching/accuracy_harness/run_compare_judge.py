"""
A/B comparative judge over the two-run snapshots: for every candidate present in BOTH runs,
an LLM judge decides which run's SCORING is more accurate/justified — run 1 (summary fed to the
scorer) vs run 2 (no summary). Answers: does feeding the summary produce better scoring?

De-biased: which run is shown as "A" vs "B" is randomized per candidate (stable md5 coin), so the
judge can't favor by position; verdicts are mapped back to run1/run2 afterward.

  source set_env.sh                      # ANTHROPIC_API_KEY (judge runs on Sonnet 5, no RCRM calls)
  python3 run_compare_judge.py --run-id ts9run1                 # judge ALL matched candidates
  python3 run_compare_judge.py --run-id ts9run1 --limit-per-query 25   # sample per query
Output: output/compare_judge_<runid>.xlsx (Dashboard tally + Verdicts) + console tally.
run1 = the FIRST snapshot account (summary-fed), run2 = SECOND (no summary).
"""
import argparse
import hashlib
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

import config
import llm
import scale_report as SR

WORKERS = int(os.environ.get("COMPARE_JUDGE_WORKERS", "8"))
RUBRIC = os.path.join(config.PROMPTS_DIR, "compare_scoring_rubric.md")
# Display labels for the two snapshots (run1 = first phase captured, run2 = second).
# Override per experiment, e.g. RUN1_LABEL="old prompt" RUN2_LABEL="new prompt".
L1 = os.environ.get("RUN1_LABEL", "run 1")
L2 = os.environ.get("RUN2_LABEL", "run 2")


def _key(c):
    li = re.sub(r"\s+", "", (c.get("linkedinUrl") or "").strip().lower())
    return li or ("id:" + str(c.get("id")))


def _profile(c):
    exp = [{"title": e.get("title"), "company": e.get("company"),
            "description": (e.get("description") or "")[:300]} for e in (c.get("experience") or [])][:6]
    return {"position": c.get("position"), "company": c.get("company"),
            "city": c.get("city"), "state": c.get("state"), "country": c.get("country"),
            "keySkills": c.get("keySkills"), "workexpyr": c.get("workexpyr"),
            "education": c.get("education"), "experience": exp}


def _scoring(c):
    return {"matchScore": c.get("matchScore"), "matchAnalysis": c.get("matchAnalysis")}


def _a_is_run1(key):
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 2 == 0


def _judge_pair(item, rubric):
    qid, query, criteria, c1, c2 = item
    key = _key(c1)
    a_run1 = _a_is_run1(key)
    sa, sb = (_scoring(c1), _scoring(c2)) if a_run1 else (_scoring(c2), _scoring(c1))
    v = llm.judge_compare_scoring(rubric, query, criteria, _profile(c1), sa, sb)
    row = {"qid": qid, "name": f"{c1.get('firstname') or ''} {c1.get('lastname') or ''}".strip(),
           "linkedin": c1.get("linkedinUrl"), "run1_score": c1.get("matchScore"),
           "run2_score": c2.get("matchScore")}
    if v.get("_error"):
        row.update({"winner": "error", "confidence": "", "key_differences": v["_error"], "reasoning": ""})
        return row
    verdict = v.get("verdict")
    if verdict == "equivalent":
        winner = "equivalent"
    elif verdict == "A_better":
        winner = "run1" if a_run1 else "run2"
    else:  # B_better
        winner = "run2" if a_run1 else "run1"
    row.update({"winner": winner, "confidence": v.get("confidence", ""),
                "key_differences": v.get("key_differences", ""), "reasoning": v.get("reasoning", "")})
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit-per-query", type=int, default=0, help="0 = all matched")
    args = ap.parse_args()
    config.require_runtime_env()
    base = os.path.join(config.OUTPUT_DIR, f"compare_{args.run_id}")
    r1 = json.load(open(base + "_run1.json"))["snapshot"]
    r2 = json.load(open(base + "_run2.json"))["snapshot"]
    state = json.load(open(base + "_state.json"))["queries"]

    items = []
    for qid in r1:
        a = {_key(c): c for c in r1[qid]}
        b = {_key(c): c for c in r2.get(qid, [])}
        common = [k for k in a if k in b]
        common.sort()
        if args.limit_per_query:
            common = common[: args.limit_per_query]
        query = state.get(qid, {}).get("aiSearch", qid)
        criteria = state.get(qid, {}).get("criteria", [])
        for k in common:
            items.append((qid, query, criteria, a[k], b[k]))
    print(f"judging {len(items)} matched candidates ({WORKERS} workers) — run1={L1}, run2={L2}\n")
    if not os.path.exists(RUBRIC):
        raise SystemExit(f"Missing rubric {RUBRIC}")
    rubric = open(RUBRIC, encoding="utf-8").read()

    rows, done = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_judge_pair, it, rubric) for it in items]
        for fut in as_completed(futs):
            rows.append(fut.result())
            done += 1
            if done % 25 == 0 or done == len(items):
                print(f"  {done}/{len(items)} judged")

    tally = Counter(r["winner"] for r in rows)
    n = sum(tally[w] for w in ("run1", "run2", "equivalent"))
    print("\n=== VERDICT TALLY (matched candidates) ===")
    print(f"  run1 ({L1}) better : {tally['run1']}")
    print(f"  run2 ({L2}) better : {tally['run2']}")
    print(f"  equivalent                : {tally['equivalent']}")
    print(f"  judge errors              : {tally['error']}")
    if n:
        print(f"\n  -> run1 wins {tally['run1']/n:.0%} · run2 wins {tally['run2']/n:.0%} · "
              f"tie {tally['equivalent']/n:.0%}   (of {n} decided)")
    print("\nper-query:")
    for qid in sorted({r["qid"] for r in rows}):
        qr = [r for r in rows if r["qid"] == qid]
        t = Counter(r["winner"] for r in qr)
        print(f"  {qid:<20} run1={t['run1']:>3} run2={t['run2']:>3} tie={t['equivalent']:>3} err={t['error']}")
    # persist per-candidate verdicts (keyed by normalized linkedin) so compare_report can
    # merge a `scoring_verdict` field into the main comparison workbook.
    vmap = {}
    for r in rows:
        li = re.sub(r"\s+", "", (r.get("linkedin") or "").strip().lower())
        if li:  # key by (query, candidate) so a person appearing in two queries doesn't collide
            vmap[f"{r['qid']}|{li}"] = {"winner": r["winner"], "confidence": r.get("confidence"),
                                        "key_differences": r.get("key_differences")}
    vpath = os.path.join(config.OUTPUT_DIR, f"compare_{args.run_id}_verdicts.json")
    json.dump(vmap, open(vpath, "w"), ensure_ascii=False)
    out = _workbook(rows, tally, n, args.run_id)
    print(f"\nWORKBOOK -> {out}\nverdicts -> {vpath}")


def _workbook(rows, tally, n, run_id):
    wb = Workbook()
    ws = wb.active
    ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False
    for c in range(1, 13):
        ws.column_dimensions[SR.get_column_letter(c)].width = 12
    ws.merge_cells("A1:L1")
    ws["A1"] = f"AI Sourcing scoring — Run 1 ({L1}) vs Run 2 ({L2}): which is better?"
    ws["A1"].font = Font(bold=True, size=14, color="FFFFFFFF"); ws["A1"].fill = SR._fill(SR.NAVY)
    r = 3
    r = SR._section(ws, r, f"Blind LLM-judge verdict over {n} matched candidates")
    win1 = tally["run1"] / n if n else 0
    win2 = tally["run2"] / n if n else 0
    SR._card(ws, r, 1, "RUN 1 BETTER", f"{tally['run1']} ({win1:.0%})", L1,
             SR.GREEN_T if win1 > win2 else SR.NAVY)
    SR._card(ws, r, 4, "RUN 2 BETTER", f"{tally['run2']} ({win2:.0%})", L2,
             SR.GREEN_T if win2 > win1 else SR.NAVY)
    SR._card(ws, r, 7, "EQUIVALENT", str(tally["equivalent"]), "no meaningful difference")
    SR._card(ws, r, 10, "JUDGE ERRORS", str(tally["error"]), "excluded from %",
             SR.RED_T if tally["error"] else SR.GREEN_T)
    r += 4
    verdict = (f"RUN 1 ({L1}) scores better" if win1 - win2 > 0.1 else
               f"RUN 2 ({L2}) scores better" if win2 - win1 > 0.1 else
               "No clear winner — roughly equivalent")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=12)
    ws.cell(row=r, column=1, value="Headline: " + verdict).font = Font(bold=True, size=12,
                                                                       color=SR.GREEN_T)
    ws2 = wb.create_sheet("Verdicts")
    cols = ["qid", "name", "linkedin", "run1_score", "run2_score", "winner", "confidence",
            "key_differences", "reasoning"]
    SR._header(ws2, cols, {"qid": 18, "name": 20, "linkedin": 30, "run1_score": 9, "run2_score": 9,
                           "winner": 11, "confidence": 10, "key_differences": 70, "reasoning": 80})
    for r_ in sorted(rows, key=lambda x: x["qid"]):
        SR._append(ws2, [r_.get(c) for c in cols])
        SR._verdict_fill(ws2[ws2.max_row][cols.index("winner")], ("run1",), ("run2",))
    for rc in ws2.iter_rows(min_row=2):
        for c in rc:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    out = os.path.join(config.OUTPUT_DIR, f"compare_judge_{run_id}.xlsx")
    wb.save(out)
    return out


if __name__ == "__main__":
    main()

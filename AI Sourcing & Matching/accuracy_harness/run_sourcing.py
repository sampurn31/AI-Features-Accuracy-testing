"""
Day-2 Global / AI Sourcing (db=2) candidate-scoring accuracy.

For each golden sourcing case:
  1. Call the CURRENT impl: POST {neptune}/v1/ai-sourcing-v3/search (aiSearch + matchingCriteria)
     -> scored, ranked candidates (matchScore / matchLabel / per-criterion matchAnalysis).
  2. DETERMINISTIC anchor (sourcing_checks): ranking monotonic, score<->satisfied sane,
     criteria coverage, analysis shape, label not contradictory.
  3. LLM JUDGE (Sonnet 5) on the top-K candidates: is the per-criterion reasoning grounded
     in the profile, are the `satisfied` flags correct, is matchScore sensible?
Results -> colour-coded output/sourcing_results_<ts>.xlsx (summary + raw_io).

The external corpus only returns candidates on PROD, so this runs on prod:
  # in your gitignored set_env.sh (NEVER committed) add the prod values, then:
  source set_env.sh
  python run_sourcing.py --limit 2      # tiny smoke first (spends ~1 credit/case)
  python run_sourcing.py                # full golden set

Required env for prod (set them yourself; the harness never stores them):
  ANTHROPIC_API_KEY, RCRM_ALBATROSS_URL=https://albatross.recruitcrm.io/v1,
  RCRM_NEPTUNE_URL=<prod neptune base .../v1>, RCRM_LOGIN_EMAIL, RCRM_LOGIN_PASSWORD
  (or RCRM_ALBATROSS_TOKEN to skip login).
"""
import argparse
import datetime
import glob
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import sourcing_client
import sourcing_checks
import llm
from run import _load_text, _resolve_token, WORKERS

# worst-first severity orderings for aggregating the per-candidate judge verdicts
_GROUND = {"hallucinated": 3, "partly_grounded": 2, "grounded": 1, "": 0}
_SATISF = {"wrong": 3, "partly": 2, "correct": 1, "": 0}
_SCORE = {"no": 3, "borderline": 2, "yes": 1, "": 0}


def _worst(verdicts, key, order):
    best = ""
    for v in verdicts:
        if order.get(v.get(key, ""), 0) > order.get(best, 0):
            best = v.get(key, "")
    return best


def _load_cases():
    cases = []
    for p in sorted(glob.glob(os.path.join(config.GOLDEN_SOURCING_DIR, "day2_queries.json"))):
        with open(p, encoding="utf-8") as f:
            cases.extend(json.load(f))
    return cases


def _process(idx, case, rubric, token):
    cid = case.get("id", f"case-{idx}")
    query = case["query"]
    criteria = case.get("criteria", [])
    base = {"id": cid, "database_type": config.DB_GLOBAL_DATABASE, "query": query,
            "criteria": json.dumps(criteria, ensure_ascii=False),
            "expectation": case.get("expectation", "")}
    try:
        status, result = sourcing_client.scored_search(
            ai_search=query, matching_criteria=criteria, page=1, token=token)
        cands = result.get("candidates", [])
        base["current_http"] = status
        base["n_candidates"] = len(cands)
        base["scoring_applied"] = result.get("scoring_applied")
        base["raw_metadata"] = json.dumps(result.get("metadata"), ensure_ascii=False)
        # compact view for the xlsx cell (openpyxl caps cells at 32767 chars); the FULL
        # candidates incl per-criterion reasoning + profile go to a side JSON file (see main()).
        compact = [{"idx": c.get("candidateIndex"), "matchScore": c.get("matchScore"),
                    "matchLabel": c.get("matchLabel"),
                    "analysis": [{"c": a.get("criterionText"), "w": a.get("weight"),
                                  "score": a.get("score")} for a in (c.get("matchAnalysis") or [])]}
                   for c in cands]
        base["raw_candidates"] = json.dumps(compact, ensure_ascii=False, indent=1)[:32000]
        base["_full"] = {"id": cid, "query": query, "criteria": criteria,
                         "metadata": result.get("metadata"), "candidates": cands}

        if status != 200 or result.get("error"):
            base.update({"det_pass": "error", "det_issues": (result.get("error") or "")[:500],
                         "judge_flag": "error", "grounding": "", "scores_justified": "",
                         "score_sensible": "", "judge_reasoning": "", "issues": (result.get("error") or "")[:300]})
            return idx, base
        if not cands:
            msg = result.get("message") or "no candidates returned (external corpus empty for this env/query)"
            base.update({"det_pass": "no_data", "det_issues": msg, "judge_flag": "no_data",
                         "grounding": "", "scores_justified": "", "score_sensible": "",
                         "judge_reasoning": "", "issues": msg})
            return idx, base

        # 2. deterministic anchor
        det_pass, det_issues = sourcing_checks.run_all(cands, criteria)

        # 3. judge the top-K ranked candidates
        verdicts, worst_reason = [], ""
        for c in cands[: config.SOURCING_JUDGE_TOP_K]:
            v = llm.judge_sourcing(rubric, query, criteria, c)
            verdicts.append(v)
        g, s, sc = (_worst(verdicts, "grounding", _GROUND),
                    _worst(verdicts, "scores_justified", _SATISF),
                    _worst(verdicts, "score_sensible", _SCORE))
        judge_bad = (_GROUND.get(g, 0) >= 2 or _SATISF.get(s, 0) >= 2 or _SCORE.get(sc, 0) >= 2)
        # surface the reasoning of the worst-judged candidate for the reviewer
        if verdicts:
            worst = max(verdicts, key=lambda v: max(_GROUND.get(v.get("grounding", ""), 0),
                                                    _SATISF.get(v.get("scores_justified", ""), 0),
                                                    _SCORE.get(v.get("score_sensible", ""), 0)))
            worst_reason = worst.get("reasoning", "")
        judge_issues = "; ".join(v.get("issues", "") for v in verdicts if v.get("issues"))

        base.update({
            "det_pass": "yes" if det_pass else "no",
            "det_issues": " | ".join(det_issues)[:600],
            "grounding": g, "scores_justified": s, "score_sensible": sc,
            "judge_flag": "yes" if judge_bad else "no",
            "judge_reasoning": worst_reason,
            "issues": (" | ".join(det_issues)[:300] + (" || JUDGE: " + judge_issues[:300] if judge_issues else "")),
        })
    except Exception as e:  # noqa: BLE001 — keep the batch alive
        base.update({"current_http": "ERROR", "n_candidates": 0, "scoring_applied": "",
                     "det_pass": "error", "det_issues": f"{type(e).__name__}: {e}",
                     "judge_flag": "error", "grounding": "", "scores_justified": "",
                     "score_sensible": "", "judge_reasoning": "", "issues": f"{type(e).__name__}: {e}",
                     "raw_metadata": "", "raw_candidates": ""})
    return idx, base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="run only the first N cases (smoke)")
    args = ap.parse_args()

    config.require_runtime_env()
    rubric = _load_text(config.SOURCING_JUDGE_RUBRIC, "sourcing judge rubric")
    cases = _load_cases()
    if not cases:
        raise SystemExit(f"No golden sourcing cases in {config.GOLDEN_SOURCING_DIR}")
    if args.limit:
        cases = cases[: args.limit]

    token = _resolve_token()
    print(f"Neptune: {config.RCRM_NEPTUNE_URL}/{config.SOURCING_SEARCH_PATH}")
    print(f"Running {len(cases)} sourcing cases (judging top {config.SOURCING_JUDGE_TOP_K}), "
          f"{WORKERS} workers ...\n")

    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_process, i, c, rubric, token) for i, c in enumerate(cases, 1)]
        for fut in as_completed(futs):
            i, row = fut.result()
            results[i] = row
            done += 1
            print(f"[{done}/{len(cases)}] {row['id']:<26} http={str(row.get('current_http')):<5} "
                  f"n={row.get('n_candidates'):<3} det={row.get('det_pass'):<6} "
                  f"judge(g/s/sc)={row.get('grounding','')}/{row.get('scores_justified','')}/{row.get('score_sensible','')}")

    rows = [results[i] for i in sorted(results)]
    print("\nhttp     :", dict(Counter(str(r.get('current_http')) for r in rows)))
    print("det_pass :", dict(Counter(str(r.get('det_pass')) for r in rows)))
    print("judge_flag:", dict(Counter(str(r.get('judge_flag')) for r in rows)))
    # full raw (per-criterion reasoning + candidate profiles) -> side JSON, so nothing is
    # lost to openpyxl's 32k-per-cell cap; the xlsx keeps a compact view + the summary.
    full = [f for f in (r.pop("_full", None) for r in rows) if f]
    if full:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        raw_path = os.path.join(config.OUTPUT_DIR,
                                f"sourcing_raw_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        with open(raw_path, "w", encoding="utf-8") as fh:
            json.dump(full, fh, ensure_ascii=False, indent=2)
        print(f"Full raw candidates -> {raw_path}")
    if rows:
        _write_sourcing_xlsx(rows)


# ── colour-coded xlsx: summary sheet + raw_io sheet ──────────────────────────

def _write_sourcing_xlsx(rows, prefix="sourcing_results"):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(config.OUTPUT_DIR, f"{prefix}_{stamp}.xlsx")
    green, red, amber, grey = "C6EFCE", "FFC7CE", "FFEB9C", "D9D9D9"

    def fill(hexc):
        return PatternFill("solid", fgColor=hexc)

    def paint(cell, ok):
        cell.fill = fill(green if ok is True else red if ok is False else amber)

    wb = Workbook()
    ws = wb.active
    ws.title = "summary"
    cols = ["id", "current_http", "n_candidates", "scoring_applied", "det_pass",
            "grounding", "scores_justified", "score_sensible", "judge_flag",
            "det_issues", "issues", "judge_reasoning", "query", "criteria", "expectation"]
    widths = {"id": 24, "current_http": 8, "n_candidates": 6, "scoring_applied": 9, "det_pass": 8,
              "grounding": 15, "scores_justified": 15, "score_sensible": 13, "judge_flag": 9,
              "det_issues": 50, "issues": 50, "judge_reasoning": 70, "query": 40, "criteria": 40,
              "expectation": 44}
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c, "") for c in cols])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFFFF")
        c.fill = fill("305496")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=len(cols)).column_letter}1"
    ci = {c: i for i, c in enumerate(cols)}
    for rc in ws.iter_rows(min_row=2):
        for c in rc:
            c.alignment = Alignment(vertical="top", wrap_text=True)
        paint(rc[ci["current_http"]], str(rc[ci["current_http"]].value) == "200")
        dp = str(rc[ci["det_pass"]].value)
        paint(rc[ci["det_pass"]], True if dp == "yes" else False if dp == "no" else None)
        jf = str(rc[ci["judge_flag"]].value)
        paint(rc[ci["judge_flag"]], True if jf == "no" else False if jf == "yes" else None)
        for col in ("grounding", "scores_justified", "score_sensible"):
            v = str(rc[ci[col]].value)
            good = v in ("grounded", "correct", "yes")
            bad = v in ("hallucinated", "wrong", "no")
            if v:
                paint(rc[ci[col]], True if good else False if bad else None)
    for name, w in widths.items():
        ws.column_dimensions[ws.cell(row=1, column=ci[name] + 1).column_letter].width = w

    ws2 = wb.create_sheet("raw_io")
    rcols = ["id", "current_http", "raw_metadata", "raw_candidates"]
    ws2.append(rcols)
    for r in rows:
        ws2.append([r.get(c, "") for c in rcols])
    for c in ws2[1]:
        c.font = Font(bold=True, color="FFFFFFFF")
        c.fill = fill("305496")
    ws2.freeze_panes = "A2"
    for rc in ws2.iter_rows(min_row=2):
        for c in rc:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    for name, w in {"id": 24, "current_http": 8, "raw_metadata": 60, "raw_candidates": 120}.items():
        ws2.column_dimensions[ws2.cell(row=1, column=rcols.index(name) + 1).column_letter].width = w

    wb.save(out)
    print(f"\nWrote {len(rows)} rows -> {out}  (sheets: summary, raw_io)")


if __name__ == "__main__":
    main()

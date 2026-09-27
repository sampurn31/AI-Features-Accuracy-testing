"""
PW-6818 Day-2 SCALE run — AI Sourcing V3 (Global) candidate + scoring accuracy on PROD.

Per variation (300 realistic cases in golden/sourcing/scale_cases.json):
  pages 1-4 of POST /v1/ai-sourcing-v3/search  (~100 candidates)  ->
  deterministic checks on EVERY candidate (score formula, ranking, pagination dedup,
  coverage, shape, label, metadata + cohort contracts)                ->
  retrieval-relevance heuristics vs the parsed queryUsed              ->
  LLM judge (Sonnet 5) on sampled ranks (first / middle / last).

Safety: workers hard-capped at 10; page-1 timeout 300s; one retry per page;
crash-safe checkpoint (JSONL) — re-running the same command resumes, skipping
completed variations. ~1 credit per page => full run ≈ 1,200 credits.

Run (prod):
  source set_env.sh && source set_prod_urls.sh
  python3 run_sourcing_scale.py --limit 12 --yes     # pilot (~5 min) -> prints full-run ETA
  python3 run_sourcing_scale.py --yes                # full 300 (resumes past the pilot 12)

Output: ONE workbook output/sourcing_scale_<runid>.xlsx (Dashboard / Variations /
Failures / Judge Samples / Candidates / Cases) + raw audit JSONL (the checkpoint).
  --rebuild  rebuilds the workbook from the checkpoint without any network calls.
  --no-judge skips LLM judging (deterministic-only).
"""
import argparse
import datetime
import json
import os
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import sourcing_client
import sourcing_checks
import llm
from run import _load_text, _resolve_token

WORKERS = min(10, int(os.environ.get("SCALE_WORKERS", "10")))  # hard cap 10 (prod)
PAGES = int(os.environ.get("SCALE_PAGES", "4"))
JUDGE_K = int(os.environ.get("SCALE_JUDGE_K", "3"))
PAGE1_TIMEOUT = int(os.environ.get("SCALE_PAGE1_TIMEOUT", "300"))
PAGE_TIMEOUT = int(os.environ.get("SCALE_PAGE_TIMEOUT", "150"))

_ckpt_lock = threading.Lock()


def _fetch_pool(case, token, keep_history=False):
    """Fetch pages 1..PAGES. A scored run's pages >1 are served from a cache keyed by a
    search-history row, so we create one first (3.7), pass its id as historyId on every
    page, and delete it afterwards (3.4) so the prod account stays clean.
    Returns (pages_meta, candidates, error_str, page1_seconds, history_info)."""
    pages, cands, err, p1s = [], [], None, None
    crit = case.get("criteria") or None
    h_status, hid, h_err = sourcing_client.create_history(case["query"], token)
    hist = {"create_http": h_status, "id": hid, "error": h_err, "deleted": None}
    try:
        for p in range(1, PAGES + 1):
            status, res = None, None
            for attempt in (1, 2):
                t0 = time.time()
                status, res = sourcing_client.scored_search(
                    ai_search=case["query"], matching_criteria=crit, page=p, token=token,
                    timeout=(PAGE1_TIMEOUT if p == 1 else PAGE_TIMEOUT), history_id=hid)
                # retry only transient failures — a 4xx is a contract answer, not a blip
                if (status == 200 and not res.get("error")) or \
                   (isinstance(status, int) and 400 <= status < 500):
                    break
                if attempt == 1:
                    time.sleep(3)
            if p == 1:
                p1s = round(time.time() - t0, 1)
            n = len(res.get("candidates", []))
            pages.append({"page": p, "http": status, "n_candidates": n,
                          "metadata": res.get("metadata"), "error": res.get("error"),
                          "message": res.get("message")})
            if status != 200 or res.get("error"):
                err = f"page {p}: HTTP {status} {str(res.get('error'))[:200]}"
                break
            for c in res["candidates"]:
                c["_page"] = p
                cands.append(c)
            meta = res.get("metadata") or {}
            if n == 0 or meta.get("hasNextPage") is False:
                break
    finally:
        # by default we delete the history row so a 300-case run leaves no clutter; with
        # --keep-history we KEEP it so the search is inspectable in the UI afterwards.
        if hid is not None and not keep_history:
            hist["deleted"] = sourcing_client.delete_history(hid, token)
        elif hid is not None:
            hist["deleted"] = "kept-for-UI"
    for rank, c in enumerate(cands, 1):
        c["_rank"] = rank
    return pages, cands, err, p1s, hist


_RV = {"wrong": 3, "partly": 2, "correct": 1, "": 0}


def _judge_samples(case, cands, rubric):
    """PER-CRITERION audit of first / middle / last ranked candidates (scored cohorts).
    Each judged candidate carries `crit_verdicts` (one per matchAnalysis row, with the
    system's score+reasoning attached) plus roll-ups used by the sheets."""
    if case.get("cohort") == "none" or not cands:
        return []
    n = len(cands)
    picks = sorted({1, (n + 1) // 2, n})
    out = []
    for r in picks[:JUDGE_K]:
        c = cands[r - 1]
        v = llm.judge_candidate_criteria(rubric, case["query"], case.get("criteria") or [], c)
        rec = {"rank": r, "page": c.get("_page"), "cand_id": c.get("id"),
               "name": f"{c.get('firstname') or ''} {c.get('lastname') or ''}".strip(),
               "position": c.get("position"), "company": c.get("company"),
               "matchScore": c.get("matchScore")}
        if v.get("_error"):
            rec.update({"error": v["_error"], "crit_verdicts": [], "reasoning_worst": "",
                        "score_agree_rate": None, "avg_gap": None, "overall_sensible": "",
                        "summary": f"judge error: {v['_error']}"})
            out.append(rec)
            continue
        sys_rows = c.get("matchAnalysis") or []
        cvs = []
        for k, cv in enumerate(v.get("criteria") or []):
            sysr = sys_rows[k] if k < len(sys_rows) else {}
            if isinstance(cv.get("judge_score"), (int, float)):     # schema can't bound ints
                cv["judge_score"] = max(0, min(100, int(cv["judge_score"])))
            gap = (abs(cv["judge_score"] - sysr.get("score"))
                   if isinstance(sysr.get("score"), (int, float))
                   and isinstance(cv.get("judge_score"), (int, float)) else None)
            cvs.append({"criterion": sysr.get("criterionText") or cv.get("criterion"),
                        "weight": sysr.get("weight"), "ai_score": sysr.get("score"),
                        "ai_reasoning": sysr.get("reasoning"),
                        "judge_score": cv.get("judge_score"), "gap": gap,
                        "score_verdict": cv.get("score_verdict"),
                        "reasoning_verdict": cv.get("reasoning_verdict"),
                        "judge_note": cv.get("reasoning_note")})
        worst = ""
        for cv in cvs:
            if _RV.get(cv["reasoning_verdict"], 0) > _RV.get(worst, 0):
                worst = cv["reasoning_verdict"]
        agree = [cv for cv in cvs if cv["score_verdict"] == "agree"]
        gaps = [cv["gap"] for cv in cvs if cv["gap"] is not None]
        rec.update({"crit_verdicts": cvs, "reasoning_worst": worst,
                    "score_agree_rate": (len(agree) / len(cvs)) if cvs else None,
                    "avg_gap": (sum(gaps) / len(gaps)) if gaps else None,
                    "overall_sensible": v.get("overall_sensible", ""),
                    "summary": v.get("summary", "")})
        out.append(rec)
    return out


def _process(case, token, rubric, do_judge, keep_history=False):
    t0 = time.time()
    row = {"id": case["id"], "cohort": case["cohort"], "format": case["format"],
           "family": case["family"], "role": case["role"], "seniority": case.get("seniority"),
           "location": case.get("location"), "n_criteria": len(case.get("criteria") or []),
           "criteria": case.get("criteria") or [], "query_chars": len(case["query"]),
           "query": case["query"], "company_led": case.get("company_led", False),
           "status": "ok", "error": None}
    try:
        # input-cap contract cohort: >1000-char input must be REJECTED with the documented 400
        if case.get("expect") == "cap_400":
            status, res = sourcing_client.scored_search(
                ai_search=case["query"], matching_criteria=case.get("criteria") or None,
                page=1, token=token, timeout=PAGE1_TIMEOUT)
            row.update({"pages": [{"page": 1, "http": status, "n_candidates": 0,
                                   "metadata": res.get("metadata"), "error": res.get("error")}],
                        "page1_s": None, "n_candidates": 0, "totalResults": None,
                        "scoringApplied": None, "candidates": [], "judge": [],
                        "relevance": {"n": 0}, "score_top": None, "score_p50": None,
                        "score_low": None, "history": None})
            ok = status == 400 and "at most 1000 characters" in str(res.get("error") or "")
            row["fails"] = [] if ok else [{
                "check": "cap_contract",
                "detail": f"expected HTTP 400 'String should have at most 1000 characters' for "
                          f"{row['query_chars']}-char input, got HTTP {status}: "
                          f"{str(res.get('error'))[:200]}"}]
            row["elapsed_s"] = round(time.time() - t0, 1)
            return row

        pages, cands, err, p1s, hist = _fetch_pool(case, token, keep_history)
        row["pages"] = pages
        row["page1_s"] = p1s
        row["history"] = hist
        row["n_candidates"] = len(cands)
        meta1 = next((p.get("metadata") for p in pages if p.get("metadata")), {}) or {}
        row["totalResults"] = meta1.get("totalResults")
        row["scoringApplied"] = meta1.get("scoringApplied")
        if err:
            row["status"] = "partial" if cands else "error"
            row["error"] = err
        fails = sourcing_checks.run_pool_checks(pages, cands, case.get("criteria") or [], case["cohort"])
        # zero results on a realistic scored query = query-extraction over-constraint finding
        if not cands and row["status"] == "ok" and case["cohort"] != "none":
            try:
                q = json.loads(meta1.get("queryUsed") or "{}")
                hint = {k: q.get(k) for k in ("companies", "job_titles", "locations") if q.get(k)}
            except (TypeError, ValueError):
                hint = {}
            fails.append({"check": "zero_results",
                          "detail": "0 candidates for a realistic query — parsed filters likely "
                                    f"over-constrained: {json.dumps(hint, ensure_ascii=False)[:300]}"})
        row["fails"] = fails
        row["relevance"] = sourcing_checks.relevance_rates(cands, meta1)
        # per-candidate formula deviation for the Candidates sheet
        for c in cands:
            wa = sourcing_checks.weighted_avg(c.get("matchAnalysis") or [])
            c["_fdev"] = (round(abs(c["matchScore"] - wa), 1)
                          if wa is not None and c.get("matchScore") is not None else None)
        scores = [c.get("matchScore") for c in cands if c.get("matchScore") is not None]
        row["score_top"] = max(scores) if scores else None
        row["score_p50"] = int(statistics.median(scores)) if scores else None
        row["score_low"] = min(scores) if scores else None
        row["judge"] = _judge_samples(case, cands, rubric) if do_judge else []
        row["candidates"] = cands
    except Exception as e:  # noqa: BLE001 — keep the batch alive
        row.update({"status": "error", "error": f"{type(e).__name__}: {e}",
                    "pages": row.get("pages", []), "candidates": [], "fails": [],
                    "relevance": {"n": 0}, "judge": [], "n_candidates": 0, "page1_s": None,
                    "history": row.get("history")})
    row["elapsed_s"] = round(time.time() - t0, 1)
    return row


def _ckpt_path(run_id):
    return os.path.join(config.OUTPUT_DIR, f"scale_checkpoint_{run_id}.jsonl")


def _load_ckpt(path):
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        r = json.loads(line)
                        done[r["id"]] = r
                    except (ValueError, KeyError):
                        continue
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="run only the first N cases (pilot)")
    ap.add_argument("--cases", default=os.path.join(config.GOLDEN_SOURCING_DIR, "scale_cases.json"))
    ap.add_argument("--run-id", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--fresh", action="store_true", help="ignore existing checkpoint (start over)")
    ap.add_argument("--rebuild", action="store_true", help="rebuild xlsx from checkpoint, no network")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the prod-credits confirmation")
    ap.add_argument("--only", default="", help="run only the case with this exact id (UI verify)")
    ap.add_argument("--keep-history", action="store_true",
                    help="do NOT delete the search-history row, so the run is inspectable in the UI")
    args = ap.parse_args()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    ckpt = _ckpt_path(args.run_id)
    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            raise SystemExit(f"--only: no case with id {args.only!r} in {args.cases}")
    if args.limit:
        cases = cases[: args.limit]

    done = {} if args.fresh else _load_ckpt(ckpt)
    if args.rebuild:
        if not done:
            raise SystemExit(f"No checkpoint rows at {ckpt}")
        import scale_report
        out = scale_report.build(list(done.values()), args.run_id)
        print(f"Rebuilt workbook -> {out}")
        return

    config.require_runtime_env()
    rubric = None if args.no_judge else _load_text(config.SOURCING_CRITERION_RUBRIC,
                                                   "per-criterion judge rubric")
    todo = [c for c in cases if c["id"] not in done or done[c["id"]].get("status") == "error"]
    est_req = sum(min(PAGES, 4) for _ in todo)
    print(f"PROD scale run  | neptune: {config.RCRM_NEPTUNE_URL}/{config.SOURCING_SEARCH_PATH}")
    print(f"cases: {len(cases)} total, {len(cases)-len(todo)} already done (checkpoint), {len(todo)} to run")
    print(f"workers: {WORKERS} (hard cap 10)  | pages/case: {PAGES}  | judge/case: {0 if args.no_judge else JUDGE_K}")
    print(f"estimated requests this run: up to {est_req}  (~1 credit each)")
    if not args.yes:
        if input("Proceed against PROD? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("aborted")
    if not todo:
        print("Nothing to run — building workbook from checkpoint.")
        import scale_report
        print(f"Workbook -> {scale_report.build(list(done.values()), args.run_id)}")
        return

    token = _resolve_token()
    t_start = time.time()
    results = dict(done)
    finished = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_process, c, token, rubric, not args.no_judge, args.keep_history): c["id"]
                for c in todo}
        for fut in as_completed(futs):
            row = fut.result()
            results[row["id"]] = row
            with _ckpt_lock:
                with open(ckpt, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            finished += 1
            # completions arrive at rate finished/wall (worker overlap already baked in),
            # so remaining wall-time ≈ remaining * (wall / finished)
            avg = (time.time() - t_start) / finished
            eta_min = (len(todo) - finished) * avg / 60
            jw = "-"
            for j in row.get("judge") or []:
                if _RV.get(j.get("reasoning_worst", ""), 0) > _RV.get(jw if jw != "-" else "", 0):
                    jw = j.get("reasoning_worst") or "-"
            print(f"[{finished:>3}/{len(todo)}] {row['id']:<38} {row['status']:<7} "
                  f"{row.get('n_candidates', 0):>3}c fails={len(row.get('fails', [])):<3} "
                  f"reasoning={jw:<8} {row['elapsed_s']:>6.1f}s  ETA {eta_min:5.1f}m")

    rows = list(results.values())
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\ndone: {len(rows)} variations  ok={ok} partial="
          f"{sum(1 for r in rows if r['status'] == 'partial')} error="
          f"{sum(1 for r in rows if r['status'] == 'error')}")
    print("fail counts by check:", dict(Counter(f['check'] for r in rows for f in r.get('fails', []))))
    import scale_report
    out = scale_report.build(rows, args.run_id)
    print(f"\nWORKBOOK -> {out}")
    print(f"raw audit (checkpoint) -> {ckpt}")
    if args.limit and len(cases) < 300:
        per = (time.time() - t_start) / max(1, finished)   # wall seconds per completion
        print(f"pilot pace {per:.0f}s/completion (wall, {WORKERS} workers) "
              f"-> remaining {300 - len(rows)} ≈ {(300 - len(rows)) * per / 60:.0f} min")


if __name__ == "__main__":
    main()

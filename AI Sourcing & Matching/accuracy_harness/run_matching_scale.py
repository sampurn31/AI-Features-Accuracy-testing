"""
db=1 (My Database) AI Matching accuracy runner. Reads the seeded ledger, and per job runs the
full chain, evaluating the candidates that COME BACK for (1) scoring correctness and (2) relevance
— NOT retrieval. Candidates are joined to their ground-truth label by slug via the ledger.

Per job:
  1.1 get_configuration(jobSlug, db=1)         -> criteria[]{id,text,weight}, filters, questions
  1.2 criteria_to_filters(..., is_save=True)   -> persists config (precondition for the search)
  1.3 + poll 1.5 (aries wait_for_scores)       -> scored candidates (async)
  join ledger (slug -> bucket + authored profile), then:
    - deterministic + ground-truth checks (matching_checks.run_all)
    - axis 1: llm.judge_candidate_criteria (per-criterion scoring correctness)  [reused]
    - axis 2: llm.judge_candidate_relevance (per-candidate relevance)           [new]
Checkpoints per job to output/db1_matching_<run-id>.jsonl; builds one workbook via matching_report.

  source set_env.sh && source set_prod_urls.sh   # + RCRM_ACCOUNT_API_KEY/URLs (seed first)
  python3 prep_db1_dataset.py --run-id smoke1 --limit-jobs 1
  python3 run_matching_scale.py --run-id smoke1 --yes           # smoke (1 job in the ledger)
  python3 run_matching_scale.py --run-id smoke1 --rebuild       # rebuild workbook offline
"""
import argparse
import datetime
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import rcrm_client
import aries_matching_client as aries
import matching_checks
import llm
from run import _load_text, _resolve_token
from prep_db1_dataset import _load_ledger

WORKERS = min(4, int(os.environ.get("DB1_WORKERS", "3")))
_RV = {"wrong": 3, "partly": 2, "correct": 1, "": 0}


def _ledger_by_job(run_id):
    rows = _load_ledger(run_id)
    jobs, cands = {}, {}
    for r in rows:
        if r["type"] == "job":
            jobs[r["job_key"]] = r
        elif r["type"] == "candidate":
            cands.setdefault(r["job_key"], {})[r["slug"]] = r
    return jobs, cands


def _process(job_key, jobrec, ledger_cands, token, scoring_rubric, relevance_rubric, do_judge):
    t0 = time.time()
    job_slug, jd = jobrec["slug"], jobrec.get("jd_text", "")
    row = {"job_key": job_key, "job_slug": job_slug, "title": jobrec.get("title"),
           "status": "ok", "error": None, "jd_chars": len(jd)}
    try:
        s1, cfg = rcrm_client.get_configuration(job_slug=job_slug, jd_text=jd,
                                                database_type=config.DB_MY_DATABASE, token=token)
        criteria = (cfg or {}).get("criteria") or []
        row["n_criteria"] = len(criteria)
        if s1 != 200 or not criteria:
            row.update({"status": "error", "error": f"1.1 config http={s1} criteria={len(criteria)}"})
            row["elapsed_s"] = round(time.time() - t0, 1)
            return row
        s2, saved = rcrm_client.criteria_to_filters(
            job_slug=job_slug, jd_text=jd, database_type=config.DB_MY_DATABASE,
            criteria=criteria, filters=(cfg or {}).get("filters"),
            questions=(cfg or {}).get("questions"), is_save=True, token=token)
        row["persist_http"] = s2
        if s2 not in (200, 201):
            row.update({"status": "error", "error": f"1.2 persist http={s2}: {str(saved)[:200]}"})
            row["elapsed_s"] = round(time.time() - t0, 1)
            return row

        cands, count, polls, timed_out = aries.wait_for_scores(job_slug, token)
        row.update({"n_returned": len(cands), "polls": polls, "timed_out": timed_out,
                    "overallStatus": count.get("overallStatus"), "totalCount": count.get("totalCount")})

        gt = ledger_cands.get(job_key, {})
        ours = []
        for c in cands:
            rec = gt.get(c.get("slug"))
            if not rec:
                continue  # pre-existing / unlabeled account candidate — not ours, skip
            c["_gt_bucket"] = rec.get("bucket")
            prof = rec.get("profile") or {}
            for k, v in prof.items():
                if not c.get(k):
                    c[k] = v          # overlay authored profile as judge evidence
            ours.append(c)
        row["n_ours"] = len(ours)
        row["n_ours_scored"] = sum(1 for c in ours if isinstance(c.get("matchScore"), (int, float)))
        row["reasoning_status"] = dict(Counter(c.get("reasoningStatus") for c in ours))
        row["bucket_returned"] = dict(Counter(c.get("_gt_bucket") for c in ours))

        passed, hard_fails, exploratory = matching_checks.run_all(ours, criteria)
        row["fails"] = [{"check": k, "detail": d} for k, ds in hard_fails.items() for d in ds]
        row["exploratory"] = [{"check": k, "detail": d} for k, ds in exploratory.items() for d in ds]

        # scores summary
        scores = [c["matchScore"] for c in ours if isinstance(c.get("matchScore"), (int, float))]
        row["score_top"] = max(scores) if scores else None
        row["score_low"] = min(scores) if scores else None
        # band-envelope pass rate per candidate
        row["band_pass"] = sum(1 for c in ours if isinstance(c.get("matchScore"), (int, float))
                               and not matching_checks.band_envelope(c))
        row["candidates"] = ours

        row["judge_scoring"], row["judge_relevance"] = [], []
        if do_judge:
            topk = sorted([c for c in ours if isinstance(c.get("matchScore"), (int, float))],
                          key=lambda c: -c["matchScore"])[: config.DB1_JUDGE_TOP_K]
            # ensure at least one of each returned bucket gets judged (esp. IRRELEVANT/INFERRED)
            for c in ours:
                if len(topk) >= config.DB1_JUDGE_TOP_K * 2:
                    break
                if c not in topk and c.get("_gt_bucket") in ("IRRELEVANT", "INFERRED", "WEAK"):
                    topk.append(c)
            for c in topk:
                v = llm.judge_candidate_criteria(scoring_rubric, jd, criteria, c)
                row["judge_scoring"].append(_scoring_row(job_key, c, v))
                rv = llm.judge_candidate_relevance(relevance_rubric, jobrec.get("title"), jd, criteria, c)
                row["judge_relevance"].append(_relevance_row(job_key, c, rv))
    except Exception as e:  # noqa: BLE001
        row.update({"status": "error", "error": f"{type(e).__name__}: {e}",
                    "candidates": row.get("candidates", []), "fails": [], "exploratory": [],
                    "judge_scoring": [], "judge_relevance": []})
    row["elapsed_s"] = round(time.time() - t0, 1)
    return row


def _scoring_row(job_key, c, v):
    base = {"job_key": job_key, "slug": c.get("slug"), "gt_bucket": c.get("_gt_bucket"),
            "matchScore": c.get("matchScore"), "name": f"{c.get('firstname') or ''} {c.get('lastname') or ''}".strip()}
    if v.get("_error"):
        return {**base, "error": v["_error"], "crit_verdicts": [], "reasoning_worst": "", "overall_sensible": ""}
    sys_rows = c.get("matchAnalysis") or []
    cvs, worst = [], ""
    for k, cv in enumerate(v.get("criteria") or []):
        sysr = sys_rows[k] if k < len(sys_rows) else {}
        js = cv.get("judge_score")
        if isinstance(js, (int, float)):
            js = max(0, min(100, int(js)))
        gap = (abs(js - sysr.get("score")) if isinstance(sysr.get("score"), (int, float))
               and isinstance(js, (int, float)) else None)
        cvs.append({"criterion": sysr.get("criterionText") or cv.get("criterion"),
                    "weight": sysr.get("weight"), "ai_score": sysr.get("score"), "judge_score": js,
                    "gap": gap, "score_verdict": cv.get("score_verdict"),
                    "reasoning_verdict": cv.get("reasoning_verdict"),
                    "ai_reasoning": sysr.get("reasoning"), "judge_note": cv.get("reasoning_note")})
        if _RV.get(cv.get("reasoning_verdict", ""), 0) > _RV.get(worst, 0):
            worst = cv.get("reasoning_verdict", "")
    return {**base, "error": None, "crit_verdicts": cvs, "reasoning_worst": worst,
            "overall_sensible": v.get("overall_sensible", "")}


def _relevance_row(job_key, c, rv):
    base = {"job_key": job_key, "slug": c.get("slug"), "gt_bucket": c.get("_gt_bucket"),
            "matchScore": c.get("matchScore"),
            "name": f"{c.get('firstname') or ''} {c.get('lastname') or ''}".strip()}
    if rv.get("_error"):
        return {**base, "relevance_verdict": "", "error": rv["_error"], "reasoning": "", "expected": ""}
    expected = {"STRONG": "relevant", "PARTIAL": "relevant", "INFERRED": "relevant",
                "WEAK": "borderline", "IRRELEVANT": "irrelevant"}.get(c.get("_gt_bucket"), "")
    v = rv.get("relevance_verdict", "")
    return {**base, "relevance_verdict": v, "expected": expected,
            "agrees": (v == expected) if expected else None,
            "disqualifiers": "; ".join(rv.get("disqualifiers") or []),
            "reasoning": rv.get("reasoning", ""), "error": None}


def _ckpt(run_id):
    return os.path.join(config.OUTPUT_DIR, f"db1_matching_{run_id}.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit-jobs", type=int, default=0)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    ck = _ckpt(args.run_id)

    done = {}
    if not args.fresh and os.path.exists(ck):
        for line in open(ck, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                done[r["job_key"]] = r
    if args.rebuild:
        import matching_report
        jobs, _ = _ledger_by_job(args.run_id)
        out = matching_report.build(list(done.values()), args.run_id, jobs)
        print(f"Rebuilt -> {out}")
        return

    jobs, ledger_cands = _ledger_by_job(args.run_id)
    if not jobs:
        raise SystemExit(f"No seeded jobs in ledger for run-id {args.run_id} — run prep_db1_dataset first.")
    job_items = list(jobs.items())
    if args.limit_jobs:
        job_items = job_items[: args.limit_jobs]
    todo = [(k, j) for k, j in job_items if k not in done or done[k].get("status") == "error"]

    config.require_runtime_env()
    scoring_rubric = None if args.no_judge else _load_text(config.DB1_SCORING_RUBRIC, "db1 scoring rubric")
    relevance_rubric = None if args.no_judge else _load_text(config.DB1_RELEVANCE_RUBRIC, "db1 relevance rubric")
    print(f"Aries: {config.RCRM_ARIES_URL}/{config.MATCH_SEARCH_PATH}")
    print(f"jobs: {len(job_items)} ({len(job_items)-len(todo)} done) | workers {WORKERS} | "
          f"judge/job top {0 if args.no_judge else config.DB1_JUDGE_TOP_K}")
    if not args.yes:
        if input("Proceed (hits prod, spends match credits on the seeded account)? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("aborted")
    if not todo:
        import matching_report
        print(f"Nothing to run -> {matching_report.build(list(done.values()), args.run_id, jobs)}")
        return
    token = _resolve_token()
    results = dict(done)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_process, k, j, ledger_cands, token, scoring_rubric, relevance_rubric,
                          not args.no_judge): k for k, j in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results[r["job_key"]] = r
            with open(ck, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(todo)}] {r['job_key']:<24} {r['status']:<6} "
                  f"ours={r.get('n_ours',0)}/{r.get('n_returned',0)} scored={r.get('n_ours_scored',0)} "
                  f"band_pass={r.get('band_pass',0)} fails={len(r.get('fails',[]))} {r['elapsed_s']}s")
    rows = list(results.values())
    print("\nfail checks:", dict(Counter(f["check"] for r in rows for f in r.get("fails", []))))
    import matching_report
    out = matching_report.build(rows, args.run_id, jobs)
    print(f"WORKBOOK -> {out}")


if __name__ == "__main__":
    main()

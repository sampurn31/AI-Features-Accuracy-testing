"""
Head-to-head ENV comparison for AI Sourcing (Global / db=2): ai1 (perf changes) vs ai3 (baseline).
For the SAME query on each env it captures + diffs — NO LLM judge:

  - response time   : criteria-generation (config call) + search (page-1 + total)
  - generated criteria (from the config endpoint)
  - generated filter   (search metadata `queryUsed`)
  - candidate scoring  (matchScore + per-criterion score & reasoning)
  - candidate summary  (profile `description` — the now-shorter, query/criteria-aware summary)

Flow per query:
  POST /v2/ai-matching/configuration {databaseType:2, aiSearch}   (job-service, timed)  -> criteria + filters
  POST /v1/ai-sourcing-v3/search {aiSearch, matchingCriteria:<generated>}  (neptune, timed, pages 1..N)

Reuses the 2 test accounts; the queries are TWEAKED (role + country, incl. "... in us") so they
don't collide with the score cache from earlier runs. Capture each env once, then build the diff:

  RCRM_ENV=ai1 RCRM_LOGIN_EMAIL=<acct> RCRM_LOGIN_PASSWORD=<pw> python run_env_compare.py --capture --run-id envcmp1
  RCRM_ENV=ai3 RCRM_LOGIN_EMAIL=<acct> RCRM_LOGIN_PASSWORD=<pw> python run_env_compare.py --capture --run-id envcmp1
  python run_env_compare.py --compare --run-id envcmp1 --env-a ai1 --env-b ai3

Output: output/envcmp_<runid>_<env>.json (per env)  +  output/envcmp_<runid>.xlsx (head-to-head).
"""
import argparse
import json
import os
import re
import time

import config
import rcrm_auth
import rcrm_client
import sourcing_client as SC

PAGES = int(os.environ.get("COMPARE_PAGES", "2"))

# Tweaked simple queries (role + country, some "in us") — distinct from the earlier 5 so the
# reused accounts stay cache-clean. Edit freely.
QUERIES = [
    ("software-india",        "software engineer in india"),
    ("data-engineer-india",   "data engineer in india"),
    ("data-analyst-india",    "data analyst in india"),
    ("backend-india",         "backend developer in india"),
    ("product-manager-india", "product manager in india"),
    ("sales-manager-india",   "sales manager in india"),
]

# Fallback criteria used when --fixed-criteria is set OR the config endpoint (job-service) is
# unreachable. Holding criteria constant across envs also removes criteria-generation as a
# confound for the SCORING/SUMMARY comparison.
FIXED_CRITERIA = {
    "software-india":        [{"text": "Software development experience", "weight": 8}, {"text": "Based in India", "weight": 6}],
    "data-engineer-india":   [{"text": "Data engineering experience (Spark/Airflow/Python)", "weight": 8}, {"text": "Based in India", "weight": 6}],
    "data-analyst-india":    [{"text": "Data analysis experience", "weight": 8}, {"text": "Based in India", "weight": 6}],
    "backend-india":         [{"text": "Backend development experience", "weight": 8}, {"text": "Based in India", "weight": 6}],
    "product-manager-india": [{"text": "Product management experience", "weight": 8}, {"text": "Based in India", "weight": 6}],
    "sales-manager-india":   [{"text": "Sales management experience", "weight": 8}, {"text": "Based in India", "weight": 6}],
}


def _key(c):
    li = re.sub(r"\s+", "", (c.get("linkedinUrl") or "").strip().lower())
    return li or ("id:" + str(c.get("id")))


def _token():
    return config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]


def _search_pages(ai, mc, token):
    """pages 1..PAGES with historyId. Returns (candidates, queryUsed, page1_ms, total_ms, totalResults)."""
    cands, query_used, total, p1_ms = [], None, None, None
    hid = None
    if PAGES > 1:
        _, hid, _ = SC.create_history(ai, token)
    t_all = time.perf_counter()
    for page in range(1, PAGES + 1):
        res = {}
        for attempt in range(3):                       # retry empty page (async-scoring race on fresh acct)
            t0 = time.perf_counter()
            st, res = SC.scored_search(ai_search=ai, matching_criteria=mc, page=page,
                                       token=token, history_id=hid, timeout=180)
            dt = (time.perf_counter() - t0) * 1000
            if page == 1 and p1_ms is None:
                p1_ms = dt
            meta = res.get("metadata") or {}
            if (res.get("candidates") or []) or meta.get("hasNextPage") is False or st != 200:
                break
            time.sleep(5)
        meta = res.get("metadata") or {}
        if query_used is None and meta.get("queryUsed"):
            query_used = meta.get("queryUsed")
            total = meta.get("totalResults")
        pc = res.get("candidates") or []
        for c in pc:
            c["_page"] = page
            cands.append(c)
        if not pc or meta.get("hasNextPage") is False:
            break
    total_ms = (time.perf_counter() - t_all) * 1000
    if hid is not None:
        SC.delete_history(hid, token)
    return cands, query_used, p1_ms, total_ms, total


def capture(run_id, limit, fixed, label=None):
    token = _token()
    env = config.RCRM_ENV
    slot = label or env                                 # filename slot: lets 2 runs on the SAME env
    queries = QUERIES[:limit] if limit else QUERIES      # (e.g. new-code vs baseline-code) coexist
    print(f"CAPTURE env={env}  acct={config.LOGIN_EMAIL}  neptune={config.RCRM_NEPTUNE_URL}")
    print(f"        jobsvc={config.RCRM_JOB_SERVICE_URL} | {len(queries)} queries, {PAGES} pages"
          f"{'  [FIXED criteria — config skipped]' if fixed else ''}\n")
    out = {}
    for qid, ai in queries:
        cfg_ms, cfg_st, cfg_state, gen_criteria, gen_filters, src = None, None, None, [], {}, "config"
        if fixed:
            src = "fixed"
        else:
            t0 = time.perf_counter()
            try:
                cfg_st, cfg = rcrm_client.get_configuration(ai_search=ai,
                                                            database_type=config.DB_GLOBAL_DATABASE,
                                                            token=token, timeout=60)
            except Exception as e:                          # job-service unreachable → fall back
                cfg_st, cfg = "ERROR", {"_err": str(e)[:120]}
            cfg_ms = round((time.perf_counter() - t0) * 1000)
            cfg = cfg or {}
            gen_criteria = cfg.get("criteria") or []
            gen_filters = cfg.get("filters") or {}
            cfg_state = cfg.get("status")
        mc = [{"text": c.get("text"), "weight": c.get("weight")} for c in gen_criteria if c.get("text")]
        if not mc:                                          # config down/empty → fixed fallback so scoring still runs
            mc = FIXED_CRITERIA.get(qid, [{"text": "Relevant professional experience", "weight": 8}])
            src = "fixed"
        cands, qu, p1, tot_ms, total = _search_pages(ai, mc, token)
        out[qid] = {"aiSearch": ai, "config_status": cfg_st, "config_state": cfg_state,
                    "config_ms": cfg_ms, "criteria_source": src, "criteria_used": mc,
                    "gen_criteria": gen_criteria, "gen_filters": gen_filters,
                    "search_p1_ms": round(p1) if p1 else None, "search_total_ms": round(tot_ms),
                    "queryUsed": qu, "totalResults": total, "candidates": cands}
        print(f"  {qid:<22} cfg={str(cfg_ms)+'ms' if cfg_ms else '-':>7} crit[{src}]={len(mc)} "
              f"p1={round(p1) if p1 else '-':>5}ms cands={len(cands):>3} total={total}")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(config.OUTPUT_DIR, f"envcmp_{run_id}_{slot}.json")
    json.dump({"env": env, "slot": slot, "account": config.LOGIN_EMAIL, "run_id": run_id,
               "pages": PAGES, "queries": out}, open(path, "w"), ensure_ascii=False)
    print(f"\nsnapshot ({slot}) -> {path}")
    print(f"When both slots captured:  python run_env_compare.py --compare --run-id {run_id} --env-a <slotA> --env-b <slotB>")


# ── report ────────────────────────────────────────────────────────────────
def _filt(qu):
    try:
        d = json.loads(qu or "{}")
    except (ValueError, TypeError):
        d = {}
    sk = d.get("skills") or []
    if isinstance(sk, dict):
        sk = (sk.get("must_have") or []) + (sk.get("good_to_have") or [])
    return {"titles": d.get("job_titles") or [], "skills": sk, "locs": d.get("locations") or []}


def _crit_str(cr):
    return " | ".join(f"{c.get('text')} (w{c.get('weight')})" for c in (cr or []))


def compare(run_id, ea, eb):
    import scale_report as SR
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    A = json.load(open(os.path.join(config.OUTPUT_DIR, f"envcmp_{run_id}_{ea}.json")))
    B = json.load(open(os.path.join(config.OUTPUT_DIR, f"envcmp_{run_id}_{eb}.json")))
    qa, qb = A["queries"], B["queries"]
    wrap = Alignment(vertical="top", wrap_text=True)
    wb = Workbook()

    # 1) TIMING (headline)
    ws = wb.active
    ws.title = "Timing"
    ws.merge_cells("A1:I1")
    ws["A1"] = f"AI Sourcing — response time: {ea} (changes) vs {eb} (baseline).  Lower is faster; Δ = {ea}−{eb}."
    ws["A1"].font = Font(bold=True, size=13, color="FFFFFFFF"); ws["A1"].fill = SR._fill(SR.NAVY)
    cols = ["query", f"{ea} config ms", f"{eb} config ms", "config Δ",
            f"{ea} search p1 ms", f"{eb} search p1 ms", "search p1 Δ",
            f"{ea} search total ms", f"{eb} search total ms"]
    SR._header(ws, cols, {"query": 22}, row=3)
    cΔ = sΔ = n = 0
    for qid in qa:
        a, b = qa[qid], qb.get(qid, {})
        ca, cb = a.get("config_ms"), b.get("config_ms")
        sa, sb = a.get("search_p1_ms"), b.get("search_p1_ms")
        cd = (ca - cb) if (ca is not None and cb is not None) else None
        sd = (sa - sb) if (sa is not None and sb is not None) else None
        SR._append(ws, [qid, ca, cb, cd, sa, sb, sd, a.get("search_total_ms"), b.get("search_total_ms")])
        if cd is not None:
            cΔ += cd; n += 1
        if sd is not None:
            sΔ += sd
        for ci in (4, 7):  # color the deltas: green if ea faster (negative)
            cell = ws.cell(row=ws.max_row, column=ci)
            if isinstance(cell.value, (int, float)):
                cell.fill = SR._fill(SR.GREEN_T if cell.value < 0 else SR.RED_T if cell.value > 0 else "FFFFFF")
    if n:
        SR._append(ws, ["AVG Δ", "", "", round(cΔ / n), "", "", round(sΔ / n), "", ""])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    # 2) GENERATED CRITERIA
    ws2 = wb.create_sheet("Generated Criteria")
    SR._header(ws2, ["query", f"{ea} criteria", f"{eb} criteria", "identical?"],
               {"query": 22, f"{ea} criteria": 70, f"{eb} criteria": 70, "identical?": 10})
    for qid in qa:
        cra, crb = _crit_str(qa[qid].get("gen_criteria")), _crit_str(qb.get(qid, {}).get("gen_criteria"))
        SR._append(ws2, [qid, cra, crb, "YES" if cra == crb else "NO"])
        SR._verdict_fill(ws2.cell(row=ws2.max_row, column=4), ("YES",), ("NO",))
    for r in ws2.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    # 3) GENERATED FILTER
    ws3 = wb.create_sheet("Generated Filter")
    SR._header(ws3, ["query", f"{ea} titles/skills/locs", f"{eb} titles/skills/locs", "identical?"],
               {"query": 22, f"{ea} titles/skills/locs": 60, f"{eb} titles/skills/locs": 60, "identical?": 10})
    for qid in qa:
        fa, fb = _filt(qa[qid].get("queryUsed")), _filt(qb.get(qid, {}).get("queryUsed"))
        sa = f"t:{', '.join(fa['titles'])}\ns:{', '.join(fa['skills'])}\nl:{', '.join(fa['locs'])}"
        sb = f"t:{', '.join(fb['titles'])}\ns:{', '.join(fb['skills'])}\nl:{', '.join(fb['locs'])}"
        SR._append(ws3, [qid, sa, sb, "YES" if sa == sb else "NO"])
        SR._verdict_fill(ws3.cell(row=ws3.max_row, column=4), ("YES",), ("NO",))
    for r in ws3.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    # match candidates per query for the candidate-level sheets
    matched = {}
    for qid in qa:
        da = {_key(c): c for c in qa[qid].get("candidates", [])}
        db = {_key(c): c for c in qb.get(qid, {}).get("candidates", [])}
        matched[qid] = [(k, da[k], db[k]) for k in da if k in db]

    # 4) TOTAL SCORING
    ws4 = wb.create_sheet("Total Scoring")
    SR._header(ws4, ["query", "candidate", "linkedin", f"{ea} score", f"{eb} score", "Δ"],
               {"query": 20, "candidate": 22, "linkedin": 34, f"{ea} score": 9, f"{eb} score": 9, "Δ": 7})
    for qid in qa:
        for k, ca, cb in matched[qid]:
            nm = f"{(ca.get('firstname') or '')} {(ca.get('lastname') or '')}".strip()
            s1, s2 = ca.get("matchScore"), cb.get("matchScore")
            d = (s1 - s2) if (s1 is not None and s2 is not None) else None
            SR._append(ws4, [qid, nm, ca.get("linkedinUrl"), s1, s2, d])

    # 5) CRITERION SCORING (per matched candidate × criterion, matched by criterion text)
    ws5 = wb.create_sheet("Criterion Scoring")
    SR._header(ws5, ["query", "candidate", "criterion", f"{ea} score", f"{eb} score", "Δ",
                     f"{ea} reasoning", f"{eb} reasoning"],
               {"query": 16, "candidate": 18, "criterion": 30, f"{ea} score": 8, f"{eb} score": 8,
                "Δ": 6, f"{ea} reasoning": 60, f"{eb} reasoning": 60})
    for qid in qa:
        for k, ca, cb in matched[qid]:
            nm = f"{(ca.get('firstname') or '')} {(ca.get('lastname') or '')}".strip()
            bb = {(x.get("criterionText") or ""): x for x in (cb.get("matchAnalysis") or [])}
            for x in (ca.get("matchAnalysis") or []):
                t = x.get("criterionText") or ""
                y = bb.get(t, {})
                s1, s2 = x.get("score"), y.get("score")
                d = (s1 - s2) if (s1 is not None and s2 is not None) else None
                SR._append(ws5, [qid, nm, t, s1, s2, d, x.get("reasoning"), y.get("reasoning")])
    for r in ws5.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    # 6) SUMMARY (candidate description — length + text side by side)
    ws6 = wb.create_sheet("Summary")
    SR._header(ws6, ["query", "candidate", f"{ea} len", f"{eb} len", "len Δ",
                     f"{ea} summary", f"{eb} summary"],
               {"query": 16, "candidate": 18, f"{ea} len": 7, f"{eb} len": 7, "len Δ": 7,
                f"{ea} summary": 70, f"{eb} summary": 70})
    for qid in qa:
        for k, ca, cb in matched[qid]:
            nm = f"{(ca.get('firstname') or '')} {(ca.get('lastname') or '')}".strip()
            da, db = ca.get("description") or "", cb.get("description") or ""
            SR._append(ws6, [qid, nm, len(da), len(db), len(da) - len(db), da, db])
    for r in ws6.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    total_matched = sum(len(v) for v in matched.values())
    out = os.path.join(config.OUTPUT_DIR, f"envcmp_{run_id}.xlsx")
    wb.save(out)
    print(f"matched candidates: {total_matched}  ({', '.join(f'{q}:{len(matched[q])}' for q in matched)})")
    print(f"WORKBOOK -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", action="store_true", help="capture current RCRM_ENV")
    ap.add_argument("--compare", action="store_true", help="build the head-to-head workbook")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fixed-criteria", action="store_true",
                    help="skip the config call; use FIXED_CRITERIA (for when job-svc is down / clean scoring compare)")
    ap.add_argument("--label", help="filename slot override (e.g. 'new' / 'base') so two runs on the SAME env coexist")
    ap.add_argument("--env-a", help="slot A (e.g. new / ai1)")
    ap.add_argument("--env-b", help="slot B (e.g. base / ai3)")
    args = ap.parse_args()
    if args.capture:
        capture(args.run_id, args.limit, args.fixed_criteria, args.label)
    elif args.compare:
        if not (args.env_a and args.env_b):
            raise SystemExit("--compare needs --env-a and --env-b")
        compare(args.run_id, args.env_a, args.env_b)
    else:
        raise SystemExit("give --capture or --compare")


if __name__ == "__main__":
    main()

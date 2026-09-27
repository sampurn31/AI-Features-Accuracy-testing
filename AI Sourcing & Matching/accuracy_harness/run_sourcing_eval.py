"""
AI Sourcing (Global / db=2) ACCURACY EVAL on PROD, judged by Sonnet 5.

For 5 real user queries: auto-generate criteria (product config endpoint; LLM fallback) → search
prod for the top ~100 candidates → for EACH candidate one Sonnet-5 call returns its own overall +
per-criterion scores, a verdict on whether OUR API's score is good enough, and whether the
candidate is RELEVANT to the query. One analytics workbook.

  source set_env.sh && source set_prod_urls.sh \
    && python run_sourcing_eval.py --run-id evalprod1 --yes
  # rebuild the workbook offline from the checkpoint:
  python run_sourcing_eval.py --run-id evalprod1 --rebuild

Output: output/sourcing_eval_<runid>.jsonl (checkpoint, resumable per query) +
        sourcing_eval_<runid>.xlsx (Queries / Candidate Scoring / Criterion Scoring / Relevance / Dashboard).
"""
import argparse
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import llm
import rcrm_auth
import rcrm_client
import sourcing_client as SC

PAGES = int(os.environ.get("EVAL_PAGES", "4"))          # ~100 candidates
WORKERS = int(os.environ.get("EVAL_WORKERS", "8"))      # concurrent Sonnet-5 judge calls
RUBRIC = os.path.join(config.PROMPTS_DIR, "sourcing_eval_rubric.md")

# The 5 selected user queries — LONG / detailed real prompts, spanning technical + non-technical
# domains (semiconductor, manufacturing-quality, clinical psychology, commercial banking, forensics).
QUERIES = [
    ("mfg-quality-leader",
     "Quality Systems and Continuous Improvement leader with 8+ years of experience in automotive or "
     "manufacturing environments, located in or willing to work onsite in Warsaw, IN. Strong "
     "background in QMS/QOS, ISO 9001, IATF 16949, APQP, PPAP, FMEA, SPC, MSA, audits, and quality "
     "system implementation. Experienced in leading teams, launching and integrating quality systems, "
     "and connecting QMS with PLM, MES, and ERP platforms. Must have ISO/IATF/VDA auditing experience."),
    # ── mainstream roles, broad/high-supply geo → results + good relevance (round-2 active set).
    ("finance-accountant-houston",
     "Accountants with current fixed asset experience located in Houston, Katy, Sugarland, Spring or "
     "the woodlands with 7+ years accounting experience, strong GAAP/ASC 360 knowledge, capital asset "
     "accounting, depreciation, disposals, CIP, asset tracking, controls, audit support, and advanced "
     "Excel. Accountants that have fully owned the fixed asset functions and can serve as a fixed "
     "asset subject matter expert."),
    ("sales-bd-bengaluru",
     "0-6 yrs of experience in enterprise sales, business development selling sales experience with "
     "enterprise mid to large size companies preferrably in the voice ai platform business taking "
     "care of end to end sales lifecycle with tier one pedigree in bengaluru"),
    ("embedded-sw-engineer",
     "Senior Embedded Systems Software Engineer with 5+ yrs building real-time control software for "
     "complex safety-critical systems. Strong C/C++ on real-time microcontrollers; hands-on with "
     "system-level control logic, state machines, and sequencing. Experience with CAN/ARINC comms "
     "protocols and MATLAB/Simulink model-based design, SIL/HIL testing. Aerospace, automotive, or "
     "industrial safety-critical background (DO-178C/ARP4754) a strong plus. Based in California."),
    ("healthcare-vp-periop",
     "Seeking a VP or Vice President of Perioperative Services or Surgical Services in New York, New "
     "Jersey, or Pennsylvania. Should have senior nursing/operational leadership over the OR, "
     "pre-op and PACU, with responsibility for surgical services strategy, staffing, budgets, "
     "throughput, and quality/safety in a hospital or health-system setting."),
]


def _token():
    return config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]


def _gen_criteria(query, token):
    """Product flow: config endpoint generates criteria from the query. LLM fallback if unavailable."""
    try:
        st, cfg = rcrm_client.get_configuration(ai_search=query, database_type=config.DB_GLOBAL_DATABASE,
                                                token=token, timeout=60)
        crit = [{"text": c.get("text"), "weight": c.get("weight")}
                for c in ((cfg or {}).get("criteria") or []) if c.get("text")]
        if crit:
            return crit, "config"
    except Exception:  # noqa: BLE001
        pass
    return llm.derive_criteria(query), "llm_fallback"


def _search_top(query, criteria, token):
    """pages 1..PAGES with historyId; retry an empty page (async-scoring race). Returns candidates."""
    mc = [{"text": c["text"], "weight": c["weight"]} for c in criteria]
    cands, hid = [], None
    _, hid, _ = SC.create_history(query, token)
    for page in range(1, PAGES + 1):
        res = {}
        for _ in range(3):
            st, res = SC.scored_search(ai_search=query, matching_criteria=mc, page=page,
                                       token=token, history_id=hid, timeout=240)
            meta = res.get("metadata") or {}
            if (res.get("candidates") or []) or meta.get("hasNextPage") is False or st != 200:
                break
            time.sleep(5)
        pc = res.get("candidates") or []
        for i, c in enumerate(pc):
            c["_page"] = page
            cands.append(c)
        if not pc or (res.get("metadata") or {}).get("hasNextPage") is False:
            break
    if hid is not None:
        SC.delete_history(hid, token)
    return cands


def _key(c):
    return (c.get("linkedinUrl") or "").strip().lower() or ("id:" + str(c.get("id")))


def run(run_id, limit_q, limit_c, do_judge, yes):
    if not os.path.exists(RUBRIC):
        raise SystemExit("missing rubric " + RUBRIC)
    if do_judge:
        config.require_runtime_env()                    # ANTHROPIC_API_KEY present
    rubric = open(RUBRIC, encoding="utf-8").read()
    token = _token()
    queries = QUERIES[:limit_q] if limit_q else QUERIES
    cp = os.path.join(config.OUTPUT_DIR, f"sourcing_eval_{run_id}.jsonl")
    done = set()                                         # queries already fully captured (resume)
    if os.path.exists(cp):
        for l in open(cp):
            d = json.loads(l)
            if d.get("kind") == "qdone":
                done.add(d["qid"])
    if not yes:
        print(f"About to hit PROD ({config.RCRM_NEPTUNE_URL}) as {config.LOGIN_EMAIL} for "
              f"{len(queries)} queries × ~{PAGES*25} candidates + Sonnet-5 judging. Re-run with --yes.")
        return
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    fh = open(cp, "a")
    print(f"EVAL on {config.RCRM_ENV}  acct={config.LOGIN_EMAIL}  judge-model={config.MODEL}  "
          f"{len(queries)} queries, {PAGES} pages, {WORKERS} workers\n")
    for qid, query in queries:
        if qid in done:
            print(f"  {qid:<26} already done — skipping"); continue
        crit, src = _gen_criteria(query, token)
        cands = _search_top(query, crit, token)
        if limit_c:
            cands = cands[:limit_c]
        print(f"  {qid:<26} criteria[{src}]={len(crit)}  candidates={len(cands)}  judging...")
        rows = []
        if do_judge:
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futs = {ex.submit(llm.judge_sourcing_eval, rubric, query, crit, c): c for c in cands}
                for fut in as_completed(futs):
                    c = futs[fut]; v = fut.result()
                    rows.append(_row(qid, query, src, crit, c, v))
        else:
            rows = [_row(qid, query, src, crit, c, None) for c in cands]
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"kind": "qdone", "qid": qid, "query": query, "criteria_source": src,
                             "criteria": crit, "n": len(rows)}, ensure_ascii=False) + "\n")
        fh.flush()
        ge = sum(1 for r in rows if (r.get("judge") or {}).get("api_overall_good_enough") == "yes")
        rel = sum(1 for r in rows if (r.get("judge") or {}).get("relevant") in ("yes", "partial"))
        print(f"    -> {len(rows)} judged | api good-enough {ge} | relevant(y/partial) {rel}")
    fh.close()
    print("\nWORKBOOK ->", build(run_id))


_PROF_KEYS = ("firstname", "lastname", "position", "company", "city", "state", "country",
              "keySkills", "workexpyr", "description", "education", "experience", "highlightTerms")


def _row(qid, query, src, crit, c, v):
    return {"kind": "cand", "qid": qid, "query": query, "criteria_source": src, "criteria": crit,
            "name": f"{(c.get('firstname') or '')} {(c.get('lastname') or '')}".strip(),
            "linkedin": c.get("linkedinUrl"), "page": c.get("_page"),
            "api_overall": c.get("matchScore"), "api_label": c.get("matchLabel"),
            "api_analysis": c.get("matchAnalysis") or [],
            "profile": {k: c.get(k) for k in _PROF_KEYS},   # stored so a model swap = re-judge, no re-search
            "judge": v}


def rejudge(run_id):
    """Re-judge every candidate in the checkpoint using the STORED profile + api scoring — NO
    prod re-search. The model is config.MODEL (set ACCURACY_MODEL, e.g. claude-opus-5). Only works
    on checkpoints that stored profiles; older ones must be re-run fresh."""
    config.require_runtime_env()
    rubric = open(RUBRIC, encoding="utf-8").read()
    cp = os.path.join(config.OUTPUT_DIR, f"sourcing_eval_{run_id}.jsonl")
    lines = [json.loads(l) for l in open(cp)]
    cands = [d for d in lines if d.get("kind") == "cand"]
    missing = [c for c in cands if not c.get("profile")]
    if missing:
        raise SystemExit(f"{len(missing)}/{len(cands)} rows have NO stored profile (checkpoint predates "
                         "profile-storing) — cannot re-judge without re-search. Do a fresh run instead.")
    print(f"RE-JUDGE {len(cands)} candidates with model={config.MODEL} ({WORKERS} workers) — no re-search\n")

    def _do(c):
        cand = {**(c.get("profile") or {}), "matchScore": c.get("api_overall"),
                "matchLabel": c.get("api_label"), "matchAnalysis": c.get("api_analysis")}
        return c, llm.judge_sourcing_eval(rubric, c.get("query"), c.get("criteria") or [], cand)
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_do, c) for c in cands]
        for fut in as_completed(futs):
            c, v = fut.result(); c["judge"] = v; done += 1
            if done % 50 == 0 or done == len(cands):
                print(f"  {done}/{len(cands)} re-judged")
    with open(cp, "w") as f:                              # rewrite checkpoint with new verdicts
        for d in lines:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print("\nWORKBOOK ->", build(run_id))


# ── report ──────────────────────────────────────────────────────────────────
def build(run_id):
    import scale_report as SR
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    cp = os.path.join(config.OUTPUT_DIR, f"sourcing_eval_{run_id}.jsonl")
    rows, meta = [], {}
    for l in open(cp):
        d = json.loads(l)
        if d.get("kind") == "cand":
            rows.append(d)
        elif d.get("kind") == "qdone":
            meta[d["qid"]] = d
    keep = {qid for qid, _ in QUERIES}                   # only the CURRENT query set (drops retired queries)
    active = {r["qid"] for r in rows} & keep             # ...that also returned candidates
    rows = [r for r in rows if r["qid"] in active]       # 0-result / retired queries excluded from the workbook
    wrap = Alignment(vertical="top", wrap_text=True)
    wb = Workbook()

    # Dashboard
    ws = wb.active; ws.title = "Dashboard"; ws.sheet_view.showGridLines = False
    for c in range(1, 13):
        ws.column_dimensions[SR.get_column_letter(c)].width = 12
    ws.merge_cells("A1:L1")
    ws["A1"] = "AI Sourcing accuracy on PROD — our API vs Sonnet 5 (score + relevance)"
    ws["A1"].font = Font(bold=True, size=14, color="FFFFFFFF"); ws["A1"].fill = SR._fill(SR.NAVY)
    judged = [r for r in rows if r.get("judge") and not r["judge"].get("_error")]
    n = len(judged)

    def _avg(f):
        xs = [f(r) for r in judged if f(r) is not None]
        return round(sum(xs) / len(xs), 1) if xs else 0
    good = sum(1 for r in judged if r["judge"].get("api_overall_good_enough") == "yes")
    bord = sum(1 for r in judged if r["judge"].get("api_overall_good_enough") == "borderline")
    rely = sum(1 for r in judged if r["judge"].get("relevant") == "yes")
    relp = sum(1 for r in judged if r["judge"].get("relevant") == "partial")
    r0 = 3
    r0 = SR._section(ws, r0, f"Overall — {n} candidates judged across {len(active)} queries (with results)")
    SR._card(ws, r0, 1, "API SCORE GOOD ENOUGH", f"{_pct(good, n)}", f"{good} yes · {bord} borderline",
             SR.GREEN_T if n and good / n >= 0.7 else SR.RED_T)
    SR._card(ws, r0, 4, "RELEVANT", f"{_pct(rely + relp, n)}", f"{rely} yes · {relp} partial",
             SR.GREEN_T if n and (rely + relp) / n >= 0.7 else SR.RED_T)
    SR._card(ws, r0, 7, "AVG API SCORE", str(_avg(lambda r: r.get("api_overall"))), "our matchScore")
    SR._card(ws, r0, 10, "AVG SONNET SCORE", str(_avg(lambda r: r["judge"].get("llm_overall_score"))),
             "independent")
    r0 += 4
    r0 = SR._section(ws, r0, "Per query")
    cols = ["query", "candidates", "avg API", "avg Sonnet", "avg |Δ|", "API good-enough %", "relevant %", "criteria src"]
    SR._header(ws, cols, {"query": 26}, row=r0); r0 += 1
    for qid in meta:
        if qid not in active:
            continue
        jr = [r for r in judged if r["qid"] == qid]
        m = len(jr) or 1
        aavg = round(sum(r.get("api_overall") or 0 for r in jr) / m, 1)
        lavg = round(sum(r["judge"].get("llm_overall_score") or 0 for r in jr) / m, 1)
        dabs = round(sum(abs((r.get("api_overall") or 0) - (r["judge"].get("llm_overall_score") or 0)) for r in jr) / m, 1)
        ge = sum(1 for r in jr if r["judge"].get("api_overall_good_enough") == "yes")
        rl = sum(1 for r in jr if r["judge"].get("relevant") in ("yes", "partial"))
        SR._append(ws, [qid, len(jr), aavg, lavg, dabs, _pct(ge, len(jr)), _pct(rl, len(jr)),
                        meta[qid].get("criteria_source")])

    # Queries sheet
    wq = wb.create_sheet("Queries")
    SR._header(wq, ["qid", "search query", "criteria source", "generated criteria", "candidates"],
               {"qid": 24, "search query": 60, "criteria source": 12, "generated criteria": 70, "candidates": 10})
    for qid, mrow in meta.items():
        if qid not in active:
            continue
        crit = " | ".join(f"{c.get('text')} (w{c.get('weight')})" for c in (mrow.get("criteria") or []))
        SR._append(wq, [qid, mrow.get("query"), mrow.get("criteria_source"), crit, mrow.get("n")])
    for r in wq.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    # Candidate Scoring
    wc = wb.create_sheet("Candidate Scoring")
    SR._header(wc, ["query", "candidate", "linkedin", "API score", "Sonnet score", "Δ (API-Sonnet)",
                    "API good enough?", "relevant?", "note"],
               {"query": 22, "candidate": 20, "linkedin": 30, "API score": 9, "Sonnet score": 11,
                "Δ (API-Sonnet)": 12, "API good enough?": 14, "relevant?": 10, "note": 60})
    for r in rows:
        j = r.get("judge") or {}
        if j.get("_error"):
            SR._append(wc, [r["qid"], r["name"], r["linkedin"], r.get("api_overall"), "ERR", "",
                            "", "", j["_error"][:80]]); continue
        d = (r.get("api_overall") or 0) - (j.get("llm_overall_score") or 0)
        SR._append(wc, [r["qid"], r["name"], r["linkedin"], r.get("api_overall"), j.get("llm_overall_score"),
                        d, j.get("api_overall_good_enough"), j.get("relevant"), j.get("api_overall_note")])
        SR._verdict_fill(wc.cell(row=wc.max_row, column=7), ("yes",), ("no",))
        SR._verdict_fill(wc.cell(row=wc.max_row, column=8), ("yes",), ("no",))
    for r in wc.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    # Criterion Scoring
    wcr = wb.create_sheet("Criterion Scoring")
    SR._header(wcr, ["query", "candidate", "criterion", "weight", "API score", "Sonnet score", "Δ",
                     "verdict on API", "API reasoning", "Sonnet reasoning"],
               {"query": 16, "candidate": 18, "criterion": 30, "weight": 6, "API score": 8,
                "Sonnet score": 9, "Δ": 6, "verdict on API": 13, "API reasoning": 55, "Sonnet reasoning": 55})
    for r in rows:
        j = r.get("judge") or {}
        if j.get("_error"):
            continue
        apib = {(a.get("criterionText") or ""): a for a in (r.get("api_analysis") or [])}
        for pc in (j.get("per_criterion") or []):
            t = pc.get("criterion") or ""
            a = apib.get(t) or _closest(apib, t)
            aps = a.get("score") if a else None
            d = (aps - pc.get("llm_score")) if (aps is not None and pc.get("llm_score") is not None) else None
            SR._append(wcr, [r["qid"], r["name"], t, a.get("weight") if a else None, aps, pc.get("llm_score"),
                             d, pc.get("api_verdict"), a.get("reasoning") if a else None, pc.get("reasoning")])
            SR._verdict_fill(wcr.cell(row=wcr.max_row, column=8), ("agree",), ("api_too_high", "api_too_low"))
    for r in wcr.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    # Relevance
    wr = wb.create_sheet("Relevance")
    SR._header(wr, ["query", "candidate", "relevant?", "reason", "API score", "Sonnet score", "API good enough?"],
               {"query": 22, "candidate": 20, "relevant?": 10, "reason": 70, "API score": 9,
                "Sonnet score": 11, "API good enough?": 14})
    for r in rows:
        j = r.get("judge") or {}
        if j.get("_error"):
            continue
        SR._append(wr, [r["qid"], r["name"], j.get("relevant"), j.get("relevance_reason"),
                        r.get("api_overall"), j.get("llm_overall_score"), j.get("api_overall_good_enough")])
        SR._verdict_fill(wr.cell(row=wr.max_row, column=3), ("yes",), ("no",))
    for r in wr.iter_rows(min_row=2):
        for c in r:
            c.alignment = wrap

    out = os.path.join(config.OUTPUT_DIR, f"sourcing_eval_{run_id}.xlsx")
    wb.save(out)
    return out


def _closest(apib, t):
    for k, v in apib.items():
        if k and (k in t or t in k):
            return v
    return None


def _pct(a, b):
    return f"{(100*a//b) if b else 0}% ({a}/{b})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit-queries", type=int, default=0)
    ap.add_argument("--limit-cands", type=int, default=0, help="cap candidates per query (default all ~100)")
    ap.add_argument("--no-judge", action="store_true", help="capture only, skip the judge")
    ap.add_argument("--rebuild", action="store_true", help="rebuild workbook from checkpoint offline")
    ap.add_argument("--rejudge", action="store_true",
                    help="re-judge stored candidates with config.MODEL (ACCURACY_MODEL) — NO prod re-search")
    ap.add_argument("--yes", action="store_true", help="confirm the prod run")
    args = ap.parse_args()
    if args.rebuild:
        print("WORKBOOK ->", build(args.run_id)); return
    if args.rejudge:
        rejudge(args.run_id); return
    run(args.run_id, args.limit_queries, args.limit_cands, not args.no_judge, args.yes)


if __name__ == "__main__":
    main()

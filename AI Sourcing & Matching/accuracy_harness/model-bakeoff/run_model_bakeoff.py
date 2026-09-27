"""
MODEL BAKE-OFF — can another LLM replace the env's candidate scorer?

The env's POST /v1/ai-sourcing-v3/search scores candidates with ONE model (today:
Gemini 2.5 Flash Lite). This script takes that env scoring as the BASELINE and, for the
SAME fixed query + the SAME retrieved candidates + the SAME weighted criteria, re-scores
every candidate with 4-6 challenger models — each its own provider/key/model — IN PARALLEL.
It then builds one workbook ranking every model against the env on:

  scoring agreement (overall + per-criterion) · label agreement · candidate-ranking correlation
  · response time (p50/p95/mean) · throughput (candidates/sec) · token cost · failure rate

…and asks Anthropic (Sonnet 5) to read those metrics + samples and recommend which model, if
any, is a viable drop-in replacement for the env scorer.

Robust by design: a model with a bad key, a bad response, or a mid-run failure is recorded as a
miss and the run continues for everyone else (requirement 4). Which models run is defined in the
clearly-marked SLOTS block at the top of challenger_llms.py (requirement 3).

Run:
  source set_env.sh && source set_prod_urls.sh          # env auth + prod URLs (env has candidates only on prod)
  export GEMINI_API_KEY=...  OPENAI_API_KEY=...  ...     # one per slot you enabled
  python3 run_model_bakeoff.py --list-models            # show slots + which keys resolved
  python3 run_model_bakeoff.py --query "software engineer in india" --run-id bake1 --limit 40 --yes
  python3 run_model_bakeoff.py --run-id bake1 --rebuild # rebuild workbook offline from the snapshot

Output: output/bakeoff_<runid>.json (raw) + output/bakeoff_<runid>.xlsx (the report).
"""
import argparse
import datetime
import json
import math
import os
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

import challenger_llms as CL
import config
import scale_report as SR
import sourcing_client as SC

WORKERS = int(os.environ.get("BAKEOFF_WORKERS", "8"))
LABEL_TOL = int(os.environ.get("BAKEOFF_SCORE_TOL", "10"))      # |Δoverall| within this = "agrees"
CRIT_TOL = int(os.environ.get("BAKEOFF_CRIT_TOL", "15"))        # |Δcriterion| within this = "agrees"
SCORING_PROMPT = os.path.join(config.PROMPTS_DIR, "model_scoring_prompt.txt")

# env model under test — purely a display label for the report.
ENV_MODEL_LABEL = os.environ.get("ENV_MODEL_LABEL", "env · Gemini 2.5 Flash Lite (POST /ai-sourcing-v3/search)")


# ── candidate helpers ─────────────────────────────────────────────────────────

def _key(c):
    li = (c.get("linkedinUrl") or "").strip().lower().replace(" ", "")
    return li or ("id:" + str(c.get("id")))


def _profile(c):
    """The objective evidence the models score against — same fields, every model."""
    exp = [{"title": e.get("title"), "company": e.get("company"),
            "duration": e.get("duration"), "description": (e.get("description") or "")[:300]}
           for e in (c.get("experience") or [])][:6]
    edu = [{"degree": e.get("degree"), "institution": e.get("institution")}
           for e in (c.get("education") or [])][:4]
    return {"position": c.get("position"), "company": c.get("company"),
            "location": ", ".join(x for x in (c.get("city"), c.get("state"), c.get("country")) if x),
            "keySkills": c.get("keySkills"), "workExperienceYears": c.get("workexpyr"),
            "summary": (c.get("description") or "")[:800], "experience": exp, "education": edu}


def _criteria_pairs(criteria):
    return [{"text": c.get("text") or c.get("criterionText"), "weight": c.get("weight")}
            for c in (criteria or []) if (c.get("text") or c.get("criterionText"))]


def _score_user(query, criteria, profile):
    lines = "\n".join(f"- {c['text']} (weight {c['weight']})" for c in criteria)
    return (f"SEARCH QUERY: {query}\n\nWEIGHTED CRITERIA:\n{lines}\n\n"
            f"CANDIDATE PROFILE:\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n\n"
            "Score this candidate on every criterion and return ONLY the JSON object.")


# ── baseline (env) capture ────────────────────────────────────────────────────

def capture_baseline(query, criteria, pages, limit):
    """Call the env scored search for the FIXED query; return (candidates, env_meta)."""
    token = config.RCRM_ALBATROSS_TOKEN
    if not token:
        import rcrm_auth
        token = rcrm_auth.login()["token"]

    cands, env_total_ms, query_used, total_results, err = [], 0.0, None, None, None
    hid = None
    if pages > 1:
        _, hid, _ = SC.create_history(query, token)
    t_all = time.perf_counter()
    for page in range(1, pages + 1):
        t0 = time.perf_counter()
        st, res = SC.scored_search(ai_search=query, matching_criteria=criteria, page=page,
                                   token=token, history_id=hid, timeout=300)
        env_total_ms += (time.perf_counter() - t0) * 1000
        meta = res.get("metadata") or {}
        if query_used is None:
            query_used, total_results = meta.get("queryUsed"), meta.get("totalResults")
        if res.get("error") and not res.get("candidates"):
            err = f"page {page}: {res['error'][:200]}"
        for c in (res.get("candidates") or []):
            c["_page"] = page
            cands.append(c)
        if not (res.get("candidates") or []) or meta.get("hasNextPage") is False:
            break
    if hid is not None:
        SC.delete_history(hid, token)
    if limit:
        cands = cands[:limit]
    env_meta = {"total_ms": round(env_total_ms), "n": len(cands), "queryUsed": query_used,
                "totalResults": total_results, "error": err,
                "per_candidate_ms": round(env_total_ms / len(cands), 1) if cands else None,
                "throughput_cps": round(len(cands) / (env_total_ms / 1000), 3) if env_total_ms else None}
    return cands, env_meta


# ── challenger execution ──────────────────────────────────────────────────────

def run_challengers(slots, query, criteria, candidates, system_prompt):
    """Score every candidate with every enabled slot IN PARALLEL. Returns
    results[slot_id][cand_key] = score-call dict, plus per-slot wall timing."""
    enabled = [s for s in slots if s["enabled"]]
    tasks = []
    for s in enabled:
        for c in candidates:
            tasks.append((s, c))
    results = {s["id"]: {} for s in enabled}
    starts = {s["id"]: [] for s in enabled}
    ends = {s["id"]: [] for s in enabled}
    done = 0

    def _one(slot, cand):
        user = _score_user(query, criteria, _profile(cand))
        t0 = time.perf_counter()
        r = CL.score(slot, system_prompt, user)
        return slot["id"], _key(cand), r, t0, time.perf_counter()

    print(f"scoring {len(candidates)} candidates × {len(enabled)} models = {len(tasks)} calls "
          f"({WORKERS} workers)…")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_one, s, c) for s, c in tasks]
        for fut in as_completed(futs):
            sid, ckey, r, t0, t1 = fut.result()
            results[sid][ckey] = r
            starts[sid].append(t0)
            ends[sid].append(t1)
            done += 1
            if done % 25 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)} calls done")
    walls = {sid: (max(ends[sid]) - min(starts[sid]) if ends[sid] else 0.0) for sid in results}
    return results, walls


# ── metrics vs env baseline ───────────────────────────────────────────────────

def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return round(num / (dx * dy), 3) if dx and dy else None


def _spearman(xs, ys):
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):                       # average ties
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    if len(xs) < 2:
        return None
    return _pearson(ranks(xs), ranks(ys))


def _crit_map(match_analysis):
    out = {}
    for a in (match_analysis or []):
        t = (a.get("criterionText") or "").strip().lower()
        if t and a.get("score") is not None:
            out[t] = a.get("score")
    return out


def compute_metrics(slot, per_cand, env_by_key, criteria, wall_s):
    """One model's numbers vs env. per_cand: {cand_key: score-call dict}."""
    n_total = len(env_by_key)
    ok_keys = [k for k, r in per_cand.items() if r.get("ok") and r.get("parsed")]
    n_ok = len(ok_keys)
    fails = [{"key": k, "http": r.get("http"), "attempts": r.get("attempts"),
              "error": r.get("error") or ("returned non-JSON" if r.get("ok") else "call failed")}
             for k, r in per_cand.items() if not (r.get("ok") and r.get("parsed"))]

    env_scores, mdl_scores, abs_err, signed = [], [], [], []
    label_hits = label_n = 0
    crit_abs, crit_agree, crit_n = [], 0, 0
    lat = []
    tin = tout = 0
    for k in ok_keys:
        r = per_cand[k]
        p = r["parsed"]
        env_c = env_by_key[k]
        ev, mv = env_c.get("matchScore"), p.get("matchScore")
        if r.get("ms") is not None:
            lat.append(r["ms"])
        tin += r.get("in_tok") or 0
        tout += r.get("out_tok") or 0
        if isinstance(ev, (int, float)) and isinstance(mv, (int, float)):
            env_scores.append(ev); mdl_scores.append(mv)
            abs_err.append(abs(mv - ev)); signed.append(mv - ev)
        el, ml = env_c.get("matchLabel"), p.get("matchLabel")
        if el and ml:
            label_n += 1
            if el.strip().lower() == ml.strip().lower():
                label_hits += 1
        em, mm = _crit_map(env_c.get("matchAnalysis")), _crit_map(p.get("matchAnalysis"))
        for t, es in em.items():
            if t in mm:
                d = abs(mm[t] - es); crit_abs.append(d); crit_n += 1
                if d <= CRIT_TOL:
                    crit_agree += 1

    def rate(a, b):
        return round(a / b, 3) if b else None
    within = sum(1 for e in abs_err if e <= LABEL_TOL)
    m = statistics.mean
    lat_sorted = sorted(lat)
    return {
        "id": slot["id"], "label": slot["label"], "provider": slot["provider"],
        "model": slot.get("model"),
        "n_total": n_total, "n_ok": n_ok, "n_fail": len(fails),
        "coverage": rate(n_ok, n_total), "failure_rate": rate(len(fails), n_total),
        "score_mae": round(m(abs_err), 2) if abs_err else None,
        "score_bias": round(m(signed), 2) if signed else None,       # +ve = model scores higher than env
        "score_within_tol": rate(within, len(abs_err)),
        "score_corr": _pearson(env_scores, mdl_scores),
        "rank_corr": _spearman(env_scores, mdl_scores),
        "label_agree": rate(label_hits, label_n),
        "crit_mae": round(m(crit_abs), 2) if crit_abs else None,
        "crit_agree": rate(crit_agree, crit_n), "crit_n": crit_n,
        "lat_p50": round(statistics.median(lat)) if lat else None,
        "lat_p95": round(lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))]) if lat else None,
        "lat_mean": round(m(lat)) if lat else None,
        "lat_max": round(max(lat)) if lat else None,
        "throughput_cps": round(n_ok / wall_s, 3) if wall_s else None,       # at the run's concurrency
        "throughput_seq_cps": round(1000 / m(lat), 3) if lat else None,      # one worker
        "tok_in": tin, "tok_out": tout,
        "tok_out_avg": round(tout / n_ok) if n_ok else None,
        "fails": fails,
    }


# ── Anthropic recommendation ──────────────────────────────────────────────────

RECO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "recommended_model": {"type": "string", "description": "slot id of the best drop-in replacement, or \"none\""},
        "viable_alternatives": {"type": "array", "items": {"type": "string"}},
        "not_viable": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
        "risks": {"type": "string"},
    },
    "required": ["recommended_model", "viable_alternatives", "not_viable", "reasoning", "risks"],
}


def judge_replacement(query, metrics, samples):
    """Ask Anthropic which model can replace the env scorer. Skipped (returns None
    with a note) if ANTHROPIC_API_KEY isn't set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"_skipped": "ANTHROPIC_API_KEY not set — numeric metrics only."}
    import llm
    cols = ["id", "label", "coverage", "failure_rate", "score_mae", "score_bias",
            "score_within_tol", "score_corr", "rank_corr", "label_agree", "crit_mae",
            "crit_agree", "lat_p50", "lat_p95", "throughput_cps", "tok_out_avg"]
    table = [{k: mm.get(k) for k in cols} for mm in metrics]
    system = (
        "You are choosing whether any candidate LLM can REPLACE the current production candidate-"
        "scorer of a recruiting search API. The production system (the 'env') scores candidates 0-100 "
        "per weighted criterion and overall. You are given, for the SAME query and SAME candidates, how "
        "each challenger model's scores compare to the env's.\n\n"
        "A viable replacement must (a) rarely fail (high coverage), (b) agree closely with the env on "
        "overall AND per-criterion scores (low MAE, high within-tolerance, positive correlation), (c) "
        "preserve candidate RANKING (high rank correlation — this matters most for a search product), "
        "(d) agree on labels, and (e) be at least competitive on latency/throughput. A large positive "
        "or negative score_bias means the model is systematically harsher/more generous than the env — "
        "note it. Recommend the single best drop-in, list any other viable ones, and name the models "
        "that are clearly NOT viable and why. Return JSON only."
    )
    user = (f"FIXED QUERY: {query}\n\nMETRICS (env is the baseline every model is compared to):\n"
            f"{json.dumps(table, indent=2)}\n\n"
            f"SAMPLE CANDIDATES (env score vs each model's score + per-criterion):\n"
            f"{json.dumps(samples, indent=2)[:9000]}\n\n"
            "Which model, if any, is the best drop-in replacement for the env scorer? Return JSON.")
    try:
        resp = llm._client.messages.create(
            model=config.MODEL, max_tokens=1600,
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": RECO_SCHEMA}},
            system=llm._system(system),
            messages=[{"role": "user", "content": user}])
        return json.loads(llm._text_of(resp))
    except Exception as e:                                       # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def _samples(env_by_key, results, metrics, k=6):
    """A few candidates with env vs every model, for the judge + the report."""
    keys = list(env_by_key)[:k]
    out = []
    for ck in keys:
        env_c = env_by_key[ck]
        row = {"candidate": f"{env_c.get('firstname') or ''} {env_c.get('lastname') or ''}".strip(),
               "position": env_c.get("position"), "env_score": env_c.get("matchScore"),
               "env_label": env_c.get("matchLabel"), "models": {}}
        for mm in metrics:
            r = (results.get(mm["id"]) or {}).get(ck) or {}
            p = r.get("parsed") or {}
            row["models"][mm["id"]] = {"score": p.get("matchScore"), "label": p.get("matchLabel"),
                                       "ok": bool(r.get("ok") and r.get("parsed")),
                                       "error": None if r.get("ok") else r.get("error")}
        out.append(row)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  REPORT
# ══════════════════════════════════════════════════════════════════════════════

def _mae_color(v, good=8, warn=15):
    if v is None:
        return SR.NAVY
    return SR.GREEN_T if v <= good else (SR.AMBER_T if v <= warn else SR.RED_T)


def _rate_color(v, good=0.9, warn=0.75):
    return SR._rate_color(v, good=good, warn=warn)


def build_report(snap, path):
    query = snap["query"]
    env = snap["env_meta"]
    metrics = snap["metrics"]
    reco = snap.get("recommendation") or {}
    run_id = snap["run_id"]
    wb = Workbook()

    # ── Dashboard ──
    ws = wb.active
    ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False
    for col in range(1, 15):
        ws.column_dimensions[SR.get_column_letter(col)].width = 12
    ws.merge_cells("A1:N1")
    ws["A1"] = "Model Bake-off — who can replace the env candidate scorer?"
    ws["A1"].font = Font(bold=True, size=16, color="FFFFFFFF")
    ws["A1"].fill = SR._fill(SR.NAVY)
    ws.row_dimensions[1].height = 30
    ws.merge_cells("A2:N2")
    ws["A2"] = (f"run {run_id} · {datetime.datetime.now():%Y-%m-%d %H:%M} · fixed query: “{query}” · "
                f"{env['n']} candidates · baseline = {ENV_MODEL_LABEL}")
    ws["A2"].font = Font(size=10, color="595959")

    # recommendation banner
    r = 4
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=14)
    rec_id = reco.get("recommended_model")
    if reco.get("_skipped") or reco.get("_error"):
        banner = "Anthropic recommendation: " + (reco.get("_skipped") or reco.get("_error"))
        color = SR.AMBER
    elif rec_id and rec_id != "none":
        rec_label = next((m["label"] for m in metrics if m["id"] == rec_id), rec_id)
        banner = f"★ Recommended replacement: {rec_label}   ({rec_id})"
        color = SR.GREEN
    else:
        banner = "✗ No model is a safe drop-in replacement for the env scorer (see reasoning below)."
        color = SR.RED
    c = ws.cell(row=r, column=1, value=banner)
    c.font = Font(bold=True, size=13, color="FFFFFF" if color != SR.AMBER else "000000")
    c.fill = SR._fill(color if color in (SR.GREEN, SR.RED) else SR.AMBER)
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[r].height = 24
    if reco.get("reasoning"):
        r += 1
        ws.merge_cells(start_row=r, start_column=1, end_row=r + 2, end_column=14)
        rc = ws.cell(row=r, column=1, value=reco.get("reasoning"))
        rc.alignment = Alignment(vertical="top", wrap_text=True)
        rc.font = Font(size=10)
        r += 2
    if reco.get("risks"):
        r += 1
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=14)
        rc = ws.cell(row=r, column=1, value="Risks: " + reco.get("risks"))
        rc.alignment = Alignment(vertical="top", wrap_text=True)
        rc.font = Font(size=10, italic=True, color="9C0006")

    # env KPI cards
    r += 2
    r = SR._section(ws, r, "Env baseline (POST /v1/ai-sourcing-v3/search)")
    SR._card(ws, r, 1, "Candidates", str(env["n"]))
    SR._card(ws, r, 4, "Total search ms", f"{env['total_ms']:,}")
    SR._card(ws, r, 7, "Per-candidate ms", str(env.get("per_candidate_ms")))
    SR._card(ws, r, 10, "Throughput c/s", str(env.get("throughput_cps")))
    r += 4

    # leaderboard
    r = SR._section(ws, r, "Leaderboard — every model vs the env (green = closer to env / faster)")
    cols = ["model", "cov", "fail", "overall MAE", "bias", "±tol", "score r",
            "rank r", "label agr", "crit MAE", "crit agr", "p50 ms", "p95 ms",
            "thru c/s", "out tok"]
    hdr = r
    for i, name in enumerate(cols, 1):
        cell = ws.cell(row=hdr, column=i, value=name)
        cell.font = Font(bold=True, color="FFFFFFFF", size=9)
        cell.fill = SR._fill(SR.BLUE)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.column_dimensions["A"].width = 30
    # env row first (self-reference baseline)
    r = hdr + 1
    envrow = [ENV_MODEL_LABEL, 1.0, 0.0, 0, 0, 1.0, 1.0, 1.0, 1.0, 0, 1.0,
              env.get("per_candidate_ms"), env.get("per_candidate_ms"), env.get("throughput_cps"), ""]
    SR._append(ws, envrow)
    for cc in range(1, 16):
        ws.cell(row=r, column=cc).fill = SR._fill(SR.GREY)
        ws.cell(row=r, column=cc).font = Font(bold=True, size=9)
    # ranked models: best replacement first (low MAE, high rank corr, high coverage)
    def _rankkey(mm):
        return (-(mm.get("coverage") or 0), (mm.get("score_mae") if mm.get("score_mae") is not None else 999),
                -((mm.get("rank_corr") or -1)))
    for mm in sorted(metrics, key=_rankkey):
        r += 1
        SR._append(ws, [mm["label"], mm["coverage"], mm["failure_rate"], mm["score_mae"],
                        mm["score_bias"], mm["score_within_tol"], mm["score_corr"], mm["rank_corr"],
                        mm["label_agree"], mm["crit_mae"], mm["crit_agree"], mm["lat_p50"],
                        mm["lat_p95"], mm["throughput_cps"], mm["tok_out_avg"]])
        row = r
        ws.cell(row=row, column=2).font = Font(color=_rate_color(mm["coverage"]), bold=True, size=9)
        for ci, gw in ((4, (8, 15)), (10, (10, 20))):     # MAEs: lower better
            ws.cell(row=row, column=ci).font = Font(color=_mae_color(ws.cell(row=row, column=ci).value or 0, *gw), bold=True, size=9)
        for ci in (6, 7, 8, 9, 11):                         # rates/correlations: higher better
            v = ws.cell(row=row, column=ci).value
            ws.cell(row=row, column=ci).font = Font(color=_rate_color(v if isinstance(v, (int, float)) else None), bold=True, size=9)
    r += 2
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=14)
    ws.cell(row=r, column=1, value=(f"cov=coverage · fail=failure rate · MAE=mean |model−env| (lower=closer) · "
                                    f"bias=mean signed (model−env) · ±tol=share within ±{LABEL_TOL} overall / ±{CRIT_TOL} per-criterion "
                                    f"· r=Pearson · rank r=Spearman on env ordering · thru=candidates/sec at concurrency")
            ).font = Font(size=9, italic=True, color="808080")

    # ── Slots sheet ──
    ws2 = wb.create_sheet("Models & Keys")
    SR._header(ws2, ["slot id", "status", "provider", "model", "key env var", "note"],
               {"slot id": 20, "provider": 15, "model": 34, "key env var": 20, "note": 46})
    for s in snap["slots"]:
        SR._append(ws2, [s["id"], "ENABLED" if s["enabled"] else "skipped", s["provider"],
                         s.get("model"), s.get("key_env"), s.get("skip_reason") or ""])
        SR._verdict_fill(ws2.cell(row=ws2.max_row, column=2), ("ENABLED",), ("skipped",))

    # ── Per-candidate scores (env vs each model) ──
    ws3 = wb.create_sheet("Candidate Scores")
    ids = [m["id"] for m in metrics]
    head = ["candidate", "position", "env score", "env label"] + [f"{i} score" for i in ids] + [f"{i} Δ" for i in ids]
    SR._header(ws3, head, {"candidate": 24, "position": 26, "env label": 16})
    env_by_key = snap["env_by_key"]
    for ck, env_c in env_by_key.items():
        nm = f"{env_c.get('firstname') or ''} {env_c.get('lastname') or ''}".strip()
        ev = env_c.get("matchScore")
        row = [nm, env_c.get("position"), ev, env_c.get("matchLabel")]
        deltas = []
        for i in ids:
            r = (snap["results"].get(i) or {}).get(ck) or {}
            p = r.get("parsed") or {}
            mv = p.get("matchScore") if (r.get("ok") and p) else None
            row.append(mv if mv is not None else ("ERR" if r else "—"))
            deltas.append((mv - ev) if (isinstance(mv, (int, float)) and isinstance(ev, (int, float))) else None)
        SR._append(ws3, row + deltas)

    # ── Failures ──
    ws4 = wb.create_sheet("Failures")
    SR._header(ws4, ["model", "candidate key", "http", "attempts", "error"],
               {"model": 22, "candidate key": 34, "error": 80})
    for mm in metrics:
        for f in mm["fails"]:
            SR._append(ws4, [mm["label"], f["key"], f.get("http"), f.get("attempts"), f.get("error")])
    if ws4.max_row == 1:
        SR._append(ws4, ["— no failures —", "", "", "", ""])

    # ── Samples (env vs models, human-readable) ──
    ws5 = wb.create_sheet("Samples")
    SR._header(ws5, ["candidate", "position", "env score/label"] +
               [m["id"] for m in metrics], {"candidate": 24, "position": 26, "env score/label": 20})
    for s in snap["samples"]:
        row = [s["candidate"], s["position"], f"{s['env_score']} / {s['env_label']}"]
        for mm in metrics:
            d = s["models"].get(mm["id"]) or {}
            row.append(f"{d.get('score')} / {d.get('label')}" if d.get("ok") else f"ERR: {(d.get('error') or '')[:40]}")
        SR._append(ws5, row)
    for rr in ws5.iter_rows(min_row=2):
        for cc in rr:
            cc.alignment = Alignment(vertical="top", wrap_text=True)

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    wb.save(path)
    return path


# ══════════════════════════════════════════════════════════════════════════════

def _load_criteria_arg(query, criteria_arg, database_type, no_config):
    """Fixed criteria: from --criteria JSON, else generated once by the config endpoint,
    else a simple fallback so the run still proceeds."""
    if criteria_arg:
        raw = json.load(open(criteria_arg, encoding="utf-8")) if os.path.exists(criteria_arg) else json.loads(criteria_arg)
        return _criteria_pairs(raw), "explicit"
    if not no_config:
        try:
            import rcrm_auth
            import rcrm_client
            token = config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]
            st, cfg = rcrm_client.get_configuration(ai_search=query, database_type=database_type,
                                                    token=token, timeout=60)
            crit = _criteria_pairs((cfg or {}).get("criteria"))
            if crit:
                return crit, "config-endpoint"
        except Exception as e:                                  # noqa: BLE001
            print(f"  (config endpoint unavailable: {type(e).__name__}: {e} — using fallback criteria)")
    return [{"text": "Relevant professional experience for the query", "weight": 8},
            {"text": "Location / seniority match", "weight": 5}], "fallback"


def main():
    ap = argparse.ArgumentParser(description="Bake off challenger LLMs against the env candidate scorer.")
    ap.add_argument("--run-id", default="bake1")
    ap.add_argument("--query", help="the FIXED query scored by env + every model")
    ap.add_argument("--criteria", help="JSON [{text,weight}] or a path; else generated from the query")
    ap.add_argument("--pages", type=int, default=int(os.environ.get("BAKEOFF_PAGES", "1")))
    ap.add_argument("--limit", type=int, default=0, help="cap candidates scored (0 = all returned)")
    ap.add_argument("--database-type", type=int, default=config.DB_GLOBAL_DATABASE)
    ap.add_argument("--no-config", action="store_true", help="skip the config endpoint; use fallback/--criteria")
    ap.add_argument("--no-judge", action="store_true", help="skip the Anthropic recommendation")
    ap.add_argument("--models", help="path to a JSON slots file (overrides challenger_llms.SLOTS)")
    ap.add_argument("--list-models", action="store_true", help="print slots + resolved keys and exit")
    ap.add_argument("--init-models", metavar="PATH", help="write the built-in SLOTS to PATH and exit")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the workbook from the saved snapshot")
    ap.add_argument("--yes", action="store_true", help="skip the pre-run confirmation")
    args = ap.parse_args()

    if args.init_models:
        print("wrote slot template ->", CL.write_template(args.init_models))
        return

    slots = CL.load_slots(args.models)
    if args.list_models:
        print("Challenger slots (edit challenger_llms.SLOTS or pass --models file.json):\n")
        print(CL.describe(slots))
        enabled = [s for s in slots if s["enabled"]]
        print(f"\n{len(enabled)}/{len(slots)} enabled. Export the missing key env vars to enable the rest.")
        return

    snap_path = os.path.join(config.OUTPUT_DIR, f"bakeoff_{args.run_id}.json")
    xlsx_path = os.path.join(config.OUTPUT_DIR, f"bakeoff_{args.run_id}.xlsx")

    if args.rebuild:
        snap = json.load(open(snap_path, encoding="utf-8"))
        print("rebuilt ->", build_report(snap, xlsx_path))
        return

    if not args.query:
        raise SystemExit("--query is required (the fixed query scored by env + every model).")
    enabled = [s for s in slots if s["enabled"]]
    if not enabled:
        raise SystemExit("No enabled slots. Run --list-models; export the API-key env vars for the slots you want.")

    system_prompt = open(SCORING_PROMPT, encoding="utf-8").read()
    criteria, crit_src = _load_criteria_arg(args.query, args.criteria, args.database_type, args.no_config)

    print(f"\nBAKE-OFF run '{args.run_id}'")
    print(f"  query        : {args.query!r}  (FIXED for env + all models)")
    print(f"  criteria     : {crit_src} — {len(criteria)} criterion(s)")
    print(f"  env baseline : {config.RCRM_NEPTUNE_URL}/{config.SOURCING_SEARCH_PATH}  (db={args.database_type}, {args.pages} page[s])")
    print(f"  models       : {', '.join(s['id'] for s in enabled)}")
    print(f"  judge        : {'off' if args.no_judge else ('Sonnet 5' if os.environ.get('ANTHROPIC_API_KEY') else 'off (no ANTHROPIC_API_KEY)')}")
    skipped = [s for s in slots if not s["enabled"]]
    if skipped:
        print(f"  skipped      : " + "; ".join(f"{s['id']} ({s['skip_reason']})" for s in skipped))
    if not args.yes:
        if input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
            return

    print("\n[1/4] capturing env baseline…")
    candidates, env_meta = capture_baseline(args.query, criteria, args.pages, args.limit)
    print(f"      env scored {env_meta['n']} candidates in {env_meta['total_ms']} ms "
          f"({env_meta.get('throughput_cps')} c/s)"
          + (f"  ⚠ {env_meta['error']}" if env_meta.get("error") else ""))
    if not candidates:
        raise SystemExit("Env returned 0 candidates — nothing to score. (External corpus returns "
                         "candidates only on PROD; run `source set_prod_urls.sh` first.)")
    env_by_key = {}
    for c in candidates:
        env_by_key.setdefault(_key(c), c)

    print("[2/4] scoring with challenger models…")
    results, walls = run_challengers(slots, args.query, criteria, list(env_by_key.values()), system_prompt)

    print("[3/4] computing metrics…")
    metrics = [compute_metrics(s, results.get(s["id"], {}), env_by_key, criteria, walls.get(s["id"], 0.0))
               for s in enabled]
    for mm in metrics:
        print(f"      {mm['label']:<40} cov={mm['coverage']} MAE={mm['score_mae']} "
              f"rank_r={mm['rank_corr']} p50={mm['lat_p50']}ms thru={mm['throughput_cps']}c/s")

    samples = _samples(env_by_key, results, metrics)
    recommendation = None
    if not args.no_judge:
        print("[4/4] asking Anthropic which model can replace the env…")
        recommendation = judge_replacement(args.query, metrics, samples)
        if recommendation.get("recommended_model"):
            print(f"      → recommended: {recommendation['recommended_model']}")
        elif recommendation.get("_skipped") or recommendation.get("_error"):
            print("      → " + (recommendation.get("_skipped") or recommendation.get("_error")))

    snap = {"run_id": args.run_id, "query": args.query, "criteria": criteria, "criteria_source": crit_src,
            "database_type": args.database_type, "pages": args.pages,
            "env_model_label": ENV_MODEL_LABEL, "env_meta": env_meta,
            "slots": [{k: s.get(k) for k in ("id", "label", "provider", "model", "key_env",
                                             "enabled", "skip_reason")} for s in slots],
            "metrics": metrics, "samples": samples, "recommendation": recommendation,
            "env_by_key": env_by_key, "results": results,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds")}
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    json.dump(snap, open(snap_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\nsnapshot -> {snap_path}")
    print("report   ->", build_report(snap, xlsx_path))


if __name__ == "__main__":
    main()

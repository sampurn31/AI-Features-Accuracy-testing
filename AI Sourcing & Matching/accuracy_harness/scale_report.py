"""
Workbook builder for the scale run: ONE pretty xlsx, six sheets.
  Dashboard      — KPI cards, per-check pass rates, score histogram + chart, top issues
  Variations     — one row per variation (the working sheet; fully filterable)
  Failures       — every deterministic check failure (only what broke)
  Judge Samples  — LLM-judge verdicts on sampled ranks, with reasoning
  Candidates     — every fetched candidate, compact (page/rank/score/formula/relevance)
  Cases          — the input catalog (what we asked for)
"""
import datetime
import os
import statistics
from collections import Counter

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config

NAVY, BLUE = "1F3864", "305496"
GREEN, GREEN_T = "C6EFCE", "006100"
RED, RED_T = "FFC7CE", "9C0006"
AMBER, AMBER_T = "FFEB9C", "9C6500"
GREY, ZEBRA = "D9D9D9", "F4F6FA"
THIN = Border(*[Side(style="thin", color="BFBFBF")] * 4)

# Plain-English names for the automated checks (keys stay in the raw data / checkpoint).
CHECK_LABELS = {
    "score_formula": "Score ≠ weighted avg (formula broken)",
    "ranking_monotonic": "Ranking out of order",
    "pagination_dup": "Same person on multiple pages",
    "criteria_coverage": "Criterion dropped or invented",
    "analysis_shape": "Analysis malformed (missing score/reasoning)",
    "label_consistency": "Label contradicts score",
    "score_range": "Per-criterion score outside 0-100",
    "meta_consistency": "Metadata inconsistent across pages",
    "cohort_none_null": "Unscored run returned scoring fields",
    "cohort_single": "matchScore ≠ its single criterion's score",
    "cap_contract": "1000-char input cap contract broken",
    "zero_results": "Zero results — query over-constrained",
    # db=1 ground-truth checks
    "band_envelope": "Score outside ground-truth band",
    "label_ordering": "STRONG>PARTIAL>WEAK>IRRELEVANT ordering broken",
    "precision_at_k": "Irrelevant candidate in top-K",
    "inference_band": "Inferred-match under-scored (keyword-only matching)",
}


def _check_label(key):
    return CHECK_LABELS.get(key, key)


def _fill(hexc):
    return PatternFill("solid", fgColor=hexc)


def _clean(v):
    """Strip control chars that openpyxl rejects (scraped profiles carry them, e.g. a
    company name 'Saks Caf\\x01' → IllegalCharacterError). Non-strings pass through."""
    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v


def _append(ws, vals):
    """Append a row, sanitizing every cell value first."""
    ws.append([_clean(v) for v in vals])


def _header(ws, cols, widths=None, row=1, display=None):
    """Header row; `display` maps internal keys -> the plain-English title shown."""
    display = display or {}
    for i, c in enumerate(cols, 1):
        cell = ws.cell(row=row, column=i, value=display.get(c, c))
        cell.font = Font(bold=True, color="FFFFFFFF", size=10)
        cell.fill = _fill(BLUE)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = f"A{row + 1}"        # string form — ws.cell() would CREATE the row
    ws.auto_filter.ref = f"A{row}:{get_column_letter(len(cols))}{row}"
    for name, w in (widths or {}).items():
        if name in cols:
            ws.column_dimensions[get_column_letter(cols.index(name) + 1)].width = w


def _verdict_fill(cell, good, bad):
    v = cell.value
    if v is None or v == "":
        return
    if v in good:
        cell.fill = _fill(GREEN); cell.font = Font(bold=True, color=GREEN_T, size=10)
    elif v in bad:
        cell.fill = _fill(RED); cell.font = Font(bold=True, color=RED_T, size=10)
    else:
        cell.fill = _fill(AMBER); cell.font = Font(bold=True, color=AMBER_T, size=10)


def _pct(x):
    return None if x is None else round(x, 3)


# ── aggregation ──────────────────────────────────────────────────────────────

def _aggregate(rows):
    a = {}
    a["n"] = len(rows)
    a["ok"] = sum(1 for r in rows if r["status"] == "ok")
    a["partial"] = sum(1 for r in rows if r["status"] == "partial")
    a["error"] = sum(1 for r in rows if r["status"] == "error")
    a["pages"] = sum(len(r.get("pages") or []) for r in rows)
    a["cands"] = sum(r.get("n_candidates") or 0 for r in rows)
    a["judged"] = sum(len(r.get("judge") or []) for r in rows)
    a["fails_by_check"] = Counter(f["check"] for r in rows for f in r.get("fails") or [])
    a["vars_failing"] = sum(1 for r in rows if r.get("fails"))
    # formula accuracy across every scored candidate
    devs, formula_bad = [], 0
    all_scores = []
    for r in rows:
        for c in r.get("candidates") or []:
            if c.get("matchScore") is not None:
                all_scores.append(c["matchScore"])
            d = c.get("_fdev")
            if d is not None:
                devs.append(d)
                if d > config.SOURCING_SCORE_TOL:
                    formula_bad += 1
    a["scored_cands"] = len(devs)
    a["formula_ok_rate"] = (1 - formula_bad / len(devs)) if devs else None
    a["formula_max_dev"] = max(devs) if devs else None
    a["scores"] = all_scores
    # relevance means (variations where facet applied)
    for k in ("title_rate", "skill_rate", "loc_rate"):
        vals = [r["relevance"][k] for r in rows if (r.get("relevance") or {}).get(k) is not None]
        a[k] = statistics.mean(vals) if vals else None
    # judge tallies — per-criterion verdicts
    cr, cs, sens = Counter(), Counter(), Counter()
    gaps, judged_cands, judge_errors = [], 0, 0
    for r in rows:
        for j in r.get("judge") or []:
            if j.get("error"):
                judge_errors += 1
                continue
            judged_cands += 1
            if j.get("overall_sensible"):
                sens[j["overall_sensible"]] += 1
            for cv in j.get("crit_verdicts") or []:
                if cv.get("reasoning_verdict"):
                    cr[cv["reasoning_verdict"]] += 1
                if cv.get("score_verdict"):
                    cs[cv["score_verdict"]] += 1
                if cv.get("gap") is not None:
                    gaps.append(cv["gap"])
    a["crit_reason"], a["crit_score"], a["sens"] = cr, cs, sens
    a["n_crit_verdicts"] = sum(cr.values())
    a["judged_cands"], a["judge_errors"] = judged_cands, judge_errors
    a["avg_gap"] = statistics.mean(gaps) if gaps else None
    a["p90_gap"] = (sorted(gaps)[int(0.9 * (len(gaps) - 1))] if gaps else None)
    # per-check pass rates at variation level
    a["check_names"] = ["score_formula", "ranking_monotonic", "pagination_dup", "criteria_coverage",
                        "analysis_shape", "label_consistency", "score_range", "meta_consistency",
                        "cohort_none_null", "cohort_single", "cap_contract", "zero_results"]
    a["check_var_fail"] = {cn: sum(1 for r in rows if any(f["check"] == cn for f in r.get("fails") or []))
                           for cn in a["check_names"]}
    # cohort splits
    a["cohorts"] = Counter(r["cohort"] for r in rows)
    a["formats"] = Counter(r["format"] for r in rows)
    a["n_overlimit"] = sum(1 for r in rows if r.get("format") == "jd_overlimit")
    a["zero_results"] = a["check_var_fail"]["zero_results"]
    a["n_scored_vars"] = sum(1 for r in rows if r["cohort"] != "none"
                             and r.get("format") != "jd_overlimit")
    return a


# ── data sheets ──────────────────────────────────────────────────────────────

def _sheet_variations(wb, rows):
    ws = wb.create_sheet("Variations")
    cols = ["id", "cohort", "format", "family", "role", "location", "n_criteria", "query_chars",
            "status", "pages", "http", "totalResults", "n_candidates", "det_pass", "n_fails",
            "fail_checks", "score_top", "score_p50", "score_low", "score_decay",
            "title_match", "skill_match", "loc_match",
            "reasoning_worst", "score_agree", "avg_gap", "overall_sensible",
            "page1_s", "elapsed_s", "error"]
    widths = {"id": 34, "cohort": 8, "format": 10, "family": 11, "role": 26, "location": 20,
              "n_criteria": 7, "query_chars": 8, "status": 8, "pages": 7, "http": 16,
              "totalResults": 10, "n_candidates": 8, "det_pass": 8, "n_fails": 7,
              "fail_checks": 30, "score_top": 8, "score_p50": 8, "score_low": 8, "score_decay": 8,
              "title_match": 9, "skill_match": 9, "loc_match": 9, "reasoning_worst": 11,
              "score_agree": 10, "avg_gap": 8, "overall_sensible": 10,
              "page1_s": 8, "elapsed_s": 8, "error": 40}
    display = {"id": "Case ID", "cohort": "Criteria set", "format": "Input type",
               "family": "Role family", "role": "Role", "location": "Location",
               "n_criteria": "# criteria", "query_chars": "Input length (chars)",
               "status": "Run status", "pages": "Pages fetched", "http": "HTTP per page",
               "totalResults": "Corpus matches", "n_candidates": "Candidates fetched",
               "det_pass": "All checks passed?", "n_fails": "# issues",
               "fail_checks": "What failed", "score_top": "Top score",
               "score_p50": "Median score", "score_low": "Lowest score",
               "score_decay": "Top − lowest", "title_match": "Title match %",
               "skill_match": "Skill match %", "loc_match": "Location match %",
               "reasoning_worst": "AI reasoning (judge verdict)",
               "score_agree": "Judge agrees with scores",
               "avg_gap": "Judge vs AI score gap", "overall_sensible": "Overall score fair?",
               "page1_s": "Page-1 sec", "elapsed_s": "Total sec", "error": "Error"}
    _header(ws, cols, widths, display=display)
    RV = {"wrong": 3, "partly": 2, "correct": 1, "": 0}
    SV = {"no": 3, "borderline": 2, "yes": 1, "": 0}

    def worst(js, key, order):
        w = ""
        for j in js:
            if order.get(j.get(key, ""), 0) > order.get(w, 0):
                w = j.get(key, "")
        return w

    for r in sorted(rows, key=lambda x: x["id"]):
        rel = r.get("relevance") or {}
        js = r.get("judge") or []
        decay = (r["score_top"] - r["score_low"]
                 if r.get("score_top") is not None and r.get("score_low") is not None else None)
        vals = [r["id"], r["cohort"], r["format"], r["family"], r["role"], r["location"],
                r["n_criteria"], r["query_chars"], r["status"], len(r.get("pages") or []),
                ",".join(str(p.get("http")) for p in r.get("pages") or []),
                r.get("totalResults"), r.get("n_candidates"),
                "PASS" if not r.get("fails") else "FAIL", len(r.get("fails") or []),
                ", ".join(sorted({_check_label(f["check"]) for f in r.get("fails") or []})),
                r.get("score_top"), r.get("score_p50"), r.get("score_low"), decay,
                _pct(rel.get("title_rate")), _pct(rel.get("skill_rate")), _pct(rel.get("loc_rate")),
                worst(js, "reasoning_worst", RV),
                (round(min((j.get("score_agree_rate") for j in js
                            if j.get("score_agree_rate") is not None), default=None), 2)
                 if any(j.get("score_agree_rate") is not None for j in js) else None),
                (round(max((j.get("avg_gap") for j in js if j.get("avg_gap") is not None),
                           default=0), 1) if any(j.get("avg_gap") is not None for j in js) else None),
                worst(js, "overall_sensible", SV), r.get("page1_s"), r.get("elapsed_s"),
                (r.get("error") or "")[:200]]
        _append(ws,vals)
        rc = ws[ws.max_row]                # style the row we just appended
        if ws.max_row % 2 == 0:
            for c in rc:
                c.fill = _fill(ZEBRA)
        for c in rc:
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = THIN
        _verdict_fill(rc[cols.index("status")], ("ok",), ("error",))
        _verdict_fill(rc[cols.index("det_pass")], ("PASS",), ("FAIL",))
        _verdict_fill(rc[cols.index("reasoning_worst")], ("correct",), ("wrong",))
        _verdict_fill(rc[cols.index("overall_sensible")], ("yes",), ("no",))
        sa = rc[cols.index("score_agree")]
        if isinstance(sa.value, (int, float)):
            sa.number_format = "0%"
            sa.fill = _fill(GREEN if sa.value >= 0.8 else AMBER if sa.value >= 0.5 else RED)
        for name in ("title_match", "skill_match", "loc_match"):
            c = rc[cols.index(name)]
            if isinstance(c.value, float):
                c.number_format = "0%"
                if c.value < 0.3:
                    c.fill = _fill(AMBER)
    return ws


def _sheet_failures(wb, rows):
    ws = wb.create_sheet("Failures")
    cols = ["variation", "cohort", "format", "check", "issue", "detail"]
    _header(ws, cols, {"variation": 34, "cohort": 8, "format": 10, "check": 18, "issue": 30,
                       "detail": 100},
            display={"variation": "Case ID", "cohort": "Criteria set", "format": "Input type",
                     "check": "Check ID", "issue": "Issue", "detail": "What happened"})
    n = 0
    for r in sorted(rows, key=lambda x: x["id"]):
        for f in r.get("fails") or []:
            _append(ws,[r["id"], r["cohort"], r["format"], f["check"],
                       _check_label(f["check"]), f["detail"]])
            n += 1
    if n == 0:
        _append(ws,["(no deterministic check failures — every candidate passed)", "", "", "", "", ""])
        ws["A2"].font = Font(bold=True, color=GREEN_T)
        ws["A2"].fill = _fill(GREEN)
    for row_cells in ws.iter_rows(min_row=2):
        for c in row_cells:
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = THIN
        if row_cells[4].value:
            row_cells[4].fill = _fill(RED)
            row_cells[4].font = Font(bold=True, color=RED_T, size=10)
    return ws


def _sheet_criterion_verdicts(wb, rows):
    """THE per-criterion audit sheet: one row per (variation, judged candidate, criterion) —
    the AI's score + reasoning side by side with the judge's own score + verdicts."""
    ws = wb.create_sheet("Criterion Verdicts")
    cols = ["variation", "rank", "candidate", "criterion", "weight", "ai_score", "judge_score",
            "gap", "score_verdict", "reasoning_verdict", "ai_reasoning", "judge_note"]
    _header(ws, cols, {"variation": 32, "rank": 5, "candidate": 18, "criterion": 40, "weight": 6,
                       "ai_score": 8, "judge_score": 8, "gap": 6, "score_verdict": 10,
                       "reasoning_verdict": 10, "ai_reasoning": 70, "judge_note": 70},
            display={"variation": "Case ID", "rank": "Rank", "candidate": "Candidate",
                     "criterion": "Criterion", "weight": "Weight", "ai_score": "AI score",
                     "judge_score": "Judge score", "gap": "Gap",
                     "score_verdict": "Judge on score", "reasoning_verdict": "Judge on reasoning",
                     "ai_reasoning": "AI's reasoning", "judge_note": "Judge's evidence note"})
    for r in sorted(rows, key=lambda x: x["id"]):
        for j in r.get("judge") or []:
            for cv in j.get("crit_verdicts") or []:
                _append(ws,[r["id"], j.get("rank"), j.get("name"), cv.get("criterion"),
                           cv.get("weight"), cv.get("ai_score"), cv.get("judge_score"),
                           cv.get("gap"), cv.get("score_verdict"), cv.get("reasoning_verdict"),
                           cv.get("ai_reasoning"), cv.get("judge_note")])
    for row_cells in ws.iter_rows(min_row=2):
        for c in row_cells:
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = THIN
        _verdict_fill(row_cells[cols.index("score_verdict")], ("agree",), ("too_high", "too_low"))
        _verdict_fill(row_cells[cols.index("reasoning_verdict")], ("correct",), ("wrong",))
        g = row_cells[cols.index("gap")]
        if isinstance(g.value, (int, float)) and g.value > config.SOURCING_JUDGE_GAP:
            g.fill = _fill(AMBER)
            g.font = Font(bold=True, color=AMBER_T, size=10)
    return ws


def _sheet_judge(wb, rows):
    ws = wb.create_sheet("Judge Samples")
    cols = ["variation", "rank", "page", "cand_id", "name", "position", "company", "matchScore",
            "reasoning_worst", "score_agree", "avg_gap", "overall_sensible", "summary"]
    _header(ws, cols, {"variation": 34, "rank": 6, "page": 6, "cand_id": 11, "name": 18,
                       "position": 26, "company": 22, "matchScore": 9, "reasoning_worst": 11,
                       "score_agree": 10, "avg_gap": 8, "overall_sensible": 11, "summary": 90},
            display={"variation": "Case ID", "rank": "Rank", "page": "Page", "cand_id": "API id",
                     "name": "Candidate", "position": "Position", "company": "Company",
                     "matchScore": "AI overall score", "reasoning_worst": "Worst reasoning verdict",
                     "score_agree": "Score agreement", "avg_gap": "Avg gap",
                     "overall_sensible": "Overall fair?", "summary": "Judge summary"})
    for r in sorted(rows, key=lambda x: x["id"]):
        for j in r.get("judge") or []:
            sar = j.get("score_agree_rate")
            _append(ws,[r["id"], j.get("rank"), j.get("page"), j.get("cand_id"), j.get("name"),
                       j.get("position"), j.get("company"), j.get("matchScore"),
                       j.get("reasoning_worst"), (round(sar, 2) if sar is not None else None),
                       (round(j["avg_gap"], 1) if j.get("avg_gap") is not None else None),
                       j.get("overall_sensible"), j.get("summary")])
    for row_cells in ws.iter_rows(min_row=2):
        for c in row_cells:
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = THIN
        _verdict_fill(row_cells[cols.index("reasoning_worst")], ("correct",), ("wrong",))
        _verdict_fill(row_cells[cols.index("overall_sensible")], ("yes",), ("no",))
        sa = row_cells[cols.index("score_agree")]
        if isinstance(sa.value, (int, float)):
            sa.number_format = "0%"
    return ws


def _sheet_candidates(wb, rows):
    ws = wb.create_sheet("Candidates")
    cols = ["variation", "page", "rank", "cand_id", "name", "position", "company", "city",
            "country", "matchScore", "formula_expected", "formula_dev", "formula_ok",
            "matchLabel", "crit_scores(w)",
            "title_m", "skill_m", "loc_m"]
    _header(ws, cols, {"variation": 32, "page": 5, "rank": 5, "cand_id": 11, "name": 18,
                       "position": 28, "company": 22, "city": 13, "country": 10, "matchScore": 9,
                       "formula_expected": 10, "formula_dev": 9, "formula_ok": 9,
                       "matchLabel": 13, "crit_scores(w)": 22,
                       "title_m": 7, "skill_m": 7, "loc_m": 7},
            display={"variation": "Case ID", "page": "Page", "rank": "Rank", "cand_id": "API id",
                     "name": "Candidate", "position": "Position", "company": "Company",
                     "city": "City", "country": "Country", "matchScore": "AI score",
                     "formula_expected": "Expected (Σs·w/Σw)", "formula_dev": "Deviation",
                     "formula_ok": "Formula OK?", "matchLabel": "Label",
                     "crit_scores(w)": "Per-criterion scores (weight)",
                     "title_m": "Title ✓", "skill_m": "Skill ✓", "loc_m": "Loc ✓"})
    import sourcing_checks
    for r in sorted(rows, key=lambda x: x["id"]):
        for c in r.get("candidates") or []:
            fdev = c.get("_fdev")
            fok = ("" if fdev is None else ("OK" if fdev <= config.SOURCING_SCORE_TOL else "OFF"))
            wa = sourcing_checks.weighted_avg(c.get("matchAnalysis") or [])
            _append(ws,[r["id"], c.get("_page"), c.get("_rank"), c.get("id"),
                       f"{c.get('firstname') or ''} {c.get('lastname') or ''}".strip(),
                       c.get("position"), c.get("company"), c.get("city"), c.get("country"),
                       c.get("matchScore"), (round(wa, 1) if wa is not None else None),
                       fdev, fok, c.get("matchLabel"),
                       "|".join(f"{a.get('score')}(w{a.get('weight')})"
                                for a in (c.get("matchAnalysis") or [])),
                       "Y" if c.get("_title_m") else "", "Y" if c.get("_skill_m") else "",
                       "Y" if c.get("_loc_m") else ""])
            if fok == "OFF":
                rc = ws[ws.max_row]
                rc[cols.index("formula_ok")].fill = _fill(RED)
                rc[cols.index("formula_ok")].font = Font(bold=True, color=RED_T, size=10)
    return ws


def _sheet_cases(wb, rows):
    ws = wb.create_sheet("Cases")
    cols = ["id", "cohort", "format", "family", "role", "seniority", "location", "company_led",
            "n_criteria", "criteria", "query_chars", "query_preview"]
    _header(ws, cols, {"id": 34, "cohort": 8, "format": 10, "family": 11, "role": 26,
                       "seniority": 9, "location": 20, "company_led": 9, "n_criteria": 7,
                       "criteria": 60, "query_chars": 8, "query_preview": 100},
            display={"id": "Case ID", "cohort": "Criteria set", "format": "Input type",
                     "family": "Role family", "role": "Role", "seniority": "Seniority",
                     "location": "Location", "company_led": "Names hiring co?",
                     "n_criteria": "# criteria", "criteria": "Criteria (weight)",
                     "query_chars": "Input length", "query_preview": "Input preview (first 500 chars)"})
    for r in sorted(rows, key=lambda x: x["id"]):
        crit = "; ".join(f"{c['text']} (w{c['weight']})" for c in r.get("criteria") or [])
        _append(ws,[r["id"], r["cohort"], r["format"], r["family"], r["role"], r.get("seniority"),
                   r.get("location"), ("YES" if r.get("company_led") else ""), r["n_criteria"],
                   crit, r["query_chars"], (r.get("query") or "")[:500]])
    for row_cells in ws.iter_rows(min_row=2):
        for c in row_cells:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    return ws


# ── dashboard ────────────────────────────────────────────────────────────────

def _card(ws, row, col, title, value, sub="", color=NAVY):
    """3-wide x 3-tall KPI card."""
    ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 2)
    ws.merge_cells(start_row=row + 1, start_column=col, end_row=row + 1, end_column=col + 2)
    ws.merge_cells(start_row=row + 2, start_column=col, end_row=row + 2, end_column=col + 2)
    t = ws.cell(row=row, column=col, value=title)
    v = ws.cell(row=row + 1, column=col, value=value)
    s = ws.cell(row=row + 2, column=col, value=sub)
    t.font = Font(bold=True, size=10, color="595959")
    v.font = Font(bold=True, size=20, color=color)
    s.font = Font(size=9, color="808080")
    for rr in range(row, row + 3):
        for cc in range(col, col + 3):
            ws.cell(row=rr, column=cc).fill = _fill("F2F2F2")
            ws.cell(row=rr, column=cc).border = THIN
    t.alignment = v.alignment = s.alignment = Alignment(horizontal="center", vertical="center")


def _rate_color(rate, good=0.98, warn=0.90):
    if rate is None:
        return NAVY
    return GREEN_T if rate >= good else (AMBER_T if rate >= warn else RED_T)


def _section(ws, row, text):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=14)
    c = ws.cell(row=row, column=1, value=text)
    c.font = Font(bold=True, size=12, color="FFFFFFFF")
    c.fill = _fill(BLUE)
    c.alignment = Alignment(vertical="center")
    return row + 1


def _sheet_dashboard(ws, a, run_id):
    ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False
    for col in range(1, 15):
        ws.column_dimensions[get_column_letter(col)].width = 11
    ws.merge_cells("A1:N1")
    ws["A1"] = "PW-6818 · AI Sourcing V3 — Scale Accuracy Run (Global database, PROD)"
    ws["A1"].font = Font(bold=True, size=16, color="FFFFFFFF")
    ws["A1"].fill = _fill(NAVY)
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 30
    ws.merge_cells("A2:N2")
    ws["A2"] = (f"run {run_id} · generated {datetime.datetime.now():%Y-%m-%d %H:%M} · "
                f"{a['n']} variations · {a['pages']} pages · {a['cands']:,} candidates · "
                f"{a['judged']} LLM-judged samples")
    ws["A2"].font = Font(size=10, color="595959")

    # ── coverage map: exactly WHAT was verified, how, on how much, with what result ──
    r = 4
    r = _section(ws, r, "What was verified — coverage map")
    hdr_cells = [("A", "#"), ("B", "Verification"), ("F", "Method"), ("H", "Scope"), ("K", "Result")]
    for colL, text in hdr_cells:
        c = ws[f"{colL}{r}"]
        c.value = text
        c.font = Font(bold=True, size=10, color="595959")
    r += 1
    n = a["n"] or 1
    pv = a["check_var_fail"]
    fr = a["formula_ok_rate"]
    cr, cs, sens = a["crit_reason"], a["crit_score"], a["sens"]

    def _cov(num, what, method, scope, result, ok):
        nonlocal r
        ws.merge_cells(f"B{r}:E{r}"); ws.merge_cells(f"F{r}:G{r}")
        ws.merge_cells(f"H{r}:J{r}"); ws.merge_cells(f"K{r}:N{r}")
        ws[f"A{r}"] = num; ws[f"B{r}"] = _clean(what); ws[f"F{r}"] = _clean(method)
        ws[f"H{r}"] = _clean(scope); ws[f"K{r}"] = _clean(result)
        for colL in ("A", "B", "F", "H", "K"):
            ws[f"{colL}{r}"].alignment = Alignment(vertical="center", wrap_text=True)
        res = ws[f"K{r}"]
        res.font = Font(bold=True, size=10,
                        color=(GREEN_T if ok is True else RED_T if ok is False else AMBER_T))
        res.fill = _fill(GREEN if ok is True else RED if ok is False else AMBER)
        for cc in range(1, 15):
            ws.cell(row=r, column=cc).border = THIN
        r += 1

    _cov(1, "Endpoint & pagination health (HTTP 200, 4-page SSE stream)", "deterministic",
         f"{a['pages']} pages / {a['n']} variations",
         f"{a['ok']} ok · {a['partial']} partial · {a['error']} error",
         a['partial'] + a['error'] == 0)
    _cov(2, f"Overall score formula: matchScore = Σ(score×w)/Σw (±{config.SOURCING_SCORE_TOL})",
         "deterministic recompute", f"{a['scored_cands']:,} scored candidates",
         ("no scored candidates" if fr is None else
          f"{fr:.2%} pass · max deviation {a['formula_max_dev']}"),
         None if fr is None else fr >= 0.999)
    _cov(3, "Ranking strictly ordered by score across pages", "deterministic",
         f"{a['n']} variations", f"{n - pv['ranking_monotonic']}/{n} pass",
         pv['ranking_monotonic'] == 0)
    _cov(4, "No duplicate candidates across pages (pagination integrity)", "deterministic",
         f"{a['cands']:,} candidates", f"{n - pv['pagination_dup']}/{n} variations clean",
         pv['pagination_dup'] == 0)
    _cov(5, "Criteria coverage — no criterion dropped or invented, weights preserved",
         "deterministic", "every scored candidate",
         f"{n - pv['criteria_coverage']}/{n} pass", pv['criteria_coverage'] == 0)
    _cov(6, "Analysis shape — numeric 0-100 score + non-empty reasoning per criterion",
         "deterministic", "every scored candidate",
         f"{n - pv['analysis_shape']}/{n} pass", pv['analysis_shape'] == 0)
    _cov(7, "matchLabel consistent with matchScore", "deterministic", "every scored candidate",
         f"{n - pv['label_consistency']}/{n} pass", pv['label_consistency'] == 0)
    _cov(8, "Metadata consistency (totalResults, scoringApplied, hasNextPage)", "deterministic",
         "every page", f"{n - pv['meta_consistency']}/{n} pass", pv['meta_consistency'] == 0)
    n_none, n_single = a["cohorts"].get("none", 0), a["cohorts"].get("single", 0)
    _cov(9, "No-criteria cohort → scoring fields must be null", "deterministic",
         f"{n_none} variations", f"{n_none - pv['cohort_none_null']}/{n_none} pass",
         pv['cohort_none_null'] == 0 if n_none else None)
    _cov(10, "Single-criterion cohort → matchScore equals that criterion's score",
         "deterministic", f"{n_single} variations",
         f"{n_single - pv['cohort_single']}/{n_single} pass",
         pv['cohort_single'] == 0 if n_single else None)
    _cov(11, "Retrieval relevance — title / skill / location vs the parsed query", "heuristic",
         f"{a['cands']:,} candidates",
         " / ".join("—" if a[k] is None else f"{a[k]:.0%}"
                    for k in ("title_rate", "skill_rate", "loc_rate")) + "  (title/skill/loc)",
         None)
    _cov(12, "Per-criterion REASONING grounded in the candidate profile",
         "LLM judge (Sonnet 5)",
         f"{a['n_crit_verdicts']} criterion verdicts · {a['judged_cands']} candidates (ranks 1/mid/last)",
         f"correct {cr.get('correct', 0)} · partly {cr.get('partly', 0)} · wrong {cr.get('wrong', 0)}",
         (cr.get('wrong', 0) == 0 and cr.get('partly', 0) <= 0.1 * max(1, sum(cr.values())))
         if sum(cr.values()) else None)
    _cov(13, "Per-criterion SCORE correctness (judge independently re-scores each criterion)",
         "LLM judge (Sonnet 5)", f"{a['n_crit_verdicts']} criterion verdicts",
         (f"agree {cs.get('agree', 0)} · too_high {cs.get('too_high', 0)} · too_low "
          f"{cs.get('too_low', 0)} · avg gap {a['avg_gap']:.1f}" if a['avg_gap'] is not None
          else "—"),
         (cs.get('too_high', 0) + cs.get('too_low', 0) <= 0.2 * max(1, sum(cs.values())))
         if sum(cs.values()) else None)
    _cov(14, "Overall matchScore is a fair weighted summary of the evidence",
         "LLM judge (Sonnet 5)", f"{a['judged_cands']} candidates",
         f"yes {sens.get('yes', 0)} · borderline {sens.get('borderline', 0)} · no {sens.get('no', 0)}",
         sens.get('no', 0) == 0 if sum(sens.values()) else None)
    if a["n_overlimit"]:
        _cov(15, "Input cap contract — aiSearch >1000 chars rejected with the documented 400",
             "deterministic", f"{a['n_overlimit']} multi-page JD variations (3.8-10k chars)",
             f"{a['n_overlimit'] - pv['cap_contract']}/{a['n_overlimit']} rejected correctly",
             pv['cap_contract'] == 0)
    _cov(16, "Realistic scored queries return a non-empty pool (query-extraction sanity)",
         "deterministic", f"{a['n_scored_vars']} scored variations",
         f"{a['zero_results']} returned ZERO candidates"
         + (" — inspect queryUsed in Failures (hiring-company-as-filter?)" if a['zero_results'] else ""),
         a['zero_results'] == 0)
    if a["judge_errors"]:
        _cov("!", "Judge calls that errored (recorded, never fabricated)", "—",
             f"{a['judge_errors']} candidates", "see Judge Samples sheet", False)
    r += 1
    r = _section(ws, r, "Run health")
    _card(ws, r, 1, "VARIATIONS OK", f"{a['ok']}/{a['n']}", "completed all pages",
          _rate_color(a['ok'] / a['n'] if a['n'] else None))
    _card(ws, r, 4, "PARTIAL / ERROR", f"{a['partial']} / {a['error']}", "see Variations sheet",
          GREEN_T if (a['partial'] + a['error']) == 0 else AMBER_T)
    _card(ws, r, 7, "CANDIDATES", f"{a['cands']:,}", f"across {a['pages']} pages")
    _card(ws, r, 10, "VARIATIONS CLEAN", f"{a['n'] - a['vars_failing']}/{a['n']}",
          "zero deterministic fails", _rate_color((a['n'] - a['vars_failing']) / a['n'] if a['n'] else None))
    r += 4
    r = _section(ws, r, "Deterministic checks (objective — every candidate)")
    fr = a["formula_ok_rate"]
    _card(ws, r, 1, "SCORE FORMULA", "—" if fr is None else f"{fr:.1%}",
          f"matchScore = Σ(s·w)/Σw ±{config.SOURCING_SCORE_TOL} · max dev "
          f"{a['formula_max_dev'] if a['formula_max_dev'] is not None else '—'}",
          _rate_color(fr))
    per_var = a["check_var_fail"]
    n = a["n"] or 1
    _card(ws, r, 4, "RANKING ORDER", f"{n - per_var['ranking_monotonic']}/{n}",
          "variations strictly ordered", _rate_color((n - per_var['ranking_monotonic']) / n))
    _card(ws, r, 7, "PAGINATION", f"{n - per_var['pagination_dup']}/{n}",
          "no dup candidates across pages", _rate_color((n - per_var['pagination_dup']) / n))
    _card(ws, r, 10, "CRITERIA COVERAGE", f"{n - per_var['criteria_coverage']}/{n}",
          "no criterion dropped/invented", _rate_color((n - per_var['criteria_coverage']) / n))
    r += 4
    _card(ws, r, 1, "ANALYSIS SHAPE", f"{n - per_var['analysis_shape']}/{n}",
          "score+reasoning well-formed", _rate_color((n - per_var['analysis_shape']) / n))
    _card(ws, r, 4, "LABEL vs SCORE", f"{n - per_var['label_consistency']}/{n}",
          "matchLabel consistent", _rate_color((n - per_var['label_consistency']) / n))
    _card(ws, r, 7, "METADATA", f"{n - per_var['meta_consistency']}/{n}",
          "totals/flags consistent", _rate_color((n - per_var['meta_consistency']) / n))
    coh_bad = per_var["cohort_none_null"] + per_var["cohort_single"]
    n_coh = a["cohorts"].get("none", 0) + a["cohorts"].get("single", 0)
    _card(ws, r, 10, "COHORT CONTRACTS", f"{max(0, n_coh - coh_bad)}/{n_coh}",
          "no-criteria null · 1-criterion equality",
          _rate_color((n_coh - coh_bad) / n_coh if n_coh else None))
    r += 4
    r = _section(ws, r, f"AI quality — per-criterion LLM audit "
                        f"({a['n_crit_verdicts']} criterion verdicts on {a['judged_cands']} "
                        f"candidates: ranks 1/mid/last per variation)")

    def frac(cnt, good):
        tot = sum(cnt.values())
        return (cnt.get(good, 0) / tot) if tot else None

    _card(ws, r, 1, "REASONING CORRECT", "—" if not cr else f"{frac(cr, 'correct'):.0%}",
          f"per criterion · wrong: {cr.get('wrong', 0)} · partly: {cr.get('partly', 0)} "
          f"→ Criterion Verdicts sheet", _rate_color(frac(cr, "correct"), 0.9, 0.75))
    _card(ws, r, 4, "SCORE AGREEMENT", "—" if not cs else f"{frac(cs, 'agree'):.0%}",
          f"judge re-scored each criterion · too_high: {cs.get('too_high', 0)} · "
          f"too_low: {cs.get('too_low', 0)}", _rate_color(frac(cs, "agree"), 0.9, 0.75))
    _card(ws, r, 7, "AVG SCORE GAP", "—" if a["avg_gap"] is None else f"{a['avg_gap']:.1f}",
          f"|judge − AI| per criterion · p90 {a['p90_gap'] if a['p90_gap'] is not None else '—'} "
          f"(agree band ±{config.SOURCING_JUDGE_GAP})",
          GREEN_T if (a["avg_gap"] or 99) <= 10 else AMBER_T if (a["avg_gap"] or 99) <= 18 else RED_T)
    _card(ws, r, 10, "OVERALL SENSIBLE", "—" if not sens else f"{frac(sens, 'yes'):.0%}",
          f"no: {sens.get('no', 0)} · borderline: {sens.get('borderline', 0)}",
          _rate_color(frac(sens, "yes"), 0.9, 0.75))
    r += 4
    r = _section(ws, r, "Top failing checks")
    top = a["fails_by_check"].most_common(8)
    ws.cell(row=r, column=1, value="issue").font = Font(bold=True)
    ws.cell(row=r, column=4, value="failures").font = Font(bold=True)
    for i, (name, cnt) in enumerate(top, 1):
        ws.merge_cells(start_row=r + i, start_column=1, end_row=r + i, end_column=3)
        ws.cell(row=r + i, column=1, value=_check_label(name))
        ws.cell(row=r + i, column=4, value=cnt).font = Font(bold=True, color=RED_T)
    if not top:
        ws.merge_cells(start_row=r + 1, start_column=1, end_row=r + 1, end_column=4)
        c = ws.cell(row=r + 1, column=1, value="No deterministic failures 🎉")
        c.font = Font(bold=True, color=GREEN_T)
    # score histogram (table + bar chart) on the right of the section
    hist_col = 7
    ws.cell(row=r, column=hist_col, value="matchScore distribution").font = Font(bold=True)
    buckets = [(f"{lo}-{lo+9}" if lo < 100 else "100", lo) for lo in range(0, 101, 10)]
    counts = Counter()
    for s in a["scores"]:
        counts[min(int(s) // 10 * 10, 100)] += 1
    for i, (label, lo) in enumerate(buckets, 1):
        ws.cell(row=r + i, column=hist_col, value=label)
        ws.cell(row=r + i, column=hist_col + 1, value=counts.get(lo, 0))
    try:
        chart = BarChart()
        chart.type, chart.style, chart.title = "col", 10, "matchScore histogram"
        chart.y_axis.title, chart.x_axis.title = "candidates", "score band"
        data = Reference(ws, min_col=hist_col + 1, min_row=r, max_row=r + len(buckets))
        cats = Reference(ws, min_col=hist_col, min_row=r + 1, max_row=r + len(buckets))
        chart.add_data(data, titles_from_data=False)
        chart.set_categories(cats)
        chart.legend = None
        chart.width, chart.height = 16, 7.5
        ws.add_chart(chart, f"{get_column_letter(hist_col + 3)}{r}")
    except Exception:  # noqa: BLE001 — a chart must never sink the workbook
        pass
    r += max(len(top), len(buckets)) + 3
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=14)
    ws.cell(row=r, column=1, value=("How to read this: the coverage map lists every verification with its scope and "
                                    "result. Deterministic rows are objective — any red is a real defect (Failures "
                                    "sheet has the exact candidates). Judge rows are the AI-vs-AI audit — open "
                                    "Criterion Verdicts to see, per criterion, the system's score+reasoning next to "
                                    "the judge's own score+note, then spot-check against the Candidates sheet."))
    ws.cell(row=r, column=1).font = Font(italic=True, size=9, color="808080")
    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True)
    ws.row_dimensions[r].height = 28


_LEGEND = [
    ("HOW TO READ THIS WORKBOOK", ""),
    ("1. Dashboard", "Start here. The coverage map lists every verification with method, scope and a "
                     "colored result. Green = verified clean. Red = real defect. Amber = needs a look."),
    ("2. Variations", "One row per test case. Filter 'All checks passed?' = FAIL to find problems, then "
                      "look at 'What failed' and jump to the Failures sheet for the exact detail."),
    ("3. Failures", "Only what broke: one row per failed check instance, with the exact candidate/page."),
    ("4. Criterion Verdicts", "THE criteria-accuracy sheet: for every judged candidate, each criterion's "
                              "AI score + AI reasoning sits next to the judge's own score + evidence note. "
                              "Red 'Judge on reasoning' = the AI reasoning claims something not in the profile."),
    ("5. Judge Samples", "Candidate-level judge roll-up (worst reasoning verdict, score agreement, gap) + "
                         "a one-line judge summary."),
    ("6. Candidates", "Every fetched candidate: AI score next to the recomputed weighted average "
                      "('Expected') proving the formula, plus per-criterion scores and relevance ticks."),
    ("7. Cases", "What we asked: every input query/JD, its criteria and weights."),
    ("", ""),
    ("COLOR KEY", ""),
    ("Green", "verified / passed"),
    ("Red", "objective defect (deterministic) or judge says wrong/hallucinated"),
    ("Amber", "borderline / needs human eyes (judge disagreement, low relevance, partial run)"),
    ("Grey", "not applicable (e.g. no candidates to score)"),
    ("", ""),
    ("TERMS", ""),
    ("Criteria set", "full = 3-6 weighted criteria · single = exactly 1 · none = no criteria (scoring must be off)"),
    ("Input type", "short = 1-3 line query · brief = paragraph brief · jd_excerpt = ~900-char JD paste "
                   "(the 1000-char cap) · jd_overlimit = multi-page JD that MUST get the documented 400"),
    ("AI score / matchScore", "0-100 overall fit the system returned for a candidate"),
    ("Expected (Σs·w/Σw)", "the weighted average of per-criterion scores — matchScore must equal it (±2)"),
    ("Judge", "Sonnet-5 auditor that re-reads the candidate profile and independently re-scores each "
              "criterion; 'agree' means within ±15 of the AI's per-criterion score"),
    ("Names hiring co?", "case opens with 'Acme is hiring…' — the phrasing that currently triggers the "
                         "hiring-company-as-filter bug (kept as its regression cohort)"),
]


def _sheet_legend(wb):
    ws = wb.create_sheet("Read Me")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 130
    ws.merge_cells("A1:B1")
    ws["A1"] = "Read Me — what every sheet and column means"
    ws["A1"].font = Font(bold=True, size=14, color="FFFFFFFF")
    ws["A1"].fill = _fill(NAVY)
    r = 3
    for k, v in _LEGEND:
        a_cell = ws.cell(row=r, column=1, value=k)
        b_cell = ws.cell(row=r, column=2, value=v)
        if v == "" and k:
            a_cell.font = Font(bold=True, size=12, color="FFFFFFFF")
            a_cell.fill = _fill(BLUE)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        else:
            a_cell.font = Font(bold=True, size=10)
            b_cell.alignment = Alignment(wrap_text=True, vertical="top")
            if k in ("Green", "Red", "Amber", "Grey"):
                a_cell.fill = _fill({"Green": GREEN, "Red": RED, "Amber": AMBER, "Grey": GREY}[k])
        r += 1
    return ws


def build(rows, run_id):
    """Build the workbook from variation-result rows; returns the xlsx path."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    out = os.path.join(config.OUTPUT_DIR, f"sourcing_scale_{run_id}.xlsx")
    a = _aggregate(rows)
    wb = Workbook()
    _sheet_dashboard(wb.active, a, run_id)
    _sheet_legend(wb)
    _sheet_variations(wb, rows)
    _sheet_failures(wb, rows)
    _sheet_criterion_verdicts(wb, rows)
    _sheet_judge(wb, rows)
    _sheet_candidates(wb, rows)
    _sheet_cases(wb, rows)
    wb.save(out)
    return out

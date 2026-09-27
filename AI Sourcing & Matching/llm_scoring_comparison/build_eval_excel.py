#!/usr/bin/env python3
"""
Build a rich Excel comparison report from llm_eval_report.json
Sheets:
  1. Summary      — aggregate stats + per-job breakdown
  2. Scores       — all candidates, dimensions, formula score, LLM score, hard-rule explanation
  3. Reasoning    — side-by-side reasoning text per dimension
  4. Outliers     — only the flagged candidates (diff > threshold)
"""

import json
import os

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

WORKSPACE   = os.path.dirname(os.path.abspath(__file__))
JSON_PATH   = os.path.join(WORKSPACE, "llm_eval_report.json")
OUTPUT_PATH = os.path.join(WORKSPACE, "llm_eval_report.xlsx")

FLAG_OVERALL = 10
FLAG_DIM     = 15

KNOWN_LABELS = {"Excellent", "Strong", "Good", "Partial", "Weak"}
DIM_KEYS     = ["fn", "sk", "lv", "tr", "dm", "lc"]
DIM_NAMES    = {"fn": "Function", "sk": "Skills", "lv": "Level",
                "tr": "Trajectory", "dm": "Domain", "lc": "Location"}
DIM_WEIGHTS  = {"fn": 25, "sk": 25, "lv": 20, "tr": 15, "dm": 10, "lc": 5}

# Colour palette
C_HDR_DARK   = "1F3864"
C_HDR_MID    = "2E5AA8"
C_HDR_LIGHT  = "D6E4F7"
C_WHITE      = "FFFFFF"
C_GREY       = "F5F5F5"
C_ALT        = "EEF4FB"

C_GREEN_F    = "C6EFCE"; C_GREEN_T  = "276221"
C_AMBER_F    = "FFEB9C"; C_AMBER_T  = "9C6500"
C_RED_F      = "FFC7CE"; C_RED_T    = "9C0006"
C_BLUE_F     = "BDD7EE"; C_BLUE_T   = "1F3864"
C_ORANGE_F   = "FCE4D6"; C_ORANGE_T = "833C00"

THIN  = Side(style="thin",   color="BFBFBF")
MED   = Side(style="medium", color="8E8E8E")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_label(raw):
    if not raw:
        return "N/A"
    first = str(raw).strip().split()[0].rstrip(".,;:")
    return first if first in KNOWN_LABELS else str(raw)[:15]

def label_style(label):
    label = clean_label(label)
    if label == "Excellent": return C_GREEN_F, C_GREEN_T
    if label == "Strong":    return C_BLUE_F,  C_BLUE_T
    if label == "Good":      return C_AMBER_F, C_AMBER_T
    if label in ("Partial", "Weak"): return C_RED_F, C_RED_T
    return C_GREY, "000000"

def diff_fill(diff, threshold=FLAG_DIM):
    if diff is None: return None
    ad = abs(diff)
    if ad > threshold: return C_RED_F
    if ad > 5:         return C_AMBER_F
    return C_GREEN_F

def cell(ws, row, col, value="", bold=False, italic=False,
         bg=None, fg="000000", align="left", wrap=False,
         border=None, size=9):
    c = ws.cell(row=row, column=col, value=value)
    c.font      = Font(bold=bold, italic=italic, color=fg, size=size)
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    if bg:
        c.fill = PatternFill(fill_type="solid", fgColor=bg)
    if border:
        c.border = border
    return c

def hdr(ws, row, col, value, span=1, bg=C_HDR_DARK, fg=C_WHITE,
        align="center", size=9, bold=True):
    c = cell(ws, row, col, value, bold=bold, bg=bg, fg=fg,
             align=align, border=BORDER, size=size)
    if span > 1:
        ws.merge_cells(start_row=row, start_column=col,
                       end_row=row,   end_column=col + span - 1)
    return c

def col_w(ws, col, width):
    ws.column_dimensions[get_column_letter(col)].width = width

def score_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("s")
    return None

def reason_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("r", "")
    return ""

def formula_score(sn):
    """Recalculate overall from dim scores using production formula."""
    vals = {dk: score_val(sn, dk) for dk in DIM_KEYS}
    if any(v is None for v in vals.values()):
        return None
    return round(sum(vals[dk] * DIM_WEIGHTS[dk] for dk in DIM_KEYS) / 100, 1)

def get_formula_delta(r):
    """Get formula_delta from record — prefer stored value, fall back to recalculation."""
    stored = r.get("overall", {}).get("formula_delta")
    if stored is not None:
        return stored
    fs  = formula_score(r.get("sonnet_raw"))
    sno = r.get("overall", {}).get("sonnet")
    if fs is not None and sno is not None:
        return round(sno - fs, 1)
    return None

def get_hard_rule(r):
    """Get hard_rule explanation from record — prefer stored value, fall back to recalculation."""
    stored = r.get("overall", {}).get("hard_rule")
    if stored:
        return stored
    sn  = r.get("sonnet_raw")
    fs  = formula_score(sn)
    sno = r.get("overall", {}).get("sonnet")
    return hard_rule_reason(sn, fs, sno)

def hard_rule_reason(sn, formula, llm_o):
    if formula is None or llm_o is None:
        return ""
    diff = round(llm_o - formula, 1)
    if abs(diff) <= 5:
        return "No cap applied — LLM overall ≈ formula"
    reasons = []
    if llm_o <= 30 and formula > 50:
        reasons.append("Cap at 30: missing required credential for regulated role (e.g. CPA, license)")
    elif llm_o <= 55 and formula > 55:
        reasons.append("Cap at 55: core function mismatch OR seniority gap 2+ levels")
    elif llm_o <= 75 and formula > 75:
        reasons.append("Cap at 75: missing must-have skill(s)")
    for dk in ["sk", "fn", "lv"]:
        r_text = reason_val(sn, dk)
        for kw in ["CPA", "license", "credential", "certification",
                   "must-have", "cap", "regulated", "required"]:
            if kw.lower() in r_text.lower():
                reasons.append(f"Evidence in {DIM_NAMES[dk]} reasoning: '{kw}' mentioned")
                break
    if not reasons:
        reasons.append(f"LLM self-corrected (formula={formula}, returned={llm_o}, Δ={diff:+.1f})")
    return " | ".join(dict.fromkeys(reasons))

# ── Sheet 1: Summary ──────────────────────────────────────────────────────────

def build_summary(wb, results, anthropic_usage=None):
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:N1")
    c = ws["A1"]
    c.value = "LLM Scoring Heads-On Collision — Evaluation Report"
    c.font  = Font(bold=True, size=16, color=C_WHITE)
    c.fill  = PatternFill(fill_type="solid", fgColor=C_HDR_DARK)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells("A2:N2")
    c = ws["A2"]
    c.value = "Claude Sonnet 4.5 (direct Anthropic call)  vs  Web App LLM output  |  Formula score = (fn×25 + sk×25 + lv×20 + tr×15 + dm×10 + lc×5) / 100"
    c.font  = Font(italic=True, size=10, color=C_HDR_DARK)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 18

    row = 4
    hdr(ws, row, 1, "AGGREGATE STATISTICS", span=4, size=11)
    row += 1

    total      = len(results)
    lm         = sum(1 for r in results if clean_label(r['label']['sonnet']) == r['label']['webapp'])
    diffs_ov   = [abs(r['overall'].get('diff_s_vs_w', r['overall'].get('diff'))) for r in results if r['overall'].get('diff_s_vs_w', r['overall'].get('diff')) is not None]
    avg_diff   = round(sum(diffs_ov)/len(diffs_ov), 1) if diffs_ov else 0
    max_diff   = max(diffs_ov) if diffs_ov else 0
    max_cand   = next((r['candidate'] for r in results
                       if r['overall'].get('diff_s_vs_w', r['overall'].get('diff')) is not None
                       and abs(r['overall'].get('diff_s_vs_w', r['overall'].get('diff'))) == max_diff), "")
    hard_caps  = sum(1 for r in results
                     if r['sonnet_raw']
                     and formula_score(r['sonnet_raw']) is not None
                     and r['overall']['sonnet'] is not None
                     and abs(r['overall']['sonnet'] - formula_score(r['sonnet_raw'])) > 5)
    dim_flags  = sum(1 for r in results for dk in DIM_KEYS
                     if score_val(r['sonnet_raw'], dk) is not None
                     and score_val(r['webapp_raw'], dk) is not None
                     and abs(score_val(r['sonnet_raw'], dk)
                             - score_val(r['webapp_raw'], dk)) > FLAG_DIM)

    stats = [
        ("Jobs processed",                           len(set(r['job'] for r in results))),
        ("Total candidates compared",                total),
        ("Avg |overall Sonnet vs WebApp| diff",      f"{avg_diff} pts"),
        ("Max |overall| diff",                       f"{max_diff} pts  ({max_cand})"),
        ("Label match rate  (Sonnet vs WebApp)",     f"{lm} / {total}  ({100*lm//total if total else 0}%)"),
        ("Candidates with Sonnet hard-rule cap",     f"{hard_caps}  (formula vs LLM o > ±5)"),
        (f"Overall flags ⚠ (Sonnet vs WebApp > ±{FLAG_OVERALL})",
         f"{sum(1 for d in diffs_ov if d > FLAG_OVERALL)} candidates"),
        (f"Dimension flags ⚠ (diff > ±{FLAG_DIM})", f"{dim_flags} instances"),
    ]
    for lbl, val in stats:
        cell(ws, row, 1, lbl, bold=True, bg=C_HDR_LIGHT, border=BORDER)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        cell(ws, row, 3, val, bg=C_WHITE, border=BORDER)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
        ws.row_dimensions[row].height = 17
        row += 1

    # Optional: token usage from llm_scoring_eval (direct Anthropic calls only)
    if anthropic_usage and not anthropic_usage.get("skipped"):
        row += 1
        hdr(ws, row, 1, "ANTHROPIC TOKEN USAGE (direct Sonnet API only)", span=4, size=11)
        row += 1
        tot = anthropic_usage.get("totals") or {}
        tin = tot.get("input_tokens", "—")
        tout = tot.get("output_tokens", "—")
        simple = anthropic_usage.get("approx_billable_total_tokens_note")
        if simple is None and tin not in ("—", None) and tout not in ("—", None):
            simple = int(tin) + int(tout)
        u_stats = [
            ("Model",                           anthropic_usage.get("model", "")),
            ("Successful scoring parses",       anthropic_usage.get("successful_scoring_calls", "")),
            ("Failed or empty parse",           anthropic_usage.get("failed_or_empty_parse", "")),
            ("Calls with usage metadata",       anthropic_usage.get("calls_with_usage_recorded", "")),
            ("Sum input_tokens",                tin),
            ("Sum output_tokens",               tout),
            ("Input + output (simple sum)",     simple if simple is not None else "—"),
        ]
        for lbl, val in u_stats:
            cell(ws, row, 1, lbl, bold=True, bg=C_HDR_LIGHT, border=BORDER)
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            cell(ws, row, 3, val, bg=C_WHITE, border=BORDER)
            ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
            ws.row_dimensions[row].height = 17
            row += 1

    row += 1
    hdr(ws, row, 1, "PER-JOB BREAKDOWN", span=9, size=11)
    row += 1
    for ci, h in enumerate(["Job","Cands","Label Match","%",
                             "Avg |Diff|","Max |Diff|","Outlier","Hard Caps","Dim Flags ⚠"], 1):
        hdr(ws, row, ci, h, bg=C_HDR_MID, size=9)
    row += 1

    for alt, job in enumerate(list(dict.fromkeys(r['job'] for r in results))):
        jr    = [r for r in results if r['job'] == job]
        jt    = len(jr)
        jlm   = sum(1 for r in jr if clean_label(r['label']['sonnet']) == r['label']['webapp'])
        jd    = [abs(r['overall'].get('diff_s_vs_w', r['overall'].get('diff'))) for r in jr if r['overall'].get('diff_s_vs_w', r['overall'].get('diff')) is not None]
        javg  = round(sum(jd)/len(jd), 1) if jd else 0
        jmax  = max(jd) if jd else 0
        jout  = next((r['candidate'] for r in jr
                      if r['overall'].get('diff_s_vs_w', r['overall'].get('diff')) is not None and abs(r['overall'].get('diff_s_vs_w', r['overall'].get('diff'))) == jmax), "")
        jhc   = sum(1 for r in jr
                    if r['sonnet_raw']
                    and formula_score(r['sonnet_raw']) is not None
                    and r['overall']['sonnet'] is not None
                    and abs(r['overall']['sonnet'] - formula_score(r['sonnet_raw'])) > 5)
        jdf   = sum(1 for r in jr for dk in DIM_KEYS
                    if score_val(r['sonnet_raw'], dk) is not None
                    and score_val(r['webapp_raw'], dk) is not None
                    and abs(score_val(r['sonnet_raw'], dk)
                            - score_val(r['webapp_raw'], dk)) > FLAG_DIM)
        bg = C_GREY if alt % 2 == 0 else C_WHITE
        for ci, v in enumerate([job,jt,jlm,f"{100*jlm//jt if jt else 0}%",
                                 javg,jmax,jout,jhc,jdf], 1):
            cell(ws, row, ci, v, bg=bg, border=BORDER)
        ws.row_dimensions[row].height = 16
        row += 1

    col_w(ws, 1, 36); col_w(ws, 7, 26)
    for c in range(2, 10): col_w(ws, c, 15)

# ── Sheet 2: Scores ───────────────────────────────────────────────────────────

def build_scores(wb, results):
    ws = wb.create_sheet("Scores")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A4"

    # Row 1 — group headers
    hdr(ws, 1, 1, "Candidate Info", span=3, bg=C_HDR_DARK)
    col = 4
    for dk in DIM_KEYS:
        hdr(ws, 1, col, DIM_NAMES[dk], span=3, bg=C_HDR_MID)
        col += 3
    hdr(ws, 1, col, "Formula Score", span=1, bg=C_HDR_DARK); col += 1
    hdr(ws, 1, col, "Sonnet Overall", span=3, bg=C_HDR_DARK); col += 3
    hdr(ws, 1, col, "WebApp Overall", span=1, bg=C_HDR_MID);  col += 1
    hdr(ws, 1, col, "S vs W Diff",    span=1, bg=C_HDR_DARK); col += 1
    hdr(ws, 1, col, "Label",          span=3, bg=C_HDR_MID)

    # Row 2 — column labels
    hdrs2 = ["Job", "Candidate", "ID"]
    for _ in DIM_KEYS:
        hdrs2 += ["Sonnet", "WebApp", "Diff"]
    hdrs2 += ["Formula"]                          # formula
    hdrs2 += ["LLM o", "Formula Δ", "Hard Rule Explanation"]  # sonnet overall block
    hdrs2 += ["WebApp o"]                         # webapp overall
    hdrs2 += ["Diff"]                             # s vs w overall diff
    hdrs2 += ["Sonnet", "WebApp", "Match"]        # label

    for ci, h in enumerate(hdrs2, 1):
        hdr(ws, 2, ci, h, bg=C_HDR_LIGHT, fg=C_HDR_DARK, size=9)

    ws.row_dimensions[1].height = 18
    ws.row_dimensions[2].height = 18

    for ri, r in enumerate(results):
        row = ri + 3
        bg  = C_GREY if ri % 2 == 0 else C_WHITE
        sn  = r['sonnet_raw']
        wa  = r['webapp_raw']
        sn_l = clean_label(r['label']['sonnet'])
        wa_l = r['label']['webapp']

        col = 1
        cell(ws, row, col, r['job'],       bg=bg, border=BORDER); col += 1
        cell(ws, row, col, r['candidate'], bg=bg, border=BORDER, bold=True); col += 1
        cell(ws, row, col, r['cid'],       bg=bg, border=BORDER); col += 1

        # Dimensions
        for dk in DIM_KEYS:
            sn_s = score_val(sn, dk)
            wa_s = score_val(wa, dk)
            d    = (sn_s - wa_s) if (sn_s is not None and wa_s is not None) else None
            df   = diff_fill(d)
            cell(ws, row, col, sn_s, bg=bg,      border=BORDER, align="center"); col += 1
            cell(ws, row, col, wa_s, bg=bg,      border=BORDER, align="center"); col += 1
            cell(ws, row, col, d,   bg=df or bg, border=BORDER, align="center",
                 bold=(df==C_RED_F)); col += 1

        # Formula score
        fs = formula_score(sn)
        cell(ws, row, col, fs, bg=C_BLUE_F, fg=C_BLUE_T,
             border=BORDER, align="center", bold=True); col += 1

        # Sonnet overall block
        sn_o = r['overall']['sonnet']
        fd   = get_formula_delta(r)
        hr   = get_hard_rule(r)
        hr_bg = C_RED_F if (fd is not None and abs(fd) > 5) else bg
        cell(ws, row, col, sn_o, bg=bg, border=BORDER, align="center", bold=True); col += 1
        cell(ws, row, col, fd,   bg=hr_bg, border=BORDER, align="center",
             bold=(hr_bg == C_RED_F)); col += 1
        cell(ws, row, col, hr,   bg=hr_bg, border=BORDER, wrap=True, italic=True,
             size=8); col += 1

        # WebApp overall
        wa_o = r['overall']['webapp']
        cell(ws, row, col, wa_o, bg=bg, border=BORDER, align="center"); col += 1

        # Sonnet vs WebApp overall diff
        ov_diff = r['overall'].get('diff_s_vs_w', r['overall'].get('diff'))
        ovdf    = diff_fill(ov_diff, FLAG_OVERALL)
        cell(ws, row, col, ov_diff, bg=ovdf or bg, border=BORDER, align="center",
             bold=(ovdf==C_RED_F)); col += 1

        # Labels
        lf_s, lt_s = label_style(sn_l)
        lf_w, lt_w = label_style(wa_l)
        match_bg   = C_GREEN_F if sn_l == wa_l else C_RED_F
        cell(ws, row, col, sn_l, bg=lf_s, fg=lt_s, border=BORDER, align="center"); col += 1
        cell(ws, row, col, wa_l, bg=lf_w, fg=lt_w, border=BORDER, align="center"); col += 1
        cell(ws, row, col, "✓" if sn_l == wa_l else "✗",
             bg=match_bg, border=BORDER, align="center", bold=True); col += 1

        ws.row_dimensions[row].height = 42

    # Column widths
    col_w(ws, 1, 34); col_w(ws, 2, 22); col_w(ws, 3, 12)
    base = 4
    for _ in DIM_KEYS:
        col_w(ws, base, 8); col_w(ws, base+1, 8); col_w(ws, base+2, 8)
        base += 3
    col_w(ws, base, 10)          # formula
    col_w(ws, base+1, 8)         # LLM o
    col_w(ws, base+2, 10)        # formula Δ
    col_w(ws, base+3, 55)        # hard rule explanation
    col_w(ws, base+4, 10)        # webapp o
    col_w(ws, base+5, 10)        # s vs w diff
    col_w(ws, base+6, 12)        # label sonnet
    col_w(ws, base+7, 12)        # label webapp
    col_w(ws, base+8, 8)         # match

# ── Sheet 3: Reasoning ────────────────────────────────────────────────────────

def build_reasoning(wb, results):
    ws = wb.create_sheet("Reasoning")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"

    for ci, h in enumerate(["Job","Candidate","Dimension",
                             "Sonnet 4.5 Reasoning","Web App Reasoning"], 1):
        hdr(ws, 1, ci, h, bg=C_HDR_DARK, size=10)
    ws.row_dimensions[1].height = 20

    row = 2
    for r in results:
        sn = r['sonnet_raw']
        wa = r['webapp_raw']
        for di, dk in enumerate(DIM_KEYS):
            bg = C_GREY if di % 2 == 0 else C_WHITE
            cell(ws, row, 1, r['job'],           bg=bg, border=BORDER)
            cell(ws, row, 2, r['candidate'],      bg=bg, border=BORDER, bold=True)
            cell(ws, row, 3, DIM_NAMES[dk],       bg=bg, border=BORDER, bold=True, align="center")
            cell(ws, row, 4, reason_val(sn, dk),  bg=bg, border=BORDER, wrap=True)
            cell(ws, row, 5, reason_val(wa, dk),  bg=bg, border=BORDER, wrap=True)
            ws.row_dimensions[row].height = 52
            row += 1

    col_w(ws, 1, 34); col_w(ws, 2, 22); col_w(ws, 3, 13)
    col_w(ws, 4, 70); col_w(ws, 5, 70)

# ── Sheet 4: Outliers ─────────────────────────────────────────────────────────

def build_outliers(wb, results):
    ws = wb.create_sheet("Outliers")
    ws.sheet_view.showGridLines = False

    # Hard rule caps section
    ws.merge_cells("A1:J1")
    c = ws["A1"]
    c.value = "⚠  Hard Rule Caps — Sonnet LLM o differs from formula by > 5 pts"
    c.font  = Font(bold=True, size=12, color=C_WHITE)
    c.fill  = PatternFill(fill_type="solid", fgColor="C00000")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24

    cap_hdrs = ["Job","Candidate","fn","sk","lv","tr","dm","lc",
                "Formula","Sonnet o","Δ (o−formula)","Hard Rule Explanation"]
    for ci, h in enumerate(cap_hdrs, 1):
        hdr(ws, 2, ci, h, bg=C_HDR_MID, size=9)
    ws.row_dimensions[2].height = 18

    capped = [r for r in results
              if r['sonnet_raw']
              and formula_score(r['sonnet_raw']) is not None
              and r['overall']['sonnet'] is not None
              and abs(r['overall']['sonnet'] - formula_score(r['sonnet_raw'])) > 5]
    capped.sort(key=lambda r: abs(r['overall']['sonnet'] - formula_score(r['sonnet_raw'])), reverse=True)

    for ri, r in enumerate(capped):
        row = ri + 3
        sn  = r['sonnet_raw']
        fs  = formula_score(sn)
        sn_o = r['overall']['sonnet']
        fd   = round(sn_o - fs, 1)
        hr   = hard_rule_reason(sn, fs, sn_o)

        fd  = get_formula_delta(r)
        hr  = get_hard_rule(r)
        cell(ws, row, 1, r['job'],       bg=C_ORANGE_F, fg=C_ORANGE_T, border=BORDER)
        cell(ws, row, 2, r['candidate'], bg=C_ORANGE_F, fg=C_ORANGE_T, border=BORDER, bold=True)
        for ci, dk in enumerate(DIM_KEYS, 3):
            cell(ws, row, ci, score_val(sn, dk), bg=C_ORANGE_F, border=BORDER, align="center")
        cell(ws, row, 9,  fs,   bg=C_BLUE_F,  fg=C_BLUE_T,  border=BORDER, align="center", bold=True)
        cell(ws, row, 10, sn_o, bg=C_RED_F,   fg=C_RED_T,   border=BORDER, align="center", bold=True)
        cell(ws, row, 11, fd,   bg=C_RED_F,   fg=C_RED_T,   border=BORDER, align="center", bold=True)
        cell(ws, row, 12, hr,   bg=C_ORANGE_F, border=BORDER, wrap=True, italic=True, size=8)
        ws.row_dimensions[row].height = 40
        row_end = ri + 3

    # Sonnet vs WebApp outliers section
    sep_row = (row_end + 3) if capped else 4
    ws.merge_cells(start_row=sep_row, start_column=1, end_row=sep_row, end_column=10)
    c = ws.cell(row=sep_row, column=1,
                value=f"⚠  Sonnet vs Web App Outliers — Overall diff > ±{FLAG_OVERALL} pts")
    c.font  = Font(bold=True, size=12, color=C_WHITE)
    c.fill  = PatternFill(fill_type="solid", fgColor="833C00")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[sep_row].height = 24

    ov_hdrs = ["Job","Candidate","Sonnet o","WebApp o","Diff",
               "Sonnet Label","WebApp Label","Match",
               "Formula","Δ Sonnet−Formula","Hard Rule"]
    for ci, h in enumerate(ov_hdrs, 1):
        hdr(ws, sep_row + 1, ci, h, bg=C_HDR_MID, size=9)
    ws.row_dimensions[sep_row + 1].height = 18

    flagged = [r for r in results
               if r['overall'].get('diff_s_vs_w', r['overall'].get('diff')) is not None
               and abs(r['overall'].get('diff_s_vs_w', r['overall'].get('diff'))) > FLAG_OVERALL]
    flagged.sort(key=lambda r: abs(r['overall'].get('diff_s_vs_w', r['overall'].get('diff'))), reverse=True)

    for ri, r in enumerate(flagged):
        row = sep_row + 2 + ri
        sn  = r['sonnet_raw']
        fs  = formula_score(sn)
        sn_o = r['overall']['sonnet']
        wa_o = r['overall']['webapp']
        fd   = round(sn_o - fs, 1) if (sn_o and fs) else None
        hr   = hard_rule_reason(sn, fs, sn_o)
        sn_l = clean_label(r['label']['sonnet'])
        wa_l = r['label']['webapp']
        lf_s, lt_s = label_style(sn_l)
        lf_w, lt_w = label_style(wa_l)
        match_bg   = C_GREEN_F if sn_l == wa_l else C_RED_F

        cell(ws, row, 1,  r['job'],       bg=C_RED_F, border=BORDER)
        cell(ws, row, 2,  r['candidate'], bg=C_RED_F, border=BORDER, bold=True)
        cell(ws, row, 3,  sn_o,           bg=C_RED_F, border=BORDER, align="center")
        cell(ws, row, 4,  wa_o,           bg=C_RED_F, border=BORDER, align="center")
        cell(ws, row, 5,  r['overall'].get('diff_s_vs_w', r['overall'].get('diff')), bg=C_RED_F, fg=C_RED_T,
             border=BORDER, align="center", bold=True)
        cell(ws, row, 6,  sn_l, bg=lf_s, fg=lt_s, border=BORDER, align="center")
        cell(ws, row, 7,  wa_l, bg=lf_w, fg=lt_w, border=BORDER, align="center")
        cell(ws, row, 8,  "✓" if sn_l == wa_l else "✗",
             bg=match_bg, border=BORDER, align="center", bold=True)
        cell(ws, row, 9,  fs,  bg=C_BLUE_F, fg=C_BLUE_T, border=BORDER, align="center", bold=True)
        cell(ws, row, 10, fd,  bg=C_ORANGE_F if fd and abs(fd)>5 else C_WHITE,
             border=BORDER, align="center")
        cell(ws, row, 11, hr,  bg=C_ORANGE_F if hr and "cap" in hr.lower() else C_WHITE,
             border=BORDER, wrap=True, italic=True, size=8)
        ws.row_dimensions[row].height = 40

    col_w(ws, 1, 36); col_w(ws, 2, 22); col_w(ws, 12, 55); col_w(ws, 11, 55)
    for c in range(3, 12): col_w(ws, c, 12)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(JSON_PATH) as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "records" in raw:
        results = raw["records"]
        anthropic_usage_meta = raw.get("anthropic_usage")
    elif isinstance(raw, list):
        results = raw
        anthropic_usage_meta = None
    else:
        raise SystemExit(f"Unexpected JSON shape in {JSON_PATH}")

    # Fix label pollution
    for r in results:
        r['label']['sonnet'] = clean_label(r['label']['sonnet'])

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    print("Building Summary...")
    build_summary(wb, results, anthropic_usage=anthropic_usage_meta)
    print("Building Scores...")
    build_scores(wb, results)
    print("Building Reasoning...")
    build_reasoning(wb, results)
    print("Building Outliers...")
    build_outliers(wb, results)

    wb.save(OUTPUT_PATH)
    print(f"\n✓ Saved: {OUTPUT_PATH}")
    print(f"  Sheets: Summary | Scores | Reasoning | Outliers")
    print(f"  Candidates: {len(results)}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Build a rich Excel report from llm_3way_final_report.json
Styled with colors, merged headers, and all scores inline per candidate.

Sheets:
  1. Summary      — aggregate stats, verdict, per-job breakdown
  2. Scores       — 3-way per-candidate scores with all 6 dims + overall in one row
  3. Reasoning    — side-by-side overall + dimension reasoning from all 3 models
  4. Outliers     — candidates where any model deviates > threshold from Sonnet
"""

import json
import os
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(WORKSPACE, "llm_3way_final_report.json")
OUTPUT_FILE = os.path.join(WORKSPACE, "llm_3way_final_report.xlsx")

DIM_KEYS = ["fn", "sk", "lv", "tr", "dm", "lc"]
DIM_NAMES = {"fn": "Function", "sk": "Skills", "lv": "Level",
             "tr": "Trajectory", "dm": "Domain", "lc": "Location"}
DIM_WEIGHTS = {"fn": 25, "sk": 25, "lv": 20, "tr": 15, "dm": 10, "lc": 5}

FLAG_OVERALL = 10
FLAG_DIM = 15
KNOWN_LABELS = {"Excellent", "Strong", "Good", "Partial", "Weak", "No Fit"}

# Colour palette
C_HDR_DARK = "1F3864"
C_HDR_MID = "2E5AA8"
C_HDR_LIGHT = "D6E4F7"
C_WHITE = "FFFFFF"
C_GREY = "F5F5F5"

C_GREEN_F = "C6EFCE"; C_GREEN_T = "276221"
C_AMBER_F = "FFEB9C"; C_AMBER_T = "9C6500"
C_RED_F = "FFC7CE"; C_RED_T = "9C0006"
C_BLUE_F = "BDD7EE"; C_BLUE_T = "1F3864"
C_ORANGE_F = "FCE4D6"; C_ORANGE_T = "833C00"
C_PURPLE_F = "E8D5F5"; C_PURPLE_T = "5B2C6F"
C_TEAL_F = "D1F2EB"; C_TEAL_T = "0E6655"

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def strip_html(text):
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def clean_label(raw):
    if not raw:
        return "N/A"
    s = str(raw).strip()
    for lab in ("No Fit", "Excellent", "Strong", "Good", "Partial", "Weak"):
        if s.lower().startswith(lab.lower()):
            return lab
    first = s.split()[0].rstrip(".,;:")
    return first if first in KNOWN_LABELS else s[:15]


def label_style(label):
    label = clean_label(label)
    if label == "Excellent":
        return C_GREEN_F, C_GREEN_T
    if label == "Strong":
        return C_BLUE_F, C_BLUE_T
    if label == "Good":
        return C_AMBER_F, C_AMBER_T
    if label in ("Partial", "Weak", "No Fit"):
        return C_RED_F, C_RED_T
    return C_GREY, "000000"


def diff_fill(diff, threshold=FLAG_DIM):
    if diff is None:
        return None
    ad = abs(diff)
    if ad > threshold:
        return C_RED_F
    if ad > 5:
        return C_AMBER_F
    return C_GREEN_F


def score_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("s")
    return None


def reason_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("r", "")
    return ""


def formula_score(d):
    if not d:
        return None
    vals = {dk: score_val(d, dk) for dk in DIM_KEYS}
    if any(v is None for v in vals.values()):
        return None
    return round(sum(vals[dk] * DIM_WEIGHTS[dk] for dk in DIM_KEYS) / 100, 1)


def cell(ws, row, col, value="", bold=False, italic=False,
         bg=None, fg="000000", align="left", wrap=False,
         border=None, size=9):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=bold, italic=italic, color=fg, size=size)
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
                       end_row=row, end_column=col + span - 1)
    return c


def col_w(ws, col, width):
    ws.column_dimensions[get_column_letter(col)].width = width


# ══════════════════════════════════════════════════════════════════════
# SHEET 1: Summary
# ══════════════════════════════════════════════════════════════════════

def build_summary(wb, data, records):
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:H1")
    c = ws["A1"]
    c.value = "3-Way LLM Scoring Head-On Collision — Final Report"
    c.font = Font(bold=True, size=16, color=C_WHITE)
    c.fill = PatternFill(fill_type="solid", fgColor=C_HDR_DARK)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:H2")
    c = ws["A2"]
    c.value = "Ground Truth: Claude Sonnet 4.5  |  Challenger A: DeepSeek (titans10)  |  Challenger B: Gemini (titans8)"
    c.font = Font(italic=True, size=10, color=C_HDR_DARK)
    c.alignment = Alignment(horizontal="center", vertical="center")

    summary = data["summary"]
    verdict = data.get("verdict", "N/A")

    row = 4
    hdr(ws, row, 1, "AGGREGATE STATISTICS", span=4, size=11)
    row += 1

    stats = [
        ("Jobs evaluated", summary["jobs"]),
        ("Total candidates compared", summary["total_candidates"]),
        ("", ""),
        ("", "DeepSeek", "Gemini"),
        ("Avg |Sonnet − X| overall diff", f"{summary['deepseek_avg_diff']:.1f} pts", f"{summary['gemini_avg_diff']:.1f} pts"),
        ("Max |Sonnet − X| overall diff", f"{summary['deepseek_max_diff']} pts", f"{summary['gemini_max_diff']} pts"),
        ("Label match rate vs Sonnet", f"{summary['deepseek_label_match_pct']:.1f}%", f"{summary['gemini_label_match_pct']:.1f}%"),
        ("Dimension flags ⚠ (>±15)", summary["deepseek_dim_flags"], summary["gemini_dim_flags"]),
    ]

    for item in stats:
        if len(item) == 2:
            cell(ws, row, 1, item[0], bold=True, bg=C_HDR_LIGHT, border=BORDER)
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            cell(ws, row, 3, item[1], bg=C_WHITE, border=BORDER)
            ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=4)
        elif len(item) == 3:
            cell(ws, row, 1, item[0], bold=True, bg=C_HDR_LIGHT, border=BORDER)
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            cell(ws, row, 3, item[1], bg=C_TEAL_F, fg=C_TEAL_T, border=BORDER, align="center")
            cell(ws, row, 4, item[2], bg=C_PURPLE_F, fg=C_PURPLE_T, border=BORDER, align="center")
        ws.row_dimensions[row].height = 18
        row += 1

    # Verdict box
    row += 1
    hdr(ws, row, 1, "VERDICT", span=4, size=12, bg="C00000")
    row += 1
    if verdict == "DeepSeek":
        verdict_text = f"DeepSeek is CLOSER to Sonnet (avg diff {summary['deepseek_avg_diff']:.1f} vs {summary['gemini_avg_diff']:.1f})"
        vbg, vfg = C_TEAL_F, C_TEAL_T
    elif verdict == "Gemini":
        verdict_text = f"Gemini is CLOSER to Sonnet (avg diff {summary['gemini_avg_diff']:.1f} vs {summary['deepseek_avg_diff']:.1f})"
        vbg, vfg = C_PURPLE_F, C_PURPLE_T
    else:
        verdict_text = f"TIE — both avg diff {summary['deepseek_avg_diff']:.1f}"
        vbg, vfg = C_AMBER_F, C_AMBER_T
    cell(ws, row, 1, verdict_text, bold=True, bg=vbg, fg=vfg, border=BORDER, size=12)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    ws.row_dimensions[row].height = 28

    # Per-job breakdown
    row += 2
    hdr(ws, row, 1, "PER-JOB BREAKDOWN", span=8, size=11)
    row += 1
    job_hdrs = ["Job", "Cands", "DS Avg Diff", "GM Avg Diff", "DS Label ✓", "GM Label ✓", "Winner"]
    for ci, h in enumerate(job_hdrs, 1):
        hdr(ws, row, ci, h, bg=C_HDR_MID, size=9)
    row += 1

    jobs_seen = []
    for r in records:
        if r["job"] not in jobs_seen:
            jobs_seen.append(r["job"])

    for alt, job in enumerate(jobs_seen):
        jr = [r for r in records if r["job"] == job]
        jn = len(jr)
        ds_diffs = [abs(r["overall"]["diff_sonnet_deepseek"]) for r in jr if r["overall"]["diff_sonnet_deepseek"] is not None]
        gm_diffs = [abs(r["overall"]["diff_sonnet_gemini"]) for r in jr if r["overall"]["diff_sonnet_gemini"] is not None]
        ds_avg = round(sum(ds_diffs) / len(ds_diffs), 1) if ds_diffs else 0
        gm_avg = round(sum(gm_diffs) / len(gm_diffs), 1) if gm_diffs else 0
        ds_lm = sum(1 for r in jr if r["label"]["sonnet"] == r["label"]["deepseek"])
        gm_lm = sum(1 for r in jr if r["label"]["sonnet"] == r["label"]["gemini"])
        winner = "DeepSeek" if ds_avg < gm_avg else ("Gemini" if gm_avg < ds_avg else "Tie")

        bg = C_GREY if alt % 2 == 0 else C_WHITE
        vals = [job, jn, f"{ds_avg}", f"{gm_avg}", f"{ds_lm}/{jn}", f"{gm_lm}/{jn}", winner]
        for ci, v in enumerate(vals, 1):
            cell(ws, row, ci, v, bg=bg, border=BORDER)
        ws.row_dimensions[row].height = 17
        row += 1

    col_w(ws, 1, 35); col_w(ws, 2, 8)
    for c in range(3, 9):
        col_w(ws, c, 14)


# ══════════════════════════════════════════════════════════════════════
# SHEET 2: Scores (all dims + overall in one row per candidate)
# ══════════════════════════════════════════════════════════════════════

def build_scores(wb, records):
    ws = wb.create_sheet("Scores")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A4"

    # Row 1 — group headers
    col = 1
    hdr(ws, 1, col, "Candidate Info", span=3, bg=C_HDR_DARK)
    col += 3

    for dk in DIM_KEYS:
        hdr(ws, 1, col, DIM_NAMES[dk], span=5, bg=C_HDR_MID)
        col += 5

    hdr(ws, 1, col, "Overall Score", span=5, bg=C_HDR_DARK)
    col += 5
    hdr(ws, 1, col, "Label", span=3, bg=C_HDR_MID)
    col += 3
    hdr(ws, 1, col, "Formula", span=1, bg=C_HDR_DARK)

    # Row 2 — sub-headers
    hdrs2 = ["Job", "Candidate", "ID"]
    for _ in DIM_KEYS:
        hdrs2 += ["Sonnet", "DeepSeek", "Δ (S-DS)", "Gemini", "Δ (S-GM)"]
    hdrs2 += ["Sonnet", "DeepSeek", "Δ (S-DS)", "Gemini", "Δ (S-GM)"]
    hdrs2 += ["Sonnet", "DS", "GM"]
    hdrs2 += ["Sonnet"]

    for ci, h in enumerate(hdrs2, 1):
        hdr(ws, 2, ci, h, bg=C_HDR_LIGHT, fg=C_HDR_DARK, size=8)

    ws.row_dimensions[1].height = 20
    ws.row_dimensions[2].height = 18

    for ri, r in enumerate(records):
        row = ri + 3
        bg = C_GREY if ri % 2 == 0 else C_WHITE

        sn = r.get("sonnet_raw") or {}
        ds = r.get("deepseek_raw") or {}
        gm = r.get("gemini_raw") or {}

        col = 1
        cell(ws, row, col, r["job"], bg=bg, border=BORDER); col += 1
        cell(ws, row, col, r["candidate"], bg=bg, border=BORDER, bold=True); col += 1
        cell(ws, row, col, r["cid"], bg=bg, border=BORDER); col += 1

        # Dimensions
        for dk in DIM_KEYS:
            sn_s = score_val(sn, dk)
            ds_s = score_val(ds, dk)
            gm_s = score_val(gm, dk)

            d_ds = (sn_s - ds_s) if (sn_s is not None and ds_s is not None) else None
            d_gm = (sn_s - gm_s) if (sn_s is not None and gm_s is not None) else None
            df_ds = diff_fill(d_ds)
            df_gm = diff_fill(d_gm)

            cell(ws, row, col, sn_s, bg=bg, border=BORDER, align="center"); col += 1
            cell(ws, row, col, ds_s, bg=C_TEAL_F if ds_s is not None else bg, border=BORDER, align="center"); col += 1
            cell(ws, row, col, d_ds, bg=df_ds or bg, border=BORDER, align="center",
                 bold=(df_ds == C_RED_F)); col += 1
            cell(ws, row, col, gm_s, bg=C_PURPLE_F if gm_s is not None else bg, border=BORDER, align="center"); col += 1
            cell(ws, row, col, d_gm, bg=df_gm or bg, border=BORDER, align="center",
                 bold=(df_gm == C_RED_F)); col += 1

        # Overall
        sn_o = r["overall"].get("sonnet")
        ds_o = r["overall"].get("deepseek")
        gm_o = r["overall"].get("gemini")
        d_sds = r["overall"].get("diff_sonnet_deepseek")
        d_sgm = r["overall"].get("diff_sonnet_gemini")
        ov_df_ds = diff_fill(d_sds, FLAG_OVERALL)
        ov_df_gm = diff_fill(d_sgm, FLAG_OVERALL)

        cell(ws, row, col, sn_o, bg=bg, border=BORDER, align="center", bold=True); col += 1
        cell(ws, row, col, ds_o, bg=C_TEAL_F, border=BORDER, align="center"); col += 1
        cell(ws, row, col, d_sds, bg=ov_df_ds or bg, border=BORDER, align="center",
             bold=(ov_df_ds == C_RED_F)); col += 1
        cell(ws, row, col, gm_o, bg=C_PURPLE_F, border=BORDER, align="center"); col += 1
        cell(ws, row, col, d_sgm, bg=ov_df_gm or bg, border=BORDER, align="center",
             bold=(ov_df_gm == C_RED_F)); col += 1

        # Labels
        sn_l = clean_label(r["label"].get("sonnet"))
        ds_l = clean_label(r["label"].get("deepseek"))
        gm_l = clean_label(r["label"].get("gemini"))

        lf_s, lt_s = label_style(sn_l)
        lf_d, lt_d = label_style(ds_l)
        lf_g, lt_g = label_style(gm_l)

        cell(ws, row, col, sn_l, bg=lf_s, fg=lt_s, border=BORDER, align="center"); col += 1
        cell(ws, row, col, ds_l, bg=lf_d, fg=lt_d, border=BORDER, align="center"); col += 1
        cell(ws, row, col, gm_l, bg=lf_g, fg=lt_g, border=BORDER, align="center"); col += 1

        # Formula
        fs = formula_score(sn)
        cell(ws, row, col, fs, bg=C_BLUE_F, fg=C_BLUE_T, border=BORDER, align="center", bold=True)

        ws.row_dimensions[row].height = 20

    # Column widths
    col_w(ws, 1, 32); col_w(ws, 2, 20); col_w(ws, 3, 10)
    base = 4
    for _ in DIM_KEYS:
        # Sonnet, DeepSeek, Δ(S-DS), Gemini, Δ(S-GM)
        col_w(ws, base, 7); col_w(ws, base+1, 9); col_w(ws, base+2, 8)
        col_w(ws, base+3, 8); col_w(ws, base+4, 8)
        base += 5
    # Overall: Sonnet, DeepSeek, Δ(S-DS), Gemini, Δ(S-GM)
    col_w(ws, base, 8); col_w(ws, base+1, 9); col_w(ws, base+2, 8)
    col_w(ws, base+3, 8); col_w(ws, base+4, 8)
    base += 5
    # Labels
    for j in range(3):
        col_w(ws, base + j, 10)
    base += 3
    # Formula
    col_w(ws, base, 9)


# ══════════════════════════════════════════════════════════════════════
# SHEET 3: Reasoning (3-way side by side)
# ══════════════════════════════════════════════════════════════════════

def build_reasoning(wb, records):
    ws = wb.create_sheet("Reasoning")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"

    headers = ["Job", "Candidate", "Dimension", "Sonnet 4.5", "DeepSeek", "Gemini"]
    for ci, h in enumerate(headers, 1):
        hdr(ws, 1, ci, h, bg=C_HDR_DARK, size=10)

    # Sub-header for OR reasoning
    hdr(ws, 2, 1, "", bg=C_HDR_LIGHT); hdr(ws, 2, 2, "", bg=C_HDR_LIGHT)
    hdr(ws, 2, 3, "incl. Overall Reasoning (OR)", bg=C_HDR_LIGHT, fg=C_HDR_DARK, size=8)
    hdr(ws, 2, 4, "", bg=C_HDR_LIGHT); hdr(ws, 2, 5, "", bg=C_HDR_LIGHT); hdr(ws, 2, 6, "", bg=C_HDR_LIGHT)

    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 16

    row = 3
    for r in records:
        sn = r.get("sonnet_raw") or {}
        ds = r.get("deepseek_raw") or {}
        gm = r.get("gemini_raw") or {}
        rs = r.get("reasoning", {})

        # Overall reasoning row first (highlighted)
        cell(ws, row, 1, r["job"], bg=C_ORANGE_F, fg=C_ORANGE_T, border=BORDER)
        cell(ws, row, 2, r["candidate"], bg=C_ORANGE_F, fg=C_ORANGE_T, border=BORDER, bold=True)
        cell(ws, row, 3, "OVERALL (or)", bg=C_ORANGE_F, fg=C_ORANGE_T, border=BORDER, bold=True, align="center")
        cell(ws, row, 4, strip_html(rs.get("sonnet_or", "")), bg=C_ORANGE_F, border=BORDER, wrap=True)
        cell(ws, row, 5, strip_html(rs.get("deepseek_or", "")), bg=C_ORANGE_F, border=BORDER, wrap=True)
        cell(ws, row, 6, strip_html(rs.get("gemini_or", "")), bg=C_ORANGE_F, border=BORDER, wrap=True)
        ws.row_dimensions[row].height = 55
        row += 1

        # Per-dimension reasoning
        for di, dk in enumerate(DIM_KEYS):
            bg = C_GREY if di % 2 == 0 else C_WHITE
            cell(ws, row, 1, r["job"], bg=bg, border=BORDER)
            cell(ws, row, 2, r["candidate"], bg=bg, border=BORDER)
            cell(ws, row, 3, DIM_NAMES[dk], bg=bg, border=BORDER, bold=True, align="center")
            cell(ws, row, 4, strip_html(reason_val(sn, dk)), bg=bg, border=BORDER, wrap=True)
            cell(ws, row, 5, strip_html(reason_val(ds, dk)), bg=bg, border=BORDER, wrap=True)
            cell(ws, row, 6, strip_html(reason_val(gm, dk)), bg=bg, border=BORDER, wrap=True)
            ws.row_dimensions[row].height = 48
            row += 1

    col_w(ws, 1, 32); col_w(ws, 2, 20); col_w(ws, 3, 14)
    col_w(ws, 4, 65); col_w(ws, 5, 65); col_w(ws, 6, 65)


# ══════════════════════════════════════════════════════════════════════
# SHEET 4: Outliers (biggest deviations from Sonnet)
# ══════════════════════════════════════════════════════════════════════

def build_outliers(wb, records):
    ws = wb.create_sheet("Outliers")
    ws.sheet_view.showGridLines = False

    # Title
    ws.merge_cells("A1:K1")
    c = ws["A1"]
    c.value = f"⚠  Outliers — Candidates where |Sonnet − Challenger| overall > ±{FLAG_OVERALL} pts"
    c.font = Font(bold=True, size=12, color=C_WHITE)
    c.fill = PatternFill(fill_type="solid", fgColor="C00000")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    out_hdrs = ["Job", "Candidate", "Sonnet", "DeepSeek", "Gemini",
                "Δ S-DS", "Δ S-GM", "Worst Δ", "Closer Model",
                "Sonnet Label", "Sonnet OR"]
    for ci, h in enumerate(out_hdrs, 1):
        hdr(ws, 2, ci, h, bg=C_HDR_MID, size=9)
    ws.row_dimensions[2].height = 18

    # Filter outliers
    outliers = []
    for r in records:
        d_ds = r["overall"].get("diff_sonnet_deepseek")
        d_gm = r["overall"].get("diff_sonnet_gemini")
        worst = max(abs(d_ds or 0), abs(d_gm or 0))
        if worst > FLAG_OVERALL:
            outliers.append((r, worst, d_ds, d_gm))

    outliers.sort(key=lambda x: x[1], reverse=True)

    for ri, (r, worst, d_ds, d_gm) in enumerate(outliers):
        row = ri + 3
        sn_o = r["overall"].get("sonnet")
        ds_o = r["overall"].get("deepseek")
        gm_o = r["overall"].get("gemini")

        closer = "DeepSeek" if abs(d_ds or 999) < abs(d_gm or 999) else (
            "Gemini" if abs(d_gm or 999) < abs(d_ds or 999) else "Tie")

        sn_l = clean_label(r["label"].get("sonnet"))
        sn_or = strip_html(r.get("reasoning", {}).get("sonnet_or", ""))

        cell(ws, row, 1, r["job"], bg=C_RED_F, border=BORDER)
        cell(ws, row, 2, r["candidate"], bg=C_RED_F, border=BORDER, bold=True)
        cell(ws, row, 3, sn_o, bg=C_RED_F, border=BORDER, align="center", bold=True)
        cell(ws, row, 4, ds_o, bg=C_TEAL_F, border=BORDER, align="center")
        cell(ws, row, 5, gm_o, bg=C_PURPLE_F, border=BORDER, align="center")
        cell(ws, row, 6, d_ds, bg=diff_fill(d_ds, FLAG_OVERALL) or C_WHITE, border=BORDER, align="center")
        cell(ws, row, 7, d_gm, bg=diff_fill(d_gm, FLAG_OVERALL) or C_WHITE, border=BORDER, align="center")
        cell(ws, row, 8, worst, bg=C_RED_F, fg=C_RED_T, border=BORDER, align="center", bold=True)
        cell(ws, row, 9, closer, bg=C_WHITE, border=BORDER, align="center", bold=True)
        lf, lt = label_style(sn_l)
        cell(ws, row, 10, sn_l, bg=lf, fg=lt, border=BORDER, align="center")
        cell(ws, row, 11, sn_or, bg=C_WHITE, border=BORDER, wrap=True, size=8)
        ws.row_dimensions[row].height = 40

    col_w(ws, 1, 32); col_w(ws, 2, 20)
    for c in range(3, 10):
        col_w(ws, c, 12)
    col_w(ws, 10, 12); col_w(ws, 11, 60)


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    with open(INPUT_FILE) as f:
        data = json.load(f)

    records = data["records"]

    wb = Workbook()
    wb.remove(wb.active)

    print("Building Summary...")
    build_summary(wb, data, records)
    print("Building Scores...")
    build_scores(wb, records)
    print("Building Reasoning...")
    build_reasoning(wb, records)
    print("Building Outliers...")
    build_outliers(wb, records)

    wb.save(OUTPUT_FILE)
    print(f"\n✓ Saved: {OUTPUT_FILE}")
    print(f"  Sheets: Summary | Scores | Reasoning | Outliers")
    print(f"  Candidates: {len(records)}")
    print(f"  Outliers (>±{FLAG_OVERALL} overall): {sum(1 for r in records if max(abs(r['overall'].get('diff_sonnet_deepseek') or 0), abs(r['overall'].get('diff_sonnet_gemini') or 0)) > FLAG_OVERALL)}")


if __name__ == "__main__":
    main()

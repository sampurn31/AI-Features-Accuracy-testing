#!/usr/bin/env python3
"""
Build Excel report for Direct LLM Eval (Sonnet vs Gemini)
==========================================================
Reads:  direct_eval_report_<prompt>.json
Writes: direct_eval_report_<prompt>.xlsx

CLI:
  python3 build_direct_excel.py --prompt old   # default
  python3 build_direct_excel.py --prompt new
"""

import json
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import (Alignment, Border, Font, PatternFill, Side)
from openpyxl.utils import get_column_letter

# ── Prompt tag ────────────────────────────────────────────────
_prompt_arg = "old"
if "--prompt" in sys.argv:
    _i = sys.argv.index("--prompt")
    if _i + 1 < len(sys.argv):
        _prompt_arg = sys.argv[_i + 1].lower()

PROMPT_TAG = "new" if _prompt_arg in ("new", "new_systemprompt") else "old"
WORKSPACE  = os.path.dirname(os.path.abspath(__file__))

JSON_IN  = os.path.join(WORKSPACE, f"direct_eval_report_{PROMPT_TAG}.json")
XLSX_OUT = os.path.join(WORKSPACE, f"direct_eval_report_{PROMPT_TAG}.xlsx")


def excel_path_for_prompt(prompt_tag):
    """Return xlsx path for old/new prompt tag (does not overwrite the other)."""
    tag = "new" if str(prompt_tag).lower() in ("new", "new_systemprompt") else "old"
    return os.path.join(WORKSPACE, f"direct_eval_report_{tag}.xlsx")


def build_excel_from_json(json_path, xlsx_path=None):
    """
    Build Excel from a direct_eval_report JSON file.
    Old and new prompt runs use separate json/xlsx paths — never shared.
    """
    if not os.path.isfile(json_path):
        raise FileNotFoundError(f"Input not found: {json_path}")

    with open(json_path) as f:
        data = json.load(f)

    if xlsx_path is None:
        tag = data.get("prompt_tag") or PROMPT_TAG
        xlsx_path = excel_path_for_prompt(tag)

    records = data.get("records", [])
    wb = Workbook()
    for s in list(wb.sheetnames):
        del wb[s]

    ws_scores = wb.create_sheet("Scores")
    ws_reason = wb.create_sheet("Reasoning")
    ws_summary = wb.create_sheet("Summary")

    build_scores_sheet(
        ws_scores, records, data.get("prompt_tag", "?"), data.get("prompt_file", "?")
    )
    build_reasoning_sheet(ws_reason, records)
    build_summary_sheet(ws_summary, data)

    wb.active = ws_scores
    wb.save(xlsx_path)
    return xlsx_path

# ── Colours ───────────────────────────────────────────────────
C_HEADER_DARK   = "1F3864"   # dark navy — title rows
C_HEADER_MED    = "2E4D8E"   # medium navy — section headers
C_SONNET        = "D6E4F7"   # light blue  — Sonnet cols
C_GEMINI        = "D9F0D9"   # light green — Gemini cols
C_DIFF          = "FFF2CC"   # pale yellow — diff col
C_FLAG          = "FF4444"   # red text    — big difference
C_MATCH_OK      = "C6EFCE"   # green bg    — label match
C_MATCH_FAIL    = "FFCCCC"   # red bg      — label mismatch
C_ALT_ROW       = "F2F2F2"   # light grey  — alternating row
C_OVERALL_ROW   = "E2EFDA"   # light green — overall / formula rows
C_WHITE         = "FFFFFF"

DIM_KEYS   = ["fn", "sk", "lv", "tr", "dm", "lc"]
DIM_LABELS = {"fn": "Function", "sk": "Skills",  "lv": "Level",
              "tr": "Trajectory", "dm": "Domain", "lc": "Location"}


def fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)


def font(bold=False, color="000000", size=10):
    return Font(bold=bold, color=color, size=size, name="Calibri")


def centre():
    return Alignment(horizontal="center", vertical="center", wrap_text=False)


def left():
    return Alignment(horizontal="left", vertical="center", wrap_text=False)


def wrap_left():
    return Alignment(horizontal="left", vertical="top", wrap_text=True)


THIN = Side(style="thin", color="BFBFBF")
MED  = Side(style="medium", color="8FAADC")


def thin_border():
    return Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def med_border():
    return Border(left=MED, right=MED, top=MED, bottom=MED)


def _set_cell(ws, row, col, value, bold=False, bg=None, align=None, color="000000", size=10):
    c = ws.cell(row=row, column=col)
    c.value = value
    c.font  = font(bold=bold, color=color, size=size)
    if bg:
        c.fill = fill(bg)
    c.alignment = align or left()
    c.border = thin_border()
    return c


def diff_color(val):
    """Return red font color if |diff| > 10, else black."""
    if val is None:
        return "000000"
    try:
        v = float(str(val).replace("+", ""))
        return "CC0000" if abs(v) > 10 else "000000"
    except Exception:
        return "000000"


def fmt_diff(sn, gm):
    if sn is None or gm is None:
        return "N/A"
    d = sn - gm
    if isinstance(d, float) and not d.is_integer():
        return f"{d:+.1f}"
    return f"{int(d):+d}"


# ──────────────────────────────────────────────────────────────
# SHEET 1: SCORES
# ──────────────────────────────────────────────────────────────

def build_scores_sheet(ws, records, prompt_tag, prompt_file):
    ws.title = "Scores"

    col_widths = [4, 22, 10, 10, 12, 10, 10, 12, 18, 18]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    row = 1
    # Title
    ws.merge_cells(f"A{row}:J{row}")
    c = ws.cell(row=row, column=1,
                value=f"Direct LLM Evaluation — Sonnet 4.5 vs Gemini 2.5 Flash Lite | Prompt: {prompt_tag.upper()} ({prompt_file})")
    c.font = font(bold=True, color=C_WHITE, size=12)
    c.fill = fill(C_HEADER_DARK)
    c.alignment = centre()
    row += 1

    # Sub-header: column groups
    for col, txt, bg in [
        (1, "#",          C_HEADER_MED),
        (2, "Candidate",  C_HEADER_MED),
        (3, "Sonnet",     C_SONNET),
        (4, "Gemini",     C_GEMINI),
        (5, "Δ (S-G)",    C_DIFF),
        (6, "Sonnet",     C_SONNET),
        (7, "Gemini",     C_GEMINI),
        (8, "Δ (S-G)",    C_DIFF),
        (9, "Sonnet Label", C_SONNET),
        (10, "Gemini Label", C_GEMINI),
    ]:
        _set_cell(ws, row, col, txt, bold=True, bg=bg, align=centre(),
                  color=C_WHITE if bg == C_HEADER_MED else "000000")
    row += 1

    # Dim group label row  (merged per dim)
    # We need a header for the dimension block above the per-dim scores
    # Format: #, Candidate, [dim 1..6 repeated as Sonnet|Gemini|Diff], Overall Sonnet, Overall Gemini, Diff, Label S, Label G
    # ---- For the scores sheet we do: one row per candidate
    # Columns: #, Name, fn_sn, fn_gm, fn_diff, sk_sn, sk_gm, sk_diff, ... lc_sn, lc_gm, lc_diff, formula_sn, formula_gm, overall_sn, overall_gm, overall_diff, label_sn, label_gm

    # Rebuild headers with full dim list
    # We need more columns. Let's redo the layout:
    # Col  1: #
    # Col  2: Candidate
    # Col  3..20: fn_sn, fn_gm, fn_diff, sk_sn, ... lc_sn, lc_gm, lc_diff   (6 dims × 3 = 18 cols)
    # Col 21..22: formula_sn, formula_gm
    # Col 23..25: overall_sn, overall_gm, overall_diff
    # Col 26..27: label_sn, label_gm
    # Total: 27 cols

    ws.delete_rows(row - 1, 2)   # remove the temp header we started
    row = 2

    # Adjust column widths for the full layout
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 24
    for i in range(3, 22):    # dim cols
        ws.column_dimensions[get_column_letter(i)].width = 7
    for i in range(22, 26):   # overall / formula
        ws.column_dimensions[get_column_letter(i)].width = 9
    for i in range(26, 29):   # labels
        ws.column_dimensions[get_column_letter(i)].width = 12

    # Row 2: merged dim group headers
    dim_start_col = 3
    for di, dk in enumerate(DIM_KEYS):
        sc = dim_start_col + di * 3
        ws.merge_cells(start_row=row, start_column=sc, end_row=row, end_column=sc+2)
        c = ws.cell(row=row, column=sc, value=f"{DIM_LABELS[dk]} ({dk})")
        c.font      = font(bold=True, color=C_WHITE, size=9)
        c.fill      = fill(C_HEADER_MED)
        c.alignment = centre()
        c.border    = med_border()

    for col, val, bg in [
        (21, "Formula Score", C_HEADER_MED),
        (22, "Formula Score", C_HEADER_MED),
        (23, "Overall",       C_HEADER_MED),
        (24, "Overall",       C_HEADER_MED),
        (25, "Δ Overall",     C_DIFF),
        (26, "Label",         C_HEADER_MED),
        (27, "Label",         C_HEADER_MED),
    ]:
        c = ws.cell(row=row, column=col, value=val)
        c.font      = font(bold=True, color=C_WHITE if bg == C_HEADER_MED else "000000", size=9)
        c.fill      = fill(bg)
        c.alignment = centre()
        c.border    = thin_border()
    row += 1

    # Row 3: sub-headers
    _set_cell(ws, row, 1, "#",         bold=True, bg=C_HEADER_DARK, align=centre(), color=C_WHITE)
    _set_cell(ws, row, 2, "Candidate", bold=True, bg=C_HEADER_DARK, align=centre(), color=C_WHITE)
    dim_start_col = 3
    for di, dk in enumerate(DIM_KEYS):
        sc = dim_start_col + di * 3
        for off, lbl, bg in [(0, "Sonnet", C_SONNET), (1, "Gemini", C_GEMINI), (2, "Δ(S-G)", C_DIFF)]:
            _set_cell(ws, row, sc + off, lbl, bold=True, bg=bg, align=centre(),
                      color="000000" if bg != C_HEADER_MED else C_WHITE)

    for col, lbl, bg in [
        (21, "Sonnet", C_SONNET),
        (22, "Gemini", C_GEMINI),
        (23, "Sonnet", C_SONNET),
        (24, "Gemini", C_GEMINI),
        (25, "Δ(S-G)", C_DIFF),
        (26, "Sonnet", C_SONNET),
        (27, "Gemini", C_GEMINI),
    ]:
        _set_cell(ws, row, col, lbl, bold=True, bg=bg, align=centre())
    row += 1

    # Data rows
    for ri, rec in enumerate(records):
        bg_row = C_WHITE if ri % 2 == 0 else C_ALT_ROW
        _set_cell(ws, row, 1, ri + 1, bg=bg_row, align=centre())
        _set_cell(ws, row, 2, rec["candidate"], bg=bg_row)

        sn_raw = rec.get("sonnet_raw") or {}
        gm_raw = rec.get("gemini_raw") or {}

        for di, dk in enumerate(DIM_KEYS):
            sc = 3 + di * 3
            sn_s = (sn_raw.get(dk) or {}).get("s")
            gm_s = (gm_raw.get(dk) or {}).get("s")
            dv   = fmt_diff(sn_s, gm_s)
            _set_cell(ws, row, sc,   sn_s if sn_s is not None else "N/A", bg=C_SONNET, align=centre())
            _set_cell(ws, row, sc+1, gm_s if gm_s is not None else "N/A", bg=C_GEMINI, align=centre())
            c = _set_cell(ws, row, sc+2, dv, bg=C_DIFF, align=centre())
            c.font = font(color=diff_color(dv))

        # Formula & overall
        fs = rec.get("formula_score", {})
        ov = rec.get("overall", {})
        sn_fs = fs.get("sonnet")
        gm_fs = fs.get("gemini")
        sn_o  = ov.get("sonnet")
        gm_o  = ov.get("gemini")
        o_dv  = fmt_diff(sn_o, gm_o)

        _set_cell(ws, row, 21, sn_fs if sn_fs is not None else "N/A", bg=C_SONNET, align=centre())
        _set_cell(ws, row, 22, gm_fs if gm_fs is not None else "N/A", bg=C_GEMINI, align=centre())
        _set_cell(ws, row, 23, sn_o  if sn_o  is not None else "N/A", bg=C_SONNET, align=centre())
        _set_cell(ws, row, 24, gm_o  if gm_o  is not None else "N/A", bg=C_GEMINI, align=centre())
        c = _set_cell(ws, row, 25, o_dv, bg=C_DIFF, align=centre())
        c.font = font(color=diff_color(o_dv))

        # Labels
        lbl = rec.get("label", {})
        sn_l = lbl.get("sonnet", "N/A")
        gm_l = lbl.get("gemini", "N/A")
        match = lbl.get("match", False)
        lbl_bg = C_MATCH_OK if match else C_MATCH_FAIL
        _set_cell(ws, row, 26, sn_l, bg=lbl_bg, align=centre())
        _set_cell(ws, row, 27, gm_l, bg=lbl_bg, align=centre())

        row += 1

    ws.freeze_panes = "C4"


# ──────────────────────────────────────────────────────────────
# SHEET 2: REASONING
# ──────────────────────────────────────────────────────────────

def build_reasoning_sheet(ws, records):
    ws.title = "Reasoning"

    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 55
    ws.column_dimensions["E"].width = 55
    ws.column_dimensions["F"].width = 55

    row = 1
    ws.merge_cells(f"A{row}:F{row}")
    c = ws.cell(row=row, column=1, value="Reasoning Comparison — Sonnet vs Gemini")
    c.font = font(bold=True, color=C_WHITE, size=12)
    c.fill = fill(C_HEADER_DARK)
    c.alignment = centre()
    row += 1

    for col, lbl, bg in [(1,"#",C_HEADER_MED),(2,"Candidate",C_HEADER_MED),
                          (3,"Dim",C_HEADER_MED),(4,"Sonnet Reasoning",C_SONNET),
                          (5,"Gemini Reasoning",C_GEMINI),(6,"Overall Reasoning (or)",C_DIFF)]:
        _set_cell(ws, row, col, lbl, bold=True,
                  bg=bg, align=centre(),
                  color=C_WHITE if bg == C_HEADER_MED else "000000")
    row += 1

    ws.row_dimensions[1].height = 20
    ws.row_dimensions[2].height = 20

    for ri, rec in enumerate(records):
        bg_row = C_WHITE if ri % 2 == 0 else C_ALT_ROW
        sn_raw = rec.get("sonnet_raw") or {}
        gm_raw = rec.get("gemini_raw") or {}
        sn_or  = (rec.get("overall") or {}).get("sonnet_or", "")
        gm_or  = (rec.get("overall") or {}).get("gemini_or", "")
        name   = rec.get("candidate", "")

        # One row per dim
        start_row = row
        for di, dk in enumerate(DIM_KEYS):
            sn_r = (sn_raw.get(dk) or {}).get("r", "")
            gm_r = (gm_raw.get(dk) or {}).get("r", "")

            if di == 0:
                _set_cell(ws, row, 1, ri + 1, bg=bg_row, align=centre())
                _set_cell(ws, row, 2, name, bg=bg_row)
            else:
                _set_cell(ws, row, 1, "", bg=bg_row)
                _set_cell(ws, row, 2, "", bg=bg_row)

            _set_cell(ws, row, 3, f"{DIM_LABELS[dk]} ({dk})", bg=bg_row, align=centre())
            c = ws.cell(row=row, column=4)
            c.value = sn_r
            c.fill  = fill(C_SONNET)
            c.font  = font(size=9)
            c.alignment = wrap_left()
            c.border = thin_border()

            c = ws.cell(row=row, column=5)
            c.value = gm_r
            c.fill  = fill(C_GEMINI)
            c.font  = font(size=9)
            c.alignment = wrap_left()
            c.border = thin_border()

            # Overall reasoning only on last dim row
            if di == len(DIM_KEYS) - 1 and (sn_or or gm_or):
                c = ws.cell(row=row, column=6)
                c.value = f"S: {sn_or}\nG: {gm_or}" if sn_or and gm_or else (sn_or or gm_or)
                c.fill  = fill(C_DIFF)
                c.font  = font(size=9)
                c.alignment = wrap_left()
                c.border = thin_border()
            else:
                _set_cell(ws, row, 6, "", bg=bg_row)

            ws.row_dimensions[row].height = 40
            row += 1

        # Merge candidate col for the block
        if len(DIM_KEYS) > 1:
            try:
                ws.merge_cells(start_row=start_row, start_column=1, end_row=row-1, end_column=1)
                ws.merge_cells(start_row=start_row, start_column=2, end_row=row-1, end_column=2)
            except Exception:
                pass

    ws.freeze_panes = "A3"


# ──────────────────────────────────────────────────────────────
# SHEET 3: SUMMARY
# ──────────────────────────────────────────────────────────────

def build_summary_sheet(ws, data):
    ws.title = "Summary"

    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 20

    row = 1
    ws.merge_cells(f"A{row}:D{row}")
    c = ws.cell(row=row, column=1,
                value="Summary — Sonnet 4.5 vs Gemini 2.5 Flash Lite")
    c.font = font(bold=True, color=C_WHITE, size=13)
    c.fill = fill(C_HEADER_DARK)
    c.alignment = centre()
    row += 1

    meta = [
        ("Prompt",          data.get("prompt_tag", "").upper()),
        ("Prompt File",     data.get("prompt_file", "")),
        ("Sonnet Model",    data.get("sonnet_model", "")),
        ("Gemini Model",    data.get("gemini_model", "")),
        ("Candidates",      data.get("total", 0)),
    ]
    for label, val in meta:
        _set_cell(ws, row, 1, label, bold=True, bg=C_HEADER_MED, color=C_WHITE)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        _set_cell(ws, row, 2, val)
        row += 1

    row += 1

    # Per-candidate summary
    records = data.get("records", [])
    if not records:
        return

    _set_cell(ws, row, 1, "Candidate",        bold=True, bg=C_HEADER_MED, color=C_WHITE)
    _set_cell(ws, row, 2, "Sonnet Overall",   bold=True, bg=C_SONNET, align=centre())
    _set_cell(ws, row, 3, "Gemini Overall",   bold=True, bg=C_GEMINI, align=centre())
    _set_cell(ws, row, 4, "Δ (Sonnet-Gemini)",bold=True, bg=C_DIFF,   align=centre())
    row += 1

    for ri, rec in enumerate(records):
        bg = C_WHITE if ri % 2 == 0 else C_ALT_ROW
        ov = rec.get("overall", {})
        sn_o = ov.get("sonnet")
        gm_o = ov.get("gemini")
        dv   = fmt_diff(sn_o, gm_o)

        _set_cell(ws, row, 1, rec.get("candidate", ""), bg=bg)
        _set_cell(ws, row, 2, sn_o if sn_o is not None else "N/A", bg=C_SONNET, align=centre())
        _set_cell(ws, row, 3, gm_o if gm_o is not None else "N/A", bg=C_GEMINI, align=centre())
        c = _set_cell(ws, row, 4, dv, bg=C_DIFF, align=centre())
        c.font = font(color=diff_color(dv))
        row += 1

    # Aggregate stats
    row += 1
    ws.merge_cells(f"A{row}:D{row}")
    c = ws.cell(row=row, column=1, value="Aggregate Statistics")
    c.font = font(bold=True, color=C_WHITE, size=11)
    c.fill = fill(C_HEADER_MED)
    c.alignment = centre()
    row += 1

    diffs = [abs(r["overall"]["diff_s_vs_g"])
             for r in records if r["overall"].get("diff_s_vs_g") is not None]
    lm = sum(1 for r in records if r.get("label", {}).get("match"))
    total = len(records)

    stats = [
        ("Avg |Sonnet-Gemini| overall diff",   f"{sum(diffs)/len(diffs):.1f} pts" if diffs else "N/A"),
        ("Max |Sonnet-Gemini| overall diff",   f"{max(diffs):.0f} pts" if diffs else "N/A"),
        ("Label match rate (Sonnet=Gemini)",   f"{lm}/{total} ({100*lm//total if total else 0}%)"),
        ("Candidates with |diff| > 10 pts",    str(sum(1 for d in diffs if d > 10))),
        ("Sonnet API calls OK",                str(sum(1 for r in records if r.get("sonnet_raw")))),
        ("Gemini API calls OK",                str(sum(1 for r in records if r.get("gemini_raw")))),
    ]

    au = data.get("anthropic_usage", {})
    totals = au.get("totals", {})
    if totals.get("input_tokens"):
        stats += [
            ("Sonnet input tokens (total)",   str(totals.get("input_tokens", 0))),
            ("Sonnet output tokens (total)",  str(totals.get("output_tokens", 0))),
        ]

    for label, val in stats:
        _set_cell(ws, row, 1, label, bold=True)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        _set_cell(ws, row, 2, val, align=centre())
        row += 1

    ws.freeze_panes = "A2"


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    if not os.path.isfile(JSON_IN):
        raise SystemExit(f"Input not found: {JSON_IN}\nRun direct_llm_eval.py --prompt {PROMPT_TAG} first.")

    with open(JSON_IN) as f:
        data = json.load(f)
    records = data.get("records", [])
    print(f"Loaded {len(records)} records from {JSON_IN}")

    out = build_excel_from_json(JSON_IN, XLSX_OUT)
    print(f"✓ Excel saved → {out}")


if __name__ == "__main__":
    main()

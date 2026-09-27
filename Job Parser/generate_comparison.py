#!/usr/bin/env python3
"""
Generate a detailed comparison Excel workbook between Sovren (ground truth)
and the New System parser results for 50 Job Descriptions.

Scoring System (editable via "Verdict" column):
  - Exact Match        → Verdict: "Correct"       → Score: +1
  - Partial Match      → Verdict: "Partial Credit" → Score: +0.5
  - Mismatch           → Verdict: "GT Better"      → Score: -1
  - New System Better   → Verdict: "New Better"    → Score: +1  (bonus!)
  - Both Wrong         → Verdict: "Both Wrong"     → Score:  0

The user can override any Verdict cell. All stats update dynamically.

Output sheets:
  1. Dashboard           – overall stats with dynamic formulas
  2. Scoring Guide       – explains the scoring system
  3. Field-wise Accuracy – per-field breakdown (dynamic)
  4. All JDs Combined    – flat table, all 550 comparisons
  5. JD-1 … JD-50       – per-JD side-by-side with verdict & score
"""

import os
import openpyxl
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side,
)
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# ── Paths ────────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
SOVREN_FILE = f"{BASE}/Sovren.xlsx"
NEW_FILE    = f"{BASE}/LLM.xlsx"
OUT_FILE    = f"{BASE}/Comparison_Report.xlsx"

# NUM_JDS is auto-detected from the source files below

# ── Fields ───────────────────────────────────────────────────────────────────
COMPARE_FIELDS = [
    "Job Title",
    "Job Type",
    "Min Experience (Years)",
    "Max Experience (Years)",
    "City",
    "State",
    "Country",
    "Full Address",
    "Min Salary (Annual)",
    "Max Salary (Annual)",
    "Skills",
]

META_FIELDS = [
    "Response Time (s)",
    "Model Number",
    "Tokens Consumed",
    "Status",
    "Job Document ID",
]

# ── Verdict options ──────────────────────────────────────────────────────────
# User can pick any of these in the Verdict column
VERDICT_OPTIONS = "Correct,New Better,GT Better,Partial Credit,Both Wrong"

# ── Styles ───────────────────────────────────────────────────────────────────
HEADER_FILL    = PatternFill("solid", fgColor="2F5496")
HEADER_FONT    = Font(bold=True, color="FFFFFF", size=11)
MATCH_FILL     = PatternFill("solid", fgColor="C6EFCE")   # green
MISMATCH_FILL  = PatternFill("solid", fgColor="FFC7CE")   # red
PARTIAL_FILL   = PatternFill("solid", fgColor="FFEB9C")   # yellow
NEW_BETTER_FILL= PatternFill("solid", fgColor="B4C6E7")   # blue
BOTH_WRONG_FILL= PatternFill("solid", fgColor="D9D9D9")   # gray
LABEL_FILL     = PatternFill("solid", fgColor="D6DCE4")
LABEL_FONT     = Font(bold=True, size=11)
TITLE_FONT     = Font(bold=True, size=14, color="2F5496")
SUBTITLE_FONT  = Font(bold=True, size=12, color="2F5496")
PCT_FMT        = '0.0%'
SCORE_FMT      = '0.0'
THIN_BORDER    = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)

def style_header_row(ws, row, max_col):
    for c in range(1, max_col + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = THIN_BORDER

def style_cell(cell):
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    cell.border = THIN_BORDER

def auto_width(ws, min_w=12, max_w=50):
    for col_cells in ws.columns:
        length = min_w
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            if cell.value:
                length = max(length, min(len(str(cell.value)) + 2, max_w))
        ws.column_dimensions[col_letter].width = length

# ── Load data ────────────────────────────────────────────────────────────────
def load_jd_data(wb, sheet_name):
    ws = wb[sheet_name]
    data = {}
    for row in ws.iter_rows(min_row=2, max_col=2, values_only=True):
        if row[0]:
            data[str(row[0]).strip()] = row[1]
    return data

print("Loading workbooks …")
wb_sovren = openpyxl.load_workbook(SOVREN_FILE, data_only=True)
wb_new    = openpyxl.load_workbook(NEW_FILE, data_only=True)

# Auto-detect JD sheet names (exclude Summary, Model Statistics, etc.)
SKIP_SHEETS = {"Summary", "Model Statistics"}
JD_SHEETS = [s for s in wb_sovren.sheetnames if s not in SKIP_SHEETS]
NUM_JDS = len(JD_SHEETS)
print(f"Detected {NUM_JDS} JD sheets: {JD_SHEETS[:5]}{'...' if NUM_JDS > 5 else ''}")

sovren_data = {}
new_data    = {}
for sn in JD_SHEETS:
    sovren_data[sn] = load_jd_data(wb_sovren, sn)
    new_data[sn]    = load_jd_data(wb_new, sn)

# ── Helpers for initial verdict ──────────────────────────────────────────────
def compute_initial_match(s_val, n_val, field):
    """Return (match_label, initial_verdict, notes)."""
    s_str = str(s_val).strip().lower() if s_val is not None else ""
    n_str = str(n_val).strip().lower() if n_val is not None else ""

    if field == "Skills":
        s_set = set(x.strip() for x in s_str.split(",") if x.strip()) if s_str else set()
        n_set = set(x.strip() for x in n_str.split(",") if x.strip()) if n_str else set()
        if s_set == n_set:
            return "Exact Match", "Correct", ""
        elif not s_set and not n_set:
            return "Both Empty", "Correct", ""
        elif s_set and n_set and (s_set & n_set):
            overlap = len(s_set & n_set)
            total   = len(s_set | n_set)
            # build notes
            s_raw = set(x.strip() for x in str(s_val).split(",") if x.strip()) if s_val else set()
            n_raw = set(x.strip() for x in str(n_val).split(",") if x.strip()) if n_val else set()
            parts = []
            missing = s_raw - n_raw
            extra   = n_raw - s_raw
            if missing:
                parts.append(f"Missing: {', '.join(list(missing)[:5])}")
            if extra:
                parts.append(f"Extra: {', '.join(list(extra)[:5])}")
            return f"Partial ({overlap}/{total})", "Partial Credit", "; ".join(parts)
        elif not s_set and n_set:
            return "GT Empty / New Has Value", "New Better", "GT had no skills; New system extracted some"
        elif s_set and not n_set:
            return "Mismatch", "GT Better", "New system returned no skills"
        else:
            return "Mismatch", "GT Better", "Values differ"
    else:
        try:
            s_num = float(s_str) if s_str else None
            n_num = float(n_str) if n_str else None
            if s_num is not None and n_num is not None:
                if s_num == n_num:
                    return "Exact Match", "Correct", ""
                else:
                    return "Mismatch", "GT Better", "Values differ"
            elif s_num is None and n_num is None:
                return "Both Empty", "Correct", ""
            elif s_num is None and n_num is not None:
                return "GT Empty / New Has Value", "New Better", "GT was empty; New system found a value"
            else:
                return "New Empty / GT Has Value", "GT Better", "New system returned empty"
        except ValueError:
            pass

        if s_str == n_str:
            return ("Exact Match" if s_str else "Both Empty"), "Correct", ""
        elif not s_str and n_str:
            return "GT Empty / New Has Value", "New Better", "GT was empty; New system found a value"
        elif s_str and not n_str:
            return "New Empty / GT Has Value", "GT Better", "New system returned empty"
        elif s_str in n_str or n_str in s_str:
            return "Partial Match", "Partial Credit", "Partial overlap"
        else:
            return "Mismatch", "GT Better", "Values differ"


# ── Create output workbook ───────────────────────────────────────────────────
out = openpyxl.Workbook()

# =====================================================================
# SHEET: Dashboard
# =====================================================================
print("Creating Dashboard …")
dash = out.active
dash.title = "Dashboard"
dash.sheet_properties.tabColor = "2F5496"

dash.cell(1, 1, "JD Parser Comparison Report").font = TITLE_FONT
dash.cell(2, 1, "Sovren (Ground Truth) vs New System – 50 Job Descriptions").font = SUBTITLE_FONT

# ── Overall metrics (rows 4-14) ──────────────────────────────────────
dash.cell(4, 1, "Overall Metrics").font = SUBTITLE_FONT
headers = ["Metric", "Value"]
for c, h in enumerate(headers, 1):
    dash.cell(5, c, h)
style_header_row(dash, 5, 2)

metric_labels = [
    "Total JDs",
    "Total Fields Compared",
    "Correct (inc. Both Empty)",
    "New System Better (+1 bonus)",
    "Partial Credit",
    "GT Better (Mismatches)",
    "Both Wrong",
    "Overall Accuracy (%)",
    "Total Score",
    "Max Possible Score",
]
for r, label in enumerate(metric_labels, 6):
    dash.cell(r, 1, label).font = LABEL_FONT
    dash.cell(r, 1).fill = LABEL_FILL
    style_cell(dash.cell(r, 1))
    style_cell(dash.cell(r, 2))

# Values filled later after JD sheets exist (row 6 = Total JDs)
dash.cell(6, 2, NUM_JDS)

# =====================================================================
# SHEET: Scoring Guide
# =====================================================================
print("Creating Scoring Guide …")
sg = out.create_sheet("Scoring Guide")
sg.sheet_properties.tabColor = "7030A0"

sg.cell(1, 1, "Scoring System & How to Validate").font = TITLE_FONT

guide_data = [
    ("", "", "", ""),
    ("Verdict", "When to Use", "Score", "Color"),
    ("Correct", "Both systems agree, or GT matches and is right", "+1", "Green"),
    ("New Better", "GT was wrong/empty but New System got the right value", "+1", "Blue"),
    ("Partial Credit", "Both have some overlap but neither is fully correct", "+0.5", "Yellow"),
    ("GT Better", "GT was right, New System got it wrong", "-1", "Red"),
    ("Both Wrong", "Neither system got the correct answer", "0", "Gray"),
]

for r, (a, b, c, d) in enumerate(guide_data, 2):
    sg.cell(r, 1, a)
    sg.cell(r, 2, b)
    sg.cell(r, 3, c)
    sg.cell(r, 4, d)
    for col in range(1, 5):
        style_cell(sg.cell(r, col))

style_header_row(sg, 3, 4)

# Color the verdict examples
sg.cell(4, 1).fill = MATCH_FILL
sg.cell(5, 1).fill = NEW_BETTER_FILL
sg.cell(6, 1).fill = PARTIAL_FILL
sg.cell(7, 1).fill = MISMATCH_FILL
sg.cell(8, 1).fill = BOTH_WRONG_FILL

sg.cell(10, 1, "How to Validate:").font = SUBTITLE_FONT
instructions = [
    "1. Go to any JD-x sheet",
    "2. Review the Sovren vs New System values for each field",
    "3. Change the 'Verdict' column (F) to the appropriate value using the dropdown",
    "4. The Score (G) updates automatically based on your verdict",
    "5. Dashboard, Field-wise Accuracy, and All JDs Combined all update automatically",
    "",
    "Example: If Sovren says Job Title = 'Engineer' but the actual JD says",
    "'Senior Software Engineer' and New System says 'Senior Software Engineer',",
    "change the Verdict from 'GT Better' to 'New Better' → Score becomes +1",
]
for r, line in enumerate(instructions, 11):
    sg.cell(r, 1, line)
    sg.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)

auto_width(sg, max_w=60)

# =====================================================================
# SHEET: Field-wise Accuracy (placeholder, formulas filled later)
# =====================================================================
print("Creating Field-wise Accuracy …")
fwa = out.create_sheet("Field-wise Accuracy")
fwa.sheet_properties.tabColor = "4472C4"
fwa.cell(1, 1, "Field-wise Accuracy & Scoring Breakdown").font = TITLE_FONT

fwa_headers = [
    "Field", "Correct", "New Better", "Partial Credit",
    "GT Better", "Both Wrong", "Total", "Accuracy (%)", "Total Score"
]
for c, h in enumerate(fwa_headers, 1):
    fwa.cell(3, c, h)
style_header_row(fwa, 3, len(fwa_headers))

for r, field in enumerate(COMPARE_FIELDS, 4):
    fwa.cell(r, 1, field).font = LABEL_FONT
    fwa.cell(r, 1).fill = LABEL_FILL
    for c in range(1, len(fwa_headers) + 1):
        style_cell(fwa.cell(r, c))

fwa_total_row = 4 + len(COMPARE_FIELDS)
fwa.cell(fwa_total_row, 1, "TOTAL").font = Font(bold=True, color="FFFFFF", size=11)
fwa.cell(fwa_total_row, 1).fill = HEADER_FILL
for c in range(1, len(fwa_headers) + 1):
    style_cell(fwa.cell(fwa_total_row, c))

# =====================================================================
# SHEET: All JDs Combined
# =====================================================================
print("Creating All JDs Combined …")
combined = out.create_sheet("All JDs Combined")
combined.sheet_properties.tabColor = "70AD47"
combined.cell(1, 1, "All JDs – Side-by-Side Comparison").font = TITLE_FONT

comb_headers = [
    "JD #", "Field",
    "Sovren (Ground Truth)", "New System",
    "Auto Match", "Verdict", "Score", "Notes"
]
for c, h in enumerate(comb_headers, 1):
    combined.cell(3, c, h)
style_header_row(combined, 3, len(comb_headers))

# Data validation for Verdict column in combined sheet
dv_combined = DataValidation(
    type="list", formula1=f'"{VERDICT_OPTIONS}"', allow_blank=False
)
dv_combined.error = "Pick a valid verdict"
dv_combined.errorTitle = "Invalid Verdict"
dv_combined.prompt = "Select verdict after reviewing"
dv_combined.promptTitle = "Verdict"
combined.add_data_validation(dv_combined)

row = 4
for sn in JD_SHEETS:
    s_data = sovren_data[sn]
    n_data = new_data[sn]
    for field in COMPARE_FIELDS:
        s_val = s_data.get(field, "")
        n_val = n_data.get(field, "")
        match_label, verdict, notes = compute_initial_match(s_val, n_val, field)

        combined.cell(row, 1, sn)                                    # A: JD #
        combined.cell(row, 2, field)                                 # B: Field
        combined.cell(row, 3, s_val if s_val is not None else "")    # C: Sovren
        combined.cell(row, 4, n_val if n_val is not None else "")    # D: New System
        combined.cell(row, 5, match_label)                           # E: Auto Match
        combined.cell(row, 6, verdict)                               # F: Verdict (editable!)
        dv_combined.add(combined.cell(row, 6))

        # G: Score formula driven by Verdict (F)
        combined.cell(row, 7).value = (
            f'=IF(F{row}="Correct",1,'
            f'IF(F{row}="New Better",1,'
            f'IF(F{row}="Partial Credit",0.5,'
            f'IF(F{row}="GT Better",-1,'
            f'IF(F{row}="Both Wrong",0,0)))))'
        )
        combined.cell(row, 7).number_format = SCORE_FMT

        combined.cell(row, 8, notes)                                 # H: Notes

        # Color the verdict cell
        v_cell = combined.cell(row, 6)
        if verdict == "Correct":
            v_cell.fill = MATCH_FILL
        elif verdict == "New Better":
            v_cell.fill = NEW_BETTER_FILL
        elif verdict == "Partial Credit":
            v_cell.fill = PARTIAL_FILL
        elif verdict == "GT Better":
            v_cell.fill = MISMATCH_FILL
        else:
            v_cell.fill = BOTH_WRONG_FILL

        for c in range(1, 9):
            style_cell(combined.cell(row, c))
        row += 1

comb_start = 4
comb_end   = row - 1

# =====================================================================
# Per-JD sheets (JD-1 … JD-50)
# =====================================================================
print("Creating per-JD comparison sheets …")
NUM_FIELDS = len(COMPARE_FIELDS)
JD_DATA_START = 5
JD_DATA_END   = JD_DATA_START + NUM_FIELDS - 1

for sn in JD_SHEETS:
    ws = out.create_sheet(sn)
    ws.sheet_properties.tabColor = "FFC000"

    s_data = sovren_data[sn]
    n_data = new_data[sn]

    ws.cell(1, 1, f"{sn} – Comparison").font = TITLE_FONT
    ws.cell(2, 1, f"Sheet: {sn}").font = Font(italic=True, color="666666")

    # Headers: A=Field, B=Sovren, C=New System, D=Auto Match, E=Notes, F=Verdict, G=Score
    jd_headers = [
        "Field", "Sovren (Ground Truth)", "New System",
        "Auto Match", "Notes", "Verdict", "Score"
    ]
    for c, h in enumerate(jd_headers, 1):
        ws.cell(4, c, h)
    style_header_row(ws, 4, len(jd_headers))

    # Data validation for Verdict column (F)
    dv_jd = DataValidation(
        type="list", formula1=f'"{VERDICT_OPTIONS}"', allow_blank=False
    )
    dv_jd.error = "Pick a valid verdict"
    dv_jd.errorTitle = "Invalid Verdict"
    dv_jd.prompt = "Select verdict after reviewing"
    dv_jd.promptTitle = "Verdict"
    ws.add_data_validation(dv_jd)

    r = JD_DATA_START
    for field in COMPARE_FIELDS:
        s_val = s_data.get(field, "")
        n_val = n_data.get(field, "")
        match_label, verdict, notes = compute_initial_match(s_val, n_val, field)

        ws.cell(r, 1, field).font = LABEL_FONT           # A: Field
        ws.cell(r, 1).fill = LABEL_FILL
        ws.cell(r, 2, s_val if s_val is not None else "") # B: Sovren
        ws.cell(r, 3, n_val if n_val is not None else "") # C: New System

        # D: Auto Match (formula – updates if B or C are edited)
        ws.cell(r, 4).value = (
            f'=IF(TRIM(LOWER(B{r}))=TRIM(LOWER(C{r})),"Exact Match",'
            f'IF(AND(B{r}="",C{r}=""),"Both Empty",'
            f'IF(AND(B{r}="",C{r}<>""),"GT Empty / New Has Value",'
            f'IF(AND(B{r}<>"",C{r}=""),"New Empty / GT Has Value",'
            f'IF(OR(ISNUMBER(SEARCH(B{r},C{r})),ISNUMBER(SEARCH(C{r},B{r}))),"Partial Match","Mismatch")))))'
        )

        ws.cell(r, 5, notes)                              # E: Notes
        ws.cell(r, 6, verdict)                             # F: Verdict (EDITABLE)
        dv_jd.add(ws.cell(r, 6))

        # G: Score formula driven by Verdict (F)
        ws.cell(r, 7).value = (
            f'=IF(F{r}="Correct",1,'
            f'IF(F{r}="New Better",1,'
            f'IF(F{r}="Partial Credit",0.5,'
            f'IF(F{r}="GT Better",-1,'
            f'IF(F{r}="Both Wrong",0,0)))))'
        )
        ws.cell(r, 7).number_format = SCORE_FMT

        # Color verdict cell based on initial verdict
        v_cell = ws.cell(r, 6)
        if verdict == "Correct":
            v_cell.fill = MATCH_FILL
        elif verdict == "New Better":
            v_cell.fill = NEW_BETTER_FILL
        elif verdict == "Partial Credit":
            v_cell.fill = PARTIAL_FILL
        elif verdict == "GT Better":
            v_cell.fill = MISMATCH_FILL
        else:
            v_cell.fill = BOTH_WRONG_FILL

        for c in range(1, 8):
            style_cell(ws.cell(r, c))
        r += 1

    # ── Summary section ──────────────────────────────────────────────
    r += 1
    ws.cell(r, 1, "Summary").font = SUBTITLE_FONT
    r += 1
    fs = JD_DATA_START
    fe = JD_DATA_END

    summary_items = [
        ("Fields Compared", str(NUM_FIELDS)),
        ("Correct",         f'=COUNTIF(F{fs}:F{fe},"Correct")'),
        ("New System Better", f'=COUNTIF(F{fs}:F{fe},"New Better")'),
        ("Partial Credit",  f'=COUNTIF(F{fs}:F{fe},"Partial Credit")'),
        ("GT Better",       f'=COUNTIF(F{fs}:F{fe},"GT Better")'),
        ("Both Wrong",      f'=COUNTIF(F{fs}:F{fe},"Both Wrong")'),
    ]
    correct_row = None
    new_better_row = None
    for label, formula in summary_items:
        ws.cell(r, 1, label).font = LABEL_FONT
        ws.cell(r, 1).fill = LABEL_FILL
        if formula.startswith("="):
            ws.cell(r, 2).value = formula
        else:
            ws.cell(r, 2, int(formula))
        style_cell(ws.cell(r, 1))
        style_cell(ws.cell(r, 2))
        if label == "Correct":
            correct_row = r
        if label == "New System Better":
            new_better_row = r
        r += 1

    # Accuracy = (Correct + New Better) / Total
    ws.cell(r, 1, "Accuracy (%)").font = Font(bold=True, size=12, color="2F5496")
    ws.cell(r, 2).value = f'=(B{correct_row}+B{new_better_row})/{NUM_FIELDS}'
    ws.cell(r, 2).number_format = PCT_FMT
    style_cell(ws.cell(r, 1))
    style_cell(ws.cell(r, 2))
    r += 1

    # Total Score
    ws.cell(r, 1, "Total Score").font = Font(bold=True, size=12, color="2F5496")
    ws.cell(r, 2).value = f'=SUM(G{fs}:G{fe})'
    ws.cell(r, 2).number_format = SCORE_FMT
    style_cell(ws.cell(r, 1))
    style_cell(ws.cell(r, 2))
    score_row = r
    r += 1

    ws.cell(r, 1, "Max Possible Score").font = LABEL_FONT
    ws.cell(r, 2, NUM_FIELDS)
    style_cell(ws.cell(r, 1))
    style_cell(ws.cell(r, 2))
    r += 1

    ws.cell(r, 1, "Score %").font = Font(bold=True, size=12, color="2F5496")
    ws.cell(r, 2).value = f'=B{score_row}/{NUM_FIELDS}'
    ws.cell(r, 2).number_format = PCT_FMT
    style_cell(ws.cell(r, 1))
    style_cell(ws.cell(r, 2))

    # Metadata section
    r += 2
    ws.cell(r, 1, "Metadata").font = SUBTITLE_FONT
    r += 1
    meta_h = ["Field", "Sovren", "New System"]
    for c, h in enumerate(meta_h, 1):
        ws.cell(r, c, h)
    style_header_row(ws, r, len(meta_h))
    r += 1
    for mf in META_FIELDS:
        ws.cell(r, 1, mf).font = LABEL_FONT
        ws.cell(r, 1).fill = LABEL_FILL
        ws.cell(r, 2, s_data.get(mf, ""))
        ws.cell(r, 3, n_data.get(mf, ""))
        for c in range(1, 4):
            style_cell(ws.cell(r, c))
        r += 1

    auto_width(ws)

# =====================================================================
# Back-fill Field-wise Accuracy formulas
# =====================================================================
print("Adding dynamic formulas to Field-wise Accuracy …")

# References into "All JDs Combined" sheet
# Col B=Field, Col F=Verdict, Col G=Score
cref = "'All JDs Combined'"

for r, field in enumerate(COMPARE_FIELDS, 4):
    # B: Correct
    fwa.cell(r, 2).value = (
        f'=COUNTIFS({cref}!B{comb_start}:B{comb_end},A{r},'
        f'{cref}!F{comb_start}:F{comb_end},"Correct")'
    )
    # C: New Better
    fwa.cell(r, 3).value = (
        f'=COUNTIFS({cref}!B{comb_start}:B{comb_end},A{r},'
        f'{cref}!F{comb_start}:F{comb_end},"New Better")'
    )
    # D: Partial Credit
    fwa.cell(r, 4).value = (
        f'=COUNTIFS({cref}!B{comb_start}:B{comb_end},A{r},'
        f'{cref}!F{comb_start}:F{comb_end},"Partial Credit")'
    )
    # E: GT Better
    fwa.cell(r, 5).value = (
        f'=COUNTIFS({cref}!B{comb_start}:B{comb_end},A{r},'
        f'{cref}!F{comb_start}:F{comb_end},"GT Better")'
    )
    # F: Both Wrong
    fwa.cell(r, 6).value = (
        f'=COUNTIFS({cref}!B{comb_start}:B{comb_end},A{r},'
        f'{cref}!F{comb_start}:F{comb_end},"Both Wrong")'
    )
    # G: Total
    fwa.cell(r, 7).value = f'=SUM(B{r}:F{r})'
    # H: Accuracy = (Correct + New Better) / Total
    fwa.cell(r, 8).value = f'=IF(G{r}=0,0,(B{r}+C{r})/G{r})'
    fwa.cell(r, 8).number_format = PCT_FMT
    # I: Total Score
    fwa.cell(r, 9).value = (
        f'=SUMPRODUCT(({cref}!B{comb_start}:B{comb_end}=A{r})*'
        f'{cref}!G{comb_start}:G{comb_end})'
    )
    fwa.cell(r, 9).number_format = SCORE_FMT

# Totals row
tr = fwa_total_row
for c in range(2, 7):
    fwa.cell(tr, c).value = f'=SUM({get_column_letter(c)}4:{get_column_letter(c)}{tr-1})'
    fwa.cell(tr, c).font = Font(bold=True)
    fwa.cell(tr, c).fill = PatternFill("solid", fgColor="D6DCE4")

fwa.cell(tr, 7).value = f'=SUM(G4:G{tr-1})'
fwa.cell(tr, 7).font = Font(bold=True)
fwa.cell(tr, 7).fill = PatternFill("solid", fgColor="D6DCE4")

fwa.cell(tr, 8).value = f'=IF(G{tr}=0,0,(B{tr}+C{tr})/G{tr})'
fwa.cell(tr, 8).number_format = PCT_FMT
fwa.cell(tr, 8).font = Font(bold=True)
fwa.cell(tr, 8).fill = PatternFill("solid", fgColor="D6DCE4")

fwa.cell(tr, 9).value = f'=SUM(I4:I{tr-1})'
fwa.cell(tr, 9).number_format = SCORE_FMT
fwa.cell(tr, 9).font = Font(bold=True)
fwa.cell(tr, 9).fill = PatternFill("solid", fgColor="D6DCE4")

for c in range(1, len(fwa_headers) + 1):
    fwa.cell(tr, c).border = THIN_BORDER

auto_width(fwa)

# =====================================================================
# Back-fill Dashboard formulas
# =====================================================================
print("Filling Dashboard formulas …")

# Row 7: Total Fields
dash.cell(7, 2).value = f"='Field-wise Accuracy'!G{fwa_total_row}"
# Row 8: Correct
dash.cell(8, 2).value = f"='Field-wise Accuracy'!B{fwa_total_row}"
# Row 9: New Better
dash.cell(9, 2).value = f"='Field-wise Accuracy'!C{fwa_total_row}"
# Row 10: Partial Credit
dash.cell(10, 2).value = f"='Field-wise Accuracy'!D{fwa_total_row}"
# Row 11: GT Better
dash.cell(11, 2).value = f"='Field-wise Accuracy'!E{fwa_total_row}"
# Row 12: Both Wrong
dash.cell(12, 2).value = f"='Field-wise Accuracy'!F{fwa_total_row}"
# Row 13: Overall Accuracy
dash.cell(13, 2).value = f"='Field-wise Accuracy'!H{fwa_total_row}"
dash.cell(13, 2).number_format = PCT_FMT
# Row 14: Total Score
dash.cell(14, 2).value = f"='Field-wise Accuracy'!I{fwa_total_row}"
dash.cell(14, 2).number_format = SCORE_FMT
# Row 15: Max Possible Score
dash.cell(15, 2).value = NUM_JDS * NUM_FIELDS
dash.cell(15, 2).number_format = '0'

# Fix metric labels (we had 10 metrics starting at row 6)
new_metric_labels = [
    "Total JDs",
    "Total Fields Compared",
    "Correct (inc. Both Empty)",
    "New System Better (+1 bonus)",
    "Partial Credit",
    "GT Better (Mismatches)",
    "Both Wrong",
    "Overall Accuracy (%)",
    "Total Score",
    "Max Possible Score",
]
for r, label in enumerate(new_metric_labels, 6):
    dash.cell(r, 1, label).font = LABEL_FONT
    dash.cell(r, 1).fill = LABEL_FILL
    style_cell(dash.cell(r, 1))
    style_cell(dash.cell(r, 2))

# ── Per-JD table on Dashboard ────────────────────────────────────────
r = 17
dash.cell(r, 1, "Per-JD Summary").font = SUBTITLE_FONT
r += 1
jd_headers = [
    "JD #", "Job Title (Sovren)",
    "Correct", "New Better", "Partial", "GT Better", "Both Wrong",
    "Accuracy (%)", "Score"
]
for c, h in enumerate(jd_headers, 1):
    dash.cell(r, c, h)
style_header_row(dash, r, len(jd_headers))

r += 1
jd_table_start = r
fs = JD_DATA_START
fe = JD_DATA_END

for sn in JD_SHEETS:
    dash.cell(r, 1, sn)
    dash.cell(r, 2, sovren_data[sn].get("Job Title", ""))

    # Counts from each JD sheet's Verdict column (F)
    dash.cell(r, 3).value = f"=COUNTIF('{sn}'!F{fs}:F{fe},\"Correct\")"
    dash.cell(r, 4).value = f"=COUNTIF('{sn}'!F{fs}:F{fe},\"New Better\")"
    dash.cell(r, 5).value = f"=COUNTIF('{sn}'!F{fs}:F{fe},\"Partial Credit\")"
    dash.cell(r, 6).value = f"=COUNTIF('{sn}'!F{fs}:F{fe},\"GT Better\")"
    dash.cell(r, 7).value = f"=COUNTIF('{sn}'!F{fs}:F{fe},\"Both Wrong\")"
    # Accuracy = (Correct + New Better) / total
    dash.cell(r, 8).value = f"=(C{r}+D{r})/{NUM_FIELDS}"
    dash.cell(r, 8).number_format = PCT_FMT
    # Score
    dash.cell(r, 9).value = f"=SUM('{sn}'!G{fs}:G{fe})"
    dash.cell(r, 9).number_format = SCORE_FMT

    for c in range(1, len(jd_headers) + 1):
        style_cell(dash.cell(r, c))
    r += 1

jd_table_end = r - 1

# Averages row
dash.cell(r, 1, "AVERAGE / TOTAL").font = Font(bold=True, color="FFFFFF")
dash.cell(r, 1).fill = HEADER_FILL
for c in range(3, 8):
    cl = get_column_letter(c)
    dash.cell(r, c).value = f"=SUM({cl}{jd_table_start}:{cl}{jd_table_end})"
    dash.cell(r, c).font = Font(bold=True)

dash.cell(r, 8).value = f"=AVERAGE(H{jd_table_start}:H{jd_table_end})"
dash.cell(r, 8).number_format = PCT_FMT
dash.cell(r, 8).font = Font(bold=True)

dash.cell(r, 9).value = f"=SUM(I{jd_table_start}:I{jd_table_end})"
dash.cell(r, 9).number_format = SCORE_FMT
dash.cell(r, 9).font = Font(bold=True)

for c in range(1, len(jd_headers) + 1):
    dash.cell(r, c).border = THIN_BORDER
    if not dash.cell(r, c).fill or dash.cell(r, c).fill.fgColor.rgb == "00000000":
        dash.cell(r, c).fill = PatternFill("solid", fgColor="D6DCE4")

auto_width(dash)
auto_width(combined)

# ── Freeze panes ─────────────────────────────────────────────────────
combined.freeze_panes = "A4"
fwa.freeze_panes = "A4"
dash.freeze_panes = "A19"

# ── Save ─────────────────────────────────────────────────────────────
print(f"Saving to {OUT_FILE} …")
out.save(OUT_FILE)
print("Done!")

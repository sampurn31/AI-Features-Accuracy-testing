#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A/B Comparison Pipeline
-----------------------
Compares how Track A (albatross-staging) and Track B (albatross-ts2) parse
the SAME modified resume (original + injected custom-field snippets).

Flow:
  1. Load Track A baseline → resume_text per resume
  2. Generate / load ground truth (LLM snippets per field)
  3. Inject snippets → modified_text
  4. Convert modified_text → PDF
  5. Upload + parse the same PDF on BOTH Track A and Track B
  6. Extract standard fields from both; custom fields from Track B only
  7. Compare Track A (modified) vs Track B (modified)
  8. Write JSON + Excel report to pipeline_output/

Reuses all helpers from custom_field_pipeline.py — no logic is duplicated.
Does NOT read or write any files that the main pipeline owns.
"""

import os, json, time, copy, logging
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from custom_field_pipeline import (
    BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A,
    BASE_URL_SS,      EMAIL_SS,      PASSWORD_SS,
    BASELINE_FILE, GROUND_TRUTH_FILE, CUSTOM_FIELDS_MANIFEST,
    get_token,
    get_presigned_url, upload_pdf_bytes,
    text_to_pdf_bytes, call_parse_api,
    extract_standard_fields, extract_custom_fields, extract_resume_text,
    load_custom_fields_from_manifest,
    run_track_a,
    generate_ground_truth,
    inject_snippets_individually,
    classify_custom, classify_standard,
    classify_history_comparator_style,
    flatten_work_history_list, flatten_education_history_list,
    _cell_preview, _sanitize_df,
)

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ab_comparison")

OUTPUT_DIR       = Path("pipeline_output")
AB_RESULTS_FILE  = OUTPUT_DIR / "ab_comparison_results.json"
AB_REPORT_FILE   = OUTPUT_DIR / "ab_comparison_report.xlsx"


def load_or_run_baseline(token_a: str) -> list:
    if BASELINE_FILE.exists():
        print(f"[Baseline] Loading cached → {BASELINE_FILE}")
        return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    print(f"[Baseline] No cache at {BASELINE_FILE}")
    print("         Running Track A to parse resumes and build baseline ...")
    return run_track_a(token_a)


def load_ground_truth(baseline: list, custom_fields: list) -> list:
    if GROUND_TRUTH_FILE.exists():
        print(f"[LLM] Loading cached ground truth → {GROUND_TRUTH_FILE}")
        return json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    return generate_ground_truth(baseline, custom_fields)


def run_ab_parse(
    token_a: str,
    token_b: str,
    baseline: list,
    ground_truth_all: list,
) -> list:
    """
    For each resume:
      - Inject snippets → modified_text → PDF
      - Upload + parse on Track A  (albatross-staging)
      - Upload + parse on Track B  (albatross-ts2)
    Returns list of per-resume result dicts.
    """
    gt_by_id = {g["resume_id"]: g for g in ground_truth_all}
    valid    = [r for r in baseline if "error" not in r]
    results  = []

    print(f"\n[A/B Parse] Processing {len(valid)} resumes on both environments ...")

    for record in valid:
        rid          = record["resume_id"]
        resume_text  = record.get("resume_text", "")
        injected     = gt_by_id.get(rid, {}).get("injected_fields", [])
        pdf_name     = f"{rid}_ab_modified.pdf"

        print(f"  [{rid}] injecting + converting ...", end=" ", flush=True)

        try:
            modified_text = inject_snippets_individually(resume_text, injected)
            pdf_bytes     = text_to_pdf_bytes(modified_text)

            presign_a  = get_presigned_url(BASE_URL_TRACK_A, pdf_name, token_a)
            upload_pdf_bytes(presign_a["preSignedUrl"], pdf_bytes)
            result_a   = call_parse_api(BASE_URL_TRACK_A, token_a,
                                        presign_a["key"], pdf_name)
            std_a      = extract_standard_fields(result_a)
            print("A✓", end=" ", flush=True)

            presign_b  = get_presigned_url(BASE_URL_SS, pdf_name, token_b)
            upload_pdf_bytes(presign_b["preSignedUrl"], pdf_bytes)
            result_b   = call_parse_api(BASE_URL_SS, token_b,
                                        presign_b["key"], pdf_name)
            std_b      = extract_standard_fields(result_b)

            col_indices   = [inj["column_index"] for inj in injected]
            custom_b      = extract_custom_fields(result_b, col_indices)
            print("B✓")

            results.append({
                "resume_id":            rid,
                "filename":             record.get("filename", ""),
                "modified_resume_text": modified_text,
                "track_a_modified":     std_a,
                "track_b_modified":     std_b,
                "parsed_custom_fields": custom_b,
                "injected_fields":      injected,
            })
            log.info(f"[A/B] {rid} OK")

        except Exception as exc:
            print(f"ERROR — {exc}")
            log.error(f"[A/B] {rid} failed: {exc}")
            results.append({
                "resume_id": rid,
                "filename":  record.get("filename", ""),
                "error":     str(exc),
            })

        time.sleep(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    AB_RESULTS_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[A/B Parse] Results saved → {AB_RESULTS_FILE}")
    return results


def run_ab_validation(
    ab_results: list,
    custom_fields: list = None,
) -> pd.DataFrame:
    """
    Produces one row per (resume × field) for:
      - Standard fields : Track A (modified) vs Track B (modified)
      - Custom fields   : ground-truth value vs Track B parsed value
    """
    opts_by_col = {}
    if custom_fields:
        for cf in custom_fields:
            opts_by_col[cf["column_index"]] = cf.get("options", [])

    rows = []

    for rec in ab_results:
        rid      = rec.get("resume_id")
        filename = rec.get("filename", "")

        if "error" in rec:
            rows.append({
                "resume_id": rid, "filename": filename,
                "section": "ERROR", "field": "-",
                "track_a_value": "", "track_b_value": "",
                "ground_truth": "", "parsed_value": "",
                "classification": "ERROR",
                "details": rec["error"],
            })
            continue

        std_a    = rec.get("track_a_modified", {})
        std_b    = rec.get("track_b_modified", {})
        injected = rec.get("injected_fields", [])
        custom_b = rec.get("parsed_custom_fields", {})

        for fname, a_val in std_a.items():
            b_val = std_b.get(fname)

            if fname in ("workhistory", "educationhistory"):
                a_list = a_val if isinstance(a_val, list) else []
                b_list = b_val if isinstance(b_val, list) else []
                classification, details = classify_history_comparator_style(
                    a_val, b_val, fname
                )
                if fname == "workhistory":
                    a_prev = _cell_preview(flatten_work_history_list(a_list))
                    b_prev = _cell_preview(flatten_work_history_list(b_list))
                else:
                    a_prev = _cell_preview(flatten_education_history_list(a_list))
                    b_prev = _cell_preview(flatten_education_history_list(b_list))
            else:
                b_val  = b_val if b_val is not None else ""
                classification, details = classify_standard(a_val, b_val, fname)
                a_prev = _cell_preview(a_val)
                b_prev = _cell_preview(b_val)

            rows.append({
                "resume_id":      rid,
                "filename":       filename,
                "section":        "Standard",
                "field":          fname,
                "track_a_value":  a_prev,
                "track_b_value":  b_prev,
                "ground_truth":   "",
                "parsed_value":   "",
                "classification": classification,
                "details":        details,
            })

        for inj in injected:
            col_idx    = inj["column_index"]
            fname      = inj["field_name"]
            ftype      = inj["field_type"]
            gt_val     = inj["value"]
            parsed_val = custom_b.get(col_idx)

            classification, details = classify_custom(gt_val, parsed_val, ftype)
            rows.append({
                "resume_id":      rid,
                "filename":       filename,
                "section":        "Custom",
                "field":          f"{fname} (custcolumn{col_idx})",
                "track_a_value":  "",
                "track_b_value":  "",
                "ground_truth":   _cell_preview(gt_val),
                "parsed_value":   _cell_preview(parsed_val),
                "classification": classification,
                "details":        details,
            })

    return pd.DataFrame(rows)


def build_ab_report(df: pd.DataFrame) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    CLS_FILL = {
        "Match":               "D6EFD8",
        "Exact Match":         "D6EFD8",
        "Partial Match":       "FFF3CD",
        "Partial Match (+ve)": "FFF3CD",
        "Partial Match (-ve)": "F4CCCC",
        "Regression":          "FADADD",
        "Missing in B":        "FADADD",
        "Mismatch":            "FADADD",
        "Missed":              "FADADD",
        "Hallucination":       "F4CCCC",
        "Both Empty":          "E8E8E8",
        "ERROR":               "F4CCCC",
    }

    HEADER_FILL    = PatternFill("solid", fgColor="1F4E79")
    HEADER_FONT    = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
    DATA_FONT      = Font(size=10, name="Calibri")
    DATA_FONT_BOLD = Font(size=10, bold=True, name="Calibri")
    BAND_A         = PatternFill("solid", fgColor="EBF3FB")
    BAND_B         = PatternFill("solid", fgColor="FFFFFF")
    THIN           = Side(border_style="thin", color="C0C0C0")
    BORDER         = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    COLS      = ["resume_id", "filename", "section", "field",
                 "track_a_value", "track_b_value",
                 "ground_truth", "parsed_value",
                 "classification", "details"]
    COL_WIDTH = [12, 22, 12, 32, 52, 52, 32, 32, 22, 58]

    safe_df = _sanitize_df(df)
    wb      = Workbook()

    ws = wb.active
    ws.title = "A-B Full Detail"
    ws.freeze_panes  = "A2"
    ws.sheet_view.showGridLines = False

    for ci, (col, width) in enumerate(zip(COLS, COL_WIDTH), start=1):
        cell = ws.cell(row=1, column=ci,
                       value=col.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        cell.border    = BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 30

    sub_df     = safe_df.reindex(columns=COLS).fillna("")
    cur_row    = 2
    band_flip  = True

    for rid in sub_df["resume_id"].unique():
        block = sub_df[sub_df["resume_id"] == rid]
        band  = BAND_A if band_flip else BAND_B
        band_flip = not band_flip

        for _, row_data in block.iterrows():
            cls      = row_data.get("classification", "")
            cls_fill = PatternFill("solid", fgColor=CLS_FILL.get(cls, "FFFFFF"))
            for ci, col in enumerate(COLS, start=1):
                val  = row_data.get(col, "")
                cell = ws.cell(row=cur_row, column=ci,
                               value="" if val is None else val)
                cell.border    = BORDER
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if col == "classification":
                    cell.fill = cls_fill
                    cell.font = DATA_FONT_BOLD
                else:
                    cell.fill = band
                    cell.font = DATA_FONT
            ws.row_dimensions[cur_row].height = 18
            cur_row += 1

        cur_row += 2

    std_df = safe_df[safe_df["section"] == "Standard"]
    if not std_df.empty:
        ws_s = wb.create_sheet("Standard Summary")
        ws_s.sheet_view.showGridLines = False
        pivot = (std_df.groupby(["field", "classification"])
                 .size().unstack(fill_value=0).reset_index())
        totals = std_df.groupby("field").size().rename("Total")
        pivot  = pivot.merge(totals, on="field")
        for cls in ["Match", "Partial Match", "Regression", "Missing in B", "Other"]:
            if cls not in pivot.columns:
                pivot[cls] = 0
            pivot[f"{cls} %"] = (pivot[cls] / pivot["Total"] * 100).round(1)
        pivot = pivot.rename(columns={"Match %": "Pass %"})

        _write_summary_ws(ws_s, pivot, HEADER_FONT, BORDER)

    cst_df = safe_df[safe_df["section"] == "Custom"]
    if not cst_df.empty:
        ws_c = wb.create_sheet("Custom Summary")
        ws_c.sheet_view.showGridLines = False
        pivot_c = (cst_df.groupby(["field", "classification"])
                   .size().unstack(fill_value=0).reset_index())
        totals_c = cst_df.groupby("field").size().rename("Total")
        pivot_c  = pivot_c.merge(totals_c, on="field")
        for cls in ["Exact Match", "Partial Match (+ve)", "Partial Match (-ve)",
                    "Missed", "Hallucination", "Mismatch", "Both Empty"]:
            if cls not in pivot_c.columns:
                pivot_c[cls] = 0
            pivot_c[f"{cls} %"] = (pivot_c[cls] / pivot_c["Total"] * 100).round(1)

        _write_summary_ws(ws_c, pivot_c, HEADER_FONT, BORDER)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    wb.save(AB_REPORT_FILE)
    print(f"\n[Report] A/B comparison report saved → {AB_REPORT_FILE}")


def _write_summary_ws(ws_s, pivot: pd.DataFrame, header_font, border) -> None:
    """Shared helper — writes a styled pivot table into an existing worksheet."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    NEG_COLS    = {"(-ve)", "Hallucination", "Regression", "Missed", "Mismatch"}
    POS_COLS    = {"(+ve)", "Exact Match", "Match", "Pass"}
    DARK_SLATE  = "2C3E50"
    RED_HEX     = "C0392B"
    GREEN_HEX   = "1E8449"

    cols = list(pivot.columns)
    ws_s.append(cols)

    for i, cell in enumerate(ws_s[1]):
        col_name = cols[i] if i < len(cols) else ""
        fill_hex = DARK_SLATE
        if any(k in col_name for k in NEG_COLS):
            fill_hex = RED_HEX
        elif any(k in col_name for k in POS_COLS):
            fill_hex = GREEN_HEX
        cell.font      = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
        cell.fill      = PatternFill("solid", fgColor=fill_hex)
        cell.border    = border
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    ws_s.row_dimensions[1].height = 30

    ALT_A = PatternFill("solid", fgColor="F2F2F2")
    ALT_B = PatternFill("solid", fgColor="FFFFFF")

    for r_idx, row in enumerate(pivot.itertuples(index=False), start=2):
        band = ALT_A if r_idx % 2 == 0 else ALT_B
        for c_idx, col_name in enumerate(cols, start=1):
            val  = row[c_idx - 1]
            cell = ws_s.cell(row=r_idx, column=c_idx, value=val)
            cell.border    = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if col_name.endswith("%") and isinstance(val, (int, float)):
                if val >= 80:
                    cell.fill = PatternFill("solid", fgColor="D5F5E3")
                    cell.font = Font(size=10, bold=True, color="1E8449",
                                     name="Calibri")
                elif val >= 40:
                    cell.fill = PatternFill("solid", fgColor="FDEBD0")
                    cell.font = Font(size=10, bold=True, color="784212",
                                     name="Calibri")
                else:
                    cell.fill = PatternFill("solid", fgColor="FADBD8")
                    cell.font = Font(size=10, bold=True, color="922B21",
                                     name="Calibri")
            elif c_idx == 1:
                cell.fill = PatternFill("solid", fgColor="D6EAF8")
                cell.font = Font(size=10, bold=True, name="Calibri")
            else:
                cell.fill = band
                cell.font = Font(size=10, name="Calibri")
        ws_s.row_dimensions[r_idx].height = 20

    for c_idx, col_name in enumerate(cols, start=1):
        if c_idx == 1:
            ws_s.column_dimensions[get_column_letter(c_idx)].width = 42
        elif col_name.endswith("%"):
            ws_s.column_dimensions[get_column_letter(c_idx)].width = 14
        else:
            ws_s.column_dimensions[get_column_letter(c_idx)].width = 18


def print_ab_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 65)
    print("A/B COMPARISON SUMMARY")
    print("=" * 65)

    std_df = df[df["section"] == "Standard"]
    if not std_df.empty:
        total = len(std_df)
        print(f"\n── Standard Fields  (n={total} field-resume pairs) ──")
        for cls in ["Match", "Partial Match", "Regression", "Missing in B", "Other"]:
            count = (std_df["classification"] == cls).sum()
            if count:
                print(f"  {cls:<28s} {count:4d}  ({count / total * 100:5.1f}%)")
        print(f"\n  Per-field summary (Pass / Partial / Regression / Missing):")
        for fname, grp in std_df.groupby("field"):
            n  = len(grp)
            p  = (grp["classification"] == "Match").sum()
            pm = (grp["classification"] == "Partial Match").sum()
            r  = (grp["classification"] == "Regression").sum()
            mb = (grp["classification"] == "Missing in B").sum()
            print(f"    {fname:<30s}  Pass={p:3d}  Partial={pm:3d}"
                  f"  Regression={r:3d}  Missing={mb:3d}  (n={n})")

    cst_df = df[df["section"] == "Custom"]
    if not cst_df.empty:
        total = len(cst_df)
        print(f"\n── Custom Fields  (n={total} field-resume pairs) ──")
        for cls in ["Exact Match", "Partial Match (+ve)", "Partial Match (-ve)",
                    "Partial Match", "Missed", "Hallucination", "Both Empty"]:
            count = (cst_df["classification"] == cls).sum()
            if count:
                print(f"  {cls:<32s} {count:4d}  ({count / total * 100:5.1f}%)")
        print(f"\n  Per-field exact match %:")
        for fname, grp in cst_df.groupby("field"):
            pct = round((grp["classification"] == "Exact Match").sum() / len(grp) * 100, 1)
            print(f"    {fname:<50s} {pct:5.1f}%")

    print("=" * 65)


def main():
    print("=" * 65)
    print("A/B Comparison Pipeline")
    print("=" * 65)

    print("\n[Auth] Fetching tokens ...")
    token_a = get_token(BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A)
    token_b = get_token(BASE_URL_SS,      EMAIL_SS,      PASSWORD_SS)
    print("  Track A token OK | Track B token OK")

    baseline = load_or_run_baseline(token_a)
    valid    = sum(1 for r in baseline if "error" not in r)
    print(f"[Baseline] {valid}/{len(baseline)} resumes OK")

    try:
        custom_fields = load_custom_fields_from_manifest()
    except FileNotFoundError as e:
        print(f"\n[Setup] {e}")
        return
    print(f"[Setup] Loaded {len(custom_fields)} custom field(s)")

    ground_truth = load_ground_truth(baseline, custom_fields)

    if AB_RESULTS_FILE.exists():
        print(f"[A/B Parse] Loading cached results → {AB_RESULTS_FILE}")
        ab_results = json.loads(AB_RESULTS_FILE.read_text(encoding="utf-8"))
    else:
        ab_results = run_ab_parse(token_a, token_b, baseline, ground_truth)

    print("\n[Validation] Building comparison ...")
    df = run_ab_validation(ab_results, custom_fields=custom_fields)

    build_ab_report(df)
    print_ab_summary(df)


if __name__ == "__main__":
    main()

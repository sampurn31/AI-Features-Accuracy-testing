#!/usr/bin/env python3
"""
Ground-Truth Rescoring of SambaNova vs Production Comparison
-------------------------------------------------------------
Reads each resume PDF, extracts ground truth text, then re-evaluates
both Production and SambaNova outputs against what's actually in the resume.

Scoring (per field, for EACH provider):
  +1  = correctly extracted (matches ground truth)
   0  = partially correct or formatting difference
  -1  = wrong or missing when data exists in resume

Output: SambaNova_GroundTruth_Report.xlsx
"""

import os, re, json, subprocess
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from pathlib import Path

RESUME_FOLDER = Path(os.getenv("RESUME_FOLDER", Path(__file__).parent / "ss5_Test_Data"))
ORIGINAL_REPORT = "SambaNova_Comparison_Report.xlsx"
OUTPUT_REPORT = "SambaNova_GroundTruth_Report.xlsx"

FIELDS = [
    "fullName", "firstName", "lastName", "email", "phone",
    "city", "state", "country", "currentTitle", "currentOrg",
    "workExpYears", "skills", "specialization", "summary",
    "workHistory", "education",
]

CRITICAL_FIELDS = {"fullName", "email", "phone", "city"}

# ─── PDF Text Extraction ───────────────────────────────────────────

def extract_pdf_text(file_path: Path) -> str:
    """Extract text from PDF using python. Falls back to reading raw bytes."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(file_path))
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except ImportError:
        pass

    try:
        result = subprocess.run(
            ["python3", "-c", f"from pathlib import Path; print('cannot extract')"],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        pass

    # Fallback: read raw file and extract printable strings
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
        # Extract text between parentheses (PDF text objects)
        text_parts = re.findall(rb'\(([^\)]{2,})\)', raw)
        return " ".join(p.decode("latin-1", errors="ignore") for p in text_parts)
    except Exception:
        return ""


def read_file_as_text(file_path: Path) -> str:
    """Read a resume file as text (PDF or DOC)."""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(file_path)
    elif suffix in (".doc", ".docx"):
        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(file_path)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass
    # Last resort
    try:
        with open(file_path, "r", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


# ─── Ground Truth Comparison Logic ──────────────────────────────────

def _norm(v: str) -> str:
    return re.sub(r'\s+', ' ', str(v or "")).strip().lower()

def _norm_phone(v: str) -> str:
    return re.sub(r'\D', '', str(v or ""))

def _text_contains(haystack: str, needle: str) -> bool:
    """Check if the resume text contains the needle (fuzzy)."""
    if not needle or needle.lower() in ('', 'none', 'null', '(empty)'):
        return False
    h = _norm(haystack)
    n = _norm(needle)
    if not n:
        return False
    # Direct substring
    if n in h:
        return True
    # Check individual words (at least 70% of words present)
    words = [w for w in n.split() if len(w) > 2]
    if not words:
        return False
    found = sum(1 for w in words if w in h)
    return found / len(words) >= 0.6

def _phone_in_text(resume_text: str, phone_val: str) -> bool:
    """Check if phone digits appear in the resume text."""
    digits = _norm_phone(phone_val)
    if len(digits) < 7:
        return False
    text_digits = _norm_phone(resume_text)
    return digits[-10:] in text_digits or digits in text_digits

def _name_in_text(resume_text: str, name_val: str) -> bool:
    """Check if a name appears in the resume text."""
    if not name_val or name_val.lower() in ('', 'none', 'null', 'not available'):
        return False
    norm_text = _norm(resume_text)
    norm_name = _norm(name_val)
    if norm_name in norm_text:
        return True
    # Check parts
    parts = [p for p in norm_name.split() if len(p) > 1]
    if not parts:
        return False
    found = sum(1 for p in parts if p in norm_text)
    return found / len(parts) >= 0.7

def _email_in_text(resume_text: str, email_val: str) -> bool:
    """Check if an email appears in the resume text."""
    if not email_val:
        return False
    return _norm(email_val) in _norm(resume_text)

def _value_is_empty(v: str) -> bool:
    return _norm(v) in ('', 'none', 'null', 'not available', '(empty)', '—')

def score_field_against_truth(field: str, value: str, resume_text: str) -> tuple:
    """
    Score a single field value against the resume ground truth.
    Returns (score, reason).
    
    Scores:
      1 = correct (value found in resume)
      0 = neutral (empty when nothing to extract, or partial match)
     -1 = wrong (value NOT in resume but provider returned something)
     -2 = missing (data IS in resume but provider returned empty)
    """
    is_empty = _value_is_empty(value)
    
    if field in ("fullName", "firstName", "lastName"):
        if is_empty:
            return (-2, "missing - resume has a name")
        if _name_in_text(resume_text, value):
            return (1, "correct")
        # Check for corrupted parsing (numbers as name, duplicated chars)
        if re.match(r'^[\d\s]+$', value.strip()):
            return (-1, "wrong - parsed numbers as name")
        # Check for character duplication
        if len(set(value.lower())) < len(value) / 3:
            return (-1, "wrong - character duplication")
        return (-1, f"wrong - '{value[:30]}' not in resume")

    elif field == "email":
        if is_empty:
            # Check if resume has an email
            emails_in_resume = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', resume_text)
            if emails_in_resume:
                return (-2, "missing - resume has email")
            return (0, "neutral - no email in resume")
        if _email_in_text(resume_text, value):
            return (1, "correct")
        return (-1, f"wrong - '{value[:30]}' not in resume")

    elif field == "phone":
        if is_empty:
            phones_in_resume = re.findall(r'[\+\d][\d\s\-\(\)\.]{6,}', resume_text)
            if phones_in_resume:
                return (-2, "missing - resume has phone")
            return (0, "neutral - no phone in resume")
        if _phone_in_text(resume_text, value):
            return (1, "correct")
        return (-1, f"wrong - digits not in resume")

    elif field in ("city", "state", "country"):
        if is_empty:
            return (0, "empty")
        if _text_contains(resume_text, value):
            return (1, "correct")
        # Location might be inferred - softer check
        return (0, "uncertain - may be inferred")

    elif field in ("currentTitle", "currentOrg"):
        if is_empty:
            return (0, "empty")
        if _text_contains(resume_text, value):
            return (1, "correct")
        return (-1, f"wrong - not in resume")

    elif field == "workExpYears":
        # Hard to verify from text alone
        return (0, "unverifiable")

    elif field == "skills":
        if is_empty:
            # Check if resume mentions skills
            has_skills_section = bool(re.search(
                r'skill|competenc|expertise|proficien',
                resume_text, re.IGNORECASE
            ))
            if has_skills_section:
                return (-2, "missing - resume has skills")
            return (0, "neutral")
        # Skills present - check if some of the extracted skills appear in resume
        skill_words = [s.strip() for s in value.split(",") if len(s.strip()) > 2]
        if not skill_words:
            return (0, "empty skills")
        found = sum(1 for s in skill_words[:10] if _text_contains(resume_text, s))
        ratio = found / min(len(skill_words), 10)
        if ratio >= 0.3:
            return (1, f"correct ({found}/{min(len(skill_words),10)} found)")
        return (0, f"partial ({found}/{min(len(skill_words),10)} found)")

    elif field in ("workHistory", "education"):
        if is_empty:
            return (0, "empty")
        # Check word overlap
        val_words = set(_norm(value).split())
        text_words = set(_norm(resume_text).split())
        if not val_words:
            return (0, "empty")
        overlap = len(val_words & text_words) / len(val_words)
        if overlap >= 0.4:
            return (1, f"correct (overlap {overlap:.0%})")
        elif overlap >= 0.2:
            return (0, f"partial (overlap {overlap:.0%})")
        return (-1, f"wrong (overlap {overlap:.0%})")

    elif field == "summary":
        return (0, "neutral")
    elif field == "specialization":
        return (0, "neutral")

    return (0, "unscored")


# ─── Main Processing ────────────────────────────────────────────────

def main():
    print("Loading original report...")
    wb_orig = openpyxl.load_workbook(ORIGINAL_REPORT)
    
    # Extract original report data
    report_data = {}
    for sheet_name in wb_orig.sheetnames:
        if sheet_name == "Summary":
            continue
        ws = wb_orig[sheet_name]
        fields = {}
        for row in range(2, ws.max_row + 1):
            field = ws.cell(row, 1).value
            prod = ws.cell(row, 2).value
            ss5 = ws.cell(row, 3).value
            match_type = ws.cell(row, 4).value
            orig_score = ws.cell(row, 5).value
            if field and match_type:
                fields[field] = {
                    "prod": str(prod) if prod else "",
                    "ss5": str(ss5) if ss5 else "",
                    "match_type": match_type,
                    "original_score": orig_score or 0,
                }
        report_data[sheet_name] = fields

    print(f"Loaded data for {len(report_data)} resumes")

    # Process each resume
    results = []
    for resume_name in sorted(report_data.keys(), key=lambda x: (len(x), x)):
        file_path = RESUME_FOLDER / resume_name
        if not file_path.exists():
            print(f"  SKIP {resume_name} - file not found")
            continue

        print(f"  Processing {resume_name}...")
        resume_text = read_file_as_text(file_path)
        if not resume_text or len(resume_text) < 50:
            print(f"    WARNING: Could not extract text ({len(resume_text)} chars)")

        fields = report_data[resume_name]
        row_results = []
        prod_total = 0
        ss5_total = 0
        prod_critical_fails = []
        ss5_critical_fails = []

        for field in FIELDS:
            if field not in fields:
                continue

            prod_val = fields[field]["prod"]
            ss5_val = fields[field]["ss5"]
            orig_match = fields[field]["match_type"]
            orig_score = fields[field]["original_score"]

            # Score each provider against ground truth
            prod_score, prod_reason = score_field_against_truth(field, prod_val, resume_text)
            ss5_score, ss5_reason = score_field_against_truth(field, ss5_val, resume_text)

            # Comparative score: positive = ss5 better, negative = prod better
            comparative = ss5_score - prod_score
            
            prod_total += prod_score
            ss5_total += ss5_score

            if field in CRITICAL_FIELDS:
                if prod_score < 0:
                    prod_critical_fails.append(field)
                if ss5_score < 0:
                    ss5_critical_fails.append(field)

            # Determine who wins this field
            if ss5_score > prod_score:
                winner = "SambaNova"
            elif prod_score > ss5_score:
                winner = "Production"
            else:
                winner = "Tie"

            row_results.append({
                "field": field,
                "prod_val": prod_val,
                "ss5_val": ss5_val,
                "orig_match": orig_match,
                "orig_score": orig_score,
                "prod_gt_score": prod_score,
                "ss5_gt_score": ss5_score,
                "prod_reason": prod_reason,
                "ss5_reason": ss5_reason,
                "comparative": comparative,
                "winner": winner,
            })

        # Determine revised verdict
        net_score = ss5_total - prod_total
        if ss5_critical_fails:
            if net_score >= 0 and not prod_critical_fails:
                revised_verdict = "MIXED - ss5 critical fail but overall better"
            else:
                revised_verdict = "FAIL - ss5 has critical issues"
        elif ss5_total >= prod_total:
            if ss5_total > prod_total:
                revised_verdict = "SS5 WINS"
            else:
                revised_verdict = "TIE"
        else:
            if prod_total - ss5_total <= 2:
                revised_verdict = "SLIGHT PROD ADVANTAGE"
            else:
                revised_verdict = "PROD WINS"

        results.append({
            "resume": resume_name,
            "rows": row_results,
            "prod_total": prod_total,
            "ss5_total": ss5_total,
            "net_score": net_score,
            "prod_critical_fails": prod_critical_fails,
            "ss5_critical_fails": ss5_critical_fails,
            "revised_verdict": revised_verdict,
            "text_len": len(resume_text),
        })

    # ─── Generate Excel Report ──────────────────────────────────────

    print("\nGenerating Excel report...")
    
    BLUE_HDR = PatternFill("solid", fgColor="1F4E79")
    BLUE_FONT = Font(bold=True, color="FFFFFF", size=10)
    GREEN_FILL = PatternFill("solid", fgColor="E2EFDA")
    RED_FILL = PatternFill("solid", fgColor="FFDDC1")
    YELLOW_FILL = PatternFill("solid", fgColor="FFFFE0")
    ORANGE_FILL = PatternFill("solid", fgColor="FFD580")
    PURPLE_FILL = PatternFill("solid", fgColor="E8D5F5")
    BOLD = Font(bold=True)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # ─── Summary Sheet ──────────────────────────────────────────────

    ws_sum = wb.create_sheet("Summary", 0)
    sum_headers = [
        "Resume",
        "Original Score", "Original Verdict",
        "Prod GT Score", "SS5 GT Score", "Net (SS5-Prod)",
        "Revised Verdict",
        "Prod Critical Fails", "SS5 Critical Fails",
        "Text Extracted",
    ]
    for c, h in enumerate(sum_headers, 1):
        cell = ws_sum.cell(row=1, column=c, value=h)
        cell.fill = BLUE_HDR
        cell.font = BLUE_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    for row_idx, r in enumerate(results, start=2):
        orig_fields = report_data.get(r["resume"], {})
        orig_total = sum(f.get("original_score", 0) for f in orig_fields.values())
        
        orig_verdict_map = {
            name: wb_orig[name].cell(
                len([f for f in FIELDS if f in orig_fields]) + 5, 2
            ).value
            for name in [r["resume"]]
            if name in wb_orig.sheetnames
        }
        
        # Get original verdict from summary
        ws_orig_sum = wb_orig["Summary"]
        orig_verdict = ""
        for orow in range(2, ws_orig_sum.max_row + 1):
            if ws_orig_sum.cell(orow, 1).value == r["resume"]:
                orig_verdict = ws_orig_sum.cell(orow, 9).value or ""
                orig_total = ws_orig_sum.cell(orow, 8).value or 0
                break

        values = [
            r["resume"],
            orig_total, orig_verdict,
            r["prod_total"], r["ss5_total"], r["net_score"],
            r["revised_verdict"],
            ", ".join(r["prod_critical_fails"]) or "None",
            ", ".join(r["ss5_critical_fails"]) or "None",
            f"{r['text_len']} chars",
        ]

        verdict = r["revised_verdict"]
        if "SS5 WINS" in verdict:
            fill = GREEN_FILL
        elif "TIE" in verdict:
            fill = YELLOW_FILL
        elif "PROD WINS" in verdict or "SLIGHT PROD" in verdict:
            fill = ORANGE_FILL
        elif "MIXED" in verdict:
            fill = PURPLE_FILL
        else:
            fill = RED_FILL

        for c, v in enumerate(values, 1):
            cell = ws_sum.cell(row=row_idx, column=c, value=v)
            cell.fill = fill

    # Stats
    stats_row = len(results) + 3
    total = len(results)
    ss5_wins = sum(1 for r in results if "SS5 WINS" in r["revised_verdict"])
    prod_wins = sum(1 for r in results if "PROD WINS" in r["revised_verdict"])
    slight_prod = sum(1 for r in results if "SLIGHT PROD" in r["revised_verdict"])
    ties = sum(1 for r in results if "TIE" in r["revised_verdict"])
    mixed = sum(1 for r in results if "MIXED" in r["revised_verdict"])
    fails = sum(1 for r in results if "FAIL" in r["revised_verdict"])

    stats = [
        ("─── REVISED GROUND-TRUTH SUMMARY ───", ""),
        ("Total Resumes", total),
        ("SS5 WINS (ss5 closer to truth)", f"{ss5_wins} / {total} ({ss5_wins/total*100:.1f}%)"),
        ("TIE", f"{ties} / {total} ({ties/total*100:.1f}%)"),
        ("SLIGHT PROD ADVANTAGE", f"{slight_prod} / {total} ({slight_prod/total*100:.1f}%)"),
        ("PROD WINS", f"{prod_wins} / {total} ({prod_wins/total*100:.1f}%)"),
        ("MIXED (ss5 overall better but critical fail)", f"{mixed} / {total} ({mixed/total*100:.1f}%)"),
        ("SS5 CRITICAL FAIL", f"{fails} / {total} ({fails/total*100:.1f}%)"),
        ("", ""),
        ("Avg Prod GT Score", f"{sum(r['prod_total'] for r in results)/total:.1f}"),
        ("Avg SS5 GT Score", f"{sum(r['ss5_total'] for r in results)/total:.1f}"),
        ("", ""),
        ("Prod critical failures (total fields)", sum(len(r["prod_critical_fails"]) for r in results)),
        ("SS5 critical failures (total fields)", sum(len(r["ss5_critical_fails"]) for r in results)),
    ]
    for i, (label, value) in enumerate(stats):
        ws_sum.cell(row=stats_row + i, column=1, value=label).font = BOLD
        ws_sum.cell(row=stats_row + i, column=2, value=str(value) if value != "" else "")

    # Auto-width summary
    for col in ws_sum.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=10)
        ws_sum.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 60)

    # ─── Per-Resume Sheets ──────────────────────────────────────────

    for r in results:
        safe_name = re.sub(r'[\\/*?:\[\]]', '_', r["resume"])[:31]
        ws = wb.create_sheet(title=safe_name)

        headers = [
            "Field", "Production Value", "SS5 Value",
            "Original Match", "Original Score",
            "Prod GT Score", "SS5 GT Score",
            "Prod Reason", "SS5 Reason",
            "Comparative (+ss5)", "Winner",
        ]
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.fill = BLUE_HDR
            cell.font = BLUE_FONT
            cell.alignment = Alignment(horizontal="center", wrap_text=True)

        for row_idx, row_data in enumerate(r["rows"], start=2):
            values = [
                row_data["field"],
                row_data["prod_val"][:200] if row_data["prod_val"] else "",
                row_data["ss5_val"][:200] if row_data["ss5_val"] else "",
                row_data["orig_match"],
                row_data["orig_score"],
                row_data["prod_gt_score"],
                row_data["ss5_gt_score"],
                row_data["prod_reason"],
                row_data["ss5_reason"],
                row_data["comparative"],
                row_data["winner"],
            ]

            winner = row_data["winner"]
            if winner == "SambaNova":
                fill = GREEN_FILL
            elif winner == "Production":
                fill = ORANGE_FILL
            else:
                fill = YELLOW_FILL

            for c, v in enumerate(values, 1):
                cell = ws.cell(row=row_idx, column=c, value=v)
                cell.fill = fill

        # Meta block
        meta_start = len(r["rows"]) + 3
        meta = [
            ("PRODUCTION GT SCORE", r["prod_total"]),
            ("SS5 GT SCORE", r["ss5_total"]),
            ("NET SCORE (SS5 - PROD)", r["net_score"]),
            ("REVISED VERDICT", r["revised_verdict"]),
            ("PROD CRITICAL FAILS", ", ".join(r["prod_critical_fails"]) or "None"),
            ("SS5 CRITICAL FAILS", ", ".join(r["ss5_critical_fails"]) or "None"),
        ]
        for i, (label, value) in enumerate(meta):
            ws.cell(row=meta_start + i, column=1, value=label).font = BOLD
            ws.cell(row=meta_start + i, column=2, value=str(value))

        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=10)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 60)

    # ─── Save ───────────────────────────────────────────────────────

    wb.save(OUTPUT_REPORT)
    print(f"\nSaved: {OUTPUT_REPORT}")
    print(f"\n{'='*60}")
    print("GROUND-TRUTH COMPARISON RESULTS")
    print(f"{'='*60}")
    print(f"SS5 WINS:             {ss5_wins} / {total} ({ss5_wins/total*100:.1f}%)")
    print(f"TIE:                  {ties} / {total} ({ties/total*100:.1f}%)")
    print(f"SLIGHT PROD:          {slight_prod} / {total} ({slight_prod/total*100:.1f}%)")
    print(f"PROD WINS:            {prod_wins} / {total} ({prod_wins/total*100:.1f}%)")
    print(f"MIXED:                {mixed} / {total} ({mixed/total*100:.1f}%)")
    print(f"SS5 FAIL:             {fails} / {total} ({fails/total*100:.1f}%)")
    print(f"{'='*60}")
    print(f"Avg Prod GT Score:    {sum(r['prod_total'] for r in results)/total:.2f}")
    print(f"Avg SS5 GT Score:     {sum(r['ss5_total'] for r in results)/total:.2f}")
    print(f"Prod critical fails:  {sum(len(r['prod_critical_fails']) for r in results)}")
    print(f"SS5 critical fails:   {sum(len(r['ss5_critical_fails']) for r in results)}")

    # Per-field analysis
    print(f"\n{'='*60}")
    print("PER-FIELD GROUND-TRUTH ANALYSIS")
    print(f"{'='*60}")
    for field in FIELDS:
        prod_scores = []
        ss5_scores = []
        ss5_wins_f = 0
        prod_wins_f = 0
        ties_f = 0
        for r in results:
            for row_data in r["rows"]:
                if row_data["field"] == field:
                    prod_scores.append(row_data["prod_gt_score"])
                    ss5_scores.append(row_data["ss5_gt_score"])
                    if row_data["winner"] == "SambaNova":
                        ss5_wins_f += 1
                    elif row_data["winner"] == "Production":
                        prod_wins_f += 1
                    else:
                        ties_f += 1
        if prod_scores:
            prod_avg = sum(prod_scores) / len(prod_scores)
            ss5_avg = sum(ss5_scores) / len(ss5_scores)
            print(f"  {field:<15}: Prod={prod_avg:+.2f} SS5={ss5_avg:+.2f} | "
                  f"SS5 wins={ss5_wins_f} Prod wins={prod_wins_f} Ties={ties_f}")


if __name__ == "__main__":
    main()

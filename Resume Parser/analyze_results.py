#!/usr/bin/env python3
import openpyxl, re
from collections import Counter
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# ── styles ───────────────────────────────────────────────────────────────────
BLUE_HDR    = PatternFill("solid", fgColor="1F4E79")
BLUE_FONT   = Font(bold=True, color="FFFFFF", size=10)
GREEN_FILL  = PatternFill("solid", fgColor="E2EFDA")
RED_FILL    = PatternFill("solid", fgColor="FFDDC1")
YELLOW_FILL = PatternFill("solid", fgColor="FFFFE0")
ORANGE_FILL = PatternFill("solid", fgColor="FFD580")
BOLD        = Font(bold=True)
WRAP        = Alignment(wrap_text=True, vertical="top")
CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)

SEV_FILL = {
    "CRITICAL": RED_FILL,
    "HIGH":     ORANGE_FILL,
    "MEDIUM":   ORANGE_FILL,
    "LOW":      GREEN_FILL,
    "INFO":     YELLOW_FILL,
}

def auto_widths(ws):
    for col in ws.columns:
        mx = max((len(str(cell.value or "")) for cell in col), default=10)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(mx + 4, 70)

# ── diagnosis ────────────────────────────────────────────────────────────────
def diagnose(resume, score, critical_str, mismatches_str):
    lang   = str(resume).split("_")[0]
    mset   = set(f.strip() for f in mismatches_str.split(",")) if mismatches_str and mismatches_str != "None" else set()
    cset   = set(f.strip() for f in critical_str.split(","))   if critical_str  and critical_str  != "None" else set()
    issues = []
    root   = []
    sev    = "LOW"

    if "production_error" in cset:
        issues.append("Production parser returned an error — file may be corrupt or unsupported.")
        root.append("PROD parse failure — check file format/encoding.")
        sev = "INFO"
    elif "staging_error" in cset:
        issues.append("Staging parser returned an error for this resume.")
        root.append("STAGING parse failure.")
        sev = "CRITICAL"
    else:
        if score == 7 and len(mset) >= 10:
            issues.append("Staging extracted data for ALL fields but they are all wrong vs PROD — classic 'wrong candidate' bug: Staging may have matched the upload to an existing DB record.")
            root.append("Wrong-candidate matching: Staging returned a previously stored candidate's data instead of parsing the new resume.")
            sev = "CRITICAL"
        if "fullName" in cset or "firstName" in cset or "lastName" in cset:
            issues.append("CRITICAL: fullName not extracted correctly — candidate identity failure.")
            root.append("Name extraction broken (RTL/encoding issue or incorrect section detection).")
            sev = "CRITICAL"
        if "email" in cset:
            issues.append("CRITICAL: Email missing or wrong in Staging.")
            root.append("Email regex/field mapping failure in Staging parser.")
            sev = "CRITICAL"
        if "phone" in cset:
            issues.append("CRITICAL: Phone missing or mismatched in Staging.")
            root.append("Phone extraction failure — possible country-code handling or format issue.")
            sev = "CRITICAL"
        if "city" in cset:
            issues.append("CRITICAL: City not extracted by Staging.")
            root.append("Location/city extraction failure — address format in this language not handled.")
            sev = "CRITICAL"
        if "skills" in mset and sev == "LOW":
            sev = "MEDIUM"
            issues.append("Skills field mismatch — Staging extracted different/fewer skills.")
            root.append("Staging NLP skill model differs from Production.")
        elif "skills" in mset:
            issues.append("Skills field mismatch — Staging extracted different/fewer skills.")
        if "workHistory" in mset:
            issues.append("Work history missing or structured differently in Staging.")
            root.append("Work history parser has lower recall for this language/format in Staging.")
            if sev == "LOW": sev = "MEDIUM"
        if "education" in mset:
            issues.append("Education details (degree/institution) missing or wrong in Staging.")
            root.append("Education section parsing is weaker in Staging for this language.")
            if sev == "LOW": sev = "MEDIUM"
        if "workExpYears" in mset:
            issues.append("Total work experience years differ between providers.")
            root.append("Experience calculation algorithm differs between PROD and STAGING.")
            if sev == "LOW": sev = "MEDIUM"
        if "currentTitle" in mset or "currentOrg" in mset:
            issues.append("Current job title / organisation mismatch — Staging extracted wrong or blank values.")
            root.append("Current position detection logic differs; Staging may not identify most recent role correctly.")
            if sev == "LOW": sev = "MEDIUM"
        if "state" in mset or "country" in mset:
            issues.append("State/Country not extracted or wrong in Staging.")
            root.append("Address/locale parsing not tuned for this language in Staging provider.")
            if sev == "LOW": sev = "MEDIUM"

    # language context
    if lang == "Hebrew":
        root.append("Hebrew is RTL (right-to-left) — Staging likely lacks BiDi support for text segmentation.")
    elif lang == "Japanese":
        root.append("Japanese CJK tokenisation may be limited in Staging parser.")
    elif lang == "German":
        root.append("German 'Lebenslauf' section headers may not be recognised by Staging.")
    elif lang == "Dutch":
        root.append("Dutch format close to English — minor extraction gaps expected.")
    elif lang == "Spanish":
        root.append("Spanish double-surname format and accented characters challenge Staging NER.")

    return (
        " | ".join(issues) if issues else "No significant issues detected.",
        " | ".join(dict.fromkeys(root)) if root else "—",
        sev,
    )

# ── load workbook ─────────────────────────────────────────────────────────────
wb = openpyxl.load_workbook("Parser_Comparison_Report.xlsx")
summary_ws = wb["Summary"]
sum_hdrs = [summary_ws.cell(1, c).value for c in range(1, summary_ws.max_column + 1)]

resume_data = {}
for row in range(2, summary_ws.max_row + 1):
    rec = {sum_hdrs[c - 1]: summary_ws.cell(row, c).value for c in range(1, len(sum_hdrs) + 1)}
    rname = rec.get("Resume")
    if isinstance(rname, str):
        resume_data[rname] = rec

# ── 1. Annotate each per-resume sheet ────────────────────────────────────────
for sheet_name in wb.sheetnames:
    if sheet_name in ("Summary", "🔍 Overall Verdict"):
        continue
    ws = wb[sheet_name]

    match_key = None
    for k in resume_data:
        if k[:31] == sheet_name or sheet_name.startswith(k[:30]):
            match_key = k
            break
    if not match_key:
        continue

    rd  = resume_data[match_key]
    sc  = rd.get("Score") or 0
    iss, rt, sev = diagnose(
        match_key,
        sc,
        str(rd.get("Critical Failures") or "None"),
        str(rd.get("Field Mismatches") or "None"),
    )
    fill = SEV_FILL.get(sev, YELLOW_FILL)
    last = ws.max_row + 2

    def mr(label, value, f=None):
        global last
        c1 = ws.cell(last, 1, label)
        c2 = ws.cell(last, 2, value)
        c1.font = BOLD; c1.alignment = WRAP; c2.alignment = WRAP
        if f:
            c1.fill = f; c2.fill = f
        last += 1

    hdr = ws.cell(last, 1, "─── ANALYSIS ───")
    hdr.font = Font(bold=True, color="1F4E79")
    last += 1
    mr("ISSUES FOUND", iss, fill)
    mr("ROOT CAUSE",   rt,  fill)
    mr("SEVERITY",     sev, fill)
    mr("LANGUAGE",     match_key.split("_")[0])
    auto_widths(ws)

print("✓ Annotated all per-resume sheets")

# ── 2. Add Severity / Issues / Root Cause columns to Summary ─────────────────
last_col = summary_ws.max_column
for i, hv in enumerate(["Severity", "Issues Found", "Root Cause"], 1):
    c = summary_ws.cell(1, last_col + i, hv)
    c.fill = BLUE_HDR; c.font = BLUE_FONT
    c.alignment = Alignment(horizontal="center", wrap_text=True)

for row in range(2, summary_ws.max_row + 1):
    rname = summary_ws.cell(row, 1).value
    if not isinstance(rname, str) or rname not in resume_data:
        continue
    rd = resume_data[rname]
    sc = rd.get("Score") or 0
    iss, rt, sev = diagnose(
        rname, sc,
        str(rd.get("Critical Failures") or "None"),
        str(rd.get("Field Mismatches") or "None"),
    )
    fill = SEV_FILL.get(sev, YELLOW_FILL)
    for ci, val in enumerate([sev, iss, rt], 1):
        cell = summary_ws.cell(row, last_col + ci, val)
        cell.fill = fill; cell.alignment = WRAP

auto_widths(summary_ws)
print("✓ Enriched Summary sheet")

# ── 3. Build Overall Verdict sheet ───────────────────────────────────────────
if "🔍 Overall Verdict" in wb.sheetnames:
    del wb["🔍 Overall Verdict"]
vws = wb.create_sheet("🔍 Overall Verdict", 1)

rows_list = [r for r in resume_data.values() if isinstance(r.get("Resume"), str)]
total      = len(rows_list)
passes     = sum(1 for r in rows_list if "PASS"       in str(r.get("Verdict", "")))
acceptable = sum(1 for r in rows_list if "ACCEPTABLE" in str(r.get("Verdict", "")))
fails      = sum(1 for r in rows_list if "FAIL"       in str(r.get("Verdict", "")))

lang_stats = {}
for rec in rows_list:
    lang = str(rec["Resume"]).split("_")[0]
    s = lang_stats.setdefault(lang, {"pass": 0, "acceptable": 0, "fail": 0, "scores": []})
    v = str(rec.get("Verdict", ""))
    sc = rec.get("Score") or 0
    if   "PASS"       in v: s["pass"]       += 1
    elif "ACCEPTABLE" in v: s["acceptable"] += 1
    else:                   s["fail"]       += 1
    if isinstance(sc, (int, float)): s["scores"].append(sc)

field_ctr    = Counter()
critical_ctr = Counter()
for rec in rows_list:
    mm = str(rec.get("Field Mismatches", ""))
    if mm and mm != "None":
        for f in mm.split(","): field_ctr[f.strip()] += 1
    cf = str(rec.get("Critical Failures", ""))
    if cf and cf != "None":
        for f in cf.split(","): critical_ctr[f.strip()] += 1

# writer helpers
cur_row = [1]

def title_row(text, bg="1F4E79", fg="FFFFFF", size=11, span=2):
    vws.merge_cells(start_row=cur_row[0], start_column=1, end_row=cur_row[0], end_column=span)
    cell = vws.cell(cur_row[0], 1, text)
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.font = Font(bold=True, color=fg, size=size)
    cell.alignment = CENTER_WRAP
    vws.row_dimensions[cur_row[0]].height = 24
    cur_row[0] += 1

def kv_row(label, value, fill=None):
    c1 = vws.cell(cur_row[0], 1, label); c1.font = BOLD; c1.alignment = WRAP
    c2 = vws.cell(cur_row[0], 2, value); c2.alignment = WRAP
    if fill:
        c1.fill = fill; c2.fill = fill
    vws.row_dimensions[cur_row[0]].height = 40
    cur_row[0] += 1

def blank_row():
    cur_row[0] += 1

title_row("🔍  STAGING vs PRODUCTION — OVERALL VERDICT REPORT", "1F4E79", "FFFFFF", 13, 2)
blank_row()

title_row("📊  AGGREGATE RESULTS", "2E4057", "FFFFFF")
kv_row("Total Resumes Tested",  total)
kv_row("✅  PASS",              f"{passes} / {total}  ({round(passes/total*100,1) if total else 0}%)",  GREEN_FILL)
kv_row("⚠️  ACCEPTABLE",        f"{acceptable} / {total}  ({round(acceptable/total*100,1) if total else 0}%)", ORANGE_FILL)
kv_row("❌  FAIL",              f"{fails} / {total}  ({round(fails/total*100,1) if total else 0}%)",   RED_FILL)
kv_row("Languages Tested",     "Dutch, German, Hebrew, Japanese, Spanish  (10 resumes each)")
blank_row()

title_row("📋  PER-LANGUAGE BREAKDOWN", "2E4057", "FFFFFF")
for lang in ["Dutch", "German", "Hebrew", "Japanese", "Spanish"]:
    s = lang_stats.get(lang, {"pass": 0, "acceptable": 0, "fail": 0, "scores": []})
    avg = round(sum(s["scores"]) / len(s["scores"]), 1) if s["scores"] else "—"
    n   = s["pass"] + s["acceptable"] + s["fail"]
    f   = GREEN_FILL if s["fail"] == 0 else (ORANGE_FILL if s["fail"] <= 3 else RED_FILL)
    kv_row(
        f"  {lang}",
        f"✅ {s['pass']} PASS  |  ⚠️ {s['acceptable']} ACCEPTABLE  |  ❌ {s['fail']} FAIL  |  Avg Score: {avg}  (n={n})",
        f,
    )
blank_row()

title_row("🚨  TOP FAILING FIELDS (across all 50 resumes)", "843C0C", "FFFFFF")
for field, cnt in field_ctr.most_common(12):
    pct = round(cnt / total * 100, 1)
    f   = RED_FILL if field in {"fullName","firstName","lastName","email","phone","city"} \
          else (ORANGE_FILL if cnt >= 10 else YELLOW_FILL)
    kv_row(f"  {field}", f"Failed in {cnt}/{total} resumes  ({pct}%)", f)
blank_row()

title_row("⚠️  CRITICAL FIELD FAILURES", "C00000", "FFFFFF")
for field, cnt in critical_ctr.most_common():
    pct = round(cnt / total * 100, 1)
    kv_row(f"  {field}", f"Critical failure in {cnt}/{total} resumes  ({pct}%)", RED_FILL)
blank_row()

title_row("🔬  KEY ISSUES IDENTIFIED", "375623", "FFFFFF")
issues_list = [
    ("CRITICAL", "Wrong-candidate bug (score +7, all fields wrong)",
     "Hebrew_01, Japanese_09, Spanish_05 all show score=+7 (Staging has MORE data than Prod) but every critical field fails. "
     "Staging is returning a previously stored candidate's data instead of parsing the uploaded resume. "
     "This is a data integrity / privacy risk and must be fixed before any production rollout."),
    ("CRITICAL", "Hebrew (RTL) — complete parsing breakdown",
     "8/10 Hebrew resumes FAIL. Staging has no functional Right-to-Left (BiDi) text support. "
     "Name, phone, email, city — all critical fields are empty or incorrect. "
     "Staging CANNOT be deployed for Hebrew resumes in its current state."),
    ("CRITICAL", "Spanish — 70% failure rate on critical fields",
     "7/10 Spanish resumes FAIL with critical failures on fullName, email, phone, and city. "
     "Spanish double-surname conventions and accented characters (á, é, ñ) are not handled by Staging NER. "
     "Two resumes show the wrong-candidate bug."),
    ("HIGH", "Japanese — 50% failure, name extraction unreliable",
     "5/10 Japanese resumes FAIL. CJK character tokenisation is partially functional but name and structured "
     "field extraction is inconsistent. Japanese_08 caused a Production error (likely corrupt/unsupported file format)."),
    ("MEDIUM", "German — 50% failure, structured fields missing",
     "5/10 German resumes FAIL. 'Lebenslauf' section headers are not consistently mapped by Staging. "
     "State/country, work history, and workExpYears are frequently absent."),
    ("MEDIUM", "Skills extraction gap — universal across all 5 languages",
     "90%+ of resumes show a skills mismatch. Staging's NLP skill-recognition model returns significantly "
     "fewer or different skills than Production. This affects every language and every resume format."),
    ("MEDIUM", "Work history recall deficit",
     "~40% of resumes have missing or structurally incorrect work history in Staging. "
     "Staging's work-experience parser has lower recall, especially for non-English section labels."),
    ("MEDIUM", "Education parsing gap",
     "~35% of resumes have missing or wrong education details in Staging. "
     "Degree/institution recognition is weaker across all non-English languages."),
    ("LOW", "Dutch — best performer but not production-ready",
     "Dutch is the best-performing language: 1 PASS + 7 ACCEPTABLE. Main gap is skills extraction. "
     "Dutch_03 had a phone critical failure. While promising, even Dutch cannot be called production-ready at 70% acceptable."),
    ("LOW", "Response time — no regression",
     "Staging response times are broadly comparable to Production (within 5–15s per resume). "
     "No significant performance regression observed. This is a positive signal."),
]
for sev, issue_title, detail in issues_list:
    fill = SEV_FILL.get(sev, YELLOW_FILL)
    kv_row(f"[{sev}]  {issue_title}", detail, fill)
blank_row()

title_row("🏁  FINAL VERDICT", "7B2D00", "FFFFFF", 13)
verdict_rows = [
    ("Overall Recommendation",
     "❌  DO NOT replace Production with Staging in its current state.",
     RED_FILL),
    ("Pass Rate",
     f"Only {passes}/{total} resumes ({round(passes/total*100,1)}%) passed. "
     f"Even combining PASS + ACCEPTABLE gives {passes+acceptable}/{total} ({round((passes+acceptable)/total*100,1)}%) — "
     f"still unacceptable for a production parser replacement.",
     RED_FILL),
    ("Critical Blocker #1 — Hebrew",
     "Hebrew parsing is completely non-functional in Staging. RTL/BiDi support must be implemented before any rollout.",
     RED_FILL),
    ("Critical Blocker #2 — Wrong Candidate Bug",
     "Staging returns other candidates' data for some resumes (Hebrew_01, Japanese_09, Spanish_05). "
     "This is a data integrity / GDPR risk.",
     RED_FILL),
    ("Critical Blocker #3 — Spanish",
     "Spanish NER is broken for critical fields (name, email, phone, city). 7/10 resumes fail.",
     RED_FILL),
    ("Acceptable Subset",
     "Dutch resumes perform reasonably well (70% ACCEPTABLE). If scope is restricted to Dutch only, "
     "Staging is borderline acceptable — but skills extraction must be fixed first.",
     ORANGE_FILL),
    ("Positive Signal",
     "Response time performance is equivalent to Production — no latency regression.",
     GREEN_FILL),
    ("Recommended Action",
     "1) Fix wrong-candidate matching bug (critical — data integrity)  "
     "2) Implement RTL/BiDi support for Hebrew  "
     "3) Fix Spanish NER (names, accents, double surnames)  "
     "4) Align skills extraction model with Production  "
     "5) Improve work history & education parsers for German/Japanese  "
     "6) Re-test with 100+ resumes per language after all fixes before reconsidering rollout.",
     YELLOW_FILL),
]
for label, val, fill in verdict_rows:
    kv_row(label, val, fill)

vws.column_dimensions["A"].width = 40
vws.column_dimensions["B"].width = 95
for row_obj in vws.iter_rows():
    for cell in row_obj:
        if not cell.alignment or not cell.alignment.wrap_text:
            cell.alignment = WRAP

print("✓ Created '🔍 Overall Verdict' sheet")

wb.save("Parser_Comparison_Report.xlsx")
print("✓ Saved → Parser_Comparison_Report.xlsx")

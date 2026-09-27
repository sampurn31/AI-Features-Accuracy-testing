#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Resume Parser – Production vs Staging Comparison
-------------------------------------------------
Sends each resume to both Production and Staging parsers in parallel,
then compares parsed results field-by-field.

Output: Parser_Comparison_Report.xlsx
  - Summary dashboard (first sheet) with verdicts and scores
  - One sheet per resume showing side-by-side field comparison
"""

import os, time, json, requests, logging, shutil, tempfile, datetime, re
import pandas as pd
import openpyxl
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────── CONFIG ────────────────────────────────────

RESUME_FOLDER = Path(os.getenv("RESUME_FOLDER", Path(__file__).parent / "Test_Data"))

# Production  (reference baseline)
PROD_EMAIL    = os.getenv("PROD_EMAIL", "")
PROD_PASSWORD = os.getenv("PROD_PASSWORD", "")
PROD_BASE     = "https://albatross.recruitcrm.io"

# Staging  (under test)
STAGE_EMAIL    = os.getenv("STAGE_EMAIL", "")
STAGE_PASSWORD = os.getenv("STAGE_PASSWORD", "")
STAGE_BASE     = "https://albatross-staging.recruitcrm.net"

BATCH_SIZE   = 20   # resumes processed concurrently per batch

RESULT_EXCEL = "Parser_Comparison_Report.xlsx"
LOGFILE      = "parser_comparison.log"

# Critical fields — staging failure on any of these → automatic FAIL
CRITICAL_FIELDS = {"fullName", "email", "phone", "city"}

# Verdict thresholds (total score)
PASS_THRESHOLD       = 0    # score >= 0  → ✅ PASS
ACCEPTABLE_THRESHOLD = -3   # score >= -3 → ⚠️  ACCEPTABLE, else ❌ FAIL

MIME_TYPES = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf":  "application/rtf",
}

# ─────────────────────────────── LOGGING ───────────────────────────────────

os.makedirs("logs", exist_ok=True)
os.makedirs("raw_responses", exist_ok=True)

logging.basicConfig(
    filename=f"logs/{LOGFILE}",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

_ILLEGAL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def sanitize(v):
    return _ILLEGAL_CHARS.sub('', v) if isinstance(v, str) else v


def sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.select_dtypes(include='object').columns:
        df[col] = df[col].map(sanitize)
    return df


# ─────────────────────────────── AUTH ──────────────────────────────────────

def login(base_url: str, email: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/v1/login",
        data={"email": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "data" in data and "token" in data["data"]:
        return data["data"]["token"]
    raise Exception(f"Token not found in login response: {data}")


# ─────────────────────────────── API HELPERS ───────────────────────────────

def get_presigned_url(base_url: str, file_name: str, token: str) -> dict:
    r = requests.get(
        f"{base_url}/v1/get-presigned-url",
        headers={"authorization": f"Bearer {token}"},
        params={"fileName": file_name, "requestType": "put"},
        timeout=30,
    )
    if r.status_code == 401:
        raise Exception("401 – token expired or invalid")
    r.raise_for_status()
    return r.json().get("data", r.json())


def upload_to_s3(put_url: str, file_path: Path) -> None:
    mime = MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    with open(file_path, "rb") as f:
        resp = requests.put(put_url, data=f, headers={"Content-Type": mime}, timeout=60)
    resp.raise_for_status()


def call_parser(base_url: str, token: str, file_key: str, file_name: str, file_size: int) -> dict:
    payload = {
        "resumeParserData": {
            "resumesParsed": 0,
            "resumesFailed": 0,
            "resumesTotal":  1,
            "filesInfo": {
                "key":   file_key,
                "name":  file_name,
                "type":  MIME_TYPES.get(Path(file_name).suffix.lower(), "application/octet-stream"),
                "size":  file_size,
                "index": 0,
            },
        },
        "actionid": 0,
    }
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    r       = requests.post(
        f"{base_url}/v1/candidates/parse-resume?actionsteps=1",
        headers=headers,
        json=payload,
        timeout=120,
    )
    sc = r.status_code
    try:
        data = r.json()
    except json.JSONDecodeError as e:
        raise Exception(f"Invalid JSON (HTTP {sc}): {e}. Body: {r.text[:300]}")
    data["_status_code"] = sc
    r.raise_for_status()
    return data


def fetch_work_history(base_url: str, token: str, candidate_id: int) -> list:
    try:
        r = requests.get(
            f"{base_url}/v1/candidates/candidate-work/{candidate_id}",
            headers={"authorization": f"Bearer {token}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        logging.warning(f"Work history fetch failed (id={candidate_id}): {e}")
        return []


def fetch_education_history(base_url: str, token: str, candidate_id: int) -> list:
    try:
        r = requests.get(
            f"{base_url}/v1/candidates/candidate-education/{candidate_id}",
            headers={"authorization": f"Bearer {token}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        logging.warning(f"Education history fetch failed (id={candidate_id}): {e}")
        return []


# ─────────────────────────────── DATA EXTRACTION ───────────────────────────

def extract_work_history(work_list: list) -> str:
    entries = []
    for w in work_list:
        title   = w.get("title", "")
        company = w.get("work_company_name", "")
        try:
            start = datetime.datetime.fromtimestamp(w["work_start_date"]).strftime("%Y") if w.get("work_start_date") else ""
        except Exception:
            start = ""
        try:
            end = datetime.datetime.fromtimestamp(w["work_end_date"]).strftime("%Y") if w.get("work_end_date") else "Present"
        except Exception:
            end = "Present"
        if title or company:
            entries.append(f"{title} at {company} ({start}–{end})")
    return " | ".join(entries)


def extract_education_history(edu_list: list) -> str:
    entries = []
    for e in edu_list:
        degree = e.get("educational_qualification", "")
        inst   = e.get("institute_name", "")
        spec   = e.get("educational_specialization", "")
        try:
            sy = datetime.datetime.fromtimestamp(e["education_start_date"]).strftime("%Y") if e.get("education_start_date") else "?"
        except Exception:
            sy = "?"
        try:
            ey = datetime.datetime.fromtimestamp(e["education_end_date"]).strftime("%Y") if e.get("education_end_date") else "?"
        except Exception:
            ey = "?"
        line = degree
        if spec: line += f" in {spec}"
        if inst:  line += f" from {inst}"
        line += f" ({sy}–{ey})"
        entries.append(line)
    return " | ".join(entries)


def extract_fields(candidate: dict, work_list: list, edu_list: list) -> dict:
    first = candidate.get("firstname", "") or ""
    last  = candidate.get("lastname",  "") or ""
    return {
        "fullName":       f"{first} {last}".strip(),
        "firstName":      first,
        "lastName":       last,
        "email":          candidate.get("emailid",          "") or "",
        "phone":          candidate.get("contactnumber",    "") or "",
        "city":           candidate.get("city",             "") or "",
        "state":          candidate.get("state",            "") or "",
        "country":        candidate.get("country",          "") or "",
        "currentTitle":   candidate.get("position",         "") or "",
        "currentOrg":     candidate.get("lastorganisation", "") or "",
        "workExpYears":   str(candidate.get("workexpyr",    "") or ""),
        "skills":         candidate.get("skill",            "") or "",
        "specialization": candidate.get("specialization",   "") or "",
        "summary":        "Yes" if candidate.get("summary") else "No",
        "workHistory":    extract_work_history(work_list),
        "education":      extract_education_history(edu_list),
    }


# ─────────────────────────────── PER-PARSER RUNNER ─────────────────────────

def parse_with(label: str, base_url: str, token: str, file_path: Path) -> dict:
    """
    Uploads resume to S3 via the given environment, calls the parser,
    fetches work/education history, and returns extracted fields.
    """
    start = time.time()
    try:
        presign = get_presigned_url(base_url, file_path.name, token)
        upload_to_s3(presign["preSignedUrl"], file_path)
        key = presign["key"]

        parse_result = call_parser(base_url, token, key, file_path.name, file_path.stat().st_size)
        status_code  = parse_result.get("_status_code", 200)

        raw_path = f"raw_responses/{file_path.stem}_{label}.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(parse_result, f, indent=2, ensure_ascii=False)

        candidate    = parse_result.get("data", {}).get("candidate", {})
        candidate_id = candidate.get("id")
        sovren_id    = candidate.get("sovren_document_id", "")

        work_list, edu_list = [], []
        if candidate_id:
            work_list = fetch_work_history(base_url, token, candidate_id)
            edu_list  = fetch_education_history(base_url, token, candidate_id)

        fields  = extract_fields(candidate, work_list, edu_list)
        elapsed = round(time.time() - start, 2)

        logging.info(f"[{label}] OK {file_path.name} in {elapsed}s (HTTP {status_code})")
        return {
            "fields":       fields,
            "status_code":  status_code,
            "elapsed":      elapsed,
            "candidate_id": candidate_id,
            "sovren_id":    sovren_id,
            "error":        None,
        }
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        logging.error(f"[{label}] FAILED {file_path.name}: {e}")
        return {
            "fields":       {},
            "status_code":  None,
            "elapsed":      elapsed,
            "candidate_id": None,
            "sovren_id":    "",
            "error":        str(e)[:500],
        }


# ─────────────────────────────── COMPARISON LOGIC ──────────────────────────

def _norm(v) -> str:
    return str(v or "").strip().lower()


def _normalize_phone(v) -> str:
    """Strip all non-digit characters to get canonical digit string."""
    return re.sub(r'\D', '', str(v or ""))


def _phones_match(p, s) -> bool:
    """
    Strict digit-only phone comparison.

    1. Strip all non-digits from both values.
    2. Exact digit match → True.
    3. Suffix match on the last 10 digits → True (handles country-code prefix
       differences, e.g. "+1-800-555-1234" vs "8005551234").
    Returns False if either value has fewer than 7 digits (likely garbage).
    """
    pd = _normalize_phone(p)
    sd = _normalize_phone(s)
    if len(pd) < 7 or len(sd) < 7:
        return False
    if pd == sd:
        return True
    # Country-code tolerance: compare the last 10 digits
    return pd[-10:] == sd[-10:]


def compare_fields(prod_fields: dict, stage_fields: dict) -> tuple:
    """
    Returns (rows, total_score, critical_failures, mismatched_fields).

    Scoring per field:
      0  → match (or phone format-only difference)
      +1 → staging has extra data that production is missing
      -1 → staging wrong or missing

    Phone gets an additional strict digit-level check on top of the
    standard normalised string comparison:
      • Raw strings differ but digits match → "Phone Format Diff" (score 0)
      • Digits also differ                  → "Phone Mismatch"    (score -1, critical)
    """
    rows              = []
    total_score       = 0
    critical_failures = []
    mismatched        = []

    for field in prod_fields:
        pv = prod_fields.get(field, "")
        sv = stage_fields.get(field, "")
        pn = _norm(pv)
        sn = _norm(sv)
        remark = ""

        if field == "phone":
            # ── Strict phone check ──────────────────────────────────────────
            if pn == sn:
                match_type = "Match"
                score      = 0
            elif not pn and sn:
                match_type = "Staging Extra"
                score      = 1
                remark     = f"Stage digits: {_normalize_phone(sv)}"
            elif pn and not sn:
                match_type = "Staging Missing"
                score      = -1
                remark     = f"Prod digits: {_normalize_phone(pv)}"
            elif _phones_match(pv, sv):
                # Same digits, different formatting — not a real failure
                match_type = "Phone Format Diff"
                score      = 0
                remark     = (
                    f"Prod digits: {_normalize_phone(pv)} | "
                    f"Stage digits: {_normalize_phone(sv)}"
                )
            else:
                # Digits genuinely differ → strict failure
                match_type = "Phone Mismatch"
                score      = -1
                remark     = (
                    f"Prod digits: {_normalize_phone(pv)} | "
                    f"Stage digits: {_normalize_phone(sv)}"
                )
        else:
            # ── Standard check for all other fields ─────────────────────────
            if pn == sn:
                match_type = "Match"
                score      = 0
            elif not pn and sn:
                match_type = "Staging Extra"
                score      = 1
            elif pn and not sn:
                match_type = "Staging Missing"
                score      = -1
            else:
                match_type = "Mismatch"
                score      = -1

        total_score += score
        if score != 0:
            mismatched.append(field)
            if field in CRITICAL_FIELDS:
                critical_failures.append(field)

        rows.append({
            "Field":      field,
            "Production": sanitize(str(pv)) if pv else "",
            "Staging":    sanitize(str(sv)) if sv else "",
            "Match Type": match_type,
            "Score":      score,
            "Critical":   "⚠️" if field in CRITICAL_FIELDS else "",
            "Remarks":    remark,
        })

    return rows, total_score, critical_failures, mismatched


def get_verdict(score: int, critical_failures: list) -> str:
    if critical_failures:
        return "❌ FAIL"
    if score >= PASS_THRESHOLD:
        return "✅ PASS"
    if score >= ACCEPTABLE_THRESHOLD:
        return "⚠️  ACCEPTABLE"
    return "❌ FAIL"


# ─────────────────────────────── CORE PROCESSOR ────────────────────────────

def process_resume(file_path: Path, prod_token: str, stage_token: str) -> dict:
    """
    Parses the resume against both environments in parallel, then compares.
    Returns a result dict used for both the summary row and the per-resume sheet.
    """
    wall_start = time.time()

    with ThreadPoolExecutor(max_workers=2) as ex:
        prod_fut  = ex.submit(parse_with, "Production", PROD_BASE,  prod_token,  file_path)
        stage_fut = ex.submit(parse_with, "Staging",    STAGE_BASE, stage_token, file_path)
        prod_res  = prod_fut.result()
        stage_res = stage_fut.result()

    elapsed_total = round(time.time() - wall_start, 2)

    prod_ok  = prod_res["error"]  is None
    stage_ok = stage_res["error"] is None

    comparison_rows, total_score, critical_failures, mismatched = [], 0, [], []

    if prod_ok and stage_ok:
        comparison_rows, total_score, critical_failures, mismatched = compare_fields(
            prod_res["fields"], stage_res["fields"]
        )
    elif not prod_ok:
        total_score       = -99
        critical_failures = ["production_error"]
    else:
        total_score       = -99
        critical_failures = ["staging_error"]

    verd = get_verdict(total_score, critical_failures)

    return {
        "resume":           file_path.name,
        "elapsed_total":    elapsed_total,
        # production
        "prod_ok":          prod_ok,
        "prod_status":      prod_res["status_code"],
        "prod_elapsed":     prod_res["elapsed"],
        "prod_sovren_id":   prod_res["sovren_id"],
        "prod_error":       prod_res["error"],
        # staging
        "stage_ok":         stage_ok,
        "stage_status":     stage_res["status_code"],
        "stage_elapsed":    stage_res["elapsed"],
        "stage_sovren_id":  stage_res["sovren_id"],
        "stage_error":      stage_res["error"],
        # comparison
        "comparison_rows":  comparison_rows,
        "total_score":      total_score,
        "critical_failures": critical_failures,
        "mismatched":       mismatched,
        "verdict":          verd,
    }


# ─────────────────────────────── EXCEL STYLES ──────────────────────────────

BLUE_HDR    = PatternFill("solid", fgColor="1F4E79")
BLUE_FONT   = Font(bold=True, color="FFFFFF", size=10)
GREEN_FILL  = PatternFill("solid", fgColor="E2EFDA")
RED_FILL    = PatternFill("solid", fgColor="FFDDC1")
YELLOW_FILL = PatternFill("solid", fgColor="FFFFE0")
ORANGE_FILL = PatternFill("solid", fgColor="FFD580")
BOLD        = Font(bold=True)

_MATCH_FILL = {
    "Match":             GREEN_FILL,
    "Staging Extra":     YELLOW_FILL,
    "Phone Format Diff": YELLOW_FILL,   # digits match, formatting differs — not a failure
    "Staging Missing":   RED_FILL,
    "Mismatch":          RED_FILL,
    "Phone Mismatch":    RED_FILL,
}


def _header_row(ws, headers: list):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill      = BLUE_HDR
        cell.font      = BLUE_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _auto_widths(ws):
    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=10)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 60)


# ─────────────────────────────── EXCEL WRITERS ─────────────────────────────

def write_resume_sheet(wb, result: dict) -> None:
    safe_name = re.sub(r'[\\/*?:\[\]]', '_', result["resume"])[:31]
    ws = wb.create_sheet(title=safe_name)

    _header_row(ws, ["Field", "Production", "Staging", "Match Type", "Score", "Critical", "Remarks"])

    for row_idx, row in enumerate(result["comparison_rows"], start=2):
        fill = _MATCH_FILL.get(row["Match Type"], None)
        for c, key in enumerate(["Field", "Production", "Staging", "Match Type", "Score", "Critical", "Remarks"], 1):
            cell = ws.cell(row=row_idx, column=c, value=row.get(key, ""))
            if fill:
                cell.fill = fill

    meta_start = len(result["comparison_rows"]) + 3
    meta = [
        ("TOTAL SCORE",                  result["total_score"]),
        ("VERDICT",                      result["verdict"]),
        ("CRITICAL FAILURES",            ", ".join(result["critical_failures"]) or "None"),
        ("FIELD MISMATCHES",             ", ".join(result["mismatched"]) or "None"),
        ("",                             ""),
        ("PRODUCTION STATUS CODE",       result["prod_status"] or f"ERROR: {(result['prod_error'] or '')[:60]}"),
        ("PRODUCTION RESPONSE TIME",     f"{result['prod_elapsed']}s"),
        ("PRODUCTION SOVREN DOCUMENT ID", result["prod_sovren_id"] or "—"),
        ("STAGING STATUS CODE",          result["stage_status"] or f"ERROR: {(result['stage_error'] or '')[:60]}"),
        ("STAGING RESPONSE TIME",        f"{result['stage_elapsed']}s"),
        ("STAGING SOVREN DOCUMENT ID",   result["stage_sovren_id"] or "—"),
    ]
    for i, (label, value) in enumerate(meta):
        r = meta_start + i
        ws.cell(row=r, column=1, value=label).font = BOLD
        ws.cell(row=r, column=2, value=str(value) if value is not None else "")

    _auto_widths(ws)


def write_summary_sheet(wb, results: list) -> None:
    ws = wb.create_sheet(title="Summary", index=0)
    headers = [
        "Resume",
        "Prod Status", "Prod Time (s)", "Prod Sovren ID",
        "Stage Status", "Stage Time (s)", "Stage Sovren ID",
        "Score", "Verdict",
        "Critical Failures", "Field Mismatches",
    ]
    _header_row(ws, headers)

    for row_idx, r in enumerate(results, start=2):
        values = [
            r["resume"],
            r["prod_status"]  or f"ERR: {(r['prod_error']  or '')[:40]}",
            r["prod_elapsed"],
            r["prod_sovren_id"] or "—",
            r["stage_status"] or f"ERR: {(r['stage_error'] or '')[:40]}",
            r["stage_elapsed"],
            r["stage_sovren_id"] or "—",
            r["total_score"],
            r["verdict"],
            ", ".join(r["critical_failures"]) or "None",
            ", ".join(r["mismatched"])         or "None",
        ]
        verd = r["verdict"]
        fill = GREEN_FILL if "PASS" in verd else (ORANGE_FILL if "ACCEPTABLE" in verd else RED_FILL)
        for c, v in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=c, value=v)
            cell.fill = fill

    # Statistics block
    stats_row = len(results) + 3
    total      = len(results)
    passes     = sum(1 for r in results if "PASS"       in r["verdict"])
    acceptable = sum(1 for r in results if "ACCEPTABLE" in r["verdict"])
    fails      = sum(1 for r in results if "FAIL"       in r["verdict"])

    def stat(label, value):
        nonlocal stats_row
        ws.cell(row=stats_row, column=1, value=label).font = BOLD
        ws.cell(row=stats_row, column=2, value=value)
        stats_row += 1

    stat("─── SUMMARY STATISTICS ───", "")
    stat("Total Resumes",   total)
    stat("✅ PASS",          f"{passes} / {total}  ({round(passes/total*100,1) if total else 0}%)")
    stat("⚠️  ACCEPTABLE",  f"{acceptable} / {total}  ({round(acceptable/total*100,1) if total else 0}%)")
    stat("❌ FAIL",          f"{fails} / {total}  ({round(fails/total*100,1) if total else 0}%)")

    prod_times  = [r["prod_elapsed"]  for r in results if isinstance(r["prod_elapsed"],  (int, float))]
    stage_times = [r["stage_elapsed"] for r in results if isinstance(r["stage_elapsed"], (int, float))]
    if prod_times:
        stat("Avg Production Time (s)",  round(sum(prod_times)  / len(prod_times),  2))
    if stage_times:
        stat("Avg Staging Time (s)",     round(sum(stage_times) / len(stage_times), 2))

    _auto_widths(ws)


# ─────────────────────────────── MAIN ──────────────────────────────────────

def main():
    print("\n" + "=" * 80)
    print("🚀  RESUME PARSER — PRODUCTION vs STAGING COMPARISON")
    print("=" * 80)
    print(f"📌  Production : {PROD_BASE}  ({PROD_EMAIL})")
    print(f"📌  Staging    : {STAGE_BASE}  ({STAGE_EMAIL})")
    print(f"⚡  Batch size : {BATCH_SIZE} resumes processed concurrently")
    print(f"📊  Output     : {RESULT_EXCEL}")
    print("=" * 80 + "\n")

    # ── Discover resumes ────────────────────────────────────────────────────
    supported = ["pdf", "doc", "docx", "rtf"]
    raw: list[Path] = []
    for ext in supported:
        raw.extend(RESUME_FOLDER.glob(f"*.{ext}"))
        raw.extend(RESUME_FOLDER.glob(f"*.{ext.upper()}"))

    seen: set = set()
    resumes: list[Path] = []
    for r in sorted(raw):
        k = str(r).lower()
        if k not in seen:
            seen.add(k)
            resumes.append(r)

    if not resumes:
        print(f"❌  No resumes found in {RESUME_FOLDER}")
        return
    print(f"📂  Found {len(resumes)} resume(s)\n")

    # ── Authenticate both environments ──────────────────────────────────────
    print("🔑  Fetching authentication tokens...")
    try:
        prod_token = login(PROD_BASE, PROD_EMAIL, PROD_PASSWORD)
        print("   ✓ Production token OK")
    except Exception as e:
        print(f"   ✗ Production login FAILED: {e}")
        return

    try:
        stage_token = login(STAGE_BASE, STAGE_EMAIL, STAGE_PASSWORD)
        print("   ✓ Staging token OK")
    except Exception as e:
        print(f"   ✗ Staging login FAILED: {e}")
        return
    print()

    # ── Batch processing ────────────────────────────────────────────────────
    all_results: list[dict] = []
    batches = [resumes[i:i + BATCH_SIZE] for i in range(0, len(resumes), BATCH_SIZE)]

    for batch_num, batch in enumerate(batches, 1):
        print(f"{'=' * 80}")
        print(f"📦  BATCH {batch_num}/{len(batches)}: {len(batch)} resumes in parallel")
        print(f"{'=' * 80}")
        batch_start = time.time()

        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            future_to_file = {
                executor.submit(process_resume, fp, prod_token, stage_token): fp
                for fp in batch
            }
            for future in as_completed(future_to_file):
                result = future.result()
                all_results.append(result)

                verd = result["verdict"]
                icon = "✅" if "PASS" in verd else ("⚠️ " if "ACCEPTABLE" in verd else "❌")
                print(
                    f"  {icon}  {result['resume']:<40}"
                    f"  score={result['total_score']:>3}"
                    f"  prod={result['prod_elapsed']}s"
                    f"  stage={result['stage_elapsed']}s"
                    f"  {verd}"
                )

        batch_elapsed = round(time.time() - batch_start, 2)
        print(
            f"\n⏱️   Batch {batch_num} done in {batch_elapsed}s"
            f"  (avg {round(batch_elapsed / len(batch), 1)}s per resume)\n"
        )

    # ── Generate Excel report ───────────────────────────────────────────────
    print("📊  Generating Excel report...")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    write_summary_sheet(wb, all_results)
    for result in all_results:
        write_resume_sheet(wb, result)

    try:
        if os.path.exists(RESULT_EXCEL):
            os.remove(RESULT_EXCEL)
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".xlsx",
                                         dir=tempfile.gettempdir()) as tmp:
            tmp_path = tmp.name
        wb.save(tmp_path)
        import shutil as _sh
        _sh.copy2(tmp_path, RESULT_EXCEL)
        os.remove(tmp_path)
        print(f"   ✓ Saved → {RESULT_EXCEL}")
    except Exception as e:
        logging.error(f"Save failed: {e}")
        wb.save(RESULT_EXCEL)
        print(f"   ✓ Saved (fallback) → {RESULT_EXCEL}")

    # ── Final summary ───────────────────────────────────────────────────────
    total      = len(all_results)
    passes     = sum(1 for r in all_results if "PASS"       in r["verdict"])
    acceptable = sum(1 for r in all_results if "ACCEPTABLE" in r["verdict"])
    fails      = sum(1 for r in all_results if "FAIL"       in r["verdict"])

    print(f"\n{'=' * 80}")
    print("📊  COMPARISON SUMMARY")
    print(f"{'=' * 80}")
    print(f"✅  PASS:        {passes:>3} / {total}  ({round(passes/total*100,1) if total else 0}%)")
    print(f"⚠️   ACCEPTABLE:  {acceptable:>3} / {total}  ({round(acceptable/total*100,1) if total else 0}%)")
    print(f"❌  FAIL:        {fails:>3} / {total}  ({round(fails/total*100,1) if total else 0}%)")
    print(f"{'=' * 80}")
    print(f"📄  Report : {RESULT_EXCEL}")
    print(f"📋  Logs   : logs/{LOGFILE}")
    print(f"📁  Raw    : raw_responses/")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()

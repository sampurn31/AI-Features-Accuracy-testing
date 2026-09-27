#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Resume Parser – Dev (new model provider) vs Staging (current implementation)
----------------------------------------------------------------------------
Derived from Comparator.py. Sends each resume in English_100/ to both
environments in parallel, stores every raw response, then compares the
parsed fields side by side.

Additions over Comparator.py (needed for a 100 x 2 run):
  1. Token refresh on 401 + per-call retry with backoff.
  2. Resumable state file -- a re-run skips resumes already parsed on an env.
  3. Raw response + work/education history saved per env for later
     ground-truth rescoring (no re-parse needed).
  4. Skills compared as a set (overlap %), not as one string.
  5. Per-field agreement sheet and "updated existing candidate" flag.

Usage:
    python3 Dev_vs_Staging_Comparator.py --limit 5     # pilot
    python3 Dev_vs_Staging_Comparator.py               # everything not yet done
    python3 Dev_vs_Staging_Comparator.py --report-only # rebuild Excel from state
    python3 Dev_vs_Staging_Comparator.py --retry-failed

Output: Dev_vs_Staging_Report.xlsx
"""

import argparse, os, time, json, requests, logging, tempfile, shutil, datetime, re, threading
import openpyxl
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────── CONFIG ────────────────────────────────────

SCRIPT_DIR    = Path(__file__).resolve().parent
RESUME_FOLDER = SCRIPT_DIR / "English_100"

# Staging  (baseline — current implementation, dev DB)
STAGE_EMAIL    = os.getenv("STAGE_EMAIL", "")
STAGE_PASSWORD = os.getenv("STAGE_PASSWORD", "")
STAGE_BASE     = "https://albatross-staging.recruitcrm.net"

# Dev  (under test — new model provider, test DB)
DEV_EMAIL    = os.getenv("DEV_EMAIL", "")
DEV_PASSWORD = os.getenv("DEV_PASSWORD", "")
DEV_BASE     = "https://albatross-dev.recruitcrm.net"

BATCH_SIZE   = 10   # resumes processed concurrently per batch (x2 envs)
MAX_ATTEMPTS = 3

RESULT_EXCEL = SCRIPT_DIR / "Dev_vs_Staging_Report.xlsx"
STATE_FILE   = SCRIPT_DIR / "dev_vs_staging_state.json"
LOGFILE      = SCRIPT_DIR / "logs" / "dev_vs_staging.log"
RAW_DIR      = {"Staging": SCRIPT_DIR / "raw_responses_staging_en",
                "Dev":     SCRIPT_DIR / "raw_responses_dev_en"}

# Critical fields — dev failure on any of these → automatic FAIL
CRITICAL_FIELDS = {"fullName", "email", "phone", "city"}

# Verdict thresholds (total score)
PASS_THRESHOLD       = 0    # score >= 0  → ✅ PASS
ACCEPTABLE_THRESHOLD = -3   # score >= -3 → ⚠️  ACCEPTABLE, else ❌ FAIL

# Skills overlap (Jaccard) at or above this counts as a match
SKILLS_MATCH_THRESHOLD = 0.6

MIME_TYPES = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf":  "application/rtf",
}

# ─────────────────────────────── LOGGING ───────────────────────────────────

LOGFILE.parent.mkdir(parents=True, exist_ok=True)
for d in RAW_DIR.values():
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOGFILE, encoding="utf-8")],
)
logging.getLogger("urllib3").setLevel(logging.WARNING)

_ILLEGAL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def sanitize(v):
    return _ILLEGAL_CHARS.sub('', v) if isinstance(v, str) else v


# ─────────────────────────────── AUTH ──────────────────────────────────────

class AuthExpired(Exception):
    pass


class TokenManager:
    """Thread-safe token holder that re-logs-in on demand."""

    def __init__(self, base_url, email, password):
        self.base_url, self.email, self.password = base_url, email, password
        self._token, self._gen = None, 0
        self._lock = threading.Lock()

    def _login(self):
        resp = requests.post(f"{self.base_url}/v1/login",
                             data={"email": self.email, "password": self.password},
                             timeout=30)
        resp.raise_for_status()
        token = (resp.json().get("data") or {}).get("token")
        if not token:
            raise Exception(f"Token not found in login response: {resp.text[:300]}")
        return token

    def get(self):
        with self._lock:
            if self._token is None:
                self._token = self._login()
                self._gen += 1
            return self._token, self._gen

    def refresh(self, stale_gen):
        with self._lock:
            if stale_gen == self._gen:
                logging.warning(f"401 from {self.base_url} - re-authenticating")
                self._token = self._login()
                self._gen += 1
            return self._token, self._gen


# ─────────────────────────────── API HELPERS ───────────────────────────────

def get_presigned_url(base_url: str, file_name: str, token: str) -> dict:
    r = requests.get(
        f"{base_url}/v1/get-presigned-url",
        headers={"authorization": f"Bearer {token}"},
        params={"fileName": file_name, "requestType": "put"},
        timeout=30,
    )
    if r.status_code == 401:
        raise AuthExpired("401 on get-presigned-url")
    r.raise_for_status()
    return r.json().get("data", r.json())


def upload_to_s3(put_url: str, file_path: Path) -> None:
    mime = MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    with open(file_path, "rb") as f:
        resp = requests.put(put_url, data=f, headers={"Content-Type": mime}, timeout=120)
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
    r = requests.post(
        f"{base_url}/v1/candidates/parse-resume?actionsteps=1",
        headers=headers,
        json=payload,
        timeout=180,
    )
    sc = r.status_code
    if sc == 401:
        raise AuthExpired("401 on parse-resume")
    try:
        data = r.json()
    except json.JSONDecodeError as e:
        raise Exception(f"Invalid JSON (HTTP {sc}): {e}. Body: {r.text[:300]}")
    data["_status_code"] = sc
    r.raise_for_status()
    return data


def fetch_history(base_url: str, token: str, kind: str, candidate_id: int) -> list:
    """kind: 'candidate-work' or 'candidate-education'"""
    try:
        r = requests.get(
            f"{base_url}/v1/candidates/{kind}/{candidate_id}",
            headers={"authorization": f"Bearer {token}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        logging.warning(f"{kind} fetch failed (id={candidate_id}): {e}")
        return []


# ─────────────────────────────── DATA EXTRACTION ───────────────────────────

def _year(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y") if ts else ""
    except Exception:
        return ""


def extract_work_history(work_list: list) -> str:
    entries = []
    for w in work_list:
        title   = w.get("title", "")
        company = w.get("work_company_name", "")
        start   = _year(w.get("work_start_date"))
        end     = _year(w.get("work_end_date")) or "Present"
        if title or company:
            entries.append(f"{title} at {company} ({start}–{end})")
    return " | ".join(entries)


def extract_education_history(edu_list: list) -> str:
    entries = []
    for e in edu_list:
        line = e.get("educational_qualification", "") or ""
        spec = e.get("educational_specialization", "")
        inst = e.get("institute_name", "")
        if spec: line += f" in {spec}"
        if inst: line += f" from {inst}"
        line += f" ({_year(e.get('education_start_date')) or '?'}–{_year(e.get('education_end_date')) or '?'})"
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
        "postalCode":     candidate.get("postal_code",      "") or "",
        "linkedin":       candidate.get("profilelinkedin",  "") or "",
        "currentTitle":   candidate.get("position",         "") or "",
        "currentOrg":     candidate.get("lastorganisation", "") or "",
        "workExpYears":   str(candidate.get("workexpyr",    "") or ""),
        "skills":         candidate.get("skill",            "") or "",
        "specialization": candidate.get("specialization",   "") or "",
        "qualification":  candidate.get("qualification",    "") or "",
        "summary":        "Yes" if candidate.get("summary") else "No",
        "workHistory":    extract_work_history(work_list),
        "workCount":      str(len(work_list)),
        "education":      extract_education_history(edu_list),
        "eduCount":       str(len(edu_list)),
    }


# ─────────────────────────────── PER-ENV RUNNER ────────────────────────────

def _parse_once(label, base_url, token, file_path):
    presign = get_presigned_url(base_url, file_path.name, token)
    put_url = presign.get("preSignedUrl") or presign.get("put_url") or presign.get("url")
    upload_to_s3(put_url, file_path)
    parse_result = call_parser(base_url, token, presign["key"], file_path.name, file_path.stat().st_size)

    candidate    = (parse_result.get("data") or {}).get("candidate") or {}
    candidate_id = candidate.get("id")
    work_list, edu_list = [], []
    if candidate_id:
        work_list = fetch_history(base_url, token, "candidate-work", candidate_id)
        edu_list  = fetch_history(base_url, token, "candidate-education", candidate_id)

    raw = {"parse": parse_result, "work": work_list, "education": edu_list}
    with open(RAW_DIR[label] / f"{file_path.name}.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)

    return parse_result, candidate, work_list, edu_list


def parse_with(label: str, base_url: str, tokens: TokenManager, file_path: Path) -> dict:
    """Upload, parse, fetch history for one env with retry + token refresh."""
    start, last_err = time.time(), None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        token, gen = tokens.get()
        t0 = time.time()
        try:
            parse_result, candidate, work_list, edu_list = _parse_once(label, base_url, token, file_path)
            parse_elapsed = round(time.time() - t0, 2)
            logging.info(f"[{label}] OK {file_path.name} in {parse_elapsed}s (attempt {attempt})")
            return {
                "fields":       extract_fields(candidate, work_list, edu_list),
                "status_code":  parse_result.get("_status_code", 200),
                "elapsed":      parse_elapsed,
                "attempts":     attempt,
                "candidate_id": candidate.get("id"),
                "updated":      bool((parse_result.get("data") or {}).get("updated")),
                "error":        None,
            }
        except AuthExpired as e:
            last_err = e
            tokens.refresh(gen)
        except Exception as e:
            last_err = e
            logging.warning(f"[{label}] attempt {attempt} failed {file_path.name}: {e}")
            time.sleep(3 * attempt)
    logging.error(f"[{label}] FAILED {file_path.name}: {last_err}")
    return {
        "fields": {}, "status_code": None, "elapsed": round(time.time() - start, 2),
        "attempts": MAX_ATTEMPTS, "candidate_id": None, "updated": False,
        "error": str(last_err)[:500],
    }


# ─────────────────────────────── COMPARISON LOGIC ──────────────────────────

def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _normalize_phone(v) -> str:
    return re.sub(r'\D', '', str(v or ""))


def _phones_match(p, s) -> bool:
    """Digit-only compare; last-10-digit suffix tolerates country-code prefix."""
    pd, sd = _normalize_phone(p), _normalize_phone(s)
    if len(pd) < 7 or len(sd) < 7:
        return False
    return pd == sd or pd[-10:] == sd[-10:]


def _skill_set(v) -> set:
    return {s.strip().lower() for s in re.split(r"[,;|\n]", str(v or "")) if s.strip()}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def compare_fields(stage_fields: dict, dev_fields: dict) -> tuple:
    """
    Returns (rows, total_score, critical_failures, mismatched_fields).

    Scoring per field (from Dev's point of view, Staging = baseline):
      0  → match (or phone format-only / skills overlap >= threshold)
      +1 → dev has data staging is missing
      -1 → dev missing or different
    NOTE: a -1 here means "differs from staging", not "wrong". Accuracy vs the
    actual resume is decided in the ground-truth pass.
    """
    rows, total_score, critical_failures, mismatched = [], 0, [], []

    for field in stage_fields:
        sv, dv = stage_fields.get(field, ""), dev_fields.get(field, "")
        sn, dn = _norm(sv), _norm(dv)
        remark = ""

        if sn == dn:
            match_type, score = "Match", 0
        elif not sn and dn:
            match_type, score = "Dev Extra", 1
        elif sn and not dn:
            match_type, score = "Dev Missing", -1
        elif field == "phone" and _phones_match(sv, dv):
            match_type, score = "Phone Format Diff", 0
            remark = f"Stage digits: {_normalize_phone(sv)} | Dev digits: {_normalize_phone(dv)}"
        elif field == "phone":
            match_type, score = "Phone Mismatch", -1
            remark = f"Stage digits: {_normalize_phone(sv)} | Dev digits: {_normalize_phone(dv)}"
        elif field == "skills":
            ss, ds = _skill_set(sv), _skill_set(dv)
            j = _jaccard(ss, ds)
            remark = (f"overlap {j:.0%} | stage {len(ss)} / dev {len(ds)} / common {len(ss & ds)}")
            if j >= SKILLS_MATCH_THRESHOLD:
                match_type, score = "Skills Overlap", 0
            else:
                match_type, score = "Mismatch", -1
        else:
            match_type, score = "Mismatch", -1

        total_score += score
        if score != 0:
            mismatched.append(field)
            if score < 0 and field in CRITICAL_FIELDS:
                critical_failures.append(field)

        rows.append({
            "Field":      field,
            "Staging":    sanitize(str(sv)) if sv else "",
            "Dev":        sanitize(str(dv)) if dv else "",
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


def build_result(name: str, stage_res: dict, dev_res: dict) -> dict:
    stage_ok, dev_ok = stage_res["error"] is None, dev_res["error"] is None
    rows, score, crit, mism = [], 0, [], []
    if stage_ok and dev_ok:
        rows, score, crit, mism = compare_fields(stage_res["fields"], dev_res["fields"])
    elif not dev_ok:
        score, crit = -99, ["dev_error"]
    else:
        score, crit = -99, ["staging_error"]
    return {
        "resume": name, "stage": stage_res, "dev": dev_res,
        "comparison_rows": rows, "total_score": score,
        "critical_failures": crit, "mismatched": mism,
        "verdict": get_verdict(score, crit),
    }


# ─────────────────────────────── STATE ─────────────────────────────────────

_state_lock = threading.Lock()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    with _state_lock:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)


def process_resume(file_path: Path, state: dict, stage_tm: TokenManager, dev_tm: TokenManager,
                   retry_failed: bool) -> dict:
    """Parse on whichever env still needs it (both in parallel), then compare."""
    entry = state.setdefault(file_path.name, {})
    jobs = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        for label, base, tm in (("Staging", STAGE_BASE, stage_tm), ("Dev", DEV_BASE, dev_tm)):
            prev = entry.get(label)
            if prev is None or (retry_failed and prev.get("error")):
                jobs[label] = ex.submit(parse_with, label, base, tm, file_path)
        for label, fut in jobs.items():
            entry[label] = fut.result()
    save_state(state)
    return build_result(file_path.name, entry["Staging"], entry["Dev"])


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
    "Skills Overlap":    GREEN_FILL,
    "Dev Extra":         YELLOW_FILL,
    "Phone Format Diff": YELLOW_FILL,
    "Dev Missing":       RED_FILL,
    "Mismatch":          RED_FILL,
    "Phone Mismatch":    RED_FILL,
}


def _header_row(ws, headers: list):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill, cell.font = BLUE_HDR, BLUE_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _auto_widths(ws):
    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=10)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 60)


def _pct(n, d):
    return f"{n} / {d}  ({round(n / d * 100, 1) if d else 0}%)"


def _percentile(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))]


# ─────────────────────────────── EXCEL WRITERS ─────────────────────────────

def write_resume_sheet(wb, result: dict) -> None:
    safe_name = re.sub(r'[\\/*?:\[\]]', '_', result["resume"])[:31]
    ws = wb.create_sheet(title=safe_name)
    cols = ["Field", "Staging", "Dev", "Match Type", "Score", "Critical", "Remarks"]
    _header_row(ws, cols)
    for row_idx, row in enumerate(result["comparison_rows"], start=2):
        fill = _MATCH_FILL.get(row["Match Type"])
        for c, key in enumerate(cols, 1):
            cell = ws.cell(row=row_idx, column=c, value=row.get(key, ""))
            if fill:
                cell.fill = fill

    s, d = result["stage"], result["dev"]
    meta = [
        ("TOTAL SCORE",            result["total_score"]),
        ("VERDICT",                result["verdict"]),
        ("CRITICAL FAILURES",      ", ".join(result["critical_failures"]) or "None"),
        ("FIELD MISMATCHES",       ", ".join(result["mismatched"]) or "None"),
        ("", ""),
        ("STAGING STATUS CODE",    s["status_code"] or f"ERROR: {(s['error'] or '')[:80]}"),
        ("STAGING PARSE TIME",     f"{s['elapsed']}s"),
        ("STAGING CANDIDATE ID",   s["candidate_id"] or "—"),
        ("STAGING UPDATED EXISTING", "Yes" if s.get("updated") else "No"),
        ("DEV STATUS CODE",        d["status_code"] or f"ERROR: {(d['error'] or '')[:80]}"),
        ("DEV PARSE TIME",         f"{d['elapsed']}s"),
        ("DEV CANDIDATE ID",       d["candidate_id"] or "—"),
        ("DEV UPDATED EXISTING",   "Yes" if d.get("updated") else "No"),
    ]
    start = len(result["comparison_rows"]) + 3
    for i, (label, value) in enumerate(meta):
        ws.cell(row=start + i, column=1, value=label).font = BOLD
        ws.cell(row=start + i, column=2, value=str(value) if value is not None else "")
    _auto_widths(ws)


def write_summary_sheet(wb, results: list) -> None:
    ws = wb.create_sheet(title="Summary", index=0)
    headers = ["Resume",
               "Stage Status", "Stage Time (s)", "Stage Cand ID",
               "Dev Status", "Dev Time (s)", "Dev Cand ID",
               "Score", "Verdict", "Critical Failures", "Field Mismatches"]
    _header_row(ws, headers)
    for row_idx, r in enumerate(results, start=2):
        s, d = r["stage"], r["dev"]
        values = [
            r["resume"],
            s["status_code"] or f"ERR: {(s['error'] or '')[:40]}", s["elapsed"], s["candidate_id"] or "—",
            d["status_code"] or f"ERR: {(d['error'] or '')[:40]}", d["elapsed"], d["candidate_id"] or "—",
            r["total_score"], r["verdict"],
            ", ".join(r["critical_failures"]) or "None",
            ", ".join(r["mismatched"]) or "None",
        ]
        verd = r["verdict"]
        fill = GREEN_FILL if "PASS" in verd else (ORANGE_FILL if "ACCEPTABLE" in verd else RED_FILL)
        for c, v in enumerate(values, 1):
            ws.cell(row=row_idx, column=c, value=v).fill = fill

    row = len(results) + 3
    total = len(results)

    def stat(label, value):
        nonlocal row
        ws.cell(row=row, column=1, value=label).font = BOLD
        ws.cell(row=row, column=2, value=value)
        row += 1

    stat("─── SUMMARY STATISTICS ───", "")
    stat("Total Resumes", total)
    stat("✅ PASS",        _pct(sum("PASS" in r["verdict"] for r in results), total))
    stat("⚠️  ACCEPTABLE", _pct(sum("ACCEPTABLE" in r["verdict"] for r in results), total))
    stat("❌ FAIL",        _pct(sum("FAIL" in r["verdict"] for r in results), total))
    stat("NOTE", "PASS/FAIL = agreement with Staging, not accuracy. See ground-truth pass.")
    stat("", "")
    for label, key in (("Staging", "stage"), ("Dev", "dev")):
        ok    = [r[key] for r in results if r[key]["error"] is None]
        times = [x["elapsed"] for x in ok]
        stat(f"{label} parse success", _pct(len(ok), total))
        if times:
            stat(f"{label} parse time avg / p50 / p95 (s)",
                 f"{round(sum(times) / len(times), 2)} / {_percentile(times, 50)} / {_percentile(times, 95)}")
        stat(f"{label} retries needed", sum(1 for x in ok if x.get("attempts", 1) > 1))
        stat(f"{label} updated existing candidate", sum(1 for x in ok if x.get("updated")))
    _auto_widths(ws)


def write_field_agreement_sheet(wb, results: list) -> None:
    ws = wb.create_sheet(title="Field Agreement", index=1)
    types = ["Match", "Skills Overlap", "Phone Format Diff", "Dev Extra", "Dev Missing", "Mismatch", "Phone Mismatch"]
    _header_row(ws, ["Field", "Compared"] + types + ["Agreement %"])
    counts = {}
    for r in results:
        for row in r["comparison_rows"]:
            counts.setdefault(row["Field"], {}).setdefault(row["Match Type"], 0)
            counts[row["Field"]][row["Match Type"]] += 1
    for i, (field, c) in enumerate(counts.items(), start=2):
        n = sum(c.values())
        agree = c.get("Match", 0) + c.get("Skills Overlap", 0) + c.get("Phone Format Diff", 0)
        vals = [field, n] + [c.get(t, 0) for t in types] + [round(agree / n * 100, 1) if n else 0]
        for col, v in enumerate(vals, 1):
            ws.cell(row=i, column=col, value=v)
    _auto_widths(ws)


def write_report(results: list) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    write_summary_sheet(wb, results)
    write_field_agreement_sheet(wb, results)
    for r in results:
        write_resume_sheet(wb, r)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp_path = tmp.name
    wb.save(tmp_path)
    shutil.move(tmp_path, RESULT_EXCEL)


# ─────────────────────────────── MAIN ──────────────────────────────────────

def discover_resumes() -> list:
    return sorted(p for p in RESUME_FOLDER.iterdir()
                  if p.is_file() and p.suffix.lower() in MIME_TYPES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only process the first N pending resumes")
    ap.add_argument("--retry-failed", action="store_true", help="re-attempt env calls that errored")
    ap.add_argument("--report-only", action="store_true", help="rebuild Excel from state, parse nothing")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = ap.parse_args()

    resumes = discover_resumes()
    state   = load_state()

    def pending(p):
        e = state.get(p.name, {})
        if "Staging" not in e or "Dev" not in e:
            return True
        return args.retry_failed and (e["Staging"].get("error") or e["Dev"].get("error"))

    todo = [] if args.report_only else [p for p in resumes if pending(p)]
    if args.limit:
        todo = todo[:args.limit]

    print("\n" + "=" * 80)
    print("🚀  RESUME PARSER — DEV (new provider) vs STAGING (current)")
    print("=" * 80)
    print(f"📌  Staging : {STAGE_BASE}  ({STAGE_EMAIL})")
    print(f"📌  Dev     : {DEV_BASE}  ({DEV_EMAIL})")
    print(f"📂  Resumes : {len(resumes)} in {RESUME_FOLDER.name}/ | queued this run: {len(todo)}")
    print(f"⚡  Batch   : {args.batch_size} resumes concurrently")
    print("=" * 80 + "\n")

    if todo:
        stage_tm = TokenManager(STAGE_BASE, STAGE_EMAIL, STAGE_PASSWORD)
        dev_tm   = TokenManager(DEV_BASE, DEV_EMAIL, DEV_PASSWORD)
        stage_tm.get(); dev_tm.get()
        print("🔑  Both logins OK\n")

        batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
        for bn, batch in enumerate(batches, 1):
            t0 = time.time()
            print(f"📦  BATCH {bn}/{len(batches)}: {len(batch)} resumes")
            with ThreadPoolExecutor(max_workers=args.batch_size) as ex:
                futs = [ex.submit(process_resume, fp, state, stage_tm, dev_tm, args.retry_failed)
                        for fp in batch]
                for fut in as_completed(futs):
                    r = fut.result()
                    icon = "✅" if "PASS" in r["verdict"] else ("⚠️ " if "ACCEPTABLE" in r["verdict"] else "❌")
                    print(f"  {icon}  {r['resume']:<14} score={r['total_score']:>3}"
                          f"  stage={r['stage']['elapsed']}s  dev={r['dev']['elapsed']}s"
                          f"  diff: {', '.join(r['mismatched']) or '-'}")
            print(f"⏱️   Batch {bn} done in {round(time.time() - t0, 1)}s\n")

    # Report covers every resume that has both env results in state
    results = [build_result(p.name, state[p.name]["Staging"], state[p.name]["Dev"])
               for p in resumes if "Staging" in state.get(p.name, {}) and "Dev" in state.get(p.name, {})]
    if not results:
        print("Nothing parsed yet.")
        return
    write_report(results)

    total = len(results)
    print("=" * 80)
    print(f"📊  {total} resumes in report")
    print(f"✅  PASS        {_pct(sum('PASS' in r['verdict'] for r in results), total)}")
    print(f"⚠️   ACCEPTABLE  {_pct(sum('ACCEPTABLE' in r['verdict'] for r in results), total)}")
    print(f"❌  FAIL        {_pct(sum('FAIL' in r['verdict'] for r in results), total)}")
    print(f"❗  Errors      staging={sum(r['stage']['error'] is not None for r in results)}"
          f"  dev={sum(r['dev']['error'] is not None for r in results)}")
    print(f"📄  Report : {RESULT_EXCEL.name}")
    print(f"📋  Logs   : logs/{LOGFILE.name}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()

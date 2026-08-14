#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Parser Standard Field Parity Check
--------------------------------------
Same idea as ../standard_field_parity.py, but Track B is the standalone
AI resume-parser service instead of an Albatross environment.

  Track A  : Albatross baseline  → presigned S3 upload → POST /v1/candidates/parse-resume
  Track B  : AI parser v3        → POST /v3/parse?model_id=N  (multipart file upload)

Both tracks parse the SAME original (unmodified) resume.  Only standard fields
are compared — no injection, no custom fields, no LLM.

account_id / user_id for the AI parser are taken from the Track A login
response (`user.id` and `user.accountid`); the AI parser token is hardcoded
below.

Flow:
  1. Log in to Track A → token + user.id + user.accountid
  2. Discover resumes in ../100 resumes/
  3. Upload + parse each resume on Track A
  4. POST the same file to the AI parser v3 endpoint
  5. Map the v3 payload onto the Albatross standard-field schema
  6. Compare field-by-field
  7. Write JSON + Excel report to output/

Run:
  python3 ai_parser_parity.py                 # all resumes (cached if results exist)
  python3 ai_parser_parity.py --limit 5       # first 5 resumes only
  python3 ai_parser_parity.py --reparse       # ignore cache, parse again
  python3 ai_parser_parity.py --model-id 8    # AI parser model_id (default 8)

Outputs:
  output/ai_parity_results.json
  output/ai_parity_report.xlsx
  output/raw_ai_responses/<resume>.json
"""

import argparse
import json
import logging
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from config import (  # noqa: E402
    AI_PARSER_TOKEN,
    BASE_URL_TRACK_A,
    EMAIL_TRACK_A,
    PASSWORD_TRACK_A,
)

# ── AI parser configuration ──────────────────────────────────────────────────
# The token lives in ../.env as AI_PARSER_TOKEN (full header value, including
# the "Private " scheme) — this repo is public, so it must not be committed.
AI_PARSER_URL = "https://ai6-resume-parser.recruitcrm.net/v3/parse"
AI_MODEL_ID   = 8

AI_FORM_DEFAULTS = {
    "create_index":                 "false",
    "bool_index_resume":            "false",
    "bool_resume_skills_fine_tune": "false",
    "is_custom_fields":             "false",
}

REQUEST_TIMEOUT = 300

# Albatross staging intermittently returns 5xx (DB connection refused).  Without
# retries a single blip kills a 100-resume run, so every HTTP call goes through
# _request_with_retries.
MAX_RETRIES   = 4
RETRY_BACKOFF = 3.0

RESUME_FOLDER = PARENT / "100 resumes"

OUTPUT_DIR   = HERE / "output"
RAW_AI_DIR   = OUTPUT_DIR / "raw_ai_responses"
RESULTS_JSON = OUTPUT_DIR / "ai_parity_results.json"
REPORT_FILE  = OUTPUT_DIR / "ai_parity_report.xlsx"
LOGS_DIR     = HERE / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_AI_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "ai_parser_parity.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("ai_parity")

# Compared in this order.  sovren_document_id is deliberately excluded — the two
# services mint unrelated document ids, so comparing them is pure noise.
COMPARE_FIELDS = [
    "firstname", "lastname", "emailid", "contactnumber",
    "city", "state", "country", "address",
    "position", "lastorganisation", "workexpyr",
    "summary", "skill", "languageskills",
    "qualification", "specialization",
    "profilelinkedin", "profilegithub",
    "workhistory", "educationhistory",
]

SET_FIELDS     = {"skill", "languageskills"}
HISTORY_FIELDS = {"workhistory", "educationhistory"}


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation + classification
# (kept byte-for-byte compatible with custom_field_pipeline.py so that this
#  report is directly comparable to the existing parity report)
# ─────────────────────────────────────────────────────────────────────────────

_EMPTY_SENTINELS = {"", "null", "none", "not available", "0.00", "n/a", "na",
                    "not applicable"}


def is_empty(val) -> bool:
    if val is None:
        return True
    if isinstance(val, (list, dict)):
        return len(val) == 0
    return str(val).lower().strip() in _EMPTY_SENTINELS


def normalize_val(val) -> str:
    """Lowercase, strip, collapse whitespace, normalise punctuation variants."""
    if is_empty(val):
        return ""
    s = str(val)
    _UNICODE_MAP = str.maketrans({
        "–": "-", "—": "-", "−": "-", "‐": "-",
        "‑": "-", "‒": "-", "―": "-",
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"',
        "·": ".", "…": "...", " ": " ",
    })
    s = s.translate(_UNICODE_MAP)
    s = s.lower().strip()
    s = re.sub(r"\s*-\s*", " - ", s)
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\(\s*", "(", s)
    s = re.sub(r"\s*\)", ")", s)
    return re.sub(r"\s+", " ", s).strip()


def classify_standard(a_val, b_val, field_name: str = "") -> tuple:
    """Scalar comparison — identical to custom_field_pipeline.classify_standard."""
    a = normalize_val(a_val)
    b = normalize_val(b_val)

    if a == b:
        return "Match", ""
    if not b and a:
        return "Missing in B", f"A='{a}'"
    if a and b and (a in b or b in a):
        return "Partial Match", f"A='{a}', B='{b}'"
    if a and b:
        return "Regression", f"A='{a}' → B='{b}'"
    return "Other", f"A='{a}', B='{b}'"


def _to_token_set(val) -> set:
    """Split a list or delimited string into a normalised token set."""
    if isinstance(val, list):
        items = val
    elif is_empty(val):
        items = []
    else:
        items = re.split(r"[,|;]+", str(val))
    return {normalize_val(i) for i in items if not is_empty(i)}


def classify_set_field(a_val, b_val, field_name: str = "") -> tuple:
    """
    Set comparison for skills / languages.

    Track A returns a comma-separated string, the AI parser returns a JSON list,
    and neither preserves order — a straight string compare would report every
    single row as a Regression, so these two fields are scored on set overlap
    instead.  Counts, coverage and sample diffs go into the details column.
    """
    a_set = _to_token_set(a_val)
    b_set = _to_token_set(b_val)

    if not a_set and not b_set:
        return "Match", ""
    if a_set and not b_set:
        return "Missing in B", f"A has {len(a_set)} item(s), B empty"
    if not a_set and b_set:
        return "Other", f"A empty, B has {len(b_set)} item(s)"

    common   = a_set & b_set
    only_a   = a_set - b_set
    only_b   = b_set - a_set
    union    = a_set | b_set
    jaccard  = len(common) / len(union) if union else 0.0
    coverage = len(common) / len(a_set) if a_set else 0.0

    detail = (
        f"A={len(a_set)}, B={len(b_set)}, common={len(common)}, "
        f"coverage={coverage * 100:.0f}%, jaccard={jaccard:.2f}"
    )
    if only_a:
        detail += f" | only in A: {sorted(only_a)[:5]}"
    if only_b:
        detail += f" | only in B: {sorted(only_b)[:5]}"

    if a_set == b_set:
        return "Match", detail
    if common:
        return "Partial Match", detail
    return "Regression", detail


def flatten_work_history_list(work_list: list) -> str:
    """'Title at Company (StartYear - EndYear)' — Comparator-style flattening."""
    if not work_list:
        return ""
    parts = []
    for work in work_list:
        if not isinstance(work, dict):
            continue
        company  = work.get("work_company_name", "") or ""
        title    = work.get("title", "") or ""
        start_ts = work.get("work_start_date", "")
        end_ts   = work.get("work_end_date", "")
        start, end = "", "Present"
        try:
            if start_ts and isinstance(start_ts, (int, float)) and start_ts > 0:
                start = time.strftime("%Y", time.localtime(start_ts))
        except (ValueError, OSError, OverflowError):
            start = ""
        try:
            if end_ts and isinstance(end_ts, (int, float)) and end_ts > 0:
                end = time.strftime("%Y", time.localtime(end_ts))
        except (ValueError, OSError, OverflowError):
            end = "Present"
        if title or company:
            parts.append(f"{title} at {company} ({start} - {end})")
    return " | ".join(parts)


def flatten_education_history_list(edu_list: list) -> str:
    """'Degree in Specialization from Institute (Start-End)' — Comparator-style."""
    if not edu_list:
        return ""
    edu_strs = []
    for education in edu_list:
        if not isinstance(education, dict):
            continue
        degree         = education.get("educational_qualification", "") or ""
        institute      = education.get("institute_name", "") or ""
        specialization = education.get("educational_specialization", "") or ""
        start_ts       = education.get("education_start_date", "")
        end_ts         = education.get("education_end_date", "")
        start_year, end_year = "NaN", "NaN"
        try:
            if start_ts and isinstance(start_ts, (int, float)) and start_ts > 0:
                start_year = time.strftime("%Y", time.localtime(start_ts))
        except (ValueError, OSError, OverflowError):
            start_year = "NaN"
        try:
            if end_ts and isinstance(end_ts, (int, float)) and end_ts > 0:
                end_year = time.strftime("%Y", time.localtime(end_ts))
        except (ValueError, OSError, OverflowError):
            end_year = "NaN"
        if degree or institute:
            s = degree
            if specialization:
                s += f" in {specialization}"
            if institute:
                s += f" from {institute}"
            s += f" ({start_year}-{end_year})"
            edu_strs.append(s)
    return " | ".join(edu_strs)


def _history_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, str(a).lower().strip(), str(b).lower().strip()).ratio()


def classify_history_comparator_style(ta_list, tb_list, field_name: str) -> tuple:
    """Array-length note + flattened-string SequenceMatcher, Comparator thresholds."""
    ta_list = ta_list if isinstance(ta_list, list) else []
    tb_list = tb_list if isinstance(tb_list, list) else []

    remark_parts = []
    if len(ta_list) != len(tb_list):
        remark_parts.append(f"array length mismatch: A={len(ta_list)} B={len(tb_list)}")

    if field_name == "workhistory":
        s_a = flatten_work_history_list(ta_list)
        s_b = flatten_work_history_list(tb_list)
    else:
        s_a = flatten_education_history_list(ta_list)
        s_b = flatten_education_history_list(tb_list)

    def _hist_empty(s: str) -> bool:
        return not s or str(s).lower().strip() in ("none", "null", "")

    a_empty = _hist_empty(s_a)
    b_empty = _hist_empty(s_b)

    if a_empty and b_empty:
        return "Match", "; ".join(remark_parts) if remark_parts else ""
    if a_empty and not b_empty:
        remark_parts.append("Track A flattened summary empty; Track B has data")
        return "Other", "; ".join(remark_parts)
    if not a_empty and b_empty:
        remark_parts.append("Track B flattened summary empty")
        return "Missing in B", "; ".join(remark_parts)

    sim = _history_similarity(s_a, s_b)
    if sim == 1.0:
        remark_parts.append("similarity=1.000 (Comparator-style)")
        return "Match", "; ".join(remark_parts)
    if sim >= 0.85:
        remark_parts.append(f"similarity={sim:.3f} (≥0.85 → partial, Comparator-style)")
        return "Partial Match", "; ".join(remark_parts)
    if len(s_b) > len(s_a) and sim > 0.6:
        remark_parts.append(
            f"similarity={sim:.3f}; B longer & sim>0.6 (Comparator extra-data rule)"
        )
        return "Partial Match", "; ".join(remark_parts)

    remark_parts.append(f"similarity={sim:.3f} (<0.85, Comparator-style)")
    return "Regression", "; ".join(remark_parts)


EXCEL_MAX_CELL_CHARS = 32767


def _cell_preview(val, max_len: int = EXCEL_MAX_CELL_CHARS) -> str:
    if val is None:
        return ""
    if isinstance(val, (list, dict)):
        s = json.dumps(val, ensure_ascii=False)
    else:
        s = str(val)
    if len(s) <= max_len:
        return s
    return s[: max_len - 50] + "…[truncated: Excel 32767 char cell limit]"


_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    def _clean(val):
        if isinstance(val, str):
            return _ILLEGAL_XML_RE.sub("", val)
        return val
    return df.apply(lambda col: col.map(_clean))


# ─────────────────────────────────────────────────────────────────────────────
# Track A — Albatross
# ─────────────────────────────────────────────────────────────────────────────

def _request_with_retries(method: str, url: str, *, label: str, **kwargs):
    """
    Issue an HTTP request, retrying on 5xx / 429 / transport errors with
    exponential back-off.  4xx responses are returned immediately (retrying a
    401 or 422 is pointless).  Raises the last error if every attempt fails.

    `files` bodies are re-opened by the caller via a factory, so this helper
    only ever retries kwargs that are safe to resend.
    """
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
            if resp.status_code >= 500 or resp.status_code == 429:
                last_exc = requests.HTTPError(
                    f"HTTP {resp.status_code} on {label}: {resp.text[:200]}",
                    response=resp,
                )
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF ** attempt
                    log.warning(f"{label}: HTTP {resp.status_code}, retry "
                                f"{attempt}/{MAX_RETRIES} in {wait:.0f}s")
                    time.sleep(wait)
                    continue
                raise last_exc
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if getattr(exc, "response", None) is not None and \
                    exc.response.status_code < 500 and exc.response.status_code != 429:
                raise
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF ** attempt
                log.warning(f"{label}: {exc}. retry {attempt}/{MAX_RETRIES} "
                            f"in {wait:.0f}s")
                time.sleep(wait)
            else:
                raise
    raise last_exc if last_exc else RuntimeError(f"{label} failed")


def login_track_a(base_url: str, email: str, password: str) -> dict:
    """
    POST /v1/login → {token, user_id, account_id}.

    The token lives under data.token; the user object is at the TOP level of
    the login payload (not under data), exposing `id` and `accountid`.
    """
    resp = _request_with_retries(
        "POST", f"{base_url}/v1/login", label="login",
        data={"email": email, "password": password},
    )
    body = resp.json()

    token = (body.get("data") or {}).get("token")
    if not token:
        raise RuntimeError("Login succeeded but no data.token in response")

    user = body.get("user") or (body.get("data") or {}).get("user") or {}
    user_id    = user.get("id")
    account_id = user.get("accountid", user.get("account_id"))

    if user_id is None or account_id is None:
        raise RuntimeError(
            f"Login response missing user.id / user.accountid (got id={user_id}, "
            f"accountid={account_id})"
        )

    log.info(f"Track A login OK: user_id={user_id} account_id={account_id}")
    return {"token": token, "user_id": user_id, "account_id": account_id}


def _auth_headers(token: str) -> dict:
    return {"authorization": f"Bearer {token}", "content-type": "application/json"}


def get_presigned_url(base_url: str, file_name: str, token: str) -> dict:
    r = _request_with_retries(
        "GET", f"{base_url}/v1/get-presigned-url", label="presign",
        headers={"authorization": f"Bearer {token}"},
        params={"fileName": file_name, "requestType": "put"},
    )
    return r.json().get("data", r.json())


def upload_pdf(put_url: str, file_path: Path) -> None:
    _request_with_retries(
        "PUT", put_url, label="s3-upload",
        data=file_path.read_bytes(),
        headers={"Content-Type": "application/pdf"},
    )


def call_parse_api(base_url: str, token: str, file_key: str, file_name: str) -> dict:
    payload = {
        "resumeParserData": {"filesInfo": {"key": file_key, "name": file_name}},
        "onlyParserData": True,
    }
    r = _request_with_retries(
        "POST", f"{base_url}/v1/candidates/parse-resume", label="parse-resume",
        headers=_auth_headers(token), json=payload,
    )
    return r.json()


def extract_standard_fields_track_a(parse_result: dict) -> dict:
    """Albatross candidate object → the shared standard-field schema."""
    c = (parse_result.get("data") or {}).get("candidate", {}) or {}
    return {
        "firstname":        c.get("firstname", ""),
        "lastname":         c.get("lastname", ""),
        "emailid":          c.get("emailid", ""),
        "contactnumber":    c.get("contactnumber", ""),
        "city":             c.get("city", ""),
        "state":            c.get("state", ""),
        "country":          c.get("country", ""),
        "address":          c.get("address", ""),
        "position":         c.get("position", ""),
        "lastorganisation": c.get("lastorganisation", ""),
        "workexpyr":        str(c.get("workexpyr", "") or ""),
        "summary":          c.get("summary", ""),
        "skill":            c.get("skill", ""),
        "languageskills":   c.get("languageskills", ""),
        "qualification":    c.get("qualification", ""),
        "specialization":   c.get("specialization", ""),
        "profilelinkedin":  c.get("profilelinkedin", ""),
        "profilegithub":    c.get("profilegithub", ""),
        "workhistory":      list(c.get("workhistory") or []),
        "educationhistory": list(c.get("educationhistory") or []),
    }


def parse_on_track_a(base_url: str, token: str, resume_path: Path) -> tuple:
    """Returns (standard_fields, timings_dict)."""
    t0 = time.time()
    presign = get_presigned_url(base_url, resume_path.name, token)
    t1 = time.time()
    upload_pdf(presign["preSignedUrl"], resume_path)
    t2 = time.time()
    result = call_parse_api(base_url, token, presign["key"], resume_path.name)
    t3 = time.time()

    timings = {
        "presign_ms": round((t1 - t0) * 1000, 2),
        "upload_ms":  round((t2 - t1) * 1000, 2),
        "parse_ms":   round((t3 - t2) * 1000, 2),
        "total_ms":   round((t3 - t0) * 1000, 2),
    }
    return extract_standard_fields_track_a(result), timings


# ─────────────────────────────────────────────────────────────────────────────
# Track B — AI parser v3
# ─────────────────────────────────────────────────────────────────────────────

_CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf":  "application/rtf",
    ".txt":  "text/plain",
}


def call_ai_parser(resume_path: Path, account_id, user_id, model_id: int) -> tuple:
    """
    POST the raw file to /v3/parse as multipart/form-data.

    Returns (payload, request_ms).  Raises on transport / HTTP / is_success errors.
    """
    if not AI_PARSER_TOKEN:
        raise RuntimeError(
            "AI_PARSER_TOKEN is not set. Add it to ../.env, e.g.\n"
            '  AI_PARSER_TOKEN=Private <your-token>'
        )

    content_type = _CONTENT_TYPES.get(resume_path.suffix.lower(), "application/octet-stream")
    form = {
        **AI_FORM_DEFAULTS,
        "account_id": str(account_id),
        "user_id":    str(user_id),
    }

    # The multipart body has to be rebuilt per attempt (the file handle is
    # consumed), so this call gets its own retry loop.  request_ms is measured
    # on the successful attempt only — retry sleeps never inflate the metric.
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            t0 = time.time()
            with resume_path.open("rb") as fh:
                r = requests.post(
                    AI_PARSER_URL,
                    params={"model_id": model_id},
                    headers={"Authorization": AI_PARSER_TOKEN},
                    files={"file": (resume_path.name, fh, content_type)},
                    data=form,
                    timeout=REQUEST_TIMEOUT,
                )
            request_ms = round((time.time() - t0) * 1000, 2)

            if r.status_code >= 500 or r.status_code == 429:
                last_exc = RuntimeError(
                    f"AI parser HTTP {r.status_code}: {r.text[:200]}"
                )
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF ** attempt
                    log.warning(f"ai-parse: HTTP {r.status_code}, retry "
                                f"{attempt}/{MAX_RETRIES} in {wait:.0f}s")
                    time.sleep(wait)
                    continue
                raise last_exc
            r.raise_for_status()

            payload = r.json()
            if payload.get("is_success") is False:
                raise RuntimeError(
                    f"AI parser returned is_success=false: {str(payload)[:300]}"
                )
            return payload, request_ms

        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if getattr(exc, "response", None) is not None and \
                    exc.response.status_code < 500 and exc.response.status_code != 429:
                raise
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF ** attempt
                log.warning(f"ai-parse: {exc}. retry {attempt}/{MAX_RETRIES} "
                            f"in {wait:.0f}s")
                time.sleep(wait)
            else:
                raise
    raise last_exc if last_exc else RuntimeError("AI parser call failed")


def extract_ai_record(payload: dict) -> dict:
    """
    Unwrap the v3 envelope.

        {"is_success": true, "data": [ { "0": {<record>} } ], ...}

    Falls back gracefully if the record is not keyed by "0" or `data` is a dict.
    """
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            if "0" in first and isinstance(first["0"], dict):
                return first["0"]
            for value in first.values():
                if isinstance(value, dict):
                    return value
            return first
    if isinstance(data, dict):
        return data
    return {}


def find_llm_response_time(payload: dict, record: dict):
    """
    `llm_response_time` (seconds) is not always present — look for it on the
    record, then the envelope, then anywhere in the tree.  Returns float or None.
    """
    for source in (record, payload):
        val = source.get("llm_response_time")
        if isinstance(val, (int, float)):
            return float(val)

    found = None

    def _walk(node):
        nonlocal found
        if found is not None:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "llm_response_time" and isinstance(value, (int, float)):
                    found = float(value)
                    return
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return found


def _first_non_empty(*values) -> str:
    for value in values:
        if value not in (None, "", []):
            return value
    return ""


def extract_standard_fields_ai(record: dict) -> dict:
    """
    Map the AI parser v3 record onto the same standard-field schema Track A uses,
    so every field can be compared 1:1.

      firstname        ← firstname            | full_name (first token)
      lastname         ← lastname             | full_name (rest)
      emailid          ← emailid | email
      contactnumber    ← contactnumber | phone
      address          ← fullAddress | location
      position         ← title
      lastorganisation ← currentOrganization
      workexpyr        ← total_experience
      summary          ← summary | candidateSummary
      skill            ← skills[]             (list — set-compared)
      languageskills   ← languages[]          (list — set-compared)
      qualification    ← educationHistory[0].educational_qualification | degree[0]
      specialization   ← educationHistory[0].educational_specialization
      profilelinkedin  ← linkedin_url | socialMedia.linkedin
      profilegithub    ← github_url   | socialMedia.github
      workhistory      ← workHistory[]        (same per-entry keys as Albatross)
      educationhistory ← educationHistory[]   (same per-entry keys as Albatross)
    """
    social   = record.get("socialMedia") or {}
    edu_list = record.get("educationHistory") or []
    edu_0    = edu_list[0] if edu_list and isinstance(edu_list[0], dict) else {}
    degrees  = record.get("degree") or []

    firstname = record.get("firstname") or ""
    lastname  = record.get("lastname") or ""
    if not firstname and not lastname:
        full = (record.get("full_name") or record.get("candidatename") or "").strip()
        if full:
            bits      = full.split()
            firstname = bits[0]
            lastname  = " ".join(bits[1:])

    work_exp = record.get("total_experience", "")
    work_exp = "" if work_exp in (None, "") else str(work_exp)

    return {
        "firstname":        firstname,
        "lastname":         lastname,
        "emailid":          _first_non_empty(record.get("emailid"), record.get("email")),
        "contactnumber":    _first_non_empty(record.get("contactnumber"), record.get("phone")),
        "city":             record.get("city", "") or "",
        "state":            record.get("state", "") or "",
        "country":          record.get("country", "") or "",
        "address":          _first_non_empty(record.get("fullAddress"), record.get("location")),
        "position":         record.get("title", "") or "",
        "lastorganisation": record.get("currentOrganization", "") or "",
        "workexpyr":        work_exp,
        "summary":          _first_non_empty(record.get("summary"),
                                             record.get("candidateSummary")),
        "skill":            record.get("skills") or [],
        "languageskills":   record.get("languages") or [],
        "qualification":    _first_non_empty(edu_0.get("educational_qualification"),
                                             degrees[0] if degrees else ""),
        "specialization":   edu_0.get("educational_specialization", "") or "",
        "profilelinkedin":  _first_non_empty(record.get("linkedin_url"),
                                             social.get("linkedin")),
        "profilegithub":    _first_non_empty(record.get("github_url"),
                                             social.get("github")),
        "workhistory":      record.get("workHistory") or [],
        "educationhistory": edu_list,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────

def discover_resumes(folder: Path, limit: int = 0) -> list:
    resumes = []
    for ext in ("pdf", "doc", "docx", "rtf"):
        resumes.extend(folder.glob(f"*.{ext}"))
        resumes.extend(folder.glob(f"*.{ext.upper()}"))

    seen, unique = set(), []
    for r in sorted(resumes, key=lambda p: (len(p.stem), p.stem.lower())):
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:limit] if limit else unique


def run_parity_parse(auth: dict, resumes: list, model_id: int, sleep_s: float) -> list:
    results = []
    total   = len(resumes)
    print(f"\n[Parity] {total} resume(s) — Track A: {BASE_URL_TRACK_A}")
    print(f"[Parity] Track B: {AI_PARSER_URL}?model_id={model_id}  "
          f"(account_id={auth['account_id']}, user_id={auth['user_id']})\n")

    for idx, resume_path in enumerate(resumes, start=1):
        rid = f"R{idx:03d}"
        print(f"  [{rid}] {resume_path.name} ...", end=" ", flush=True)

        try:
            std_a, timings_a = parse_on_track_a(
                BASE_URL_TRACK_A, auth["token"], resume_path
            )
            print(f"A✓({timings_a['total_ms']:.0f}ms)", end=" ", flush=True)

            payload, ai_ms = call_ai_parser(
                resume_path, auth["account_id"], auth["user_id"], model_id
            )
            record   = extract_ai_record(payload)
            llm_secs = find_llm_response_time(payload, record)
            std_b    = extract_standard_fields_ai(record)

            (RAW_AI_DIR / f"{resume_path.stem}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            llm_ms   = round(llm_secs * 1000, 2) if llm_secs is not None else None
            diff_ms  = round(ai_ms - timings_a["total_ms"], 2)
            faster   = "A" if diff_ms > 0 else "B" if diff_ms < 0 else "Equal"
            llm_note = f", llm={llm_ms:.0f}ms" if llm_ms is not None else ", llm=n/a"
            print(f"B✓({ai_ms:.0f}ms{llm_note}) [Δ={diff_ms:+.0f}ms, {faster} faster]")

            results.append({
                "resume_id":            rid,
                "filename":             resume_path.name,
                "track_a":              std_a,
                "track_b":              std_b,
                "track_a_presign_ms":   timings_a["presign_ms"],
                "track_a_upload_ms":    timings_a["upload_ms"],
                "track_a_parse_ms":     timings_a["parse_ms"],
                "track_a_total_ms":     timings_a["total_ms"],
                "ai_request_ms":        ai_ms,
                "ai_llm_response_ms":   llm_ms,
                "ai_llm_response_s":    llm_secs,
                "ai_overhead_ms": (round(ai_ms - llm_ms, 2)
                                   if llm_ms is not None else None),
                "time_difference_ms":   diff_ms,
                "faster_env":           faster,
                "ai_model_id":          payload.get("model_id", model_id),
                "ai_sovren_document_id": record.get("sovrenDocumentId", ""),
            })
            log.info(f"[Parity] {rid} OK | A={timings_a['total_ms']}ms "
                     f"B={ai_ms}ms llm={llm_ms}")

        except Exception as exc:
            print(f"ERROR — {exc}")
            log.error(f"[Parity] {rid} ({resume_path.name}) failed: {exc}")
            results.append({
                "resume_id": rid,
                "filename":  resume_path.name,
                "error":     str(exc),
            })

        time.sleep(sleep_s)

    RESULTS_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\n[Parity] Results saved → {RESULTS_JSON}")
    return results


def run_parity_validation(results: list) -> pd.DataFrame:
    rows = []

    for rec in results:
        rid      = rec.get("resume_id")
        filename = rec.get("filename", "")

        if "error" in rec:
            rows.append({
                "resume_id": rid, "filename": filename, "field": "-",
                "track_a_value": "", "track_b_value": "",
                "classification": "ERROR", "details": rec["error"],
            })
            continue

        std_a = rec.get("track_a", {})
        std_b = rec.get("track_b", {})

        for fname in COMPARE_FIELDS:
            a_val = std_a.get(fname)
            b_val = std_b.get(fname)

            if fname in HISTORY_FIELDS:
                a_list = a_val if isinstance(a_val, list) else []
                b_list = b_val if isinstance(b_val, list) else []
                classification, details = classify_history_comparator_style(
                    a_list, b_list, fname
                )
                if fname == "workhistory":
                    a_prev = _cell_preview(flatten_work_history_list(a_list))
                    b_prev = _cell_preview(flatten_work_history_list(b_list))
                else:
                    a_prev = _cell_preview(flatten_education_history_list(a_list))
                    b_prev = _cell_preview(flatten_education_history_list(b_list))
            elif fname in SET_FIELDS:
                classification, details = classify_set_field(a_val, b_val, fname)
                a_prev = _cell_preview(a_val)
                b_prev = _cell_preview(b_val)
            else:
                b_val = b_val if b_val is not None else ""
                classification, details = classify_standard(a_val, b_val, fname)
                a_prev = _cell_preview(a_val)
                b_prev = _cell_preview(b_val)

            rows.append({
                "resume_id":      rid,
                "filename":       filename,
                "field":          fname,
                "track_a_value":  a_prev,
                "track_b_value":  b_prev,
                "classification": classification,
                "details":        details,
            })

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def build_report(df: pd.DataFrame, results: list, model_id: int) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    CLS_FILL = {
        "Match":         "D6EFD8",
        "Partial Match": "FFF3CD",
        "Regression":    "FADADD",
        "Missing in B":  "FADADD",
        "Other":         "FFF9C4",
        "ERROR":         "F4CCCC",
    }

    HEADER_FILL    = PatternFill("solid", fgColor="1F4E79")
    HEADER_FONT    = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
    DATA_FONT      = Font(size=10, name="Calibri")
    DATA_FONT_BOLD = Font(size=10, bold=True, name="Calibri")
    BAND_A         = PatternFill("solid", fgColor="EBF3FB")
    BAND_B         = PatternFill("solid", fgColor="FFFFFF")
    THIN           = Side(border_style="thin", color="C0C0C0")
    BORDER         = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    COLS      = ["resume_id", "filename", "field",
                 "track_a_value", "track_b_value", "classification", "details"]
    COL_WIDTH = [12, 24, 22, 55, 55, 18, 60]

    safe_df = _sanitize_df(df)
    wb      = Workbook()

    # ── Sheet 1: Full Detail ────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Full Detail"
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False

    for ci, (col, width) in enumerate(zip(COLS, COL_WIDTH), start=1):
        cell = ws.cell(row=1, column=ci, value=col.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 30

    sub_df    = safe_df.reindex(columns=COLS).fillna("")
    cur_row   = 2
    band_flip = True

    for rid in sub_df["resume_id"].unique():
        block = sub_df[sub_df["resume_id"] == rid]
        band  = BAND_A if band_flip else BAND_B
        band_flip = not band_flip

        for _, row_data in block.iterrows():
            cls      = row_data.get("classification", "")
            cls_fill = PatternFill("solid", fgColor=CLS_FILL.get(cls, "FFFFFF"))
            for ci, col in enumerate(COLS, start=1):
                val  = row_data.get(col, "")
                cell = ws.cell(row=cur_row, column=ci, value="" if val is None else val)
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

    # ── Sheet 2: Field Summary ──────────────────────────────────────────────
    if not safe_df.empty:
        ws_s = wb.create_sheet("Field Summary")
        ws_s.sheet_view.showGridLines = False

        data  = safe_df[safe_df["classification"] != "ERROR"]
        pivot = (data.groupby(["field", "classification"])
                 .size().unstack(fill_value=0).reset_index())
        totals = data.groupby("field").size().rename("Total")
        pivot  = pivot.merge(totals, on="field")

        for cls in ["Match", "Partial Match", "Regression", "Missing in B", "Other"]:
            if cls not in pivot.columns:
                pivot[cls] = 0
            pivot[f"{cls} %"] = (pivot[cls] / pivot["Total"] * 100).round(1)
        pivot = pivot.rename(columns={"Match %": "Pass %"})

        desired = ["field", "Total", "Match", "Pass %",
                   "Partial Match", "Partial Match %",
                   "Regression", "Regression %",
                   "Missing in B", "Missing in B %",
                   "Other", "Other %"]
        ordered = [c for c in desired if c in pivot.columns] + \
                  [c for c in pivot.columns if c not in desired]
        pivot = pivot[ordered]
        pivot["field"] = pd.Categorical(pivot["field"], categories=COMPARE_FIELDS,
                                        ordered=True)
        pivot = pivot.sort_values("field")

        SUM_FILLS = {
            "Regression": "C0392B", "Regression %": "C0392B",
            "Missing in B": "E74C3C", "Missing in B %": "E74C3C",
            "Partial Match": "E67E22", "Partial Match %": "E67E22",
            "Match": "1E8449", "Pass %": "1E8449",
        }
        DEFAULT_FILL = "2C3E50"

        sum_cols = list(pivot.columns)
        ws_s.append(sum_cols)
        for i, cell in enumerate(ws_s[1]):
            col_name = sum_cols[i] if i < len(sum_cols) else ""
            cell.font      = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
            cell.fill      = PatternFill("solid",
                                         fgColor=SUM_FILLS.get(col_name, DEFAULT_FILL))
            cell.border    = BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center",
                                       wrap_text=True)
        ws_s.row_dimensions[1].height = 30

        ALT_A = PatternFill("solid", fgColor="F2F2F2")
        ALT_B = PatternFill("solid", fgColor="FFFFFF")

        for r_idx, row in enumerate(pivot.itertuples(index=False), start=2):
            band = ALT_A if r_idx % 2 == 0 else ALT_B
            for c_idx, col_name in enumerate(sum_cols, start=1):
                val  = row[c_idx - 1]
                if isinstance(val, pd.Categorical) or not isinstance(
                        val, (int, float, str, type(None))):
                    val = str(val)
                cell = ws_s.cell(row=r_idx, column=c_idx, value=val)
                cell.border    = BORDER
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if col_name.endswith("%") and isinstance(val, (int, float)):
                    if val >= 80:
                        cell.fill = PatternFill("solid", fgColor="D5F5E3")
                        cell.font = Font(size=10, bold=True, color="1E8449", name="Calibri")
                    elif val >= 40:
                        cell.fill = PatternFill("solid", fgColor="FDEBD0")
                        cell.font = Font(size=10, bold=True, color="784212", name="Calibri")
                    else:
                        cell.fill = PatternFill("solid", fgColor="FADBD8")
                        cell.font = Font(size=10, bold=True, color="922B21", name="Calibri")
                elif c_idx == 1:
                    cell.fill = PatternFill("solid", fgColor="D6EAF8")
                    cell.font = Font(size=10, bold=True, name="Calibri")
                else:
                    cell.fill = band
                    cell.font = Font(size=10, name="Calibri")
            ws_s.row_dimensions[r_idx].height = 20

        for c_idx, col_name in enumerate(sum_cols, start=1):
            if c_idx == 1:
                ws_s.column_dimensions[get_column_letter(c_idx)].width = 24
            elif col_name.endswith("%"):
                ws_s.column_dimensions[get_column_letter(c_idx)].width = 14
            else:
                ws_s.column_dimensions[get_column_letter(c_idx)].width = 16

    # ── Sheet 3: Response Time Analysis ─────────────────────────────────────
    valid = [r for r in results if "error" not in r]
    if valid:
        ws_rt = wb.create_sheet("Response Time Analysis")
        ws_rt.sheet_view.showGridLines = False
        ws_rt.freeze_panes = "A2"

        rt_cols = ["resume_id", "filename", "track_a_total_ms", "track_a_parse_ms",
                   "ai_request_ms", "ai_llm_response_ms", "ai_overhead_ms",
                   "time_difference_ms", "faster_env"]
        rt_headers = ["Resume ID", "Filename", "Track A Total (ms)",
                      "Track A Parse (ms)", "AI Request (ms)", "AI LLM Time (ms)",
                      "AI Non-LLM Overhead (ms)", "Difference (ms)", "Faster"]
        rt_widths = [12, 28, 18, 18, 16, 16, 22, 16, 10]

        for ci, (header, width) in enumerate(zip(rt_headers, rt_widths), start=1):
            cell = ws_rt.cell(row=1, column=ci, value=header)
            cell.font      = HEADER_FONT
            cell.fill      = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center",
                                       wrap_text=True)
            cell.border    = BORDER
            ws_rt.column_dimensions[get_column_letter(ci)].width = width
        ws_rt.row_dimensions[1].height = 32

        for row_idx, rec in enumerate(valid, start=2):
            for col_idx, col_name in enumerate(rt_cols, start=1):
                val  = rec.get(col_name)
                cell = ws_rt.cell(row=row_idx, column=col_idx)
                cell.border    = BORDER
                cell.font      = DATA_FONT
                cell.alignment = Alignment(vertical="center",
                                           horizontal="center" if col_idx > 2 else "left")

                if val is None:
                    cell.value = "n/a"
                    cell.fill  = PatternFill("solid", fgColor="F2F2F2")
                    cell.font  = Font(size=10, italic=True, color="7F8C8D", name="Calibri")
                    continue

                if col_name == "faster_env":
                    cell.value = val
                    if val == "A":
                        cell.fill = PatternFill("solid", fgColor="D5F5E3")
                        cell.font = Font(size=10, bold=True, color="1E8449", name="Calibri")
                    elif val == "B":
                        cell.fill = PatternFill("solid", fgColor="D6EAF8")
                        cell.font = Font(size=10, bold=True, color="1F4E79", name="Calibri")
                    else:
                        cell.fill = PatternFill("solid", fgColor="FFF3CD")
                        cell.font = Font(size=10, bold=True, color="856404", name="Calibri")
                elif col_name == "time_difference_ms":
                    cell.value = f"{val:+.2f}"
                    if val < 0:
                        cell.fill = PatternFill("solid", fgColor="E8F8F5")
                        cell.font = Font(size=10, color="0E6655", name="Calibri")
                    else:
                        cell.fill = PatternFill("solid", fgColor="FDEDEC")
                        cell.font = Font(size=10, color="943126", name="Calibri")
                elif col_name == "ai_llm_response_ms":
                    cell.value = f"{val:.2f}"
                    cell.fill  = PatternFill("solid", fgColor="EAF2F8")
                elif isinstance(val, (int, float)):
                    cell.value = f"{val:.2f}"
                    cell.fill  = PatternFill("solid", fgColor="F8F9FA")
                else:
                    cell.value = val
                    cell.fill  = PatternFill("solid", fgColor="FFFFFF")
            ws_rt.row_dimensions[row_idx].height = 20

        # Statistics block
        rt_df    = pd.DataFrame(valid)
        llm_df   = rt_df[rt_df["ai_llm_response_ms"].notna()]
        n_llm    = len(llm_df)
        stat_row = len(valid) + 3

        cell = ws_rt.cell(row=stat_row, column=1, value="STATISTICS")
        cell.font      = Font(size=12, bold=True, color="FFFFFF", name="Calibri")
        cell.fill      = PatternFill("solid", fgColor="34495E")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws_rt.merge_cells(start_row=stat_row, start_column=1,
                          end_row=stat_row, end_column=len(rt_cols))
        ws_rt.row_dimensions[stat_row].height = 25

        def _fmt(value):
            return f"{value:.2f}" if isinstance(value, (int, float)) else str(value)

        def _stat(series, fn):
            return _fmt(fn(series)) if len(series) else "—"

        a_total = rt_df["track_a_total_ms"]
        a_parse = rt_df["track_a_parse_ms"]
        b_req   = rt_df["ai_request_ms"]
        b_llm   = llm_df["ai_llm_response_ms"] if n_llm else pd.Series(dtype=float)
        b_over  = llm_df["ai_overhead_ms"] if n_llm else pd.Series(dtype=float)

        stats = [
            ("Average Response Time", _stat(a_total, lambda s: s.mean()),
             _stat(b_req, lambda s: s.mean()),
             _fmt(b_req.mean() - a_total.mean())),
            ("Highest Response Time", _stat(a_total, lambda s: s.max()),
             _stat(b_req, lambda s: s.max()),
             _fmt(b_req.max() - a_total.max())),
            ("Lowest Response Time", _stat(a_total, lambda s: s.min()),
             _stat(b_req, lambda s: s.min()),
             _fmt(b_req.min() - a_total.min())),
            ("Median Response Time", _stat(a_total, lambda s: s.median()),
             _stat(b_req, lambda s: s.median()),
             _fmt(b_req.median() - a_total.median())),
            ("Server Parse Time (avg)", _stat(a_parse, lambda s: s.mean()), "—", "—"),
            ("— LLM METRICS —", "", "", ""),
            (f"LLM Time avg  (n={n_llm}/{len(valid)})", "—",
             _stat(b_llm, lambda s: s.mean()), "—"),
            ("LLM Time max", "—", _stat(b_llm, lambda s: s.max()), "—"),
            ("LLM Time min", "—", _stat(b_llm, lambda s: s.min()), "—"),
            ("LLM Time median", "—", _stat(b_llm, lambda s: s.median()), "—"),
            ("LLM Time total", "—", _stat(b_llm, lambda s: s.sum()), "—"),
            ("Non-LLM Overhead (avg)", "—", _stat(b_over, lambda s: s.mean()), "—"),
            ("LLM Share of AI Request",
             "—",
             (f"{b_llm.sum() / llm_df['ai_request_ms'].sum() * 100:.1f}%"
              if n_llm and llm_df["ai_request_ms"].sum() else "—"),
             "—"),
            ("— COUNTS —", "", "", ""),
            ("Total Resumes", str(len(valid)), str(len(valid)), "—"),
            ("A Faster Count", str(int((rt_df["faster_env"] == "A").sum())), "—", "—"),
            ("B Faster Count", "—", str(int((rt_df["faster_env"] == "B").sum())), "—"),
            ("Responses with llm_response_time", "—", f"{n_llm}/{len(valid)}", "—"),
        ]

        header_row = stat_row + 1
        for ci, header in enumerate(["Metric", "Track A (ms)", "Track B / AI (ms)",
                                     "Difference"], start=1):
            cell = ws_rt.cell(row=header_row, column=ci, value=header)
            cell.font      = Font(size=10, bold=True, color="FFFFFF", name="Calibri")
            cell.fill      = PatternFill("solid", fgColor="5D6D7E")
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border    = BORDER
        ws_rt.row_dimensions[header_row].height = 22

        for offset, (metric, val_a, val_b, diff) in enumerate(stats, start=1):
            r = header_row + offset
            is_section = metric.startswith("—")

            cell = ws_rt.cell(row=r, column=1, value=metric)
            cell.font      = Font(size=10, bold=True, name="Calibri",
                                  color="FFFFFF" if is_section else "000000")
            cell.fill      = PatternFill("solid",
                                         fgColor="7F8C8D" if is_section else "ECF0F1")
            cell.alignment = Alignment(horizontal="left", vertical="center")
            cell.border    = BORDER

            for ci, val, tint in ((2, val_a, "E8F8F5"), (3, val_b, "EBF5FB"),
                                  (4, diff, "FFFFFF")):
                cell = ws_rt.cell(row=r, column=ci, value=val)
                cell.font      = DATA_FONT
                cell.fill      = PatternFill("solid",
                                             fgColor="7F8C8D" if is_section else tint)
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border    = BORDER

            if not is_section and diff not in ("—", ""):
                try:
                    diff_val = float(diff)
                    cell = ws_rt.cell(row=r, column=4)
                    if diff_val < 0:
                        cell.fill = PatternFill("solid", fgColor="D5F5E3")
                        cell.font = Font(size=10, bold=True, color="1E8449", name="Calibri")
                    elif diff_val > 0:
                        cell.fill = PatternFill("solid", fgColor="FADBD8")
                        cell.font = Font(size=10, bold=True, color="922B21", name="Calibri")
                except ValueError:
                    pass
            ws_rt.row_dimensions[r].height = 20

    # ── Sheet 4: Regressions Only ───────────────────────────────────────────
    regressions = safe_df[safe_df["classification"].isin(["Regression", "Missing in B"])]
    if not regressions.empty:
        ws_r = wb.create_sheet("Regressions Only")
        ws_r.sheet_view.showGridLines = False
        ws_r.freeze_panes = "A2"
        ws_r.append([c.replace("_", " ").title() for c in COLS])
        for ci, width in enumerate(COL_WIDTH, start=1):
            cell = ws_r.cell(row=1, column=ci)
            cell.font      = HEADER_FONT
            cell.fill      = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center",
                                       wrap_text=True)
            cell.border    = BORDER
            ws_r.column_dimensions[get_column_letter(ci)].width = width
        ws_r.row_dimensions[1].height = 30
        for r_idx, (_, row_data) in enumerate(regressions.iterrows(), start=2):
            for ci, col in enumerate(COLS, start=1):
                cell = ws_r.cell(row=r_idx, column=ci, value=row_data.get(col, ""))
                cell.border    = BORDER
                cell.font      = DATA_FONT
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            ws_r.row_dimensions[r_idx].height = 18

    # ── Sheet 5: Run Info ───────────────────────────────────────────────────
    ws_i = wb.create_sheet("Run Info")
    ws_i.sheet_view.showGridLines = False
    info = [
        ("Track A (baseline)", BASE_URL_TRACK_A),
        ("Track B (AI parser)", f"{AI_PARSER_URL}?model_id={model_id}"),
        ("Resumes folder", str(RESUME_FOLDER)),
        ("Resumes compared", str(len([r for r in results if "error" not in r]))),
        ("Errors", str(len([r for r in results if "error" in r]))),
        ("Fields compared", ", ".join(COMPARE_FIELDS)),
        ("Set-compared fields", ", ".join(sorted(SET_FIELDS))),
        ("Excluded fields", "sovren_document_id (unrelated ids on the two services)"),
        ("Generated", time.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    ws_i.column_dimensions["A"].width = 26
    ws_i.column_dimensions["B"].width = 110
    for r_idx, (k, v) in enumerate(info, start=1):
        c1 = ws_i.cell(row=r_idx, column=1, value=k)
        c1.font = Font(size=10, bold=True, name="Calibri")
        c1.fill = PatternFill("solid", fgColor="D6EAF8")
        c1.border = BORDER
        c2 = ws_i.cell(row=r_idx, column=2, value=v)
        c2.font = DATA_FONT
        c2.alignment = Alignment(wrap_text=True, vertical="top")
        c2.border = BORDER

    wb.save(REPORT_FILE)
    print(f"\n[Report] Saved → {REPORT_FILE}")


def print_summary(df: pd.DataFrame, results: list) -> None:
    print("\n" + "=" * 70)
    print("AI PARSER PARITY SUMMARY  (Track A vs AI parser v3, original resumes)")
    print("=" * 70)

    data = df[df["classification"] != "ERROR"]
    if data.empty:
        print("No data.")
        return

    total = len(data)
    print(f"\nTotal comparisons : {total}")
    for cls in ["Match", "Partial Match", "Regression", "Missing in B", "Other"]:
        count = int((data["classification"] == cls).sum())
        if count:
            print(f"  {cls:<28s} {count:4d}  ({count / total * 100:5.1f}%)")

    print("\n  Per-field (Pass / Partial / Regression / Missing / Other):")
    for fname in COMPARE_FIELDS:
        grp = data[data["field"] == fname]
        if grp.empty:
            continue
        n  = len(grp)
        p  = int((grp["classification"] == "Match").sum())
        pm = int((grp["classification"] == "Partial Match").sum())
        r  = int((grp["classification"] == "Regression").sum())
        mb = int((grp["classification"] == "Missing in B").sum())
        o  = int((grp["classification"] == "Other").sum())
        print(f"    {fname:<20s}  Pass={p:3d}  Partial={pm:3d}  Regression={r:3d}"
              f"  Missing={mb:3d}  Other={o:3d}  (n={n})")

    errors = int((df["classification"] == "ERROR").sum())
    if errors:
        print(f"\n  Errors : {errors}")

    valid = [r for r in results if "error" not in r]
    if not valid:
        print("=" * 70)
        return

    rt   = pd.DataFrame(valid)
    llm  = rt[rt["ai_llm_response_ms"].notna()]

    print("\n" + "─" * 70)
    print("RESPONSE TIME ANALYSIS")
    print("─" * 70)
    print("\n  Track A (presign + upload + parse):")
    print(f"    avg {rt['track_a_total_ms'].mean():9.2f} ms   "
          f"min {rt['track_a_total_ms'].min():9.2f}   "
          f"max {rt['track_a_total_ms'].max():9.2f}")
    print(f"    parse-call only, avg {rt['track_a_parse_ms'].mean():.2f} ms")
    print("\n  AI parser v3 (single multipart request):")
    print(f"    avg {rt['ai_request_ms'].mean():9.2f} ms   "
          f"min {rt['ai_request_ms'].min():9.2f}   "
          f"max {rt['ai_request_ms'].max():9.2f}")
    print(f"\n    Difference (AI − Track A): "
          f"{rt['ai_request_ms'].mean() - rt['track_a_total_ms'].mean():+.2f} ms")

    print(f"\n  LLM time (llm_response_time), present on {len(llm)}/{len(valid)} response(s):")
    if len(llm):
        print(f"    avg {llm['ai_llm_response_ms'].mean():9.2f} ms   "
              f"min {llm['ai_llm_response_ms'].min():9.2f}   "
              f"max {llm['ai_llm_response_ms'].max():9.2f}   "
              f"median {llm['ai_llm_response_ms'].median():9.2f}")
        print(f"    non-LLM overhead avg {llm['ai_overhead_ms'].mean():.2f} ms")
        share = llm["ai_llm_response_ms"].sum() / llm["ai_request_ms"].sum() * 100
        print(f"    LLM share of AI request time: {share:.1f}%")
    else:
        print("    not reported by the API for any resume in this run")

    a_faster = int((rt["faster_env"] == "A").sum())
    b_faster = int((rt["faster_env"] == "B").sum())
    print("\n  Performance comparison:")
    print(f"    Track A faster: {a_faster:3d}  ({a_faster / len(rt) * 100:5.1f}%)")
    print(f"    AI parser faster: {b_faster:3d}  ({b_faster / len(rt) * 100:5.1f}%)")
    print("=" * 70)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Standard field parity: Albatross Track A vs AI parser v3"
    )
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N resumes (0 = all)")
    ap.add_argument("--reparse", action="store_true",
                    help="ignore cached results and parse again")
    ap.add_argument("--model-id", type=int, default=AI_MODEL_ID,
                    help=f"AI parser model_id (default {AI_MODEL_ID})")
    ap.add_argument("--resumes", type=str, default=str(RESUME_FOLDER),
                    help="folder containing resumes")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="seconds to sleep between resumes (default 1.0)")
    args = ap.parse_args()

    print("=" * 70)
    print("AI Parser Standard Field Parity Check")
    print("=" * 70)

    if RESULTS_JSON.exists() and not args.reparse:
        print(f"\n[Parity] Loading cached results → {RESULTS_JSON}")
        print("         (use --reparse to force a fresh run)")
        results = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    else:
        print("\n[Auth] Logging in to Track A ...")
        auth = login_track_a(BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A)
        print(f"  Token OK | user_id={auth['user_id']} account_id={auth['account_id']}")

        folder  = Path(args.resumes)
        resumes = discover_resumes(folder, args.limit)
        if not resumes:
            print(f"[Parity] No resumes found in {folder}")
            return
        results = run_parity_parse(auth, resumes, args.model_id, args.sleep)

    ok  = sum(1 for r in results if "error" not in r)
    print(f"[Parity] {ok}/{len(results)} resume(s) parsed on both tracks")

    print("\n[Validation] Comparing standard fields ...")
    df = run_parity_validation(results)

    build_report(df, results, args.model_id)
    print_summary(df, results)


if __name__ == "__main__":
    main()

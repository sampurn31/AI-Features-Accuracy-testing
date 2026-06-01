#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Custom Field Parsing Automation Pipeline
-----------------------------------------------
Track A  : baseline parse → resume_text + standard-field snapshot
Custom fields: create separately —  python3 create_custom_fields.py
               (writes pipeline_output/custom_fields_manifest.json; pipeline loads it)
LLM      : Groq → snippet + value per (resume × field)
Track B  : SS env → re-parse modified text → validate standard + custom fields
Report   : Excel → metrics + regression sheet
Column mapping: column_index in CUSTOM_FIELDS → custcolumn{N} on candidate JSON.
"""

import os, json, time, random, re, logging, requests, uuid, datetime, copy
from difflib import SequenceMatcher
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv
from groq import Groq
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
import pandas as pd

load_dotenv()

from config import (
    GROQ_API_KEY,
    BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A,
    BASE_URL_SS, EMAIL_SS, PASSWORD_SS,
    ENTITY_TYPE_ID,
)

GROQ_MODEL = "openai/gpt-oss-20b"

RESUME_FOLDER = Path(__file__).parent / "100 resumes"

OUTPUT_DIR        = Path("pipeline_output")
INJECTED_PDF_DIR  = OUTPUT_DIR / "injected_pdfs"
LOGS_DIR          = Path(__file__).parent / "logs"
TRACK_B_RESPONSE_LOG = LOGS_DIR / "track_b_parse_responses.json"
BASELINE_FILE     = OUTPUT_DIR / "track_a_baseline.json"
GROUND_TRUTH_FILE = OUTPUT_DIR / "ground_truth.json"
TRACK_B_FILE      = OUTPUT_DIR / "track_b_results.json"
REPORT_FILE       = OUTPUT_DIR / "validation_report.xlsx"
CUSTOM_FIELDS_MANIFEST = OUTPUT_DIR / "custom_fields_manifest.json"

CUSTOM_FIELDS = [
    {
        "column_index": 1,
        "name": "Preferred Contact Window",
        "type": "text",
        "description": (
            "Rough sense of when this person is usually reachable for a recruiter conversation, "
            "without treating it as a rigid appointment."
        ),
        "options": [],
    },
    {
        "column_index": 2,
        "name": "Career Objective Statement",
        "type": "longtext",
        "description": (
            "A concise statement of what they want next in their career, written for a profile "
            "sidebar rather than a full narrative bio."
        ),
        "options": [],
    },
    {
        "column_index": 3,
        "name": "Professional Certification Expiry",
        "type": "date",
        "description": (
            "The calendar day after which their primary professional credential is no longer "
            "considered current for compliance or hiring checks."
        ),
        "options": [],
    },
    {
        "column_index": 4,
        "name": "Open to Relocation",
        "type": "checkbox",
        "description": (
            "Whether they would consider a longer-term move for the right role, as opposed to "
            "occasional travel only."
        ),
        "options": [],
    },
    {
        "column_index": 5,
        "name": "Preferred Employment Type",
        "type": "dropdown",
        "description": (
            "The engagement style they are targeting from a payroll and commitment standpoint."
        ),
        "options": [
            "Full-time",
            "Part-time",
            "Contract",
            "Internship",
            "Freelance",
        ],
    },
    {
        "column_index": 6,
        "name": "Acceptable Work Arrangements",
        "type": "multiselect",
        "description": (
            "The workplace presence patterns they can honestly commit to, which may combine "
            "several modes at once."
        ),
        "options": [
            "Remote",
            "Hybrid",
            "On-site",
            "Weekly travel",
            "Client-site",
        ],
    },
    {
        "column_index": 7,
        "name": "Alternate Contact Phone",
        "type": "phonenumber",
        "description": (
            "Another voice line recruiters can try if the main number is tied up or goes unanswered."
        ),
        "options": [],
    },
    {
        "column_index": 8,
        "name": "Secondary Professional Email",
        "type": "email",
        "description": (
            "An inbox used for professional correspondence that should be treated separately from "
            "whatever appears most prominently at the top of the resume."
        ),
        "options": [],
    },
    {
        "column_index": 9,
        "name": "Portfolio Website URL",
        "type": "social_profile",
        "description": (
            "Where they point people who want proof of work beyond employer names and titles."
        ),
        "options": [],
    },
    {
        "column_index": 10,
        "name": "Expected Annual Salary",
        "type": "number",
        "description": (
            "The total yearly compensation they have in mind when evaluating new opportunities."
        ),
        "options": [],
    },
]
os.makedirs("logs", exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
INJECTED_PDF_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename="logs/pipeline.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

_EMPTY_SENTINELS = {"", "null", "none", "not available", "0.00", "n/a", "na",
                    "not applicable"}


def is_empty(val) -> bool:
    if val is None:
        return True
    return str(val).lower().strip() in _EMPTY_SENTINELS


def normalize_val(val) -> str:
    """Lowercase, strip, collapse whitespace, and normalise common punctuation
    variants so that PDF rendering differences (en-dashes, curly quotes,
    variable hyphen/slash spacing, etc.) don't cause false mismatches.
    Returns '' for any empty sentinel.

    Normalisation pipeline (in order):
      1. Unicode map  : en-dash/em-dash → '-', curly quotes → straight, NBSP → space, …
      2. Lowercase + strip
      3. Hyphen spacing     : 'ML-NLP' / 'ML -NLP' → 'ML - NLP'
      4. Slash spacing      : 'A / B' / 'A/ B'     → 'A/B'
      5. Parenthesis spacing: '( foo )' → '(foo)'
      6. Final whitespace collapse
    """
    if is_empty(val):
        return ""
    s = str(val)
    _UNICODE_MAP = str.maketrans({
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2015": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u00b7": ".",
        "\u2026": "...",
        "\u00a0": " ",
    })
    s = s.translate(_UNICODE_MAP)
    s = s.lower().strip()
    s = re.sub(r"\s*-\s*", " - ", s)
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\(\s*", "(", s)
    s = re.sub(r"\s*\)", ")", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_numeric(val) -> str:
    """Strip currency symbols / separators, return clean integer string."""
    if is_empty(val):
        return ""
    digits = re.sub(r"[^\d]", "", str(val))
    try:
        return str(int(digits)) if digits else ""
    except ValueError:
        return digits


def normalize_list(val) -> list:
    """Normalize a list or comma-separated string to a sorted lowercase list."""
    if isinstance(val, list):
        return sorted(normalize_val(v) for v in val if not is_empty(v))
    if is_empty(val):
        return []
    return sorted(normalize_val(v) for v in re.split(r"[,|;]+", str(val)) if v.strip())


def normalize_option(val) -> str:
    """
    Strip-only normalisation for dropdown / multiselect options.
    Case is preserved exactly as stored — only leading/trailing whitespace is removed.
    """
    if is_empty(val):
        return ""
    return str(val).strip()


def normalize_option_list(val) -> list:
    """
    Strip-only normalisation for a multiselect value (list or delimited string).
    Returns a sorted list of stripped, non-empty option strings.
    Case is preserved exactly — no lowercase conversion.
    """
    if isinstance(val, list):
        return sorted(normalize_option(v) for v in val if not is_empty(v))
    if is_empty(val):
        return []
    return sorted(normalize_option(v) for v in re.split(r"[,|;]+", str(val)) if v.strip())


_CHECKBOX_TRUE  = {"1"}
_CHECKBOX_FALSE = {"0"}


def normalize_checkbox(val) -> str:
    """
    Normalise a checkbox value to exactly "1" (checked) or "0" (unchecked).
    Accepts: "1" or 1 → "1"
             "0" or 0 → "0"
    Anything else is returned stripped as-is so it surfaces as a Mismatch.
    """
    if is_empty(val):
        return ""
    v = str(val).strip()
    if v in _CHECKBOX_TRUE:
        return "1"
    if v in _CHECKBOX_FALSE:
        return "0"
    return v
def get_token(base_url: str, email: str, password: str) -> str:
    resp = requests.post(f"{base_url}/v1/login", data={"email": email, "password": password})
    resp.raise_for_status()
    token = resp.json()["data"]["token"]
    log.info(f"Token fetched for {email} @ {base_url}")
    return token


def _auth_headers(token: str) -> dict:
    return {"authorization": f"Bearer {token}", "content-type": "application/json"}

def get_presigned_url(base_url: str, file_name: str, token: str) -> dict:
    r = requests.get(
        f"{base_url}/v1/get-presigned-url",
        headers={"authorization": f"Bearer {token}"},
        params={"fileName": file_name, "requestType": "put"},
    )
    if r.status_code == 401:
        raise Exception("Token expired or invalid (401)")
    r.raise_for_status()
    return r.json().get("data", r.json())


def upload_pdf(put_url: str, file_path: Path) -> None:
    r = requests.put(put_url, data=file_path.read_bytes(),
                     headers={"Content-Type": "application/pdf"})
    r.raise_for_status()


def upload_pdf_bytes(put_url: str, pdf_bytes: bytes) -> None:
    r = requests.put(put_url, data=pdf_bytes,
                     headers={"Content-Type": "application/pdf"})
    r.raise_for_status()

def text_to_pdf_bytes(text: str) -> bytes:
    """
    Wrap plain text in a minimal A4 PDF using reportlab.
    The parser receives a proper PDF, identical in content to the modified text.
    """
    buf    = BytesIO()
    doc    = SimpleDocTemplate(buf, pagesize=A4,
                               leftMargin=20*mm, rightMargin=20*mm,
                               topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    style  = styles["Normal"]
    style.fontName  = "Helvetica"
    style.fontSize  = 10
    style.leading   = 14

    story = []
    for para in text.split("\n"):
        clean = para.strip()
        if clean:
            story.append(Paragraph(clean.replace("&", "&amp;")
                                        .replace("<", "&lt;")
                                        .replace(">", "&gt;"), style))
            story.append(Spacer(1, 2*mm))
        else:
            story.append(Spacer(1, 4*mm))

    doc.build(story)
    return buf.getvalue()

def call_parse_api(base_url: str, token: str, file_key: str, file_name: str) -> dict:
    """
    POST /v1/candidates/parse-resume (staging endpoint)

    Uses the new staging API format that returns work/education history
    directly in the response (no separate API calls needed).
    
    Payload format: {"resumeParserData":{"filesInfo":{"key":...,"name":...}},"onlyParserData":true}
    """
    payload = {
        "resumeParserData": {
            "filesInfo": {
                "key": file_key,
                "name": file_name,
            },
        },
        "onlyParserData": True,
    }
    r = requests.post(
        f"{base_url}/v1/candidates/parse-resume",
        headers=_auth_headers(token),
        json=payload,
    )
    r.raise_for_status()
    return r.json()


def _enrich_with_history(base_url: str, token: str, parse_result: dict) -> None:
    """
    Extract work/education history from the parse response and standardize
    the keys so extract_standard_fields can find them.

    The new staging API returns:
        parse_result["data"]["candidate"]["workhistory"]       → list[dict]
        parse_result["data"]["candidate"]["educationhistory"]  → list[dict]
    
    We copy these to top-level keys for backward compatibility:
        parse_result["data"]["work_history"]       → list[dict]
        parse_result["data"]["education_history"]  → list[dict]
    """
    candidate = (parse_result.get("data") or {}).get("candidate", {})
    if not candidate:
        log.warning("[History] No candidate object in parse result — skipping history extraction")
        return
    
    data = parse_result.setdefault("data", {})
    
    work_history = candidate.get("workhistory", []) or []
    education_history = candidate.get("educationhistory", []) or []
    
    data["work_history"] = work_history
    data["education_history"] = education_history
    
    log.info(
        f"[History] Extracted from parse response: "
        f"{len(work_history)} work, {len(education_history)} edu entries"
    )

def flatten_work_history_list(work_list: list) -> str:
    """
    Same string as Comparator.extract_work_history builds from work_history:
    title / company / years only — ignores work_description.
    """
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
                start = datetime.datetime.fromtimestamp(start_ts).strftime("%Y")
        except (ValueError, OSError, OverflowError):
            start = ""
        try:
            if end_ts and isinstance(end_ts, (int, float)) and end_ts > 0:
                end = datetime.datetime.fromtimestamp(end_ts).strftime("%Y")
        except (ValueError, OSError, OverflowError):
            end = "Present"
        if title or company:
            parts.append(f"{title} at {company} ({start} - {end})")
    return " | ".join(parts)


def flatten_education_history_list(edu_list: list) -> str:
    """
    Same string as Comparator.extract_education_history — ignores
    education_description (and matches its degree/institute/specialization/dates).
    """
    if not edu_list:
        return ""
    edu_strs = []
    for education in edu_list:
        if not isinstance(education, dict):
            continue
        degree = education.get("educational_qualification", "") or ""
        institute = education.get("institute_name", "") or ""
        specialization = education.get("educational_specialization", "") or ""
        start_ts = education.get("education_start_date", "")
        end_ts = education.get("education_end_date", "")
        start_year, end_year = "NaN", "NaN"
        try:
            if start_ts and isinstance(start_ts, (int, float)) and start_ts > 0:
                start_year = datetime.datetime.fromtimestamp(start_ts).strftime("%Y")
        except (ValueError, OSError, OverflowError):
            start_year = "NaN"
        try:
            if end_ts and isinstance(end_ts, (int, float)) and end_ts > 0:
                end_year = datetime.datetime.fromtimestamp(end_ts).strftime("%Y")
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
    """Comparator.py similarity() — whole-string ratio."""
    return SequenceMatcher(None, str(a).lower().strip(), str(b).lower().strip()).ratio()


def classify_history_comparator_style(ta_list, tb_list, field_name: str) -> tuple:
    """
    1) If array lengths differ, note it in details (remark).
    2) Flatten each side like Comparator (no long description fields).
    3) Compare flattened strings with Comparator thresholds (SequenceMatcher).
    """
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
        return "Regression", "; ".join(remark_parts)

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


def extract_standard_fields(parse_result: dict) -> dict:
    """
    Extract standard candidate fields from a parse response.

    Work/education history priority:
      1. data["work_history"] / data["education_history"]  — written by
         _enrich_with_history() after the ?actionsteps=1 parse call.
      2. candidate["workhistory"] / candidate["educationhistory"] — inline
         fallback (present on older endpoint responses).

    Validation flattens them Comparator-style (no description text) then
    uses SequenceMatcher.
    """
    data = parse_result.get("data", {})
    c    = data.get("candidate", {})

    work_history = (
        copy.deepcopy(data["work_history"])
        if data.get("work_history")
        else copy.deepcopy(list(c.get("workhistory") or []))
    )
    edu_history = (
        copy.deepcopy(data["education_history"])
        if data.get("education_history")
        else copy.deepcopy(list(c.get("educationhistory") or []))
    )

    return {
        "firstname":          c.get("firstname", ""),
        "lastname":           c.get("lastname", ""),
        "emailid":            c.get("emailid", ""),
        "contactnumber":      c.get("contactnumber", ""),
        "city":               c.get("city", ""),
        "state":              c.get("state", ""),
        "country":            c.get("country", ""),
        "address":            c.get("address", ""),
        "position":           c.get("position", ""),
        "lastorganisation":   c.get("lastorganisation", ""),
        "workexpyr":          str(c.get("workexpyr", "")),
        "summary":            c.get("summary", ""),
        "skill":              c.get("skill", ""),
        "languageskills":     c.get("languageskills", ""),
        "qualification":      c.get("qualification", ""),
        "specialization":     c.get("specialization", ""),
        "profilelinkedin":    c.get("profilelinkedin", ""),
        "profilegithub":      c.get("profilegithub", ""),
        "sovren_document_id": str(c.get("sovren_document_id") or ""),
        "workhistory":        work_history,
        "educationhistory":   edu_history,
    }


def extract_custom_fields(parse_result: dict, column_indices: list) -> dict:
    """
    Read custcolumn{N} values from data.candidate (AI parser embeds them
    directly on the candidate object, e.g. candidate.custcolumn4).
    Returns {column_index: value} for every requested index.
    Falls back gracefully to None when the key is absent.
    """
    c = parse_result.get("data", {}).get("candidate", {})
    return {idx: c.get(f"custcolumn{idx}") for idx in column_indices}


def extract_resume_text(parse_result: dict) -> str:
    return parse_result.get("data", {}).get("candidate", {}).get("resumetext", "")

def run_track_a(token: str) -> list:
    resumes = []
    for ext in ("pdf", "doc", "docx"):
        resumes.extend(RESUME_FOLDER.glob(f"*.{ext}"))
        resumes.extend(RESUME_FOLDER.glob(f"*.{ext.upper()}"))

    seen, unique = set(), []
    for r in resumes:
        k = str(r).lower()
        if k not in seen:
            seen.add(k)
            unique.append(r)

    print(f"\n[Track A] Found {len(unique)} resume(s)  ({BASE_URL_TRACK_A})")
    baseline = []

    for idx, resume_path in enumerate(unique, start=1):
        rid = f"R{idx:03d}"
        print(f"  [{rid}] {resume_path.name} ...", end=" ", flush=True)
        try:
            presign    = get_presigned_url(BASE_URL_TRACK_A, resume_path.name, token)
            upload_pdf(presign["preSignedUrl"], resume_path)
            result     = call_parse_api(BASE_URL_TRACK_A, token,
                                        presign["key"], resume_path.name)
            _enrich_with_history(BASE_URL_TRACK_A, token, result)
            std_fields = extract_standard_fields(result)

            baseline.append({
                "resume_id":       rid,
                "filename":        resume_path.name,
                "resume_text":     extract_resume_text(result),
                "standard_fields": std_fields,
            })
            print("OK")
            log.info(f"[Track A] {rid} OK")
        except Exception as exc:
            print(f"ERROR - {exc}")
            log.error(f"[Track A] {rid} failed: {exc}")
            baseline.append({"resume_id": rid, "filename": resume_path.name, "error": str(exc)})
        time.sleep(1)

    BASELINE_FILE.write_text(json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Track A] Baseline saved → {BASELINE_FILE}")
    return baseline

def _api_type(field_type: str) -> str:
    """Map pipeline type keys → RecruitCRM `extrafieldtype` for POST /v1/custom-fields.

    SS expects literal strings `social_profile` and `date_time` (not legacy socialprofile/datetime).
    """
    return {
        "text": "text",
        "longtext": "longtext",
        "number": "number",
        "multiselect": "multiselect",
        "dropdown": "dropdown",
        "date": "date",
        "datetime": "date_time",
        "date_time": "date_time",
        "checkbox": "checkbox",
        "phone": "phone",
        "phonenumber": "phone",
        "email": "email",
        "social": "social_profile",
        "social_profile": "social_profile",
    }.get(field_type, "text")


def create_custom_fields(token: str) -> list:
    """
    Validates column_index uniqueness, then POSTs each field to SS.
    column_index is taken from CUSTOM_FIELDS config and sent as columnid — no
    runtime mapping is derived from the API response.
    Returns the CUSTOM_FIELDS list enriched with the server-assigned field_id.
    """
    used_indices: set = set()
    for f in CUSTOM_FIELDS:
        ci = f["column_index"]
        if not (1 <= ci <= 99):
            raise ValueError(f"column_index={ci} for '{f['name']}' is outside [1, 99]")
        if ci in used_indices:
            raise ValueError(f"Duplicate column_index={ci} found for '{f['name']}'")
        used_indices.add(ci)

    print(f"\n[Setup / SS] Creating {len(CUSTOM_FIELDS)} custom fields ...")
    created = []

    for field in CUSTOM_FIELDS:
        options = []
        for seq, opt_label in enumerate(field.get("options", []), start=1):
            options.append({
                "label":       opt_label,
                "sequence_no": seq,
                "tempId":      str(uuid.uuid4()),
            })

        payload = {
            "custumField": {
                "extrafieldname":      field["name"],
                "extrafieldtype":      _api_type(field["type"]),
                "entitytypeid":        ENTITY_TYPE_ID,
                "columnid":            field["column_index"],
                "defaultvalue":        None,
                "defaultoptionsvalue": options,
                "description":         field["description"],
                "is_parser_enabled":   1,
            },
            "deleteSocialFile": False,
        }
        try:
            r = requests.post(f"{BASE_URL_SS}/v1/custom-fields",
                              headers=_auth_headers(token), json=payload)
            r.raise_for_status()
            field_id = r.json()["data"]["custumField"]["id"]

            created.append({**field, "field_id": field_id})
            print(f"  Created: '{field['name']}' → id={field_id}, custcolumn{field['column_index']}")
            log.info(f"[Setup] '{field['name']}' id={field_id} custcolumn{field['column_index']}")
        except Exception as exc:
            print(f"  ERROR '{field['name']}': {exc}")
            log.error(f"[Setup] '{field['name']}' failed: {exc}")
        time.sleep(0.5)

    return created


def save_custom_fields_manifest(created: list) -> None:
    """Persist field definitions + server field_ids for the main pipeline."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    payload = {
        "base_url_ss": BASE_URL_SS,
        "fields":      created,
    }
    CUSTOM_FIELDS_MANIFEST.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info(f"[Setup] Manifest saved → {CUSTOM_FIELDS_MANIFEST}")


def load_custom_fields_from_manifest() -> list:
    """
    Load custom fields created earlier (run create_custom_fields.py once).
    Each entry must include field_id, column_index, name, type, description, options.
    """
    if not CUSTOM_FIELDS_MANIFEST.is_file():
        raise FileNotFoundError(
            f"Missing {CUSTOM_FIELDS_MANIFEST}. "
            "Run:  python3 create_custom_fields.py"
        )
    data = json.loads(CUSTOM_FIELDS_MANIFEST.read_text(encoding="utf-8"))
    fields = data.get("fields") or []
    if not fields:
        raise ValueError(f"{CUSTOM_FIELDS_MANIFEST} has no 'fields' entries")
    for f in fields:
        if "field_id" not in f:
            raise ValueError(f"Manifest entry missing field_id: {f.get('name', f)}")
    log.info(f"[Setup] Loaded {len(fields)} custom fields from manifest")
    return fields

def pre_scan_existing_values(resume_text: str, field: dict) -> list:
    """Return option strings already in resume_text (multiselect: list; dropdown: one)."""
    text_lower = resume_text.lower()
    ftype = field["type"]
    opts  = field.get("options", [])
    if ftype == "multiselect":
        return [opt for opt in opts if opt.lower() in text_lower]
    if ftype == "dropdown":
        for opt in opts:
            if opt.lower() in text_lower:
                return [opt]
    return []

_groq = Groq(api_key=GROQ_API_KEY)


def _build_prompt(field: dict, forced_value=None) -> tuple:
    """
    Returns (prompt, ground_truth_value).
    Generates a clean, natural resume snippet that clearly states the correct
    value(s) for the field — no distractors, no red herrings, no ambiguity.
    """
    fname  = field["name"]
    fdesc  = field["description"]
    ftype  = field["type"]

    if ftype == "multiselect":
        opts = field.get("options", [])
        if forced_value is not None:
            selected = forced_value
        elif not opts:
            selected = []
        else:
            n_hi = min(4, len(opts))
            n_lo = min(2, n_hi) if n_hi else 1
            n_select = random.randint(n_lo, n_hi) if n_hi >= 2 else 1
            selected = random.sample(opts, k=n_select)
        locked_value = selected
        return_line  = f'"value": {json.dumps(selected)}'
        value_hint   = (
            f"Values to embed verbatim: {json.dumps(selected)}\n\n"
            "SNIPPET RULES:\n"
            f"- Write 3-5 natural resume sentences that clearly mention all {len(selected)} values.\n"
            "- Each value must appear verbatim at least once — do NOT paraphrase or abbreviate.\n"
            "- Do NOT mention any other options not in the list above.\n"
            "- Do NOT use label-value markup like 'Selected: …'."
        )

    elif ftype == "dropdown":
        opts = field.get("options", [])
        picked = forced_value if forced_value is not None else random.choice(opts)
        locked_value = picked
        return_line  = f'"value": {json.dumps(picked)}'
        value_hint   = (
            f"The only allowed value is one of: {json.dumps(opts)}\n"
            f"Pick exactly this value for the JSON: {json.dumps(picked)}\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences.\n"
            f"- The phrase {json.dumps(picked)} must appear verbatim once.\n"
            "- Do NOT mention other employment types from the list."
        )

    elif ftype == "number":
        locked_value = forced_value
        return_line  = '"value": <integer>'
        value_hint   = (
            "Generate a realistic integer between 1 and 10 for this field.\n"
            "Embed it clearly in 2-3 natural sentences. Do not include other numbers "
            "that could be confused with the value for this field."
        )

    elif ftype == "date":
        locked_value = forced_value
        return_line  = '"value": "YYYY-MM-DD"'
        value_hint   = (
            "Generate a single calendar date as ISO YYYY-MM-DD (e.g. 2026-06-01).\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences.\n"
            "- The exact date string (YYYY-MM-DD) must appear once in the snippet.\n"
            "- Do NOT add other dates that could be confused with this one."
        )

    elif ftype in ("datetime", "date_time"):
        locked_value = forced_value
        return_line  = '"value": "YYYY-MM-DDTHH:MM"'
        value_hint   = (
            "Generate a single local date-time as YYYY-MM-DDTHH:MM (24-hour, no timezone suffix).\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences.\n"
            "- The exact string must appear once in the snippet.\n"
            "- Do NOT add a second date-time string."
        )

    elif ftype == "checkbox":
        locked_value = forced_value if forced_value is not None else random.choice(["1", "0"])
        return_line  = f'"value": "{locked_value}"'
        value_hint   = (
            f"The value must be exactly \"{locked_value}\" (use 1 for yes/checked, 0 for no/unchecked).\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences.\n"
            f"- The value \"{locked_value}\" must appear verbatim in the JSON response.\n"
            "- Do NOT also state the opposite."
        )

    elif ftype in ("phone", "phonenumber"):
        locked_value = forced_value
        return_line  = '"value": "+1 415 555 0199"'
        value_hint   = (
            "Generate one plausible phone number with country code (e.g. +1 415 555 0199).\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences.\n"
            "- The full number must appear verbatim once.\n"
            "- Do NOT include a second phone number."
        )

    elif ftype == "email":
        locked_value = forced_value
        return_line  = '"value": "name@example.com"'
        value_hint   = (
            "Generate one plausible secondary work email (not a common placeholder like test@test.com).\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences.\n"
            "- The email address must appear verbatim once.\n"
            "- Do NOT include a second email."
        )

    elif ftype in ("social", "social_profile"):
        locked_value = forced_value
        return_line  = '"value": "https://example.com/portfolio"'
        value_hint   = (
            "Generate one https URL for a portfolio or personal project site.\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences.\n"
            "- The full URL must appear verbatim once.\n"
            "- Do NOT include a second URL."
        )

    else:
        locked_value = forced_value
        return_line  = '"value": "<verbatim string>"'
        value_hint   = (
            "Generate a realistic, specific value for this field "
            "(see field description for the required format).\n\n"
            "SNIPPET RULES:\n"
            "- Write 2-3 natural resume sentences that clearly state the value.\n"
            "- The exact verbatim value must appear once in the snippet.\n"
            "- Do NOT include alternative phrases or misleading information."
        )

    prompt = f"""You are writing resume content for a candidate-parsing test.

Field Name: {fname}
Field Description: {fdesc}
{value_hint}

Return ONLY this JSON object — no markdown, no explanation, no trailing text:
{{
  "snippet": "<natural resume prose>",
  {return_line}
}}"""
    return prompt, locked_value


def _call_groq(prompt: str) -> dict:
    for attempt in range(3):
        try:
            resp    = _groq.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system",  "content": "You are an expert resume writer. Reply with valid JSON only. Never truncate your response."},
                    {"role": "user",    "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4000,
            )
            content = resp.choices[0].message.content.strip()

            if not content:
                raise ValueError("Empty response from model")

            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"No JSON block in: {content[:200]}")
        except Exception as exc:
            wait = 2 ** attempt
            log.warning(f"[Groq] attempt {attempt+1} failed: {exc}. retry in {wait}s")
            time.sleep(wait)
    return {}


def _value_in_snippet(snippet: str, value, field_type: str = "text") -> bool:
    """
    Check that the value is represented in the snippet.

    multiselect : every option string must appear verbatim (word-for-word).
    number      : the digit must appear somewhere in the snippet.
    text        : ALL significant words (≥4 chars) from the value must appear
                  in the snippet — relaxed to handle minor rephrasing without
                  losing meaningful signal.
    """
    if not snippet or not value:
        return False
    s = snippet.lower()

    if field_type == "multiselect":
        return all(str(v).lower() in s for v in value)

    if field_type == "number":
        return str(value) in s

    val_str   = str(value).lower()
    if val_str in s:
        return True
    sig_words = [w for w in re.findall(r'\w+', val_str) if len(w) >= 4]
    if not sig_words:
        return val_str in s
    return all(w in s for w in sig_words)


def llm_generate_snippet(field: dict, pre_existing: list = None) -> dict:
    """
    One LLM call per field.
    For multiselect: merges pre-existing option values with newly sampled ones.
    Validates snippet contains the value; regenerates once if not.
    Returns: {"snippet": str, "value": str|int|list}
    """
    forced = None
    ftype  = field["type"]
    if ftype == "multiselect" and pre_existing:
        extra    = [o for o in field.get("options", []) if o not in pre_existing]
        new_pick = random.sample(extra, k=min(2, len(extra))) if extra else []
        forced   = sorted(set(pre_existing + new_pick))
    elif ftype == "dropdown" and pre_existing:
        forced = pre_existing[0]

    for attempt in range(2):
        prompt, locked = _build_prompt(field, forced_value=forced)
        result = _call_groq(prompt)
        if not result:
            break

        snippet = result.get("snippet", "")
        value   = locked if locked is not None else result.get("value", "")

        if snippet and _value_in_snippet(snippet, value, field["type"]):
            return {"snippet": snippet, "value": value}

        log.warning(f"[Snippet validation] value not in snippet for '{field['name']}', attempt {attempt+1}")
        forced = value

    return {"snippet": result.get("snippet", ""), "value": locked or result.get("value", "")}

def generate_ground_truth(baseline: list, custom_fields: list) -> list:
    valid   = [r for r in baseline if "error" not in r]
    total   = len(valid)
    print(f"\n[LLM] Generating ground truth: {total} resumes × {len(custom_fields)} fields ...")
    all_gts = []

    for idx, record in enumerate(valid, start=1):
        rid         = record["resume_id"]
        resume_text = record.get("resume_text", "")
        print(f"  [{idx}/{total}] {rid} ...", end=" ", flush=True)

        injected = []
        for field in custom_fields:
            pre_existing = pre_scan_existing_values(resume_text, field)
            result       = llm_generate_snippet(field, pre_existing)
            snippet = result.get("snippet", "")
            value   = result.get("value", "")
            if field["type"] == "checkbox":
                v = str(value).strip()
                value = "1" if v in {"1", "true", "yes"} else "0"

            if snippet:
                injected.append({
                    "field_id":     field["field_id"],
                    "field_name":   field["name"],
                    "field_type":   field["type"],
                    "column_index": field["column_index"],
                    "value":        value,
                    "snippet":      snippet,
                })

        all_gts.append({"resume_id": rid, "injected_fields": injected})
        print(f"{len(injected)}/{len(custom_fields)} snippets OK")
        time.sleep(0.3)

    GROUND_TRUTH_FILE.write_text(
        json.dumps(all_gts, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[LLM] Ground truth saved → {GROUND_TRUTH_FILE}")
    return all_gts

def inject_at_random_position(resume_text: str, block: str) -> str:
    """
    Splits on double-newline paragraph boundaries and inserts the snippet block
    at a random position, avoiding the first paragraph (name/header block) to
    prevent confusing the parser's identity extraction.
    """
    if not block:
        return resume_text

    paragraphs = resume_text.split("\n\n")
    if len(paragraphs) <= 2:
        return resume_text + "\n\n" + block

    insert_at = random.randint(1, len(paragraphs))
    paragraphs.insert(insert_at, block)
    return "\n\n".join(paragraphs)


def inject_snippets_individually(resume_text: str, injected_fields: list) -> str:
    """
    Insert each custom-field snippet independently at a different random
    paragraph position, simulating realistic resume distribution where
    relevant information is spread across the document rather than clustered.

    Process:
    - Shuffle snippet order so insertion positions are not correlated with
      field order.
    - Call inject_at_random_position once per snippet, threading the result
      of each call into the next so positions are drawn from the
      ever-growing paragraph list.
    """
    if not injected_fields:
        return resume_text

    snippets = [f["snippet"].strip() for f in injected_fields if f.get("snippet")]
    if not snippets:
        return resume_text

    random.shuffle(snippets)
    text = resume_text
    for snippet in snippets:
        text = inject_at_random_position(text, snippet)
    return text


def build_injection_block(injected_fields: list) -> str:
    """
    Kept for backward-compatibility. Not used by run_track_b anymore.
    Combine snippets into one unstructured prose block — no section headers,
    no labels.
    """
    if not injected_fields:
        return ""
    return " ".join(f["snippet"].strip() for f in injected_fields if f.get("snippet"))

def run_track_b(token: str, baseline: list, ground_truth_all: list) -> list:
    gt_by_id = {g["resume_id"]: g for g in ground_truth_all}
    valid    = [r for r in baseline if "error" not in r]
    results  = []

    print(f"\n[Track B / SS] Re-parsing {len(valid)} resumes ...")
    track_b_raw_snapshots = []
    for record in valid:
        rid         = record["resume_id"]
        resume_text = record.get("resume_text", "")
        injected    = gt_by_id.get(rid, {}).get("injected_fields", [])

        block         = build_injection_block(injected)
        modified_text = inject_snippets_individually(resume_text, injected)
        pdf_name      = f"{rid}_modified.pdf"

        print(f"  [{rid}] converting to PDF, uploading + parsing ...", end=" ", flush=True)
        try:
            pdf_bytes      = text_to_pdf_bytes(modified_text)
            local_pdf_path = INJECTED_PDF_DIR / pdf_name
            local_pdf_path.write_bytes(pdf_bytes)
            presign        = get_presigned_url(BASE_URL_SS, pdf_name, token)
            upload_pdf_bytes(presign["preSignedUrl"], pdf_bytes)
            parse_result   = call_parse_api(BASE_URL_SS, token, presign["key"], pdf_name)
            _enrich_with_history(BASE_URL_SS, token, parse_result)

            data_block = parse_result.get("data") or {}
            track_b_raw_snapshots.append({
                "resume_id":       rid,
                "pdf_filename":    pdf_name,
                "candidate":       copy.deepcopy(data_block.get("candidate")),
                "custom_fields":   copy.deepcopy(data_block.get("custom_fields")),
                "work_history":    copy.deepcopy(data_block.get("work_history")),
                "education_history": copy.deepcopy(data_block.get("education_history")),
            })

            std_fields_b = extract_standard_fields(parse_result)

            col_indices  = [inj["column_index"] for inj in injected]
            parsed_custom = extract_custom_fields(parse_result, col_indices)

            results.append({
                "resume_id":            rid,
                "modified_resume_text": modified_text,
                "standard_fields":      std_fields_b,
                "parsed_custom_fields": parsed_custom,
                "injected_fields":      injected,
            })
            print("OK")
            log.info(f"[Track B] {rid} OK")
        except Exception as exc:
            print(f"ERROR - {exc}")
            log.error(f"[Track B] {rid} failed: {exc}")
            results.append({"resume_id": rid, "error": str(exc)})
        time.sleep(1)

    TRACK_B_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Track B] Results saved → {TRACK_B_FILE}")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    TRACK_B_RESPONSE_LOG.write_text(
        json.dumps(track_b_raw_snapshots, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[Track B] Full parse payloads logged → {TRACK_B_RESPONSE_LOG}")
    log.info(f"[Track B] Wrote {len(track_b_raw_snapshots)} snapshot(s) to {TRACK_B_RESPONSE_LOG}")

    return results

def classify_custom(gt_val, parsed_val, field_type: str) -> tuple:
    """
    Returns (classification, details).
    custcolumn{N} is null/empty when parser found nothing.
    """
    gt_empty  = is_empty(gt_val)
    prs_empty = is_empty(parsed_val)

    if gt_empty and prs_empty:
        return "Both Empty", ""

    if not gt_empty and prs_empty:
        return "Missed", f"Expected='{gt_val}'"

    if gt_empty and not prs_empty:
        return "Hallucination", f"Parsed='{parsed_val}' but GT is empty"

    if field_type == "number":
        gt_n  = normalize_numeric(gt_val)
        prs_n = normalize_numeric(parsed_val)
        if gt_n == prs_n:
            return "Exact Match", ""
        return "Mismatch", f"GT='{gt_n}', Parsed='{prs_n}'"

    if field_type == "multiselect":
        gt_set  = set(normalize_option_list(gt_val))
        prs_set = set(normalize_option_list(parsed_val))
        if gt_set == prs_set:
            return "Exact Match", ""
        overlap = gt_set & prs_set
        if overlap:
            missing = gt_set - prs_set
            extra   = prs_set - gt_set
            base_detail = (f"matched={len(overlap)}, "
                           f"missing={sorted(missing)}, extra={sorted(extra)}")
            if len(prs_set) > len(gt_set):
                return "Partial Match (-ve)", base_detail
            return "Partial Match (+ve)", base_detail
        return "Hallucination", f"GT={sorted(gt_set)}, Parsed={sorted(prs_set)}"

    if field_type == "dropdown":
        gt_n  = normalize_option(gt_val)
        prs_n = normalize_option(parsed_val)
        if gt_n == prs_n:
            return "Exact Match", ""
        if gt_n in prs_n or prs_n in gt_n:
            return "Partial Match", f"GT='{gt_n}', Parsed='{prs_n}'"
        return "Hallucination", f"GT='{gt_n}', Parsed='{prs_n}'"

    if field_type == "checkbox":
        gt_n  = normalize_checkbox(gt_val)
        prs_n = normalize_checkbox(parsed_val)
        if gt_n == prs_n:
            return "Exact Match", ""
        return "Mismatch", f"GT='{gt_n}', Parsed='{prs_n}'"

    gt_n  = normalize_val(gt_val)
    prs_n = normalize_val(parsed_val)
    if gt_n == prs_n:
        return "Exact Match", ""
    if gt_n in prs_n or prs_n in gt_n:
        return "Partial Match", f"GT='{gt_n}', Parsed='{prs_n}'"
    return "Hallucination", f"GT='{gt_n}', Parsed='{prs_n}'"


def classify_standard(ta_val, tb_val, field_name: str) -> tuple:
    """
    Relaxed regression: uses partial/contains match to avoid false regressions
    from minor text re-ordering caused by injecting unstructured snippet text.
    """
    a = normalize_val(ta_val)
    b = normalize_val(tb_val)

    if a == b:
        return "Match", ""
    if not b and a:
        return "Missing in B", f"A='{a}'"
    if a and b and (a in b or b in a):
        return "Partial Match", f"A='{a}', B='{b}'"
    if a and b:
        return "Regression", f"A='{a}' → B='{b}'"
    return "Other", f"A='{a}', B='{b}'"

EXCEL_MAX_CELL_CHARS = 32767


def _cell_preview(val, max_len: int = EXCEL_MAX_CELL_CHARS) -> str:
    """Serialize a value for an Excel cell (lists/dicts as JSON). Truncates only
    if longer than max_len (Excel’s per-cell cap)."""
    if val is None:
        return ""
    if isinstance(val, (list, dict)):
        s = json.dumps(val, ensure_ascii=False)
    else:
        s = str(val)
    if len(s) <= max_len:
        return s
    return s[: max_len - 50] + "…[truncated: Excel 32767 char cell limit]"


def _rca_extra_values(extra: set, field_options: list, modified_resume_text: str) -> dict:
    """
    For each extra value in a Partial Match (-ve) row, determine:
      - is_in_options   : value matches a field option after normalize_val.
      - is_in_resume    : value appears in the modified resume text using
                          word-boundary matching (not bare substring) to avoid
                          false positives where a short label matches inside
                          a longer word.
      - extra_classification per value:
            not in options, not in resume → "Strong Hallucination"
            in options, not in resume     → "Weak Hallucination"
            in options, in resume         → "Misclassification"
            else                          → "Unknown"
    Returns dict with the four new column values (all as plain strings for Excel).
    """
    if not field_options:
        log.warning("[RCA] opts_by_col empty for this field — "
                    "in_options will always be No. "
                    "Ensure custom_fields manifest is passed to run_validation().")

    opts_norm   = {normalize_val(o) for o in (field_options or []) if not is_empty(o)}
    resume_norm = normalize_val(modified_resume_text or "")

    in_opts_list    = []
    in_resume_list  = []
    cls_list        = []

    for v in sorted(extra):
        vn = normalize_val(v) if v else ""

        in_opts = bool(vn) and vn in opts_norm

        in_resume = bool(vn) and bool(
            re.search(r"(?<!\w)" + re.escape(vn) + r"(?!\w)", resume_norm)
        )

        in_opts_list.append(in_opts)
        in_resume_list.append(in_resume)

        if not in_opts and not in_resume:
            cls_list.append("Strong Hallucination")
        elif in_opts and not in_resume:
            cls_list.append("Weak Hallucination")
        elif in_opts and in_resume:
            cls_list.append("Misclassification")
        else:
            cls_list.append("Unknown")

    def _fmt(items):
        pairs = []
        for i, v in enumerate(sorted(extra)):
            tag = "Yes" if items[i] else "No"
            pairs.append(f"{v}: {tag}")
        return "; ".join(pairs)

    return {
        "extra_values":          ", ".join(sorted(extra)),
        "extra_in_options":      _fmt(in_opts_list),
        "extra_in_resume":       _fmt(in_resume_list),
        "extra_classification":  "; ".join(
            f"{v}: {c}" for v, c in zip(sorted(extra), cls_list)
        ),
    }


def run_validation(baseline: list, track_b_results: list,
                   custom_fields: list = None) -> pd.DataFrame:
    a_by_id = {r["resume_id"]: r for r in baseline if "error" not in r}
    opts_by_col = {}
    if custom_fields:
        for cf in custom_fields:
            opts_by_col[cf["column_index"]] = cf.get("options", [])
    rows    = []

    for tb in track_b_results:
        rid      = tb.get("resume_id")
        filename = a_by_id.get(rid, {}).get("filename", "")
        if "error" in tb:
            rows.append({
                "resume_id": rid, "filename": filename,
                "type": "ERROR", "field": "-",
                "track_a_value": "", "track_b_value": "",
                "ground_truth": "", "parsed_value": "",
                "classification": "ERROR", "details": tb["error"],
                "extra_values": "", "extra_in_options": "",
                "extra_in_resume": "", "extra_classification": "",
            })
            continue

        ta     = a_by_id.get(rid, {})
        ta_std = ta.get("standard_fields", {})
        tb_std = tb.get("standard_fields", {})

        for fname, ta_val in ta_std.items():
            tb_val = tb_std.get(fname)
            if fname in ("workhistory", "educationhistory"):
                ta_list = ta_val if isinstance(ta_val, list) else []
                tb_list = tb_val if isinstance(tb_val, list) else []
                classification, details = classify_history_comparator_style(
                    ta_val, tb_val, fname
                )
                if fname == "workhistory":
                    flat_a = flatten_work_history_list(ta_list)
                    flat_b = flatten_work_history_list(tb_list)
                else:
                    flat_a = flatten_education_history_list(ta_list)
                    flat_b = flatten_education_history_list(tb_list)
                a_prev = _cell_preview(flat_a) if flat_a else ""
                b_prev = _cell_preview(flat_b) if flat_b else ""
            else:
                if tb_val is None:
                    tb_val = ""
                classification, details = classify_standard(ta_val, tb_val, fname)
                a_prev = _cell_preview(ta_val)
                b_prev = _cell_preview(tb_val)
            rows.append({
                "resume_id":      rid,
                "filename":       filename,
                "type":           "Standard",
                "field":          fname,
                "track_a_value":  a_prev,
                "track_b_value":  b_prev,
                "ground_truth":   "",
                "parsed_value":   "",
                "classification": classification,
                "details":        details,
                "extra_values":          "",
                "extra_in_options":      "",
                "extra_in_resume":       "",
                "extra_classification":  "",
            })

        parsed_custom   = tb.get("parsed_custom_fields", {})
        injected_fields = tb.get("injected_fields", [])

        modified_resume_text = tb.get("modified_resume_text", "")

        for inj in injected_fields:
            col_idx    = inj["column_index"]
            fname      = inj["field_name"]
            ftype      = inj["field_type"]
            gt_val     = inj["value"]
            parsed_val = parsed_custom.get(col_idx)

            classification, details = classify_custom(gt_val, parsed_val, ftype)

            rca = {"extra_values": "", "extra_in_options": "",
                   "extra_in_resume": "", "extra_classification": ""}
            if classification == "Partial Match (-ve)" and ftype == "multiselect":
                gt_set  = set(normalize_option_list(gt_val))
                prs_set = set(normalize_option_list(parsed_val))
                extra   = prs_set - gt_set
                if extra:
                    field_opts = opts_by_col.get(col_idx, [])
                    rca = _rca_extra_values(extra, field_opts, modified_resume_text)

            rows.append({
                "resume_id":      rid,
                "filename":       filename,
                "type":           "Custom",
                "field":          f"{fname} (custcolumn{col_idx})",
                "track_a_value":  "",
                "track_b_value":  "",
                "ground_truth":   _cell_preview(gt_val),
                "parsed_value":   _cell_preview(parsed_val),
                "classification": classification,
                "details":        details,
                **rca,
            })

    return pd.DataFrame(rows)

_ILLEGAL_XML_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f]"
)


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Strip XML-illegal control characters from every string cell so openpyxl
    doesn't raise IllegalCharacterError."""
    def _clean(val):
        if isinstance(val, str):
            return _ILLEGAL_XML_RE.sub("", val)
        return val
    return df.apply(lambda col: col.map(_clean))


def build_report(df: pd.DataFrame) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import (Font, PatternFill, Alignment, Border, Side,
                                  GradientFill)
    from openpyxl.utils import get_column_letter

    CLS_FILL = {
        "Match":                "D6EFD8",
        "Exact Match":          "D6EFD8",
        "Partial Match":        "FFF3CD",
        "Partial Match (+ve)":  "FFF3CD",
        "Partial Match (-ve)":  "F4CCCC",
        "Regression":           "FADADD",
        "Missing in B":         "FADADD",
        "Mismatch":             "FADADD",
        "Missed":               "FADADD",
        "Hallucination":        "F4CCCC",
        "Both Empty":           "E8E8E8",
        "ERROR":                "F4CCCC",
    }

    HEADER_FILL    = PatternFill("solid", fgColor="1F4E79")
    HEADER_FONT    = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
    DATA_FONT      = Font(size=10, name="Calibri")
    DATA_FONT_BOLD = Font(size=10, bold=True, name="Calibri")
    RESUME_FILL_A  = PatternFill("solid", fgColor="EBF3FB")
    RESUME_FILL_B  = PatternFill("solid", fgColor="FFFFFF")
    THIN           = Side(border_style="thin", color="C0C0C0")
    CELL_BORDER    = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    COLS           = ["resume_id", "filename", "type", "field",
                      "track_a_value", "track_b_value",
                      "ground_truth", "parsed_value",
                      "classification", "details",
                      "extra_values", "extra_in_options",
                      "extra_in_resume", "extra_classification"]
    COL_WIDTHS     = [12, 22, 12, 32, 52, 52, 32, 32, 22, 58,
                      30, 30, 30, 28]

    RCA_COLS = {"extra_values", "extra_in_options", "extra_in_resume", "extra_classification"}
    RCA_HEADER_FILL = PatternFill("solid", fgColor="6C3483")

    safe_df = _sanitize_df(df)

    wb = Workbook()

    ws = wb.active
    ws.title = "Full Detail"
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False

    for col_idx, (col_name, width) in enumerate(zip(COLS, COL_WIDTHS), start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = RCA_HEADER_FILL if col_name in RCA_COLS else HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = CELL_BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 30

    sub_df = safe_df.reindex(columns=COLS).fillna("")
    resume_ids    = sub_df["resume_id"].unique() if not sub_df.empty else []
    current_row   = 2
    fill_toggle   = True

    for rid in resume_ids:
        block = sub_df[sub_df["resume_id"] == rid]
        band  = RESUME_FILL_A if fill_toggle else RESUME_FILL_B
        fill_toggle = not fill_toggle

        for _, data_row in block.iterrows():
            cls        = data_row.get("classification", "")
            cls_fill   = PatternFill("solid", fgColor=CLS_FILL.get(cls, "FFFFFF"))
            for col_idx, col_name in enumerate(COLS, start=1):
                val  = data_row.get(col_name, "")
                cell = ws.cell(row=current_row, column=col_idx,
                               value="" if val is None else val)
                cell.border    = CELL_BORDER
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.font      = DATA_FONT
                if col_name == "classification":
                    cell.fill = cls_fill
                    cell.font = DATA_FONT_BOLD
                elif col_name in RCA_COLS:
                    cell.fill = PatternFill("solid", fgColor="F5EEF8")
                else:
                    cell.fill = band
            ws.row_dimensions[current_row].height = 18
            current_row += 1

        current_row += 2

    SUM_HEADER_FONT  = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
    SUM_DATA_FONT    = Font(size=10, name="Calibri")
    SUM_DATA_FONT_B  = Font(size=10, bold=True, name="Calibri")

    SUM_COL_FILLS = {
        "(-ve)": "C0392B",
        "(+ve)": "E67E22",
        "Pass %": "27AE60",
        "Regression": "E74C3C",
        "Regression %": "E74C3C",
        "Hallucination": "C0392B",
        "Hallucination %": "C0392B",
        "Missed": "E67E22",
        "Missed %": "E67E22",
        "Mismatch": "E74C3C",
        "Mismatch %": "E74C3C",
        "Exact Match": "1E8449",
        "Exact Match %": "1E8449",
        "Match": "1E8449",
        "Pass %": "1E8449",
    }
    SUM_DEFAULT_FILL = "2C3E50"

    def _add_summary_sheet(ws_name: str, data: pd.DataFrame) -> None:
        if data.empty:
            return
        ws_s = wb.create_sheet(title=ws_name)
        ws_s.sheet_view.showGridLines = False
        cols = list(data.columns)
        ws_s.append(cols)

        for i, cell in enumerate(ws_s[1]):
            col_name = cols[i] if i < len(cols) else ""
            fill_hex = SUM_DEFAULT_FILL
            for key, hex_val in SUM_COL_FILLS.items():
                if key in col_name:
                    fill_hex = hex_val
                    break
            cell.font      = SUM_HEADER_FONT
            cell.fill      = PatternFill("solid", fgColor=fill_hex)
            cell.border    = CELL_BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws_s.row_dimensions[1].height = 30

        ALT_A = PatternFill("solid", fgColor="F2F2F2")
        ALT_B = PatternFill("solid", fgColor="FFFFFF")
        for r_idx, row in enumerate(data.itertuples(index=False), start=2):
            row_fill = ALT_A if r_idx % 2 == 0 else ALT_B
            for c_idx, col_name in enumerate(cols, start=1):
                val  = getattr(row, col_name.replace(" ", "_").replace("(", "").replace(")", "").replace("%", "pct").replace("+", "plus").replace("-", "minus"), "")
                val  = row[c_idx - 1]
                cell = ws_s.cell(row=r_idx, column=c_idx, value=val)
                cell.border    = CELL_BORDER
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
                    cell.font = SUM_DATA_FONT_B
                else:
                    cell.fill = row_fill
                    cell.font = SUM_DATA_FONT
            ws_s.row_dimensions[r_idx].height = 20

        for c_idx, col_name in enumerate(cols, start=1):
            if c_idx == 1:
                ws_s.column_dimensions[get_column_letter(c_idx)].width = 42
            elif col_name.endswith("%"):
                ws_s.column_dimensions[get_column_letter(c_idx)].width = 14
            else:
                ws_s.column_dimensions[get_column_letter(c_idx)].width = 18

    custom_df = safe_df[safe_df["type"] == "Custom"]
    if not custom_df.empty:
        pivot = (
            custom_df.groupby(["field", "classification"])
            .size().unstack(fill_value=0).reset_index()
        )
        totals = custom_df.groupby("field").size().rename("Total")
        pivot  = pivot.merge(totals, on="field")
        for cls in ["Exact Match", "Partial Match", "Partial Match (+ve)",
                    "Partial Match (-ve)", "Missed", "Hallucination",
                    "Mismatch", "Both Empty"]:
            if cls not in pivot.columns:
                pivot[cls] = 0
            pivot[f"{cls} %"] = (pivot[cls] / pivot["Total"] * 100).round(1)
        for col in ["Partial Match (+ve)", "Partial Match (+ve) %",
                    "Partial Match (-ve)", "Partial Match (-ve) %"]:
            if col not in pivot.columns:
                pivot[col] = 0
        _add_summary_sheet("Custom Field Summary", pivot)

    std_df = safe_df[safe_df["type"] == "Standard"]
    if not std_df.empty:
        pivot_std = (
            std_df.groupby(["field", "classification"])
            .size().unstack(fill_value=0).reset_index()
        )
        totals_std = std_df.groupby("field").size().rename("Total")
        pivot_std  = pivot_std.merge(totals_std, on="field")
        for cls in ["Match", "Partial Match", "Regression", "Missing in B", "Other"]:
            if cls not in pivot_std.columns:
                pivot_std[cls] = 0
            pivot_std[f"{cls} %"] = (pivot_std[cls] / pivot_std["Total"] * 100).round(1)
        pivot_std = pivot_std.rename(columns={"Match %": "Pass %"})
        desired_order = ["field", "Total",
                         "Match", "Pass %",
                         "Partial Match", "Partial Match %",
                         "Regression", "Regression %",
                         "Missing in B", "Missing in B %",
                         "Other", "Other %"]
        ordered_cols = [c for c in desired_order if c in pivot_std.columns] + \
                       [c for c in pivot_std.columns if c not in desired_order]
        pivot_std = pivot_std[ordered_cols]
        _add_summary_sheet("Standard Fields Summary", pivot_std)

    regressions = (std_df[std_df["classification"] == "Regression"]
                   if not std_df.empty else pd.DataFrame())
    _add_summary_sheet("Regressions Only", regressions)

    wb.save(REPORT_FILE)
    print(f"\n[Report] Saved → {REPORT_FILE}")


def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 65)
    print("VALIDATION SUMMARY")
    print("=" * 65)

    custom_df = df[df["type"] == "Custom"]
    if not custom_df.empty:
        total_c = len(custom_df)
        print(f"\n── Custom Fields  (n={total_c} field-resume pairs) ──")
        for cls in ["Exact Match", "Partial Match (+ve)", "Partial Match (-ve)",
                    "Partial Match", "Missed", "Hallucination", "Both Empty"]:
            count = (custom_df["classification"] == cls).sum()
            if count:
                print(f"  {cls:<32s} {count:4d}  ({count / total_c * 100:5.1f}%)")

        print("\n  Per-field pass rate (Exact Match %):")
        per_field = (custom_df.groupby("field")
                     .apply(lambda g: round((g["classification"] == "Exact Match").sum() / len(g) * 100, 1),
                            include_groups=False)
                     .reset_index(name="Exact Match %"))
        for _, row in per_field.iterrows():
            print(f"    {row['field']:<50s} {row['Exact Match %']:5.1f}%")

    std_df = df[df["type"] == "Standard"]
    if not std_df.empty:
        total_s     = len(std_df)
        regressions = (std_df["classification"] == "Regression").sum()
        missing_b   = (std_df["classification"] == "Missing in B").sum()
        matches     = (std_df["classification"] == "Match").sum()
        partials    = (std_df["classification"] == "Partial Match").sum()
        print(f"\n── Standard Fields  (n={total_s} field-resume pairs) ──")
        print(f"  {'Pass (Match)':<28s} {matches:4d}  ({matches / total_s * 100:5.1f}%)")
        print(f"  {'Partial Match':<28s} {partials:4d}  ({partials / total_s * 100:5.1f}%)")
        print(f"  {'Regression':<28s} {regressions:4d}  ({regressions / total_s * 100:5.1f}%)")
        print(f"  {'Missing in B':<28s} {missing_b:4d}  ({missing_b / total_s * 100:5.1f}%)")
        print(f"\n  Per-field counts (Pass / Partial / Regression / Missing):")
        for fname, grp in std_df.groupby("field"):
            n   = len(grp)
            p   = (grp["classification"] == "Match").sum()
            pm  = (grp["classification"] == "Partial Match").sum()
            r   = (grp["classification"] == "Regression").sum()
            mb  = (grp["classification"] == "Missing in B").sum()
            print(f"    {fname:<30s}  Pass={p:3d}  Partial={pm:3d}  Regression={r:3d}  Missing={mb:3d}  (n={n})")

    print("=" * 65)


def main():
    print("=" * 65)
    print("Custom Field Parsing Automation Pipeline  (v2)")
    print("=" * 65)

    print("\n[Auth] Fetching tokens ...")
    track_a_token = get_token(BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A)
    ss_token      = get_token(BASE_URL_SS, EMAIL_SS, PASSWORD_SS)
    print("  Track A token OK | SS token OK")

    if BASELINE_FILE.exists():
        print(f"[Track A] Loading cached baseline → {BASELINE_FILE}")
        baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    else:
        baseline = run_track_a(track_a_token)
    valid_count = sum(1 for r in baseline if "error" not in r)
    print(f"[Track A] {valid_count} / {len(baseline)} resumes OK")

    try:
        custom_fields = load_custom_fields_from_manifest()
    except FileNotFoundError as e:
        print(f"\n[Setup] {e}")
        return
    except ValueError as e:
        print(f"\n[Setup] Invalid manifest: {e}")
        return
    print(f"[Setup] Loaded {len(custom_fields)} custom field(s) from {CUSTOM_FIELDS_MANIFEST}")

    if GROUND_TRUTH_FILE.exists():
        print(f"[LLM] Loading cached ground truth → {GROUND_TRUTH_FILE}")
        ground_truth_all = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    else:
        ground_truth_all = generate_ground_truth(baseline, custom_fields)

    if TRACK_B_FILE.exists():
        print(f"[Track B] Loading cached results → {TRACK_B_FILE}")
        track_b_results = json.loads(TRACK_B_FILE.read_text(encoding="utf-8"))
    else:
        track_b_results = run_track_b(ss_token, baseline, ground_truth_all)

    print("\n[Validation] Running comparison ...")
    df = run_validation(baseline, track_b_results, custom_fields=custom_fields)
    build_report(df)
    print_summary(df)


if __name__ == "__main__":
    main()

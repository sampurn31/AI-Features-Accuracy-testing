#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nested Custom Fields — Phase 1 Testing Pipeline (v3 — controlled generation)
------------------------------------------------------
10 fields, column_index 1–10 (sequential, custcolumn1 … custcolumn10).

Field layout:
  Parent A   col 1  Willing to Travel          (checkbox)
  Parent B   col 2  Work Mode Preference        (dropdown:  Onsite / Remote / Hybrid)
  Parent C   col 3  Technical Skill Focus       (multiselect: Frontend / Backend / Data)
  Child→A    col 4  Travel Radius               (text)
                      visible when 1=true
  Child→B    col 5  Office Setup Type           (dropdown: Dedicated room/Shared space/Co-working space)
                      visible when 2=Remote → options: Dedicated room, Shared space
                      visible when 2=Hybrid → options: Shared space, Co-working space
                      hidden  when 2=Onsite
  Child→B    col 6  Onsite Location             (text)
                      visible when 2=Onsite
                      hidden  when 2=Remote or Hybrid
  Child→C    col 7  Backend Stack               (multiselect: Django/Spring Boot/FastAPI/Express)
                      visible when 3∋Backend → all 4 options valid
                      visible when 3∋Data    → only Django, FastAPI valid (option filter)
                      hidden  when 3∋Frontend only
  Child→C    col 8  Data Tool Preference        (text)
                      visible when 3∋Data
                      hidden  otherwise
  Independent col 9  Notice Period              (number)
  Independent col 10 Preferred Contract Rate    (text)

Key features (v3):
  • 26 COVERAGE_SCENARIOS: every parent option × valid/invalid/missing_parent
  • Round-robin scenario assignment across resumes (guaranteed full coverage)
  • generate_controlled_snippet() drives the LLM with explicit scenario_type prompts
  • _log_coverage() prints per-scenario, per-type, and per-parent-option counts
  • scenario_type + expected_outcome columns in validation output + Excel report
  • Sheet 4: Coverage Summary (per-scenario EM / DepViol / Missed stats)

Usage:
    python3 nested_custom_field_pipeline.py --setup    # create fields + deps (run once)
    python3 nested_custom_field_pipeline.py            # GT → parse → validate
    python3 nested_custom_field_pipeline.py --reparse  # force re-parse / re-GT
"""

import sys, os, json, time, re, logging, requests, uuid
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd

from custom_field_pipeline import (
    get_token,
    _auth_headers,
    text_to_pdf_bytes,
    call_parse_api,
    get_presigned_url,
    upload_pdf_bytes,
    extract_custom_fields,
    inject_snippets_individually,
    pre_scan_existing_values,
    llm_generate_snippet,
    _call_groq,
    normalize_val,
    normalize_list,
    normalize_option,
    normalize_option_list,
    is_empty,
    run_track_a,
    BASELINE_FILE,
    BASE_URL_SS,  EMAIL_SS,  PASSWORD_SS,  ENTITY_TYPE_ID,
    BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A,
)

from config import NESTED_SS_EMAIL as EMAIL_SS, NESTED_SS_PASSWORD as PASSWORD_SS


load_dotenv()

OUTPUT_DIR    = Path("pipeline_output/nested")
INJECTED_PDF_DIR = OUTPUT_DIR / "injected_pdfs"
LOGS_DIR      = Path("logs")
MANIFEST_FILE = OUTPUT_DIR / "nested_fields_manifest.json"
GT_FILE       = OUTPUT_DIR / "nested_ground_truth.json"
RESULTS_FILE  = OUTPUT_DIR / "nested_track_b_results.json"
REPORT_FILE   = OUTPUT_DIR / "nested_validation_report.xlsx"
JSON_OUT_FILE = OUTPUT_DIR / "nested_validation.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INJECTED_PDF_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=str(LOGS_DIR / "nested_pipeline.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

NESTED_FIELDS = [
    {
        "column_index": 1,
        "name":         "Willing to Travel",
        "type":         "checkbox",
        "role":         "parent",
        "description":  "Whether the candidate can commit to regular work-related travel.",
        "options":      [],
    },
    {
        "column_index": 2,
        "name":         "Work Mode Preference",
        "type":         "dropdown",
        "role":         "parent",
        "description":  "The day-to-day working environment the candidate is targeting.",
        "options":      ["Onsite", "Remote", "Hybrid"],
    },
    {
        "column_index": 3,
        "name":         "Technical Skill Focus",
        "type":         "multiselect",
        "role":         "parent",
        "description":  "The engineering areas where the candidate has deepest expertise.",
        "options":      ["Frontend", "Backend", "Data"],
    },
    {
        "column_index": 4,
        "name":         "Travel Radius",
        "type":         "text",
        "role":         "child",
        "parent_col":   1,
        "description":  "How far from their base location the candidate is willing to travel regularly.",
        "options":      [],
    },
    {
        "column_index": 5,
        "name":         "Office Setup Type",
        "type":         "dropdown",
        "role":         "child",
        "parent_col":   2,
        "description":  "The type of working space arrangement the candidate has or uses.",
        "options":      ["Dedicated room", "Shared space", "Co-working space"],
    },
    {
        "column_index": 6,
        "name":         "Onsite Location",
        "type":         "text",
        "role":         "child",
        "parent_col":   2,
        "description":  "The city or office area where the candidate expects to work in person.",
        "options":      [],
    },
    {
        "column_index": 7,
        "name":         "Backend Stack",
        "type":         "multiselect",
        "role":         "child",
        "parent_col":   3,
        "description":  "The server-side frameworks and technologies the candidate works with.",
        "options":      ["Django", "Spring Boot", "FastAPI", "Express"],
    },
    {
        "column_index": 8,
        "name":         "Data Tool Preference",
        "type":         "text",
        "role":         "child",
        "parent_col":   3,
        "description":  "The data engineering or analytics toolset the candidate is most productive with.",
        "options":      [],
    },
    {
        "column_index": 9,
        "name":         "Notice Period",
        "type":         "number",
        "role":         "independent",
        "description":  "How many weeks of notice the candidate must serve before joining.",
        "options":      [],
    },
    {
        "column_index": 10,
        "name":         "Preferred Contract Rate",
        "type":         "text",
        "role":         "independent",
        "description":  "The rate the candidate expects for contract or freelance engagements.",
        "options":      [],
    },
]

DEPENDENCY_RULES: dict = {
    4: {
        "parent_col":          1,
        "parent_type":         "checkbox",
        "child_type":          "text",
        "child_option_filter": {
            "1": "ALL",
            "0": None,
        },
    },
    5: {
        "parent_col":          2,
        "parent_type":         "dropdown",
        "child_type":          "dropdown",
        "child_option_filter": {
            "Remote": frozenset({"Dedicated room", "Shared space"}),
            "Hybrid": frozenset({"Shared space", "Co-working space"}),
            "Onsite": None,
        },
    },
    6: {
        "parent_col":          2,
        "parent_type":         "dropdown",
        "child_type":          "text",
        "child_option_filter": {
            "Onsite": "ALL",
            "Remote": None,
            "Hybrid": None,
        },
    },
    7: {
        "parent_col":          3,
        "parent_type":         "multiselect",
        "child_type":          "multiselect",
        "child_option_filter": {
            "Backend":  frozenset({"Django", "Spring Boot", "FastAPI", "Express"}),
            "Data":     frozenset({"FastAPI", "Django"}),
            "Frontend": None,
        },
    },
    8: {
        "parent_col":          3,
        "parent_type":         "multiselect",
        "child_type":          "text",
        "child_option_filter": {
            "Data":     "ALL",
            "Backend":  None,
            "Frontend": None,
        },
    },
}

COVERAGE_SCENARIOS: list = [
    {"sid": "A-T-COL4-V",    "parent_col": 1, "parent_value": "1",
     "child_col": 4, "scenario_type": "valid",          "forced_child_value": None},
    {"sid": "A-F-COL4-I",    "parent_col": 1, "parent_value": "0",
     "child_col": 4, "scenario_type": "invalid",        "forced_child_value": None},
    {"sid": "MP-COL4",       "parent_col": None, "parent_value": None,
     "child_col": 4, "scenario_type": "missing_parent", "forced_child_value": None},

    {"sid": "B-REM-COL5-V1", "parent_col": 2, "parent_value": "Remote",
     "child_col": 5, "scenario_type": "valid",          "forced_child_value": "Dedicated room"},
    {"sid": "B-REM-COL5-V2", "parent_col": 2, "parent_value": "Remote",
     "child_col": 5, "scenario_type": "valid",          "forced_child_value": "Shared space"},
    {"sid": "B-REM-COL5-I",  "parent_col": 2, "parent_value": "Remote",
     "child_col": 5, "scenario_type": "invalid",        "forced_child_value": "Co-working space"},
    {"sid": "B-HYB-COL5-V",  "parent_col": 2, "parent_value": "Hybrid",
     "child_col": 5, "scenario_type": "valid",          "forced_child_value": "Shared space"},
    {"sid": "B-HYB-COL5-I",  "parent_col": 2, "parent_value": "Hybrid",
     "child_col": 5, "scenario_type": "invalid",        "forced_child_value": "Dedicated room"},
    {"sid": "B-ONS-COL5-I",  "parent_col": 2, "parent_value": "Onsite",
     "child_col": 5, "scenario_type": "invalid",        "forced_child_value": "Shared space"},
    {"sid": "MP-COL5",       "parent_col": None, "parent_value": None,
     "child_col": 5, "scenario_type": "missing_parent", "forced_child_value": "Dedicated room"},

    {"sid": "B-ONS-COL6-V",  "parent_col": 2, "parent_value": "Onsite",
     "child_col": 6, "scenario_type": "valid",          "forced_child_value": None},
    {"sid": "B-REM-COL6-I",  "parent_col": 2, "parent_value": "Remote",
     "child_col": 6, "scenario_type": "invalid",        "forced_child_value": None},
    {"sid": "B-HYB-COL6-I",  "parent_col": 2, "parent_value": "Hybrid",
     "child_col": 6, "scenario_type": "invalid",        "forced_child_value": None},
    {"sid": "MP-COL6",       "parent_col": None, "parent_value": None,
     "child_col": 6, "scenario_type": "missing_parent", "forced_child_value": None},

    {"sid": "C-BKD-COL7-V",  "parent_col": 3, "parent_value": ["Backend"],
     "child_col": 7, "scenario_type": "valid",          "forced_child_value": ["Django", "Spring Boot"]},
    {"sid": "C-DAT-COL7-V",  "parent_col": 3, "parent_value": ["Data"],
     "child_col": 7, "scenario_type": "valid",          "forced_child_value": ["Django", "FastAPI"]},
    {"sid": "C-DAT-COL7-I",  "parent_col": 3, "parent_value": ["Data"],
     "child_col": 7, "scenario_type": "invalid",        "forced_child_value": ["Spring Boot", "Express"]},
    {"sid": "C-FRD-COL7-I",  "parent_col": 3, "parent_value": ["Frontend"],
     "child_col": 7, "scenario_type": "invalid",        "forced_child_value": ["Django"]},
    {"sid": "C-FBK-COL7-V",  "parent_col": 3, "parent_value": ["Frontend", "Backend"],
     "child_col": 7, "scenario_type": "valid",          "forced_child_value": ["Django", "FastAPI"]},
    {"sid": "C-BDT-COL7-V",  "parent_col": 3, "parent_value": ["Backend", "Data"],
     "child_col": 7, "scenario_type": "valid",          "forced_child_value": ["Django", "FastAPI"]},
    {"sid": "MP-COL7",       "parent_col": None, "parent_value": None,
     "child_col": 7, "scenario_type": "missing_parent", "forced_child_value": ["Django"]},

    {"sid": "C-DAT-COL8-V",  "parent_col": 3, "parent_value": ["Data"],
     "child_col": 8, "scenario_type": "valid",          "forced_child_value": None},
    {"sid": "C-BKD-COL8-I",  "parent_col": 3, "parent_value": ["Backend"],
     "child_col": 8, "scenario_type": "invalid",        "forced_child_value": None},
    {"sid": "C-FRD-COL8-I",  "parent_col": 3, "parent_value": ["Frontend"],
     "child_col": 8, "scenario_type": "invalid",        "forced_child_value": None},
    {"sid": "C-FDT-COL8-V",  "parent_col": 3, "parent_value": ["Frontend", "Data"],
     "child_col": 8, "scenario_type": "valid",          "forced_child_value": None},
    {"sid": "MP-COL8",       "parent_col": None, "parent_value": None,
     "child_col": 8, "scenario_type": "missing_parent", "forced_child_value": None},
]

_TRUE_SET  = {"1"}
_FALSE_SET = {"0"}


def normalize_checkbox(val) -> str:
    """
    Normalise a checkbox value to exactly '1' (checked) or '0' (unchecked).
    Accepts: "1" or 1 → '1'
             "0" or 0 → '0'
    Anything else is returned stripped as-is so it surfaces as a Mismatch.
    """
    v = str(val).strip()
    if v in _TRUE_SET:
        return "1"
    if v in _FALSE_SET:
        return "0"
    return v


def parse_multivalue(val) -> list:
    """
    Robustly split a multi-value string into a stripped list.
    Handles comma, slash, and semicolon delimiters.
    Case is preserved exactly — only strip applied.
    Already-list inputs are stripped element-wise.
    """
    if isinstance(val, list):
        return [str(v).strip() for v in val if not is_empty(v)]
    if is_empty(val):
        return []
    parts = re.split(r"[,/;]+", str(val))
    return [p.strip() for p in parts if p.strip()]


def _lookup_filter(filter_dict: dict, norm_key: str):
    """Strip-only lookup in a child_option_filter dict (case-sensitive after strip)."""
    key_stripped = norm_key.strip()
    for k, v in filter_dict.items():
        if str(k).strip() == key_stripped:
            return v
    return None


def _filter_value_to_allowed(val, allowed_opts: frozenset, field_type: str):
    """
    Keep only child values whose stripped form is in allowed_opts.
    Returns filtered value (str or list) or None if nothing remains.
    """
    if is_empty(val):
        return None
    allowed_stripped = {str(o).strip() for o in allowed_opts}

    if field_type == "dropdown":
        return val if str(val).strip() in allowed_stripped else None

    parts = parse_multivalue(val)
    kept  = [p for p in parts if p in allowed_stripped]
    return kept if kept else None


_TEXT_DEFAULTS: dict = {
    4: "50 miles",
    6: "San Francisco, CA",
    8: "Apache Spark and dbt",
}


def _default_text_value(child_col: int) -> str:
    """Return a plausible fallback value for text child fields."""
    return _TEXT_DEFAULTS.get(child_col, "relevant information")


def _get_allowed_disallowed(
    parent_col: int | None,
    parent_value,
    child_col: int,
    manifest: list,
) -> tuple:
    """
    Return (allowed_child_opts, disallowed_child_opts) given the parent value
    and the dependency rule for child_col.
    Works for dropdown and multiselect children; returns ([], []) for text/number.
    """
    rule = DEPENDENCY_RULES.get(child_col)
    child_field = next(
        (f for f in manifest if f["column_index"] == child_col), None
    )
    if not rule or not child_field:
        return [], []

    all_opts    = child_field.get("options", [])
    filter_map  = rule["child_option_filter"]
    parent_type = rule["parent_type"]

    if parent_value is None:
        return [], list(all_opts)

    if parent_type == "checkbox":
        pkeys = [normalize_checkbox(parent_value)]
    elif parent_type == "dropdown":
        pkeys = [str(parent_value).strip()]
    else:
        pkeys = parse_multivalue(parent_value) if isinstance(parent_value, list) else [
            str(parent_value).strip()
        ]

    allowed: set = set()
    has_all      = False
    child_visible = False

    for pk in pkeys:
        mapped = _lookup_filter(filter_map, pk)
        if mapped is None:
            continue
        child_visible = True
        if mapped == "ALL":
            has_all = True
        elif isinstance(mapped, frozenset):
            allowed.update(mapped)

    if not child_visible:
        return [], list(all_opts)

    if has_all:
        return list(all_opts), []

    allowed_list    = [o for o in all_opts if o in allowed]
    disallowed_list = [o for o in all_opts if o not in allowed]
    return allowed_list, disallowed_list


def _api_type(ftype: str) -> str:
    return {
        "text":        "text",
        "longtext":    "longtext",
        "number":      "number",
        "multiselect": "multiselect",
        "dropdown":    "dropdown",
        "date":        "date",
        "datetime":    "datetime",
        "checkbox":    "checkbox",
        "phone":       "phone",
        "email":       "email",
    }.get(ftype, "text")


def create_nested_fields(token: str) -> list:
    """
    POST each field to /v1/custom-fields.
    Returns manifest: field dicts enriched with field_id and option_map {label→server_id}.
    """
    print(f"\n[Setup] Creating {len(NESTED_FIELDS)} custom fields ...")
    created = []
    for field in NESTED_FIELDS:
        opts = [
            {"label": lbl, "sequence_no": i + 1, "tempId": str(uuid.uuid4())}
            for i, lbl in enumerate(field.get("options", []))
        ]
        payload = {
            "custumField": {
                "extrafieldname":      field["name"],
                "extrafieldtype":      _api_type(field["type"]),
                "entitytypeid":        ENTITY_TYPE_ID,
                "columnid":            field["column_index"],
                "defaultvalue":        None,
                "defaultoptionsvalue": opts,
                "description":         field["description"],
                "is_parser_enabled":   1,
            }
        }
        try:
            r = requests.post(
                f"{BASE_URL_SS}/v1/custom-fields",
                headers=_auth_headers(token), json=payload,
            )
            r.raise_for_status()
            data       = r.json()["data"]["custumField"]
            field_id   = data["id"]
            option_map = {o["label"]: o["id"] for o in data.get("defaultoptionsvalue", [])}
            created.append({**field, "field_id": field_id, "option_map": option_map})
            print(f"  OK  '{field['name']}' → id={field_id}, custcolumn{field['column_index']}")
            log.info(f"[Setup] '{field['name']}' id={field_id} custcolumn{field['column_index']}")
        except Exception as exc:
            print(f"  ERR '{field['name']}': {exc}")
            log.error(f"[Setup] '{field['name']}' failed: {exc}")
        time.sleep(0.5)
    return created

def _store_one_dependency(token, entity_id, parent_fid, child_fid, mappings, level=1):
    payload = {
        "entity":        str(entity_id),
        "level":         level,
        "dependency_id": str(parent_fid),
        "parent_id":     parent_fid,
        "child_id":      child_fid,
        "mappings":      mappings,
    }
    r = requests.post(
        f"{BASE_URL_SS}/v1/nested-custom-fields/store",
        headers=_auth_headers(token), json=payload,
    )
    r.raise_for_status()
    print(f"  dep  parent_id={parent_fid} → child_id={child_fid}: "
          f"{r.json().get('message','OK')}")
    log.info(f"[Setup] dep parent_id={parent_fid} → child_id={child_fid}")
    time.sleep(0.3)


def create_all_dependencies(token: str, manifest: list) -> None:
    """
    Build and POST dependency mappings.

    For each (parent_option, child_option) pair allowed by DEPENDENCY_RULES:
      - Text/number child (no server option IDs) → child_value_id=null, child_visibility=true
      - Dropdown/multiselect child with specific allowed options
          → one mapping row per (parent_option_id, child_option_id)
          → child_visibility=null  (the option-ID presence IS the visibility signal)
      - Hidden mappings (None) → no row stored (absence of a row = hidden)
    Checkbox parent: the server auto-creates "Yes" / "No" options whose IDs are
    retrieved via get-default-options (called by refresh_option_maps_from_api before
    this function).  The "true" state in DEPENDENCY_RULES maps to the "Yes" option.
    """
    by_col = {f["column_index"]: f for f in manifest}
    print(f"\n[Setup] Storing {len(DEPENDENCY_RULES)} dependency rule(s) ...")

    for child_col, rule in DEPENDENCY_RULES.items():
        parent_col  = rule["parent_col"]
        parent_type = rule["parent_type"]
        filter_map  = rule["child_option_filter"]

        parent = by_col.get(parent_col)
        child  = by_col.get(child_col)
        if not parent or not child:
            print(f"  SKIP col {child_col}: not in manifest")
            continue

        child_opt_map  = child.get("option_map", {})
        parent_opt_map = parent.get("option_map", {})
        mappings = []

        if parent_type == "checkbox":
            _TRUE_LABELS = {"yes", "true", "1", "checked"}
            allowed = filter_map.get("1")
            if allowed is not None:
                true_option_id = next(
                    (oid for lbl, oid in parent_opt_map.items()
                     if str(lbl).lower().strip() in _TRUE_LABELS),
                    None,
                )
                if true_option_id is None:
                    print(f"  WARN  col {child_col}: checkbox parent '{parent['name']}' "
                          f"has no 'Yes/true' option ID — dependency skipped.\n"
                          f"       Make sure refresh_option_maps_from_api() ran before "
                          f"create_all_dependencies(). option_map={parent_opt_map}")
                    continue
                if allowed == "ALL" or not child.get("options"):
                    mappings.append({
                        "parent_value_id":  true_option_id,
                        "child_value_id":   None,
                        "child_visibility": True,
                    })
                elif isinstance(allowed, frozenset):
                    for clabel in allowed:
                        cid = child_opt_map.get(clabel)
                        if cid:
                            mappings.append({
                                "parent_value_id":  true_option_id,
                                "child_value_id":   cid,
                                "child_visibility": None,
                            })
        else:
            for parent_label, allowed in filter_map.items():
                if allowed is None:
                    continue
                pid = next(
                    (oid for lbl, oid in parent_opt_map.items()
                     if str(lbl).strip() == str(parent_label).strip()),
                    None,
                )
                if pid is None:
                    print(f"  WARN  parent option '{parent_label}' not found "
                          f"in '{parent['name']}'")
                    continue
                if allowed == "ALL" or not child.get("options"):
                    mappings.append({
                        "parent_value_id":  pid,
                        "child_value_id":   None,
                        "child_visibility": True,
                    })
                elif isinstance(allowed, frozenset):
                    for clabel in allowed:
                        cid = child_opt_map.get(clabel)
                        if cid:
                            mappings.append({
                                "parent_value_id":  pid,
                                "child_value_id":   cid,
                                "child_visibility": None,
                            })

        if mappings:
            _store_one_dependency(
                token, ENTITY_TYPE_ID, parent["field_id"], child["field_id"], mappings
            )
        else:
            print(f"  SKIP col {child_col}: no valid mappings to store")

def save_manifest(manifest: list) -> None:
    MANIFEST_FILE.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[Setup] Manifest saved → {MANIFEST_FILE}")


def load_manifest() -> list:
    if not MANIFEST_FILE.is_file():
        raise FileNotFoundError(
            f"Manifest not found: {MANIFEST_FILE}\n"
            "Run with --setup first to create fields and save the manifest."
        )
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def _fetch_default_options(token: str) -> dict:
    """
    GET /v1/custom-fields/get-default-options/{entity_type_id}

    Returns the server-assigned option IDs for every custom field that has options,
    including checkbox fields (whose options are NOT returned in the creation
    response or the bulk GET endpoint).

    Response structure:
      { "<field_id>": [{"id": <int>, "label": str, "value": str, "sequence_no": int}, ...] }
    """
    r = requests.get(
        f"{BASE_URL_SS}/v1/custom-fields/get-default-options/{ENTITY_TYPE_ID}",
        headers=_auth_headers(token),
    )
    r.raise_for_status()
    return r.json().get("data", {})


def refresh_option_maps_from_api(token: str, manifest: list) -> list:
    """
    Call get-default-options and rebuild every field's option_map using the
    real server-assigned IDs.  This is the only reliable source for checkbox
    option IDs (the server auto-creates "Yes" / "No" options for checkbox
    fields; these are never returned in the creation response or bulk GET).

    Returns an updated copy of manifest.
    """
    print("[Setup] Refreshing option IDs from get-default-options ...")
    raw = _fetch_default_options(token)

    updated = []
    for field in manifest:
        fid_str = str(field.get("field_id", ""))
        opts    = raw.get(fid_str, [])
        if opts:
            option_map = {o["label"]: o["id"] for o in opts}
            updated.append({**field, "option_map": option_map})
            print(f"  Refreshed  '{field['name']}' → "
                  f"{len(option_map)} option(s): {list(option_map.keys())}")
        else:
            updated.append(field)
    return updated


def _val_str(value) -> str:
    """Serialise a forced value (str, list, bool) to a human-readable string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def generate_controlled_snippet(
    field:                  dict,
    forced_value,
    scenario_type:          str,
    parent_field_name:      str  = None,
    parent_value           = None,
    allowed_child_values:  list = None,
    disallowed_child_values: list = None,
) -> dict:
    """
    Ask the LLM to generate one natural resume sentence that encodes exactly
    the requested scenario, then return {"snippet": str, "value": forced_value}.

    scenario_type
    ─────────────
    "normal"         → parent field or independent: just mention the forced value
    "valid"          → child is visible AND forced_value is within allowed options;
                       mention parent context + child value naturally
    "invalid"        → child is hidden by dep OR forced_value is a disallowed option;
                       still inject the child (and parent) to create a testable violation
    "missing_parent" → inject child only; do NOT mention parent at all
    """
    fname = field["name"]
    fdesc = field.get("description", "")
    ftype = field["type"]

    fv_str = _val_str(forced_value) if forced_value is not None else ""
    pv_str = _val_str(parent_value) if parent_value is not None else ""

    ret_json_hint = json.dumps(
        {"snippet": "...", "value": forced_value}, ensure_ascii=False
    )

    if scenario_type == "normal":
        prompt = (
            f"Write exactly 1 natural resume sentence that clearly (but not obviously) "
            f"conveys the following fact about the candidate.\n"
            f"Field: {fname}\nDescription: {fdesc}\nValue: {fv_str}\n"
            f"Do NOT use label:value format. Be concise and professional.\n"
            f"Return ONLY valid JSON: {ret_json_hint}"
        )

    elif scenario_type == "valid":
        allowed_hint = (
            f"Allowed values for {fname}: {', '.join(allowed_child_values)}."
            if allowed_child_values else ""
        )
        prompt = (
            f"Write exactly 1–2 natural resume sentences that convey BOTH facts below "
            f"in a single flowing paragraph (no bullet points, no labels).\n"
            f"1. {parent_field_name or 'Work preference'}: {pv_str}\n"
            f"2. {fname}: {fv_str}\n"
            f"Description of {fname}: {fdesc}\n"
            f"{allowed_hint}\n"
            f"Keep it brief and professional. The connection between the two facts should "
            f"feel natural, not forced.\n"
            f"Return ONLY valid JSON: {ret_json_hint}"
        )

    elif scenario_type == "invalid":
        if parent_value is not None:
            parent_clause = (
                f"Also mention {parent_field_name or 'the parent preference'} as {pv_str}. "
            )
        else:
            parent_clause = ""
        disallowed_hint = (
            f"Note: {fv_str} is intentionally outside the typical allowed options."
            if disallowed_child_values else ""
        )
        prompt = (
            f"Write exactly 1–2 natural resume sentences about the candidate mentioning "
            f"{fname} as {fv_str}. {parent_clause}"
            f"Description of {fname}: {fdesc}\n"
            f"{disallowed_hint}\n"
            f"Keep it brief and professional. Do NOT use field labels.\n"
            f"Return ONLY valid JSON: {ret_json_hint}"
        )

    elif scenario_type == "missing_parent":
        avoid_clause = (
            f"Do NOT mention {parent_field_name}." if parent_field_name else ""
        )
        prompt = (
            f"Write exactly 1 natural resume sentence that mentions {fname} as {fv_str}. "
            f"{avoid_clause}\n"
            f"Description of {fname}: {fdesc}\n"
            f"Keep it brief and professional. Do NOT use field labels.\n"
            f"Return ONLY valid JSON: {ret_json_hint}"
        )

    else:
        raise ValueError(f"Unknown scenario_type: {scenario_type!r}")

    result = _call_groq(prompt)
    if result and result.get("snippet"):
        return {"snippet": result["snippet"], "value": forced_value}
    return {"snippet": "", "value": forced_value}


def _log_coverage(
    coverage_counts:      dict,
    scenario_type_counts: dict,
    parent_option_counts: dict,
) -> None:
    """Print and log coverage statistics after ground truth generation."""
    print("\n" + "─" * 64)
    print("[Coverage] Scenario distribution across resumes:")
    for sid, cnt in sorted(coverage_counts.items()):
        bar = "▓" * cnt
        print(f"  {sid:<28s}  ×{cnt:3d}  {bar}")

    print("\n[Coverage] Scenario type totals:")
    for stype in ("valid", "invalid", "missing_parent"):
        cnt = scenario_type_counts.get(stype, 0)
        print(f"  {stype:<20s}  {cnt:4d}")

    print("\n[Coverage] Parent option usage:")
    for pval, cnt in sorted(parent_option_counts.items(), key=lambda x: -x[1]):
        print(f"  {str(pval):<30s}  {cnt:4d}")
    print("─" * 64)

    log.info(
        "[Coverage] %s",
        json.dumps(
            {
                "scenario_counts":     coverage_counts,
                "type_counts":         scenario_type_counts,
                "parent_option_counts": parent_option_counts,
            },
            default=str,
        ),
    )

def generate_controlled_ground_truth(baseline: list, manifest: list) -> list:
    """
    Controlled, deterministic ground-truth generation.

    1.  Assigns one COVERAGE_SCENARIO to each resume via round-robin so that
        every parent option and every scenario_type is exercised.
    2.  For the focal (parent, child) pair in the scenario, uses
        generate_controlled_snippet() to produce a targeted snippet.
    3.  Independent fields are generated with llm_generate_snippet() as usual.
    4.  Logs full coverage breakdown at the end.
    """
    valid       = [r for r in baseline if "error" not in r]
    by_col      = {f["column_index"]: f for f in manifest}
    n_scenarios = len(COVERAGE_SCENARIOS)

    print(
        f"\n[LLM] Controlled ground truth: {len(valid)} resumes, "
        f"{n_scenarios} scenarios (round-robin) ..."
    )

    all_gts: list = []
    coverage_counts:      dict = {s["sid"]: 0 for s in COVERAGE_SCENARIOS}
    scenario_type_counts: dict = {"valid": 0, "invalid": 0, "missing_parent": 0}
    parent_option_counts: dict = {}

    for idx, record in enumerate(valid):
        rid         = record["resume_id"]
        resume_text = record.get("resume_text", "")
        scenario    = COVERAGE_SCENARIOS[idx % n_scenarios]

        sid          = scenario["sid"]
        stype        = scenario["scenario_type"]
        parent_col   = scenario["parent_col"]
        parent_value = scenario["parent_value"]
        child_col    = scenario["child_col"]
        forced_cv    = scenario.get("forced_child_value")

        coverage_counts[sid]               = coverage_counts.get(sid, 0) + 1
        scenario_type_counts[stype]        = scenario_type_counts.get(stype, 0) + 1
        if parent_value is not None:
            pk = str(parent_value)
            parent_option_counts[pk] = parent_option_counts.get(pk, 0) + 1

        print(
            f"  [{idx + 1:3d}/{len(valid)}] {rid}  scenario={sid} ...",
            end=" ", flush=True,
        )
        injected: list = []

        if parent_col and parent_value is not None:
            parent_field = by_col.get(parent_col)
            if parent_field:
                try:
                    res = generate_controlled_snippet(
                        parent_field, parent_value, "normal",
                        parent_field_name=parent_field["name"],
                    )
                    if res["snippet"]:
                        injected.append({
                            "field_id":      parent_field.get("field_id", ""),
                            "field_name":    parent_field["name"],
                            "field_type":    parent_field["type"],
                            "column_index":  parent_col,
                            "role":          "parent",
                            "value":         normalize_checkbox(parent_value) if parent_field["type"] == "checkbox" else parent_value,
                            "snippet":       res["snippet"],
                            "scenario_type": "normal",
                            "scenario_id":   sid,
                        })
                except Exception as exc:
                    log.warning("[LLM] %s parent col%d: %s", rid, parent_col, exc)

        child_field = by_col.get(child_col)
        if child_field:
            parent_field     = by_col.get(parent_col) if parent_col else None
            parent_fname     = parent_field["name"] if parent_field else None
            child_ftype      = child_field["type"]

            if forced_cv is None:
                if child_ftype == "text":
                    forced_cv = _default_text_value(child_col)
                elif child_ftype == "number":
                    forced_cv = 3

            if forced_cv is not None:
                allowed, disallowed = _get_allowed_disallowed(
                    parent_col, parent_value, child_col, manifest
                )
                try:
                    res = generate_controlled_snippet(
                        child_field, forced_cv, stype,
                        parent_field_name=parent_fname,
                        parent_value=parent_value,
                        allowed_child_values=allowed,
                        disallowed_child_values=disallowed,
                    )
                    if res["snippet"]:
                        injected.append({
                            "field_id":      child_field.get("field_id", ""),
                            "field_name":    child_field["name"],
                            "field_type":    child_ftype,
                            "column_index":  child_col,
                            "role":          "child",
                            "value":         normalize_checkbox(forced_cv) if child_ftype == "checkbox" else forced_cv,
                            "snippet":       res["snippet"],
                            "scenario_type": stype,
                            "scenario_id":   sid,
                        })
                except Exception as exc:
                    log.warning("[LLM] %s child col%d: %s", rid, child_col, exc)

        for field in manifest:
            if field.get("role") != "independent":
                continue
            try:
                pre_existing = pre_scan_existing_values(resume_text, field)
                res          = llm_generate_snippet(field, pre_existing)
                if res.get("snippet"):
                    gt_val = res["value"]
                    if field["type"] == "checkbox":
                        v = str(gt_val).strip()
                        gt_val = "1" if v in {"1", "true", "yes"} else "0"
                    injected.append({
                        "field_id":      field.get("field_id", ""),
                        "field_name":    field["name"],
                        "field_type":    field["type"],
                        "column_index":  field["column_index"],
                        "role":          "independent",
                        "value":         gt_val,
                        "snippet":       res["snippet"],
                        "scenario_type": "normal",
                        "scenario_id":   sid,
                    })
            except Exception as exc:
                log.warning("[LLM] %s independent col%d: %s", rid, field["column_index"], exc)

        all_gts.append({
            "resume_id":    rid,
            "scenario_id":  sid,
            "scenario_type": stype,
            "parent_col":   parent_col,
            "parent_value": parent_value,
            "child_col":    child_col,
            "injected_fields": injected,
        })
        print(f"{len(injected)} injected  OK")
        time.sleep(0.3)

    _log_coverage(coverage_counts, scenario_type_counts, parent_option_counts)

    GT_FILE.write_text(json.dumps(all_gts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[LLM] Ground truth saved → {GT_FILE}")
    return all_gts

def run_nested_track_b(token: str, baseline: list, ground_truth_all: list) -> list:
    """
    For each resume: randomly inject snippets via inject_snippets_individually,
    convert to PDF, upload to SS, parse, extract custcolumn1-10.
    """
    gt_by_id = {g["resume_id"]: g for g in ground_truth_all}
    valid    = [r for r in baseline if "error" not in r]
    all_cols = [f["column_index"] for f in NESTED_FIELDS]
    results  = []

    print(f"\n[Track B / Nested] Parsing {len(valid)} resume(s) ...")

    for record in valid:
        rid         = record["resume_id"]
        resume_text = record.get("resume_text", "")
        injected    = gt_by_id.get(rid, {}).get("injected_fields", [])
        pdf_name    = f"nested_{rid}.pdf"

        print(f"  [{rid}] ...", end=" ", flush=True)
        try:
            modified_text = inject_snippets_individually(resume_text, injected)
            pdf_bytes     = text_to_pdf_bytes(modified_text)
            local_pdf_path = INJECTED_PDF_DIR / pdf_name
            local_pdf_path.write_bytes(pdf_bytes)
            presign       = get_presigned_url(BASE_URL_SS, pdf_name, token)
            upload_pdf_bytes(presign["preSignedUrl"], pdf_bytes)
            parse_result  = call_parse_api(BASE_URL_SS, token, presign["key"], pdf_name)
            parsed_custom = extract_custom_fields(parse_result, all_cols)

            results.append({
                "resume_id":       rid,
                "modified_text":   modified_text,
                "parsed_custom":   {str(k): v for k, v in parsed_custom.items()},
                "injected_fields": injected,
            })
            print("OK")
            log.info(f"[Nested Track B] {rid} OK")
        except Exception as exc:
            print(f"ERROR — {exc}")
            log.error(f"[Nested Track B] {rid} failed: {exc}")
            results.append({"resume_id": rid, "error": str(exc)})
        time.sleep(1)

    RESULTS_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[Track B] Results saved → {RESULTS_FILE}")
    return results

def _get_parent_keys(parent_val, parent_type: str) -> list:
    """
    Normalise parent value into a list of strip-only lookup keys.
      checkbox    → ["1"] or ["0"]
      dropdown    → [stripped value] (case preserved)
      multiselect → parse_multivalue → list of stripped tokens
    """
    if is_empty(parent_val):
        return []
    if parent_type == "checkbox":
        return [normalize_checkbox(parent_val)]
    if parent_type == "dropdown":
        return [str(parent_val).strip()]
    return parse_multivalue(parent_val)


def apply_dependency_with_filtering(
    child_col:        int,
    parent_parsed_val,
    gt_val,
    parsed_val,
    child_field_type: str,
) -> tuple:
    """
    Apply dependency visibility check followed by option-level child filtering
    to BOTH the ground-truth value and the raw parsed value.

    Returns
    -------
    expected_val   : what the parser SHOULD return
                     • None  if child is hidden or option-filtered gt yields nothing
                     • gt_val (or option-filtered subset) if child is visible
    filtered_val   : parsed_val after applying the same dep + option filter
    dep_met        : True  if parent condition makes child visible
    option_filtered: True  if any parsed values were removed by option filtering
    """
    rule = DEPENDENCY_RULES.get(child_col)
    if rule is None:
        return gt_val, parsed_val, True, False

    parent_type        = rule["parent_type"]
    child_option_filter = rule["child_option_filter"]

    parent_keys = _get_parent_keys(parent_parsed_val, parent_type)
    if not parent_keys:
        return None, None, False, False

    child_visible = False
    has_all       = False
    allowed_opts: set = set()

    for pk in parent_keys:
        mapped = _lookup_filter(child_option_filter, pk)
        if mapped is None:
            continue
        child_visible = True
        if mapped == "ALL":
            has_all = True
        elif isinstance(mapped, frozenset):
            allowed_opts.update(mapped)

    if not child_visible:
        return None, None, False, False

    if child_field_type not in ("dropdown", "multiselect") or has_all:
        return gt_val, parsed_val, True, False

    opts_frozen    = frozenset(allowed_opts)
    expected_val   = _filter_value_to_allowed(gt_val,     opts_frozen, child_field_type)
    filtered_val   = _filter_value_to_allowed(parsed_val, opts_frozen, child_field_type)

    raw_parts  = parse_multivalue(parsed_val) if child_field_type == "multiselect" else []
    filt_parts = parse_multivalue(filtered_val) if isinstance(filtered_val, list) else (
        [str(filtered_val).strip()] if filtered_val else []
    )
    option_filtered = (
        not is_empty(parsed_val)
        and len(filt_parts) < len(raw_parts or [str(parsed_val).strip()])
    )

    return expected_val, filtered_val, True, option_filtered

def classify_nested(
    expected_val,
    filtered_val,
    raw_parsed_val,
    field_type: str,
) -> tuple:
    """
    Returns (classification, details).

    Dependency Violation : child should be hidden (expected=None) but parser
                           returned a value in raw_parsed_val.
    Both Empty           : nothing expected, nothing parsed.
    Missed               : value expected but filtered result is empty.
    Exact / Partial / Mismatch : standard comparison on filtered_val vs expected_val.
    """
    exp_empty  = (expected_val is None) or is_empty(expected_val)
    filt_empty = (filtered_val is None) or is_empty(filtered_val)
    raw_empty  = is_empty(raw_parsed_val)

    if exp_empty and not raw_empty:
        return (
            "Dependency Violation",
            f"expected=None (child hidden/option-filtered), parsed='{raw_parsed_val}'",
        )

    if exp_empty and raw_empty:
        return "Both Empty", ""

    if not exp_empty and filt_empty:
        return "Missed", f"expected='{expected_val}'"

    if field_type == "multiselect":
        gt_set  = set(normalize_option_list(expected_val))
        prs_set = set(
            normalize_option_list(filtered_val)
            if not is_empty(filtered_val) else []
        )
        if gt_set == prs_set:
            return "Exact Match", ""
        overlap = gt_set & prs_set
        if overlap:
            if len(prs_set) > len(gt_set):
                return "Partial Match (-ve)", f"extra={sorted(prs_set - gt_set)}"
            return "Partial Match (+ve)", f"missing={sorted(gt_set - prs_set)}"
        return "Mismatch", f"expected={sorted(gt_set)}, filtered={sorted(prs_set)}"

    if field_type == "dropdown":
        e_n = normalize_option(str(expected_val))
        f_n = normalize_option(str(filtered_val))
        if e_n == f_n:
            return "Exact Match", ""
        if e_n in f_n or f_n in e_n:
            return "Partial Match (+ve)", f"expected='{e_n}', filtered='{f_n}'"
        return "Mismatch", f"expected='{e_n}', filtered='{f_n}'"

    if field_type == "number":
        def _n(v):
            d = re.sub(r"[^\d]", "", str(v))
            return str(int(d)) if d else str(v).strip()
        e_n, f_n = _n(expected_val), _n(filtered_val)
        return ("Exact Match", "") if e_n == f_n else (
            "Mismatch", f"expected='{e_n}', filtered='{f_n}'"
        )

    if field_type == "checkbox":
        e_n = normalize_checkbox(expected_val)
        f_n = normalize_checkbox(filtered_val)
        return ("Exact Match", "") if e_n == f_n else (
            "Mismatch", f"expected='{e_n}', filtered='{f_n}'"
        )

    e_n = normalize_val(str(expected_val))
    f_n = normalize_val(str(filtered_val))
    if e_n == f_n:
        return "Exact Match", ""
    if e_n in f_n or f_n in e_n:
        return "Partial Match (+ve)", f"expected='{e_n}', filtered='{f_n}'"
    return "Mismatch", f"expected='{e_n}', filtered='{f_n}'"

def _fmt_val(val) -> str:
    """Serialise a value for a DataFrame cell (list → JSON string)."""
    if val is None:
        return ""
    if isinstance(val, list):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def run_nested_validation(results: list, manifest: list) -> pd.DataFrame:
    """
    For every (resume × field) pair that was actively tested:
      1. Resolve parent's parsed value.
      2. Call apply_dependency_with_filtering() to get expected_val + filtered_val.
      3. classify_nested() to compare.
    Rows where field was not injected and not parsed are silently skipped.
    Adds scenario_type and expected_outcome columns for coverage analysis.
    """
    rows = []

    for res in results:
        if "error" in res:
            rows.append({
                "resume_id":         res["resume_id"],
                "scenario_id":       res.get("scenario_id", ""),
                "scenario_type":     res.get("scenario_type", ""),
                "expected_outcome":  "",
                "field_name":        "–",
                "field_type":        "–",
                "column_index":      "–",
                "role":              "–",
                "parent_value":      "",
                "parsed_value":      "",
                "filtered_value":    "",
                "expected_value":    "",
                "classification":    "ERROR",
                "details":           res["error"],
                "dependency_status": "–",
            })
            continue

        rid           = res["resume_id"]
        res_sid       = res.get("scenario_id", "")
        res_stype     = res.get("scenario_type", "")
        parsed_custom = {int(k): v for k, v in res["parsed_custom"].items()}

        gt_by_col       = {}
        ftype_by_col    = {}
        stype_by_col    = {}
        sid_by_col      = {}
        for inj in res.get("injected_fields", []):
            c = inj["column_index"]
            gt_by_col[c]    = inj["value"]
            ftype_by_col[c] = inj["field_type"]
            stype_by_col[c] = inj.get("scenario_type", "normal")
            sid_by_col[c]   = inj.get("scenario_id", res_sid)

        for field in manifest:
            col    = field["column_index"]
            fname  = field["name"]
            ftype  = field["type"]
            role   = field.get("role", "independent")

            gt_val     = gt_by_col.get(col)
            parsed_val = parsed_custom.get(col)

            if gt_val is None and is_empty(parsed_val):
                continue

            col_stype = stype_by_col.get(col, "normal")
            col_sid   = sid_by_col.get(col, res_sid)

            rule          = DEPENDENCY_RULES.get(col)
            parent_col    = rule["parent_col"] if rule else None
            parent_parsed = parsed_custom.get(parent_col) if parent_col else None

            if role in ("parent", "independent"):
                expected_val    = gt_val
                filtered_val    = parsed_val
                dep_met         = True
                option_filtered = False
                parent_display  = ""
                classification, details = classify_nested(
                    expected_val, filtered_val, parsed_val, ftype
                )
                if classification == "Dependency Violation":
                    classification = "Hallucination"
                exp_outcome = "match"
            else:
                expected_val, filtered_val, dep_met, option_filtered = \
                    apply_dependency_with_filtering(
                        col, parent_parsed, gt_val, parsed_val, ftype
                    )
                parent_display = (
                    str(parent_parsed) if parent_parsed is not None else "(not parsed)"
                )
                classification, details = classify_nested(
                    expected_val, filtered_val, parsed_val, ftype
                )
                if col_stype in ("invalid", "missing_parent"):
                    exp_outcome = "violation"
                elif col_stype == "valid":
                    exp_outcome = "match"
                else:
                    exp_outcome = "n/a"

            dep_status = (
                "N/A" if role in ("parent", "independent")
                else ("Met" if dep_met else "Not Met")
            )

            rows.append({
                "resume_id":         rid,
                "scenario_id":       col_sid,
                "scenario_type":     col_stype,
                "expected_outcome":  exp_outcome,
                "field_name":        fname,
                "field_type":        ftype,
                "column_index":      col,
                "role":              role,
                "parent_value":      parent_display,
                "parsed_value":      _fmt_val(parsed_val),
                "filtered_value":    _fmt_val(filtered_val),
                "expected_value":    (
                    _fmt_val(expected_val)
                    if expected_val is not None
                    else "None (hidden)"
                ),
                "classification":    classification,
                "details":           details,
                "dependency_status": dep_status,
            })

    return pd.DataFrame(rows)

_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize(val):
    return _ILLEGAL_XML_RE.sub("", val) if isinstance(val, str) else val


def build_nested_report(df: pd.DataFrame) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    CLS_FILL = {
        "Exact Match":           "D6EFD8",
        "Both Empty":            "E8E8E8",
        "Partial Match (+ve)":   "FFF3CD",
        "Partial Match (-ve)":   "F4CCCC",
        "Missed":                "FADADD",
        "Mismatch":              "FADADD",
        "Hallucination":         "F4CCCC",
        "Dependency Violation":  "FF4444",
        "ERROR":                 "F4CCCC",
    }

    STYPE_FILL = {
        "valid":          "D6EFD8",
        "invalid":        "FADADD",
        "missing_parent": "FFF3CD",
        "normal":         "FFFFFF",
    }

    HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
    COV_HDR     = PatternFill("solid", fgColor="274E13")
    VIOL_HDR    = PatternFill("solid", fgColor="CC0000")
    HEADER_FONT = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
    DATA_FONT   = Font(size=10, name="Calibri")
    THIN        = Side(border_style="thin", color="C0C0C0")
    BORDER      = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    BAND_A      = PatternFill("solid", fgColor="EBF3FB")
    BAND_B      = PatternFill("solid", fgColor="FFFFFF")

    DETAIL_COLS   = [
        "resume_id", "scenario_id", "scenario_type", "expected_outcome",
        "field_name", "field_type", "column_index", "role",
        "parent_value", "parsed_value", "filtered_value", "expected_value",
        "classification", "details", "dependency_status",
    ]
    DETAIL_WIDTHS = [10, 22, 16, 16, 28, 14, 14, 14, 24, 28, 28, 28, 26, 52, 18]

    safe_df = df.apply(lambda col: col.map(_sanitize)) if not df.empty else df

    wb = Workbook()

    ws = wb.active
    ws.title = "Detail"
    ws.freeze_panes = "A2"

    for ci, col in enumerate(DETAIL_COLS, start=1):
        cell = ws.cell(row=1, column=ci,
                       value=col.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        cell.border    = BORDER
        ws.column_dimensions[get_column_letter(ci)].width = DETAIL_WIDTHS[ci - 1]
    ws.row_dimensions[1].height = 30

    rid_band: dict  = {}
    band_toggle     = [0]

    for ri, row in enumerate(safe_df.itertuples(index=False), start=2):
        rid = getattr(row, "resume_id", "")
        if rid not in rid_band:
            band_toggle[0] = (band_toggle[0] + 1) % 2
            rid_band[rid] = band_toggle[0]
        row_fill = BAND_A if rid_band[rid] == 0 else BAND_B

        cls     = getattr(row, "classification", "")
        stype   = getattr(row, "scenario_type", "")
        cls_hex = CLS_FILL.get(cls)

        for ci, col in enumerate(DETAIL_COLS, start=1):
            val  = getattr(row, col, "")
            cell = ws.cell(row=ri, column=ci,
                           value=str(val) if val is not None else "")
            cell.font      = DATA_FONT
            cell.border    = BORDER
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col == "classification" and cls_hex:
                cell.fill = PatternFill("solid", fgColor=cls_hex)
            elif col == "scenario_type" and stype in STYPE_FILL:
                cell.fill = PatternFill("solid", fgColor=STYPE_FILL[stype])
            else:
                cell.fill = row_fill

    ws2 = wb.create_sheet("Summary by Field")
    SUM_COLS   = [
        "field_name", "column_index", "role",
        "Exact Match", "Missed", "Dependency Violation",
        "Partial Match (+ve)", "Partial Match (-ve)", "Mismatch",
        "Both Empty", "Total", "Dep Violation %",
    ]
    SUM_WIDTHS = [28, 14, 14, 16, 16, 22, 20, 20, 16, 14, 10, 16]

    for ci, h in enumerate(SUM_COLS, start=1):
        cell = ws2.cell(row=1, column=ci, value=h)
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border    = BORDER
        ws2.column_dimensions[get_column_letter(ci)].width = SUM_WIDTHS[ci - 1]

    if not safe_df.empty:
        field_meta = (
            safe_df[["field_name", "column_index", "role"]]
            .drop_duplicates(subset=["field_name"])
        )
        for ri, (_, frow) in enumerate(field_meta.iterrows(), start=2):
            fname = frow["field_name"]
            sub   = safe_df[safe_df["field_name"] == fname]
            total = len(sub)
            counts = {
                cls: int((sub["classification"] == cls).sum())
                for cls in ["Exact Match", "Missed", "Dependency Violation",
                            "Partial Match (+ve)", "Partial Match (-ve)",
                            "Mismatch", "Both Empty"]
            }
            dv_pct = round(counts["Dependency Violation"] / total * 100, 1) if total else 0.0
            vals = (
                [fname, frow["column_index"], frow["role"]]
                + [counts[c] for c in [
                    "Exact Match", "Missed", "Dependency Violation",
                    "Partial Match (+ve)", "Partial Match (-ve)",
                    "Mismatch", "Both Empty",
                ]]
                + [total, dv_pct]
            )
            for ci, v in enumerate(vals, start=1):
                cell = ws2.cell(row=ri, column=ci, value=v)
                cell.font   = DATA_FONT
                cell.border = BORDER

    ws3 = wb.create_sheet("Dependency Violations")
    VIOL_COLS   = [
        "resume_id", "scenario_id", "scenario_type",
        "field_name", "column_index", "role",
        "parent_value", "parsed_value", "filtered_value", "expected_value", "details",
    ]
    VIOL_WIDTHS = [10, 22, 16, 28, 14, 12, 24, 28, 28, 22, 52]

    for ci, h in enumerate(VIOL_COLS, start=1):
        cell = ws3.cell(row=1, column=ci, value=h.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = VIOL_HDR
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border    = BORDER
        ws3.column_dimensions[get_column_letter(ci)].width = VIOL_WIDTHS[ci - 1]

    violations = (
        safe_df[safe_df["classification"] == "Dependency Violation"]
        if not safe_df.empty else pd.DataFrame()
    )
    if violations.empty:
        cell = ws3.cell(row=2, column=1, value="No dependency violations detected.")
        cell.font = Font(bold=True, color="008000", size=11, name="Calibri")
    else:
        vbg = PatternFill("solid", fgColor="FFE0E0")
        for ri, vrow in enumerate(violations.itertuples(index=False), start=2):
            for ci, col in enumerate(VIOL_COLS, start=1):
                val  = getattr(vrow, col, "")
                cell = ws3.cell(row=ri, column=ci,
                                value=str(val) if val is not None else "")
                cell.font      = DATA_FONT
                cell.fill      = vbg
                cell.border    = BORDER
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws4 = wb.create_sheet("Coverage Summary")
    COV_COLS   = [
        "scenario_id", "scenario_type", "parent_value", "child_col",
        "child_field_name",
        "total_assigned",
        "valid_count", "invalid_count", "missing_parent_count",
        "dep_violations", "exact_matches", "missed",
    ]
    COV_WIDTHS = [24, 18, 28, 12, 28, 16, 14, 14, 22, 18, 16, 12]

    for ci, h in enumerate(COV_COLS, start=1):
        cell = ws4.cell(row=1, column=ci, value=h.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = COV_HDR
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border    = BORDER
        ws4.column_dimensions[get_column_letter(ci)].width = COV_WIDTHS[ci - 1]

    child_name_by_col = {f["column_index"]: f["name"] for f in NESTED_FIELDS}
    if not safe_df.empty and "scenario_id" in safe_df.columns:
        for ri, sc in enumerate(COVERAGE_SCENARIOS, start=2):
            sid        = sc["sid"]
            stype      = sc["scenario_type"]
            p_val      = str(sc["parent_value"]) if sc["parent_value"] is not None else "(none)"
            child_col  = sc["child_col"]
            child_name = child_name_by_col.get(child_col, "")

            sub = safe_df[
                (safe_df["scenario_id"] == sid)
                & (safe_df["column_index"] == child_col)
            ] if "scenario_id" in safe_df.columns else pd.DataFrame()

            total    = len(sub)
            v_cnt    = int((sub["scenario_type"] == "valid").sum())          if not sub.empty else 0
            i_cnt    = int((sub["scenario_type"] == "invalid").sum())        if not sub.empty else 0
            mp_cnt   = int((sub["scenario_type"] == "missing_parent").sum()) if not sub.empty else 0
            dv_cnt   = int((sub["classification"] == "Dependency Violation").sum()) if not sub.empty else 0
            em_cnt   = int((sub["classification"] == "Exact Match").sum())   if not sub.empty else 0
            miss_cnt = int((sub["classification"] == "Missed").sum())        if not sub.empty else 0

            row_fill = PatternFill("solid", fgColor=STYPE_FILL.get(stype, "FFFFFF"))
            vals = [sid, stype, p_val, child_col, child_name,
                    total, v_cnt, i_cnt, mp_cnt, dv_cnt, em_cnt, miss_cnt]
            for ci, v in enumerate(vals, start=1):
                cell = ws4.cell(row=ri, column=ci, value=v)
                cell.font      = DATA_FONT
                cell.fill      = row_fill
                cell.border    = BORDER
                cell.alignment = Alignment(horizontal="left" if ci > 1 else "center")

    wb.save(REPORT_FILE)
    print(f"\n[Report] Saved → {REPORT_FILE}")

def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("NESTED CUSTOM FIELDS — VALIDATION SUMMARY (v3 — controlled gen)")
    print("=" * 72)

    if df.empty:
        print("  No results.")
        return

    total = len(df)
    print(f"\n  Total rows evaluated : {total}")
    for cls in [
        "Exact Match", "Partial Match (+ve)", "Partial Match (-ve)",
        "Missed", "Dependency Violation", "Mismatch",
        "Hallucination", "Both Empty", "ERROR",
    ]:
        n = (df["classification"] == cls).sum()
        if n:
            flag = "  *** " if cls == "Dependency Violation" else "      "
            print(f"{flag}{cls:<38s} {n:5d}  ({n / total * 100:5.1f}%)")

    if "scenario_type" in df.columns:
        print("\n  Breakdown by scenario_type:")
        for stype in ("valid", "invalid", "missing_parent", "normal"):
            sub = df[df["scenario_type"] == stype]
            if sub.empty:
                continue
            n_sub = len(sub)
            em  = (sub["classification"] == "Exact Match").sum()
            dv  = (sub["classification"] == "Dependency Violation").sum()
            ms  = (sub["classification"] == "Missed").sum()
            print(
                f"    {stype:<20s}  n={n_sub:4d}  "
                f"EM={em:4d} ({em/n_sub*100:4.0f}%)  "
                f"DepViol={dv:4d} ({dv/n_sub*100:4.0f}%)  "
                f"Missed={ms:4d} ({ms/n_sub*100:4.0f}%)"
            )

    if "scenario_id" in df.columns:
        child_df = df[df["role"] == "child"] if "role" in df.columns else pd.DataFrame()
        if not child_df.empty:
            print("\n  Per-scenario results  (sid → field, count, EM/DepViol/Missed):")
            for sc in COVERAGE_SCENARIOS:
                sid  = sc["sid"]
                ccol = sc["child_col"]
                sub  = child_df[
                    (child_df["scenario_id"] == sid)
                    & (child_df["column_index"] == ccol)
                ]
                if sub.empty:
                    continue
                n   = len(sub)
                em  = (sub["classification"] == "Exact Match").sum()
                dv  = (sub["classification"] == "Dependency Violation").sum()
                ms  = (sub["classification"] == "Missed").sum()
                print(
                    f"    {sid:<28s}  col{ccol}  n={n:3d}  "
                    f"EM={em:3d}  DepViol={dv:3d}  Miss={ms:3d}"
                )

    print("\n  Child field breakdown  (DepViol / EM / Missed):")
    child_df = df[df["role"] == "child"] if "role" in df.columns else pd.DataFrame()
    if not child_df.empty:
        for fname, grp in child_df.groupby("field_name"):
            n  = len(grp)
            dv = (grp["classification"] == "Dependency Violation").sum()
            em = (grp["classification"] == "Exact Match").sum()
            ms = (grp["classification"] == "Missed").sum()
            print(f"    col {grp['column_index'].iloc[0]}  {fname:<32s}"
                  f"  DepViol={dv:3d}/{n}  EM={em:3d}  Miss={ms:3d}")
    else:
        print("    No child fields found in results.")
    print("=" * 72)

def main():
    print("=" * 72)
    print("Nested Custom Fields — Phase 1 Testing Pipeline (v3 — controlled gen)")
    print(f"  {len(COVERAGE_SCENARIOS)} coverage scenarios, round-robin across resumes")
    print("=" * 72)

    run_setup   = "--setup"   in sys.argv
    force_rerun = "--reparse" in sys.argv

    print("\n[Auth] Fetching tokens ...")
    track_a_token = get_token(BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A)
    ss_token      = get_token(BASE_URL_SS,       EMAIL_SS,       PASSWORD_SS)
    print("  Track A token OK | SS token OK")

    if run_setup:
        print("\n[Setup] Creating nested custom fields and dependencies ...")
        manifest = create_nested_fields(ss_token)
        if not manifest:
            print("[Setup] No fields created — aborting.")
            return
        manifest = refresh_option_maps_from_api(ss_token, manifest)
        save_manifest(manifest)
        create_all_dependencies(ss_token, manifest)
        print("\n[Setup] Done. Run without --setup to parse and validate.")
        return

    try:
        manifest = load_manifest()
    except FileNotFoundError as exc:
        print(f"\n[Error] {exc}")
        return
    print(f"[Manifest] Loaded {len(manifest)} field(s)")

    if BASELINE_FILE.exists() and not force_rerun:
        print(f"[Track A] Loading cached baseline → {BASELINE_FILE}")
        baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    else:
        baseline = run_track_a(track_a_token)
    valid_count = sum(1 for r in baseline if "error" not in r)
    print(f"[Track A] {valid_count} / {len(baseline)} resumes OK")

    if GT_FILE.exists() and not force_rerun:
        print(f"[LLM] Loading cached ground truth → {GT_FILE}")
        ground_truth_all = json.loads(GT_FILE.read_text(encoding="utf-8"))
    else:
        ground_truth_all = generate_controlled_ground_truth(baseline, manifest)

    if RESULTS_FILE.exists() and not force_rerun:
        print(f"[Track B] Loading cached results → {RESULTS_FILE}")
        results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    else:
        results = run_nested_track_b(ss_token, baseline, ground_truth_all)

    print("\n[Validation] Applying dependency rules + option filtering ...")
    df = run_nested_validation(results, manifest)

    df.to_json(str(JSON_OUT_FILE), orient="records", indent=2, force_ascii=False)
    print(f"[Output] JSON  → {JSON_OUT_FILE}")
    build_nested_report(df)
    print_summary(df)


if __name__ == "__main__":
    main()

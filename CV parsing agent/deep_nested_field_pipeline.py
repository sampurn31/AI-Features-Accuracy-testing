#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deep Nested Custom Fields — 5-Level Chain Testing Pipeline
-----------------------------------------------------------
Two hierarchical chains (max 5 levels deep) + 2 independent fields.
column_index 1–10 (sequential, custcolumn1 … custcolumn10).

Field layout:
  ─── Chain A: 5 levels ───────────────────────────────────────────────────
  col 1  Work Arrangement         (dropdown: Onsite / Remote / Hybrid)       L1 root
  col 2  Remote Setup             (dropdown: Home Office / Co-working Space)  L2 ← col1
             visible when col1 ∈ {Remote, Hybrid}
  col 3  Office Room Type         (dropdown: Dedicated Room / Shared Room)    L3 ← col2
             visible when col2 = Home Office
  col 4  Equipment Source         (dropdown: Personal / Company-provided)     L4 ← col3
             visible when col3 = Dedicated Room
  col 5  Equipment Specification  (text)                                      L5 ← col4
             visible when col4 = Personal

  ─── Chain B: 3 levels ───────────────────────────────────────────────────
  col 6  Domain Expertise         (dropdown: Engineering / Design / Mgmt)     L1 root
  col 7  Engineering Specialization (dropdown: Backend / Frontend / DevOps)   L2 ← col6
             visible when col6 = Engineering
  col 8  DevOps Tool Stack        (multiselect: Docker / Kubernetes /         L3 ← col7
                                   Terraform / CI/CD Pipelines)
             visible when col7 = DevOps   → all 4 options valid
             visible when col7 = Backend  → only Docker, Kubernetes valid
             hidden  when col7 = Frontend

  ─── Independent ─────────────────────────────────────────────────────────
  col 9  Years of Experience      (number)
  col 10 Target Salary            (text)

API dependency rule (from cURL reference):
  Level 1 : dependency_id = root_fid (int), parent_id = root_fid (int)
  Level ≥2: dependency_id = root_fid (str), parent_id = immediate_parent_fid (str)
  All levels in a chain share the same dependency_id (the L1 root).

Usage:
    python3 deep_nested_field_pipeline.py --setup    # create fields + deps (once)
    python3 deep_nested_field_pipeline.py            # GT → parse → validate
    python3 deep_nested_field_pipeline.py --reparse  # force re-GT + re-parse
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
    BASE_URL_SS,   EMAIL_SS,   PASSWORD_SS,   ENTITY_TYPE_ID,
    BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A,
)

from config import DEEP_NESTED_SS_EMAIL as EMAIL_SS, DEEP_NESTED_SS_PASSWORD as PASSWORD_SS

load_dotenv()

OUTPUT_DIR       = Path("pipeline_output/deep_nested")
INJECTED_PDF_DIR = OUTPUT_DIR / "injected_pdfs"
LOGS_DIR         = Path("logs")
MANIFEST_FILE    = OUTPUT_DIR / "deep_nested_manifest.json"
GT_FILE          = OUTPUT_DIR / "deep_nested_ground_truth.json"
RESULTS_FILE     = OUTPUT_DIR / "deep_nested_track_b_results.json"
REPORT_FILE      = OUTPUT_DIR / "deep_nested_validation_report.xlsx"
JSON_OUT_FILE    = OUTPUT_DIR / "deep_nested_validation.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INJECTED_PDF_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=str(LOGS_DIR / "deep_nested_pipeline.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DEEP_NESTED_FIELDS: list = [
    {
        "column_index": 1,
        "name":         "Work Arrangement",
        "type":         "dropdown",
        "role":         "l1_root",
        "chain":        "A",
        "depth":        1,
        "description":  "The primary working environment the candidate is targeting for their next role.",
        "options":      ["Onsite", "Remote", "Hybrid"],
    },
    {
        "column_index": 2,
        "name":         "Remote Setup",
        "type":         "dropdown",
        "role":         "l2_child",
        "chain":        "A",
        "depth":        2,
        "parent_col":   1,
        "description":  "Where the candidate works when away from a company office.",
        "options":      ["Home Office", "Co-working Space"],
    },
    {
        "column_index": 3,
        "name":         "Office Room Type",
        "type":         "dropdown",
        "role":         "l3_child",
        "chain":        "A",
        "depth":        3,
        "parent_col":   2,
        "description":  "The type of home workspace the candidate uses for remote work.",
        "options":      ["Dedicated Room", "Shared Room"],
    },
    {
        "column_index": 4,
        "name":         "Equipment Source",
        "type":         "dropdown",
        "role":         "l4_child",
        "chain":        "A",
        "depth":        4,
        "parent_col":   3,
        "description":  "Whether the candidate uses personal hardware or relies on company-provided equipment.",
        "options":      ["Personal", "Company-provided"],
    },
    {
        "column_index": 5,
        "name":         "Equipment Specification",
        "type":         "text",
        "role":         "l5_leaf",
        "chain":        "A",
        "depth":        5,
        "parent_col":   4,
        "description":  "Details of the candidate's own hardware setup used for remote work.",
        "options":      [],
    },
    {
        "column_index": 6,
        "name":         "Domain Expertise",
        "type":         "dropdown",
        "role":         "l1_root",
        "chain":        "B",
        "depth":        1,
        "description":  "The broad professional domain the candidate specialises in.",
        "options":      ["Engineering", "Design", "Management"],
    },
    {
        "column_index": 7,
        "name":         "Engineering Specialization",
        "type":         "dropdown",
        "role":         "l2_child",
        "chain":        "B",
        "depth":        2,
        "parent_col":   6,
        "description":  "The technical sub-discipline the candidate focuses on within engineering.",
        "options":      ["Backend", "Frontend", "DevOps"],
    },
    {
        "column_index": 8,
        "name":         "DevOps Tool Stack",
        "type":         "multiselect",
        "role":         "l3_leaf",
        "chain":        "B",
        "depth":        3,
        "parent_col":   7,
        "description":  "The specific infrastructure or DevOps tools the candidate is most proficient with.",
        "options":      ["Docker", "Kubernetes", "Terraform", "CI/CD Pipelines"],
    },
    {
        "column_index": 9,
        "name":         "Years of Experience",
        "type":         "number",
        "role":         "independent",
        "chain":        None,
        "depth":        0,
        "description":  "Total years of relevant professional experience the candidate brings.",
        "options":      [],
    },
    {
        "column_index": 10,
        "name":         "Target Salary",
        "type":         "text",
        "role":         "independent",
        "chain":        None,
        "depth":        0,
        "description":  "The compensation the candidate expects for their next permanent or contract role.",
        "options":      [],
    },
]

FIELD_BY_COL: dict = {f["column_index"]: f for f in DEEP_NESTED_FIELDS}

DEPENDENCY_RULES: dict = {
    2: {
        "parent_col":          1,
        "parent_type":         "dropdown",
        "chain":               "A",
        "depth":               2,
        "child_type":          "dropdown",
        "child_option_filter": {
            "Remote": "ALL",
            "Hybrid": "ALL",
            "Onsite": None,
        },
    },
    3: {
        "parent_col":          2,
        "parent_type":         "dropdown",
        "chain":               "A",
        "depth":               3,
        "child_type":          "dropdown",
        "child_option_filter": {
            "Home Office":      "ALL",
            "Co-working Space": None,
        },
    },
    4: {
        "parent_col":          3,
        "parent_type":         "dropdown",
        "chain":               "A",
        "depth":               4,
        "child_type":          "dropdown",
        "child_option_filter": {
            "Dedicated Room": "ALL",
            "Shared Room":    None,
        },
    },
    5: {
        "parent_col":          4,
        "parent_type":         "dropdown",
        "chain":               "A",
        "depth":               5,
        "child_type":          "text",
        "child_option_filter": {
            "Personal":         "ALL",
            "Company-provided": None,
        },
    },
    7: {
        "parent_col":          6,
        "parent_type":         "dropdown",
        "chain":               "B",
        "depth":               2,
        "child_type":          "dropdown",
        "child_option_filter": {
            "Engineering": "ALL",
            "Design":      None,
            "Management":  None,
        },
    },
    8: {
        "parent_col":          7,
        "parent_type":         "dropdown",
        "chain":               "B",
        "depth":               3,
        "child_type":          "multiselect",
        "child_option_filter": {
            "DevOps":   frozenset({"Docker", "Kubernetes", "Terraform", "CI/CD Pipelines"}),
            "Backend":  frozenset({"Docker", "Kubernetes"}),
            "Frontend": None,
        },
    },
}

DEPENDENCY_CHAINS: list = [
    {
        "chain_id": "A",
        "root_col": 1,
        "links": [
            {"parent_col": 1, "child_col": 2, "level": 1},
            {"parent_col": 2, "child_col": 3, "level": 2},
            {"parent_col": 3, "child_col": 4, "level": 3},
            {"parent_col": 4, "child_col": 5, "level": 4},
        ],
    },
    {
        "chain_id": "B",
        "root_col": 6,
        "links": [
            {"parent_col": 6, "child_col": 7, "level": 1},
            {"parent_col": 7, "child_col": 8, "level": 2},
        ],
    },
]

COVERAGE_SCENARIOS: list = [

    {"sid": "A2-REM-V",  "parent_chain": [{"col": 1, "value": "Remote"}],
     "child_col": 2, "forced_child_value": "Home Office",    "scenario_type": "valid"},
    {"sid": "A2-HYB-V",  "parent_chain": [{"col": 1, "value": "Hybrid"}],
     "child_col": 2, "forced_child_value": "Co-working Space", "scenario_type": "valid"},
    {"sid": "A2-ONS-I",  "parent_chain": [{"col": 1, "value": "Onsite"}],
     "child_col": 2, "forced_child_value": "Home Office",    "scenario_type": "invalid"},
    {"sid": "MP-COL2",   "parent_chain": [],
     "child_col": 2, "forced_child_value": "Home Office",    "scenario_type": "missing_parent"},

    {"sid": "A3-HO-V",
     "parent_chain": [{"col": 1, "value": "Remote"},  {"col": 2, "value": "Home Office"}],
     "child_col": 3, "forced_child_value": "Dedicated Room", "scenario_type": "valid"},
    {"sid": "A3-HO-V2",
     "parent_chain": [{"col": 1, "value": "Hybrid"},  {"col": 2, "value": "Home Office"}],
     "child_col": 3, "forced_child_value": "Shared Room",    "scenario_type": "valid"},
    {"sid": "A3-CW-I",
     "parent_chain": [{"col": 1, "value": "Hybrid"},  {"col": 2, "value": "Co-working Space"}],
     "child_col": 3, "forced_child_value": "Dedicated Room", "scenario_type": "invalid"},
    {"sid": "A3-ONS-I",  "parent_chain": [{"col": 1, "value": "Onsite"}],
     "child_col": 3, "forced_child_value": "Dedicated Room", "scenario_type": "invalid"},
    {"sid": "MP-COL3",   "parent_chain": [],
     "child_col": 3, "forced_child_value": "Dedicated Room", "scenario_type": "missing_parent"},

    {"sid": "A4-DR-V",
     "parent_chain": [{"col": 1, "value": "Remote"}, {"col": 2, "value": "Home Office"},
                      {"col": 3, "value": "Dedicated Room"}],
     "child_col": 4, "forced_child_value": "Personal",        "scenario_type": "valid"},
    {"sid": "A4-DR-V2",
     "parent_chain": [{"col": 1, "value": "Hybrid"}, {"col": 2, "value": "Home Office"},
                      {"col": 3, "value": "Dedicated Room"}],
     "child_col": 4, "forced_child_value": "Company-provided", "scenario_type": "valid"},
    {"sid": "A4-SR-I",
     "parent_chain": [{"col": 1, "value": "Remote"}, {"col": 2, "value": "Home Office"},
                      {"col": 3, "value": "Shared Room"}],
     "child_col": 4, "forced_child_value": "Personal",        "scenario_type": "invalid"},
    {"sid": "A4-CW-I",
     "parent_chain": [{"col": 1, "value": "Hybrid"}, {"col": 2, "value": "Co-working Space"}],
     "child_col": 4, "forced_child_value": "Company-provided", "scenario_type": "invalid"},
    {"sid": "MP-COL4",   "parent_chain": [],
     "child_col": 4, "forced_child_value": "Personal",        "scenario_type": "missing_parent"},

    {"sid": "A5-FULL-V",
     "parent_chain": [{"col": 1, "value": "Remote"}, {"col": 2, "value": "Home Office"},
                      {"col": 3, "value": "Dedicated Room"}, {"col": 4, "value": "Personal"}],
     "child_col": 5, "forced_child_value": None, "scenario_type": "valid"},
    {"sid": "A5-COMP-I",
     "parent_chain": [{"col": 1, "value": "Hybrid"}, {"col": 2, "value": "Home Office"},
                      {"col": 3, "value": "Dedicated Room"}, {"col": 4, "value": "Company-provided"}],
     "child_col": 5, "forced_child_value": None, "scenario_type": "invalid"},
    {"sid": "A5-SR-I",
     "parent_chain": [{"col": 1, "value": "Remote"}, {"col": 2, "value": "Home Office"},
                      {"col": 3, "value": "Shared Room"}],
     "child_col": 5, "forced_child_value": None, "scenario_type": "invalid"},
    {"sid": "A5-ONS-I",  "parent_chain": [{"col": 1, "value": "Onsite"}],
     "child_col": 5, "forced_child_value": None, "scenario_type": "invalid"},
    {"sid": "MP-COL5",   "parent_chain": [],
     "child_col": 5, "forced_child_value": None, "scenario_type": "missing_parent"},


    {"sid": "B2-ENG-DEV-V", "parent_chain": [{"col": 6, "value": "Engineering"}],
     "child_col": 7, "forced_child_value": "DevOps",   "scenario_type": "valid"},
    {"sid": "B2-ENG-BKD-V", "parent_chain": [{"col": 6, "value": "Engineering"}],
     "child_col": 7, "forced_child_value": "Backend",  "scenario_type": "valid"},
    {"sid": "B2-ENG-FRD-V", "parent_chain": [{"col": 6, "value": "Engineering"}],
     "child_col": 7, "forced_child_value": "Frontend", "scenario_type": "valid"},
    {"sid": "B2-DES-I",     "parent_chain": [{"col": 6, "value": "Design"}],
     "child_col": 7, "forced_child_value": "Backend",  "scenario_type": "invalid"},
    {"sid": "B2-MGT-I",     "parent_chain": [{"col": 6, "value": "Management"}],
     "child_col": 7, "forced_child_value": "DevOps",   "scenario_type": "invalid"},
    {"sid": "MP-COL7",      "parent_chain": [],
     "child_col": 7, "forced_child_value": "DevOps",   "scenario_type": "missing_parent"},

    {"sid": "B3-DEV-V",
     "parent_chain": [{"col": 6, "value": "Engineering"}, {"col": 7, "value": "DevOps"}],
     "child_col": 8, "forced_child_value": ["Docker", "Kubernetes", "Terraform"],
     "scenario_type": "valid"},
    {"sid": "B3-BKD-V",
     "parent_chain": [{"col": 6, "value": "Engineering"}, {"col": 7, "value": "Backend"}],
     "child_col": 8, "forced_child_value": ["Docker", "Kubernetes"],
     "scenario_type": "valid"},
    {"sid": "B3-BKD-OPT-I",
     "parent_chain": [{"col": 6, "value": "Engineering"}, {"col": 7, "value": "Backend"}],
     "child_col": 8, "forced_child_value": ["Terraform", "CI/CD Pipelines"],
     "scenario_type": "invalid"},
    {"sid": "B3-FRD-I",
     "parent_chain": [{"col": 6, "value": "Engineering"}, {"col": 7, "value": "Frontend"}],
     "child_col": 8, "forced_child_value": ["Docker"], "scenario_type": "invalid"},
    {"sid": "B3-DES-I",     "parent_chain": [{"col": 6, "value": "Design"}],
     "child_col": 8, "forced_child_value": ["Docker"], "scenario_type": "invalid"},
    {"sid": "MP-COL8",      "parent_chain": [],
     "child_col": 8, "forced_child_value": ["Kubernetes"], "scenario_type": "missing_parent"},
]

_TRUE_SET  = {"1"}
_FALSE_SET = {"0"}


def normalize_checkbox(val) -> str:
    v = str(val).strip()
    if v in _TRUE_SET:
        return "1"
    if v in _FALSE_SET:
        return "0"
    return v


def parse_multivalue(val) -> list:
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
    if is_empty(val):
        return None
    allowed_stripped = {str(o).strip() for o in allowed_opts}
    if field_type == "dropdown":
        return val if str(val).strip() in allowed_stripped else None
    parts = parse_multivalue(val)
    kept  = [p for p in parts if p in allowed_stripped]
    return kept if kept else None

_TEXT_DEFAULTS: dict = {
    5:  "MacBook Pro 16-inch, 32 GB RAM, 1 TB SSD, external 4K monitor",
    10: "$120,000 per year or $90/hour on a contract basis",
}


def _default_text_value(child_col: int) -> str:
    return _TEXT_DEFAULTS.get(child_col, "relevant information")

def get_field_path(col: int, by_col: dict | None = None) -> str:
    """Return the ancestor chain as 'L1 Name > L2 Name > … > Leaf Name'."""
    lookup  = by_col if by_col else FIELD_BY_COL
    path    = []
    current = col
    visited: set = set()
    while current not in visited:
        visited.add(current)
        field = lookup.get(current)
        if not field:
            break
        path.insert(0, field["name"])
        rule = DEPENDENCY_RULES.get(current)
        if rule is None:
            break
        current = rule["parent_col"]
    return " > ".join(path) if len(path) > 1 else (path[0] if path else f"col{col}")


def _get_ancestor_chain(child_col: int) -> list:
    """
    Return an ordered list of (col_to_check, rule) starting from the
    shallowest dependent node down to child_col itself.

    Example for child_col=5 (depth 5 in chain A):
      [(2, rule2), (3, rule3), (4, rule4), (5, rule5)]
    Each (col, rule) means: rule.parent_col's parsed value controls col's visibility.
    """
    chain: list = []
    col = child_col
    visited: set = set()
    while col not in visited:
        rule = DEPENDENCY_RULES.get(col)
        if rule is None:
            break
        chain.insert(0, (col, rule))
        visited.add(col)
        col = rule["parent_col"]
    return chain


def _get_parent_keys(parent_val, parent_type: str) -> list:
    if is_empty(parent_val):
        return []
    if parent_type == "checkbox":
        return [normalize_checkbox(parent_val)]
    if parent_type == "dropdown":
        return [str(parent_val).strip()]
    return parse_multivalue(parent_val)


def apply_dependency_chain(
    child_col:        int,
    parsed_custom:    dict,
    gt_val,
    parsed_val,
    child_field_type: str,
) -> tuple:
    """
    Walk the full ancestor chain of child_col.

    Steps
    ─────
    1. For every ancestor (from root+1 down to child_col), verify that its
       parent's parsed value makes IT visible.  If any link is broken the
       child is hidden → return (None, None, False, False).
    2. Once all ancestors are confirmed visible, apply option-level filtering
       at the immediate parent level (same logic as the existing pipeline).

    Returns
    ───────
    (expected_val, filtered_val, dep_met: bool, option_filtered: bool)
    """
    rule = DEPENDENCY_RULES.get(child_col)
    if rule is None:
        return gt_val, parsed_val, True, False

    ancestor_chain = _get_ancestor_chain(child_col)

    for col_to_check, r in ancestor_chain:
        p_col   = r["parent_col"]
        p_type  = r["parent_type"]
        p_val   = parsed_custom.get(p_col)

        p_keys  = _get_parent_keys(p_val, p_type)
        if not p_keys:
            return None, None, False, False

        fmap    = r["child_option_filter"]
        visible = any(_lookup_filter(fmap, pk) is not None for pk in p_keys)
        if not visible:
            return None, None, False, False

    fmap       = rule["child_option_filter"]
    p_col      = rule["parent_col"]
    p_type     = rule["parent_type"]
    p_val      = parsed_custom.get(p_col)
    p_keys     = _get_parent_keys(p_val, p_type)

    has_all     = False
    allowed_opts: set = set()

    for pk in p_keys:
        mapped = _lookup_filter(fmap, pk)
        if mapped is None:
            continue
        if mapped == "ALL":
            has_all = True
        elif isinstance(mapped, frozenset):
            allowed_opts.update(mapped)

    if child_field_type not in ("dropdown", "multiselect") or has_all:
        return gt_val, parsed_val, True, False

    opts_frozen  = frozenset(allowed_opts)
    expected_val = _filter_value_to_allowed(gt_val,     opts_frozen, child_field_type)
    filtered_val = _filter_value_to_allowed(parsed_val, opts_frozen, child_field_type)

    raw_parts  = parse_multivalue(parsed_val) if child_field_type == "multiselect" else []
    filt_parts = (
        parse_multivalue(filtered_val)
        if isinstance(filtered_val, list)
        else ([str(filtered_val).strip()] if filtered_val else [])
    )
    option_filtered = (
        not is_empty(parsed_val)
        and len(filt_parts) < len(raw_parts or [str(parsed_val).strip()])
    )
    return expected_val, filtered_val, True, option_filtered


def _get_allowed_disallowed(
    parent_col:   int | None,
    parent_value,
    child_col:    int,
    manifest:     list,
) -> tuple:
    """Return (allowed_child_opts, disallowed_child_opts) for the immediate parent."""
    rule        = DEPENDENCY_RULES.get(child_col)
    child_field = next((f for f in manifest if f["column_index"] == child_col), None)
    if not rule or not child_field:
        return [], []

    all_opts    = child_field.get("options", [])
    filter_map  = rule["child_option_filter"]
    parent_type = rule["parent_type"]

    if parent_value is None:
        return [], list(all_opts)

    pkeys = _get_parent_keys(parent_value, parent_type)

    allowed: set = set()
    has_all       = False
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
        "text":           "text",
        "longtext":       "longtext",
        "number":         "number",
        "multiselect":    "multiselect",
        "dropdown":       "dropdown",
        "date":           "date",
        "datetime":       "datetime",
        "checkbox":       "checkbox",
        "phone":          "phone",
        "phonenumber":    "phone",
        "email":          "email",
        "social_profile": "socialprofile",
        "date_time":      "datetime",
    }.get(ftype, "text")


def create_deep_nested_fields(token: str) -> list:
    """POST each field to /v1/custom-fields.  Returns manifest with field_id + option_map."""
    print(f"\n[Setup] Creating {len(DEEP_NESTED_FIELDS)} custom fields ...")
    created = []
    for field in DEEP_NESTED_FIELDS:
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


def _store_one_dependency(
    token, entity_id, root_fid, parent_fid, child_fid, mappings, level: int = 1
) -> None:
    """
    POST one dependency link.

    Per the API spec:
      level 1  → dependency_id and parent_id are int
      level ≥2 → dependency_id and parent_id are str
    child_id is always int.
    dependency_id is ALWAYS the chain root (L1) field ID.
    """
    payload = {
        "entity":        str(entity_id),
        "level":         level,
        "dependency_id": root_fid if level == 1 else str(root_fid),
        "parent_id":     parent_fid if level == 1 else str(parent_fid),
        "child_id":      child_fid,
        "mappings":      mappings,
    }
    r = requests.post(
        f"{BASE_URL_SS}/v1/nested-custom-fields/store",
        headers=_auth_headers(token), json=payload,
    )
    r.raise_for_status()
    msg = r.json().get("message", "OK")
    print(
        f"    dep  level={level}  dep={root_fid}  parent={parent_fid} → child={child_fid}: {msg}"
    )
    log.info(f"[Setup] dep level={level} root={root_fid} parent={parent_fid} child={child_fid}")
    time.sleep(0.3)


def create_all_dependencies(token: str, manifest: list) -> None:
    """
    Iterate DEPENDENCY_CHAINS and store all dependency links.

    Mapping strategy (per link):
      - text/number child (no option IDs)
          → child_value_id=null, child_visibility=true
      - dropdown/multiselect child where allowed == "ALL"
          → one row per child option (child_value_id=cid, child_visibility=null)
          WHY: The API needs explicit option IDs to actually tick the checkboxes in
               the Edit Dependency UI.  Sending child_visibility=true with no
               child_value_id only works for option-less (text/number) fields.
      - dropdown/multiselect child with specific frozenset
          → one row per allowed child option (child_value_id=cid, child_visibility=null)
      - None  → row omitted (absence = hidden)
    """
    by_col = {f["column_index"]: f for f in manifest}
    print(f"\n[Setup] Storing dependency links for {len(DEPENDENCY_CHAINS)} chain(s) ...")

    for chain_def in DEPENDENCY_CHAINS:
        chain_id = chain_def["chain_id"]
        root_col = chain_def["root_col"]
        root_fid = by_col[root_col]["field_id"]
        print(f"\n  Chain {chain_id}  (root_col={root_col}, root_fid={root_fid})")

        for link in chain_def["links"]:
            parent_col = link["parent_col"]
            child_col  = link["child_col"]
            dep_level  = link["level"]

            parent = by_col.get(parent_col)
            child  = by_col.get(child_col)
            rule   = DEPENDENCY_RULES.get(child_col)

            if not parent or not child or not rule:
                print(f"    SKIP col{child_col}: missing manifest entry or rule")
                continue

            parent_opt_map = parent.get("option_map", {})
            child_opt_map  = child.get("option_map", {})
            filter_map     = rule["child_option_filter"]
            parent_type    = rule["parent_type"]
            mappings       = []

            for parent_label, allowed in filter_map.items():
                if allowed is None:
                    continue

                if parent_type == "checkbox":
                    _TRUE_LABELS = {"yes", "true", "1", "checked"}
                    pid = next(
                        (oid for lbl, oid in parent_opt_map.items()
                         if str(lbl).lower().strip() in _TRUE_LABELS),
                        None,
                    )
                else:
                    pid = next(
                        (oid for lbl, oid in parent_opt_map.items()
                         if str(lbl).strip() == str(parent_label).strip()),
                        None,
                    )

                if pid is None:
                    print(f"    WARN  parent option '{parent_label}' not found in '{parent['name']}'")
                    continue

                if not child.get("options"):
                    mappings.append({
                        "parent_value_id":  pid,
                        "child_value_id":   None,
                        "child_visibility": True,
                    })
                elif allowed == "ALL":
                    for clabel, cid in child_opt_map.items():
                        if cid:
                            mappings.append({
                                "parent_value_id":  pid,
                                "child_value_id":   cid,
                                "child_visibility": None,
                            })
                    if not child_opt_map:
                        log.warning(
                            "[Setup] ALL mapping for col%d but option_map empty; "
                            "falling back to child_visibility=True", child_col
                        )
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
                    token, ENTITY_TYPE_ID,
                    root_fid, parent["field_id"], child["field_id"],
                    mappings, dep_level,
                )
            else:
                print(f"    SKIP col{child_col} in chain {chain_id}: no valid mappings")


def save_manifest(manifest: list) -> None:
    MANIFEST_FILE.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[Setup] Manifest saved → {MANIFEST_FILE}")


def load_manifest() -> list:
    if not MANIFEST_FILE.is_file():
        raise FileNotFoundError(
            f"Manifest not found: {MANIFEST_FILE}\n"
            "Run with --setup to create fields first."
        )
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def _fetch_default_options(token: str) -> dict:
    r = requests.get(
        f"{BASE_URL_SS}/v1/custom-fields/get-default-options/{ENTITY_TYPE_ID}",
        headers=_auth_headers(token),
    )
    r.raise_for_status()
    return r.json().get("data", {})


def refresh_option_maps_from_api(token: str, manifest: list) -> list:
    print("[Setup] Refreshing option IDs from get-default-options ...")
    raw     = _fetch_default_options(token)
    updated = []
    for field in manifest:
        fid_str = str(field.get("field_id", ""))
        opts    = raw.get(fid_str, [])
        if opts:
            option_map = {o["label"]: o["id"] for o in opts}
            updated.append({**field, "option_map": option_map})
            print(f"  Refreshed  '{field['name']}' → {len(option_map)} option(s): {list(option_map.keys())}")
        else:
            updated.append(field)
    return updated


def _val_str(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def generate_controlled_snippet(
    field:                   dict,
    forced_value,
    scenario_type:           str,
    parent_field_name:       str  = None,
    parent_value                  = None,
    allowed_child_values:    list = None,
    disallowed_child_values: list = None,
) -> dict:
    """
    Generate one natural resume sentence encoding the requested scenario.
    Returns {"snippet": str, "value": forced_value}.

    scenario_type
    ─────────────
    "normal"         → root/ancestor field: just mention the forced value
    "valid"          → chain intact + child value is within allowed options
    "invalid"        → chain broken OR forced_value is a disallowed option
    "missing_parent" → inject child only; do NOT mention any parent
    """
    fname  = field["name"]
    fdesc  = field.get("description", "")
    fv_str = _val_str(forced_value) if forced_value is not None else ""
    pv_str = _val_str(parent_value) if parent_value is not None else ""
    ret_hint = json.dumps({"snippet": "...", "value": forced_value}, ensure_ascii=False)

    if scenario_type == "normal":
        prompt = (
            f"Write exactly 1 natural resume sentence that clearly (but not obviously) "
            f"conveys the following fact.\n"
            f"Field: {fname}\nDescription: {fdesc}\nValue: {fv_str}\n"
            f"No label:value format. Be concise and professional.\n"
            f"Return ONLY valid JSON: {ret_hint}"
        )
    elif scenario_type == "valid":
        allowed_hint = (
            f"Allowed values for {fname}: {', '.join(allowed_child_values)}."
            if allowed_child_values else ""
        )
        prompt = (
            f"Write 1–2 natural resume sentences that convey BOTH facts in a single "
            f"flowing paragraph (no bullets, no labels).\n"
            f"1. {parent_field_name or 'Work context'}: {pv_str}\n"
            f"2. {fname}: {fv_str}\n"
            f"Description of {fname}: {fdesc}\n{allowed_hint}\n"
            f"Be brief and professional. Connection should feel natural.\n"
            f"Return ONLY valid JSON: {ret_hint}"
        )
    elif scenario_type == "invalid":
        parent_clause = (
            f"Also mention {parent_field_name or 'the parent preference'} as {pv_str}. "
            if parent_value is not None else ""
        )
        disallowed_hint = (
            f"Note: {fv_str} is intentionally outside the typical allowed set."
            if disallowed_child_values else ""
        )
        prompt = (
            f"Write 1–2 natural resume sentences mentioning {fname} as {fv_str}. "
            f"{parent_clause}{disallowed_hint}\n"
            f"Description of {fname}: {fdesc}\nNo field labels.\n"
            f"Return ONLY valid JSON: {ret_hint}"
        )
    elif scenario_type == "missing_parent":
        avoid = f"Do NOT mention {parent_field_name}." if parent_field_name else ""
        prompt = (
            f"Write exactly 1 natural resume sentence mentioning {fname} as {fv_str}. "
            f"{avoid}\nDescription: {fdesc}\nNo field labels.\n"
            f"Return ONLY valid JSON: {ret_hint}"
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
    chain_counts:         dict,
) -> None:
    print("\n" + "─" * 70)
    print("[Coverage] Scenario distribution:")
    for sid, cnt in sorted(coverage_counts.items()):
        bar = "▓" * cnt
        print(f"  {sid:<28s}  ×{cnt:3d}  {bar}")
    print("\n[Coverage] Scenario type totals:")
    for stype in ("valid", "invalid", "missing_parent"):
        cnt = scenario_type_counts.get(stype, 0)
        print(f"  {stype:<22s}  {cnt:4d}")
    print("\n[Coverage] Chain totals:")
    for chain_id, cnt in sorted(chain_counts.items()):
        print(f"  Chain {chain_id}  {cnt:4d} scenarios")
    print("─" * 70)
    log.info("[Coverage] %s", json.dumps(
        {"scenario_counts": coverage_counts, "type_counts": scenario_type_counts,
         "chain_counts": chain_counts}, default=str))


def generate_deep_ground_truth(baseline: list, manifest: list) -> list:
    """
    Controlled GT: one COVERAGE_SCENARIO per resume (round-robin).

    For each resume:
      1. Inject snippets for every field in the scenario's parent_chain.
      2. Inject snippet for the focal child_col.
      3. Inject independent fields as usual.
    """
    valid       = [r for r in baseline if "error" not in r]
    by_col      = {f["column_index"]: f for f in manifest}
    n_scenarios = len(COVERAGE_SCENARIOS)

    print(
        f"\n[LLM] Deep nested GT: {len(valid)} resumes, "
        f"{n_scenarios} scenarios (round-robin) ..."
    )

    all_gts: list = []
    coverage_counts:      dict = {s["sid"]: 0 for s in COVERAGE_SCENARIOS}
    scenario_type_counts: dict = {"valid": 0, "invalid": 0, "missing_parent": 0}
    chain_counts:         dict = {}

    for idx, record in enumerate(valid):
        rid         = record["resume_id"]
        resume_text = record.get("resume_text", "")
        scenario    = COVERAGE_SCENARIOS[idx % n_scenarios]

        sid          = scenario["sid"]
        stype        = scenario["scenario_type"]
        parent_chain = scenario.get("parent_chain", [])
        child_col    = scenario["child_col"]
        forced_cv    = scenario.get("forced_child_value")

        coverage_counts[sid]        = coverage_counts.get(sid, 0) + 1
        scenario_type_counts[stype] = scenario_type_counts.get(stype, 0) + 1

        child_field_def = FIELD_BY_COL.get(child_col, {})
        chain_id        = child_field_def.get("chain", "?")
        chain_counts[chain_id] = chain_counts.get(chain_id, 0) + 1

        print(
            f"  [{idx + 1:3d}/{len(valid)}] {rid}  scenario={sid} ...",
            end=" ", flush=True,
        )
        injected: list = []

        for pc in parent_chain:
            pcol   = pc["col"]
            pval   = pc["value"]
            pfield = by_col.get(pcol)
            if not pfield:
                continue
            try:
                res = generate_controlled_snippet(pfield, pval, "normal",
                                                  parent_field_name=pfield["name"])
                if res["snippet"]:
                    injected.append({
                        "field_id":      pfield.get("field_id", ""),
                        "field_name":    pfield["name"],
                        "field_type":    pfield["type"],
                        "column_index":  pcol,
                        "role":          pfield.get("role", "l1_root"),
                        "depth":         pfield.get("depth", 1),
                        "chain":         pfield.get("chain"),
                        "field_path":    get_field_path(pcol, by_col),
                        "value":         pval,
                        "snippet":       res["snippet"],
                        "scenario_type": "normal",
                        "scenario_id":   sid,
                    })
            except Exception as exc:
                log.warning("[LLM] %s parent chain col%d: %s", rid, pcol, exc)

        child_field = by_col.get(child_col)
        if child_field:
            child_ftype = child_field["type"]

            if forced_cv is None:
                if child_ftype == "text":
                    forced_cv = _default_text_value(child_col)
                elif child_ftype == "number":
                    forced_cv = 5

            if forced_cv is not None:
                imm_parent_col = child_field.get("parent_col")
                imm_parent_fld = by_col.get(imm_parent_col) if imm_parent_col else None
                imm_parent_val = next(
                    (pc["value"] for pc in parent_chain if pc["col"] == imm_parent_col),
                    None,
                ) if parent_chain else None

                allowed, disallowed = _get_allowed_disallowed(
                    imm_parent_col, imm_parent_val, child_col, manifest
                )
                try:
                    res = generate_controlled_snippet(
                        child_field, forced_cv, stype,
                        parent_field_name=imm_parent_fld["name"] if imm_parent_fld else None,
                        parent_value=imm_parent_val,
                        allowed_child_values=allowed,
                        disallowed_child_values=disallowed,
                    )
                    if res["snippet"]:
                        gt_val = forced_cv
                        if child_ftype == "checkbox":
                            v = str(gt_val).strip()
                            gt_val = "1" if v in {"1", "true", "yes"} else "0"
                        injected.append({
                            "field_id":      child_field.get("field_id", ""),
                            "field_name":    child_field["name"],
                            "field_type":    child_ftype,
                            "column_index":  child_col,
                            "role":          child_field.get("role", "l2_child"),
                            "depth":         child_field.get("depth", 2),
                            "chain":         child_field.get("chain"),
                            "field_path":    get_field_path(child_col, by_col),
                            "value":         gt_val,
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
                        "depth":         0,
                        "chain":         None,
                        "field_path":    field["name"],
                        "value":         gt_val,
                        "snippet":       res["snippet"],
                        "scenario_type": "normal",
                        "scenario_id":   sid,
                    })
            except Exception as exc:
                log.warning("[LLM] %s independent col%d: %s", rid, field["column_index"], exc)

        all_gts.append({
            "resume_id":       rid,
            "scenario_id":     sid,
            "scenario_type":   stype,
            "parent_chain":    parent_chain,
            "child_col":       child_col,
            "injected_fields": injected,
        })
        print(f"{len(injected)} injected  OK")
        time.sleep(0.3)

    _log_coverage(coverage_counts, scenario_type_counts, chain_counts)
    GT_FILE.write_text(json.dumps(all_gts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[LLM] Ground truth saved → {GT_FILE}")
    return all_gts


def run_deep_track_b(token: str, baseline: list, ground_truth_all: list) -> list:
    gt_by_id = {g["resume_id"]: g for g in ground_truth_all}
    valid    = [r for r in baseline if "error" not in r]
    all_cols = [f["column_index"] for f in DEEP_NESTED_FIELDS]
    results  = []

    print(f"\n[Track B / Deep Nested] Parsing {len(valid)} resume(s) ...")

    for record in valid:
        rid         = record["resume_id"]
        resume_text = record.get("resume_text", "")
        injected    = gt_by_id.get(rid, {}).get("injected_fields", [])
        pdf_name    = f"deep_nested_{rid}.pdf"

        print(f"  [{rid}] ...", end=" ", flush=True)
        try:
            modified_text = inject_snippets_individually(resume_text, injected)
            pdf_bytes     = text_to_pdf_bytes(modified_text)
            local_pdf     = INJECTED_PDF_DIR / pdf_name
            local_pdf.write_bytes(pdf_bytes)
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
            log.info(f"[Deep Track B] {rid} OK")
        except Exception as exc:
            print(f"ERROR — {exc}")
            log.error(f"[Deep Track B] {rid} failed: {exc}")
            results.append({"resume_id": rid, "error": str(exc)})
        time.sleep(1)

    RESULTS_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[Track B] Results saved → {RESULTS_FILE}")
    return results


def classify_nested(
    expected_val,
    filtered_val,
    raw_parsed_val,
    field_type: str,
) -> tuple:
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
            normalize_option_list(filtered_val) if not is_empty(filtered_val) else []
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
    if val is None:
        return ""
    if isinstance(val, list):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def run_deep_validation(results: list, manifest: list) -> pd.DataFrame:
    """
    For every (resume × field) pair that was actively tested:
      1. Build parsed_custom dict for the full chain lookup.
      2. For chain fields: apply_dependency_chain() → expected + filtered.
      3. classify_nested() → classification.
    Adds field_path, depth, chain, scenario columns.
    """
    by_col = {f["column_index"]: f for f in manifest}
    rows   = []

    for res in results:
        if "error" in res:
            rows.append({
                "resume_id":         res["resume_id"],
                "scenario_id":       "",
                "scenario_type":     "",
                "expected_outcome":  "",
                "field_path":        "–",
                "chain":             "–",
                "depth":             "–",
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

        gt_by_col:    dict = {}
        ftype_by_col: dict = {}
        stype_by_col: dict = {}
        sid_by_col:   dict = {}
        path_by_col:  dict = {}
        role_by_col:  dict = {}

        for inj in res.get("injected_fields", []):
            c                = inj["column_index"]
            gt_by_col[c]     = inj["value"]
            ftype_by_col[c]  = inj["field_type"]
            stype_by_col[c]  = inj.get("scenario_type", "normal")
            sid_by_col[c]    = inj.get("scenario_id", res_sid)
            path_by_col[c]   = inj.get("field_path", f"col{c}")
            role_by_col[c]   = inj.get("role", "independent")

        for field in manifest:
            col   = field["column_index"]
            fname = field["name"]
            ftype = field["type"]
            role  = field.get("role", "independent")
            depth = field.get("depth", 0)
            chain = field.get("chain")

            gt_val     = gt_by_col.get(col)
            parsed_val = parsed_custom.get(col)

            if gt_val is None:
                continue

            col_stype = stype_by_col.get(col, "normal")
            col_sid   = sid_by_col.get(col, res_sid)
            fpath     = path_by_col.get(col) or get_field_path(col, by_col)

            is_root_or_independent = role in ("l1_root", "independent")

            if is_root_or_independent:
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
                    apply_dependency_chain(col, parsed_custom, gt_val, parsed_val, ftype)

                rule           = DEPENDENCY_RULES.get(col)
                parent_col     = rule["parent_col"] if rule else None
                parent_parsed  = parsed_custom.get(parent_col) if parent_col else None
                parent_display = str(parent_parsed) if parent_parsed is not None else "(not parsed)"

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
                "N/A" if is_root_or_independent
                else ("Met" if dep_met else "Not Met")
            )

            rows.append({
                "resume_id":         rid,
                "scenario_id":       col_sid,
                "scenario_type":     col_stype,
                "expected_outcome":  exp_outcome,
                "field_path":        fpath,
                "chain":             chain or "–",
                "depth":             depth,
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


def build_deep_report(df: pd.DataFrame) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    CLS_FILL = {
        "Exact Match":          "D6EFD8",
        "Both Empty":           "E8E8E8",
        "Partial Match (+ve)":  "FFF3CD",
        "Partial Match (-ve)":  "F4CCCC",
        "Missed":               "FADADD",
        "Mismatch":             "FADADD",
        "Hallucination":        "F4CCCC",
        "Dependency Violation": "FF6B6B",
        "ERROR":                "F4CCCC",
    }
    STYPE_FILL = {
        "valid":          "D6EFD8",
        "invalid":        "FADADD",
        "missing_parent": "FFF3CD",
        "normal":         "FFFFFF",
    }

    HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
    COV_HDR     = PatternFill("solid", fgColor="274E13")
    HEADER_FONT = Font(color="FFFFFF", bold=True, size=11, name="Calibri")
    DATA_FONT   = Font(size=10, name="Calibri")
    DATA_BOLD   = Font(size=10, bold=True, name="Calibri")
    THIN        = Side(border_style="thin", color="C0C0C0")
    BORDER      = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    COLS = [
        "resume_id", "scenario_id", "scenario_type", "expected_outcome",
        "field_path", "chain", "depth",
        "field_name", "field_type", "column_index", "role",
        "parent_value", "parsed_value", "filtered_value", "expected_value",
        "classification", "details", "dependency_status",
    ]
    COL_WIDTHS = [
        12, 26, 18, 18,
        55, 8, 8,
        32, 14, 14, 14,
        22, 26, 26, 26,
        24, 55, 14,
    ]

    safe_df = df.apply(lambda col: col.map(_sanitize))
    wb  = Workbook()
    ws1 = wb.active
    ws1.title       = "Full Detail"
    ws1.freeze_panes = "A2"
    ws1.sheet_view.showGridLines = False

    for ci, (cname, width) in enumerate(zip(COLS, COL_WIDTHS), start=1):
        cell = ws1.cell(row=1, column=ci, value=cname.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = BORDER
        ws1.column_dimensions[get_column_letter(ci)].width = width
    ws1.row_dimensions[1].height = 32

    sub_df      = safe_df.reindex(columns=COLS).fillna("")
    resume_ids  = sub_df["resume_id"].unique() if not sub_df.empty else []
    current_row = 2
    fill_toggle = True
    BAND_A = PatternFill("solid", fgColor="EBF3FB")
    BAND_B = PatternFill("solid", fgColor="FFFFFF")

    for rid in resume_ids:
        block = sub_df[sub_df["resume_id"] == rid]
        band  = BAND_A if fill_toggle else BAND_B
        fill_toggle = not fill_toggle
        for _, data_row in block.iterrows():
            cls      = data_row.get("classification", "")
            cls_fill = PatternFill("solid", fgColor=CLS_FILL.get(cls, "FFFFFF"))
            for ci, cname in enumerate(COLS, start=1):
                val  = data_row.get(cname, "")
                cell = ws1.cell(row=current_row, column=ci,
                                value="" if val is None else val)
                cell.border    = BORDER
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if cname == "classification":
                    cell.fill = cls_fill
                    cell.font = DATA_BOLD
                elif cname == "scenario_type":
                    cell.fill = PatternFill("solid",
                                            fgColor=STYPE_FILL.get(str(val), "FFFFFF"))
                    cell.font = DATA_FONT
                else:
                    cell.fill = band
                    cell.font = DATA_FONT
            ws1.row_dimensions[current_row].height = 18
            current_row += 1
        current_row += 2

    ws2 = wb.create_sheet("Field Summary")
    ws2.sheet_view.showGridLines = False

    sum_cols = [
        "field_path", "chain", "depth", "field_name", "field_type",
        "Total", "Exact Match", "Exact Match %",
        "Dependency Violation", "Dep Viol %",
        "Missed", "Missed %",
        "Mismatch", "Mismatch %",
        "Partial Match (+ve)", "Partial Match (-ve)",
        "Both Empty", "Hallucination",
    ]
    for ci, h in enumerate(sum_cols, start=1):
        cell = ws2.cell(row=1, column=ci, value=h)
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border    = BORDER
        ws2.column_dimensions[get_column_letter(ci)].width = (
            50 if h == "field_path" else 18 if h in ("field_name",) else 12
        )
    ws2.row_dimensions[1].height = 30

    if not safe_df.empty and "field_name" in safe_df.columns:
        for ri, (fp, grp) in enumerate(safe_df.groupby("field_path", sort=False), start=2):
            first      = grp.iloc[0]
            total      = len(grp)
            cls_counts = grp["classification"].value_counts().to_dict()

            def pct(cls):
                return round(cls_counts.get(cls, 0) / total * 100, 1) if total else 0.0

            row_data = [
                fp, first.get("chain", ""), first.get("depth", ""),
                first.get("field_name", ""), first.get("field_type", ""),
                total,
                cls_counts.get("Exact Match", 0), pct("Exact Match"),
                cls_counts.get("Dependency Violation", 0), pct("Dependency Violation"),
                cls_counts.get("Missed", 0), pct("Missed"),
                cls_counts.get("Mismatch", 0), pct("Mismatch"),
                cls_counts.get("Partial Match (+ve)", 0), cls_counts.get("Partial Match (-ve)", 0),
                cls_counts.get("Both Empty", 0), cls_counts.get("Hallucination", 0),
            ]
            alt = PatternFill("solid", fgColor="F2F2F2" if ri % 2 == 0 else "FFFFFF")
            for ci, val in enumerate(row_data, start=1):
                cell = ws2.cell(row=ri, column=ci, value=val)
                cell.border    = BORDER
                cell.alignment = Alignment(vertical="center")
                cell.font      = DATA_FONT
                cell.fill      = alt
                if isinstance(val, float) and sum_cols[ci - 1].endswith("%"):
                    if val >= 80:
                        cell.fill = PatternFill("solid", fgColor="D5F5E3")
                        cell.font = Font(size=10, bold=True, color="1E8449", name="Calibri")
                    elif val >= 40:
                        cell.fill = PatternFill("solid", fgColor="FDEBD0")
                        cell.font = Font(size=10, bold=True, color="784212", name="Calibri")
                    else:
                        cell.fill = PatternFill("solid", fgColor="FADBD8")
                        cell.font = Font(size=10, bold=True, color="922B21", name="Calibri")
            ws2.row_dimensions[ri].height = 20

    ws3 = wb.create_sheet("Chain Summary")
    ws3.sheet_view.showGridLines = False

    chain_cols = [
        "chain", "field_name", "depth", "role", "field_type",
        "scenario_type", "Total", "Exact Match", "Dep Violation", "Missed", "Mismatch",
    ]
    for ci, h in enumerate(chain_cols, start=1):
        cell = ws3.cell(row=1, column=ci, value=h)
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border    = BORDER
        ws3.column_dimensions[get_column_letter(ci)].width = 20
    ws3.row_dimensions[1].height = 28

    if not safe_df.empty:
        grp_df = (
            safe_df.groupby(["chain", "field_name", "depth", "role", "field_type",
                              "scenario_type"], sort=True)
            .agg(
                Total=("classification", "count"),
                exact_match=("classification", lambda x: (x == "Exact Match").sum()),
                dep_viol=("classification", lambda x: (x == "Dependency Violation").sum()),
                missed=("classification", lambda x: (x == "Missed").sum()),
                mismatch=("classification", lambda x: (x == "Mismatch").sum()),
            )
            .reset_index()
        )
        for ri, row in enumerate(grp_df.itertuples(index=False), start=2):
            stype    = row.scenario_type
            row_fill = PatternFill("solid", fgColor=STYPE_FILL.get(stype, "FFFFFF"))
            vals     = [
                row.chain, row.field_name, row.depth, row.role, row.field_type,
                stype, row.Total, row.exact_match, row.dep_viol, row.missed, row.mismatch,
            ]
            for ci, val in enumerate(vals, start=1):
                cell = ws3.cell(row=ri, column=ci, value=val)
                cell.border    = BORDER
                cell.alignment = Alignment(vertical="center")
                cell.font      = DATA_FONT
                cell.fill      = row_fill
            ws3.row_dimensions[ri].height = 18

    ws4 = wb.create_sheet("Coverage Summary")
    ws4.sheet_view.showGridLines = False

    cov_cols = [
        "scenario_id", "scenario_type", "chain", "child_col", "child_field_name",
        "field_path", "total_assigned",
        "dep_violations", "exact_matches", "missed", "mismatch",
    ]
    cov_widths = [26, 18, 8, 12, 28, 55, 14, 16, 16, 12, 12]
    for ci, (h, w) in enumerate(zip(cov_cols, cov_widths), start=1):
        cell = ws4.cell(row=1, column=ci, value=h.replace("_", " ").title())
        cell.font      = HEADER_FONT
        cell.fill      = COV_HDR
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border    = BORDER
        ws4.column_dimensions[get_column_letter(ci)].width = w
    ws4.row_dimensions[1].height = 30

    child_name_by_col = {f["column_index"]: f["name"] for f in DEEP_NESTED_FIELDS}
    if not safe_df.empty and "scenario_id" in safe_df.columns:
        for ri, sc in enumerate(COVERAGE_SCENARIOS, start=2):
            sid        = sc["sid"]
            stype      = sc["scenario_type"]
            child_col  = sc["child_col"]
            child_name = child_name_by_col.get(child_col, "")
            chain_id   = FIELD_BY_COL.get(child_col, {}).get("chain", "")
            fpath      = get_field_path(child_col)

            sub = safe_df[
                (safe_df["scenario_id"] == sid)
                & (safe_df["column_index"] == child_col)
            ] if "scenario_id" in safe_df.columns else pd.DataFrame()

            total    = len(sub)
            dv_cnt   = int((sub["classification"] == "Dependency Violation").sum()) if not sub.empty else 0
            em_cnt   = int((sub["classification"] == "Exact Match").sum())           if not sub.empty else 0
            miss_cnt = int((sub["classification"] == "Missed").sum())                if not sub.empty else 0
            mm_cnt   = int((sub["classification"] == "Mismatch").sum())              if not sub.empty else 0

            row_fill = PatternFill("solid", fgColor=STYPE_FILL.get(stype, "FFFFFF"))
            vals = [sid, stype, chain_id, child_col, child_name,
                    fpath, total, dv_cnt, em_cnt, miss_cnt, mm_cnt]
            for ci, val in enumerate(vals, start=1):
                cell = ws4.cell(row=ri, column=ci, value=val)
                cell.font      = DATA_FONT
                cell.fill      = row_fill
                cell.border    = BORDER
                cell.alignment = Alignment(horizontal="left" if ci > 1 else "center")
            ws4.row_dimensions[ri].height = 18

    wb.save(REPORT_FILE)
    print(f"\n[Report] Saved → {REPORT_FILE}")


def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 76)
    print("DEEP NESTED CUSTOM FIELDS — VALIDATION SUMMARY (5-level chain)")
    print("=" * 76)

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
            print(f"{flag}{cls:<40s} {n:5d}  ({n / total * 100:5.1f}%)")

    if "scenario_type" in df.columns:
        print("\n  Breakdown by scenario_type:")
        for stype in ("valid", "invalid", "missing_parent", "normal"):
            sub = df[df["scenario_type"] == stype]
            if sub.empty:
                continue
            n_sub = len(sub)
            em    = (sub["classification"] == "Exact Match").sum()
            dv    = (sub["classification"] == "Dependency Violation").sum()
            ms    = (sub["classification"] == "Missed").sum()
            print(
                f"    {stype:<22s}  n={n_sub:4d}  "
                f"EM={em:4d} ({em/n_sub*100:4.0f}%)  "
                f"DepViol={dv:4d} ({dv/n_sub*100:4.0f}%)  "
                f"Missed={ms:4d} ({ms/n_sub*100:4.0f}%)"
            )

    if "chain" in df.columns:
        print("\n  Breakdown by chain:")
        for chain_id in sorted(df["chain"].unique()):
            sub = df[df["chain"] == chain_id]
            if sub.empty:
                continue
            n_sub = len(sub)
            em    = (sub["classification"] == "Exact Match").sum()
            dv    = (sub["classification"] == "Dependency Violation").sum()
            print(
                f"    Chain {chain_id:<5s}  n={n_sub:4d}  "
                f"EM={em:4d} ({em/n_sub*100:4.0f}%)  "
                f"DepViol={dv:4d} ({dv/n_sub*100:4.0f}%)"
            )

    if "depth" in df.columns:
        print("\n  Breakdown by depth (chain levels):")
        for depth in sorted(df["depth"].unique()):
            sub = df[df["depth"] == depth]
            if sub.empty:
                continue
            n_sub = len(sub)
            em    = (sub["classification"] == "Exact Match").sum()
            dv    = (sub["classification"] == "Dependency Violation").sum()
            label = f"L{depth}" if depth > 0 else "Independent"
            print(
                f"    {label:<12s}  n={n_sub:4d}  "
                f"EM={em:4d} ({em/n_sub*100:4.0f}%)  "
                f"DepViol={dv:4d} ({dv/n_sub*100:4.0f}%)"
            )

    if "scenario_id" in df.columns:
        child_df = df[~df["role"].isin(["l1_root", "independent"])] \
            if "role" in df.columns else pd.DataFrame()
        if not child_df.empty:
            print("\n  Per-scenario results (sid → field_path | n | EM | DepViol | Missed):")
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

    print("\n  Field path breakdown (DepViol / EM / Missed):")
    non_root = df[~df["role"].isin(["l1_root", "independent"])] \
        if "role" in df.columns else pd.DataFrame()
    if not non_root.empty and "field_path" in non_root.columns:
        for fp, grp in non_root.groupby("field_path"):
            n  = len(grp)
            dv = (grp["classification"] == "Dependency Violation").sum()
            em = (grp["classification"] == "Exact Match").sum()
            ms = (grp["classification"] == "Missed").sum()
            depth_val = grp["depth"].iloc[0]
            print(
                f"    [L{depth_val}]  {fp:<60s}  "
                f"DepViol={dv:3d}/{n}  EM={em:3d}  Miss={ms:3d}"
            )
    else:
        print("    No chain-child fields found.")
    print("=" * 76)


def main():
    print("=" * 76)
    print("Deep Nested Custom Fields — 5-Level Chain Testing Pipeline")
    print(f"  {len(DEEP_NESTED_FIELDS)} fields  |  {len(COVERAGE_SCENARIOS)} coverage scenarios  |  round-robin")
    print("=" * 76)

    run_setup   = "--setup"   in sys.argv
    force_rerun = "--reparse" in sys.argv

    print("\n[Auth] Fetching tokens ...")
    track_a_token = get_token(BASE_URL_TRACK_A, EMAIL_TRACK_A, PASSWORD_TRACK_A)
    ss_token      = get_token(BASE_URL_SS,       EMAIL_SS,       PASSWORD_SS)
    print("  Track A token OK | SS token OK")

    if run_setup:
        print("\n[Setup] Creating deep nested custom fields and dependencies ...")
        manifest = create_deep_nested_fields(ss_token)
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
        ground_truth_all = generate_deep_ground_truth(baseline, manifest)

    if RESULTS_FILE.exists() and not force_rerun:
        print(f"[Track B] Loading cached results → {RESULTS_FILE}")
        results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    else:
        results = run_deep_track_b(ss_token, baseline, ground_truth_all)

    print("\n[Validation] Applying full-chain dependency rules + option filtering ...")
    df = run_deep_validation(results, manifest)

    JSON_OUT_FILE.write_text(
        df.to_json(orient="records", indent=2, force_ascii=False), encoding="utf-8"
    )
    print(f"[Validation] JSON saved → {JSON_OUT_FILE}")

    build_deep_report(df)
    print_summary(df)


if __name__ == "__main__":
    main()

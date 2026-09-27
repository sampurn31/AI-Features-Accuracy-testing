#!/usr/bin/env python3
"""
LLM Scoring Heads-On Collision Evaluator
=========================================
1. Hits the live web app AI matching endpoint for all 9 jobs.
   Triggers scoring, polls until llm_status=success, collects
   every candidate that the platform algorithm actually surfaced.

2. For each scored candidate, calls Claude Sonnet 4.5 directly
   with the same system prompt + JD text + candidate resumetext.

3. Produces a side-by-side comparison report:
     - llm_eval_report.txt  (human-readable)
     - llm_eval_report.json (raw data)

CLI:
  python3 llm_scoring_eval.py --slug <job_slug>
      Run one job (or all jobs by default).
  python3 llm_scoring_eval.py --fresh
      Clear saved raw records before a new run.
  python3 llm_scoring_eval.py --report-only
      Build txt/json report from saved raw records (no API calls, no Sonnet cost).
  python3 llm_scoring_eval.py --webapp-only
      Only triggers + polls the web app; skips Anthropic entirely.
  python3 llm_scoring_eval.py --no-recheck
      Skip recheck=true refresh; use cached web-app scores if present.

Raw records are saved after EVERY candidate to llm_eval_raw_records.json so a
report failure never loses paid Sonnet responses.
"""

import json
import os
import re
import sys
import time

import anthropic
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SONNET_MODEL      = "claude-sonnet-4-5"

WEBAPP_HOST = "https://albatross-titans6.recruitcrm.net"
ARIES_HOST  = "https://dev-aries.recruitcrm.net"
WEBAPP_TOKEN = ""  # refreshed via login at runtime (see fetch_webapp_token)
LOGIN_EMAIL = os.getenv("RCRM_LOGIN_EMAIL", "")
LOGIN_PASSWORD = os.getenv("RCRM_LOGIN_PASSWORD", "")

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
RAW_RECORDS_PATH = os.path.join(WORKSPACE, "llm_eval_raw_records.json")
CHECKPOINT_PATH = RAW_RECORDS_PATH  # alias
SYSTEM_PROMPT_FILE = os.path.join(WORKSPACE, "System_Prompt.txt")
JOBS_JSON_FILE     = os.path.join(WORKSPACE, "synthetic_test_data", "data", "jobs.json")

# Jobs confirmed live on albatross-titans6
# Set RUN_ALL_JOBS=True to run all 10 jobs, False to run only the 2 already-scored jobs
RUN_ALL_JOBS = True

JOBS_ALL = [
    ("Performance Marketing Manager",       "17799868194225686098XDZ"),
    ("Senior Tax Accountant",               "17799867264695686098gxT"),
    ("Senior Backend Engineer",             "17799866826995686098kSM"),
    ("Industrial Mechanical Engineer",      "17799867660455686098Oeh"),
    ("Senior Data Scientist",               "17799873417075686098niQ"),
    ("Construction Project Manager",        "17799872604185686098xdX"),
    ("Pharmaceutical Sales Representative", "17799870547755686098Hjq"),
    ("High School Mathematics Teacher",     "17799869479085686098Gpi"),
    ("Senior UX/Product Designer",          "17799868474855686098KHY"),
    ("Pediatric Registered Nurse",          "17799867066035686098bXP"),
]

JOBS_QUICK = [
    ("Performance Marketing Manager",       "17787925650905689933ojF"),
    ("Senior Tax Accountant",               "17787925190975689933gNu"),
]

# Set to ("Title", "slug") to run exactly one job (overrides RUN_ALL_JOBS / JOBS_QUICK).
JOBS_OVERRIDE = None

if "--slug" in sys.argv:
    _i = sys.argv.index("--slug")
    if _i + 1 >= len(sys.argv):
        raise SystemExit("--slug requires a slug argument")
    _slug = sys.argv[_i + 1]
    _title = next((t for t, s in JOBS_ALL if s == _slug), None)
    if _title is None:
        raise SystemExit(f"Slug {_slug!r} not found in JOBS_ALL — add the job or fix the slug.")
    JOBS = [(_title, _slug)]
elif JOBS_OVERRIDE:
    JOBS = [JOBS_OVERRIDE]
else:
    JOBS = JOBS_ALL if RUN_ALL_JOBS else JOBS_QUICK

# Optional: ONLY_SLUGS=slug1,slug2 python3 llm_scoring_eval.py
_only = os.environ.get("ONLY_SLUGS", "").strip()
if _only:
    _want = {s.strip() for s in _only.split(",") if s.strip()}
    JOBS = [j for j in JOBS if j[1] in _want]
    if not JOBS:
        raise SystemExit(f"ONLY_SLUGS matched no jobs: {_only!r}")

# If True, only trigger + poll the web app; no direct Anthropic calls.
WEBAPP_ONLY = "--webapp-only" in sys.argv
REPORT_ONLY = "--report-only" in sys.argv

# Mirror UI "Refresh" — recheck=true forces fresh matching + LLM re-score (not cache).
# Pass --no-recheck to skip and use whatever scores the API already has.
FORCE_WEBAPP_RESCORE = "--no-recheck" not in sys.argv
RECHECK_ACTION_SOURCE = "Job Details Page - Job Related Actions"
# Max seconds to wait when recheck hits API cooldown before retrying once.
MAX_RECHECK_COOLDOWN_WAIT_SEC = 300

POLL_INTERVAL_SEC = 5
POLL_MAX_ATTEMPTS = 24   # 24 × 5s = 120s max per job

FLAG_OVERALL_DIFF  = 10   # ⚠ if |overall diff| > this
FLAG_DIM_DIFF      = 15   # ⚠ if |dimension diff| > this

DIM_KEYS    = ["fn", "sk", "lv", "tr", "dm", "lc"]
DIM_LABELS  = {"fn": "Function", "sk": "Skills", "lv": "Level",
               "tr": "Trajectory", "dm": "Domain", "lc": "Location"}
DIM_WEIGHTS = {"fn": 25, "sk": 25, "lv": 20, "tr": 15, "dm": 10, "lc": 5}

KNOWN_LABELS = {"Excellent", "Strong", "Good", "Partial", "Weak", "No Fit"}

# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def webapp_headers():
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {WEBAPP_TOKEN}",
        "content-type": "application/json",
        "cache-control": "no-cache",
        "pragma": "no-cache",
    }

def fetch_webapp_token(retries=5, backoff_sec=3):
    """Obtain a fresh Bearer token via login (no cached/stale token)."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"{WEBAPP_HOST}/v1/login",
                headers={
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/x-www-form-urlencoded",
                },
                data={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "success":
                raise RuntimeError(f"Web app login failed: {body.get('message')}")
            token = (body.get("data") or {}).get("token")
            if not token:
                raise RuntimeError("Web app login response missing data.token")
            return token
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"    ⚠ Login attempt {attempt}/{retries} failed ({e}) — retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            else:
                raise last_err

def ensure_webapp_token():
    """Refresh Bearer token (e.g. after long cooldown sleeps that exceed JWT lifetime)."""
    global WEBAPP_TOKEN
    WEBAPP_TOKEN = fetch_webapp_token()

def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def fetch_jd_text_by_slug(slug):
    """Load JD from CRM when synthetic jobs.json has no match (new branch requires server JD)."""
    headers = {
        "accept": "application/json;charset=UTF-8",
        "authorization": f"Bearer {WEBAPP_TOKEN}",
        "content-type": "application/json;charset=UTF-8",
    }
    resp = requests.post(
        f"{ARIES_HOST}/v2/advanced-search/jobs/search/get?page=1&size=100",
        headers=headers,
        json={
            "advancedSearchContext": "JOB",
            "defaultFilterList": None,
            "filterSearchList": None,
            "booleanSearchList": None,
            "sortPriorityList": [],
        },
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("data") or []
    for job in items:
        if job.get("slug") == slug:
            raw = job.get("jdtext") or job.get("jdtextdetails") or job.get("description") or ""
            jd = strip_html(raw)
            if jd:
                return jd
    return None

def normalize_api_root(json_obj):
    """Some responses are a bare list; take first object if dict."""
    if isinstance(json_obj, list):
        if not json_obj:
            return {}
        first = json_obj[0]
        return first if isinstance(first, dict) else {}
    return json_obj if isinstance(json_obj, dict) else {}

def get_matching_payload(json_obj):
    """
    Resolve the payload that carries llm_status / records /
    total_count whether the envelope is { data: {...} } or flat.
    """
    root = normalize_api_root(json_obj)
    data = root.get("data")
    if isinstance(data, dict) and (
        "llm_status" in data or "records" in data or "total_count" in data
    ):
        return data
    if "llm_status" in root or "records" in root or "total_count" in root:
        return root
    return data if isinstance(data, dict) else {}

def _matching_url(slug):
    return f"{WEBAPP_HOST}/v1/jobs/{slug}/job-candidate-matching"

def _wrap_matching_response(resp):
    resp.raise_for_status()
    root = normalize_api_root(resp.json())
    if root and "data" not in root and (
        "llm_status" in root or "records" in root
    ):
        return {"data": root}
    return root

def refresh_scoring(slug):
    """
    Step 1 — mirrors the UI Refresh action (recheck=true).
    Invalidates cached scores and re-runs candidate matching + LLM queue.
    The full browser payload includes a huge `columns` object; the API accepts
    recheck without it (verified against titans10).
    """
    payload = {
        "page_size": 25,
        "page": 1,
        "searchinnotes": "",
        "recheck": True,
        "recheck_action_source": RECHECK_ACTION_SOURCE,
        "deal_stage_report": False,
    }
    resp = requests.post(
        _matching_url(slug), headers=webapp_headers(), json=payload, timeout=60,
    )
    return _wrap_matching_response(resp)

def refresh_scoring_with_cooldown(slug):
    """
    Call refresh_scoring; if API returns cooldown, wait (up to cap) and retry once.
    """
    resp = refresh_scoring(slug)
    root = normalize_api_root(resp)
    data = get_matching_payload(resp)
    is_cooldown = (
        root.get("status") == "cooldown"
        or data.get("llm_status") == "cooldown"
        or data.get("cooldown")
    )
    if not is_cooldown:
        return resp

    remaining = int(data.get("cooldown_remaining_seconds") or 0)
    msg = root.get("message") or "recheck cooldown active"
    if remaining > MAX_RECHECK_COOLDOWN_WAIT_SEC:
        print(
            f"    ⚠ {msg} ({remaining}s remaining, exceeds wait cap "
            f"{MAX_RECHECK_COOLDOWN_WAIT_SEC}s) — continuing; scores may be cached"
        )
        return resp

    wait_sec = remaining + 2
    print(f"    ⚠ {msg} — waiting {wait_sec}s then retrying recheck...")
    time.sleep(wait_sec)
    ensure_webapp_token()
    print("    ✓ Web app token refreshed after cooldown wait")
    return refresh_scoring(slug)

def trigger_scoring(slug):
    """Step 2 — getcount=true kicks off / continues the LLM scoring job."""
    resp = requests.post(
        _matching_url(slug), headers=webapp_headers(),
        json={"page_size": 1, "page": 1, "getcount": True},
        timeout=30,
    )
    return _wrap_matching_response(resp)

def poll_scoring(slug):
    """
    Poll until llm_status == 'success' or timeout.
    Returns the full records list, or None on timeout / API error.
    """
    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        resp = requests.post(
            _matching_url(slug), headers=webapp_headers(),
            json={"page_size": 100, "page": 1},
            timeout=30,
        )
        resp.raise_for_status()
        root = normalize_api_root(resp.json())
        if root.get("status") == "fail":
            print(f"    attempt {attempt}: API fail — {root.get('message')}")
            return None
        data = get_matching_payload(root)
        status = data.get("llm_status")
        print(f"    attempt {attempt}/{POLL_MAX_ATTEMPTS}: llm_status={status}, "
              f"total_count={data.get('total_count')}")
        if status == "success":
            return data.get("records", [])
        time.sleep(POLL_INTERVAL_SEC)
    return None  # timed out

def extract_webapp_candidates(records):
    """
    From raw records, keep only candidates where llm_scoring is populated.
    Returns list of dicts with the fields we need.
    """
    out = []
    for r in records:
        scoring = r.get("llm_scoring")
        if not scoring or r.get("llm_status") != "success":
            continue
        out.append({
            "id":           r.get("id"),
            "name":         r.get("candidatename", "Unknown"),
            "resumetext":   r.get("resumetext", ""),
            "webapp_score": r.get("score"),
            "webapp_llm":   scoring,   # full llm_scoring block from web app
        })
    return out

def build_user_prompt(jd_text, resumetext):
    return f"JOB DESCRIPTION:\n{jd_text}\n\nCANDIDATE:\n{resumetext}"

def serialize_usage(message):
    """Extract token counts from Anthropic Messages API response."""
    u = getattr(message, "usage", None)
    if u is None:
        return {}
    # SDK object or dict depending on version
    keys = [
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ]
    out = {}
    for k in keys:
        v = getattr(u, k, None)
        if v is None and isinstance(u, dict):
            v = u.get(k)
        if v is not None:
            out[k] = int(v)
    return out

def call_sonnet(client, system_prompt, user_prompt):
    """Call Sonnet 4.5; return (parsed JSON dict or None, usage dict)."""
    usage = {}
    try:
        message = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        usage = serialize_usage(message)
        raw_text = message.content[0].text.strip()
        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        raw_text = re.sub(r"^```(?:json)?\s*\n?", "", raw_text, flags=re.MULTILINE)
        raw_text = re.sub(r"\n?```\s*$", "", raw_text, flags=re.MULTILINE)
        raw_text = raw_text.strip()
        # Extract the first complete JSON object in case model appends extra text
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(raw_text)
        return obj, usage
    except Exception as e:
        print(f"      ⚠ Sonnet call error: {e}")
        return None, usage

def score_val(d, key):
    """Safely get d[key]['s'], returning None if missing."""
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("s")
    return None

def reason_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("r", "")
    return ""

def label_from_score(score):
    """Derive label when LLM omits l (System_Prompt.txt computes server-side)."""
    if score is None:
        return "N/A"
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Strong"
    if score >= 55:
        return "Good"
    if score >= 40:
        return "Partial"
    if score >= 20:
        return "Weak"
    return "No Fit"

def clean_label(raw):
    """Extract clean label from potentially polluted l field."""
    if not raw:
        return "N/A"
    s = str(raw).strip()
    for lab in ("No Fit", "Excellent", "Strong", "Good", "Partial", "Weak"):
        if s.lower().startswith(lab.lower()):
            return lab
    first = s.split()[0].rstrip(".,;:")
    return first if first in KNOWN_LABELS else s[:15]

def formula_score(sn):
    """Recalculate overall from dim scores using the production formula."""
    if not sn:
        return None
    vals = {dk: score_val(sn, dk) for dk in DIM_KEYS}
    if any(v is None for v in vals.values()):
        return None
    return round(sum(vals[dk] * DIM_WEIGHTS[dk] for dk in DIM_KEYS) / 100, 1)

def hard_rule_reason(sn, fs, llm_o):
    """
    Compare formula vs LLM-returned overall.
    If they differ by > 5 pts, the LLM applied a hard rule cap.
    Returns a human-readable explanation of which rule likely fired.
    """
    if fs is None or llm_o is None:
        return ""
    fd = round(llm_o - fs, 1)
    if abs(fd) <= 5:
        return "No cap applied — LLM overall ≈ formula"

    # Identify the cap tier from the system prompt hard rules
    reasons = []
    if llm_o <= 30 and fs > 50:
        reasons.append("Cap at 30: missing required credential for regulated role (e.g. CPA, nursing license)")
    elif llm_o <= 55 and fs > 55:
        reasons.append("Cap at 55: core function mismatch OR seniority gap 2+ levels")
    elif llm_o <= 75 and fs > 75:
        reasons.append("Cap at 75: missing must-have skill(s)")

    # Cross-reference dim reasoning for supporting evidence
    for dk in ["sk", "fn", "lv"]:
        r_text = reason_val(sn, dk)
        for kw in ["CPA", "license", "credential", "certification",
                   "must-have", "cap", "regulated", "required"]:
            if kw.lower() in r_text.lower():
                reasons.append(f"Evidence in {DIM_LABELS[dk]} reasoning: '{kw}' mentioned")
                break

    if not reasons:
        reasons.append(f"LLM self-corrected (formula={fs}, returned={llm_o}, Δ={fd:+.1f})")

    return " | ".join(dict.fromkeys(reasons))

def diff_str(a, b):
    if a is None or b is None:
        return "N/A"
    d = a - b
    if isinstance(d, float) and not d.is_integer():
        return f"{d:+.1f}"
    return f"{int(d):+d}"

def flag(a, b, threshold):
    if a is None or b is None:
        return ""
    return " ⚠" if abs(a - b) > threshold else ""


def save_raw_records(records):
    """Persist every collected record immediately (atomic write)."""
    payload = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system_prompt_file": os.path.basename(SYSTEM_PROMPT_FILE),
        "webapp_host": WEBAPP_HOST,
        "sonnet_model": SONNET_MODEL,
        "record_count": len(records),
        "records": records,
    }
    tmp = RAW_RECORDS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, RAW_RECORDS_PATH)


def load_raw_records():
    """Load saved raw records; returns (records list, envelope dict)."""
    if not os.path.isfile(RAW_RECORDS_PATH):
        return [], {}
    with open(RAW_RECORDS_PATH) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data, {}
    return data.get("records") or [], data


def build_and_write_report(all_results, merged_prior=None, webapp_only=False):
    """Build txt + json reports from collected raw records."""
    merged_prior = merged_prior or []
    if merged_prior:
        ran_slugs = {s for _, s in JOBS}
        merged_prior = [r for r in merged_prior if r.get("slug") not in ran_slugs]

    if not all_results and not merged_prior:
        print("\n✗ No results to report.")
        return False

    report_lines = []
    json_output = list(merged_prior)

    total_candidates = 0
    label_matches = 0
    overall_diffs = []
    dim_flag_count = 0

    current_job = None
    for r in all_results:
        if r["job_name"] != current_job:
            current_job = r["job_name"]
            report_lines.append(f"\n{'═'*66}")
            report_lines.append(f"JOB: {current_job}  (slug: {r['slug']})")
            report_lines.append(f"{'═'*66}")

        wa = r["webapp"]
        sn = r["sonnet"]
        su = r.get("sonnet_usage") or {}

        report_lines.append(f"\n  CANDIDATE: {r['candidate']}  (ID {r['cid']})")
        report_lines.append(f"  {'─'*60}")
        report_lines.append(f"  {'DIM':<10} {'SONNET 4.5':>12} {'WEB APP':>10} {'DIFF':>8}")
        report_lines.append(f"  {'─'*10} {'─'*12} {'─'*10} {'─'*8}")

        dim_rows = []
        for dk in DIM_KEYS:
            sn_s = score_val(sn, dk)
            wa_s = score_val(wa, dk)
            d = diff_str(sn_s, wa_s)
            fl = flag(sn_s, wa_s, FLAG_DIM_DIFF)
            if fl:
                dim_flag_count += 1
            label_col = f"{DIM_LABELS[dk]} ({dk})"
            sn_col = str(sn_s) if sn_s is not None else "N/A"
            wa_col = str(wa_s) if wa_s is not None else "N/A"
            report_lines.append(f"  {label_col:<22} {sn_col:>8} {wa_col:>10} {d:>8}{fl}")
            dim_rows.append({"dim": dk, "sonnet": sn_s, "webapp": wa_s})

        fs = formula_score(sn)
        sn_o = sn.get("o") if sn else None
        if sn_o is None:
            sn_o = fs
        wa_o = wa.get("o") if wa else None

        sn_l = clean_label(sn.get("l") if sn else None)
        if sn_l == "N/A" and sn_o is not None:
            sn_l = label_from_score(sn_o)
        wa_l = clean_label(wa.get("l") if wa else None)

        fd = round(sn_o - fs, 1) if (sn_o is not None and fs is not None) else None
        hr = hard_rule_reason(sn, fs, sn_o)

        o_diff = diff_str(sn_o, wa_o)
        o_flag = flag(sn_o, wa_o, FLAG_OVERALL_DIFF)
        if not webapp_only and sn_o is not None and wa_o is not None:
            overall_diffs.append(abs(sn_o - wa_o))

        l_match = "✓" if sn_l == wa_l else "✗"
        if not webapp_only and sn_l == wa_l:
            label_matches += 1

        report_lines.append(f"  {'─'*10} {'─'*12} {'─'*10} {'─'*8}")

        fs_str = str(fs) if fs is not None else "N/A"
        fd_str = f"{fd:+.1f}" if fd is not None else "N/A"
        fd_flag = " ⚠ HARD RULE" if (fd is not None and abs(fd) > 5) else ""
        report_lines.append(f"  {'FORMULA SCORE':<22} {fs_str:>8}   (fn×25+sk×25+lv×20+tr×15+dm×10+lc×5)/100")

        report_lines.append(f"  {'SONNET OVERALL':<22} {str(sn_o) if sn_o is not None else 'N/A':>8}"
                            f"   Δ from formula: {fd_str}{fd_flag}")
        if hr and "No cap" not in hr:
            report_lines.append(f"  {'  → Why diff?':<22} {hr}")

        report_lines.append(f"  {'WEBAPP OVERALL':<22} {str(wa_o) if wa_o is not None else 'N/A':>8}")
        report_lines.append(f"  {'S vs W DIFF':<22} {o_diff:>8}{o_flag}")
        report_lines.append(f"  {'LABEL':<22} {'Sonnet: '+sn_l:>14}  WebApp: {wa_l}  {l_match if not webapp_only else '— (Sonnet skipped)'}")

        sn_or = (sn or {}).get("or") or ""
        wa_or = (wa or {}).get("or") or ""
        if sn_or or wa_or:
            report_lines.append(f"\n  OVERALL REASONING (or):")
            if sn_or:
                report_lines.append(f"  Sonnet: {sn_or}")
            if wa_or:
                report_lines.append(f"  WebApp: {wa_or}")
        if su:
            report_lines.append(
                f"  {'SONNET API TOKENS':<22} in={su.get('input_tokens', '—')}  "
                f"out={su.get('output_tokens', '—')}  "
                f"(cache writes {su.get('cache_creation_input_tokens', '—')}, "
                f"cache reads {su.get('cache_read_input_tokens', '—')})"
            )

        report_lines.append(f"\n  REASONING:")
        for dk in DIM_KEYS:
            sn_r = reason_val(sn, dk)
            wa_r = reason_val(wa, dk)
            report_lines.append(f"  {dk} | Sonnet: {sn_r}")
            report_lines.append(f"     | WebApp: {wa_r}")

        total_candidates += 1
        json_output.append({
            "job": r["job_name"],
            "slug": r["slug"],
            "candidate": r["candidate"],
            "cid": r["cid"],
            "resumetext": r.get("resumetext", ""),
            "dims": dim_rows,
            "formula_score": fs,
            "overall": {
                "sonnet": sn_o,
                "webapp": wa_o,
                "diff_s_vs_w": (sn_o - wa_o) if (sn_o is not None and wa_o is not None) else None,
                "formula_delta": fd,
                "hard_rule": hr,
                "sonnet_or": sn_or,
                "webapp_or": wa_or,
            },
            "label": {"sonnet": sn_l, "webapp": wa_l, "match": sn_l == wa_l},
            "sonnet_raw": sn,
            "webapp_raw": wa,
            "sonnet_usage": su,
        })

    avg_diff = sum(overall_diffs) / len(overall_diffs) if overall_diffs else 0
    max_diff = max(overall_diffs) if overall_diffs else 0
    max_diff_cand = ""
    for r in json_output:
        d = r["overall"]["diff_s_vs_w"]
        if d is not None and abs(d) == max_diff:
            max_diff_cand = r["candidate"]
            break

    hard_caps = sum(
        1 for r in json_output
        if r["overall"]["formula_delta"] is not None
        and abs(r["overall"]["formula_delta"]) > 5
    )

    report_lines.append(f"\n\n{'═'*66}")
    report_lines.append(f"AGGREGATE SUMMARY")
    report_lines.append(f"{'═'*66}")
    if webapp_only:
        report_lines.append("  Run mode:                      WEB APP ONLY (no Sonnet)")
    report_lines.append(f"  Jobs processed:                {len(set(r['job'] for r in json_output))}")
    report_lines.append(f"  Total candidates scored:       {total_candidates}")
    if webapp_only:
        report_lines.append("  Sonnet vs WebApp stats:        (skipped — webapp-only run)")
    else:
        report_lines.append(f"  Avg |Sonnet vs WebApp| diff:   {avg_diff:.1f} pts")
        report_lines.append(f"  Max |Sonnet vs WebApp| diff:   {max_diff} pts  ({max_diff_cand})")
        report_lines.append(f"  Label match rate:              {label_matches} / {total_candidates}  "
                            f"({100*label_matches//total_candidates if total_candidates else 0}%)")
        report_lines.append(f"  Hard rule caps (formula Δ>5):  {hard_caps} candidates")
        report_lines.append(f"  Dimension flags ⚠ (>±{FLAG_DIM_DIFF}):        {dim_flag_count} instances")
        report_lines.append(f"  Overall flags   ⚠ (>±{FLAG_OVERALL_DIFF}):        "
                            f"{sum(1 for d in overall_diffs if d > FLAG_OVERALL_DIFF)} candidates")

    usage_totals = {}
    sonnet_calls_with_usage = 0
    for r in all_results:
        su = r.get("sonnet_usage") or {}
        if not su:
            continue
        sonnet_calls_with_usage += 1
        for k, v in su.items():
            if isinstance(v, (int, float)):
                usage_totals[k] = usage_totals.get(k, 0) + int(v)
    api_ok = sum(1 for r in all_results if r.get("sonnet"))
    api_fail = 0 if webapp_only else (len(all_results) - api_ok)

    tin = usage_totals.get("input_tokens", 0)
    tout = usage_totals.get("output_tokens", 0)
    anthropic_usage = {
        "model": SONNET_MODEL,
        "successful_scoring_calls": api_ok,
        "failed_or_empty_parse": api_fail,
        "calls_with_usage_recorded": sonnet_calls_with_usage,
        "totals": usage_totals,
        "approx_billable_total_tokens_note": (tin + tout) if not webapp_only and (tin or tout) else None,
        "skipped": webapp_only,
    }
    if webapp_only:
        report_lines.append("\n  ANTHROPIC (direct Sonnet):      skipped (webapp-only run)")
    else:
        report_lines.append(f"\n  ANTHROPIC (direct Sonnet) — totals across this run:")
        report_lines.append(f"    Model:                           {SONNET_MODEL}")
        report_lines.append(f"    Parsed scoring responses:        {api_ok} ok, {api_fail} failed")
        report_lines.append(f"    Rows with usage metadata:        {sonnet_calls_with_usage}")
        report_lines.append(f"    Sum input_tokens:                {tin}")
        report_lines.append(f"    Sum output_tokens:               {tout}")
        report_lines.append(f"    Sum input + output (excl cache): {tin + tout}")

    payload = {
        "webapp_only": webapp_only,
        "anthropic_usage": anthropic_usage,
        "records": json_output,
    }

    report_path = os.path.join(WORKSPACE, "llm_eval_report.txt")
    json_path = os.path.join(WORKSPACE, "llm_eval_report.json")

    report_text = "\n".join(report_lines)
    with open(report_path, "w") as f:
        f.write(report_text)
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    print(report_text)
    print(f"\n✓ Report saved to: {report_path}")
    print(f"✓ JSON saved to:   {json_path}")
    return True

# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    global WEBAPP_TOKEN

    if "--fresh" in sys.argv and os.path.isfile(RAW_RECORDS_PATH):
        os.remove(RAW_RECORDS_PATH)
        print(f"✓ Cleared prior raw records: {RAW_RECORDS_PATH}\n")

    if REPORT_ONLY:
        all_results, envelope = load_raw_records()
        if not all_results:
            raise SystemExit(
                f"No raw records at {RAW_RECORDS_PATH}. Run eval first (costs Sonnet tokens)."
            )
        print(f"Mode: REPORT ONLY — {len(all_results)} records from {RAW_RECORDS_PATH}")
        if envelope.get("saved_at"):
            print(f"  Saved at: {envelope['saved_at']}")
        print()
        build_and_write_report(all_results, webapp_only=WEBAPP_ONLY)
        return

    WEBAPP_TOKEN = fetch_webapp_token()
    print("✓ Fresh web app token obtained via login\n")

    with open(SYSTEM_PROMPT_FILE) as f:
        system_prompt = f.read().strip()

    with open(JOBS_JSON_FILE) as f:
        raw_jobs = json.load(f)
    jd_map = {j["title"]: j["jd_text"] for j in raw_jobs}

    anthropic_client = (
        None if WEBAPP_ONLY else anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    )

    if WEBAPP_ONLY:
        print("Mode: WEB APP ONLY (no Anthropic / Sonnet calls)\n")

    all_results, _ = load_raw_records()
    done_keys = {(r["slug"], r["cid"]) for r in all_results}
    if all_results:
        print(f"✓ Resumed {len(all_results)} raw record(s) from checkpoint\n")

    merge_path = os.environ.get("MERGE_REPORT", "").strip()
    merged_prior = []
    if merge_path and os.path.isfile(merge_path):
        with open(merge_path) as f:
            prior = json.load(f)
        merged_prior = prior.get("records") or []
        print(f"✓ Loaded {len(merged_prior)} prior records from {merge_path}\n")

    for job_name, slug in JOBS:
        ensure_webapp_token()
        print(f"\n{'═'*60}")
        print(f"JOB: {job_name}")
        print(f"Slug: {slug}")
        print(f"{'═'*60}")

        try:
            if FORCE_WEBAPP_RESCORE:
                print("  → Refreshing scores (recheck=true)...")
                refresh_resp = refresh_scoring_with_cooldown(slug)
                root = normalize_api_root(refresh_resp)
                if root.get("status") == "fail":
                    print(f"  ✗ Refresh API error: {root.get('message')}")
                    continue
                _rdata = get_matching_payload(refresh_resp)
                print(
                    f"    Refresh llm_status={_rdata.get('llm_status')}, "
                    f"recheck_triggered={_rdata.get('recheck_triggered')}, "
                    f"total_count={_rdata.get('total_count')}"
                )
                if root.get("status") == "cooldown" or _rdata.get("llm_status") == "cooldown":
                    print("    ⚠ Still in recheck cooldown — web-app scores may not be freshly re-scored")
            else:
                print("  → Skipping recheck (--no-recheck)")

            print("  → Triggering LLM scoring (getcount=true)...")
            trigger_resp = trigger_scoring(slug)
            if trigger_resp.get("status") == "fail":
                print(f"  ✗ Trigger API error: {trigger_resp.get('message')}")
                continue
            _tdata = get_matching_payload(trigger_resp)
            trigger_status = (_tdata or {}).get("llm_status")
            print(
                f"    Trigger llm_status={trigger_status}, "
                f"total_count={_tdata.get('total_count')}"
            )
        except Exception as e:
            print(f"  ✗ Web app scoring failed: {e}")
            continue

        if trigger_status == "success" and not FORCE_WEBAPP_RESCORE:
            print("  → Already scored, fetching results...")
            records = poll_scoring(slug)
        else:
            print(f"  → Waiting {POLL_INTERVAL_SEC}s then polling...")
            time.sleep(POLL_INTERVAL_SEC)
            records = poll_scoring(slug)

        if records is None:
            print(f"  ✗ Timed out waiting for llm_status=success, skipping job.")
            continue

        candidates = extract_webapp_candidates(records)
        print(f"  ✓ {len(candidates)} candidates with llm_scoring")

        if not candidates:
            print("  (no scored candidates, skipping)")
            continue

        jd_text = jd_map.get(job_name)
        if not jd_text:
            for k, v in jd_map.items():
                if job_name.lower() in k.lower() or k.lower() in job_name.lower():
                    jd_text = v
                    print(f"  (matched JD via fuzzy: '{k}')")
                    break
        if not jd_text:
            jd_text = fetch_jd_text_by_slug(slug)
            if jd_text:
                print(f"  (JD loaded from CRM for slug {slug})")
        if not jd_text:
            print(f"  ✗ No JD text found for '{job_name}', skipping job.")
            continue

        job_new = 0
        for cand in candidates:
            key = (slug, cand["id"])
            if key in done_keys:
                print(f"  → Skip {cand['name']} (ID {cand['id']}) — already saved")
                continue

            if WEBAPP_ONLY:
                print(f"  → Web app only — skipping Sonnet for {cand['name']} (ID {cand['id']})")
                sonnet_out, sonnet_usage = None, {}
            else:
                print(f"  → Calling Sonnet for {cand['name']} (ID {cand['id']})...", end=" ", flush=True)
                user_prompt = build_user_prompt(jd_text, cand["resumetext"])
                sonnet_out, sonnet_usage = call_sonnet(anthropic_client, system_prompt, user_prompt)
                if sonnet_out:
                    print("✓")
                else:
                    print("✗ (no output)")

            rec = {
                "job_name": job_name,
                "slug": slug,
                "candidate": cand["name"],
                "cid": cand["id"],
                "resumetext": cand["resumetext"],
                "webapp": cand["webapp_llm"],
                "sonnet": sonnet_out,
                "sonnet_usage": sonnet_usage or {},
                "jd_text": jd_text,
            }
            all_results.append(rec)
            done_keys.add(key)
            save_raw_records(all_results)
            job_new += 1
            print(f"      ✓ Raw record saved → {RAW_RECORDS_PATH} ({len(all_results)} total)")

        print(f"  ✓ Job done: {job_new} new, {len(all_results)} total saved")

    if not all_results and not merged_prior:
        print("\n✗ No results collected. Nothing to report.")
        return

    print(f"\n{'═'*60}")
    print("Building report from saved raw records...")
    print(f"{'═'*60}")
    try:
        build_and_write_report(all_results, merged_prior=merged_prior, webapp_only=WEBAPP_ONLY)
    except Exception as e:
        print(f"\n✗ Report generation failed: {e}")
        print(f"✓ Your paid Sonnet + webapp data is SAFE at:")
        print(f"    {RAW_RECORDS_PATH}")
        print(f"  Fix the bug, then rebuild report for FREE with:")
        print(f"    python3 llm_scoring_eval.py --report-only")
        raise


if __name__ == "__main__":
    main()

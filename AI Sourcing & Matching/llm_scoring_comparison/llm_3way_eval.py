#!/usr/bin/env python3
"""
3-Way LLM Scoring Head-On Collision Evaluator
==============================================
Compares three LLM scoring sources for the same candidates:

  1. Claude Sonnet 4.5 (Anthropic direct API call) — GROUND TRUTH
  2. DeepSeek (titans10 web app)
  3. Gemini (titans8 web app)

For each job, fetches scored candidates from both web apps,
calls Sonnet directly, then produces a 3-way comparison.

Output:
  - llm_3way_report.txt   (human-readable)
  - llm_3way_report.json  (raw data for Excel builder)

CLI:
  python3 llm_3way_eval.py                     # full run, all 10 jobs
  python3 llm_3way_eval.py --slug <slug>       # single job
  python3 llm_3way_eval.py --no-recheck        # skip recheck, use cached scores
  python3 llm_3way_eval.py --skip-sonnet       # only fetch web app scores
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
SONNET_MODEL = "claude-sonnet-4-5"

# Web app environments
ENVS = {
    "deepseek": {
        "host": "https://albatross-titans10.recruitcrm.net",
        "label": "DeepSeek (titans10)",
    },
    "gemini": {
        "host": "https://albatross-titans8.recruitcrm.net",
        "label": "Gemini (titans8)",
    },
}

LOGIN_EMAIL = os.getenv("RCRM_LOGIN_EMAIL", "")
LOGIN_PASSWORD = os.getenv("RCRM_LOGIN_PASSWORD", "")
ARIES_HOST = "https://dev-aries.recruitcrm.net"

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_FILE = os.path.join(WORKSPACE, "New_systemprompt.txt")
JOBS_JSON_FILE = os.path.join(WORKSPACE, "synthetic_test_data", "data", "jobs.json")

JOBS_ALL = [
    ("Performance Marketing Manager",       "17799868194225686098XDZ"),
    ("Senior Tax Accountant",               "17799867264695686098gxT"),
    ("Senior Backend Engineer",             "17799866826995686098kSM"),
    ("Industrial Mechanical Engineer",      "17799867660455686098Oeh"),
    ("Senior Data Scientist (NLP/ML)",      "17799873417075686098niQ"),
    ("Construction Project Manager",        "17799872604185686098xdX"),
    ("Pharmaceutical Sales Representative", "17799870547755686098Hjq"),
    ("High School Mathematics Teacher",     "17799869479085686098Gpi"),
    ("Senior UX/Product Designer",          "17799868474855686098KHY"),
    ("Pediatric Registered Nurse",          "17799867066035686098bXP"),
]

if "--slug" in sys.argv:
    _i = sys.argv.index("--slug")
    if _i + 1 >= len(sys.argv):
        raise SystemExit("--slug requires a slug argument")
    _slug = sys.argv[_i + 1]
    _title = next((t for t, s in JOBS_ALL if s == _slug), None)
    if _title is None:
        raise SystemExit(f"Slug {_slug!r} not found in JOBS_ALL")
    JOBS = [(_title, _slug)]
else:
    JOBS = JOBS_ALL

SKIP_SONNET = "--skip-sonnet" in sys.argv
FORCE_RECHECK = "--no-recheck" not in sys.argv
RECHECK_ACTION_SOURCE = "Job Details Page - Job Related Actions"
MAX_COOLDOWN_WAIT = 300

POLL_INTERVAL = 5
POLL_MAX = 30  # 30 × 5s = 150s

FLAG_OVERALL = 10
FLAG_DIM = 15

DIM_KEYS = ["fn", "sk", "lv", "tr", "dm", "lc"]
DIM_LABELS = {"fn": "Function", "sk": "Skills", "lv": "Level",
              "tr": "Trajectory", "dm": "Domain", "lc": "Location"}
DIM_WEIGHTS = {"fn": 25, "sk": 25, "lv": 20, "tr": 15, "dm": 10, "lc": 5}
KNOWN_LABELS = {"Excellent", "Strong", "Good", "Partial", "Weak", "No Fit"}

# ──────────────────────────────────────────────────────────────
# TOKEN MANAGEMENT (per environment)
# ──────────────────────────────────────────────────────────────

_tokens = {}  # env_key -> token


def get_token(env_key):
    host = ENVS[env_key]["host"]
    resp = requests.post(
        f"{host}/v1/login",
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "success":
        raise RuntimeError(f"Login failed on {env_key}: {body.get('message')}")
    return body["data"]["token"]


def ensure_token(env_key):
    _tokens[env_key] = get_token(env_key)


def headers_for(env_key):
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {_tokens[env_key]}",
        "content-type": "application/json",
        "cache-control": "no-cache",
        "pragma": "no-cache",
    }


# ──────────────────────────────────────────────────────────────
# WEB APP HELPERS
# ──────────────────────────────────────────────────────────────

def normalize_api_root(json_obj):
    if isinstance(json_obj, list):
        return json_obj[0] if json_obj and isinstance(json_obj[0], dict) else {}
    return json_obj if isinstance(json_obj, dict) else {}


def get_matching_payload(json_obj):
    root = normalize_api_root(json_obj)
    data = root.get("data")
    if isinstance(data, dict) and ("llm_status" in data or "records" in data):
        return data
    if "llm_status" in root or "records" in root:
        return root
    return data if isinstance(data, dict) else {}


def matching_url(env_key, slug):
    return f"{ENVS[env_key]['host']}/v1/jobs/{slug}/job-candidate-matching"


def refresh_scoring(env_key, slug):
    payload = {
        "page_size": 25, "page": 1, "searchinnotes": "",
        "recheck": True,
        "recheck_action_source": RECHECK_ACTION_SOURCE,
        "deal_stage_report": False,
    }
    resp = requests.post(
        matching_url(env_key, slug), headers=headers_for(env_key),
        json=payload, timeout=60,
    )
    resp.raise_for_status()
    return normalize_api_root(resp.json())


def refresh_with_cooldown(env_key, slug):
    resp = refresh_scoring(env_key, slug)
    data = get_matching_payload(resp)
    is_cd = (resp.get("status") == "cooldown" or data.get("llm_status") == "cooldown"
             or data.get("cooldown"))
    if not is_cd:
        return resp

    wait = int(data.get("cooldown_remaining_seconds") or 0)
    if wait > MAX_COOLDOWN_WAIT:
        print(f"      ⚠ Cooldown {wait}s exceeds cap — continuing with cached scores")
        return resp
    print(f"      ⚠ Cooldown — waiting {wait + 2}s...")
    time.sleep(wait + 2)
    ensure_token(env_key)
    return refresh_scoring(env_key, slug)


def trigger_scoring(env_key, slug):
    resp = requests.post(
        matching_url(env_key, slug), headers=headers_for(env_key),
        json={"page_size": 1, "page": 1, "getcount": True}, timeout=30,
    )
    resp.raise_for_status()
    return normalize_api_root(resp.json())


def poll_scoring(env_key, slug):
    for attempt in range(1, POLL_MAX + 1):
        resp = requests.post(
            matching_url(env_key, slug), headers=headers_for(env_key),
            json={"page_size": 100, "page": 1}, timeout=30,
        )
        resp.raise_for_status()
        root = normalize_api_root(resp.json())
        if root.get("status") == "fail":
            print(f"      attempt {attempt}: API fail")
            return None
        data = get_matching_payload(root)
        status = data.get("llm_status")
        print(f"      [{env_key}] attempt {attempt}/{POLL_MAX}: llm_status={status}, count={data.get('total_count')}")
        if status == "success":
            return data.get("records", [])
        time.sleep(POLL_INTERVAL)
    return None


def fetch_env_candidates(env_key, slug):
    """Full flow: recheck → trigger → poll → extract scored candidates."""
    ensure_token(env_key)
    label = ENVS[env_key]["label"]

    if FORCE_RECHECK:
        print(f"    [{label}] Refreshing (recheck=true)...")
        refresh_with_cooldown(env_key, slug)

    print(f"    [{label}] Triggering scoring...")
    trigger_scoring(env_key, slug)

    print(f"    [{label}] Polling...")
    time.sleep(POLL_INTERVAL)
    records = poll_scoring(env_key, slug)
    if records is None:
        print(f"    [{label}] ✗ Timed out")
        return {}

    out = {}
    for r in records:
        scoring = r.get("llm_scoring")
        if not scoring or r.get("llm_status") != "success":
            continue
        out[r.get("id")] = {
            "id": r.get("id"),
            "name": r.get("candidatename", "Unknown"),
            "resumetext": r.get("resumetext", ""),
            "score": r.get("score"),
            "llm": scoring,
        }
    print(f"    [{label}] ✓ {len(out)} candidates scored")
    return out


# ──────────────────────────────────────────────────────────────
# SONNET (ANTHROPIC) CALL
# ──────────────────────────────────────────────────────────────

def call_sonnet(client, system_prompt, user_prompt):
    try:
        message = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        usage = {}
        u = getattr(message, "usage", None)
        if u:
            for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                v = getattr(u, k, None)
                if v is not None:
                    usage[k] = int(v)
        raw_text = message.content[0].text.strip()
        raw_text = re.sub(r"^```(?:json)?\s*\n?", "", raw_text, flags=re.MULTILINE)
        raw_text = re.sub(r"\n?```\s*$", "", raw_text, flags=re.MULTILINE)
        raw_text = raw_text.strip()
        obj, _ = json.JSONDecoder().raw_decode(raw_text)
        return obj, usage
    except Exception as e:
        print(f"      ⚠ Sonnet error: {e}")
        return None, {}


# ──────────────────────────────────────────────────────────────
# UTILITY
# ──────────────────────────────────────────────────────────────

def strip_html(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text))).strip()


def fetch_jd_text_by_slug(slug, env_key="deepseek"):
    headers = {
        "accept": "application/json;charset=UTF-8",
        "authorization": f"Bearer {_tokens[env_key]}",
        "content-type": "application/json;charset=UTF-8",
    }
    resp = requests.post(
        f"{ARIES_HOST}/v2/advanced-search/jobs/search/get?page=1&size=100",
        headers=headers,
        json={"advancedSearchContext": "JOB", "defaultFilterList": None,
              "filterSearchList": None, "booleanSearchList": None, "sortPriorityList": []},
        timeout=30,
    )
    resp.raise_for_status()
    for job in (resp.json().get("data") or []):
        if job.get("slug") == slug:
            raw = job.get("jdtext") or job.get("jdtextdetails") or job.get("description") or ""
            jd = strip_html(raw)
            if jd:
                return jd
    return None


def score_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("s")
    return None


def reason_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("r", "")
    return ""


def clean_label(raw):
    if not raw:
        return "N/A"
    s = str(raw).strip()
    for lab in ("No Fit", "Excellent", "Strong", "Good", "Partial", "Weak"):
        if s.lower().startswith(lab.lower()):
            return lab
    first = s.split()[0].rstrip(".,;:")
    return first if first in KNOWN_LABELS else s[:15]


def formula_score(d):
    if not d:
        return None
    vals = {dk: score_val(d, dk) for dk in DIM_KEYS}
    if any(v is None for v in vals.values()):
        return None
    return round(sum(vals[dk] * DIM_WEIGHTS[dk] for dk in DIM_KEYS) / 100, 1)


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("3-WAY LLM SCORING HEAD-ON COLLISION")
    print("  Ground Truth: Claude Sonnet 4.5 (Anthropic API)")
    print("  Challenger A: DeepSeek (titans10)")
    print("  Challenger B: Gemini (titans8)")
    print("=" * 70)

    # Load system prompt
    with open(SYSTEM_PROMPT_FILE) as f:
        system_prompt = f.read().strip()

    # Load JD map
    with open(JOBS_JSON_FILE) as f:
        raw_jobs = json.load(f)
    jd_map = {j["title"]: j["jd_text"] for j in raw_jobs}

    # Init tokens for both envs
    for env_key in ENVS:
        ensure_token(env_key)
        print(f"  ✓ Token for {ENVS[env_key]['label']}")

    anthropic_client = None if SKIP_SONNET else anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    if SKIP_SONNET:
        print("  Mode: SKIP SONNET (web apps only)\n")
    else:
        print(f"  ✓ Anthropic client ({SONNET_MODEL})\n")

    all_results = []

    for job_name, slug in JOBS:
        print(f"\n{'═' * 70}")
        print(f"JOB: {job_name}")
        print(f"Slug: {slug}")
        print(f"{'═' * 70}")

        # Fetch from both web apps
        deepseek_cands = fetch_env_candidates("deepseek", slug)
        gemini_cands = fetch_env_candidates("gemini", slug)

        # Union of candidate IDs across both envs
        all_ids = set(deepseek_cands.keys()) | set(gemini_cands.keys())
        if not all_ids:
            print("  ✗ No scored candidates from either environment, skipping.")
            continue

        # Resolve JD text
        jd_text = jd_map.get(job_name)
        if not jd_text:
            for k, v in jd_map.items():
                if job_name.lower() in k.lower() or k.lower() in job_name.lower():
                    jd_text = v
                    break
        if not jd_text:
            ensure_token("deepseek")
            jd_text = fetch_jd_text_by_slug(slug, "deepseek")
        if not jd_text:
            print(f"  ✗ No JD text found, skipping.")
            continue

        # For each candidate, get Sonnet score + collect 3-way
        for cid in sorted(all_ids):
            ds = deepseek_cands.get(cid)
            gm = gemini_cands.get(cid)
            cand_name = (ds or gm)["name"]
            resumetext = (ds or gm)["resumetext"]

            sonnet_out, sonnet_usage = None, {}
            if not SKIP_SONNET:
                print(f"  → Sonnet: {cand_name} (ID {cid})...", end=" ", flush=True)
                user_prompt = f"JOB DESCRIPTION:\n{jd_text}\n\nCANDIDATE:\n{resumetext}"
                sonnet_out, sonnet_usage = call_sonnet(anthropic_client, system_prompt, user_prompt)
                print("✓" if sonnet_out else "✗")

            all_results.append({
                "job": job_name,
                "slug": slug,
                "candidate": cand_name,
                "cid": cid,
                "resumetext": resumetext,
                "jd_text": jd_text,
                "sonnet": sonnet_out,
                "sonnet_usage": sonnet_usage,
                "deepseek": ds["llm"] if ds else None,
                "gemini": gm["llm"] if gm else None,
            })

        print(f"  ✓ {len(all_ids)} candidates processed for this job")

    # ── BUILD REPORT ──────────────────────────────────────────
    if not all_results:
        print("\n✗ No results. Exiting.")
        return

    report = []
    json_output = []

    # Stats accumulators
    stats = {
        "ds_diffs": [], "gm_diffs": [],
        "ds_label_match": 0, "gm_label_match": 0,
        "ds_dim_flags": 0, "gm_dim_flags": 0,
        "total": 0,
    }

    current_job = None
    for r in all_results:
        if r["job"] != current_job:
            current_job = r["job"]
            report.append(f"\n{'═' * 70}")
            report.append(f"JOB: {current_job}  (slug: {r['slug']})")
            report.append(f"{'═' * 70}")

        sn = r["sonnet"]
        ds = r["deepseek"]
        gm = r["gemini"]

        report.append(f"\n  CANDIDATE: {r['candidate']}  (ID {r['cid']})")
        report.append(f"  {'─' * 66}")
        report.append(f"  {'DIM':<12} {'SONNET':>8} {'DEEPSEEK':>10} {'GEMINI':>8}  {'S-DS':>5} {'S-GM':>5}")
        report.append(f"  {'─' * 12} {'─' * 8} {'─' * 10} {'─' * 8}  {'─' * 5} {'─' * 5}")

        dim_rows = []
        for dk in DIM_KEYS:
            sn_s = score_val(sn, dk)
            ds_s = score_val(ds, dk)
            gm_s = score_val(gm, dk)

            d_ds = (sn_s - ds_s) if (sn_s is not None and ds_s is not None) else None
            d_gm = (sn_s - gm_s) if (sn_s is not None and gm_s is not None) else None

            fl_ds = " ⚠" if (d_ds is not None and abs(d_ds) > FLAG_DIM) else ""
            fl_gm = " ⚠" if (d_gm is not None and abs(d_gm) > FLAG_DIM) else ""
            if fl_ds:
                stats["ds_dim_flags"] += 1
            if fl_gm:
                stats["gm_dim_flags"] += 1

            sn_col = str(sn_s) if sn_s is not None else "—"
            ds_col = str(ds_s) if ds_s is not None else "—"
            gm_col = str(gm_s) if gm_s is not None else "—"
            d_ds_s = f"{d_ds:+d}" if d_ds is not None else "—"
            d_gm_s = f"{d_gm:+d}" if d_gm is not None else "—"

            lbl = f"{DIM_LABELS[dk]} ({dk})"
            report.append(f"  {lbl:<12} {sn_col:>8} {ds_col:>10} {gm_col:>8}  {d_ds_s:>5}{fl_ds} {d_gm_s:>5}{fl_gm}")
            dim_rows.append({"dim": dk, "sonnet": sn_s, "deepseek": ds_s, "gemini": gm_s})

        # Overall
        sn_o = sn.get("o") if sn else None
        ds_o = ds.get("o") if ds else None
        gm_o = gm.get("o") if gm else None

        sn_l = clean_label(sn.get("l") if sn else None)
        ds_l = clean_label(ds.get("l") if ds else None)
        gm_l = clean_label(gm.get("l") if gm else None)

        sn_or = (sn or {}).get("or", "")
        ds_or = (ds or {}).get("or", "")
        gm_or = (gm or {}).get("or", "")

        sn_fs = formula_score(sn)

        d_sds = (sn_o - ds_o) if (sn_o is not None and ds_o is not None) else None
        d_sgm = (sn_o - gm_o) if (sn_o is not None and gm_o is not None) else None

        if d_sds is not None:
            stats["ds_diffs"].append(abs(d_sds))
        if d_sgm is not None:
            stats["gm_diffs"].append(abs(d_sgm))
        if sn_l == ds_l:
            stats["ds_label_match"] += 1
        if sn_l == gm_l:
            stats["gm_label_match"] += 1

        report.append(f"  {'─' * 12} {'─' * 8} {'─' * 10} {'─' * 8}  {'─' * 5} {'─' * 5}")
        report.append(f"  {'OVERALL':<12} {str(sn_o) if sn_o is not None else '—':>8} "
                      f"{str(ds_o) if ds_o is not None else '—':>10} "
                      f"{str(gm_o) if gm_o is not None else '—':>8}  "
                      f"{(str(d_sds) if d_sds is not None else '—'):>5} "
                      f"{(str(d_sgm) if d_sgm is not None else '—'):>5}")
        report.append(f"  {'LABEL':<12} {sn_l:>8} {ds_l:>10} {gm_l:>8}  "
                      f"{'✓' if sn_l == ds_l else '✗':>5} {'✓' if sn_l == gm_l else '✗':>5}")

        if sn_fs is not None:
            report.append(f"  {'FORMULA':<12} {str(sn_fs):>8}   (Sonnet dims weighted)")

        # Overall Reasoning
        report.append(f"\n  OVERALL REASONING:")
        report.append(f"    Sonnet:   {sn_or or '—'}")
        report.append(f"    DeepSeek: {ds_or or '—'}")
        report.append(f"    Gemini:   {gm_or or '—'}")

        # Token usage
        su = r.get("sonnet_usage") or {}
        if su:
            report.append(f"  SONNET TOKENS: in={su.get('input_tokens', '—')} out={su.get('output_tokens', '—')}")

        # Dimension reasoning
        report.append(f"\n  DIMENSION REASONING:")
        for dk in DIM_KEYS:
            report.append(f"    {dk} | Sonnet:   {reason_val(sn, dk) or '—'}")
            report.append(f"       | DeepSeek: {reason_val(ds, dk) or '—'}")
            report.append(f"       | Gemini:   {reason_val(gm, dk) or '—'}")

        stats["total"] += 1
        json_output.append({
            "job": r["job"],
            "slug": r["slug"],
            "candidate": r["candidate"],
            "cid": r["cid"],
            "resumetext": r["resumetext"],
            "dims": dim_rows,
            "overall": {
                "sonnet": sn_o, "deepseek": ds_o, "gemini": gm_o,
                "sonnet_formula": sn_fs,
                "diff_sonnet_deepseek": d_sds,
                "diff_sonnet_gemini": d_sgm,
            },
            "label": {"sonnet": sn_l, "deepseek": ds_l, "gemini": gm_l},
            "reasoning": {
                "sonnet_or": sn_or, "deepseek_or": ds_or, "gemini_or": gm_or,
            },
            "sonnet_raw": sn,
            "deepseek_raw": ds,
            "gemini_raw": gm,
            "sonnet_usage": su,
        })

    # ── AGGREGATE SUMMARY ─────────────────────────────────────
    n = stats["total"]
    ds_avg = sum(stats["ds_diffs"]) / len(stats["ds_diffs"]) if stats["ds_diffs"] else 0
    gm_avg = sum(stats["gm_diffs"]) / len(stats["gm_diffs"]) if stats["gm_diffs"] else 0
    ds_max = max(stats["ds_diffs"]) if stats["ds_diffs"] else 0
    gm_max = max(stats["gm_diffs"]) if stats["gm_diffs"] else 0

    report.append(f"\n\n{'═' * 70}")
    report.append("AGGREGATE SUMMARY — 3-WAY COMPARISON")
    report.append(f"{'═' * 70}")
    report.append(f"  Ground truth:                  Claude Sonnet 4.5")
    report.append(f"  Jobs processed:                {len(set(r['job'] for r in json_output))}")
    report.append(f"  Total candidates:              {n}")
    report.append(f"")
    report.append(f"  {'METRIC':<35} {'DeepSeek':>10} {'Gemini':>10}")
    report.append(f"  {'─' * 35} {'─' * 10} {'─' * 10}")
    report.append(f"  {'Avg |Sonnet - X| overall diff':<35} {ds_avg:>9.1f}p {gm_avg:>9.1f}p")
    report.append(f"  {'Max |Sonnet - X| overall diff':<35} {ds_max:>9}p {gm_max:>9}p")
    report.append(f"  {'Label match rate vs Sonnet':<35} "
                  f"{stats['ds_label_match']}/{n} ({100*stats['ds_label_match']//n if n else 0}%) "
                  f"{stats['gm_label_match']}/{n} ({100*stats['gm_label_match']//n if n else 0}%)")
    report.append(f"  {'Dimension flags ⚠ (>±{FLAG_DIM})':<35} {stats['ds_dim_flags']:>10} {stats['gm_dim_flags']:>10}")

    # Winner determination
    report.append(f"\n  VERDICT:")
    if ds_avg < gm_avg:
        report.append(f"    → DeepSeek is CLOSER to Sonnet (avg diff {ds_avg:.1f} vs {gm_avg:.1f})")
    elif gm_avg < ds_avg:
        report.append(f"    → Gemini is CLOSER to Sonnet (avg diff {gm_avg:.1f} vs {ds_avg:.1f})")
    else:
        report.append(f"    → TIE (both avg diff {ds_avg:.1f})")

    # Token summary
    total_in = sum((r.get("sonnet_usage") or {}).get("input_tokens", 0) for r in all_results)
    total_out = sum((r.get("sonnet_usage") or {}).get("output_tokens", 0) for r in all_results)
    if not SKIP_SONNET:
        report.append(f"\n  ANTHROPIC USAGE:")
        report.append(f"    Model:         {SONNET_MODEL}")
        report.append(f"    Input tokens:  {total_in:,}")
        report.append(f"    Output tokens: {total_out:,}")
        report.append(f"    Total:         {total_in + total_out:,}")

    # Write files
    payload = {
        "ground_truth": "claude-sonnet-4-5",
        "challengers": ["deepseek (titans10)", "gemini (titans8)"],
        "summary": {
            "total_candidates": n,
            "deepseek_avg_diff": round(ds_avg, 2),
            "gemini_avg_diff": round(gm_avg, 2),
            "deepseek_label_match_pct": round(100 * stats["ds_label_match"] / n, 1) if n else 0,
            "gemini_label_match_pct": round(100 * stats["gm_label_match"] / n, 1) if n else 0,
            "anthropic_tokens": {"input": total_in, "output": total_out},
        },
        "records": json_output,
    }

    report_path = os.path.join(WORKSPACE, "llm_3way_report.txt")
    json_path = os.path.join(WORKSPACE, "llm_3way_report.json")

    report_text = "\n".join(report)
    with open(report_path, "w") as f:
        f.write(report_text)
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    print(report_text)
    print(f"\n✓ Report saved to: {report_path}")
    print(f"✓ JSON saved to:   {json_path}")


if __name__ == "__main__":
    main()

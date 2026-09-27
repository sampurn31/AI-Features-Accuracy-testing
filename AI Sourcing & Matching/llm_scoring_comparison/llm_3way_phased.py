#!/usr/bin/env python3
"""
3-Way Phased LLM Scoring Evaluator
====================================
Runs in two phases (shared DB means we can't hit both web apps at once):

  Phase 1:  python3 llm_3way_phased.py --phase deepseek
            → Hits titans10, saves deepseek scores to llm_3way_deepseek.json

  Phase 2:  python3 llm_3way_phased.py --phase gemini
            → Hits titans8, saves gemini scores to llm_3way_gemini.json

  Report:   python3 llm_3way_phased.py --phase report
            → Merges existing Sonnet data (from llm_3way_report.json) with
              deepseek + gemini phase files → produces final comparison.

Usage:
  1. Run --phase deepseek
  2. Clear DB
  3. Run --phase gemini
  4. Run --phase report
"""

import json
import os
import re
import sys
import time

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

WORKSPACE = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────

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

# Top 4 jobs by candidate count
JOBS = [
    ("High School Mathematics Teacher",     "17799869479085686098Gpi"),   # 29
    ("Construction Project Manager",        "17799872604185686098xdX"),   # 30
    ("Performance Marketing Manager",       "17799868194225686098XDZ"),   # 21
    ("Pediatric Registered Nurse",          "17799867066035686098bXP"),   # 18
]

RECHECK_ACTION_SOURCE = "Job Details Page - Job Related Actions"
MAX_COOLDOWN_WAIT = 20
POLL_INTERVAL = 10
POLL_MAX = 30

# File paths
DEEPSEEK_FILE = os.path.join(WORKSPACE, "llm_3way_deepseek.json")
GEMINI_FILE = os.path.join(WORKSPACE, "llm_3way_gemini.json")
SONNET_FILE = os.path.join(WORKSPACE, "llm_3way_report.json")  # reuse existing Sonnet data
FINAL_REPORT_TXT = os.path.join(WORKSPACE, "llm_3way_final_report.txt")
FINAL_REPORT_JSON = os.path.join(WORKSPACE, "llm_3way_final_report.json")

DIM_KEYS = ["fn", "sk", "lv", "tr", "dm", "lc"]
DIM_LABELS = {"fn": "Function", "sk": "Skills", "lv": "Level",
              "tr": "Trajectory", "dm": "Domain", "lc": "Location"}
DIM_WEIGHTS = {"fn": 25, "sk": 25, "lv": 20, "tr": 15, "dm": 10, "lc": 5}
KNOWN_LABELS = {"Excellent", "Strong", "Good", "Partial", "Weak", "No Fit"}
FLAG_DIM = 15
FLAG_OVERALL = 10

# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

_token = ""


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
    global _token
    _token = get_token(env_key)


def headers():
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {_token}",
        "content-type": "application/json",
        "cache-control": "no-cache",
        "pragma": "no-cache",
    }


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
        matching_url(env_key, slug), headers=headers(), json=payload, timeout=60,
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
        matching_url(env_key, slug), headers=headers(),
        json={"page_size": 1, "page": 1, "getcount": True}, timeout=30,
    )
    resp.raise_for_status()
    return normalize_api_root(resp.json())


def poll_scoring(env_key, slug):
    for attempt in range(1, POLL_MAX + 1):
        resp = requests.post(
            matching_url(env_key, slug), headers=headers(),
            json={"page_size": 100, "page": 1}, timeout=30,
        )
        resp.raise_for_status()
        root = normalize_api_root(resp.json())
        if root.get("status") == "fail":
            print(f"      attempt {attempt}: API fail")
            return None
        data = get_matching_payload(root)
        status = data.get("llm_status")
        print(f"      attempt {attempt}/{POLL_MAX}: llm_status={status}, count={data.get('total_count')}")
        if status == "success":
            return data.get("records", [])
        time.sleep(POLL_INTERVAL)
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
# PHASE: FETCH WEB APP SCORES
# ──────────────────────────────────────────────────────────────

def run_webapp_phase(env_key):
    """Fetch scores from one web app env, save to JSON file."""
    label = ENVS[env_key]["label"]
    out_file = DEEPSEEK_FILE if env_key == "deepseek" else GEMINI_FILE

    print(f"{'=' * 70}")
    print(f"PHASE: {label}")
    print(f"Hitting: {ENVS[env_key]['host']}")
    print(f"Jobs: {len(JOBS)}")
    print(f"{'=' * 70}\n")

    ensure_token(env_key)
    print(f"  ✓ Token obtained\n")

    all_candidates = {}  # slug -> { cid -> candidate_data }

    for job_name, slug in JOBS:
        ensure_token(env_key)
        print(f"\n{'─' * 60}")
        print(f"JOB: {job_name} ({slug})")
        print(f"{'─' * 60}")

        # Recheck to force fresh scoring
        print(f"  → Refreshing (recheck=true)...")
        refresh_with_cooldown(env_key, slug)

        print(f"  → Triggering (getcount=true)...")
        trigger_scoring(env_key, slug)

        print(f"  → Polling...")
        time.sleep(POLL_INTERVAL)
        records = poll_scoring(env_key, slug)

        if records is None:
            print(f"  ✗ Timed out, skipping")
            continue

        job_cands = {}
        for r in records:
            scoring = r.get("llm_scoring")
            if not scoring or r.get("llm_status") != "success":
                continue
            job_cands[r.get("id")] = {
                "id": r.get("id"),
                "name": r.get("candidatename", "Unknown"),
                "resumetext": r.get("resumetext", ""),
                "score": r.get("score"),
                "llm": scoring,
            }

        all_candidates[slug] = job_cands
        print(f"  ✓ {len(job_cands)} candidates scored")

    # Save
    output = {
        "env": env_key,
        "label": label,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "jobs": {slug: {str(cid): c for cid, c in cands.items()}
                 for slug, cands in all_candidates.items()},
    }
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2)

    total = sum(len(c) for c in all_candidates.values())
    print(f"\n{'=' * 70}")
    print(f"✓ {label} phase complete: {total} candidates across {len(all_candidates)} jobs")
    print(f"✓ Saved to: {out_file}")
    print(f"{'=' * 70}")


# ──────────────────────────────────────────────────────────────
# PHASE: BUILD REPORT (merge all 3 sources)
# ──────────────────────────────────────────────────────────────

def run_report_phase():
    """Merge Sonnet (from prior run) + DeepSeek + Gemini into final 3-way report."""
    print(f"{'=' * 70}")
    print("PHASE: BUILD FINAL 3-WAY REPORT")
    print(f"{'=' * 70}\n")

    # Load Sonnet data from existing run
    if not os.path.exists(SONNET_FILE):
        raise SystemExit(f"Missing Sonnet data: {SONNET_FILE}")
    with open(SONNET_FILE) as f:
        sonnet_data = json.load(f)
    print(f"  ✓ Loaded Sonnet data: {len(sonnet_data['records'])} records")

    # Build Sonnet lookup: (slug, cid) -> record
    sonnet_map = {}
    for rec in sonnet_data["records"]:
        key = (rec["slug"], rec["cid"])
        sonnet_map[key] = rec

    # Load DeepSeek
    if not os.path.exists(DEEPSEEK_FILE):
        raise SystemExit(f"Missing DeepSeek data: {DEEPSEEK_FILE}")
    with open(DEEPSEEK_FILE) as f:
        ds_data = json.load(f)
    print(f"  ✓ Loaded DeepSeek data ({ds_data['timestamp']})")

    # Load Gemini
    if not os.path.exists(GEMINI_FILE):
        raise SystemExit(f"Missing Gemini data: {GEMINI_FILE}")
    with open(GEMINI_FILE) as f:
        gm_data = json.load(f)
    print(f"  ✓ Loaded Gemini data ({gm_data['timestamp']})")

    # Target slugs
    target_slugs = [slug for _, slug in JOBS]

    report = []
    json_output = []
    stats = {
        "ds_diffs": [], "gm_diffs": [],
        "ds_label_match": 0, "gm_label_match": 0,
        "ds_dim_flags": 0, "gm_dim_flags": 0,
        "total": 0,
    }

    for job_name, slug in JOBS:
        ds_cands = ds_data["jobs"].get(slug, {})
        gm_cands = gm_data["jobs"].get(slug, {})
        all_cids = set(ds_cands.keys()) | set(gm_cands.keys())

        if not all_cids:
            print(f"  ⚠ No candidates for {job_name}, skipping")
            continue

        report.append(f"\n{'═' * 70}")
        report.append(f"JOB: {job_name}  (slug: {slug})")
        report.append(f"{'═' * 70}")

        for cid_str in sorted(all_cids):
            cid = int(cid_str)
            ds_c = ds_cands.get(cid_str)
            gm_c = gm_cands.get(cid_str)

            # Get Sonnet data for this candidate
            sonnet_rec = sonnet_map.get((slug, cid))
            sn = sonnet_rec["sonnet_raw"] if sonnet_rec else None
            sn_usage = sonnet_rec.get("sonnet_usage", {}) if sonnet_rec else {}

            ds = ds_c["llm"] if ds_c else None
            gm = gm_c["llm"] if gm_c else None

            cand_name = (ds_c or gm_c or {}).get("name", sonnet_rec["candidate"] if sonnet_rec else "Unknown")
            resumetext = (ds_c or gm_c or {}).get("resumetext", "")

            report.append(f"\n  CANDIDATE: {cand_name}  (ID {cid})")
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

            # Overall scores
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
                          f"{(f'{d_sds:+d}' if d_sds is not None else '—'):>5} "
                          f"{(f'{d_sgm:+d}' if d_sgm is not None else '—'):>5}")
            report.append(f"  {'LABEL':<12} {sn_l:>8} {ds_l:>10} {gm_l:>8}  "
                          f"{'✓' if sn_l == ds_l else '✗':>5} {'✓' if sn_l == gm_l else '✗':>5}")

            if sn_fs is not None:
                report.append(f"  {'FORMULA':<12} {str(sn_fs):>8}   (Sonnet dims weighted)")

            # Overall Reasoning
            report.append(f"\n  OVERALL REASONING:")
            report.append(f"    Sonnet:   {sn_or or '—'}")
            report.append(f"    DeepSeek: {ds_or or '—'}")
            report.append(f"    Gemini:   {gm_or or '—'}")

            # Dimension reasoning
            report.append(f"\n  DIMENSION REASONING:")
            for dk in DIM_KEYS:
                report.append(f"    {dk} | Sonnet:   {reason_val(sn, dk) or '—'}")
                report.append(f"       | DeepSeek: {reason_val(ds, dk) or '—'}")
                report.append(f"       | Gemini:   {reason_val(gm, dk) or '—'}")

            stats["total"] += 1
            json_output.append({
                "job": job_name,
                "slug": slug,
                "candidate": cand_name,
                "cid": cid,
                "resumetext": resumetext,
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
                "sonnet_usage": sn_usage,
            })

    # Aggregate summary
    n = stats["total"]
    ds_avg = sum(stats["ds_diffs"]) / len(stats["ds_diffs"]) if stats["ds_diffs"] else 0
    gm_avg = sum(stats["gm_diffs"]) / len(stats["gm_diffs"]) if stats["gm_diffs"] else 0
    ds_max = max(stats["ds_diffs"]) if stats["ds_diffs"] else 0
    gm_max = max(stats["gm_diffs"]) if stats["gm_diffs"] else 0

    report.append(f"\n\n{'═' * 70}")
    report.append("FINAL 3-WAY COMPARISON — AGGREGATE SUMMARY")
    report.append(f"{'═' * 70}")
    report.append(f"  Ground truth:                  Claude Sonnet 4.5")
    report.append(f"  Jobs:                          {len(JOBS)} (top 5 by candidate count)")
    report.append(f"  Total candidates:              {n}")
    report.append(f"")
    report.append(f"  {'METRIC':<35} {'DeepSeek':>10} {'Gemini':>10}")
    report.append(f"  {'─' * 35} {'─' * 10} {'─' * 10}")
    report.append(f"  {'Avg |Sonnet - X| overall diff':<35} {ds_avg:>9.1f}p {gm_avg:>9.1f}p")
    report.append(f"  {'Max |Sonnet - X| overall diff':<35} {ds_max:>9}p {gm_max:>9}p")
    ds_pct = 100 * stats["ds_label_match"] // n if n else 0
    gm_pct = 100 * stats["gm_label_match"] // n if n else 0
    report.append(f"  {'Label match rate vs Sonnet':<35} "
                  f"{stats['ds_label_match']}/{n} ({ds_pct}%) "
                  f"{stats['gm_label_match']}/{n} ({gm_pct}%)")
    report.append(f"  {'Dimension flags ⚠ (>±15)':<35} {stats['ds_dim_flags']:>10} {stats['gm_dim_flags']:>10}")

    # Verdict
    report.append(f"\n  ┌{'─' * 50}┐")
    if ds_avg < gm_avg:
        winner = "DeepSeek"
        report.append(f"  │  VERDICT: DeepSeek is CLOSER to Sonnet          │")
        report.append(f"  │  (avg diff {ds_avg:.1f} vs {gm_avg:.1f})                         │")
    elif gm_avg < ds_avg:
        winner = "Gemini"
        report.append(f"  │  VERDICT: Gemini is CLOSER to Sonnet             │")
        report.append(f"  │  (avg diff {gm_avg:.1f} vs {ds_avg:.1f})                         │")
    else:
        winner = "TIE"
        report.append(f"  │  VERDICT: TIE                                    │")
        report.append(f"  │  (both avg diff {ds_avg:.1f})                            │")
    report.append(f"  └{'─' * 50}┘")

    # Write outputs
    payload = {
        "ground_truth": "claude-sonnet-4-5",
        "challengers": ["deepseek (titans10)", "gemini (titans8)"],
        "verdict": winner,
        "summary": {
            "jobs": len(JOBS),
            "total_candidates": n,
            "deepseek_avg_diff": round(ds_avg, 2),
            "gemini_avg_diff": round(gm_avg, 2),
            "deepseek_max_diff": ds_max,
            "gemini_max_diff": gm_max,
            "deepseek_label_match_pct": round(100 * stats["ds_label_match"] / n, 1) if n else 0,
            "gemini_label_match_pct": round(100 * stats["gm_label_match"] / n, 1) if n else 0,
            "deepseek_dim_flags": stats["ds_dim_flags"],
            "gemini_dim_flags": stats["gm_dim_flags"],
        },
        "records": json_output,
    }

    report_text = "\n".join(report)
    with open(FINAL_REPORT_TXT, "w") as f:
        f.write(report_text)
    with open(FINAL_REPORT_JSON, "w") as f:
        json.dump(payload, f, indent=2)

    print(report_text)
    print(f"\n✓ Final report: {FINAL_REPORT_TXT}")
    print(f"✓ Final JSON:   {FINAL_REPORT_JSON}")


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    if "--phase" not in sys.argv:
        print("Usage:")
        print("  python3 llm_3way_phased.py --phase deepseek   # Step 1: fetch DeepSeek scores")
        print("  python3 llm_3way_phased.py --phase gemini     # Step 2: fetch Gemini scores (after DB clear)")
        print("  python3 llm_3way_phased.py --phase report     # Step 3: merge & build final report")
        sys.exit(1)

    idx = sys.argv.index("--phase")
    phase = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""

    if phase == "deepseek":
        run_webapp_phase("deepseek")
    elif phase == "gemini":
        run_webapp_phase("gemini")
    elif phase == "report":
        run_report_phase()
    else:
        raise SystemExit(f"Unknown phase: {phase!r}. Use: deepseek, gemini, or report")


if __name__ == "__main__":
    main()

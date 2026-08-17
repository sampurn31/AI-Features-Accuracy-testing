#!/usr/bin/env python3
"""
Seed the realistic evaluation dataset.

Reuses the creation calls verified earlier in this project: companies POST /companies,
contacts POST /contacts (needs company_slug), jobs POST /jobs (needs both slugs),
candidates POST /candidates with the resume in `candidate_summary`, then notes and call
logs against each candidate.

TWO THINGS THAT WILL BITE YOU
-----------------------------
1. Retrieval and scoring read DIFFERENT things. The pool filters on the structured fields
   (position, skill, work_ex_year, city); only the scorer reads candidate_summary. Seed a
   perfect resume and omit the structured fields and every job returns totalCount 0 with
   overallStatus "fail" — nothing is ever scored. Hence retrieval_fields().
2. Notes cannot be backdated. updated_on is server-assigned and is what activity recency
   reads, so ordering comes from creation order — consecutive items are spaced by
   ORDER_GAP to land in different seconds.

The deployments share ONE database. Seed once; --env only picks who writes.

    python3 seed_dataset.py --env dev
    python3 seed_dataset.py --verify-only
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import credentials                                                    # noqa: E402
from credentials import api_base, describe, resolve_api_key, CONFIG_ENV  # noqa: E402
import dataset                                                        # noqa: E402
from dataset import build_resume                                      # noqa: E402

MANIFEST = os.path.join(HERE, "eval_manifest.json")
MAX_NOTE_CHARS = 2000
ORDER_GAP = 1.1
TIMEOUT = 60
COUNTS = {"companies": 0, "contacts": 0, "jobs": 0, "candidates": 0,
          "notes": 0, "call_logs": 0, "failures": 0}


def log(m):
    print(f"[seed] {m}", flush=True)


def truncate(t, limit=MAX_NOTE_CHARS):
    t = " ".join(str(t).split())
    return t if len(t) <= limit else t[:limit - 3].rstrip() + "..."


def rows(resp):
    """The /search endpoints return {"data": [...]} when non-empty and a bare [] when not."""
    if resp is None or resp.status_code != 200:
        return []
    b = resp.json()
    return b if isinstance(b, list) else (b.get("data", []) or [])


def retrieval_fields(r, job):
    """
    Structured fields for RETRIEVAL only.

    The pool filters on position / skill / experience / location. Omit these and the
    candidate is never pooled, so never scored. Scoring itself ignores them.
    """
    title = r["headline"].split("(")[0].split("-")[0].strip().title()
    skills = re.sub(r"\([^)]*\)", "", " ".join(r["skills"].split()))
    skills = ", ".join(s.strip() for s in skills.split(",") if s.strip())[:240]
    yr = re.search(r"(\d+)\s*(?:\+)?\s*years", r["summary"])
    years = int(yr.group(1)) if yr else job["min_exp"] + 1
    org = ""
    mm = re.search(r"\|\s*([^|,]+)", r.get("role1", ""))
    if mm:
        org = mm.group(1).strip()[:60]
    return {"position": title, "skill": skills, "work_ex_year": years,
            "relevant_experience": max(0, years - 1), "current_organization": org,
            "city": job["city"], "country": job["country"]}


class Api:
    def __init__(self, env, key=None):
        self.base = api_base(env)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {resolve_api_key(env, key)}"})

    def post(self, path, payload, what):
        last = ""
        for i in range(3):
            try:
                r = self.s.post(self.base + path, json=payload, timeout=TIMEOUT)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code} {r.text[:160]}"
                if 400 <= r.status_code < 500 and r.status_code not in (422, 429):
                    break
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(1.5 * (i + 1))
        COUNTS["failures"] += 1
        log(f"  !! {what}: {last}")
        return None

    def get(self, path, params=None):
        return self.s.get(self.base + path, params=params or {}, timeout=TIMEOUT)


def seed_job(api, job, cands, run_id):
    comp = api.post("companies", {
        "company_name": f"{job['company']} ({run_id})",
        "website": f"https://{job['key']}-{run_id}.example.com",
        "contact_number": "1234567890", "city": job["city"]}, "company")
    if not comp:
        return None
    COUNTS["companies"] += 1

    cont = api.post("contacts", {
        "first_name": "Hiring", "last_name": f"Manager {job['key'].title()}",
        "email": f"hm.{job['key']}.{run_id}@example.com", "contact_number": "1234567890",
        "company_slug": comp["slug"], "designation": "Talent Acquisition Manager"}, "contact")
    if not cont:
        return None
    COUNTS["contacts"] += 1

    j = api.post("jobs", {
        "name": f"{job['title']} [{run_id}]", "company_slug": comp["slug"],
        "contact_slug": cont["slug"], "number_of_openings": 1,
        "enable_job_application_form": 1, "job_description_text": job["jd"],
        "minimum_experience": job["min_exp"], "maximum_experience": job["max_exp"],
        "city": job["city"], "country": job["country"], "job_type": 1}, "job")
    if not j:
        return None
    COUNTS["jobs"] += 1
    log(f"[{job['key']:10s}] job {j['slug']}  JD {len(job['jd'])} chars")

    seeded = []
    for i, cand in enumerate(cands):
        first, last = cand["name"]
        resume = build_resume(job, cand)
        c = api.post("candidates", {
            "first_name": first, "last_name": last,
            "email": f"{job['key']}.{i}.{run_id}@example.com",
            "contact_number": f"9{1000000 + i}{run_id[-4:]}",
            "candidate_summary": resume,          # the ONLY thing the scorer reads
            "source": "Activity Context Evaluation",
            **retrieval_fields(cand["r"], job),   # needed to enter the pool at all
        }, f"candidate {first} {last}")
        if not c:
            continue
        COUNTS["candidates"] += 1

        notes = cand.get("notes", [])
        for k, text in enumerate(notes):
            if api.post("notes", {"related_to": c["slug"], "related_to_type": "candidate",
                                  "description": truncate(text)}, "note"):
                COUNTS["notes"] += 1
            if k < len(notes) - 1:
                time.sleep(ORDER_GAP)

        calls = cand.get("calls", [])
        for k, (text, ctype, days_ago) in enumerate(calls):
            started = (datetime.now(timezone.utc) - timedelta(days=days_ago)) \
                .strftime("%Y-%m-%dT%H:%M:%S.000000Z")
            if api.post("call-logs", {
                    "related_to": c["slug"], "related_to_type": "candidate",
                    "call_type": ctype, "call_notes": truncate(text),
                    "call_started_on": started, "contact_number": "9876543210",
                    "duration": "900"}, "call log"):
                COUNTS["call_logs"] += 1
            if k < len(calls) - 1:
                time.sleep(ORDER_GAP)

        seeded.append({"slug": c["slug"], "name": f"{first} {last}", "fit": cand["fit"],
                       "scenario": cand["scenario"], "signal": cand.get("signal"),
                       "notes": [truncate(t) for t in notes],
                       "calls": [truncate(t) for t, _, _ in calls],
                       "resume_chars": len(resume)})
    log(f"[{job['key']:10s}] {len(seeded)} candidates")
    return {"key": job["key"], "title": job["title"], "job_slug": j["slug"],
            "jd": job["jd"], "company_slug": comp["slug"], "contact_slug": cont["slug"],
            "candidates": seeded}


def verify(api, manifest):
    log("verifying ...")
    if not manifest.get("jobs"):
        log("  VERIFY FAILED — manifest contains no jobs")
        return False
    bad = 0
    for job in manifest["jobs"]:
        if api.get(f"jobs/{job['job_slug']}").status_code != 200:
            log(f"  !! job {job['key']} unreadable")
            bad += 1
            continue
        for c in job["candidates"]:
            n = rows(api.get("notes/search", {"related_to": c["slug"],
                                              "related_to_type": "candidate"}))
            cl = rows(api.get("call-logs/search", {"related_to": c["slug"],
                                                   "related_to_type": "candidate"}))
            if len(n) != len(c["notes"]) or len(cl) != len(c["calls"]):
                log(f"  !! {c['name']}: {len(n)}/{len(c['notes'])} notes, "
                    f"{len(cl)}/{len(c['calls'])} calls")
                bad += 1
    log(f"  {'VERIFY PASSED' if not bad else f'VERIFY FAILED — {bad} problems'}")
    return bad == 0


def main():
    p = argparse.ArgumentParser(description="Seed the realistic evaluation dataset")
    p.add_argument("--env", default=CONFIG_ENV)
    p.add_argument("--jobs", type=int, default=0)
    p.add_argument("--verify-only", action="store_true")
    a = p.parse_args()

    api = Api(a.env)
    log(describe(a.env))
    if a.verify_only:
        return 0 if verify(api, json.load(open(MANIFEST))) else 2

    jobs, cands = dataset.load_all()
    if a.jobs:
        jobs = jobs[:a.jobs]
    run_id = str(int(time.time()))
    log(f"env={a.env}  run_id={run_id}  {len(jobs)} jobs")

    out = []
    for job in jobs:
        r = seed_job(api, job, cands.get(job["key"], []), run_id)
        if r:
            out.append(r)

    manifest = {"run_id": run_id, "env": a.env,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "counts": dict(COUNTS), "jobs": out}
    json.dump(manifest, open(MANIFEST, "w"), indent=2)
    log("")
    for k, v in COUNTS.items():
        log(f"  {k:<12} {v}")
    log(f"  manifest     {MANIFEST}")
    return 0 if verify(api, manifest) and not COUNTS["failures"] else 2


if __name__ == "__main__":
    sys.exit(main())

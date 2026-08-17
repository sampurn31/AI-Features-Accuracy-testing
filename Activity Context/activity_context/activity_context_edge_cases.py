#!/usr/bin/env python3
"""
Activity Context (PW-6968) — twin-pair and edge-case seeder.

This is the test suite. `activity_context_seed.py` builds a broad realistic pool; this
builds narrow, adversarial cases where the correct answer is known in advance and written
into the manifest, so `runner.py` can assert on it rather than form an opinion.

THE TWIN-PAIR DESIGN
--------------------
Two candidates, identical in every respect except the one value under test. Because the
resume, the title, the skills and the experience are the same, any difference in scoring
must come from the activity — there is nothing else left to explain it. That is what makes
a single pair interpretable rather than anecdotal.

Each case records `checks`: question, argument and the EXPECTED answer. A check with a
`blocked_reason` is one we know cannot be tested through the public API — recorded rather
than quietly dropped, so the coverage gap stays visible.

KNOWN BLOCKED CASE
------------------
Anything depending on activity older than the recency window. `updated_on` is
server-assigned and ignores every override, and it is the field the pipeline reads.
`call_started_on` CAN be backdated but nothing reads it. So the cutoff cannot be exercised
from outside; both such cases are marked blocked.

    python3 activity_context_edge_cases.py --env dev
    python3 activity_context_edge_cases.py --env dev --cases conflict,absence
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from activity_context_roles import ROLES, BY_KEY                      # noqa: E402
import credentials                                                    # noqa: E402
from credentials import api_base, describe, resolve_api_key, CONFIG_ENV  # noqa: E402

EDGE_MANIFEST = os.path.join(HERE, "activity_context_edge_manifest.json")
MAX_NOTE_CHARS = 2000
ORDER_GAP = 1.1
TIMEOUT = 60

_lock = threading.Lock()
COUNTS = {"companies": 0, "contacts": 0, "jobs": 0, "candidates": 0,
          "notes": 0, "call_logs": 0, "failures": 0}


def manifest_path(env):
    return EDGE_MANIFEST


def log(m):
    print(f"[edge] {m}", flush=True)


def bump(k, n=1):
    with _lock:
        COUNTS[k] = COUNTS.get(k, 0) + n


def truncate(t, limit=MAX_NOTE_CHARS):
    t = " ".join(str(t).split())
    return t if len(t) <= limit else t[:limit - 3].rstrip() + "..."


def iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)) \
        .strftime("%Y-%m-%dT%H:%M:%S.000000Z")


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
        bump("failures")
        log(f"  !! {what}: {last}")
        return None


def build_resume(job, conflict_value, years, rng):
    """
    Identical for both twins except `conflict_value`, which is the single variable.

    Everything else is fixed so a scoring difference has exactly one possible cause.
    """
    return (f"{job['title'].upper()}\n{job['city']}, {job['country']}\n\n"
            f"PROFESSIONAL SUMMARY\n{job['title']} with {years} years in "
            f"{job['industry'].lower()}. {conflict_value}\n\n"
            f"CORE SKILLS\n{', '.join(job['skills'])}\n\n"
            f"EXPERIENCE\n{job['title']} | {job['companies'][0]} | "
            f"{2026 - years} - present\n"
            f"- Delivered work across {', '.join(job['skills'][:3])}.\n\n"
            f"EDUCATION\n{job['degree']}")


def chk(question, arg, expected, role=None, note=None):
    d = {"question": question, "arg": arg, "expected": expected}
    if note:
        d["blocked_reason"] = note
    return d


def case_table(job):
    """
    The cases. Each carries what to seed AND what the right answer is.

    `conflict_value` is the value written into the resume; `notes`/`calls` carry the
    activity. `checks` is what runner.py will assert.
    """
    skill = job["skills"][0]
    alt = job["alt_titles"][0]
    return [
     {"case": "conflict_newer_note_wins",
      "why": "Resume says one thing, a newer note says another. The newer source wins.",
      "conflict_value": f"Available to start immediately.",
      "notes": [f"Note from three weeks ago. Confirmed availability to start immediately.",
                f"Note today. Correction: they are now on a three-month notice period and "
                f"cannot start before then. The resume is out of date on this point."],
      "calls": [],
      "checks": [chk("value_reported", "notice period / start date", "yes"),
                 chk("fact_used", "the candidate is on a three-month notice period", "yes")]},

     {"case": "absence_stated_not_invented",
      "why": "Nothing in the material mentions the skill. The reasoning should say so, "
             "not invent it.",
      "conflict_value": "",
      "notes": ["Left a voicemail, no answer. Will try again tomorrow."],
      "calls": [],
      "checks": [chk("grounded", "", "grounded"),
                 chk("fact_used", f"the candidate has hands-on {skill}", "no")]},

     {"case": "call_log_reveals_hidden_strength",
      "why": f"The resume omits {skill}; a call log records real depth. It should be used.",
      "conflict_value": "",
      "notes": [],
      "calls": [(f"Technical screen. Much stronger on {skill} than the CV suggests - they "
                 f"led that work for eighteen months at {job['companies'][0]}, owned the "
                 f"design and trained the team on it. None of it is on the resume.",
                 "CALL_OUTGOING", 2)],
      "checks": [chk("fact_used", f"the candidate has hands-on {skill}", "yes"),
                 chk("source_of", f"the {skill} assessment", "call_log")]},

     {"case": "soft_signal_not_technical_evidence",
      "why": "Warm praise with no technical content must not support a technical criterion.",
      "conflict_value": "",
      "notes": ["One of the most impressive people I have spoken to this quarter. "
                "Outstanding communicator and very motivated. No technical assessment was "
                "done on this call."],
      "calls": [],
      "checks": [chk("fact_used", f"the candidate has hands-on {skill}", "no"),
                 chk("grounded", "", "grounded")]},

     {"case": "blocker_stated_in_activity",
      "why": "A hard constraint that appears only in the activity should reach the "
             "reasoning.",
      "conflict_value": "",
      "notes": [f"Screening call. Strong on the technical substance. One hard problem: "
                f"they cannot meet the working pattern this role requires and were clear "
                f"this will not change for at least twelve months."],
      "calls": [],
      "checks": [chk("fact_used", "the candidate cannot meet the required working pattern",
                     "yes")]},

     {"case": "recency_window_cutoff",
      "why": "Activity older than the window should be ignored.",
      "conflict_value": "",
      "notes": ["This note is intended to be older than the recency window."],
      "calls": [],
      "checks": [chk("fact_used", "the older note was used", "no",
                     note="updated_on is server-assigned and cannot be backdated through "
                          "the public API, so the cutoff cannot be exercised from outside")]},

     {"case": "recency_window_boundary",
      "why": "Activity right at the window edge.",
      "conflict_value": "",
      "notes": ["This note is intended to sit exactly on the window boundary."],
      "calls": [],
      "checks": [chk("fact_used", "the boundary note was used", "yes",
                     note="same cause as recency_window_cutoff — updated_on cannot be set")]},
    ]


def with_universal_checks(cases):
    """Every case also gets the checks that should hold regardless of what it tests."""
    for c in cases:
        c["checks"] = list(c["checks"]) + [chk("grounded", "", "grounded")]
    return cases


def create_candidate(api, job, name_suffix, resume, rng, conflict_value):
    first = f"Twin{name_suffix}"
    last = job["key"].title()
    run = rng.randint(100000, 999999)
    b = api.post("candidates", {
        "first_name": first, "last_name": last,
        "email": f"{job['key']}.twin{name_suffix}.{run}@example.com",
        "contact_number": f"9{run}", "candidate_summary": resume,
        "source": "Activity Context Edge Cases",
        "position": job["title"], "skill": ", ".join(job["skills"])[:240],
        "work_ex_year": job["max_exp"] - 1,
        "relevant_experience": job["max_exp"] - 2,
        "current_organization": job["companies"][0],
        "city": job["city"], "country": job["country"]}, f"candidate {first}")
    if b:
        bump("candidates")
    return b


def apply_activity(api, slug, items, job, resume_for_bump=None):
    """Notes then calls, spaced so updated_on ordering is unambiguous."""
    notes = items.get("notes", [])
    for k, text in enumerate(notes):
        if api.post("notes", {"related_to": slug, "related_to_type": "candidate",
                              "description": truncate(text)}, "note"):
            bump("notes")
        if k < len(notes) - 1:
            time.sleep(ORDER_GAP)
    calls = items.get("calls", [])
    for k, (text, ctype, days) in enumerate(calls):
        if api.post("call-logs", {
                "related_to": slug, "related_to_type": "candidate", "call_type": ctype,
                "call_notes": truncate(text), "call_started_on": iso_days_ago(days),
                "contact_number": "9876543210", "duration": "900"}, "call log"):
            bump("call_logs")
        if k < len(calls) - 1:
            time.sleep(ORDER_GAP)


def seed_case(api, job, case, run_id, rng):
    """
    Seed ONE twin pair: A carries the activity, B is identical without it.

    B is the control. If A and B score the same, the activity did nothing.
    """
    years = job["max_exp"] - 1
    resume = build_resume(job, case.get("conflict_value", ""), years, rng)
    out = []
    for side, with_activity in (("A", True), ("B", False)):
        c = create_candidate(api, job, f"{case['case'][:8]}{side}", resume, rng,
                             case.get("conflict_value", ""))
        if not c:
            continue
        if with_activity:
            apply_activity(api, c["slug"], case, job)
        out.append({"slug": c["slug"], "name": f"Twin{case['case'][:8]}{side} "
                                               f"{job['key'].title()}",
                    "side": side, "has_activity": with_activity,
                    "case": case["case"], "why": case["why"],
                    "conflict_value": case.get("conflict_value", ""),
                    "notes": [truncate(n) for n in (case["notes"] if with_activity else [])],
                    "calls": [truncate(t) for t, _, _ in
                              (case["calls"] if with_activity else [])],
                    "checks": case["checks"] if with_activity else
                              [chk("fact_used", "any note or call log", "no")]})
    return out


def seed_filler(api, job, run_id, n, rng):
    """
    Filler candidates so the twins are not the entire pool.

    Retrieval has a minimum pool size; without filler the twins can be crowded by whatever
    else happens to be in the account, which makes the comparison noisier.
    """
    out = []
    for i in range(n):
        resume = build_resume(job, "", job["min_exp"] + 1, rng)
        c = create_candidate(api, job, f"fill{i}", resume, rng, "")
        if c:
            out.append({"slug": c["slug"], "name": f"Twinfill{i} {job['key'].title()}",
                        "side": "filler", "has_activity": False, "case": "filler",
                        "why": "pool filler", "notes": [], "calls": [], "checks": []})
    return out


def main():
    p = argparse.ArgumentParser(description="Seed the twin-pair edge cases")
    p.add_argument("--env", default=CONFIG_ENV)
    p.add_argument("--roles", type=int, default=3)
    p.add_argument("--cases", default="", help="comma-separated case names, default all")
    p.add_argument("--filler", type=int, default=4)
    p.add_argument("--seed", type=int, default=6968)
    a = p.parse_args()

    api = Api(a.env)
    log(describe(a.env))
    rng = random.Random(a.seed)
    run_id = str(int(time.time()))
    wanted = {s.strip() for s in a.cases.split(",") if s.strip()}

    out = []
    for role in ROLES[:a.roles]:
        comp = api.post("companies", {
            "company_name": f"Edge {role['industry']} ({run_id})",
            "website": f"https://edge-{role['key']}-{run_id}.example.com",
            "contact_number": "1234567890", "city": role["city"]}, "company")
        if not comp:
            continue
        bump("companies")
        cont = api.post("contacts", {
            "first_name": "Edge", "last_name": f"Manager {role['key'].title()}",
            "email": f"edge.{role['key']}.{run_id}@example.com",
            "contact_number": "1234567890", "company_slug": comp["slug"],
            "designation": "Talent Acquisition Manager"}, "contact")
        if not cont:
            continue
        bump("contacts")
        job = api.post("jobs", {
            "name": f"{role['title']} [edge {run_id}]", "company_slug": comp["slug"],
            "contact_slug": cont["slug"], "number_of_openings": 1,
            "enable_job_application_form": 1, "job_description_text": role["jd"],
            "minimum_experience": role["min_exp"], "maximum_experience": role["max_exp"],
            "city": role["city"], "country": role["country"], "job_type": 1}, "job")
        if not job:
            continue
        bump("jobs")
        log(f"[{role['key']:12s}] job {job['slug']}")

        cands = []
        cases = with_universal_checks(case_table(role))
        for case in cases:
            if wanted and case["case"] not in wanted:
                continue
            cands += seed_case(api, role, case, run_id, rng)
        cands += seed_filler(api, role, run_id, a.filler, rng)
        log(f"[{role['key']:12s}] {len(cands)} candidates "
            f"({sum(1 for c in cands if c['has_activity'])} with activity)")
        out.append({"key": role["key"], "title": role["title"], "job_slug": job["slug"],
                    "jd": role["jd"], "candidates": cands})

    manifest = {"run_id": run_id, "env": a.env, "kind": "edge_cases",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "counts": dict(COUNTS), "jobs": out}
    json.dump(manifest, open(EDGE_MANIFEST, "w"), indent=2)
    log("")
    for k, v in COUNTS.items():
        log(f"  {k:<12} {v}")
    blocked = sum(1 for j in out for c in j["candidates"]
                  for ch in c["checks"] if ch.get("blocked_reason"))
    log(f"  {'blocked':<12} {blocked}  (recorded, not silently dropped)")
    log(f"  manifest     {EDGE_MANIFEST}")
    return 0 if not COUNTS["failures"] else 2


if __name__ == "__main__":
    sys.exit(main())

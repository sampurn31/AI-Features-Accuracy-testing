#!/usr/bin/env python3
"""
Activity Context (PW-6968) — accuracy test data generator.

Seeds, via the RecruitCRM public API (same endpoints the product uses), one company +
contact + job per role, then a pool of candidates against each with notes and call logs
attached.

Per role the pool is:
  strong    meets the JD outright
  medium    adjacent title, partial skills
  weak      under-qualified
  mismatch  wrong domain (retrieval control)

Activity is rendered from templates against the candidate's own profile so a note names
the role's actual skill rather than gesturing at "the area".

TWO THINGS THAT WILL BITE YOU
-----------------------------
1. Notes cannot be backdated. `updated_on` is server-assigned and ignores every override,
   and it is the field activity recency reads. Ordering therefore comes from creation
   order, so consecutive items are spaced by ORDER_GAP to land in different seconds.
2. `call_type` must be CALL_INCOMING or CALL_OUTGOING. Anything else is a 422 and the
   call log is silently lost.

The two deployments share ONE database — seed once. `--env` only picks who writes.

    python3 activity_context_seed.py --env dev
    python3 activity_context_seed.py --env dev --roles 3
    python3 activity_context_seed.py --verify-only
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from activity_context_roles import ROLES          # noqa: E402
import credentials                                # noqa: E402
from credentials import api_base, describe, resolve_api_key, CONFIG_ENV  # noqa: E402

MAX_NOTE_CHARS = 2000
ORDER_GAP = 1.1
TIMEOUT = 60
FITS = ("strong", "medium", "weak", "mismatch")

_lock = threading.Lock()
COUNTS = {"companies": 0, "contacts": 0, "jobs": 0, "candidates": 0,
          "work_history": 0, "notes": 0, "call_logs": 0, "failures": 0}

FIRST = ["Marek", "Sofia", "Devon", "Ines", "Priya", "Tobias", "Amelia", "Callum",
         "Grigor", "Hana", "Nadia", "Yuki", "Omar", "Elena", "Ravi", "Fatima"]
LAST = ["Kowalski", "Almeida", "Marsh", "Berger", "Raghunathan", "Lindqvist", "Frost",
        "Rees", "Petrov", "Okonkwo", "Alvarez", "Haddad", "Farah", "Muller", "Wei", "Kaur"]

# {skill} and {company} are filled from the candidate's own profile — a note that names
# nothing tests nothing.
ACTIVITY = {
    "strong": [
        ("note", "Screening call. {name} is strong on {skill} - walked through the work at "
                 "{company} in real depth and clearly owns it. No concerns on the technical "
                 "side."),
        ("call", "Intro call. Covered the role and the team. {name} asked sensible questions "
                 "about scope and how success is measured."),
    ],
    "medium": [
        ("note", "Interview debrief, panel of two. {name} presents well. The concern is depth "
                 "on {skill}: asked to walk through the work at {company} listed on the CV, "
                 "they could not explain why the approach was chosen or what the trade-offs "
                 "were. Recommend a second round before any decision."),
    ],
    "weak": [
        ("note", "Call notes. {name} is short of the bar on {skill} - nothing at {company} "
                 "touches it, and I said so directly. Worth recording that they completed a "
                 "certification in this area at their own expense last month."),
    ],
    "mismatch": [
        ("note", "Applied speculatively through the careers page. Background is in a "
                 "different field to the one advertised."),
    ],
}


def manifest_path(env):
    return os.path.join(HERE, f"activity_context_manifest.json")


def log(msg):
    print(f"[seed] {msg}", flush=True)


def bump(key, n=1):
    with _lock:
        COUNTS[key] = COUNTS.get(key, 0) + n


def truncate(text, limit=MAX_NOTE_CHARS):
    t = " ".join(str(text).split())
    return t if len(t) <= limit else t[:limit - 3].rstrip() + "..."


class Api:
    def __init__(self, env, key=None):
        self.env = env
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
                # 422 from the parser/validator is often transient; a hard 4xx is not
                if 400 <= r.status_code < 500 and r.status_code not in (422, 429):
                    break
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(1.5 * (i + 1))
        bump("failures")
        log(f"  !! {what}: {last}")
        return None

    def get(self, path, params=None):
        return self.s.get(self.base + path, params=params or {}, timeout=TIMEOUT)


def create_company(api, role, run_id):
    b = api.post("companies", {
        "company_name": f"{role['industry']} Partners {role['key'].title()} ({run_id})",
        "website": f"https://{role['key']}-{run_id}.example.com",
        "contact_number": "1234567890", "city": role["city"]}, "company")
    if b:
        bump("companies")
    return b


def create_contact(api, role, company_slug, run_id):
    b = api.post("contacts", {
        "first_name": "Hiring", "last_name": f"Manager {role['key'].title()}",
        "email": f"hm.{role['key']}.{run_id}@example.com",
        "contact_number": "1234567890", "company_slug": company_slug,
        "designation": "Talent Acquisition Manager"}, "contact")
    if b:
        bump("contacts")
    return b


def create_job(api, role, company_slug, contact_slug, run_id):
    b = api.post("jobs", {
        "name": f"{role['title']} [{run_id}]", "company_slug": company_slug,
        "contact_slug": contact_slug, "number_of_openings": 1,
        "enable_job_application_form": 1,
        "job_description_text": role["jd"],
        "minimum_experience": role["min_exp"], "maximum_experience": role["max_exp"],
        "city": role["city"], "country": role["country"], "job_type": 1}, "job")
    if b:
        bump("jobs")
    return b


def create_candidate(api, profile):
    b = api.post("candidates", {
        "first_name": profile["first"], "last_name": profile["last"],
        "email": profile["email"], "contact_number": profile["phone"],
        "candidate_summary": profile["resume"],
        "source": "Activity Context Evaluation",
        # structured fields exist for RETRIEVAL — omit them and the candidate is never
        # pooled, so never scored
        "position": profile["title"], "skill": profile["skill"],
        "work_ex_year": profile["years"],
        "relevant_experience": max(0, profile["years"] - 1),
        "current_organization": profile["company"],
        "city": profile["city"], "country": profile["country"]}, "candidate")
    if b:
        bump("candidates")
    return b


def add_work_history(api, candidate_slug, profile):
    b = api.post(f"candidates/{candidate_slug}", {
        "work_history": [{"title": profile["title"],
                          "work_company_name": profile["company"],
                          "work_description": profile["role_desc"],
                          "is_currently_working": 1}]}, "work history")
    if b:
        bump("work_history")
    return b


def create_description_note(api, candidate_slug, text):
    b = api.post("notes", {"related_to": candidate_slug, "related_to_type": "candidate",
                           "description": truncate(text)}, "note")
    if b:
        bump("notes")
    return b


def create_call_log(api, candidate_slug, text, call_type="CALL_OUTGOING",
                    duration="900", contact_number="9876543210"):
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    b = api.post("call-logs", {
        "related_to": candidate_slug, "related_to_type": "candidate",
        "call_type": call_type, "call_notes": truncate(text),
        "call_started_on": started, "contact_number": contact_number,
        "duration": duration}, "call log")
    if b:
        bump("call_logs")
    return b


def build_profile(role, fit, index, run_id, rng):
    """A candidate spec. Skills and title vary by fit so the pool spans the range."""
    first = FIRST[index % len(FIRST)]
    last = LAST[(index * 7 + 3) % len(LAST)]
    company = rng.choice(role["companies"])
    if fit == "strong":
        title, skills = role["title"], role["skills"]
        years = rng.randint(max(role["min_exp"] + 1, role["max_exp"] - 2), role["max_exp"])
    elif fit == "medium":
        title, skills = rng.choice(role["alt_titles"]), role["partial_skills"]
        years = rng.randint(role["min_exp"] + 1, role["min_exp"] + 3)
    elif fit == "weak":
        title, skills = rng.choice(role["alt_titles"]), role["partial_skills"][:1]
        years = rng.randint(role["min_exp"] + 1, role["min_exp"] + 2)
    else:
        title = "Operations Coordinator"
        skills = ["scheduling", "reporting"]
        years = rng.randint(role["min_exp"], role["min_exp"] + 3)

    resume = (f"{first} {last}\n{title}\n{role['city']}, {role['country']}\n\n"
              f"PROFESSIONAL SUMMARY\n{title} with {years} years in "
              f"{role['industry'].lower()}.\n\n"
              f"CORE SKILLS\n{', '.join(skills)}\n\n"
              f"EXPERIENCE\n{title} | {company} | {2026 - years} - present\n"
              f"- Delivered work across {', '.join(skills[:3])} in a "
              f"{role['industry'].lower()} setting.\n\n"
              f"EDUCATION\n{role['degree']}")
    return {"first": first, "last": last, "fit": fit, "title": title, "years": years,
            "company": company, "skill": ", ".join(skills),
            "primary_skill": skills[0] if skills else "the core area",
            "role_desc": f"Worked on {', '.join(skills[:3])} at {company}.",
            "city": role["city"], "country": role["country"],
            "email": f"{role['key']}.{fit}.{index}.{run_id}@example.com",
            "phone": f"9{2000000 + index}{run_id[-4:]}", "resume": resume}


def render_activity(template, profile, role):
    return template.format(name=profile["first"], skill=profile["primary_skill"],
                           company=profile["company"], role=role["title"])


def seed_role(api, role, run_id, per_fit, rng):
    comp = create_company(api, role, run_id)
    if not comp:
        return None
    cont = create_contact(api, role, comp["slug"], run_id)
    if not cont:
        return None
    job = create_job(api, role, comp["slug"], cont["slug"], run_id)
    if not job:
        return None
    log(f"[{role['key']:12s}] job {job['slug']}  JD {len(role['jd'])} chars")

    seeded, idx = [], 0
    for fit in FITS:
        for _ in range(per_fit):
            p = build_profile(role, fit, idx, run_id, rng)
            idx += 1
            c = create_candidate(api, p)
            if not c:
                continue
            add_work_history(api, c["slug"], p)
            notes, calls = [], []
            items = ACTIVITY.get(fit, [])
            for n, (kind, tmpl) in enumerate(items):
                text = render_activity(tmpl, p, role)
                if kind == "note":
                    create_description_note(api, c["slug"], text)
                    notes.append(truncate(text))
                else:
                    create_call_log(api, c["slug"], text)
                    calls.append(truncate(text))
                if n < len(items) - 1:
                    time.sleep(ORDER_GAP)
            seeded.append({"slug": c["slug"], "name": f"{p['first']} {p['last']}",
                           "fit": fit, "title": p["title"], "years": p["years"],
                           "email": p["email"], "notes": notes, "calls": calls,
                           "resume_chars": len(p["resume"])})
    log(f"[{role['key']:12s}] {len(seeded)} candidates")
    return {"key": role["key"], "title": role["title"], "industry": role["industry"],
            "job_slug": job["slug"], "company_slug": comp["slug"],
            "contact_slug": cont["slug"], "jd": role["jd"], "candidates": seeded}


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
            r = api.get(f"candidates/{c['slug']}")
            if r.status_code != 200 or not (r.json().get("candidate_summary") or "").strip():
                log(f"  !! {c['name']}: candidate_summary empty in the ATS")
                bad += 1
    log(f"  {'VERIFY PASSED' if not bad else f'VERIFY FAILED — {bad} problems'}")
    return bad == 0


def main():
    p = argparse.ArgumentParser(description="Seed the Activity Context test data")
    p.add_argument("--env", default=CONFIG_ENV)
    p.add_argument("--roles", type=int, default=len(ROLES))
    p.add_argument("--per-fit", type=int, default=2)
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--seed", type=int, default=6968)
    a = p.parse_args()

    api = Api(a.env)
    path = manifest_path(a.env)
    log(describe(a.env))

    if a.verify_only:
        return 0 if verify(api, json.load(open(path))) else 2

    run_id = str(int(time.time()))
    rng = random.Random(a.seed)
    log(f"env={a.env}  run_id={run_id}  {min(a.roles, len(ROLES))} roles  "
        f"{a.per_fit} candidates per fit")

    out = []
    for role in ROLES[:a.roles]:
        r = seed_role(api, role, run_id, a.per_fit, rng)
        if r:
            out.append(r)

    manifest = {"run_id": run_id, "env": a.env,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "counts": dict(COUNTS), "jobs": out}
    json.dump(manifest, open(path, "w"), indent=2)
    log("")
    for k, v in COUNTS.items():
        log(f"  {k:<14} {v}")
    log(f"  {'manifest':<14} {path}")
    return 0 if verify(api, manifest) and not COUNTS["failures"] else 2


if __name__ == "__main__":
    sys.exit(main())

"""
db=1 (My Database) dataset seeder.

Authors, per job archetype, a labeled candidate mix and creates it via the public API
(rcrm_public_client), writing a crash-safe LEDGER (output/db1_ledger_<runid>.jsonl) of every
created record + its ground-truth label. The matching runner reads the ledger.

Buckets per job (self-contained — each job carries its own irrelevants, no cross-job coupling):
  STRONG 5   — full title+skills+summary+history for the role         expect band >=75
  PARTIAL 4  — one core criterion deliberately dropped                expect 45-74
  WEAK 4     — confusable-neighbour role (adjacent family)            expect 20-44
  IRRELEVANT 4 — clearly wrong domain                                 expect <=20 / filtered
  INFERRED 3 — role evidence only in work history, headline keyword   expect >=65
               removed from title+skill (catches keyword-only matching)
= ~20 candidates/job.  10 jobs -> ~200 candidates (well under a few hundred).

  source set_env.sh && source set_prod_urls.sh   # RCRM_ACCOUNT_API_KEY + RCRM_PUBLIC_API_URL
  python3 prep_db1_dataset.py --limit-jobs 1 --run-id smoke1   # smoke seed (1 job, ~20 cands)
  python3 prep_db1_dataset.py --run-id 20260724                # full 10 jobs
  python3 prep_db1_dataset.py --run-id 20260724 --cleanup      # delete exactly what we created
Every record carries the marker [PW6818-db1-<runid>]; --cleanup deletes ONLY ledgered slugs.
"""
import argparse
import json
import os
import random

import config
import rcrm_public_client as pub
from scale_archetypes import ARCHETYPES

# 10 jobs (archetype key -> confusable-neighbour key for WEAK, far key for IRRELEVANT)
JOBS = [
    ("backend-python", "data-engineer", "icu-rn"),
    ("data-scientist", "data-engineer", "restaurant-gm"),
    ("icu-rn", "physical-therapist", "backend-python"),
    ("fpa-analyst", "staff-accountant", "hvac-tech"),
    ("saas-ae", "sdr", "civil-engineer"),
    ("supply-chain-analyst", "procurement-manager", "graphic-designer"),
    ("product-manager", "product-marketing", "warehouse-manager"),
    ("salesforce-dev", "backend-java", "executive-chef"),
    ("civil-engineer", "construction-pm", "digital-marketing"),
    ("digital-marketing", "seo-content", "network-engineer"),
]
_BY_KEY = {a["key"]: a for a in ARCHETYPES}
_FIRST = ["Alex", "Jordan", "Taylor", "Morgan", "Riley", "Casey", "Sam", "Jamie", "Avery", "Quinn",
          "Drew", "Reese", "Rowan", "Skyler", "Cameron", "Devon", "Elliot", "Harper", "Kai", "Logan"]
_LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Silva", "Haddad", "Novak", "Rossi", "Andersson",
         "Okafor", "Costa", "Meyer", "Ivanov", "Suzuki", "Dubois", "Fischer", "Mwangi", "Reyes"]
_CITY = [("Austin", "TX", "USA"), ("London", "", "United Kingdom"), ("Berlin", "", "Germany"),
         ("Toronto", "ON", "Canada"), ("Bengaluru", "", "India"), ("Sydney", "", "Australia")]


def _name(rng):
    return rng.choice(_FIRST), rng.choice(_LAST) + rng.choice(["", "", "-Lee", "s"])


def _mk(rng, arch, bucket, marker, idx, seniority):
    """Author one candidate's create-fields + work history + judge-evidence profile."""
    fn, ln = _name(rng)
    city, state, country = rng.choice(_CITY)
    skills = list(arch["skills"])
    resp = arch["resp"]
    yrs = {"STRONG": rng.randint(8, 15), "PARTIAL": rng.randint(4, 7), "WEAK": rng.randint(2, 5),
           "IRRELEVANT": rng.randint(5, 12), "INFERRED": rng.randint(7, 13)}[bucket]
    title = arch["title"]
    summary_bits = resp[:2]

    if bucket == "PARTIAL":
        skills = skills[1:]                       # drop the headline skill/criterion
        summary_bits = resp[1:3]
    elif bucket == "WEAK":
        skills = skills[3:6] or skills[-2:]        # only tangential skills
        summary_bits = resp[-2:]
    elif bucket == "INFERRED":
        headline = skills[0]
        skills = [s for s in skills[1:] if headline.lower() not in s.lower()]  # hide keyword
        title = title.replace(headline, "").strip() or arch["title"].split()[-1]

    skill_str = ", ".join(skills)
    work_desc = ". ".join(resp[:3])
    if bucket == "INFERRED":                        # signal ONLY in the work narrative
        work_desc = (f"{resp[0]} using {arch['skills'][0]} extensively. " + ". ".join(resp[1:3]))
    summary = (f"[PW6818-db1-{marker}] {title} with {yrs} years of experience. "
               + " ".join(s.rstrip('.') + '.' for s in summary_bits)
               + (f" Core skills: {skill_str}." if skill_str else ""))
    company = rng.choice(["Acme Corp", "Globex", "Initech", "Umbrella Labs", "Hooli", "Stark Industries"])

    fields = {
        "first_name": fn, "last_name": ln,
        "email": f"pw6818db1.{marker}.{idx}@example.com",
        "position": title, "skill": skill_str, "candidate_summary": summary,
        "work_ex_year": yrs, "relevant_experience": max(1, yrs - 1),
        "city": city, "state": state, "country": country,
        "current_organization": company, "specialization": arch["fam"],
    }
    work = [{"title": title, "company": company, "work_description": work_desc}]
    profile = {"firstname": fn, "lastname": ln, "position": title, "company": company,
               "city": city, "state": state, "country": country,
               "keySkills": skills, "workexpyr": yrs, "description": summary,
               "experience": [{"title": title, "company": company, "description": work_desc}],
               "education": []}
    return fields, work, profile


def _plan_job(job_key, weak_key, far_key, rng):
    arch = _BY_KEY[job_key]
    plan = []
    for _ in range(5):
        plan.append(("STRONG", arch))
    for _ in range(4):
        plan.append(("PARTIAL", arch))
    for _ in range(4):
        plan.append(("WEAK", _BY_KEY.get(weak_key, arch)))
    for _ in range(4):
        plan.append(("IRRELEVANT", _BY_KEY.get(far_key, arch)))
    for _ in range(3):
        plan.append(("INFERRED", arch))
    return arch, plan


def _ledger_path(run_id):
    return os.path.join(config.OUTPUT_DIR, f"db1_ledger_{run_id}.jsonl")


def _load_ledger(run_id):
    p = _ledger_path(run_id)
    rows = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _append(run_id, rec):
    with open(_ledger_path(run_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def cleanup(run_id):
    rows = _load_ledger(run_id)
    # delete in dependency order: candidates, jobs, contacts, companies
    order = {"candidate": 0, "job": 1, "contact": 2, "company": 3}
    n = 0
    for rec in sorted(rows, key=lambda r: order.get(r["type"], 9)):
        slug = rec.get("slug")
        if not slug:
            continue
        fn = {"candidate": pub.delete_candidate, "job": pub.delete_job,
              "contact": pub.delete_contact, "company": pub.delete_company}.get(rec["type"])
        if fn:
            st = fn(slug)
            n += 1
            print(f"  delete {rec['type']:<9} {slug} -> {st}")
    print(f"cleanup: attempted {n} deletes from ledger {run_id}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit-jobs", type=int, default=0, help="seed only the first N jobs (smoke)")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore any existing ledger (re-create)")
    args = ap.parse_args()
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    if args.cleanup:
        cleanup(args.run_id)
        return

    existing = {} if args.fresh else {(r.get("job_key"), r.get("bucket"), r.get("idx")): r
                                      for r in _load_ledger(args.run_id) if r["type"] == "candidate"}
    done_jobs = {r["job_key"]: r["slug"] for r in _load_ledger(args.run_id) if r["type"] == "job"} \
        if not args.fresh else {}
    if args.fresh and os.path.exists(_ledger_path(args.run_id)):
        os.remove(_ledger_path(args.run_id))

    jobs = JOBS[: args.limit_jobs] if args.limit_jobs else JOBS
    rng = random.Random(6818)
    print(f"Public API: {config.RCRM_PUBLIC_API_URL}  | seeding {len(jobs)} job(s), run-id {args.run_id}")
    idx = 0
    for job_key, weak_key, far_key in jobs:
        arch, plan = _plan_job(job_key, weak_key, far_key, rng)
        jd = _job_jd(arch)
        if job_key in done_jobs:
            job_slug = done_jobs[job_key]
            print(f"[job {job_key}] reuse existing slug {job_slug}")
        else:
            cs, css, _ = pub.create_company(f"[PW6818-db1-{args.run_id}] {arch['title']} Co")
            ct, cts, _ = pub.create_contact(css or "", "Hiring", "Manager",
                                            f"pw6818db1.{args.run_id}.{job_key}@example.com") if css else (None, None, None)
            js, jslug, jraw = pub.create_job(css or "", cts or "", f"[PW6818-db1-{args.run_id}] {arch['title']}", jd)
            if jslug is None:
                print(f"[job {job_key}] CREATE FAILED (company={cs}/{css} job={js}) -> {str(jraw)[:200]}")
                continue
            job_slug = jslug
            _append(args.run_id, {"type": "company", "slug": css, "run_id": args.run_id, "job_key": job_key})
            _append(args.run_id, {"type": "contact", "slug": cts, "run_id": args.run_id, "job_key": job_key})
            _append(args.run_id, {"type": "job", "slug": job_slug, "run_id": args.run_id,
                                  "job_key": job_key, "title": arch["title"], "jd_text": jd})
            print(f"[job {job_key}] created slug={job_slug}")

        made = 0
        for bucket, bucket_arch in plan:
            idx += 1
            key = (job_key, bucket, idx)
            if key in existing:
                continue
            fields, work, profile = _mk(rng, bucket_arch, bucket, args.run_id, idx,
                                        arch.get("snr", ("mid",)))
            st, slug, raw = pub.create_candidate(fields)
            if slug is None:
                print(f"  cand FAIL [{bucket}] http={st} {str(raw)[:140]}")
                continue
            if work:
                pub.add_work_history(slug, work)
            _append(args.run_id, {"type": "candidate", "slug": slug, "run_id": args.run_id,
                                  "job_key": job_key, "bucket": bucket, "idx": idx,
                                  "profile": profile})
            made += 1
        print(f"[job {job_key}] seeded {made} candidates")
    print(f"\nLedger -> {_ledger_path(args.run_id)}")


def _job_jd(arch):
    """A rich job_description_text (what 1.1 turns into weighted criteria)."""
    lines = [f"{arch['title']}", "",
             "Responsibilities:", *[f"- {r}" for r in arch["resp"]],
             "", "Requirements:", *[f"- {r}" for r in arch["reqs"]],
             "", "Nice to have: " + ", ".join(arch["nice"]),
             "", "Core skills: " + ", ".join(arch["skills"])]
    return "\n".join(lines)


if __name__ == "__main__":
    main()

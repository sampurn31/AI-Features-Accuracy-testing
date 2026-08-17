#!/usr/bin/env python3
"""
Build a working corpus from the REAL data already in the account.

Nothing here is invented. Jobs and candidates are read back from the public API; the only
thing this project adds later is notes and call logs, written against what each candidate
actually has on file.

Why this shape:
  * Candidates in this account are structured imports — no resume file, no
    candidate_summary. What they do have is work_history with real descriptions
    (768-10,155 chars, median ~1,500), plus position, skill and work_ex_year.
  * So "resume content" here means the work history text. That is what the scorer reads
    for these people, so it is what the activity has to be written against.

    python3 corpus.py --build      # fetch and cache
    python3 corpus.py --stats      # what is in the cache
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config as C  # noqa: E402

CORPUS = os.path.join(HERE, "corpus.json")
JOBS_CACHE = os.path.join(HERE, "jobs_full.json")
ENV = "dev"
WANT_CANDIDATES = 400
SCAN_PAGES = 150


def clean(t):
    t = re.sub(r"<[^>]+>", " ", str(t or ""))
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&rsquo;", "'")
    t = re.sub(r"u200b|u00a0|&middot;|&ndash;", " ", t)
    return " ".join(t.split())


class Api:
    def __init__(self, env=ENV):
        self.b = C.api_base(env)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {C.api_key(env)}"})

    def get(self, path, params=None, tries=3):
        for _ in range(tries):
            try:
                r = self.s.get(self.b + path, params=params or {}, timeout=90)
                if r.status_code == 200:
                    return r.json()
            except requests.RequestException:
                pass
        return None

    def page(self, path, n):
        """This API has no last_page on /candidates — paginate until an empty page."""
        j = self.get(path, {"page": n})
        return (j.get("data", []) if isinstance(j, dict) else j) or []


def fetch_jobs(api):
    """Distinct jobs with a usable JD. The account holds prefilter/union duplicate pairs."""
    rows, p = [], 1
    while p <= 20:
        got = api.page("jobs", p)
        if not got:
            break
        rows += got
        p += 1
    seen = {}
    for j in rows:
        d = api.get(f"jobs/{j['slug']}")
        if not d:
            continue
        jd = re.sub(r"<[^>]+>", "\n", d.get("job_description_text") or "")
        jd = re.sub(r"&nbsp;?", " ", jd)
        jd = "\n".join(l.strip() for l in jd.split("\n") if l.strip())
        if len(jd) < 400:
            continue
        base = re.sub(r"^(prefilter|union)", "", str(d.get("name"))).strip()
        if base not in seen or len(jd) > len(seen[base]["jd"]):
            seen[base] = {"slug": j["slug"], "name": base, "city": d.get("city"),
                          "min": d.get("minimum_experience"),
                          "max": d.get("maximum_experience"), "jd": jd}
    return list(seen.values())


def candidate_profile(api, slug):
    """Everything the scorer could read for this person, plus the fields retrieval uses."""
    d = api.get(f"candidates/{slug}")
    if not d:
        return None
    wh = d.get("work_history") or []
    eh = d.get("education_history") or []
    roles = [{"title": clean(w.get("title")), "company": clean(w.get("work_company_name")),
              "desc": clean(w.get("work_description")),
              "current": bool(w.get("is_currently_working"))} for w in wh]
    body = " ".join(r["desc"] for r in roles)
    return {
        "slug": slug,
        "name": f"{clean(d.get('first_name'))} {clean(d.get('last_name'))}".strip(),
        "email": d.get("email") or "",
        "position": clean(d.get("position")),
        "skill": clean(d.get("skill")),
        "work_ex_year": d.get("work_ex_year") or 0,
        "relevant_experience": d.get("relevant_experience"),
        "city": clean(d.get("city")), "country": clean(d.get("country")),
        "current_organization": clean(d.get("current_organization")),
        "summary": clean(d.get("candidate_summary")),
        "has_resume_file": bool((d.get("resume") or {}).get("filename")),
        "roles": roles,
        "education": [f"{clean(e.get('educational_qualification'))} "
                      f"{clean(e.get('institute_name'))}".strip() for e in eh],
        # the single number that decides whether this profile is worth using
        "content_chars": len(body) + len(clean(d.get("candidate_summary"))),
    }


def build(api, want=WANT_CANDIDATES):
    print("scanning the candidate list ...", flush=True)
    with ThreadPoolExecutor(max_workers=10) as p:
        pages = list(p.map(lambda n: api.page("candidates", n), range(1, SCAN_PAGES + 1)))
    rows = [x for pg in pages for x in pg]
    print(f"  {len(rows)} candidate rows")

    order = [x["slug"] for x in rows]
    profiles, seen, batch = [], set(), 0
    while len(profiles) < want and batch * 400 < len(order):
        chunk = order[batch * 400:(batch + 1) * 400]
        with ThreadPoolExecutor(max_workers=12) as p:
            got = [x for x in p.map(lambda s: candidate_profile(api, s), chunk) if x]
        for g in got:
            if g["slug"] in seen:
                continue
            seen.add(g["slug"])
            if g["content_chars"] >= 400 and g["position"] and g["skill"]:
                profiles.append(g)
        batch += 1
        print(f"  batch {batch}: {len(profiles)} usable profiles so far", flush=True)
    profiles.sort(key=lambda x: -x["content_chars"])
    return profiles[:want]


def add(slugs):
    """Fetch extra candidates (e.g. ones a pool returned) into the existing corpus."""
    api = Api()
    d = json.load(open(CORPUS))
    have = {c["slug"]: c for c in d["candidates"]}
    need = [s for s in slugs if s not in have]
    with ThreadPoolExecutor(max_workers=12) as p:
        got = [x for x in p.map(lambda s: candidate_profile(api, s), need) if x]
    for g in got:
        have[g["slug"]] = g
    d["candidates"] = list(have.values())
    json.dump(d, open(CORPUS, "w"), indent=1)
    return len(got)


def main():
    p = argparse.ArgumentParser(description="Build the corpus from real account data")
    p.add_argument("--build", action="store_true")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--want", type=int, default=WANT_CANDIDATES)
    a = p.parse_args()

    if a.build:
        api = Api()
        jobs = fetch_jobs(api)
        json.dump(jobs, open(JOBS_CACHE, "w"), indent=1)
        print(f"  {len(jobs)} distinct jobs with a real JD")
        cands = build(api, a.want)
        json.dump({"jobs": jobs, "candidates": cands}, open(CORPUS, "w"), indent=1)
        print(f"\nwrote {CORPUS}: {len(jobs)} jobs, {len(cands)} candidates")

    if a.stats or a.build:
        d = json.load(open(CORPUS))
        cs = d["candidates"]
        ch = sorted(x["content_chars"] for x in cs)
        print(f"\njobs       {len(d['jobs'])}")
        print(f"candidates {len(cs)}")
        print(f"  content chars  min {ch[0]}  median {ch[len(ch) // 2]}  max {ch[-1]}")
        print(f"  with resume file  {sum(1 for x in cs if x['has_resume_file'])}")
        exp = sorted(x["work_ex_year"] for x in cs)
        print(f"  work_ex_year   min {exp[0]}  median {exp[len(exp) // 2]}  max {exp[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

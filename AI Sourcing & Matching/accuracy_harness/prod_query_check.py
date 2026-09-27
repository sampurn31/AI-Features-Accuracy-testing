"""
READ-ONLY prod check for a curated query list. For each query prints:
  totalResults · the GENERATED filter (job-titles / must-skills / locations / companies) · #candidates
so you can SEE why a query is 0 (tight filter) or many (loose filter). Page-1 only, no writes.

  source set_env.sh && source set_prod_urls.sh && ./.venv/bin/python prod_query_check.py
  ...                                          --file output/filter_friendly_queries.txt
Default query list = the filter-friendly set (loose filters, designed to return results on prod).
"""
import argparse
import json
import os

import config
import rcrm_auth
import sourcing_client as SC

# Loose-filter queries (title / title+data-rich region / skill-as-"a plus"). No US-country,
# no narrow city, no stacked must-have skills — the three things that generate a 0-filter.
DEFAULT_QUERIES = [
    "Software Engineer",
    "Data Engineer",
    "Data Analyst",
    "Registered Nurse",
    "Accountant",
    "Sales Manager",
    "Project Manager",
    "Mechanical Engineer",
    "Marketing Manager",
    "Financial Analyst",
    "Product Manager in India",
    "Software Engineer in India",
    "Data Scientist in India",
    "Customer Success Manager",
    "Recruiter",
]


def _load(path):
    qs = []
    for line in open(path):
        s = line.strip()
        if s and not s.startswith("#"):
            qs.append(s)
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="text file, one query per line (# = comment)")
    args = ap.parse_args()
    queries = _load(args.file) if args.file else DEFAULT_QUERIES

    tok = config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]
    print(f"ENV={config.RCRM_ENV}  neptune={config.RCRM_NEPTUNE_URL}")
    print(f"account={config.LOGIN_EMAIL}   |   {len(queries)} queries (read-only, page 1)\n")
    print(f"{'query':<40}{'total':>8}{'cands':>7}{'v':>4}   filter: titles/skills/locs")
    print("-" * 100)
    ok = 0
    for ai in queries:
        st, res = SC.scored_search(ai_search=ai, page=1, token=tok, timeout=180)
        m = res.get("metadata") or {}
        total = m.get("totalResults")
        try:
            qu = json.loads(m.get("queryUsed") or "{}")
        except (ValueError, TypeError):
            qu = {}
        sk = qu.get("skills") or []
        if isinstance(sk, dict):
            sk = (sk.get("must_have") or []) + (sk.get("good_to_have") or [])
        n = len(res.get("candidates") or [])
        good = (total or 0) > 0 and n > 0
        ok += 1 if good else 0
        note = res.get("error") or res.get("message") or ""
        yoe = qu.get("years_of_experience") or {}
        yoe_s = (f"{yoe.get('min','')}-{yoe.get('max','')}".strip("-") if yoe else "")
        inds = qu.get("industries") or []
        filt = (f"t{len(qu.get('job_titles') or [])}/s{len(sk)}/l{len(qu.get('locations') or [])}"
                f"/i{len(inds)}" + (f"/y{yoe_s}" if yoe_s else ""))
        detail = f"[{','.join(sk)[:22]}|{','.join(qu.get('locations') or [])[:16]}]" if (sk or qu.get("locations")) else ""
        print(f"{ai[:47]:<48}{str(total):>7}{n:>6}{'OK' if good else '0':>4}   {filt:<16}{detail}")
    print(f"\n{ok}/{len(queries)} returned candidates.  (filter key: t=titles s=must-skills l=locations i=industries y=years)")


if __name__ == "__main__":
    main()

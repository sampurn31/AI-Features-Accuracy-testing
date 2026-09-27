"""
READ-ONLY prod smoke for AI Sourcing (Global/db=2). Page-1 only, NO history writes,
NO judging — just: does a basic query return candidates right now?

Runs a matrix to isolate WHY a query returns 0:
  - user's exact text vs the stored top-20 text ("Hiring:" prefix)
  - with matchingCriteria vs without
  - a few very basic queries

  source set_env.sh && source set_prod_urls.sh && python3 prod_smoke.py
Prints, per case: HTTP, totalResults, scoringApplied, #candidates, top sample, message/error.
"""
import config
import rcrm_auth
import sourcing_client as SC

DE = [{"text": "Data engineering (Spark/Airflow/Python) experience", "weight": 8},
      {"text": "Based in India", "weight": 6}]
GEN = [{"text": "Relevant professional experience for the role", "weight": 8}]

# (label, aiSearch, criteria)
CASES = [
    ("user_exact  (no prefix, NO criteria)", "Data Engineer, Mumbai, India. Key skills: Spark, Airflow, Python.", None),
    ("user_exact  (no prefix, WITH criteria)", "Data Engineer, Mumbai, India. Key skills: Spark, Airflow, Python.", DE),
    ("stored top20 ('Hiring:' + criteria)", "Hiring: Data Engineer, Mumbai, India. Key skills: Spark, Airflow, Python.", DE),
    ("basic: 'Data Engineer in India'", "Data Engineer in India", DE),
    ("basic: 'Software Developer in India'", "Software Developer in India", GEN),
    ("basic: 'Software Engineer' (no criteria)", "Software Engineer", None),
    ("basic: 'Software Engineer' (WITH criteria)", "Software Engineer", GEN),
    ("basic: 'Data Engineer' (bare, no criteria)", "Data Engineer", None),
]


def main():
    tok = config.RCRM_ALBATROSS_TOKEN
    if not tok:
        info = rcrm_auth.login()
        tok = info["token"]
        who = info.get("account")
    else:
        who = "(pre-set token)"
    print(f"ENV={config.RCRM_ENV}  neptune={config.RCRM_NEPTUNE_URL}")
    print(f"account={config.LOGIN_EMAIL}  {who}\n")
    print(f"{'case':<44}{'HTTP':>5}{'total':>8}{'scored':>8}{'cands':>7}   sample / note")
    print("-" * 110)
    for label, ai, crit in CASES:
        status, res = SC.scored_search(ai_search=ai, matching_criteria=crit, page=1, token=tok, timeout=180)
        meta = res.get("metadata") or {}
        cands = res.get("candidates") or []
        total = meta.get("totalResults")
        scored = res.get("scoring_applied")
        note = ""
        if cands:
            c = cands[0]
            note = f"{(c.get('firstname') or '')} {(c.get('lastname') or '')} | {c.get('position')} | score={c.get('matchScore')}"
        elif res.get("message"):
            note = f"msg: {res['message']}"
        elif res.get("error"):
            note = f"ERR: {res['error'][:70]}"
        print(f"{label:<44}{str(status):>5}{str(total):>8}{str(scored):>8}{len(cands):>7}   {note}")
    print("\n(read-only: page-1 only, no history rows created)")


if __name__ == "__main__":
    main()

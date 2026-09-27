"""
Probe how query PHRASING drives the auto-generated OpenSearch filter (the thing that
decides 0 vs many results). For each query we print totalResults + the parsed `queryUsed`
(the generated filter): #titles, must-have skills, locations, companies, YoE. Tight filter
(skills + city) -> few/0 ; loose filter (title, maybe country) -> large pool.

  RCRM_ENV=dev ./.venv/bin/python dev_probe.py
Read-only, page-1 only. Prints a table; also dumps output/dev_probe.json.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import config
import rcrm_auth
import sourcing_client as SC

# ── Matrix A: same role, escalating specificity (shows what each addition costs) ──
SHAPE_ROLE = "Data Engineer"
SHAPES = [
    ("title only", SHAPE_ROLE),
    ("title + country", f"{SHAPE_ROLE} in India"),
    ("title + US", f"{SHAPE_ROLE} in the United States"),
    ("title + city", f"{SHAPE_ROLE} in Mumbai"),
    ("title + 1 skill", f"{SHAPE_ROLE} with Python"),
    ("title + 3 skills (must)", f"{SHAPE_ROLE} with Python, Spark and Airflow"),
    ("title + skills 'a plus'", f"{SHAPE_ROLE}; Python, Spark, Airflow a plus"),
    ("seniority + title", f"Senior {SHAPE_ROLE}"),
    ("old top20 shape", f"{SHAPE_ROLE}, Mumbai, India. Key skills: Spark, Airflow, Python."),
]

# ── Matrix B: the loose recipe across many roles (title, title+country) ──
ROLES = ["Software Engineer", "Data Analyst", "Registered Nurse", "Accountant",
         "Sales Manager", "Project Manager", "Mechanical Engineer", "Marketing Manager",
         "Customer Success Manager", "Financial Analyst", "Product Manager", "Recruiter"]


def _probe(ai, tok):
    st, res = SC.scored_search(ai_search=ai, page=1, token=tok, timeout=150)
    m = res.get("metadata") or {}
    total = m.get("totalResults")
    try:
        qu = json.loads(m.get("queryUsed") or "{}")
    except (ValueError, TypeError):
        qu = {}
    sk = qu.get("skills") or []
    if isinstance(sk, dict):
        sk = (sk.get("must_have") or []) + (sk.get("good_to_have") or [])
    return {"q": ai, "total": total, "http": st, "cands": len(res.get("candidates") or []),
            "titles": len(qu.get("job_titles") or []), "skills": sk,
            "locs": qu.get("locations") or [], "cos": qu.get("companies") or [],
            "yoe": qu.get("years_of_experience") or {}, "err": res.get("error")}


def main():
    tok = config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]
    print(f"ENV={config.RCRM_ENV}  neptune={config.RCRM_NEPTUNE_URL}\n")

    def run(pairs):
        with ThreadPoolExecutor(max_workers=6) as ex:
            return list(ex.map(lambda p: (p[0], _probe(p[1], tok)), pairs))

    print("== MATRIX A: escalating specificity (same role) ==")
    print(f"{'shape':<26}{'total':>8}{'titles':>7}{'#sk':>5}{'#loc':>5}  skills / locs")
    resA = run([(lbl, q) for lbl, q in SHAPES])
    for lbl, r in resA:
        print(f"{lbl:<26}{str(r['total']):>8}{r['titles']:>7}{len(r['skills']):>5}{len(r['locs']):>5}"
              f"  {','.join(r['skills'])[:24]:<26}{','.join(r['locs'])[:20]}")

    print("\n== MATRIX B: loose recipe across roles (title only  vs  title + country) ==")
    print(f"{'role':<26}{'title-only':>12}{'+in India':>12}{'+in US':>12}")
    pairsB = []
    for role in ROLES:
        pairsB += [(f"{role}|bare", role), (f"{role}|india", f"{role} in India"),
                   (f"{role}|us", f"{role} in the United States")]
    resB = dict(run(pairsB))
    tableB = []
    for role in ROLES:
        b = resB[f"{role}|bare"]["total"]; i = resB[f"{role}|india"]["total"]; u = resB[f"{role}|us"]["total"]
        print(f"{role:<26}{str(b):>12}{str(i):>12}{str(u):>12}")
        tableB.append({"role": role, "bare": b, "india": i, "us": u})

    json.dump({"env": config.RCRM_ENV, "matrixA": [r for _, r in resA], "matrixB": tableB},
              open("output/dev_probe.json", "w"), ensure_ascii=False, indent=2)
    print("\ndump -> output/dev_probe.json")


if __name__ == "__main__":
    main()

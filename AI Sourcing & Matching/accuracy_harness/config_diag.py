"""Dump the RAW prod config (criteria-generation) response so we can see why the harness fell
back to LLM criteria. READ-ONLY.
  source set_env.sh && source set_prod_urls.sh && ./.venv/bin/python config_diag.py
"""
import json

import config
import rcrm_auth
import rcrm_client as R

tok = config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]
print("JOB_SVC:", config.RCRM_JOB_SERVICE_URL, "| acct:", config.LOGIN_EMAIL, "| db:", config.DB_GLOBAL_DATABASE)

CASES = [
    ("long-semiconductor",
     "Senior Application Engineer with MUST-HAVE hands-on semiconductor ATE experience on Advantest "
     "V93000 (93K), Teradyne UltraFLEX, or J750. Semiconductor test experience is the primary requirement."),
    ("short", "software engineer in india"),
]

for label, q in CASES:
    print("\n" + "=" * 70 + f"\nCASE: {label}")
    try:
        st, d = R.get_configuration(ai_search=q, database_type=config.DB_GLOBAL_DATABASE, token=tok, timeout=60)
        d = d or {}
        print("HTTP:", st)
        print("top-level keys:", list(d.keys()))
        print("status field:", d.get("status"))
        crit = d.get("criteria")
        print("criteria type:", type(crit).__name__, "| len:",
              len(crit) if isinstance(crit, (list, dict)) else "n/a")
        if isinstance(crit, list) and crit:
            print("first-criterion KEYS:", list(crit[0].keys()))
            print("first criterion:", json.dumps(crit[0], ensure_ascii=False))
        q_ = d.get("questions")
        print("questions:", len(q_) if isinstance(q_, list) else q_)
        print("RAW (first 2800 chars):")
        print(json.dumps(d, ensure_ascii=False, indent=2)[:2800])
    except Exception as e:  # noqa: BLE001
        print("EXCEPTION:", type(e).__name__, "-", str(e)[:300])

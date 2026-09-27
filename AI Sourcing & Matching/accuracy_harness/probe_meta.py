import json, config, rcrm_auth, sourcing_client as SC
tok = config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]
for ai in ["Data Engineer", "Data Engineer, Mumbai, India. Key skills: Spark, Airflow, Python."]:
    st, res = SC.scored_search(ai_search=ai, page=1, token=tok, timeout=120)
    m = res.get("metadata") or {}
    print("="*70); print("QUERY:", ai); print("total:", m.get("totalResults"), "| keys:", list(m.keys()))
    for k in m:
        if k != "type":
            print(f"  {k}: {json.dumps(m[k])[:600]}")

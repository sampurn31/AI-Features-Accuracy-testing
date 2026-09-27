import json, config, rcrm_auth, sourcing_client as SC
from concurrent.futures import ThreadPoolExecutor
tok = config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]
Q = [
 "Software Engineer in New York", "Software Engineer in San Francisco",
 "Data Engineer in Texas", "Accountant in London", "Software Engineer in United Kingdom",
 "Data Engineer in Bangalore", "Nurse in California", "Sales Manager in Dubai",
 "Software Engineer in Germany", "Software Engineer in Canada", "Software Engineer in Remote",
 "Software Engineer in USA",
]
def probe(ai):
    st,res=SC.scored_search(ai_search=ai,page=1,token=tok,timeout=150)
    m=res.get("metadata") or {}
    try: qu=json.loads(m.get("queryUsed") or "{}")
    except Exception: qu={}
    return ai, m.get("totalResults"), qu.get("locations") or []
with ThreadPoolExecutor(max_workers=6) as ex:
    rows=list(ex.map(probe,Q))
print(f"{'query':<40}{'total':>8}   locations")
for ai,t,loc in rows:
    print(f"{ai:<40}{str(t):>8}   {loc}")

import config, rcrm_auth, sourcing_client as SC
tok = rcrm_auth.login()["token"]
crit=[{"text":"Software development experience","weight":8},{"text":"Based in India","weight":6}]
st,res = SC.scored_search(ai_search="software developer in india", matching_criteria=crit, page=1, token=tok, timeout=180)
m=res.get("metadata") or {}
cs=res.get("candidates") or []
print("http",st,"| total",m.get("totalResults"),"| scoringApplied",res.get("scoring_applied"),"| cands",len(cs))
for c in cs[:3]:
    ma=c.get("matchAnalysis") or []
    print(f"  {c.get('firstname')} {c.get('lastname')} | score={c.get('matchScore')} | crit_scored={len(ma)}")
    if ma: print("    e.g.", ma[0].get("criterionText"), "->", ma[0].get("score"))

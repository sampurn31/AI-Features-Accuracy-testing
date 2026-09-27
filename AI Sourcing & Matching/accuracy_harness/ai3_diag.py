import config, rcrm_auth, rcrm_client
import sourcing_client as SC
tok = rcrm_auth.login()["token"]
ai = "software engineer in india"
print("JOB_SVC url:", config.RCRM_JOB_SERVICE_URL)
# 1) config / criteria generation
try:
    st, cfg = rcrm_client.get_configuration(ai_search=ai, database_type=config.DB_GLOBAL_DATABASE, token=tok, timeout=30)
    cfg = cfg or {}
    print(f"CONFIG http={st} state={cfg.get('status')} criteria={len(cfg.get('criteria') or [])} filters={'yes' if cfg.get('filters') else 'no'}")
    if cfg.get('criteria'): print("  e.g.", [(c.get('text'),c.get('weight')) for c in cfg['criteria'][:2]])
except Exception as e:
    print("CONFIG ERR:", str(e)[:120])
# 2) search with NO criteria -> does new code auto-score?
st, res = SC.scored_search(ai_search=ai, page=1, token=tok, timeout=120)
cs = res.get("candidates") or []
print(f"SEARCH no-criteria: http={st} scoringApplied={res.get('scoring_applied')} cands={len(cs)} "
      f"sample_score={cs[0].get('matchScore') if cs else None} crit={len(cs[0].get('matchAnalysis') or []) if cs else 0}")
# 3) search WITH a manual criterion -> scoring works?
mc=[{"text":"Software development experience","weight":8},{"text":"Based in India","weight":6}]
st, res = SC.scored_search(ai_search=ai, matching_criteria=mc, page=1, token=tok, timeout=120)
cs = res.get("candidates") or []
print(f"SEARCH with-criteria: http={st} scoringApplied={res.get('scoring_applied')} cands={len(cs)} "
      f"sample_score={cs[0].get('matchScore') if cs else None}")

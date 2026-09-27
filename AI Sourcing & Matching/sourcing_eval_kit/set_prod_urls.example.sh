# ─────────────────────────────────────────────────────────────────────────────
#  Prod overrides for the AI Sourcing (Global, db=2) runs.
#      cp set_prod_urls.example.sh set_prod_urls.sh
#  Fill in the login, then source it AFTER set_env.sh:
#      source set_env.sh && source set_prod_urls.sh
#  The external sourcing corpus only returns candidates on prod, so every
#  candidate/scoring run (run_sourcing*, run_sourcing_eval, prod_*) needs this.
#  set_prod_urls.sh is gitignored — never commit it.
# ─────────────────────────────────────────────────────────────────────────────
export RCRM_ALBATROSS_URL=https://albatross.recruitcrm.io/v1     # prod auth gateway (login)
export RCRM_NEPTUNE_URL=https://vms-neptune.recruitcrm.io/v1     # prod Neptune (ai-sourcing-v3/search)
export RCRM_JOB_SERVICE_URL=https://prodjob.recruitcrm.io/v2     # prod job-microservice (config / criteria)
export RCRM_LOGIN_EMAIL=""                                        # prod test account
export RCRM_LOGIN_PASSWORD=""
# export RCRM_ALBATROSS_TOKEN=""                                  # paste a bearer to skip login

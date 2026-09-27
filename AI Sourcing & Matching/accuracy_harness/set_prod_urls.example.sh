# ---------------------------------------------------------------------------
# Prod endpoint + SHARED-ACCOUNT config for the PW-6818 day-2 AI Sourcing suite.
# Holds the login for the shared internal prod test account a test account
# (team-owned, cleared for shared use), so a developer running this suite only
# needs to add their OWN ANTHROPIC_API_KEY to set_env.sh — nothing else.
#
#   source set_env.sh && source set_prod_urls.sh && python run_sourcing.py --limit 2
#
# WARNING: shared throwaway test account. Keep this file in the (un-committed)
#          harness dir only — do NOT commit it to the main repo or share publicly.
# Prod is the recruitcrm.io family with no env prefix; note neptune = "vms-neptune".
# ---------------------------------------------------------------------------
export RCRM_ALBATROSS_URL=https://albatross.recruitcrm.io/v1   # prod auth gateway (login)
export RCRM_NEPTUNE_URL=https://vms-neptune.recruitcrm.io/v1   # prod Neptune (ai-sourcing-v3/search)
export RCRM_JOB_SERVICE_URL=https://prodjob.recruitcrm.io/v2   # prod job-microservice (config-stage 1.1/1.2)
export RCRM_LOGIN_EMAIL=""                 # test account
export RCRM_LOGIN_PASSWORD=""                              # test account
# To use a pasted bearer instead of email/password login, set RCRM_ALBATROSS_TOKEN here.

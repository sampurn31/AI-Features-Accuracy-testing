#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
#  Accuracy harness — local env.   cp set_env.example.sh set_env.sh
#  Fill in, then:   source set_env.sh
#  set_env.sh is gitignored — never commit it.
# ─────────────────────────────────────────────────────────────────────────────

# ── Required ─────────────────────────────────────────────────────────────────
export ANTHROPIC_API_KEY=""            # your own key — reference model + judge
export RCRM_LOGIN_EMAIL=""             # test account with unified_ai_sourcing_matching enabled
export RCRM_LOGIN_PASSWORD=""

# ── Target environment (non-prod) ───────────────────────────────────────────
# URLs are derived from RCRM_ENV:  albatross-<env>.recruitcrm.net/v1,
# <env>job.recruitcrm.net/v2, <env>-neptune.recruitcrm.net/v1, <env>-aries.recruitcrm.net/v2
export RCRM_ENV="dev"

# ── Optional overrides ───────────────────────────────────────────────────────
# export RCRM_JOB_SERVICE_URL=""       # when the job host doesn't follow <env>job.recruitcrm.net
# export RCRM_ALBATROSS_TOKEN=""       # paste a bearer to skip the login step
# export ACCURACY_MODEL="claude-sonnet-5"

# ── My Database (db=1) seeding only — prep_db1_dataset.py / run_matching_scale.py ──
# export RCRM_ACCOUNT_API_KEY=""       # the account's PUBLIC API key (Settings → API), not the password
# export RCRM_PUBLIC_API_URL=""        # defaults to https://<env>api.recruitcrm.net/v1
# export RCRM_ARIES_URL=""             # defaults to https://<env>-aries.recruitcrm.net/v2

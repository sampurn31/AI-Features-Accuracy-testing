#!/bin/sh
# Local, gitignored env for the accuracy harness.
#
# The ONLY thing you must fill in is your Anthropic key on the line below.
# Everything else (ss5 login, job-service URL, the flag-enabled test account) is
# handled automatically by config.py — no token to paste, no reaper.
#
# Usage:
#   source set_env.sh
#   python3 check_env.py        # confirm all green (run on corp/VPN so ss5job resolves)
#   python run.py --limit 3     # smoke test
#   python run.py               # full golden set

export ANTHROPIC_API_KEY=""

# --- optional overrides (uncomment only if a default is wrong) ---------------
# export RCRM_ENV="ss5"
# export RCRM_JOB_SERVICE_URL="https://ss5job.recruitcrm.net/v2"   # real ss5 job host
# export RCRM_LOGIN_EMAIL=""
# export RCRM_LOGIN_PASSWORD=""
# export RCRM_ALBATROSS_TOKEN=""   # paste a token here to skip the login step entirely

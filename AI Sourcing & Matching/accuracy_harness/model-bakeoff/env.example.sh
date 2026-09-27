# ─────────────────────────────────────────────────────────────────────────────
#  Model bake-off — environment template.  Copy to  env.sh,  fill in, then:
#      source env.sh
#  (env.sh is gitignored so your keys never get committed / zipped by mistake.)
# ─────────────────────────────────────────────────────────────────────────────

# ── 1. AUTH — all env credentials in one place ───────────────────────────────
# The bake-off calls the env's POST /v1/ai-sourcing-v3/search for the baseline
# scoring. Authenticate ONE of two ways:
#   A) paste a bearer token  -> set RCRM_ALBATROSS_TOKEN; login is skipped.
#   B) email + password      -> leave the token blank; it logs in for you.
export RCRM_ENV="ss7"                       # env name (URL prefix); ignored if the URLs below are set
export RCRM_ALBATROSS_TOKEN=""              # (A) paste a bearer to skip login
export RCRM_LOGIN_EMAIL=""   # (B) used only when the token is blank
export RCRM_LOGIN_PASSWORD=""                  # (B)

# ── 2. ENV URLs (the external corpus returns candidates ONLY on prod) ────────
export RCRM_NEPTUNE_URL="https://neptune.recruitcrm.io/v1"          # <-- set to your prod neptune
export RCRM_JOB_SERVICE_URL="https://job.recruitcrm.io/v2"         # used only to auto-generate criteria

# ── 3. CHALLENGER MODEL KEYS (one per slot you enable in challenger_llms.SLOTS) ─
# A slot whose key is empty is simply skipped — it never breaks the run.
export GEMINI_API_KEY=""                     # google slots (gemini-2.5-flash-lite / flash)
export ANTHROPIC_API_KEY=""                  # anthropic slots + the final "which model wins" judge
export OPENAI_API_KEY=""                     # openai slots
export DEEPSEEK_API_KEY=""                   # openai_compat slot (base_url set in the slot)
export XAI_API_KEY=""                        # openai_compat slot (Grok)
# add more as you add slots…

# ── 4. OPTIONAL TUNING ───────────────────────────────────────────────────────
export BAKEOFF_WORKERS="8"                    # parallel scoring calls
# export BAKEOFF_SCORE_TOL="10"               # |Δoverall| within this counts as "agrees with env"
# export BAKEOFF_CRIT_TOL="15"                # |Δper-criterion| within this counts as "agrees"
# export BAKEOFF_MAX_TOKENS="1600"            # output cap per scoring call
# export ENV_MODEL_LABEL="env · Gemini 2.5 Flash Lite"   # baseline label shown in the report

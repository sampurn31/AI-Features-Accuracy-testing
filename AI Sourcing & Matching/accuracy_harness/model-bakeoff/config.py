"""
Central config for the PW-6818 AI accuracy harness.

Everything sensitive comes from environment variables — nothing is hardcoded.
The Anthropic key is read by the SDK itself (ANTHROPIC_API_KEY); it is never
referenced directly here, so this file is safe to commit/share.

Required env vars at run time:
  ANTHROPIC_API_KEY        - your Anthropic key (read by the SDK)
  RCRM_JOB_SERVICE_URL     - e.g. https://<env>job.recruitcrm.net/v2   (no trailing slash)
  RCRM_ALBATROSS_TOKEN     - Albatross bearer token for a unified_ai_sourcing_matching-flagged account
Optional:
  ACCURACY_MODEL           - default claude-sonnet-5
"""
import os

# --- Model -------------------------------------------------------------------
# Sonnet 5: "very good, cost-effective, not Opus/Fable". Used for BOTH the
# reference-generation leg and the judge. (Do NOT set temperature — Sonnet 5
# rejects it; depth is controlled via effort.)
MODEL = os.environ.get("ACCURACY_MODEL", "claude-sonnet-5")

REFERENCE_EFFORT = "medium"   # reference generation needs to actually produce good criteria/filters
JUDGE_EFFORT = "low"          # judge just grades — keep it cheap and terse

REFERENCE_MAX_TOKENS = 2500
JUDGE_MAX_TOKENS = 1500

# ══════════════════════════════════════════════════════════════════════════════
#  AUTH — everything needed to authenticate the env baseline call, in ONE place.
#  Two ways to auth (pick either):
#    A) paste a bearer JWT  -> set RCRM_ALBATROSS_TOKEN and login is skipped.
#    B) email + password    -> leave the token blank; the harness logs in and uses
#       the returned ~30-day Albatross JWT as the Bearer.
#  The account must have the unified_ai_sourcing_matching flag on. Run against PROD
#  (external corpus returns candidates only on prod).
# ══════════════════════════════════════════════════════════════════════════════
RCRM_ENV = os.environ.get("RCRM_ENV", "ss7")               # envname (URL prefix for derived URLs)

# — Option A: paste a token to skip login entirely —
RCRM_ALBATROSS_TOKEN = os.environ.get("RCRM_ALBATROSS_TOKEN", "")
# — Option B: email + password (used only when the token above is empty) —
LOGIN_EMAIL = os.environ.get("RCRM_LOGIN_EMAIL", "")
LOGIN_PASSWORD = os.environ.get("RCRM_LOGIN_PASSWORD", "")
# Albatross auth gateway (login endpoint). Pattern: albatross-<env>.recruitcrm.net/v1
ALBATROSS_URL = os.environ.get(
    "RCRM_ALBATROSS_URL", f"https://albatross-{RCRM_ENV}.recruitcrm.net/v1").rstrip("/")
LOGIN_URL = ALBATROSS_URL + "/login"

# --- Service URLs (not auth; derived from RCRM_ENV, override for non-standard hosts) ---
# job-microservice base — used ONLY to auto-generate criteria from the query
# (skipped when you pass --criteria or --no-config).
RCRM_JOB_SERVICE_URL = os.environ.get(
    "RCRM_JOB_SERVICE_URL", f"https://{RCRM_ENV}job.recruitcrm.net/v2").rstrip("/")

# Force a databaseType for EVERY case (RCRM_FORCE_DB=1 runs the whole suite on My Database).
# For db=1 the harness sends {jobSlug: <anchor>, jdText: <case input>, databaseType: 1}.
FORCE_DB = os.environ.get("RCRM_FORCE_DB")
# A real jobSlug used as the db=1 access anchor (auto-fetched at runtime if left empty).
JOB_ANCHOR = os.environ.get("RCRM_JOB_ANCHOR", "")

# 1.1 config endpoint (questions + criteria + filters generation)
CONFIG_PATH = "ai-matching/configuration"
# 1.2 criteria->filters endpoint (final query generation from finalized criteria)
CRITERIA_PATH = "ai-matching/configuration/criteria"

DB_MY_DATABASE = 1
DB_GLOBAL_DATABASE = 2

# --- Paths -------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(HERE, "prompts")
GOLDEN_DIR = os.path.join(HERE, "golden")
OUTPUT_DIR = os.path.join(HERE, "output")

# The REAL Neptune system prompts (you will drop these in). The reference leg
# runs THESE exact prompts through Sonnet 5, so it's same-prompt / different-model.
# One per databaseType — GLOBAL is what aiSearch (job-less) calls use; MY_DATABASE
# is used for jobSlug-anchored cases.
NEPTUNE_GLOBAL_DB_SYSTEM_PROMPT = os.path.join(PROMPTS_DIR, "neptune_global_database_config_system_prompt.txt")
NEPTUNE_MY_DB_SYSTEM_PROMPT = os.path.join(PROMPTS_DIR, "neptune_my_database_config_system_prompt.txt")
JUDGE_RUBRIC = os.path.join(PROMPTS_DIR, "judge_rubric.md")

# Criteria->filters ("final query generation") prompts + rubric (1.2 endpoint).
NEPTUNE_GLOBAL_C2F_PROMPT = os.path.join(PROMPTS_DIR, "neptune_global_criteria_to_filters_system_prompt.txt")
NEPTUNE_MY_C2F_PROMPT = os.path.join(PROMPTS_DIR, "neptune_my_criteria_to_filters_system_prompt.txt")
CRITERIA_JUDGE_RUBRIC = os.path.join(PROMPTS_DIR, "criteria_judge_rubric.md")
# Answer-influence ("impact of answers on filters") rubric (metamorphic, 1.1 with answers).
ANSWER_INFLUENCE_RUBRIC = os.path.join(PROMPTS_DIR, "answer_influence_rubric.md")

# --- Day 2: AI Sourcing V3 / Global (db=2) scored search (Neptune 2.1) --------
# POST /v1/ai-sourcing-v3/search — sources candidates from the EXTERNAL corpus
# (Coresignal/OpenSearch) and, when matchingCriteria is supplied, scores the top
# 100 (matchScore 0-100, matchLabel, per-criterion matchAnalysis{satisfied,reasoning}).
# Neptune base. Non-prod pattern is https://<env>-neptune.recruitcrm.net/v1 ; PROD is
# the recruitcrm.io family (no env prefix) so set RCRM_NEPTUNE_URL explicitly for prod.
# NOTE: the external sourcing corpus only returns candidates on PROD — dev/ss return 0
# results, so day-2 candidate/scoring accuracy MUST run on prod.
RCRM_NEPTUNE_URL = os.environ.get(
    "RCRM_NEPTUNE_URL", f"https://{RCRM_ENV}-neptune.recruitcrm.net/v1").rstrip("/")
SOURCING_SEARCH_PATH = "ai-sourcing-v3/search"

SOURCING_JUDGE_RUBRIC = os.path.join(PROMPTS_DIR, "sourcing_judge_rubric.md")
# Per-criterion audit rubric (scale run): one verdict per matchAnalysis row.
SOURCING_CRITERION_RUBRIC = os.path.join(PROMPTS_DIR, "sourcing_criterion_rubric.md")
# |judge_score - system score| beyond which the judge marks too_high/too_low.
SOURCING_JUDGE_GAP = 15

# --- Day 2: My Database (db=1) AI Matching — scored-candidate accuracy ---------
# Public create API (account API key as Bearer) for SEEDING candidates/jobs. Base is
# <env>api.recruitcrm.net/v1 on non-prod; PROD is the recruitcrm.io family so set
# RCRM_PUBLIC_API_URL explicitly. RCRM_ACCOUNT_API_KEY is the account's PUBLIC API key
# (Settings -> API in the app) — NOT the login password, NOT the Albatross JWT.
RCRM_PUBLIC_API_URL = os.environ.get(
    "RCRM_PUBLIC_API_URL", f"https://{RCRM_ENV}api.recruitcrm.net/v1").rstrip("/")
RCRM_ACCOUNT_API_KEY = os.environ.get("RCRM_ACCOUNT_API_KEY", "")
# Aries advanced-search (1.3 scored search + 1.5 count). Non-prod pattern is
# https://<env>-aries.recruitcrm.net/v2 ; PROD explicit via RCRM_ARIES_URL.
RCRM_ARIES_URL = os.environ.get(
    "RCRM_ARIES_URL", f"https://{RCRM_ENV}-aries.recruitcrm.net/v2").rstrip("/")
MATCH_SEARCH_PATH = "advanced-search/candidates/ai-matching/search/get"   # 1.3
MATCH_COUNT_PATH = "advanced-search/candidates/ai-matching/search/count/get"  # 1.5
# Scoring is async: first 1.3 returns pending; poll 1.5 overallStatus then re-read 1.3.
DB1_POLL_INTERVAL = int(os.environ.get("DB1_POLL_INTERVAL", "5"))
DB1_POLL_MAX = int(os.environ.get("DB1_POLL_MAX", "240"))
DB1_JUDGE_TOP_K = int(os.environ.get("DB1_JUDGE_TOP_K", "6"))   # candidates/job sent to the judges
DB1_RELEVANCE_RUBRIC = os.path.join(PROMPTS_DIR, "db1_relevance_rubric.md")
DB1_SCORING_RUBRIC = SOURCING_CRITERION_RUBRIC   # axis-1 reuses the per-criterion rubric
# Ground-truth expected overall-score bands per seeded bucket (inclusive lo, hi).
DB1_BANDS = {"STRONG": (75, 100), "PARTIAL": (45, 74), "WEAK": (20, 44),
             "IRRELEVANT": (0, 20), "INFERRED": (65, 100)}
GOLDEN_SOURCING_DIR = os.path.join(GOLDEN_DIR, "sourcing")

# How many top-ranked candidates per query to send to the (costlier) LLM judge.
SOURCING_JUDGE_TOP_K = int(os.environ.get("SOURCING_JUDGE_TOP_K", "5"))
# The REAL response gives a per-criterion `score` (0-100); overall matchScore is the
# weighted average of them (confirmed exact on prod). We assert that relationship, and the
# metamorphic direction, within this tolerance (rounding is <0.5, so ±2 has no false pos).
SOURCING_SCORE_TOL = int(os.environ.get("SOURCING_SCORE_TOL", "2"))


def neptune_prompt_path(database_type):
    return (NEPTUNE_GLOBAL_DB_SYSTEM_PROMPT if int(database_type) == DB_GLOBAL_DATABASE
            else NEPTUNE_MY_DB_SYSTEM_PROMPT)


def criteria_prompt_path(database_type):
    return (NEPTUNE_GLOBAL_C2F_PROMPT if int(database_type) == DB_GLOBAL_DATABASE
            else NEPTUNE_MY_C2F_PROMPT)


def require_runtime_env():
    """Fail fast with a clear message if run-time config is missing.

    Only ANTHROPIC_API_KEY is strictly required — the RCRM token is obtained by
    logging in the pre-fed account and the job-service URL defaults from RCRM_ENV.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Missing ANTHROPIC_API_KEY (read by the Anthropic SDK; never stored in code).\n"
            "Export it before running (see README.md). The RCRM token is obtained by logging in "
            "the pre-fed test account on env '" + RCRM_ENV + "'; set RCRM_ALBATROSS_TOKEN to bypass login."
        )

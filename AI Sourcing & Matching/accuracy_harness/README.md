# PW-6818 — AI Accuracy Harness (config stage)

Standalone Python harness that measures the **accuracy** of the Unified Sourcing &
Matching AI config outputs (clarifying questions, matching criteria, filters, and
term disambiguation) — the part that's testable today (pool/candidate/scoring are
deferred while infra changes settle).

Per golden case it produces three things and writes them to Excel for **manual
validation** (we do not trust the judge blindly):

| Column group | What it is |
|---|---|
| Output A | the **current implementation** (`POST /v2/ai-matching/configuration`) |
| Output B | a **reference** — Sonnet 5 running the *same, real Neptune prompt* |
| Verdict + reasoning | Sonnet 5 as **judge**, comparing A vs B against the rubric |

## Layout
```
accuracy-harness/
  config.py         env-driven config (model, ss5 login/URL, paths)
  rcrm_auth.py      logs in the pre-fed ss5 account -> Albatross token
  rcrm_client.py    calls the current implementation (Output A)
  llm.py            Sonnet 5 reference generation (B) + judge
  run.py            orchestrator -> output/accuracy_results_<ts>.xlsx
  check_env.py      pre-flight: Anthropic + login + config endpoint (stdlib)
  set_env.sh        local, gitignored — paste your Anthropic key here
  prompts/
    neptune_global_database_config_system_prompt.txt   (real prompt, in place)
    neptune_my_database_config_system_prompt.txt       (real prompt, in place)
    judge_rubric.md                                    (ready)
  golden/
    ambiguous_terms.json   disambiguation cases (Oxford, Georgia, Java, ...)
    jds.json               config-generation JDs (clear + ambiguous)
```

## Auth is automatic — no token to paste
The harness logs in the test account set in `RCRM_LOGIN_EMAIL` / `RCRM_LOGIN_PASSWORD` (it needs the
`unified_ai_sourcing_matching` flag enabled) via `albatross-<env>.recruitcrm.net/v1/login` and uses the returned ~30-day Albatross JWT
as the Bearer for the config calls. No reaper, no manual token. Paste a token into
`RCRM_ALBATROSS_TOKEN` only if you want to skip the login step.

**Target env = `dev` (public, verified live).** `albatross-dev` login + `devjob.recruitcrm.net`
config both return 200 with full criteria+filters — no VPN needed. To switch to ss5 later, set
`RCRM_ENV=ss5` **and** `RCRM_JOB_SERVICE_URL` to ss5's real (internal) job host, and run on VPN.

## What you provide before running
1. **The real Neptune system prompts** — ✅ already in place (verbatim
   `AI_MATCHING_CONFIGURATION_{GLOBAL,MY}_DATABASE_SYSTEM_PROMPT` from the neptune repo).
2. **Your Anthropic key** — paste it into `set_env.sh` (gitignored), then `source set_env.sh`.
   It is read by the SDK from `ANTHROPIC_API_KEY`; it is never written into tracked code.

That's it — on dev, `python3 check_env.py` should show green on 2 (Anthropic) & 4 (config).

## Contract confirmed (LLD 1.1, live-tested both databaseType values)
- `aiSearch` is **job-less** and **forces `databaseType=2`** — the query text is used directly
  as the JD. That's why the default golden set needs no job seeding.
- `databaseType=1` (My Database) requires a **real `jobSlug`** the token can view (a fake slug → 404).
  Add such cases with a `job_slug` field; the harness sends `{jobSlug, jdText, databaseType:1}`.
- Response `filters` = `{llmFilters, translatedFilters}`. Output A's real extraction lives in
  `filters.llmFilters`; the reference (raw prompt) emits `filters` flat. The judge rubric
  normalizes this — it compares values, not wrapper shape or key casing.
- Invalid/unsafe/gibberish input → **HTTP 400** ("Please enter a valid Job Description or Search
  Query."), not a 200. The judge treats a correct rejection as correct behavior.

## Run
```sh
cd "AI Sourcing & Matching/accuracy_harness"
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
#  paste your Anthropic key into set_env.sh, then:
source set_env.sh
python3 check_env.py        # all green? (login + config endpoint reachable)
python run.py --limit 3     # smoke test
python run.py               # full golden set
```
Output lands in `output/accuracy_results_<timestamp>.xlsx`.

## Model & cost
- Judge + reference both use **`claude-sonnet-5`** (override with `ACCURACY_MODEL`).
- ≈ **$0.05 / case** at Sonnet 5 intro pricing (2 calls/case) → ~500–600 cases within a $30 budget.
  The shared prompt/rubric prefix is sent with `cache_control`, so repeated calls pay cache-read on it.
- Judge runs at `effort=low` (terse, cheap verdicts); reference at `effort=medium`.

## Scope
- **In:** config generation — questions, criteria, `llmFilters`/`translatedFilters`, disambiguation.
- **Deferred:** candidate pool, ranking, per-candidate scoring/reasoning (need the infra work + seeded candidates).
- Default golden cases use `aiSearch` (job-less, Global) so no job seeding is required; add
  `jobSlug` + `database_type: 1` cases (and the MY_DATABASE prompt) to cover the My-Database path.

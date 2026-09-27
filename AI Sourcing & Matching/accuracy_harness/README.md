# Accuracy harness — Unified Sourcing & Matching (PW-6818 / AI Sourcing V3)

Standalone Python harness that measures how accurate the AI Sourcing & Matching outputs are, from
the natural-language query all the way to the scored candidates. Every run writes an Excel
workbook for **manual validation**: the LLM judge's reasoning is always included, because we don't
trust the judge blindly.

| Suite | Script | What it measures | Runs against |
|---|---|---|---|
| Config stage | `run.py` | Clarifying questions, criteria and filters from `POST /v2/ai-matching/configuration` vs Sonnet running the **same real Neptune prompt**; Sonnet judges | non-prod (`dev`) |
| Criteria → filters | `run_criteria.py` | The "final query" generated from finalised criteria (`/configuration/criteria`) | non-prod |
| Answer influence | `run_answers.py` | Do the user's answers to the clarifying questions change the filters? | non-prod |
| End to end | `run_e2e.py` | query → questions → answers → criteria → filters → criteria edits → search → results, every hop asserted | non-prod |
| Global sourcing | `run_sourcing.py` | Candidate relevance + per-criterion scoring of `POST /v1/ai-sourcing-v3/search` | **prod** |
| Global sourcing at scale | `run_sourcing_scale.py` | 300 realistic query variations × 4 pages (~30k candidates) | **prod** |
| Metamorphic | `run_sourcing_metamorphic.py` | Same search, criteria C vs C + extra — scores must move the right way | **prod** |
| Prod accuracy eval | `run_sourcing_eval.py` | 5 real queries; the judge scores every returned candidate independently | **prod** |
| Run-to-run drift | `run_sourcing_compare.py` → `run_compare_judge.py` | Same inputs twice; what changed, and which run scored better | any env with sourcing results |
| Env A vs env B | `run_env_compare.py` | Response time, criteria, filters, scores and summaries on two envs (no judge) | two envs |
| Model bake-off | `run_model_bakeoff.py` | Can another LLM replace the candidate scorer? See [`model-bakeoff/README.md`](model-bakeoff/README.md) | **prod** |
| My Database (db=1) | `prep_db1_dataset.py` → `run_matching_scale.py` | Seeds labelled candidates, then checks scoring correctness + relevance of what comes back | non-prod (seeds data) |
| Prod read-only checks | `prod_smoke.py`, `prod_query_check.py`, `prod_revalidate.py` | Does a query return candidates right now; what filter was generated; is it stable | **prod** |

---

## 1. Prerequisites

- **Python 3.10+**
- **An Anthropic API key** with access to `claude-sonnet-5` (used for the reference model and the judge).
- **A Recruit CRM test account with the `unified_ai_sourcing_matching` flag enabled:**
  - one on the non-prod env you test (e.g. `dev`) for the config / criteria / e2e suites;
  - one on **prod** for the sourcing suites. The external sourcing corpus only returns candidates
    on prod — dev/ss envs return 0 results.
- **Network:** `dev` and prod are public. Internal hosts (e.g. the ss5 job service) need VPN.
- **My Database runs only:** the account's **public API key** (in the app: Settings → API). Seeding
  creates candidates and jobs in that account.

## 2. Install

```bash
git clone https://github.com/kowshik-recruitcrm/AI-Features-Accuracy-testing.git
cd "AI-Features-Accuracy-testing/AI Sourcing & Matching/accuracy_harness"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install reportlab          # optional — only for make_jd_pdfs.py / build_jd_library.py
```

## 3. Configure

Credentials live in two local shell files, both gitignored. Copy the templates and fill them in:

```bash
cp set_env.example.sh set_env.sh               # always
cp set_prod_urls.example.sh set_prod_urls.sh   # only for the prod suites
```

| Variable | File | Needed for | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `set_env.sh` | everything with a judge | your own key |
| `RCRM_LOGIN_EMAIL` / `RCRM_LOGIN_PASSWORD` | `set_env.sh` (non-prod), `set_prod_urls.sh` (prod) | every suite | the flag-enabled test account; `set_prod_urls.sh` overrides it with the prod login |
| `RCRM_ENV` | `set_env.sh` | non-prod | `dev`, `ss7`, `ai1`, … — URLs are derived from it |
| `RCRM_ALBATROSS_URL`, `RCRM_NEPTUNE_URL`, `RCRM_JOB_SERVICE_URL` | `set_prod_urls.sh` | prod | already filled in for prod |
| `RCRM_ALBATROSS_TOKEN` | either | optional | paste a bearer token to skip the login step |
| `RCRM_ACCOUNT_API_KEY` | `set_env.sh` | My Database only | public API key, **not** the password |
| `ACCURACY_MODEL` | either | optional | default `claude-sonnet-5` |

Load them in every new shell:

```bash
source set_env.sh                              # non-prod suites
source set_env.sh && source set_prod_urls.sh   # prod suites (order matters)
```

## 4. Check the setup

```bash
python3 check_env.py
```

It confirms the Anthropic key works, logs in, and makes one real config call. It prints no
secrets. Fix anything marked `!!` before a real run.

## 5. Run

Always start with a small `--limit` pilot. Most long runs are **resumable**: re-run the same
command with the same `--run-id` and finished cases are skipped.

### Config stage, criteria → filters, answer influence (non-prod)
```bash
source set_env.sh
python3 run.py --limit 3            # smoke test
python3 run.py                      # full golden set → output/accuracy_results_<ts>.xlsx
python3 run_criteria.py             # → output/criteria_results_<ts>.xlsx
python3 run_answers.py              # → output/answer_influence_results_<ts>.xlsx
```
About $0.05 per case (two Sonnet calls). The shared prompt prefix is cached.

### End to end (non-prod)
```bash
source set_env.sh
python3 run_e2e.py --run-id e2e1 --limit 4     # pilot, ~5 min
python3 run_e2e.py --run-id e2e1               # full matrix (resumes)
python3 run_e2e.py --run-id e2e1 --rebuild     # rebuild the workbook offline
python3 e2e_rescore.py --run-id e2e1           # re-apply the checks without calling the API
```
`E2E_JUDGE=0` runs the deterministic checks only (free, fast). Flags and every assertion are
documented in [`README_e2e.md`](README_e2e.md).

### Global sourcing (prod)
```bash
source set_env.sh && source set_prod_urls.sh
python3 run_sourcing.py --limit 2                       # smoke test
python3 run_sourcing_scale.py --limit 12 --yes          # pilot, prints the full-run ETA
python3 run_sourcing_scale.py --yes                     # full 300 cases (resumes past the pilot)
python3 run_sourcing_metamorphic.py
python3 run_sourcing_eval.py --run-id evalprod1 --yes   # rebuild offline with --rebuild
```
Scale cases are in `golden/sourcing/scale_cases.json` (regenerate with `python3 generate_scale_cases.py`).
Details: [`README_day2_sourcing.md`](README_day2_sourcing.md).

### Run-to-run drift and env A/B
```bash
python3 run_sourcing_compare.py --phase 1 --run-id cmp1     # run 1
#   … wait …
python3 run_sourcing_compare.py --phase 2 --run-id cmp1     # run 2 with identical inputs + diff workbook
python3 run_compare_judge.py --run-id cmp1                  # judge which run scored better

RCRM_ENV=ai1 python3 run_env_compare.py --capture --run-id envcmp1
RCRM_ENV=ai3 python3 run_env_compare.py --capture --run-id envcmp1
python3 run_env_compare.py --compare --run-id envcmp1 --env-a ai1 --env-b ai3
```

### My Database (db=1) — seeds data, clean up afterwards
```bash
source set_env.sh                                        # with RCRM_ACCOUNT_API_KEY set
python3 prep_db1_dataset.py --run-id smoke1 --limit-jobs 1   # seed ~20 labelled candidates
python3 run_matching_scale.py --run-id smoke1 --yes
python3 prep_db1_dataset.py --run-id smoke1 --cleanup        # delete exactly what was created
```
Every seeded record carries the marker `[PW6818-db1-<run-id>]`, and `--cleanup` only deletes
records listed in that run's ledger. Plan and expected score bands: [`DB1_DAY2_PLAN.md`](DB1_DAY2_PLAN.md).

### Model bake-off (prod)
Has its own env template and README — see [`model-bakeoff/README.md`](model-bakeoff/README.md).
`run_model_bakeoff.py` and `challenger_llms.py` in this folder are the same tool wired to this
harness's config.

## 6. Output

Everything goes to `output/`, which is created on first run and never committed. It holds real
candidate data from the account, so don't share it publicly.

| File | What it is |
|---|---|
| `*.xlsx` | the workbook to review — start with the Dashboard / Issues sheet |
| `*.jsonl` | the crash-safe checkpoint that lets a run resume and `--rebuild` |
| `*.json` | raw snapshots (compare / env-compare runs) |

## 7. Folder layout

```
accuracy_harness/
  config.py, rcrm_auth.py, rcrm_client.py, sourcing_client.py, aries_matching_client.py,
  rcrm_public_client.py, llm.py          shared plumbing (env config, login, API clients, LLM calls)
  run*.py, prep_db1_dataset.py           the suites (table at the top)
  *_checks.py, *_report.py               deterministic checks and workbook builders
  check_env.py, *_probe.py, *_diag.py    setup check and one-off diagnostics
  golden/                                test cases (queries, JDs, criteria, e2e matrix, safety)
  prompts/                               the real Neptune system prompts + the judge rubrics
  model-bakeoff/                         self-contained bake-off tool
  inhouse_eval/                          replays a scale run's judge audit in the in-house evaluator (optional)
```

Deeper reading: [`README_e2e.md`](README_e2e.md) · [`README_day2_sourcing.md`](README_day2_sourcing.md) ·
[`DB1_DAY2_PLAN.md`](DB1_DAY2_PLAN.md) · [`JUDGE_PROMPT_ANATOMY.md`](JUDGE_PROMPT_ANATOMY.md) ·
[`ALL_PROMPTS.md`](ALL_PROMPTS.md) · [`ALL_QUERIES.md`](ALL_QUERIES.md)

## 8. API contract notes (config stage)

- `aiSearch` is **job-less** and **forces `databaseType=2`**. The query text is used directly as the JD, so the default golden set needs no job seeding.
- `databaseType=1` (My Database) needs a **real `jobSlug`** the token can view; a fake slug returns 404. The harness picks one automatically (override with `RCRM_JOB_ANCHOR`), and `RCRM_FORCE_DB=1` runs the whole suite on My Database.
- Response `filters` = `{llmFilters, translatedFilters}`. The real extraction is in `filters.llmFilters`. The rubric compares values, not wrapper shape or key casing.
- Invalid, unsafe or gibberish input returns **HTTP 400** ("Please enter a valid Job Description or Search Query."). The judge treats a correct rejection as correct.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing RCRM_LOGIN_EMAIL / RCRM_LOGIN_PASSWORD` | fill them in `set_env.sh` / `set_prod_urls.sh` and `source` again |
| `Missing ANTHROPIC_API_KEY` | `source set_env.sh` in this shell |
| `Login failed (HTTP 4xx)` | wrong password, or the account doesn't exist on that env — check `RCRM_ENV` |
| Config call 403 / feature error | the account doesn't have `unified_ai_sourcing_matching` enabled |
| Sourcing returns 0 candidates | you're not on prod — `source set_prod_urls.sh`; also avoid named companies and narrow cities in queries |
| Host doesn't resolve | an internal env — connect to VPN, or set `RCRM_JOB_SERVICE_URL` explicitly |

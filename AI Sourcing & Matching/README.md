# AI Sourcing & Matching — accuracy suites

Accuracy testing for Unified Sourcing & Matching (PW-6818 / AI Sourcing V3) and AI Matching.
There are four independent projects here. Each has its own README, `requirements.txt` and credentials template, so set up only the one you need.

| Folder | What it tests | You need |
|--------|---------------|----------|
| [`accuracy_harness/`](accuracy_harness/README.md) | The main harness. Config stage (questions, criteria, filters), criteria → filters, answer influence, the full end-to-end chain, Global sourcing at scale, metamorphic and run-to-run checks, env A/B, model bake-off, My Database matching | Anthropic key · flag-enabled test account on a non-prod env **and** on prod |
| [`sourcing_eval_kit/`](sourcing_eval_kit/README.md) | A small kit for anyone: real queries against AI Sourcing (Global) on prod; an LLM judge scores every returned candidate against the API's own score | Anthropic key · prod test account |
| [`boolean_search/`](boolean_search/README.md) | Whether the boolean keyword query a recruiter types is actually **applied** to the results, not just parsed | test account on the env under test (default `ai4`) — no LLM key |
| [`llm_scoring_comparison/`](llm_scoring_comparison/README.md) | AI Matching scorer head-to-head: platform / Gemini / DeepSeek vs Claude Sonnet as reference | Anthropic key (+ Gemini key) · test account with jobs loaded |

## Common setup

Every project follows the same four steps. The details are in each project's README.

```bash
git clone https://github.com/kowshik-recruitcrm/AI-Features-Accuracy-testing.git
cd "AI-Features-Accuracy-testing/AI Sourcing & Matching/<project>"
python3 -m venv .venv && source .venv/bin/activate      # Python 3.10+
pip install -r requirements.txt
cp <template> <local file>                              # fill in your keys and login
```

| Project | Template → local file | Load it with |
|---|---|---|
| `accuracy_harness` | `set_env.example.sh` → `set_env.sh`, and for prod runs `set_prod_urls.example.sh` → `set_prod_urls.sh` | `source set_env.sh && source set_prod_urls.sh` |
| `accuracy_harness/model-bakeoff` | `env.example.sh` → `env.sh` | `source env.sh` |
| `sourcing_eval_kit` | `set_env.example.sh` → `set_env.sh`, `set_prod_urls.example.sh` → `set_prod_urls.sh` | `source set_env.sh && source set_prod_urls.sh` |
| `boolean_search` | `.env.example` (reference) | `export AIS_EMAIL=… AIS_PASSWORD=…` |
| `llm_scoring_comparison` | `.env.example` → `.env` | read automatically |

The local files are gitignored. Never commit them.

## Things that trip people up

- **Sourcing only returns candidates on prod.** The external corpus is empty on dev/ss envs, so any
  candidate or scoring run needs the prod URLs (`set_prod_urls.sh`).
- **The test account needs the feature flag** (`unified_ai_sourcing_matching`). Without it the config
  endpoints reject the call.
- **Queries that return nothing.** Global retrieval over-constrains easily: avoid named companies and
  very narrow cities, and use natural language rather than `AND`/`OR` strings.
- **Output holds real candidate data.** Every project writes to its own `output/` or report files,
  which are gitignored. Don't share them publicly.

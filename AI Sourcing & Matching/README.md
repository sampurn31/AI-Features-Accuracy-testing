# AI Sourcing & Matching — accuracy suites

Accuracy testing for Unified Sourcing & Matching (PW-6818 / AI Sourcing V3) and AI Matching.
Four independent projects — each has its own README, requirements and env template.

| Folder | What it tests |
|--------|---------------|
| [`accuracy_harness/`](accuracy_harness/README.md) | The main harness. Config stage (clarifying questions, criteria, filters), criteria → filters, answer influence, the full end-to-end chain, Global sourcing candidate + scoring accuracy at scale, metamorphic and run-to-run checks, env A/B, model bake-off, and My Database (db=1) matching. |
| [`sourcing_eval_kit/`](sourcing_eval_kit/README.md) | A small, shareable kit: real queries against AI Sourcing (Global) on prod; an LLM judge scores every returned candidate against the API's own score. |
| [`boolean_search/`](boolean_search/README.md) | Whether the boolean keyword query a recruiter types is actually **applied** to the results, not just parsed. Uses its own boolean evaluator as the oracle. |
| [`llm_scoring_comparison/`](llm_scoring_comparison/README.md) | AI Matching scorer head-to-head: platform / Gemini / DeepSeek scores vs Claude Sonnet as reference. |

## accuracy_harness — quick start
```bash
cd "AI Sourcing & Matching/accuracy_harness"
pip install -r requirements.txt
cp set_env.example.sh set_env.sh                 # your ANTHROPIC_API_KEY
cp set_prod_urls.example.sh set_prod_urls.sh     # only for prod runs: URLs + test-account login
source set_env.sh
python3 check_env.py                             # confirm everything resolves
```

| Goal | Script | Guide |
|------|--------|-------|
| Config stage: questions / criteria / filters vs Sonnet running the real Neptune prompt, Sonnet judges | `python3 run.py` | [README.md](accuracy_harness/README.md) |
| Criteria → filters (final query generation) | `python3 run_criteria.py` | |
| Do the user's answers change the filters? | `python3 run_answers.py` | |
| End to end: query → questions → answers → criteria → filters → search → results | `python3 run_e2e.py` (`e2e_rescore.py` re-checks a finished run offline) | [README_e2e.md](accuracy_harness/README_e2e.md) |
| Global sourcing: candidate + scoring accuracy | `python3 run_sourcing.py` | [README_day2_sourcing.md](accuracy_harness/README_day2_sourcing.md) |
| Global sourcing at scale (300 cases × 4 pages) | `python3 run_sourcing_scale.py --limit 12 --yes`, then without `--limit` | |
| Metamorphic: same search, criteria C vs C + extra | `python3 run_sourcing_metamorphic.py` | |
| Run-to-run drift + judge which run scored better | `python3 run_sourcing_compare.py` → `python3 run_compare_judge.py` | |
| Env A vs env B (e.g. a perf branch vs baseline) | `python3 run_env_compare.py` | |
| Can another LLM replace the candidate scorer? | `python3 run_model_bakeoff.py` | [model-bakeoff/README.md](accuracy_harness/model-bakeoff/README.md) |
| My Database (db=1) matching accuracy | `python3 prep_db1_dataset.py` → `python3 run_matching_scale.py` | [DB1_DAY2_PLAN.md](accuracy_harness/DB1_DAY2_PLAN.md) |
| Read-only prod checks | `prod_smoke.py`, `prod_query_check.py`, `prod_revalidate.py` | |

Golden cases are in `accuracy_harness/golden/`, judge rubrics and Neptune prompts in
`accuracy_harness/prompts/`. Every run writes to `output/`, which is not committed.

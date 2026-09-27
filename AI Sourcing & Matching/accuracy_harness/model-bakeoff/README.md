# Model Bake-off — can another LLM replace the env candidate scorer?

The env scores search candidates with **one** model today (Gemini 2.5 Flash Lite, inside
`POST /v1/ai-sourcing-v3/search`). This tool takes that env scoring as the **baseline** and, for
the **same fixed query + same retrieved candidates + same weighted criteria**, re-scores every
candidate with **4–6 challenger models in parallel** — each its own provider, key and model id —
then builds one workbook ranking every model against the env and asks Anthropic which one (if any)
is a viable drop-in replacement.

Compared per model: scoring agreement (overall + per-criterion), label agreement, candidate-ranking
correlation, response time (p50/p95/mean), throughput (candidates/sec), token cost, failure rate.

A model with a missing key, a bad response, or a mid-run error is recorded as a miss — **the run
never stops for the others.**

---

## 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure keys + env auth

```bash
cp env.example.sh env.sh      # then edit env.sh
source env.sh
```

`env.sh` holds the env login/token (the baseline) **and** one API key per challenger model.
Leave a key blank to skip that model. Point `RCRM_NEPTUNE_URL` at **prod** — the external corpus
returns candidates only on prod.

## 3. Pick the models

Open **`challenger_llms.py`** and edit the clearly-marked `SLOTS` block at the top — one entry per
model (`provider`, `model` id, `key_env`). Providers supported: `google`, `anthropic`, `openai`,
and `openai_compat` (DeepSeek / Grok / Groq / Mistral / Together / OpenRouter — anything speaking
`/chat/completions`; set its `base_url`).

Check what's wired up and which keys resolved:

```bash
python3 run_model_bakeoff.py --list-models
```

## 4. Run

```bash
python3 run_model_bakeoff.py --query "software engineer in india" --run-id bake1 --limit 40 --yes
```

- `--query`   the fixed query scored by the env **and** every model (required)
- `--limit`   cap how many candidates to score (0 = all the env returns)
- `--criteria '[{"text":"...","weight":8}]'`  pin criteria instead of generating them
- `--pages N`  how many pages of env candidates to pull (default 1)
- `--no-judge` skip the Anthropic recommendation (numeric metrics only)
- `--rebuild`  rebuild the workbook from a saved snapshot, no API calls

Output lands in `output/`:
- `bakeoff_<runid>.xlsx` — Dashboard · Models&Keys · Candidate Scores · Failures · Samples
- `bakeoff_<runid>.json` — raw snapshot (feeds `--rebuild`)

---

## Reading the leaderboard

Each model is a row; the env is the grey baseline row. Best-replacement candidates sort to the top.

| column | meaning | good |
|---|---|---|
| cov | share of candidates the model scored without failing | high |
| fail | failure rate | low |
| overall MAE | mean \|model − env\| overall score | **low** |
| bias | mean signed (model − env); +ve = more generous than env | near 0 |
| ±tol | share within ±tolerance of env | high |
| score r / rank r | Pearson on scores / **Spearman on env ranking** | high |
| label agr | share with the same match label | high |
| crit MAE / crit agr | per-criterion closeness / agreement | low / high |
| p50 / p95 ms | per-call latency | low |
| thru c/s | candidates/sec at the run's concurrency | high |
| out tok | avg output tokens per candidate (cost proxy) | low |

**rank r (Spearman)** matters most for a search product — a replacement can differ on absolute
numbers yet still be safe if it keeps candidates in the same order.

---

## Files

| file | role |
|---|---|
| `run_model_bakeoff.py` | the runner + report builder |
| `challenger_llms.py` | **edit the `SLOTS` block here** — model registry + provider adapters |
| `prompts/model_scoring_prompt.txt` | the identical scoring instruction every model gets |
| `config.py` · `sourcing_client.py` · `rcrm_auth.py` · `rcrm_client.py` | env baseline capture |
| `llm.py` · `scale_report.py` | the Anthropic judge + report styling helpers |
| `env.example.sh` · `slots.example.json` | templates |

## Notes

- The env returns candidates only on **prod** (dev/staging return 0) — run against prod URLs.
- The `gemini-lite-direct` slot calls the env's *own* model directly, as a control: it separates
  "is the env slow because of the model, or because of retrieval?"
- No temperature is sent by default (reasoning models like Sonnet 5 / gpt-5 reject it); set
  `temperature` in a slot only if that provider accepts it.

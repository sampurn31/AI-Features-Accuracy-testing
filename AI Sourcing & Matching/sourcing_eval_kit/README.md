# AI Sourcing — Accuracy Eval Kit

Runs a set of real search queries against **AI Sourcing (Global)** on prod, then uses an LLM
(**Sonnet 5** or **Opus 5**) to independently judge every returned candidate. Produces one Excel
workbook answering, per candidate:

- **Our API's score** (overall `matchScore` + per-criterion) vs the **LLM's independent score**
- **Is our API's score good enough?** (yes / borderline / no + why)
- **Is the candidate actually relevant to the query?** (yes / partial / no + why)

Plus a Dashboard with per-query and overall analytics (% good-enough, % relevant, avg API vs LLM).

---

## 1. Setup (one-time, ~2 min)

Needs Python 3.10+.

```bash
cd "AI Sourcing & Matching/sourcing_eval_kit"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Create your two env files** from the templates:

```bash
cp set_env.example.sh set_env.sh             # paste your own Anthropic key
cp set_prod_urls.example.sh set_prod_urls.sh   # prod URLs are filled in; add the test-account login
```

> ⚠️ `set_env.sh` and `set_prod_urls.sh` hold secrets — keep this folder internal, don't commit
> them to a public repo or paste them in a public channel. (`.gitignore` already excludes them.)

---

## 2. Run it

Every run sources both env files first, then the script. Start with a **pilot** (1 query, 15
candidates, ~2 min, ~$0.30) to confirm everything works:

```bash
source set_env.sh && source set_prod_urls.sh
python run_sourcing_eval.py --run-id mypilot --limit-queries 1 --limit-cands 15 --yes
```

Then the **full run** (5 queries × ~100 candidates, ~20–30 min):

```bash
python run_sourcing_eval.py --run-id myrun --yes
```

Output → `output/sourcing_eval_<run-id>.xlsx` (workbook) + `output/sourcing_eval_<run-id>.jsonl`
(raw checkpoint — resumable; if a run dies, re-run the same `--run-id` and it skips finished queries).

---

## 3. Choosing the judge model

Default is **Sonnet 5** (cheaper). To use **Opus 5**, set `ACCURACY_MODEL` before running:

```bash
source set_env.sh && source set_prod_urls.sh
ACCURACY_MODEL=claude-opus-5 python run_sourcing_eval.py --run-id myrun_opus --yes
```

The console header prints `judge-model=...` so you can confirm which one is live.

**Rough cost** for a 5-query / ~500-candidate run: **~$7 on Sonnet 5, ~$15 on Opus 5**
(Opus ≈ 2.5× Sonnet). Prod search itself uses the shared account's credits, not your Anthropic key.

---

## 4. Re-judge without re-searching

Candidate profiles are saved in the checkpoint, so you can re-judge an existing run with a
different model **without hitting prod again**:

```bash
ACCURACY_MODEL=claude-opus-5 python run_sourcing_eval.py --run-id myrun --rejudge
```

(Only works on runs created by this version of the kit, which stores profiles.)

Rebuild the workbook from a checkpoint offline (no prod, no judging):

```bash
python run_sourcing_eval.py --run-id myrun --rebuild
```

---

## 5. The workbook

| sheet | what's in it |
|---|---|
| **Dashboard** | overall + per-query: % API good-enough, % relevant, avg API score vs avg LLM score |
| **Queries** | the queries used + the criteria that were generated for each |
| **Candidate Scoring** | per candidate: API score vs LLM score (Δ), good-enough verdict, relevant? |
| **Criterion Scoring** | per candidate × criterion: API score+reasoning vs LLM score+reasoning + verdict |
| **Relevance** | per candidate: relevant? + reason |

Queries that return **0 candidates are automatically excluded** from the workbook.

See `example_report.xlsx` for a sample.

---

## 6. Editing the queries

The queries live in the `QUERIES` list at the top of `run_sourcing_eval.py` — each is
`("<short-id>", "<the search query text>")`. Edit/add/remove freely.

**Tips for queries that actually return results** (Global retrieval over-constrains easily):
- Natural language beats boolean `AND/OR` strings.
- Avoid **named companies** and **ultra-narrow cities** — they generate hard filters that match nobody.
- Mainstream roles + a broad region (a metro or country) work best.
- `aiSearch` is capped at **1000 characters**.

---

## 7. How it works (per query)

1. **Criteria** — asks the config endpoint to generate weighted criteria from the query (the real
   product flow). If that endpoint is unavailable, it **falls back to LLM-derived criteria**; the
   `Queries` sheet shows which source was used (`config` vs `llm_fallback`).
2. **Search** — calls `POST /v1/ai-sourcing-v3/search` for pages 1–4 (~100 candidates), capturing
   each candidate's profile + our API's `matchScore` + per-criterion breakdown.
3. **Judge** — one LLM call per candidate returns its own scores, a verdict on our API's score, and
   a relevance verdict, all grounded only in the candidate's profile.

Files: `run_sourcing_eval.py` (runner + report) · `sourcing_client.py` (search) ·
`rcrm_client.py` (criteria/config) · `rcrm_auth.py` (login) · `llm.py` (the judge) ·
`scale_report.py` (Excel helpers) · `config.py` (env/URLs) · `prompts/sourcing_eval_rubric.md`
(the judge's instructions).

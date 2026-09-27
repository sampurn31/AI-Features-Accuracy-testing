# Day-2 accuracy — AI Sourcing V3 / Global database (db=2)

## ⚡ SCALE RUN (the main Day-2 execution)

**300 realistic variations × pages 1-4 (~100 candidates each) ≈ 1,200 requests / ~30k candidates.**
Golden set: `golden/sourcing/scale_cases.json` (regenerate with `python3 generate_scale_cases.py`) —
280 full-criteria / 10 single-criterion / 10 no-criteria; ~97 short queries, ~100 briefs,
~98 multi-page JDs (3.7-4.1k chars) + 5 extra-long (~10k); 65 role archetypes across tech,
healthcare, finance, sales, operations, legal, field, hospitality, product, education.

```bash
source set_env.sh && source set_prod_urls.sh
python3 run_sourcing_scale.py --limit 12 --yes    # pilot (~5-10 min) -> prints full-run ETA
python3 run_sourcing_scale.py --yes               # full 300 — RESUMES automatically past the pilot
```

- Workers hard-capped at **10** (default 8, `SCALE_WORKERS`); page-1 timeout 300s; 1 retry/page.
- **Crash-safe**: every finished variation is checkpointed to `output/scale_checkpoint_<runid>.jsonl`;
  re-running the same command skips completed ones. `--rebuild` re-makes the xlsx offline.
- Output: **one workbook** `output/sourcing_scale_<runid>.xlsx` — Dashboard (KPI cards + histogram),
  Variations, Failures, Judge Samples, Candidates (~30k rows), Cases.
- Validation layers: deterministic on EVERY candidate (score=weighted-avg formula, ranking order,
  cross-page dedup, coverage, shape, label, metadata + cohort contracts) · retrieval heuristics
  (title/skill/location vs parsed queryUsed) · Sonnet-5 judge on ranks 1/mid/last per scored
  variation (~850 calls ≈ $15).
- Mock-validated end-to-end (patched client + seeded defects → all caught in Failures sheet).

---

Automated accuracy testing for the **candidate side** of Global AI Sourcing: the scored,
ranked candidates returned by `POST /v1/ai-sourcing-v3/search` when `matchingCriteria`
is supplied (`matchScore`, `matchLabel`, and per-criterion `matchAnalysis`).

This is the Global slice of PW-6818 Day-2 (see the Confluence plan). It is **self-contained**
— no candidate seeding — because the Global pool comes from the external corpus
(Coresignal/OpenSearch), which cannot be seeded. Accuracy is therefore established by
three layers that don't need ground-truth labels:

| Layer | File | What it proves | Noise |
|---|---|---|---|
| **Deterministic anchor** | `sourcing_checks.py` | Objective invariants that must ALWAYS hold | none — a hit is a real bug |
| **LLM judge** (Sonnet 5) | `llm.judge_sourcing` + `prompts/sourcing_judge_rubric.md` | Reasoning grounded in the profile; `satisfied` flags correct; score sensible | noisy — read the reasoning, don't trust counts blindly |
| **Metamorphic** | `run_sourcing_metamorphic.py` | Same pool, +1 criterion → score moves the right way | none — direction only |

### Deterministic checks (the anchor)
1. **Ranking monotonic** — results are globally reordered by score, so `matchScore` must be non-increasing down the list.
2. **Overall score = weighted average** — `matchScore` must equal `Σ(score·weight)/Σweight` over the per-criterion scores, within `SOURCING_SCORE_TOL` (default ±2). Confirmed exact on prod (e.g. 90·9+100·8+100·7+95·6 /30 = 96).
3. **Per-criterion score range** — each per-criterion `score` is within 0–100.
4. **Criteria coverage** — every supplied criterion appears once in `matchAnalysis`, weights preserved, none dropped/invented (tolerant of light rewording).
5. **Analysis shape** — when scored, each row has `criterionText` / numeric `weight` / numeric `score` / non-empty `reasoning`.
6. **Label not contradictory** — `matchLabel` must not grossly contradict `matchScore`.

## Why it must run on PROD
The external sourcing corpus only returns candidates on **prod**. On dev/ss the endpoint is
up and `scoringApplied=true`, but retrieval returns **0 candidates** ("No candidates found"),
so there is nothing to score. Run this suite on prod, against the cleared internal account.

⚠️ Each search call **spends ~1 credit** (`update_credits_remaining`, per page). It makes **no
writes** — `/search` does not persist a history row or create candidate records. Keep smoke
runs small.

## Setup — a developer only needs their Anthropic key
Copy the two templates and fill them in — your own Anthropic key in `set_env.sh`, the prod URLs
and the test-account login in `set_prod_urls.sh`. Both real files are gitignored.

```bash
cp set_env.example.sh set_env.sh
cp set_prod_urls.example.sh set_prod_urls.sh
```

Then every run sources both files:

```bash
source set_env.sh && source set_prod_urls.sh && python run_sourcing.py --limit 2
```

`set_prod_urls.sh` holds the prod URLs (`albatross.recruitcrm.io`, `vms-neptune.recruitcrm.io`,
`prodjob.recruitcrm.io`) and the test-account login (email + password). Login is done at
runtime; no cookie/bearer needed.

> ⚠️ Never commit `set_prod_urls.sh` or `set_env.sh` — they hold credentials.

## Run
```bash
source set_env.sh
python run_sourcing.py --limit 2          # tiny smoke (≈2 credits)
python run_sourcing.py                     # full golden set  -> output/sourcing_results_<ts>.xlsx
python run_sourcing_metamorphic.py         # direction test   -> output/sourcing_metamorphic_<ts>.xlsx
```

Tunables: `SOURCING_JUDGE_TOP_K` (candidates judged per query, default 5), `ACCURACY_WORKERS`.

## Output (colour-coded xlsx, 2 sheets)
`summary` — one row per query: `current_http`, `n_candidates`, `scoring_applied`, `det_pass`
(green/red — the objective verdict), `grounding` / `satisfied_correct` / `score_sensible`
(judge, green/amber/red), `judge_flag`, `det_issues`, combined `issues`, and the worst
candidate's `judge_reasoning`. `raw_io` — the metadata + full scored-candidate JSON so every
verdict is hand-verifiable.

**Read it like this:** `det_pass = no` is a real defect — start there and read `det_issues`.
The judge columns are advisory; open `judge_reasoning` and the `raw_io` profile before filing
a judge-only finding (the corpus is non-deterministic run-to-run, so judge tallies drift).

## Known limitations / findings
- **Corpus non-determinism** — the same query returns different candidates over time; anchor conclusions on the deterministic + metamorphic layers, not judge counts.
- **Response schema differs from the LLD** — LLD 4015292441 documents per-criterion `satisfied` (bool); the **real prod response returns per-criterion `score` (0–100)** and no `satisfied`. The harness follows the real response — worth reconciling the contract with eng.
- **Score-calc — answered for Global** — `matchScore` = the weighted average of the per-criterion scores (confirmed exact on prod). Check #2 asserts this within ±2. (This differs from the earlier My-Database sample that didn't fit a simple formula — that was a different endpoint.)
- **No `overallReason`** in the Global search response — only per-criterion reasoning (overallReason is a My-Database concern).

## Files
`config.py` (sourcing section) · `sourcing_client.py` · `sourcing_checks.py` ·
`llm.py` (`judge_sourcing`) · `run_sourcing.py` · `run_sourcing_metamorphic.py` ·
`prompts/sourcing_judge_rubric.md` · `golden/sourcing/day2_queries.json` ·
`golden/sourcing/metamorphic_cases.json`

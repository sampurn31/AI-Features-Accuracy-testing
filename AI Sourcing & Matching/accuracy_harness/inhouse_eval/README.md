# Sourcing accuracy testing inside the inhouse-evaluator

Replays the `20260723-full` scale run's judge audit through the inhouse-evaluator's
`llm_judge` mode, so the accuracy check runs in the evaluator UI instead of the harness.

## The difference from the harness run

The harness judged an output that already existed — prod's `matchAnalysis` rows. The
evaluator always calls the models first, then judges what came back. So the models under
test play the scoring engine themselves, using `prompts/model_scoring_prompt.txt` (the same
prompt `run_model_bakeoff.py` uses). Everything else — the profile the judge sees, the
per-criterion standards, the ±15 agreement band — matches the harness.

Side effect: this compares models head-to-head, which the 300-variation run never did.

## Files

| | |
|---|---|
| `build_sourcing_dataset.py` | pulls candidates out of `output/scale_checkpoint_20260723-full.jsonl` (604 judged candidates with full profiles) into an evaluator JSONL, stratified across `short` / `brief` / `jd_excerpt` |
| `judge_system_prompt.txt` | `prompts/sourcing_criterion_rubric.md` with its output-format section removed — leave that section in and it fights the evaluator's own JSON contract, and `parse_eval_json` returns `None` |
| `judge_criteria.txt` | maps `reasoning_verdict` / `judge_score` / `score_verdict` onto the evaluator's fixed 1-10 schema; `too_high` weighted harder than `too_low` |
| `setup_run.py` | creates the Feature, uploads the dataset, starts the run |

## Running it

```
python3 build_sourcing_dataset.py --rows 30
python3 setup_run.py --models <m1>,<m2> --dry-run      # check first
python3 setup_run.py --models <m1>,<m2>
```

Needs the evaluator backend up (`uvicorn main:app --port 8000` from `backend/`).
Re-running reuses the Feature by name, so prompt versions accumulate rather than duplicating.

## What does not carry over

- **Per-criterion columns.** The evaluator's judge schema is fixed at
  `{score 1-10, explanation, strengths[], weaknesses[]}`. Verdicts survive as formatted
  strings inside `strengths`/`weaknesses`, so "reasoning correct 46%" and "score agreement
  60%" cannot be recomputed from `evaluation_scores` without parsing those strings.
- **Forced JSON schema.** The evaluator asks for JSON in the prompt and parses defensively;
  it does not enforce a schema at the API level.
- **The deterministic checks** (weighted-average formula, ranking order, cross-page
  duplicates, criteria coverage) have no equivalent in the evaluator — they stay here.

`expected_output` on each row carries prod's own scoring output, so the run also records
token-overlap against prod, and the same file can be re-run in `golden_dataset` mode.

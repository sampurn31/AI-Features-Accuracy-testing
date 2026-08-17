# Accuracy Testing

Three independent test suites for validating Recruit CRM's AI parsing, LLM, and AI Matching features.

---

## Folders

### `CV parsing agent/`
Tests the **resume parser** — uploads real resumes, checks how accurately standard and custom fields are extracted, and compares two parser environments against each other.

### `LLM Replacement/`
Tests **Neptune LLM features** — sends the same prompt to two environments (baseline vs new model), judges the output quality using a Groq LLM, and writes a scored Excel report.

### `Activity Context/`
Tests whether giving the **AI Matching scorer** a candidate's notes and call logs improves its reasoning and scores. Two deployments over one shared database — one that can see activity, one that cannot.

---

## CV parsing agent — how to run

**What it does:** Parses 100 resumes, compares extracted fields, validates custom field injection, and tests multilingual + nested field handling.

**Step 1 — Setup**
```bash
cd "CV parsing agent"
pip install -r requirements.txt
cp .env.example .env        # fill in credentials
```

**Step 2 — Pick what to run**

| Goal | Script |
|------|--------|
| Compare Sovren vs InHouse parser | `python3 Comparator.py` |
| Bulk parse 100 resumes + log fields | `python3 bulk_resume_parser_logger.py` |
| Test custom field extraction (main pipeline) | `python3 create_custom_fields.py` then `python3 custom_field_pipeline.py` |
| Test nested/dependent custom fields | `python3 nested_custom_field_pipeline.py --setup` |
| Test deep 5-level nested fields | `python3 deep_nested_field_pipeline.py --setup` |
| Test German / Spanish / Dutch / Hebrew / Japanese resumes | `python3 multilingual_pipeline_german.py` (etc.) |
| Test EEO guardrail API | `python3 eeo_guardrail_test_suite.py` |
| Check standard field parity between two envs | `python3 standard_field_parity.py` |

**Step 3 — Find your output**

All results go to `pipeline_output/` as `.xlsx` files.

---

## LLM Replacement — how to run

**What it does:** Fires the same prompts at two Neptune environments, scores the responses 1–5 using a judge LLM, and writes a colour-coded Excel report.

**Step 1 — Setup**
```bash
cd "LLM Replacement"
pip install -r requirements.txt
cp .env.example .env        # fill in Env A / Env B credentials + GROQ_API_KEY
```

**Step 2 — Run**

```bash
python3 run_all_tests.py              # run all 8 suites + write Excel
python3 run_all_tests.py --no-excel  # run only, skip report
python3 run_all_tests.py --excel-only  # write Excel from last saved log
```

**Step 3 — Find your output**

| File | What it contains |
|------|-----------------|
| `reports/report_<timestamp>.xlsx` | Scored results — one sheet per suite + Summary |
| `logs/run_<timestamp>.jsonl` | Raw log, one JSON line per test |

---

## Activity Context — how to run

**What it does:** Seeds notes and call logs against the account's real candidates, re-scores every job on both deployments with pinned criteria, and has a judge grade each match analysis at criterion level.

Two projects live here. **`activity_eval/` is the current one** — it uses the account's real candidates and grades at criterion level. `activity_context/` is the earlier synthetic-twin-pair version, kept for its design notes and its record of what the public API blocks.

**Step 1 — Setup**
```bash
cd "Activity Context"
pip install -r requirements.txt
cd activity_eval
cp .env.example .env        # fill in AI2 / DEV credentials + ANTHROPIC_API_KEY
```

**Step 2 — Run, in order**

| Goal | Script |
|------|--------|
| Offline sanity check — always run first, costs nothing | `python3 selfcheck.py` |
| Fetch real jobs + candidates | `python3 corpus.py --build` |
| Ask the system which candidates match which job | `python3 pools.py` |
| Plan the activity, grounded in each profile | `python3 design.py --plan` then `--show 4` |
| Write the notes + call logs (additive, reversible) | `python3 seed_plan.py` (`--dry-run` first) |
| Score both flows | `python3 evaluate.py` |
| Write the Excel | `python3 report.py` |

`python3 seed_plan.py --undo` removes every note and call log this wrote.

**Step 3 — Find your output**

The Excel written by `report.py` — Sheet 1 scores each analysis, Sheet 2 compares AI2 vs DEV blind, Sheet 3 covers response time.

See [Activity Context/activity_eval/README.md](Activity%20Context/activity_eval/README.md) for what the numbers mean and the five API behaviours that are easy to get wrong.

---

## Which one should I use?

| I want to test... | Use |
|-------------------|-----|
| Resume field extraction accuracy | `CV parsing agent` |
| Custom / nested / multilingual field parsing | `CV parsing agent` |
| EEO compliance guardrail | `CV parsing agent` |
| Candidate summary, email generation, job description | `LLM Replacement` |
| Any Neptune AI feature (before/after model swap) | `LLM Replacement` |
| Whether notes / call logs improve AI Matching scores | `Activity Context` |

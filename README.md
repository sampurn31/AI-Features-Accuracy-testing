# Accuracy Testing

Two independent test suites for validating Recruit CRM's AI parsing and LLM features.

---

## Folders

### `CV parsing agent/`
Tests the **resume parser** — uploads real resumes, checks how accurately standard and custom fields are extracted, and compares two parser environments against each other.

### `LLM Replacement/`
Tests **Neptune LLM features** — sends the same prompt to two environments (baseline vs new model), judges the output quality using a Groq LLM, and writes a scored Excel report.

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

## Which one should I use?

| I want to test... | Use |
|-------------------|-----|
| Resume field extraction accuracy | `CV parsing agent` |
| Custom / nested / multilingual field parsing | `CV parsing agent` |
| EEO compliance guardrail | `CV parsing agent` |
| Candidate summary, email generation, job description | `LLM Replacement` |
| Any Neptune AI feature (before/after model swap) | `LLM Replacement` |

# Neptune LLM Replacement — Test Suite

Automated A/B comparison framework that sends identical inputs to two environments (Env A baseline, Env B new) and uses a Groq LLM judge to score response quality.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in all values in .env
```

All credentials and environment URLs live in `.env` — never edit them in Python files.

---

## Architecture

| File | Role |
|------|------|
| `constants.py` | All config and thresholds (loaded from env) |
| `auth.py` | Token fetch + caching per environment |
| `api_client.py` | HTTP call wrappers for Env A and Env B |
| `base_runner.py` | Orchestrates single / conversational / multi-action test flows |
| `judge.py` | Groq LLM judge — scores new response vs baseline (1–5) |
| `schema_validator.py` | Action-specific format validation |
| `logger.py` | JSONL log writer (one entry per test) |
| `excel_reporter.py` | Builds styled Excel workbook from log entries |
| `run_all_tests.py` | Master runner — executes all suites and writes report |

---

## Run

```bash
# Run all suites + write Excel report
python3 run_all_tests.py

# Run only (skip Excel)
python3 run_all_tests.py --no-excel

# Write Excel from last log (skip tests)
python3 run_all_tests.py --excel-only
```

---

## Test suites (`tests/`)

| Suite file | Endpoint |
|-----------|---------|
| `test_candidate_summary.py` | `/v1/candidates/generate-summary` |
| `test_job_description.py` | `/v1/generate-job-description` |
| `test_note_summary.py` | `/v1/generate-note-summary` |
| `test_call_log_summary.py` | `/v1/generate-call-log-summary` |
| `test_email_template.py` | `/v1/generate-email-template` |
| `test_generate_email.py` | `/v1/generate-email` |
| `test_generate_email_sequence.py` | `/v1/generate-email-sequence` |
| `test_get_suggestions.py` | `/v1/get-suggestions` |

Each suite exposes `run_all() -> list[dict]`. Results feed into the Excel reporter.

---

## Scoring

| Score | Status | Meaning |
|-------|--------|---------|
| ≥ 4.0 | PASS | New response matches or exceeds baseline |
| 3.5 – 3.9 | REVIEW | Minor degradation — manual check required |
| < 3.5 | FAIL | Significant regression |

---

## Output

```
logs/run_<timestamp>.jsonl      # one JSON line per test
logs/judge_llm_payloads.jsonl   # exact judge prompts + responses
reports/report_<timestamp>.xlsx # styled Excel report (Summary + per-suite sheets)
```

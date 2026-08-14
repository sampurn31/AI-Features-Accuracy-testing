# AI Parser Parity

Standard-field parity check between the **Albatross baseline parser** and the
**standalone AI resume-parser service** (`ai6-resume-parser`, `/v3/parse`).

Same shape as [`../standard_field_parity.py`](../standard_field_parity.py) — original
resumes, no injection, no custom fields, no LLM snippet generation — but Track B is
the AI parser HTTP service instead of a second Albatross environment.

---

## Run

```bash
cd "CV parsing agent/ai_parser_parity"

python3 ai_parser_parity.py                # all resumes in ../100 resumes/
python3 ai_parser_parity.py --limit 5      # first 5 only (smoke test)
python3 ai_parser_parity.py --reparse      # ignore cache, hit both APIs again
python3 ai_parser_parity.py --model-id 8   # AI parser model_id (default 8)
python3 ai_parser_parity.py --resumes ../multilingual/German
python3 ai_parser_parity.py --sleep 0.5    # gap between resumes (default 1.0s)
```

Results are cached in `output/ai_parity_results.json`. A plain re-run reuses that
cache and only rebuilds the report — pass `--reparse` to actually re-parse.

Credentials come from `../.env` via `../config.py` — `TRACK_A_*` for the baseline
environment and `AI_PARSER_TOKEN` for the AI parser. The token is the **full header
value including the scheme**:

```bash
AI_PARSER_TOKEN=Private <your-token>
```

This repo is public, so the token is never committed; the script fails fast with a
clear message if it is unset.

---

## What it does

1. `POST /v1/login` on Track A → `data.token`, plus **`user.id`** and
   **`user.accountid`** (the `user` object is at the *top level* of the login
   payload, not under `data`). These become the AI parser's `user_id` /
   `account_id` form fields.
2. Track A: presigned S3 upload → `POST /v1/candidates/parse-resume`
   (`onlyParserData: true`).
3. Track B: the **same file** posted as `multipart/form-data` to
   `POST /v3/parse?model_id=8` with `Authorization: Private <token>` and
   `create_index` / `bool_index_resume` / `bool_resume_skills_fine_tune` /
   `is_custom_fields` all `"false"`.
4. The v3 payload is unwrapped (`data[0]["0"]`) and mapped onto the Albatross
   standard-field schema so every field compares 1:1.
5. Field-by-field comparison → Excel + console summary.

Both tracks parse the identical original file. Raw AI responses are kept in
`output/raw_ai_responses/<resume>.json`.

---

## Field mapping (AI v3 → Albatross schema)

| Compared field | Track A (Albatross) | Track B (AI parser v3) |
|---|---|---|
| `firstname` / `lastname` | `firstname` / `lastname` | same, falling back to splitting `full_name` |
| `emailid` | `emailid` | `emailid` → `email` |
| `contactnumber` | `contactnumber` | `contactnumber` → `phone` |
| `city` / `state` / `country` | same | same |
| `address` | `address` | `fullAddress` → `location` |
| `position` | `position` | `title` |
| `lastorganisation` | `lastorganisation` | `currentOrganization` |
| `workexpyr` | `workexpyr` | `total_experience` |
| `summary` | `summary` | `summary` → `candidateSummary` |
| `skill` | `skill` (comma string) | `skills[]` — **set-compared** |
| `languageskills` | `languageskills` (comma string) | `languages[]` — **set-compared** |
| `qualification` | `qualification` | `educationHistory[0].educational_qualification` → `degree[0]` |
| `specialization` | `specialization` | `educationHistory[0].educational_specialization` |
| `profilelinkedin` | `profilelinkedin` | `linkedin_url` → `socialMedia.linkedin` |
| `profilegithub` | `profilegithub` | `github_url` → `socialMedia.github` |
| `workhistory` | `workhistory[]` | `workHistory[]` |
| `educationhistory` | `educationhistory[]` | `educationHistory[]` |

`sovren_document_id` is **excluded** — the two services mint unrelated document
ids (`1_5046` vs `8_6017`), so comparing them is pure noise. The AI-side id is
still recorded in `ai_parity_results.json` for traceability.

Both services already use identical per-entry keys inside work/education history
(`work_company_name`, `work_start_date`, `institute_name`, …), so those arrays
flatten and compare with no translation.

---

## Classification

Same classifiers as the existing parity report, so the two are directly comparable:

| Field kind | Rule | Verdicts |
|---|---|---|
| Scalars | `classify_standard` — punctuation-normalised string compare, containment counts as partial | Match / Partial Match / Regression / Missing in B / Other |
| `skill`, `languageskills` | `classify_set_field` — set overlap | Match / Partial Match / Regression / Missing in B / Other |
| `workhistory`, `educationhistory` | `classify_history_comparator_style` — Comparator-style flattening + SequenceMatcher (1.0 exact, ≥0.85 partial) | Match / Partial Match / Regression / Missing in B / Other |

**Why skills and languages are set-compared:** Track A returns a comma-separated
string, the AI parser returns a JSON list, and neither preserves order. A straight
string compare would mark literally every row a Regression. The details column
carries `A=n, B=n, common=n, coverage=%, jaccard=` plus sample diffs so you can
still see exactly what each side got.

"Missing in B" = Track A found it, the AI parser did not. "Other" = the reverse
(A empty, B populated) — usually the AI parser extracting something extra.

---

## Response time metrics

Per resume (`Response Time Analysis` sheet):

| Column | Meaning |
|---|---|
| `Track A Total (ms)` | presign + S3 upload + parse call |
| `Track A Parse (ms)` | the `parse-resume` call alone |
| `AI Request (ms)` | the single multipart POST (upload + parse together) |
| `AI LLM Time (ms)` | `llm_response_time` from the response, converted s → ms |
| `AI Non-LLM Overhead (ms)` | `AI Request − AI LLM Time` |
| `Difference (ms)` | `AI Request − Track A Total` (negative = AI faster) |
| `Faster` | A / B / Equal |

`AI Request` vs `Track A Total` is the apples-to-apples comparison — both include
getting the file to the server.

Statistics block: avg / max / min / median for both tracks, average server-side
parse time, and a dedicated **LLM metrics** section — avg, max, min, median, total
LLM time, average non-LLM overhead, and LLM share of total AI request time.

> **`llm_response_time` is intermittent.** The service returns it on some responses
> and omits it on others (3 of 5 in the verification run; 0 of 3 in an earlier one).
> Missing values render as `n/a`, are excluded from the LLM aggregates, and the
> denominator is reported explicitly as `Responses with llm_response_time — n/N`,
> so a partial sample is never mistaken for a full one.

---

## Output

```
output/
  ai_parity_results.json          # per-resume raw comparison data + timings
  ai_parity_report.xlsx           # 5 sheets (below)
  raw_ai_responses/<resume>.json  # unmodified AI parser payloads
logs/
  ai_parser_parity.log
```

Workbook sheets:

1. **Full Detail** — one row per (resume × field), colour-coded, banded per resume
2. **Field Summary** — per-field pivot with Pass % / Partial % / Regression %
3. **Response Time Analysis** — per-resume timings + statistics incl. LLM metrics
4. **Regressions Only** — just Regression and Missing in B rows
5. **Run Info** — endpoints, counts, compared/excluded fields, timestamp

---

## Reliability

Albatross staging intermittently 500s with `SQLSTATE[HY000] [2002] Connection
refused`. Every HTTP call (login, presign, S3 upload, parse, AI parse) retries up
to 4 times with exponential back-off on 5xx / 429 / transport errors; 4xx fails
fast. Retry sleeps are excluded from the timing metrics — `request_ms` is measured
on the successful attempt only. A resume that still fails becomes an `ERROR` row
and the run continues.

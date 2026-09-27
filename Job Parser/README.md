# Job Parser — accuracy

Runs job descriptions through the job parser, then grades the new parser field by field against
**Sovren as ground truth**.

| Script | Does |
|---|---|
| `JobParser_Validator.py` | Logs in, uploads each JD (presigned URL → S3 → parse-job), extracts the key fields, and writes one sheet per JD |
| `generate_comparison.py` | Compares a Sovren workbook with a new-parser workbook and writes a scored comparison with editable verdicts |

---

## 1. Prerequisites

- **Python 3.10+**
- **A Recruit CRM login on the env you test**, with job parsing available.
- **JD files** — PDF, DOCX, DOC, TXT or PNG.

## 2. Install

```bash
git clone https://github.com/kowshik-recruitcrm/AI-Features-Accuracy-testing.git
cd "AI-Features-Accuracy-testing/Job Parser"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Configure

```bash
cp .env.example .env
```

| Variable | What to put |
|---|---|
| `PARSER_EMAIL` / `PARSER_PASSWORD` | the login used to upload and parse |
| `JOB_PARSER_BASE_URL` | Albatross base of the env to test. Default `https://albatross-dev.recruitcrm.net/v1` |

## 4. Run

### Parse the JDs
```bash
mkdir -p JD && cp /path/to/your/jds/* JD/
python3 JobParser_Validator.py        # run from this folder → Job_Parser_Results.xlsx
```
The workbook has one sheet per JD. Column A is the field and column B is the parsed value.

### Grade the new parser against Sovren
1. Get the same JDs parsed both ways, in the validator's layout (one sheet per JD, field in column A,
   value in column B, header in row 1). Sheet names must match between the two files.
   - `Sovren.xlsx` — Sovren's output (ground truth)
   - `LLM.xlsx` — the new parser's output. A `JobParser_Validator.py` run, renamed, works.
2. Put both files next to the script, then:
   ```bash
   python3 generate_comparison.py     # → Comparison_Report.xlsx
   ```

Initial verdicts are computed automatically. You can override any **Verdict** cell and every
total updates:

| Verdict | Score |
|---|---|
| Correct (exact match) | +1 |
| Partial Credit | +0.5 |
| GT Better (mismatch, Sovren right) | −1 |
| New Better (new parser right) | +1 |
| Both Wrong | 0 |

Sheets: Dashboard · Scoring Guide · Field-wise Accuracy · All JDs Combined · one sheet per JD.

The fields checked are listed in [`fieldstocheck.txt`](fieldstocheck.txt). The full API flow and test
scenarios are in [`API_AUTOMATION_SPEC_Job_Parser_Validator.md`](API_AUTOMATION_SPEC_Job_Parser_Validator.md).

## 5. Output

JDs, `job_responses/` and all `.xlsx` files are gitignored.

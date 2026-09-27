# Job Parser — accuracy

Two scripts: one runs JDs through the job parser, one grades the new parser against Sovren.

## Setup
```bash
cd "Job Parser"
pip install -r requirements.txt
cp .env.example .env        # PARSER_EMAIL / PARSER_PASSWORD
```

## Run

| Goal | Script | Input | Output |
|------|--------|-------|--------|
| Parse every JD (login → presigned URL → S3 upload → parse) and log the fields | `python3 JobParser_Validator.py` | JD files (PDF / DOCX / DOC / TXT / PNG) in `JD/` | `Job_Parser_Results.xlsx` |
| Grade the new parser against Sovren, field by field | `python3 generate_comparison.py` | `Sovren.xlsx` (ground truth) + `LLM.xlsx` (new parser), next to the script | `Comparison_Report.xlsx` |

`generate_comparison.py` scoring (editable in the workbook's Verdict column):
Exact match → Correct (+1) · Partial → Partial Credit (+0.5) · Mismatch → GT Better (−1).

The fields checked are listed in [`fieldstocheck.txt`](fieldstocheck.txt). The API flow and
test scenarios are in [`API_AUTOMATION_SPEC_Job_Parser_Validator.md`](API_AUTOMATION_SPEC_Job_Parser_Validator.md).

JDs and the input/output workbooks are not committed.

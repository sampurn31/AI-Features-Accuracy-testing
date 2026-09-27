# Resume Parser — accuracy comparisons

Sends the same resumes to two parser environments, compares every extracted field side by side,
and (for the model-provider test) scores both against a **blind ground-truth answer key**.

| Script | Compares |
|---|---|
| `Comparator.py` | Production (reference) vs Staging |
| `SambaNova_Comparator.py` | Production vs ss5 (SambaNova model) |
| `Dev_vs_Staging_Comparator.py` | Dev (new model provider) vs Staging (current implementation) |
| `score_vs_truth.py` | Dev and Staging output vs the answer key, field by field |

Resumes, raw responses, answer keys and reports are **not in the repo** because they hold real
candidate data. You supply the resumes; every script writes its output next to itself.

---

## 1. Prerequisites

- **Python 3.10+**
- **A login on each environment you compare** (Recruit CRM user with resume parsing available).
  The scripts log in via `https://albatross[-<env>].recruitcrm.{io,net}/v1/login` and upload
  each resume through the normal parse flow.
- **Resumes** in PDF / DOC / DOCX, placed in the folder the script reads (table below).
- Parsing creates candidates in those accounts, so use test accounts.

## 2. Install

```bash
git clone https://github.com/kowshik-recruitcrm/AI-Features-Accuracy-testing.git
cd "AI-Features-Accuracy-testing/Resume Parser"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Configure

```bash
cp .env.example .env
```

| Variable | Used by |
|---|---|
| `PROD_EMAIL` / `PROD_PASSWORD` | `Comparator.py`, `SambaNova_Comparator.py` |
| `STAGE_EMAIL` / `STAGE_PASSWORD` | `Comparator.py`, `Dev_vs_Staging_Comparator.py` |
| `DEV_EMAIL` / `DEV_PASSWORD` | `Dev_vs_Staging_Comparator.py` |
| `SS5_EMAIL` / `SS5_PASSWORD` | `SambaNova_Comparator.py` |
| `TS2_EMAIL` / `TS2_PASSWORD` | `verify_ts2.py` |
| `RESUME_FOLDER` | optional — overrides the resume folder for `Comparator.py`, `SambaNova_Comparator.py`, `rescore_analysis.py` |

The environment **URLs** are the `*_BASE` constants at the top of each comparator. The defaults are
`albatross.recruitcrm.io` (prod), `albatross-staging`, `albatross-dev` and `albatross-ss5`.recruitcrm.net.

## 4. Put the resumes in place

| Script | Reads resumes from |
|---|---|
| `Comparator.py` | `Test_Data/` (or `RESUME_FOLDER`) |
| `SambaNova_Comparator.py`, `rescore_analysis.py` | `ss5_Test_Data/` (or `RESUME_FOLDER`) |
| `Dev_vs_Staging_Comparator.py`, `build_review_packets.py` | `English_100/` |

These folders are gitignored, so nothing you drop in them gets committed.

## 5. Run

### Environment vs environment
```bash
python3 Comparator.py                  # → Parser_Comparison_Report.xlsx, raw_responses/
python3 analyze_results.py             # summary of that report

python3 SambaNova_Comparator.py        # → SambaNova_Comparison_Report.xlsx, raw_responses_ss5/
python3 rescore_analysis.py            # re-score it → SambaNova_GroundTruth_Report.xlsx

python3 Dev_vs_Staging_Comparator.py --limit 5            # pilot
python3 Dev_vs_Staging_Comparator.py --batch-size 10      # full run, resumable
python3 Dev_vs_Staging_Comparator.py --retry-failed       # redo resumes that errored
python3 Dev_vs_Staging_Comparator.py --report-only        # rebuild the workbook from saved responses
#   → Dev_vs_Staging_Report.xlsx, dev_vs_staging_state.json, raw_responses_{staging,dev}_en/
```
Each field is marked Match / Mismatch after normalising case, whitespace and phone formatting. [`COMPARISON_MODE.md`](COMPARISON_MODE.md) and [`BATCH_MODE.md`](BATCH_MODE.md)
cover the options.

### Against a ground-truth answer key (Dev vs Staging)
The answer key is built **from the resume text only, never from parser output**. That's what
makes it blind.

1. **Extract each resume's text** into `truth_packets/<resume>.txt`. For example:
   `pdftotext English_100/12.pdf truth_packets/12.pdf.txt`. On macOS, use `textutil -convert txt` for DOC/DOCX.
2. **Write the answer keys** into `truth/*.json`. Each file is a JSON array with one object per resume
   (`"resume": "12.pdf"`, then the fields), following [`truth_schema.md`](truth_schema.md). Split them
   across files however you like, for example `group_0.json`, `group_1.json`. Build them by hand, or with an
   LLM that sees only the resume text. [`review_rubric.md`](review_rubric.md) has the grading rules.
3. **Score both environments.** This needs `dev_vs_staging_state.json` from a `Dev_vs_Staging_Comparator.py` run:
   ```bash
   python3 score_vs_truth.py                  # → truth_scores.json
   python3 add_truth_verdict_to_report.py     # adds the verdict sheets to Dev_vs_Staging_Report.xlsx
   ```
4. **Optional human check of the decisive calls.** Put reviewer overrides in `verify/*.json`, following
   [`verify_instructions.md`](verify_instructions.md), then re-run `score_vs_truth.py`.
   `build_review_packets.py` writes per-resume review sheets to `review_packets/`, and
   `add_verdicts_to_report.py` merges manual verdicts from `review_verdicts/*.json`.

Scoring per field and environment: Correct (1) / Partial (0.5) / Wrong (0) / Missing (0) /
Hallucinated (0) / Unverified extra (0.5). Work history and education are matched entry by entry
(company/title, degree/institute), and then their dates are checked.

### Smoke check
```bash
python3 verify_ts2.py    # can we log in to ts2 and staging?
```

## 6. Output

All reports (`*.xlsx`), state files and raw responses are written next to the scripts and are
gitignored. Don't share them publicly: they contain parsed candidate data.

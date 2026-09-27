# Resume Parser — accuracy comparisons

Sends the same resumes to two parser environments, compares every extracted field side by side,
and (for the model-provider test) scores both against a blind ground-truth answer key.

Resumes, raw responses, answer keys and reports are **not** in the repo — they hold real candidate
data. Put your own resumes in the folders named below; every script creates its output next to itself.

## Setup
```bash
cd "Resume Parser"
pip install -r requirements.txt
cp .env.example .env        # fill in the logins for the environments you compare
```

## Pick what to run

| Goal | Script | Resumes from | Output |
|------|--------|--------------|--------|
| Production vs Staging, field by field | `python3 Comparator.py` | `Test_Data/` | `Parser_Comparison_Report.xlsx` |
| Production vs ss5 (SambaNova) | `python3 SambaNova_Comparator.py` | `ss5_Test_Data/` | `SambaNova_Comparison_Report.xlsx` |
| Dev (new model provider) vs Staging (current) | `python3 Dev_vs_Staging_Comparator.py` (`--limit`, `--batch-size`, `--retry-failed`, `--report-only`) | `English_100/` | `Dev_vs_Staging_Report.xlsx` + `dev_vs_staging_state.json` |
| Score Dev and Staging against the blind answer key | `python3 score_vs_truth.py` | `truth/*.json` + `truth_packets/` | `truth_scores.json` |
| Add the answer-key verdict to the report | `python3 add_truth_verdict_to_report.py` | — | updates `Dev_vs_Staging_Report.xlsx` |
| Add manual review verdicts to the report | `python3 add_verdicts_to_report.py` | `review_verdicts/*.json` | updates `Dev_vs_Staging_Report.xlsx` |
| Build per-resume review packets for manual checking | `python3 build_review_packets.py` | `English_100/` | `review_packets/*.md` |
| Re-score the SambaNova run after fixing the comparator | `python3 rescore_analysis.py` | `ss5_Test_Data/` | `SambaNova_GroundTruth_Report.xlsx` |
| Summarise a Prod vs Staging run | `python3 analyze_results.py` | — | reads `Parser_Comparison_Report.xlsx` |
| Login / parse smoke on ts2 + staging | `python3 verify_ts2.py` | — | console |

## Ground truth
The answer key is built from the resume text only, never from parser output. Schema:
[`truth_schema.md`](truth_schema.md). Grading rules: [`review_rubric.md`](review_rubric.md),
[`verify_instructions.md`](verify_instructions.md).

Per field, each environment gets Correct (1) / Partial (0.5) / Wrong (0) / Missing (0) /
Hallucinated (0) / Unverified extra (0.5). Work history and education are matched entry by entry,
then their dates are checked.

More detail: [`COMPARISON_MODE.md`](COMPARISON_MODE.md), [`BATCH_MODE.md`](BATCH_MODE.md).

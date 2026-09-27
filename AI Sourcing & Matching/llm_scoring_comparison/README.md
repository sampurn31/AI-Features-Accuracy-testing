# AI Matching — LLM scoring head-to-head

Scores the same job/candidate pairs with different LLMs and compares each against **Claude Sonnet
4.5 as the reference**, to find the scorer model closest to it. Every comparison uses the same system
prompt, the same JD and the same resume text for every model.

| Script | Compares |
|---|---|
| `llm_scoring_eval.py` | the platform's own AI Matching scores (web app + Aries) vs Sonnet, for every candidate the platform surfaced |
| `direct_llm_eval.py` | Sonnet vs Gemini 2.5 Flash Lite, both called directly. Candidates come from `/v1/candidates/search/get`, so the platform's AI ranking doesn't filter them first |
| `llm_3way_eval.py` | three-way: Sonnet vs a DeepSeek-backed env vs a Gemini-backed env |
| `llm_3way_phased.py` | the same three-way run, one env at a time, for when both envs share a database |
| `build_*_excel.py` | turn each script's JSON report into a workbook |

---

## 1. Prerequisites

- **Python 3.10+**
- **An Anthropic API key** (the Sonnet reference). **A Gemini API key** too, for `direct_llm_eval.py`.
- **A Recruit CRM test account with AI Matching enabled** on the envs you compare. The scripts log in
  with it on every host they call.
- **Jobs and candidates in that account.** The scripts were built against the synthetic dataset in
  `synthetic_test_data/data/jobs.json`, loaded into the account. The job slugs to score are listed
  at the top of each script (`JOBS` / `JOBS_ALL`). Replace them with your own account's job slugs.

## 2. Install

```bash
git clone https://github.com/kowshik-recruitcrm/AI-Features-Accuracy-testing.git
cd "AI-Features-Accuracy-testing/AI Sourcing & Matching/llm_scoring_comparison"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Configure

```bash
cp .env.example .env
```

| Variable | Needed by |
|---|---|
| `ANTHROPIC_API_KEY` | all except `llm_3way_phased.py` |
| `GEMINI_API_KEY` | `direct_llm_eval.py` |
| `RCRM_LOGIN_EMAIL` / `RCRM_LOGIN_PASSWORD` | all |

The **hosts and jobs** are set in the `CONFIG` block at the top of each script. Edit them for your envs:

| Script | Hosts it calls |
|---|---|
| `llm_scoring_eval.py` | `WEBAPP_HOST` (default `albatross-titans6`), `ARIES_HOST` (`dev-aries`) |
| `direct_llm_eval.py` | `CRM_HOST` (`albatross-titans10`). Paste the JD into `HARDCODED_JD_TEXT`, or leave it empty to fetch it |
| `llm_3way_eval.py`, `llm_3way_phased.py` | `ENVS` → `deepseek` (`albatross-titans10`), `gemini` (`albatross-titans8`) |

System prompts: `System_Prompt.txt` (old) and `New_systemprompt.txt` (new).

## 4. Run

### Platform vs Sonnet
```bash
python3 llm_scoring_eval.py                    # score every job, resumable
python3 llm_scoring_eval.py --slug <jobSlug>   # one job
ONLY_SLUGS=slug1,slug2 python3 llm_scoring_eval.py
python3 llm_scoring_eval.py --report-only      # rebuild the report from saved records
python3 build_eval_excel.py                    # → llm_eval_report.xlsx
```
Other flags: `--fresh` (ignore saved records), `--webapp-only` (skip Sonnet), `--no-recheck`.

### Sonnet vs Gemini, direct
```bash
python3 direct_llm_eval.py --prompt old --limit 5    # pilot with System_Prompt.txt
python3 direct_llm_eval.py --prompt new              # full run with New_systemprompt.txt
python3 direct_llm_eval.py --prompt new --report-only   # rebuild from saved raw records
```
Other flags: `--fresh`, `--sonnet-only`, `--gemini-only`. The workbook is
`direct_eval_report_<old|new>.xlsx`. Rebuild it on its own with `python3 build_direct_excel.py --prompt new`.

### Three-way
```bash
python3 llm_3way_eval.py                       # Sonnet + both envs → llm_3way_report.{txt,json}

# If the two envs share one database, run them one at a time instead:
python3 llm_3way_phased.py --phase deepseek    # needs llm_3way_report.json from the run above (Sonnet scores)
#   … clear the shared scores …
python3 llm_3way_phased.py --phase gemini
python3 llm_3way_phased.py --phase report      # → llm_3way_final_report.{txt,json}
python3 build_3way_excel.py                    # → llm_3way_final_report.xlsx
```

## 5. Output

Reports and raw records (`llm_*.json`, `direct_eval_*`, `*.txt`, `*.xlsx`) are written next to
the scripts and are gitignored. They contain candidate data from the account.

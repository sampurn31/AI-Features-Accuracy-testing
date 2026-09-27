# AI Matching — LLM scoring head-to-head

Scores the same job/candidate pairs with different LLMs and compares them against Claude Sonnet as
the reference, to see which scorer model is closest.

## Setup
```bash
cd "AI Sourcing & Matching/llm_scoring_comparison"
pip install -r requirements.txt
cp .env.example .env
```

## Run

| Goal | Script |
|------|--------|
| Platform AI-matching scores vs Sonnet, for every candidate the platform surfaced | `python3 llm_scoring_eval.py` → `python3 build_eval_excel.py` |
| Sonnet vs Gemini 2.5 Flash Lite, called directly with the same prompt (no platform ranking) | `python3 direct_llm_eval.py` (writes its Excel via `build_direct_excel.py`) |
| Three-way: Sonnet (reference) vs DeepSeek env vs Gemini env | `python3 llm_3way_eval.py` → `python3 build_3way_excel.py` |
| Same three-way run, one env at a time (the envs share a DB) | `python3 llm_3way_phased.py --phase deepseek`, then `--phase gemini` |

Environment hosts and job slugs are set at the top of each script. The scoring system prompts
are `System_Prompt.txt` (old) and `New_systemprompt.txt` (new). Jobs come from the synthetic set in
`synthetic_test_data/data/jobs.json`.

Reports (`*.json`, `*.txt`, `*.xlsx`) are written next to the scripts and are not committed.

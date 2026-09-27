"""
Reconstruct the EXACT payload sent to the LLM judge during the 300-variation scale run.

Nothing here is paraphrased: the system prompt is read from the same rubric file the run
used, and the user message is rebuilt with the same code path (llm.judge_candidate_criteria),
filled with a real candidate taken from the run's own checkpoint. The result is what the
judge actually saw, plus the verdict it actually returned.

    ./.venv/bin/python reconstruct_judge_call.py                     # first judged candidate
    ./.venv/bin/python reconstruct_judge_call.py --case <case-id>    # a specific variation
    ./.venv/bin/python reconstruct_judge_call.py --out FILE.md

Reads output/scale_checkpoint_20260723-full.jsonl (the 300-run checkpoint). Makes no API calls.
"""
import argparse
import json
import os

import config
import llm

CHECKPOINT = os.path.join(config.OUTPUT_DIR, "scale_checkpoint_20260723-full.jsonl")


def build_user_message(query, criteria, candidate):
    """Byte-for-byte the string llm.judge_candidate_criteria assembles as the user turn."""
    crit_lines = "\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in (criteria or []))
    sys_rows = [{"criterion": a.get("criterionText"), "weight": a.get("weight"),
                 "score": a.get("score"), "reasoning": a.get("reasoning")}
                for a in (candidate.get("matchAnalysis") or [])]
    parts = [
        "SEARCH QUERY:\n" + (query or ""),
        "\n\nWEIGHTED CRITERIA (as configured):\n" + crit_lines,
        "\n\nCANDIDATE PROFILE (the ONLY evidence — read the FULL experience list):\n"
        + json.dumps(llm._profile_for_judge(candidate), indent=2, ensure_ascii=False)[:16000],
        "\n\nSYSTEM'S PER-CRITERION OUTPUT (audit every row, in order; overall matchScore="
        + str(candidate.get("matchScore")) + "):\n"
        + json.dumps(sys_rows, indent=2, ensure_ascii=False)[:6000],
        "\n\nAudit per the rubric: one entry per system row, same order. Cite concrete profile "
        "evidence in every reasoning_note. Return JSON only.",
    ]
    return "".join(parts)


def pick(case_id=None):
    """First variation carrying a judged candidate whose full profile was kept (or the named one).

    The checkpoint stores verdicts flattened at the variation level (`judge[]`) and keeps the
    full profile of each judged candidate in `candidates[]`; they join on cand_id / rank.
    """
    with open(CHECKPOINT) as fh:
        for line in fh:
            r = json.loads(line)
            if case_id and r.get("id") != case_id:
                continue
            verdicts = r.get("judge") or []
            cands = [c for c in (r.get("candidates") or []) if c and c.get("matchAnalysis")]
            if not verdicts or not cands:
                continue
            v = verdicts[0]
            cand = next((c for c in cands if c.get("id") == v.get("cand_id")), cands[0])
            return r, cand, v
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--out", default="JUDGE_PROMPT_ANATOMY.md")
    a = ap.parse_args()

    rec, cand, v = pick(a.case)
    if not rec:
        raise SystemExit("no judged candidate found in the checkpoint")

    rubric = open(config.SOURCING_CRITERION_RUBRIC).read()
    user = build_user_message(rec["query"], rec.get("criteria") or [], cand)
    schema = json.dumps(llm.CRITERION_JUDGE_SCHEMA, indent=2)
    verdict = json.dumps(v, indent=2, ensure_ascii=False)
    name = f"{cand.get('firstname') or ''} {cand.get('lastname') or ''}".strip() or "(unnamed)"

    md = f"""# What we actually sent the LLM judge — 300-variation scale run

Reconstructed from the run's own checkpoint, not written from memory. The system prompt is
the rubric file the run loaded; the user message is rebuilt through the same code path
(`llm.judge_candidate_criteria`) with a real candidate from the run.

| | |
|---|---|
| Run | `20260723-full` · 300 variations · prod · 2026-07-23 |
| Judge model | `{config.MODEL}` at effort `{config.JUDGE_EFFORT}`, `max_tokens=2000` |
| Called from | `run_sourcing_scale.py:114` → `llm.judge_candidate_criteria()` |
| System prompt | `prompts/sourcing_criterion_rubric.md` ({len(rubric.splitlines())} lines) |
| Output | forced JSON via `output_config.format.json_schema` — the judge cannot reply in prose |
| Example shown | case `{rec['id']}` · candidate **{name}** · system matchScore {cand.get('matchScore')} |

The judge call has **four parts**. Two are fixed for every call (the system prompt and the
output schema); two are filled per candidate (the search query + criteria, and the profile +
the system's own scoring output).

Note what is **not** sent: the product's own Neptune system prompt is never shown to the judge.
The judge sees only the query, the criteria, the candidate profile, and the scores the system
produced — so it re-scores independently rather than being led by the product's instructions.

---

## Part 1 — SYSTEM PROMPT (identical on every call)

Sent as the `system` block with `cache_control` so repeated calls pay cache-read on it.
Verbatim contents of `prompts/sourcing_criterion_rubric.md`:

````text
{rubric.rstrip()}
````

---

## Part 2 — USER MESSAGE (rebuilt per candidate)

Assembled by `llm.judge_candidate_criteria`. Five segments, always in this order:

1. `SEARCH QUERY:` — the variation's query text (for JD-format cases this *is* the JD)
2. `WEIGHTED CRITERIA (as configured):` — one line per criterion with its weight
3. `CANDIDATE PROFILE (the ONLY evidence — read the FULL experience list):` — JSON, capped at
   16,000 chars. Every experience entry is included; roles are never truncated away, because a
   dropped role makes the judge flag genuinely-grounded reasoning as hallucinated.
4. `SYSTEM'S PER-CRITERION OUTPUT (audit every row, in order; overall matchScore=N):` — the
   product's own criterion/weight/score/reasoning rows, capped at 6,000 chars
5. A closing instruction: one verdict per system row, in the same order, citing concrete
   profile evidence.

Here is the complete message for this candidate, exactly as sent ({len(user):,} characters):

````text
{user}
````

---

## Part 3 — FORCED OUTPUT SCHEMA

Passed as `output_config.format = {{"type": "json_schema", "schema": ...}}`, so every verdict is
machine-readable and tabulates without parsing prose. One entry per criterion, plus an overall
call:

````json
{schema}
````

`judge_score` carries no min/max in the schema — Anthropic's structured output rejects numeric
bounds on integers, so the 0–100 range is stated in the rubric and clamped by the caller.

---

## Part 4 — WHAT CAME BACK

The verdict this exact call produced, as stored in the checkpoint. The runner flattens the
schema response for reporting — each `criteria[]` entry becomes a `crit_verdicts[]` row joined
to the system's own score and reasoning, with `gap` computed — so this is the same content as
Part 3's schema, in the shape the workbook consumes:

````json
{verdict}
````

---

## How the verdicts became the reported numbers

Each returned `criteria[]` entry became one row of the **Criterion Verdicts** sheet (2,351 rows)
in `output/sourcing_scale_20260723-full.xlsx`. `reasoning_verdict` produced the
"reasoning correct 46%" figure; `score_verdict` produced "score agreement 60%, too_high 724 vs
too_low 208"; `overall_sensible` produced "only 32% of overall scores judged clearly sensible"
across 599 candidates.

Judge coverage was a sample, not the full set: ranks 1, middle and last of each scored
variation — 604 candidates of the 15,270 retrieved, about 850 judge calls. The deterministic
checks (score formula, ranking order, cross-page duplicates, criteria coverage) ran on **every**
candidate; the judge only ever supplied the accuracy opinion on top.
"""
    with open(a.out, "w") as fh:
        fh.write(md)
    print(f"wrote {a.out}")
    print(f"  case {rec['id']} · candidate {name} · matchScore {cand.get('matchScore')}")
    print(f"  system prompt {len(rubric):,} chars · user message {len(user):,} chars")


if __name__ == "__main__":
    main()

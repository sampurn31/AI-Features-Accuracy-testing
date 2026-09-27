# AI Sourcing — A/B Scoring-Quality Comparison Rubric

You are given ONE candidate's objective profile, a search query with weighted criteria, and
**two independent scorings of that same candidate** — Scoring A and Scoring B. Each scoring has
an overall `matchScore` (0–100) and a per-criterion breakdown (`score` 0–100 + `reasoning`).

Decide which scoring is **more accurate and better justified by the candidate's profile** — or
whether they are equivalent. You are blind to which system produced A vs B; do not guess.

## What "better" means
- **Grounding** — the per-criterion reasoning is supported by the profile (real roles, skills,
  years, employers). A scoring whose reasoning cites concrete, correct evidence beats one that is
  vague, generic, or asserts things the profile doesn't support.
- **Calibration** — the per-criterion scores and the overall matchScore fairly reflect how well
  the profile meets each criterion. Strong, on-target evidence should score high; missing evidence
  should score low. A scoring that over-credits weak evidence or under-credits strong evidence is
  worse.
- **Consistency** — the overall matchScore is a sensible reflection of the per-criterion scores
  and their weights; the label/score/reasoning don't contradict each other.

## What "better" does NOT mean
- NOT "which gave the higher score." A lower score can be the *more accurate* one.
- NOT length or confidence of prose. Judge correctness, not verbosity.
- Do not favor A or B by position — they are randomized.

## Verdict
- `A_better` / `B_better` — one scoring is clearly more grounded and/or better calibrated for this
  candidate against these criteria.
- `equivalent` — both are about equally accurate (or differ only trivially/stylistically).

## Output
Return JSON only (schema enforced): `verdict`, `confidence`, `key_differences`, `reasoning`.
In `key_differences`, name the specific criterion/criteria where the two scorings diverge and say
which handled it better and why (cite the profile evidence). A human reads this to validate you.

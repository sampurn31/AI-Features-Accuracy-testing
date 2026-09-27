# AI Sourcing V3 — Scored-Candidate Accuracy Rubric (Global / db=2)

You are grading whether the system's **scoring** of ONE sourced candidate is faithful to
that candidate's profile and to the search's weighted criteria. You are NOT re-scoring the
candidate yourself and you are NOT judging whether the candidate is "good" — only whether
the system's output is **honest and internally consistent with the evidence given**.

You receive: the search query, the weighted criteria, the candidate profile (the ONLY
evidence you may use — read the FULL experience list), and the system's output
(`matchScore` 0–100, `matchLabel`, and `matchAnalysis` = per-criterion `{score, reasoning}`,
where each criterion's `score` is 0–100 and the overall `matchScore` is their weighted average).

Grade three axes.

## 1. `grounding` — is each criterion's `reasoning` supported by the profile?
- `grounded` — every reasoning statement is backed by something actually in the profile
  (position, company, keySkills, the experience roles/descriptions, education, years).
- `partly_grounded` — mostly fine, but at least one line over-claims or slightly misattributes
  a detail (e.g. cites the right skill at the wrong employer).
- `hallucinated` — reasoning invents concrete facts the profile does not support (a company,
  role, or project that appears nowhere). **Before deciding this, scan the ENTIRE experience
  list — a role you don't see at first may be further down. Only use `hallucinated` when the
  claim is genuinely absent, not a reasonable paraphrase.**

## 2. `scores_justified` — are the per-criterion 0–100 `score`s defensible?
- `correct` — each per-criterion score is reasonable for the evidence (strong evidence → high,
  little/none → low).
- `partly` — one score is arguably too high or too low given the evidence.
- `wrong` — a score clearly contradicts the evidence (near-100 with no supporting evidence, or
  near-0 when the profile plainly meets the criterion).

## 3. `score_sensible` — is the overall `matchScore` a reasonable fit?
Judge it relative to the per-criterion scores and their weights (it should be their weighted
average) and to how well the profile matches the emphasised criteria.
- `yes` — tracks the weighted evidence.
- `borderline` — a bit high/low but defensible.
- `no` — clearly contradicts the per-criterion scores or the evidence.

## Output
Return JSON only (schema enforced): `grounding`, `scores_justified`, `score_sensible`,
`confidence`, `reasoning`, `issues`. Put concrete, cite-the-evidence detail in `reasoning`
(name the criterion + the profile line); list specific defects in `issues` (or "" if none).
The deterministic checks (ranking, matchScore = weighted-avg, coverage, shape) are scored
separately — focus on what only a reader of the profile can catch: **hallucinated reasoning**
and **per-criterion scores that don't match the evidence**.

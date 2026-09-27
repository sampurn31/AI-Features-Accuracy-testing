# AI Sourcing — Candidate Evaluation Rubric (independent scoring + API verdict + relevance)

You are auditing an AI Sourcing result. You are given a recruiter **search query**, its
**weighted criteria**, **one candidate's objective profile**, and **our API's scoring** of that
candidate (an overall `matchScore` 0-100 plus a per-criterion breakdown with `score` 0-100 and
`reasoning`). Do three things, grounded ONLY in the candidate's profile.

## 1. Score the candidate yourself (independent)
- For **each criterion**, give your own `llm_score` 0-100 based strictly on evidence in the profile
  (roles, skills, years, employers, location, education). Read the FULL experience list before
  concluding a skill/role is absent.
- Give your own `llm_overall_score` 0-100 for how well the candidate fits the query as a whole
  (a weighted sense of the criteria, not a mechanical average unless that's right).
- **Anchored scale:** 0 = no evidence · 31-50 = adjacent/partial · 51-70 = related with gaps ·
  71-85 = strong direct evidence · 86-100 = excellent, explicit. Score on stated evidence only —
  never infer or invent. Trust the work history over a self-reported summary when they conflict.

## 2. Judge OUR API's scores
- Per criterion set `api_verdict`:
  - `agree` — the API's score is close to what the evidence supports (≈ within ~15 pts and the
    reasoning is grounded).
  - `api_too_high` — the API over-credits (weak/absent evidence, fabricated or ungrounded reasoning).
  - `api_too_low` — the API under-credits real, clear evidence.
- Set `api_overall_good_enough` for the overall matchScore:
  - `yes` — the overall score fairly reflects the candidate's true fit.
  - `borderline` — roughly defensible but noticeably off or shakily reasoned.
  - `no` — the overall score misrepresents the candidate (materially too high or too low).
- In `api_overall_note`, say briefly why, citing the criterion/evidence that drove it.

## 3. Relevance to the query
Independently of the score, decide whether this candidate is actually a **relevant** result for the
search query — i.e. does the profile match the query's core intent (the role/function, essential
domain, and any hard constraints like location or licensure)?
- `yes` — clearly the kind of person the query is looking for.
- `partial` — adjacent/transferable but misses a core element (wrong seniority, wrong sub-domain,
  wrong location, missing a stated must-have).
- `no` — not a relevant result (different role/domain, or fails a hard constraint).
Give `relevance_reason` citing the specific profile evidence (or the specific missing element).

## Output
Return JSON only (schema enforced): `llm_overall_score`, `per_criterion[{criterion, llm_score,
reasoning, api_verdict}]`, `api_overall_good_enough`, `api_overall_note`, `relevant`,
`relevance_reason`. Judge correctness and grounding, not verbosity. A human reads your reasoning to
validate you — cite real evidence, never assert what the profile doesn't support.

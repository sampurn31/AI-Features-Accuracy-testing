# Activity Context accuracy evaluation

Does giving the AI Matching scorer a candidate's **Notes and Call Logs** make its
reasoning and scores better?

Two flows:

- **Flow 1 — Activity Context Evaluation.** Score every job on AI2, then have a judge
  grade each analysis 1–5 **at criterion level**: did the activity reach the criterion it
  bears on, and was that criterion scored correctly?
- **Flow 2 — AI2 vs DEV.** Same database, same job, same pinned criteria. The only
  intended difference is that AI2 can see the activity and DEV cannot.

---

## Approach: reuse the account's real data

Nothing is invented. Jobs and candidates come from the account; the only thing this
project adds is notes and call logs, written against what each candidate actually has on
file.

```bash
python3 selfcheck.py            # offline, free — always run first
python3 corpus.py --build       # fetch real jobs + candidates
python3 pools.py                # ask the system which candidates match which job
python3 design.py --plan        # decide the activity, grounded in each profile
python3 design.py --show 4      # eyeball a few before writing anything
python3 seed_plan.py --dry-run  # counts only
python3 seed_plan.py            # write notes + call logs (additive, reversible)
python3 evaluate.py             # both flows
python3 report.py               # the Excel
```

`python3 seed_plan.py --undo` removes every note and call log this wrote.

---

## Five things that are easy to get wrong

**1. The matching endpoint is not standalone.** Calling `search/get` on an unconfigured
job returns HTTP 400 forever. The sequence is `POST ai-matching/configuration` →
`POST ai-matching/configuration/criteria` (`isSave: true`) → *then* search, with polling.

**2. Candidates here have no resume.** Every record in this account is a structured
import — no resume file, no `candidate_summary`. What they have is `work_history` with
real descriptions (768–10,155 chars, median ~1,500). **That text is the resume** as far as
scoring is concerned, and it is what the judge must be shown.

**3. The score is `matchScore`, not `matchAnalysis.score`.** The latter does not exist.

**4. Notes cannot be backdated.** Recency reads `updated_on`, which the server assigns.
Consecutive items for one candidate are spaced 1.1s apart so ordering is unambiguous.

**5. `call_type` must be `CALL_INCOMING` or `CALL_OUTGOING`.** Anything else is a 422 and
the call log is silently lost.

---

## Two design choices that decide whether the result means anything

**Criteria are pinned.** Criteria are LLM-generated from the JD, so configuring twice
gives two different sets. `evaluate.py` configures once and saves that same object to both
deployments — which also wipes stored scores, forcing a genuine re-score.

**Flow 2 is blind.** The analyses are shown as "System A" and "System B", randomised per
candidate, and the judge is never told which is which. Without that a judge favours
whichever it believes is newer, and activity also makes answers longer — which judges
reward on its own.

---

## Writing the activity: name the concrete thing

A note that says *"they actually led that work"* names no skill and tests nothing —
measured at **2.4–2.8** judge score against **4.0–5.0** for the same scenario written
explicitly. So `design.py` reads the job's real criteria against the candidate's real work
history and writes notes that name the criterion, the token and the employer.

Scenario assignment is **driven by the profile**, never stamped on:

| Profile offers | Scenarios it can host |
|---|---|
| A real technology gap | `mod_override`, `weak_upskilling`, `partial_evidence` |
| Real evidence | `mod_hidden`, `mod_concern`, `mod_contradiction`, `conflicting_notes` |
| Evidence + baseline ≥70 | `strong_clean`, `strong_blocker`, `blocker_resolved` |
| Nothing at all | `empty_profile` |
| Any | `weak_glowing` (false-positive control), `weak_noise` (control), `strong_manynotes` |

A gap is only asserted on a **technology** token. Absence of "React" from a profile is
evidence; absence of "B2B SaaS" is not — the employer may itself be a SaaS company.

---

## Reading the report

**Sheet 1** Job · Candidate · AI Match Score · AI Match Analysis · Judge Score · Judge
Reasoning · Activity Context Used · Final Verdict

**Sheet 2** Job · Candidate · DEV Score · AI2 Score · DEV Reasoning · AI2 Reasoning ·
Judge Comparison · Winner · Did Activity Add Value? · Final Verdict

**Sheet 3** Response time per job, with `Ran First` — whoever runs first builds the
candidate pool (~200s) and the second reads it warm (~15s), so the raw column is mostly
measuring run order. Like-for-like cold/cold and warm/warm figures are in the console.

The numbers worth arguing about:

- **Right criterion scored correctly, as a share of candidates that had activity.** Not
  the overall score — that is a weighted sum, so one fact can barely move it.
- **DEV referencing activity must be 0.** If it is not, DEV is not a control and Flow 2
  measures nothing.
- **Scenario means**, not the global mean: the controls (`weak_noise`, `strong_clean`)
  should score high, and if they do not the judge is miscalibrated rather than the product
  being broken.

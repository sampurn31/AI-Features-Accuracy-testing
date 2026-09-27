# Ground-truth review rubric — Dev (new provider) vs Staging (current)

For each resume packet in `review_packets/<resume>.md`, read the **resume text** (the ground
truth) and decide, per field, which environment extracted it better.

## Winner values (per field)
- `Both`    — both correct, or differences are only formatting/equivalent (e.g. `+91-98..` vs `+9198..`,
              `MBA (IT)` vs `MBA in IT`, same skills in different order). Also `Both` if the resume
              has no such data and both left it empty.
- `Staging` — Staging is clearly more correct/complete than Dev.
- `Dev`     — Dev is clearly more correct/complete than Staging.
- `Neither` — both wrong, or both miss data that is clearly present in the resume.

Minor cosmetic differences do NOT make a winner. Pick a winner only when one side is
materially better for a recruiter using the record.

## Fields to judge
| field | what "correct" means |
|---|---|
| fullName | candidate's real name, properly split/cased |
| email | candidate's email exactly as in resume (not a referee's) |
| phone | candidate's phone; same digits = Both |
| city, state, country | candidate's current location. Filling a value that is correctly inferable (e.g. Pune → Maharashtra, India) beats leaving it empty. A wrong inference loses. |
| currentTitle, currentOrg | the most recent / current role. Appending city to company name is a minor defect. |
| workExpYears | total professional experience implied by the resume dates; within ±1 year = correct |
| skills | real skills from the resume (skills section + clearly stated tech/tools). Penalise generic filler not supported by the text (see grounding check `skills_not_in_text`, but note paraphrased-yet-supported skills are fine) and missing key skills. |
| qualification | highest qualification |
| workHistory | every job in the resume captured once, correct title/company/dates; penalise missed jobs, duplicated/split entries, hallucinated entries, wrong dates |
| education | every degree captured with correct degree/institute/years |
| linkedin, postalCode | present in resume → should be extracted |

## Output
For each resume, one JSON object:
```json
{
  "resume": "10.pdf",
  "fields": {
    "fullName": {"winner": "Both", "note": ""},
    "workHistory": {"winner": "Dev", "note": "Staging added 3 undated duplicate Chief Engineer entries"}
    // ... every field in the table above
  },
  "overall": "Dev" | "Staging" | "Tie",
  "overall_reason": "one sentence — the decisive differences"
}
```
`note` is required whenever winner is not `Both` (≤ 25 words, concrete, cite the resume).
`overall` = your holistic judgement of which record a recruiter would rather have; weight
contact/identity fields and work history most. Use `Tie` when neither is materially better.

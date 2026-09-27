# Verify the scorer's decisive calls against the resume

For each resume you're given:
1. Read `verify_packets/<resume>.md`. It lists ONLY the fields where Staging and Dev got a
   different status from the automatic scorer, with the answer-key value, both outputs and the
   scorer's status for each env.
2. Read the resume itself: `truth_packets/<resume>.txt` (full text). If the text layer looks
   garbled/jumbled, render the page: `pdftoppm -f <page> -l <page> -r 110 -png English_100/<resume> $TMPDIR/v`
   and read the image (doc/docx: `textutil -convert txt -stdout English_100/<resume>`).
3. For every listed field and each env, decide whether the scorer's status is right **according to
   the resume**. Rules:
   - Truth is what the resume actually prints (verbatim; e.g. a name printed "Ca In Fr A Ncesco Sum"
     is correct as-is, a parser's "Francisco Sum" is wrong).
   - Correct = right value (formatting/case/country-code differences are fine; a correct inference
     like Pune → Maharashtra is fine). Partial = right but incomplete / slightly off. Wrong = a
     different value. Missing = empty although the resume has it. Hallucinated = value not supported
     by the resume at all. Unverified extra = plausible inference the resume doesn't state.
   - Also check the answer key itself — if the key is wrong (e.g. missed a job, wrong date, wrong
     current employer), judge by the resume, not the key.
   - workHistory / education / skills: give a `score` 0–1 reflecting how complete and correct that
     env's list is versus the resume (1 = every entry right with right dates; subtract for missed,
     duplicated, invented entries and wrong dates). Only override if the scorer's number is clearly
     off (by ≥ 0.15).

## Output
Write a JSON array to the path you were given, one object per override (only where the scorer is
wrong — leave out fields where it's right):
```json
{"resume": "200.pdf", "field": "currentOrg", "env": "Staging",
 "scorer_status": "Wrong", "correct_status": "Correct", "score": null,
 "reason": "Resume p1 lists 'United Arab Shipping Co.' as current employer; Staging matches it."}
```
`score` is required (0–1) for workHistory/education/skills overrides, otherwise null.
Also add one object per resume with `"field": "_checked"`, `"env": "-"`, `"reason": "<one line: is the
scorer's winner right? why>"` so we know every resume was reviewed.
Validate with `python3 -m json.tool <file> > /dev/null`. Do not modify any other file.

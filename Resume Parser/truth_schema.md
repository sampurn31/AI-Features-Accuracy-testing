# Blind ground-truth extraction

You are building the **answer key** for a resume-parser accuracy test. You see ONLY the resume.
Do NOT open any parser output (review_packets/, raw_responses*/, dev_vs_staging_state.json,
review_verdicts/, Dev_vs_Staging_Report.xlsx). Your key must come from the resume alone.

Source: `truth_packets/<resume>.txt` (full extracted text). If the text is garbled, jumbled by
columns, or looks incomplete, open the original `English_100/<resume>` (pdftotext without -layout,
or `textutil -convert txt -stdout` for doc/docx) and read that as well.

**The truth is what the resume actually says — verbatim.** Only normalise case/whitespace.
Do NOT "fix", re-spell, translate or reinterpret values, even if they look odd. Example: if the
resume's name is printed as "Ca In Fr A Ncesco Sum", the true fullName is "Ca In Fr A Ncesco Sum"
(a parser that outputs "Francisco Sum" is WRONG). If you suspect the PDF text layer differs from
what is visibly printed, render the page (`pdftoppm -f 1 -l 1 -r 110 -png <pdf> $TMPDIR/x`) and
use what is printed. Use `""` / `[]` when the resume does not contain it. Never guess a value the
resume does not support; mark inferences in `notes`.

## Output: one JSON object per resume
```json
{
  "resume": "example.pdf",   // ILLUSTRATIVE VALUES ONLY
  "fullName": "Jane Doe",
  "firstName": "Jane",
  "lastName": "Doe",
  "email": "jane.doe@example.com",          // candidate's own (not referees')
  "phone": "+49 000 0000000",             // candidate's primary phone
  "city": "Hamburg",                       // candidate's CURRENT residence/location
  "state": "Hamburg",                      // stated, or unambiguously implied by city ("" if unclear)
  "country": "Germany",                    // stated, or unambiguously implied by city
  "postalCode": "00000",
  "linkedin": "",                          // URL/handle if present
  "currentTitle": "Technical Superintendent",   // title of most recent / current job
  "currentOrg": "TB Marine Shipmanagement GmbH", // employer of most recent / current job
  "isCurrentlyEmployed": false,            // true only if resume says present/current/ongoing
  "workExpYears": 30,                      // total professional years from first job start to latest end/today (2026-09); integer
  "workExpYearsBasis": "1988-2018 per dated roles",
  "highestQualification": "MA Engineering",
  "skills": ["Marine engineering", "Technical ship management", "..."],
                                           // skills EXPLICITLY listed in a skills/competences/tools section,
                                           // plus concrete tools/technologies named in the text. No soft-skill guessing.
  "keySkills": ["...5-10 most important of the above for a recruiter..."],
  "workHistory": [                         // EVERY job/contract, most recent first, each once
    {"title": "Technical Superintendent", "company": "TB Marine Shipmanagement GmbH",
     "start": "2018-05", "end": "2018-12", "location": "Hamburg"}
                                           // dates as YYYY-MM, YYYY if only year, "Present" if current, "" if undated
  ],
  "education": [                           // every degree/diploma; exclude short training courses & certificates
    {"degree": "MA Engineering", "specialization": "Marine Engineer", "institute": "St.Petersburg State Maritime Academy",
     "start": "", "end": "1993"}
  ],
  "certificationsAndCourses": ["..."],     // short courses/licences kept separate from education
  "notes": "anything ambiguous: e.g. two phones, city inferred from address, undated jobs, sea-contracts list"
}
```

Rules:
- workHistory: include undated jobs (dates ""). Sea-service tables: one entry per contract/vessel line.
  Internships count. Education, volunteering, and "seeking opportunities" lines are NOT jobs
  (volunteering may be listed only if presented as work experience — note it).
- Count carefully; the job count is scored.
- Write the JSON array for all your resumes to the output path you were given, then validate with
  `python3 -m json.tool <file> > /dev/null`.

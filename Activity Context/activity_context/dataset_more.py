#!/usr/bin/env python3
"""
Candidates for the remaining three jobs — FP&A (mid), Junior Frontend (entry) and the
clinical and data science roles.

Split out of dataset.py purely for size. Same shape: `dataset.load_all()` merges them.

Each entry names the concrete thing the activity reveals. A note that says "they actually
led that work" names no skill and tests nothing — measured at 2.4-2.8 judge score against
4.0-5.0 for the same scenario written explicitly.
"""

CANDIDATES_MORE = {
 "nurse": [
  {"name": ("Hana", "Okonkwo"), "fit": "strong",
   "scenario": "five notes - only the latest three should count",
   "signal": "The three most recent notes carry a competing offer, a two-week deadline and "
             "confirmed availability. The two oldest are administrative.",
   "notes": ["Added Hana to the pipeline from an inbound application. Screened in.",
             "Sent the full job description and arranged the first conversation.",
             "Hana has a competing offer from another trust at a comparable band and a "
             "higher base. She prefers this role for the unit, but the other offer is real.",
             "She has been given a deadline of two weeks to respond to the other offer. If "
             "we want her the process has to move this week.",
             "Hana confirmed availability including full rotating shifts and can start "
             "within 30 days. The competing offer is the only remaining obstacle."],
   "calls": [],
   "r": {"headline": "SENIOR STAFF NURSE", "location": "Manchester, United Kingdom",
         "summary": "Band 6 nurse with 10 years in acute medicine. Deteriorating-patient "
                    "lead for the unit and regular shift coordinator.",
         "skills": "NEWS2 escalation, structured A-E assessment, SBAR handover, IV therapy, "
                   "cannulation and venepuncture, complex wound care, electronic records",
         "role1": "Senior Staff Nurse | Manchester Royal Infirmary | Feb 2019 - present",
         "bullets1": "- Recognised and escalated 40+ deteriorating patients in the past year\n"
                     "  using NEWS2, with no missed escalations at audit.\n"
                     "- Coordinates the shift three times a week: allocation, bed flow and\n"
                     "  escalation to the site team.\n"
                     "- Lead cannulator; trains and signs off new starters.",
         "role2": "Staff Nurse | Salford Royal | Sep 2016 - Jan 2019",
         "bullets2": "- Acute medical admissions and step-down from critical care.",
         "projects": "- Led a unit audit on sepsis recognition; compliance rose from 61 to\n"
                     "  94 percent over two cycles.",
         "education": "BSc (Hons) Adult Nursing, University of Manchester, 2016.",
         "certs": "NMC registration current, revalidated 2024. Immediate Life Support (2023)."}},

  {"name": ("Yuki", "Haddad"), "fit": "strong",
   "scenario": "strong on paper, blocker only in activity",
   "signal": "She cannot work night shifts for the next twelve months - the resume says "
             "nothing about availability.",
   "notes": ["Screening follow-up. Yuki is genuinely strong on the clinical substance and "
             "I would put her in front of the ward manager on that basis. One material "
             "problem: night shift availability. She was clear this cannot change for at "
             "least twelve months for caring reasons, and raised it herself. Flagged "
             "because the posting treats the full rotating pattern as a firm requirement."],
   "calls": [("Intro call. Walked through the unit and the team. Yuki asked sensible "
              "questions about acuity and staffing. She then raised night shift "
              "availability directly and was straightforward that it is a blocker as the "
              "role is currently written.", "CALL_OUTGOING", 2)],
   "r": {"headline": "SENIOR STAFF NURSE", "location": "Manchester, United Kingdom",
         "summary": "Acute medicine nurse with 8 years post-registration experience, "
                    "including two as a deteriorating-patient link nurse.",
         "skills": "Structured A-E assessment, NEWS2 escalation, SBAR handover, IV therapy, "
                   "venepuncture, medicines reconciliation, pressure ulcer prevention",
         "role1": "Senior Staff Nurse | Wythenshawe Hospital | Mar 2020 - present",
         "bullets1": "- Reduced door-to-antibiotic time on the unit from a median of 78 to\n"
                     "  51 minutes as sepsis link nurse.\n"
                     "- Preceptor to six newly qualified colleagues, all completing on time.",
         "role2": "Staff Nurse | Stepping Hill Hospital | Aug 2018 - Feb 2020",
         "bullets2": "- General medical ward, 28 beds.",
         "projects": "- Redesigned the handover document now used across three wards.",
         "education": "BSc (Hons) Adult Nursing, University of Salford, 2018.",
         "certs": "NMC registration current. ALERT course completed 2023."}},
 ],

 "fpa": [
  {"name": ("Priya", "Raghunathan"), "fit": "moderate",
   "scenario": "resume and a newer note contradict",
   "signal": "The resume claims ownership of the close; a more recent note records that "
             "she assisted on it rather than owning it. The newer source should win.",
   "notes": ["Note from three weeks ago. Priya's CV lists ownership of the monthly close "
             "and she confirmed it on the first call.",
             "Note today. Correction to my earlier note. Priya clarified that she prepared "
             "schedules for the close at Kraft Heinz but the controller owned the cycle and "
             "the commentary. She has not run a close end to end. The CV overstates this. "
             "Everything else on it stands."],
   "calls": [],
   "r": {"headline": "FINANCIAL ANALYST", "location": "Chicago, United States",
         "summary": "Commercial finance analyst with 5 years in consumer goods. Strong on "
                    "variance analysis and reporting automation.",
         "skills": "Financial modelling, variance analysis, advanced Excel, SQL, Power BI, "
                   "budgeting and forecasting, board reporting",
         "role1": "Financial Analyst | Kraft Heinz, Chicago | Jun 2021 - present",
         "bullets1": "- Owns the monthly close variance pack for a $400m revenue division,\n"
                     "  including the written commentary.\n"
                     "- Automated the weekly flash report in SQL and Power BI, removing\n"
                     "  around six hours of manual work a week.",
         "role2": "Junior Analyst | Conagra Brands, Chicago | Jul 2019 - May 2021",
         "bullets2": "- Supported the annual budget cycle across three divisions.",
         "projects": "- Rebuilt the forecast on driver-based logic; revenue-line error fell\n"
                     "  from 8.2 to 3.1 percent.",
         "education": "BSc Accounting and Finance, University of Illinois, 2019.",
         "certs": "CIMA part-qualified (2023)."}},
 ],

 "frontend": [
  {"name": ("Amelia", "Frost"), "fit": "weak",
   "scenario": "under-qualified but genuinely upskilling",
   "signal": "Notes record a completed React course and a shipped side project in "
             "TypeScript, neither of which appears on the resume.",
   "notes": ["Call notes. Amelia is short of what this role needs on React and TypeScript - "
             "nothing in her two years at the agency touches either, and I said so "
             "directly. Worth recording what she has done about it: completed a React and "
             "TypeScript course at her own expense last month and shipped a small side "
             "project with it that she could talk through in detail, including why she "
             "chose the state management approach. Not at the level needed today, but the "
             "trajectory is real.",
             "Amelia confirmed she is Krakow based and comfortable with the two days a "
             "week in the office."],
   "calls": [],
   "r": {"headline": "JUNIOR WEB DEVELOPER", "location": "Krakow, Poland",
         "summary": "Web developer with 2 years building marketing sites and small "
                    "internal tools. Solid fundamentals, still growing into product work.",
         "skills": "JavaScript, HTML, CSS, jQuery, WordPress, Git, basic Node.js",
         "role1": "Junior Web Developer | Brainly, Krakow | Mar 2024 - present",
         "bullets1": "- Builds and maintains marketing pages and landing flows.\n"
                     "- Writes and maintains the team's Cypress smoke tests.",
         "role2": "",
         "bullets2": "",
         "projects": "- Side project: a small habit tracker, built to learn component "
                     "state.",
         "education": "B.Sc. Computer Science, AGH University of Science and Technology, 2024.",
         "certs": "None listed."}},
 ],

 "datasci": [
  {"name": ("Tobias", "Lindqvist"), "fit": "moderate",
   "scenario": "call log overrides the resume",
   "signal": "The resume shows no gradient boosting; a call log records a year of "
             "production XGBoost work and a shipped scorecard.",
   "notes": [],
   "calls": [("Screening call. Tobias is light on gradient boosting on paper and I raised "
              "it directly. In fact he has a year of production XGBoost: built the "
              "behavioural scorecard now used for limit decisions at Koho, wrote the "
              "validation pack, and walked me through the monotonic constraints he used "
              "to satisfy the risk committee. None of that is on his CV because the "
              "project sat under someone else's name.", "CALL_OUTGOING", 3),
             ("Follow-up. Tobias confirmed the band works and can start in 30 days. "
              "Toronto based, no relocation needed.", "CALL_OUTGOING", 1)],
   "r": {"headline": "DATA SCIENTIST", "location": "Toronto, Canada",
         "summary": "Data scientist with 6 years in financial services, mainly in "
                    "forecasting and customer analytics.",
         "skills": "Python, SQL, pandas, scikit-learn, statistics, Airflow, Tableau",
         "role1": "Data Scientist | Koho, Toronto | Sep 2021 - present",
         "bullets1": "- Builds forecasting and segmentation models for the growth team.\n"
                     "- Owns the feature pipeline in Airflow feeding the analytics layer.",
         "role2": "Analyst | Questrade, Toronto | Jan 2020 - Aug 2021",
         "bullets2": "- Customer behaviour analysis and reporting.",
         "projects": "- Internal library for cohort analysis used across the growth team.",
         "education": "M.Sc. Statistics, University of Waterloo, 2019.",
         "certs": "None listed."}},
 ],
}

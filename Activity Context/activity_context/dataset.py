#!/usr/bin/env python3
"""
Realistic evaluation dataset for Activity Context (PW-6968).

Five jobs across different domains and seniority levels, with hand-written candidates
against each. `dataset_more.py` carries the remaining three.

The resume is the only candidate evidence the scorer reads (plus, with this enhancement,
the latest 3 notes and 3 call logs), so everything a criterion needs has to be
demonstrated in the text with concrete evidence — not a keyword list.

Each candidate carries:
  name, fit, scenario   what this case is testing
  signal                what the activity reveals that the resume does not
  notes / calls         the activity itself; calls are (text, type, days_ago)
  r                     resume parts, rendered by build_resume()
"""

JOBS = [
 {"key": "backend", "title": "Senior Backend Engineer (Payments)", "domain": "tech",
  "city": "Berlin", "country": "Germany", "min_exp": 6, "max_exp": 12,
  "company": "Northwind Commerce",
  "jd": """Senior Backend Engineer - Payments Platform | Berlin (hybrid, 3 days on site)

ABOUT THE ROLE
Our payments platform settles around 40 million transactions a month for mid-market
retailers across the EU. You will own the settlement and reconciliation services end to
end: design, build, run, and the pager that comes with them.

RESPONSIBILITIES
- Design and ship event-driven services in Python that move money correctly, every time.
- Own PostgreSQL schema design, indexing and zero-downtime migrations past 500M rows.
- Build asynchronous pipelines on Kafka with exactly-once delivery semantics.
- Operate your services on Kubernetes (EKS): Helm charts, autoscaling, resource budgets.
- Take a one-in-five production on-call rotation and lead blameless postmortems.
- Mentor two to three mid-level engineers through design and code review.

REQUIRED SKILLS AND EXPERIENCE
- 6+ years of professional backend engineering with deep Python expertise.
- Production Kubernetes experience, not just local development.
- Strong relational database fundamentals: query plans, indexing, transaction isolation.
- Hands-on experience with an event streaming platform, specifically Kafka.

NICE TO HAVE
- Fintech or payments domain knowledge.
- German language.

SOFT SKILLS
- Writes clearly for other engineers and communicates design decisions effectively.
- Comfortable holding a position under challenge, and changing it when shown better data.

PRACTICAL
Berlin based, three days a week on site. Start within 90 days."""},

 {"key": "nurse", "title": "Registered Nurse - Acute Medical Unit", "domain": "clinical",
  "city": "Manchester", "country": "United Kingdom", "min_exp": 2, "max_exp": 10,
  "company": "Pennine Acute Trust",
  "jd": """Registered Nurse - Acute Medical Unit | Manchester

ABOUT THE ROLE
A 32-bed acute medical unit taking direct GP admissions and step-downs from A&E. The
unit turns over quickly and the nursing team carries real autonomy.

RESPONSIBILITIES
- Assess and prioritise newly admitted patients using a structured A-E approach.
- Recognise and escalate deterioration using NEWS2 with SBAR handover.
- Administer medicines including IV therapy under the trust policy.
- Take charge of the shift on a regular basis: allocation, bed flow, escalation.

REQUIRED SKILLS AND EXPERIENCE
- Current NMC registration with no restrictions.
- Minimum 2 years post-registration experience in acute medical or surgical nursing.
- Competence in structured A-E assessment and NEWS2 escalation with SBAR handover.
- Availability for a full rotating shift pattern, including nights and weekends.

NICE TO HAVE
- Cannulation and venepuncture sign-off.
- Mentorship or preceptorship experience.

SOFT SKILLS
- Calm and clear with distressed patients and families.
- Reliable handover, both written and spoken.

PRACTICAL
Manchester based. Full rotating shift pattern including nights is a firm requirement."""},

 {"key": "datasci", "title": "Data Scientist - Credit Risk", "domain": "tech",
  "city": "Toronto", "country": "Canada", "min_exp": 4, "max_exp": 9,
  "company": "Maple Ridge Financial",
  "jd": """Data Scientist - Credit Risk | Toronto

ABOUT THE ROLE
Build and validate the behavioural scorecards behind limit and pricing decisions for an
unsecured lending book.

RESPONSIBILITIES
- Build behavioural and application scorecards from first principles.
- Write the model validation pack that stands up to regulatory review.
- Monitor deployed models for drift and act on what you find.
- Explain model results to non-technical stakeholders and defend them.

REQUIRED SKILLS AND EXPERIENCE
- 4+ years applying statistical or machine learning models to business problems.
- Proficiency in Python, SQL, scikit-learn and gradient boosting.
- Experience building or validating credit risk models specifically.
- Regulated-domain exposure: model governance, documentation, audit trail.

NICE TO HAVE
- Exposure to explainability tooling (SHAP, counterfactuals).
- Experience with model deployment and monitoring.

SOFT SKILLS
- Explains model results to non-technical stakeholders clearly.
- Comfortable being challenged on methodology.

PRACTICAL
Toronto based, two days a week on site. Notice period matters — the model refresh
cycle starts in Q3."""},

 {"key": "fpa", "title": "Financial Analyst - FP&A", "domain": "finance",
  "city": "Chicago", "country": "United States", "min_exp": 3, "max_exp": 8,
  "company": "Great Lakes Foods",
  "jd": """Financial Analyst - FP&A | Chicago

ABOUT THE ROLE
Own the monthly close variance pack for a $400m revenue division and the driver-based
forecast behind it.

RESPONSIBILITIES
- Build and maintain three-statement financial models with driver-based revenue logic.
- Own the monthly close variance pack including the written commentary.
- Automate recurring reporting using SQL and Power BI.
- Run the annual budget cycle with divisional heads.

REQUIRED SKILLS AND EXPERIENCE
- 3+ years in FP&A or a comparable commercial finance role.
- Advanced Excel modelling — complex models, not just pivot tables.
- Proficient in SQL, including joins and window functions.
- Experience owning a close cycle rather than assisting on one.

NICE TO HAVE
- Part-qualified CIMA or ACCA.
- Power BI or Tableau.

SOFT SKILLS
- Explains variances to non-finance managers without jargon.
- Calm during close week.

PRACTICAL
Chicago based, two days a week on site. Visa sponsorship is not available."""},

 {"key": "frontend", "title": "Junior Frontend Developer", "domain": "tech",
  "city": "Krakow", "country": "Poland", "min_exp": 0, "max_exp": 2,
  "company": "Lumen Learning",
  "jd": """Junior Frontend Developer | Krakow (hybrid)

ABOUT THE ROLE
Join a small product team building the learner-facing side of an education platform.
You will be paired with a senior engineer from day one.

RESPONSIBILITIES
- Build UI features with React and TypeScript alongside a mentor.
- Write and maintain component tests.
- Take part in code review, as author and reviewer.
- Turn design files into accessible, responsive interfaces.

REQUIRED SKILLS AND EXPERIENCE
- 0-2 years of professional experience.
- Proficient in building UI features with React and TypeScript.
- Working knowledge of JavaScript.
- Working knowledge of CSS and responsive layout.

NICE TO HAVE
- Testing library experience (Jest, React Testing Library).
- An accessibility mindset.

SOFT SKILLS
- Asks for help early rather than stalling.
- Takes code review feedback without defensiveness.

PRACTICAL
Krakow based, two days a week in the office. Hybrid attendance is expected."""},
]


def tech_resume(c):
    r = c["r"]
    return (f"{c['name'][0]} {c['name'][1]}\n{r['headline']}\n{r['location']}"
            f"{chr(10) + r['email_note'] if r.get('email_note') else ''}\n\n"
            f"PROFESSIONAL SUMMARY\n{r['summary']}\n\n"
            f"CORE SKILLS\n{r['skills']}\n\n"
            f"EXPERIENCE\n\n{r['role1']}\n{r['bullets1']}\n\n"
            f"{r.get('role2','')}\n{r.get('bullets2','')}\n\n"
            f"SELECTED PROJECTS\n{r.get('projects','- None listed.')}\n\n"
            f"EDUCATION\n{r['education']}\n\n"
            f"CERTIFICATIONS\n{r.get('certs','None listed.')}")


def clinical_resume(c):
    r = c["r"]
    return (f"{c['name'][0]} {c['name'][1]}\n{r['headline']}\n{r['location']}\n\n"
            f"PROFESSIONAL SUMMARY\n{r['summary']}\n\n"
            f"CLINICAL SKILLS\n{r['skills']}\n\n"
            f"EXPERIENCE\n\n{r['role1']}\n{r['bullets1']}\n\n"
            f"{r.get('role2','')}\n{r.get('bullets2','')}\n\n"
            f"AUDIT, QUALITY IMPROVEMENT AND PROJECTS\n{r.get('projects','- None listed.')}\n\n"
            f"EDUCATION\n{r['education']}\n\n"
            f"REGISTRATION AND CPD\n{r.get('certs','Registration current.')}")


def finance_resume(c):
    r = c["r"]
    return (f"{c['name'][0]} {c['name'][1]}\n{r['headline']}\n{r['location']}\n\n"
            f"PROFESSIONAL SUMMARY\n{r['summary']}\n\n"
            f"CORE SKILLS\n{r['skills']}\n\n"
            f"EXPERIENCE\n\n{r['role1']}\n{r['bullets1']}\n\n"
            f"{r.get('role2','')}\n{r.get('bullets2','')}\n\n"
            f"SELECTED ANALYSES AND PROJECTS\n{r.get('projects','- None listed.')}\n\n"
            f"EDUCATION\n{r['education']}\n\n"
            f"CERTIFICATIONS\n{r.get('certs','None listed.')}")


BUILDERS = {"tech": tech_resume, "clinical": clinical_resume, "finance": finance_resume}


def build_resume(job, cand):
    """Render a candidate spec into the resume text the scorer will read."""
    return BUILDERS.get(job["domain"], tech_resume)(cand)


CANDIDATES = {
 "backend": [
  {"name": ("Marek", "Kowalski"), "fit": "strong", "scenario": "control - no activity",
   "signal": None, "notes": [], "calls": [],
   "r": {"headline": "SENIOR BACKEND ENGINEER", "location": "Berlin, Germany",
         "email_note": "Available at 30 days notice",
         "summary": "Backend engineer with 9 years building payment and order systems in "
                    "Python. Has taken two services from design through to production "
                    "ownership, including the pager.",
         "skills": "Python (9 yrs), PostgreSQL (8 yrs), Kafka (5 yrs), Kubernetes/EKS "
                   "(5 yrs), Terraform, AWS, Docker, Prometheus, Grafana, pytest",
         "role1": "Senior Backend Engineer | Zalando Payments, Berlin | Mar 2021 - present",
         "bullets1": "- Owns the settlement service: 12M events a day through Kafka with\n"
                     "  exactly-once semantics via the transactional producer.\n"
                     "- Redesigned the reconciliation schema; range-partitioned a 900M-row\n"
                     "  ledger table and cut month-end close from 6 hours to 40 minutes.\n"
                     "- Led the payouts monolith into six services on EKS.\n"
                     "- One-in-five pager rotation; wrote the postmortem template now used\n"
                     "  platform-wide.",
         "role2": "Backend Engineer | Delivery Hero, Berlin | Jul 2017 - Feb 2021",
         "bullets2": "- Built the internal pricing API, 3k req/s peak, consumed by four\n"
                     "  product teams.",
         "projects": "- Internal aiokafka wrapper adopted by five teams.\n"
                     "- Conference talk on idempotency at a Berlin meetup.",
         "education": "B.Sc. Computer Science, Technical University of Berlin, 2016.",
         "certs": "Certified Kubernetes Administrator (2022)."}},

  {"name": ("Sofia", "Almeida"), "fit": "strong",
   "scenario": "strong on paper, blocker only in activity",
   "signal": "She will only work fully remote and has refused the three days a week on "
             "site - the resume says nothing about working arrangements.",
   "notes": ["Screening follow-up. Sofia is technically excellent - walked through her "
             "Kafka exactly-once work in real depth. One significant issue: she will not "
             "do the three days a week in the office. She has worked fully remote since "
             "2020 and was firm that this cannot flex in the next twelve months. Flagged "
             "to the hiring manager because the posting treats it as a firm requirement."],
   "calls": [("Intro call. Covered the payments platform and the team. Sofia raised the "
              "working pattern herself before I did and was clear that fully remote is the "
              "only arrangement she will consider - she would withdraw rather than commit "
              "to three days on site.", "CALL_OUTGOING", 1)],
   "r": {"headline": "SENIOR BACKEND ENGINEER", "location": "Berlin, Germany",
         "email_note": "Available at 60 days notice",
         "summary": "Backend engineer with 8 years in high-throughput distributed systems, "
                    "most recently payments infrastructure. Strong on event-driven design "
                    "and on operating services that cannot lose messages.",
         "skills": "Python (8 yrs), Kafka (6 yrs), PostgreSQL (7 yrs), Kubernetes (4 yrs), "
                   "Terraform, AWS, Go, Redis, OpenTelemetry",
         "role1": "Senior Software Engineer | Feedzai, Berlin (remote) | Jan 2020 - present",
         "bullets1": "- Owns transaction ingestion: 25M events a day, exactly-once delivery,\n"
                     "  200ms p99 because it sits in the authorisation path.\n"
                     "- Rebuilt consumer-group rebalancing to survive broker failover.\n"
                     "- Designed Postgres partitioning for a 1.4bn-row case store.",
         "role2": "Software Engineer | Talkdesk, Berlin (remote) | Aug 2017 - Dec 2019",
         "bullets2": "- Built call-event processing on Kafka Streams.",
         "projects": "- Outbox-pattern publishing library used by five teams.\n"
                     "- PyCon 2023 talk on idempotency in payment systems.",
         "education": "M.Sc. Computer Engineering, Technical University of Berlin, 2017.",
         "certs": "Confluent Certified Developer for Apache Kafka (2022)."}},

  {"name": ("Devon", "Marsh"), "fit": "moderate",
   "scenario": "activity reveals a strength the CV omits",
   "signal": "A technical screen recorded that he led the Terraform/EKS migration at "
             "Monzo - the resume lists Terraform only as a skill.",
   "notes": [],
   "calls": [("Technical screen. Much stronger on infrastructure than the CV suggests. "
              "Devon actually led the Terraform migration at Monzo: moved 40 services onto "
              "EKS over eight months, wrote the module library the whole org now uses, and "
              "owned the autoscaling policy. None of it is on his resume - he said he did "
              "not think it counted because his job title never changed.",
              "CALL_OUTGOING", 2)],
   "r": {"headline": "BACKEND ENGINEER", "location": "Berlin, Germany",
         "email_note": "Available at 30 days notice",
         "summary": "Backend engineer with 7 years in Python, mostly in regulated financial "
                    "services. Comfortable from API design to database performance.",
         "skills": "Python (7 yrs), PostgreSQL (6 yrs), Kafka (3 yrs), Docker, Kubernetes, "
                   "Terraform, AWS, pytest, GitHub Actions",
         "role1": "Backend Engineer | Monzo Bank, Berlin | Apr 2020 - present",
         "bullets1": "- Builds and maintains services in the payments domain.\n"
                     "- Owns Postgres-backed services including schema and indexing.",
         "role2": "Software Engineer | Sennder, Berlin | Jun 2018 - Mar 2020",
         "bullets2": "- Internal APIs for the logistics platform.",
         "projects": "- Internal library for retry semantics adopted by three teams.",
         "education": "B.Sc. Computer Science, University of Manchester, 2018.",
         "certs": "None listed."}},
 ],
}


def load_all():
    """Merge in the remaining jobs' candidates from dataset_more.py."""
    try:
        from dataset_more import CANDIDATES_MORE
    except ImportError:
        CANDIDATES_MORE = {}
    merged = dict(CANDIDATES)
    for k, v in CANDIDATES_MORE.items():
        merged.setdefault(k, []).extend(v)
    return JOBS, merged

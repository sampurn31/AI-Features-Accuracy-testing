# What we actually sent the LLM judge — 300-variation scale run

Reconstructed from the run's own checkpoint, not written from memory. The system prompt is
the rubric file the run loaded; the user message is rebuilt through the same code path
(`llm.judge_candidate_criteria`) with a real candidate from the run.

| | |
|---|---|
| Run | `20260723-full` · 300 variations · prod · 2026-07-23 |
| Judge model | `claude-sonnet-5` at effort `low`, `max_tokens=2000` |
| Called from | `run_sourcing_scale.py:114` → `llm.judge_candidate_criteria()` |
| System prompt | `prompts/sourcing_criterion_rubric.md` (52 lines) |
| Output | forced JSON via `output_config.format.json_schema` — the judge cannot reply in prose |
| Example shown | case `saas-ae-brief-099` · candidate **Bohdan Sitarskyi** · system matchScore 78 |

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
# AI Sourcing V3 — PER-CRITERION Scoring Accuracy Rubric (Global / db=2)

You are auditing how the system scored ONE sourced candidate against a set of weighted
criteria. The system produced, for EACH criterion, a `score` (0-100) and a `reasoning`
text. Your job is to verify EVERY criterion row independently, using ONLY the candidate
profile as evidence. You are the ground-truth check on whether the AI's per-criterion
scores and reasoning are honest.

You receive: the search query, the weighted criteria, the candidate profile (read the
FULL experience list before judging), and the system's matchAnalysis rows.

## For EACH criterion, return:

### `reasoning_verdict` — is the reasoning text factually correct?
- `correct` — every factual claim in the reasoning exists in the profile (role, company,
  skill, years, project), and the claims genuinely relate to this criterion.
- `partly` — the gist is right but at least one claim over-reaches or misattributes a
  detail (right skill, wrong employer; "led" when profile only shows "worked on").
- `wrong` — the reasoning invents facts not in the profile, or its claims do not support
  this criterion at all. **Scan the ENTIRE experience list before choosing `wrong` — a
  role you don't see at first may appear further down.**

### `judge_score` — YOUR OWN honest 0-100 score for this criterion
Based only on the profile evidence: 90-100 = explicit, strong, recent evidence;
70-89 = solid evidence with minor gaps; 40-69 = partial/indirect/inferred evidence;
10-39 = weak or tangential; 0-9 = no evidence at all.

### `score_verdict` — compare the SYSTEM's score with your judge_score
- `agree`    — within ±15 points of your score.
- `too_high` — system score exceeds yours by more than 15 (over-credited).
- `too_low`  — system score is more than 15 below yours (under-credited).

### `reasoning_note` — one concrete sentence citing the evidence
Name the profile fact that supports/refutes it (e.g. "Profile lists AWS in keySkills and
a 2020-2023 'Cloud Engineer' role at Acme — score 95 is justified"). A human reviewer
reads this to validate YOU; never leave it generic.

## Candidate level:
- `overall_sensible` — given the per-criterion evidence and weights, is the overall
  matchScore a fair weighted summary? `yes` / `borderline` / `no`.
  (The exact formula matchScore = Σ(score·weight)/Σweight is verified separately by code —
  judge only whether the number is a fair reflection of the evidence.)
- `summary` — 1-2 sentences on the biggest accuracy issue you found, or confirmation
  that scoring is faithful.

## Rules
- The profile is the ONLY evidence. Do not assume unstated skills, but DO credit
  reasonable inference the criterion invites (a "Senior Backend Engineer, 8 yrs" plainly
  satisfies "senior-level engineering experience" without those literal words).
- Judge each criterion independently; do not average across them.
- Return JSON only, matching the enforced schema, one entry per criterion IN THE SAME
  ORDER as the system's matchAnalysis rows.
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

Here is the complete message for this candidate, exactly as sent (7,653 characters):

````text
SEARCH QUERY:
We are hiring a SaaS Account Executive remote within Europe (CET ±2) for our HR tech team. You will own a full-cycle B2B SaaS sales motion from prospect to close and run discovery and tailored product demos, working closely with the wider team.

What we're looking for: Track record of quota attainment; Strong discovery and demo skills; Structured sales methodology (MEDDIC/SPIN); CRM discipline (Salesforce). Ideally 3-5 years of relevant experience. Outbound self-sourcing would be a bonus.

WEIGHTED CRITERIA (as configured):
- Full-cycle B2B SaaS closing experience (weight 9)
- Consistent quota attainment record (weight 9)
- Strong discovery and demo skills (weight 7)
- Salesforce/CRM discipline (weight 6)

CANDIDATE PROFILE (the ONLY evidence — read the FULL experience list):
{
  "position": "Head of Communications | Account Manager",
  "company": "Veltrix AI",
  "workexpyr": 6,
  "city": "Europe",
  "state": null,
  "country": null,
  "keySkills": [
    "B2B Sales",
    "Sales Methodology",
    "Discovery Skills",
    "Demo Skills",
    "CRM",
    "Salesforce",
    "Lead Generation",
    "Customer Onboarding",
    "AI Automations",
    "Account Management"
  ],
  "description": "Currently Head of Communications | Account Manager at Veltrix AI. Has 6 years of experience in B2B sales and account management, including a role as Account Executive at Ociety where I managed approximately 250 business accounts and established a complete proactive sales outreach pipeline. I have a strong background in full-cycle sales, quota attainment, and structured sales methodology. My expertise includes lead generation, customer onboarding, and implementing AI sales automation workflows. I am passionate about using data to achieve business goals and thrive in challenging tasks. My key skills include CRM management, Salesforce, discovery skills, demo skills, and outbound self-sourcing.",
  "education": [
    {
      "degree": "Master's degree, Political Science and Government",
      "title": "The National University of Ostroh Academy",
      "institute_logo_url": "https://media.licdn.com/dms/image/v2/C4D0BAQGzFoZ-dhPyRA/company-logo_100_100/company-logo_100_100/0/1630557040873?e=2147483647&v=beta&t=UCqAyegOeAOG92xI013GHDANwA4_scsrnBk5Fb3CGHo",
      "start_year": "2016",
      "end_year": "2021",
      "description": null,
      "graduationYear": 2021
    }
  ],
  "experience": [
    {
      "title": "Head of Communications | Account Manager",
      "company": "Veltrix AI",
      "start": "June 2024",
      "end": null,
      "is_current": true,
      "description": "Account Management:\n- Pre-Launch Sales Strategy Plan.\n- Sales Use Cases and ICP.\n- Competitor Research.\n- End to Start Account Management and Sales Funnels.\n- Establishing Lead Generation and Cold Outreach Flows.\n- Advising on Customer Success and Sales Product Touchpoints/Flows.\n- Implementing Sales Processes from Scratch.\n- Constant Sync with Marketing and Product Teams.\n- Implementing AI Sales Automation Workflows and AI Sales Agents for speed, accuracy and efficiency.\n- Full Decision-Making ",
      "location": null
    },
    {
      "title": "Business Development and Growth Manager",
      "company": "Stealth Mode",
      "start": "May 2024",
      "end": null,
      "is_current": true,
      "description": "Most Detailed List of Responsibilities, but not Limited to:\n- Full-on Business Development Plan and Strategy.\n- Outlining Growth Opportunities.\n- Complete Cycle of Lead Generation. Successfully generated various volumes of leads for various requests/use cases. For instance, from a hundred of highly niched hot leads to 45k+ of qualified cold leads, and everything in between.\n- Enriching Data at Scale with on-demand custom parameters.\n- Full Hands-on Experience of Developing Sales AI Agents and Au",
      "location": null
    },
    {
      "title": "Account Executive",
      "company": "Ociety",
      "start": "April 2023",
      "end": "June 2024",
      "is_current": false,
      "description": "- B2B Communication and Account Management (managing ~250 business accounts).\n- Establishing a Complete Proactive Sales Outreach Pipeline.\n- Full Cycle of Lead Generation and Prospecting: research, establishing target markers and ICPs, data set creation, data prep, data verification, data enrichment, data personalization, implementing data into sales campaigns and engaging with leads. Total amount of leads ~10k.\n- Lead Generation across multiple platforms and sources.\n- Cold Email and Social Med",
      "location": null
    },
    {
      "title": "Account Manager",
      "company": "Ociety",
      "start": "February 2022",
      "end": "April 2023",
      "is_current": false,
      "description": "- Full Cycle of B2B Customer Onboarding.\n- Development of Customer Journey and Onboarding Strategy.\n- Onboarding Sessions with B2B Clients.\n- Establishing Customer Success Pipeline.\n- Account Management of Existing Clients.\n- Setting up Goals/Metrics for Account Management and Business Development.\n- Offboarding Strategy.\n- Customer Education.\n- Re-engagement Campaigns.\n- Constant Proactive Communication with Clients, including Automated Flows.\n- Constant Communication with COO, CEO, PM, Sales, ",
      "location": null
    },
    {
      "title": "Customer Success Manager",
      "company": "Varyence",
      "start": "February 2022",
      "end": "April 2023",
      "is_current": false,
      "description": "B2B Customer Success Project.",
      "location": null
    },
    {
      "title": "Customer Success Manager",
      "company": "Crowdin",
      "start": "September 2020",
      "end": "February 2022",
      "is_current": false,
      "description": "- Full Customer Support across multiple channels (helped over 2500 customers with their questions, issues and goals), including Technical Support.\n- Customer Onboarding and Involvement with Sales Processes.\n- Demo Sessions with Potential Clients.\n- Troubleshooting Meetings with Clients.\n- Developing and Hosting Product Webinars.\n- Onboarding Interns with the Study Plan fully developed from scratch by me.\n- Managing Team Metrics and Performance.\n- Attending and Presenting at Industry Conferences.",
      "location": null
    }
  ]
}

SYSTEM'S PER-CRITERION OUTPUT (audit every row, in order; overall matchScore=78):
[
  {
    "criterion": "Full-cycle B2B SaaS closing experience",
    "weight": 9,
    "score": 75,
    "reasoning": "Candidate states 'strong background in full-cycle sales' and mentions 'full-cycle sales, quota attainment' in summary. Experience as Account Executive at Ociety details 'Full Cycle of Lead Generation and Prospecting'."
  },
  {
    "criterion": "Consistent quota attainment record",
    "weight": 9,
    "score": 70,
    "reasoning": "Candidate explicitly states 'quota attainment' in summary and mentions it as a key skill. However, specific details or numbers regarding attainment are not provided in the profile."
  },
  {
    "criterion": "Strong discovery and demo skills",
    "weight": 7,
    "score": 80,
    "reasoning": "Candidate lists 'discovery skills' and 'demo skills' as key skills and mentions 'Demo Sessio...' under Crowdin. Summary also highlights 'discovery and demo skills'."
  },
  {
    "criterion": "Salesforce/CRM discipline",
    "weight": 6,
    "score": 90,
    "reasoning": "Candidate lists 'CRM' and 'Salesforce' as key skills. Summary also explicitly mentions 'CRM management, Salesforce'."
  }
]

Audit per the rubric: one entry per system row, same order. Cite concrete profile evidence in every reasoning_note. Return JSON only.
````

---

## Part 3 — FORCED OUTPUT SCHEMA

Passed as `output_config.format = {"type": "json_schema", "schema": ...}`, so every verdict is
machine-readable and tabulates without parsing prose. One entry per criterion, plus an overall
call:

````json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "criteria": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "criterion": {
            "type": "string"
          },
          "reasoning_verdict": {
            "type": "string",
            "enum": [
              "correct",
              "partly",
              "wrong"
            ]
          },
          "reasoning_note": {
            "type": "string"
          },
          "judge_score": {
            "type": "integer"
          },
          "score_verdict": {
            "type": "string",
            "enum": [
              "agree",
              "too_high",
              "too_low"
            ]
          }
        },
        "required": [
          "criterion",
          "reasoning_verdict",
          "reasoning_note",
          "judge_score",
          "score_verdict"
        ]
      }
    },
    "overall_sensible": {
      "type": "string",
      "enum": [
        "yes",
        "borderline",
        "no"
      ]
    },
    "summary": {
      "type": "string"
    }
  },
  "required": [
    "criteria",
    "overall_sensible",
    "summary"
  ]
}
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
{
  "rank": 1,
  "page": 1,
  "cand_id": 1,
  "name": "Bohdan Sitarskyi",
  "position": "Head of Communications | Account Manager",
  "company": "Veltrix AI",
  "matchScore": 78,
  "crit_verdicts": [
    {
      "criterion": "Full-cycle B2B SaaS closing experience",
      "weight": 9,
      "ai_score": 75,
      "ai_reasoning": "Candidate states 'strong background in full-cycle sales' and mentions 'full-cycle sales, quota attainment' in summary. Experience as Account Executive at Ociety details 'Full Cycle of Lead Generation and Prospecting'.",
      "judge_score": 55,
      "gap": 20,
      "score_verdict": "too_high",
      "reasoning_verdict": "partly",
      "judge_note": "Profile shows sales/account roles (Ociety, Veltrix AI) but no explicit mention of 'SaaS' product or 'closing' deals; role is more account management/lead gen than full-cycle closing."
    },
    {
      "criterion": "Consistent quota attainment record",
      "weight": 9,
      "ai_score": 70,
      "ai_reasoning": "Candidate explicitly states 'quota attainment' in summary and mentions it as a key skill. However, specific details or numbers regarding attainment are not provided in the profile.",
      "judge_score": 25,
      "gap": 45,
      "score_verdict": "too_high",
      "reasoning_verdict": "wrong",
      "judge_note": "Only self-reported summary phrase 'quota attainment' with no quantified achievements, targets, or attainment percentages anywhere in experience list."
    },
    {
      "criterion": "Strong discovery and demo skills",
      "weight": 7,
      "ai_score": 80,
      "ai_reasoning": "Candidate lists 'discovery skills' and 'demo skills' as key skills and mentions 'Demo Sessio...' under Crowdin. Summary also highlights 'discovery and demo skills'.",
      "judge_score": 65,
      "gap": 15,
      "score_verdict": "agree",
      "reasoning_verdict": "partly",
      "judge_note": "Crowdin role explicitly mentions 'Demo Sessions with Potential Clients' and skills list includes discovery/demo skills, but no discovery skill evidence beyond keyword listing."
    },
    {
      "criterion": "Salesforce/CRM discipline",
      "weight": 6,
      "ai_score": 90,
      "ai_reasoning": "Candidate lists 'CRM' and 'Salesforce' as key skills. Summary also explicitly mentions 'CRM management, Salesforce'.",
      "judge_score": 60,
      "gap": 30,
      "score_verdict": "too_high",
      "reasoning_verdict": "partly",
      "judge_note": "Salesforce and CRM are listed as key skills but no experience bullet mentions actual Salesforce usage or CRM workflows in day-to-day role descriptions."
    }
  ],
  "reasoning_worst": "wrong",
  "score_agree_rate": 0.25,
  "avg_gap": 27.5,
  "overall_sensible": "no",
  "summary": "The system over-credited quota attainment and SaaS-specific closing experience based on generic self-reported summary phrases rather than concrete evidence in the experience descriptions; Salesforce usage is also unproven beyond a skill tag."
}
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

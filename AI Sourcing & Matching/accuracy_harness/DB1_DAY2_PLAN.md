# PW-6818 — "My Database" (db=1) AI Matching — Day-2 Accuracy Test PLAN + Data-Prep Design

**Owner:** Sampurn · **Date:** 2026-07-23 · **Status:** plan, ready to execute next
**Goal (verbatim intent):** do **NOT** verify retrieval / whether the right candidate was found.
Verify that the candidates that **DO** come back are **(a) SCORED correctly** (per-criterion score
+ reasoning + overall) and **(b) RELEVANT** to the search.
**Scale:** 10 jobs, ≤100 candidates/job, mix-and-match, **~a few hundred** candidates total (NOT 1000).

This plan synthesizes five research briefs: (A) Aries scored-search contract 1.3/1.5,
(B) config/criteria chain 1.1/1.2, (C) existing repo automation, (D) Neptune criteria-scoring
prompt + schema, (E) dataset model. Section numbers below map to the task's four required areas.

---

## 0. TL;DR verdicts (the definitive answers)

| Question | Answer | Source |
|---|---|---|
| Per-criterion: 0–100 score, or boolean `satisfied`? | **0–100 integer score** (steps of 5). There is **NO `satisfied` field anywhere** in db=1. | A (`matchAnalysis.criteria[].score` int 0–100), D (Neptune `s:int`, no boolean), C (repo asserts `matchScore`/`matchAnalysis` shape) |
| Overall = weighted-avg formula (like Global) or LLM-direct? | **Neptune SOURCE says formula** (`Σ(score·weight)/Σweight`, clamped/rounded, label from bands). **But a real Aries capture contradicts it** (see §1 drift). Treat overall-formula as **exploratory / record-don't-fail** for db=1. | D (formula in code), A (capture 76 vs formula-66) |
| Is scoring inline or a separate call? | **Inline in the SAME 1.3 response, populated ASYNCHRONOUSLY.** First call returns `matchScore:null / reasoningStatus:"pending"`; scores land seconds later. Client re-calls 1.3 (cached read) or polls 1.5 `overallStatus`. No separate scoring call from the client. | A, B |
| Can we run this off-prod (dev/ss7)? | **YES.** My Database scores the account's OWN candidate DB (not an external corpus), and we seed that DB ourselves via the public candidate-create API. Unlike Global (corpus only on prod), db=1 works on **ss7/dev**. Harness already defaults to **ss7**. | E, `config.py:38` |
| Does db=1 need a real `jobSlug`? | **YES.** `jobSlug` XOR `aiSearch`; `aiSearch` forces db=2. My-Database is always job-anchored; fake slug → 404. | A, B, C |

---

## 1. CONTRACT — exact db=1 endpoint chain to get scored candidates + field map + DRIFT

### 1.1 The chain (JOB → scored candidates)

All base URLs derive from env (`config.py`, default `RCRM_ENV=ss7`). Java framework equivalents in
`TestBase.java`: `baseURL` = `https://<env>api.recruitcrm.net/v1/`, `jobServiceURL` =
`https://<env>job.recruitcrm.net/v2/`, `ariesServiceURL` = `https://<env>-aries.recruitcrm.net/v2/`.

| Step | Method + path | Base | Body (verbatim fields) | Auth | Purpose |
|---|---|---|---|---|---|
| pre | `POST candidates` (+ `candidates/work-history/create`, `candidates/education-history/create`) | `baseURL` (v1 public) | `Candidate` POJO / JSON array | **account API key** (`getAccountApiKey`) | Seed the pool |
| pre | `POST companies` → `POST contacts` → `POST jobs` (`createJobWithJson`) | `baseURL` | company/contact/job JSON incl. rich `job_description_text` | account API key | Create the job the JD-driven criteria come from |
| **1.1** | `POST ai-matching/configuration` | `jobServiceURL` | `{jobSlug, databaseType:1}` (round 2+: add `jdText`, `answers[]`) | **Albatross tkn** (`getOwnerAlbatrossToken`) | JD → `questions[]` + weighted `criteria[]{id,text,weight}` + `filters{llmFilters,translatedFilters}`. **Stateless — nothing persisted.** |
| **1.2** | `POST ai-matching/configuration/criteria` **with `isSave:true`** | `jobServiceURL` | `{jobSlug, databaseType:1, criteria[], filters{}, questions[], isSave:true}` | Albatross tkn | **Persists** to `ai_matching_filter_t` (`filter_type_id=2`); deletes stale `ai_matching_llm_score_t`; returns row `data.id`. Precondition for 1.3. |
| **1.3** | `POST advanced-search/candidates/ai-matching/search/get` | `ariesServiceURL` | `{jobSlug, databaseType:1, isRefresh:false}` (+ optional top-level `filterSearchList`/`sortPriorityList`) | Albatross tkn | **The scored-candidate call.** Doris pool (min 15 / cap 100, off-limit + already-assigned excluded) → async Neptune `POST /ai-matching/llm-score/criteria` on stored `criteria_json`. Query params `page` (def 1), `size` (def 10, **@Max 100**). |
| **1.5** | `POST advanced-search/candidates/ai-matching/search/count/get` | `ariesServiceURL` | same deserializer as 1.3 | Albatross tkn | Run-level status: `totalCount`, `overallStatus`, `canRefresh`, `refreshAvailableAt`, `jdUpdated`. Poll while scoring in flight. |

Java constants (`AiMatchingResultsBaseTest.java`): `CONFIG_PATH`, `CRITERIA_PATH`, `SEARCH_PATH`,
`COUNT_PATH`; `DB_MY_DATABASE=1`. Python harness already has 1.1/1.2 (`rcrm_client.get_configuration`,
`rcrm_client.criteria_to_filters`) — **1.3/1.5 for db=1 are NOT yet in the Python harness** (the
existing `sourcing_client` is Global-SSE only). **NEW client needed** (see §3).

### 1.2 Response fields carrying score + per-criterion analysis + reasoning + overall (1.3)

`data` is a **plain array** of full candidate objects (~150 fields) with three appended fields.
**No `totalCount`/pagination metadata in the 1.3 body** — count comes from 1.5.

| jsonPath (raw Aries 1.3) | Type | Meaning |
|---|---|---|
| `data[].matchScore` | Integer, **nullable** | Per-candidate **overall** score 0–100. `null` while pending. |
| `data[].reasoningStatus` | String enum | `pending` / `success` / `fail` — per-candidate scoring state. |
| `data[].matchAnalysis` | Object, **nullable** | Per-criterion breakdown + overall reason + label. `null` while pending. |
| `data[].matchAnalysis.criteria[]` | Array | one entry per scoring criterion |
| `data[].matchAnalysis.criteria[].text` | String | criterion text |
| `data[].matchAnalysis.criteria[].weight` | Integer | criterion weight (1–10) |
| `data[].matchAnalysis.criteria[].score` | Integer 0–100 | **per-criterion score — NOT boolean** |
| `data[].matchAnalysis.criteria[].reason` | String | per-criterion reasoning (Neptune, ≤40 words) |
| `data[].matchAnalysis.overallReason` | String | overall summary reasoning |
| `data[].matchAnalysis.label` | String | match label — `Excellent/Strong/Good/Partial/Weak` |
| `meta.status` | Integer | 200 on success |

1.5: `data.totalCount` (int), `data.overallStatus` (`pending/in_progress/success/partial/fail`),
`data.canRefresh` (bool), `data.refreshAvailableAt` (ISO ts), `data.jdUpdated` (bool).
(Note: per-candidate progress counts `candidateDone/candidateTotal` were **removed** 2026-07-11.)

### 1.3 DRIFT / reconciliation (LLD vs repo vs Neptune) — FLAGS

The task asked to check for the same contract-vs-reality mismatch we found on Global. **There are three.**

**DRIFT #1 — overall score: formula (source) vs capture (reality). ⚠️ THE Global-style mismatch.**
- Neptune source (Brief D, `schemas.py:29-36`, `llm_criteria_scoring_service.py:204-217`): overall `o`
  = `Σ(score·weight)/Σweight`, `max(0,min(100,round()))`; label from bands (`≥85 Excellent, ≥70 Strong,
  ≥55 Good, ≥40 Partial, else Weak`). **The LLM does NOT emit the numeric overall** — Python computes it.
- Aries live capture (Brief A): criteria `85·w10` + `20·w4` → formula = `(850+80)/14 = 66.4 ≈ 66`
  (**"Good"** band), yet response returned `matchScore:76` + `label:"Strong"`.
- **Reconciliation:** the capture's `matchAnalysis` was likely **truncated** (only 2 of N criteria
  shown), OR Aries stores/derives `matchScore` differently from Neptune's `o` (recompute or LLM-direct).
  Cannot resolve on paper — mirrors the Global pattern exactly.
- **Decision:** `overall_score_consistent` (the weighted-avg formula check) runs as an **EXPLORATORY**
  check for db=1: **record Δ, never hard-fail**. The smoke run (§4) resolves it empirically by pulling
  the FULL criteria list for a candidate and recomputing. If matchScore == formula → promote to a
  hard check. If not → file a contract bug and keep it exploratory. (Global posture confirmed by memory.)

**DRIFT #2 — `satisfied` field is a Global-only artifact; it does NOT exist in db=1.**
- Global's `sourcing_client._slim_candidate` normalizes rows to `{criterionText, weight, satisfied,
  reasoning}` (note `satisfied`). db=1 (A + D + C) has **no boolean** — only `score` (0–100). The db=1
  normalizer must **not** synthesize `satisfied`; deterministic checks keyed on it must be dropped for db=1.

**DRIFT #3 — repo automation exercises the WRONG Neptune endpoint for the criteria path.**
- Repo `AiMatchingLlmScoreTest` hits `neptuneServiceURL + "ai-matching/llm-score"` — the **fixed
  6-dimension rubric** (`AI_SCORING_SYSTEM_PROMPT`, dims `{fn,sk,lv,tr,dm,lc}`, paths `data.results.<id>.data.<dim>.s/.r`, overall `.o/.or/.l`). Brief D confirms the My-Database criteria path is a **different**
  service: `POST /ai-matching/llm-score/criteria` (`LLMCriteriaScoringService`). The repo has **no direct
  coverage** of the criteria endpoint. **Implication for us: don't call Neptune directly — drive it
  through Aries 1.3** (which internally calls `/llm-score/criteria` on the stored weighted criteria).
  Note this repo gap in the handoff so Java automation can add a criteria-endpoint test later.

**Non-drift caveats (from LLD impl notes) to design around:**
- Job read-access ACL check in Aries is `TEMP-DISABLED-FOR-LOCAL-TEST` (must be restored before ship);
  only the `unified_ai_sourcing_matching` Flagsmith flag is enforced (403 if not enabled for the account).
- Doris pool-assembly SQL "not verified against a live Doris instance" — pool membership on ss7 is
  unproven; smoke will reveal. A local-only branch substitutes 15 random-scored candidates.
- Async: never assert scores on the first 1.3 call — poll (§3.4).

---

## 2. DATA PREP — dataset design, env, creation mechanics, idempotency, pool membership

### 2.1 Which ENV (confirmed non-prod)
- **Target: ss7** (harness default, `config.py:38`), dedicated **throwaway/team account**. Fallback: dev.
- **Why non-prod is safe here (and wasn't for Global):** the db=1 pool = the account's own candidate
  records, which we create via the public API. No external corpus needed → no prod writes, no prod credits.
- **Preconditions on the account:** (1) Flagsmith `unified_ai_sourcing_matching` **enabled** (else 1.3
  returns 403); (2) Albatross login works (`rcrm_auth.login`, `RCRM_LOGIN_EMAIL/PASSWORD`) → 30-day JWT;
  (3) account API key available for the public create endpoints. Seeding still spends **match credits** on
  the throwaway account and creates real records → always clean up (§2.5).

### 2.2 The 10 job archetypes (reuse `scale_archetypes.py` where possible)

| # | Job | Family | Top weighted criteria (weight) |
|---|---|---|---|
| J1 | Backend Engineer, Python | software | Python & API dev (9); Cloud (8); Relational DB (7); Microservices (6); CI/CD (5) |
| J2 | Data Scientist | software | ML dev (9); Python data stack (8); Stats/A-B (7); Advanced SQL (6); Model deploy (5) |
| J3 | ICU Registered Nurse | healthcare | RN license + ICU (10); Critical-care/ventilator (8); ACLS/BLS (7); EMR (5); High-acuity ratio (4) |
| J4 | FP&A Analyst | finance | Financial modeling (9); Variance/budget (8); Excel+BI (7); ERP (5); Business partnering (4) |
| J5 | SaaS Account Executive | sales | Full-cycle closing (9); Quota track record (8); Prospecting (6); Salesforce (6); MEDDIC (5) |
| J6 | Supply Chain Analyst | operations | Demand planning (9); Inventory opt (8); ERP/SCM (7); Excel/SQL (6); Logistics/S&OP (5) |
| J7 | Product Manager | product | Discovery/roadmap (9); Data-driven prioritization (8); Cross-functional (7); UX research (6); Tech fluency (5) |
| J8 | Salesforce Developer | it | Apex & LWC (9); Config/Flows (8); Integrations (6); PD1 cert (6); SOQL/data model (5) |
| J9 | Civil Engineer | field | Structural design (9); AutoCAD/Civil 3D (8); PE/EIT (7); Site supervision (6); Codes/permitting (5) |
| J10 | Digital Marketing Manager | sales | Paid acquisition (9); SEO/content (7); Analytics GA4 (7); Marketing automation (6); Budget/ROAS (5) |

(Criteria are what the JD *should* elicit; actual 1.1 criteria are LLM-generated per run and may reword —
so ground truth is asserted at **band + relevance** granularity, not exact criterion strings; see §3.5.)

### 2.3 Per-job candidate mix (ground-truth labels) — ≤100/job, ~a few hundred total

| Bucket | Count/job | Tests | Expected overall band | Per-criterion expectation |
|---|---|---|---|---|
| **STRONG** | 5 | scoring ceiling + top rank | **≥ 75** (target 80–100) | every high-weight (≥7) criterion ≥ 70 |
| **PARTIAL** | 5 | graded scoring, one planted gap | **45–74** | the dropped criterion ≤ 40, others high |
| **WEAK** | 4 | confusable-neighbour discrimination | **20–44** | only tangential/low-weight criteria hit |
| **IRRELEVANT** | 8 | **relevance guard** | **≤ 20** (target <15) | near-zero all criteria; never in top-K |
| **INFERRED** | 3 | semantic inference vs keyword | **≥ 65** | scores strong despite headline keyword omitted |
| **Evaluated pool/job** | **25** | — | — | 17 authored + 8 borrowed |

**Reuse to stay under a few hundred:** IRRELEVANT for job X = STRONG candidates of *other* jobs (a civil
engineer is irrelevant to a data-scientist search) → **0 new records** for irrelevant when in one shared
account. Several WEAK "confusable neighbours" double as another job's STRONG (Data Engineer, Staff
Accountant, SDR, Salesforce Admin, Construction PM) — author once, reuse. Per-job confusable-neighbour
WEAK list is in Brief E §3b (Data Engineer for J2, Staff Accountant for J4, SDR for J5, SF Admin for J8, …).

**Candidate ledger (unique new records):** ~130 dedicated (STRONG+PARTIAL+INFERRED) + ~25 net-new WEAK +
0 borrowed IRRELEVANT + ~15 universal distractors ≈ **~170 unique candidates**, **250 evaluated (job,
candidate) pairs**. Comfortably "a few hundred," under 1000, each pool far under the 100 cap.

### 2.4 Creation mechanics (exact endpoints + fields + the levers)

Per candidate: `POST {baseURL}candidates` (auth = account API key, body = `Candidate` POJO / JSON) →
capture `slug` → `POST {baseURL}candidates/work-history/create` (JSON **array** of `WorkHistory`) →
optional `POST {baseURL}candidates/education-history/create`. Java helpers exist
(`commanFunction.createNewCandidateWithMandatoryFields` L199, `createNewCandidateWithAllFields` L254);
the **Python harness has none yet → build a public-API create client** (§3.1).

**Fields that feed the matcher** (`Candidate.java`) and how to use them as levers:

| Field | Role | Lever |
|---|---|---|
| `position`/`title` | primary job-title relevance anchor | exact title for STRONG; omit headline keyword for INFERRED |
| `skill` (comma string) | explicit skills list | list all core skills for STRONG; omit the literal keyword for INFERRED |
| `specialization` | secondary domain signal | |
| `candidate_summary` | **richest free-text semantic field** | recite domain (STRONG) or plant hidden signal (INFERRED) |
| `work_ex_year`, `relevant_experience` | seniority | senior (8–15) STRONG; junior (1–2) for one PARTIAL/WEAK |
| WorkHistory `work_description` (free text) | role narrative — **best place for inferred signal** | put the criterion evidence here without the keyword |
| `city/state/country` | location criteria | |
| `qualification_id`, `educational_specialization` | education/domain | |

**The inference lever (the bug INFERRED catches):** put the signal ONLY in `work_description` /
`candidate_summary` and **omit the headline keyword** from `skill` and `title` (e.g. no literal
"Python", but `work_description` = "built and shipped Django + FastAPI REST services on AWS ECS").
A correct semantic matcher scores this ≥65; a keyword matcher under-scores it → flagged.

**Job creation:** `createNewCompanyWithMandatoryFields` → `createNewContact_POST(companySlug)` →
`createJobWithJson` with a **rich `job_description_text`** (the JD is what 1.1 turns into criteria;
`tbljob.description` is never used — must be `jdtext`/`job_description_text`). Fields: `name`,
`company_slug`, `contact_slug`, `number_of_openings:1`, `job_type:4`, `job_description_text:<RICH_JD>`,
`enable_job_application_form:1`.

### 2.5 Idempotency + cleanup
- **Ledger-driven idempotency** (POSTs are not naturally idempotent): every seeded record's name/summary
  carries a run marker `[PW6818-db1-<runid>]`. Write every created `{type, slug/id, jobArchetype, label}`
  to `output/db1_ledger_<runid>.json` as you go (crash-safe, JSONL append — same pattern as the scale
  checkpoint). Re-running with an existing ledger **reuses** (skips creation); `--fresh` recreates;
  `--cleanup` deletes exactly what the ledger lists (candidates, jobs, contacts, companies) via the
  public `DELETE` endpoints. Never delete anything not in the ledger.
- Optional guard: before seeding, `count_candidates`/list by the marker to detect a prior partial run.

### 2.6 How to make a specific candidate land in a specific job's pool (the key mechanic)
The pool is **server-selected** by Doris hybrid retrieval (hard filters from `TRANSLATED_FILTERS` +
soft weighted boosts from `LLM_FILTERS` + vector similarity, RRF-fused; relaxation cascade if <15; cap
100; off-limit + already-assigned excluded). We do **not** control it directly — but two facts give us control:

1. **Cap is 100 and there's a min-15 relaxation cascade.** If an account's **total candidate count ≤ 100**,
   the entire account DB is guaranteed to enter every job's pool → **every seeded candidate is scored**,
   retrieval uncertainty eliminated. This is the lever.
2. Matching is **absolute fit, not pool-normalized** — a candidate's score is independent of who else is
   in the pool. So ground-truth bands are stable regardless of mix.

**Two seeding topologies (choose per rigor vs. cost):**
- **(Recommended, rigorous) Per-job account, ≤~90 candidates each.** Seed each job's own 25-candidate mix
  (incl. re-created irrelevants) into a dedicated ss7 account whose total DB ≤ 100. Guarantees ALL 25 —
  including IRRELEVANT — are retrieved and scored, so the **relevance-scoring guard is fully testable**
  (does an irrelevant that *did* come back get low-scored + judged irrelevant?). ~250 creates across 10
  accounts. Needs 10 credential sets (or reset one account between jobs, losing cross-job reuse).
- **(Fast, single-account) One account, ~170 candidates, 10 jobs.** Relevant buckets (STRONG/PARTIAL/
  WEAK/INFERRED = 17/job, all matching the job's filters) are reliably retrieved (17 < 100). IRRELEVANT
  (wrong-domain) may be **Doris-filtered pre-scoring** — acceptable given the goal (we don't verify
  retrieval); we just can't score those we never see. Cheapest, fewest credentials.

**Recommendation:** start single-account for smoke + the scoring/relevance-on-returned checks (matches the
user's "evaluate what comes back" goal); use per-job-account only if we decide the irrelevant-in-pool
relevance guard must be exhaustively exercised. Join every returned candidate back to its ground-truth
label by **candidate id/slug** (from the ledger) so mix and pool membership don't matter to the assertions.

---

## 3. HARNESS PLAN — reuse vs new, the TWO judge axes, deterministic checks, workbook

### 3.1 Reuse from the existing Global scale harness (`accuracy_harness/`)

| Reuse as-is | Why it carries over |
|---|---|
| `llm.judge_candidate_criteria(rubric, query, criteria, candidate)` | Per-criterion scoring-correctness judge — **exactly axis 1**. Reads `candidate.matchAnalysis[].{criterionText,weight,score,reasoning}` + `matchScore`; returns per-row `reasoning_verdict`, `judge_score`, `score_verdict` + `overall_sensible`. Works unchanged if db=1 candidates are normalized to that shape (§3.3). |
| `sourcing_checks.{ranking_monotonic, per_criterion_score_range, criteria_coverage, analysis_shape, label_not_contradictory, overall_score_consistent, weighted_avg, run_all}` | Deterministic checks read the same normalized shape. `overall_score_consistent` becomes **exploratory** (§1 DRIFT #1). |
| `scale_report.py` scaffolding (`_card, _header, _fill, _verdict_fill, _pct, _sheet_dashboard`, styles, `build()`, ILLEGAL_CHARACTERS scrub, BarChart) | One-workbook multi-sheet output shape — reuse the builders, add db=1 sheets. |
| Crash-safe JSONL checkpoint + `--rebuild`/`--limit`/`--yes` CLI ergonomics of `run_sourcing_scale.py` | Resume, pilot, offline rebuild. |
| `config.py` env plumbing (`RCRM_ENV=ss7`, job/aries/neptune URL derivation, `SOURCING_SCORE_TOL`, `MODEL=claude-sonnet-5`, `JUDGE_EFFORT`) | Same env + judge config. |
| `rcrm_auth.login()` (Albatross JWT), `rcrm_client.get_configuration` (1.1), `rcrm_client.criteria_to_filters` (1.2) | Config chain already implemented for job-service. |
| `scale_archetypes.py` | Archetype library for the 10 jobs' JDs + candidate profiles. |

### 3.2 NEW for db=1

| New module | Responsibility |
|---|---|
| `rcrm_public_client.py` | Public-API create client (account API key): `create_company`, `create_contact`, `create_job` (rich JD), `create_candidate`, `add_work_history`, `add_education`, plus `delete_*` for cleanup. |
| `prep_db1_dataset.py` | Data-prep driver: read `scale_archetypes`, author the 10 jobs + ~170 candidates with ground-truth labels, write `output/db1_ledger_<runid>.json`. Flags `--fresh`, `--cleanup`, `--env`, `--topology single|per-job`. |
| `aries_matching_client.py` | db=1 Aries client: `matching_search(jobSlug, page, size)` (1.3), `matching_count(jobSlug)` (1.5), and `_normalize_candidate` (§3.3). **Async poll** helper `wait_for_scores` (§3.4). |
| `matching_checks.py` | db=1 deterministic + ground-truth checks (extends `sourcing_checks`): band-envelope, precision@K, label-ordering (Spearman), inference-band; wraps `run_all` with `overall_score_consistent` demoted to exploratory. |
| `llm.judge_candidate_relevance(...)` | **Axis 2** — NEW relevance judge (§3.5). Add to `llm.py`. |
| `run_matching_scale.py` | db=1 analog of `run_sourcing_scale.py`: per job → 1.1→1.2(isSave)→1.3→poll→1.5, normalize, run both judges + deterministic checks, checkpoint, build workbook. |
| `matching_report.py` | db=1 workbook (reuses `scale_report` scaffolding; new sheets §3.6). |
| `prompts/db1_scoring_rubric.md`, `prompts/db1_relevance_rubric.md` | Judge rubrics. |

### 3.3 Normalizer — map db=1 Aries → the internal candidate shape the judges/checks expect

The judges and `sourcing_checks` read `criterionText / weight / score / reasoning`, `matchScore`,
`matchLabel`. db=1 raw differs — `_normalize_candidate` maps:

```
matchScore                       -> matchScore            (int|None)
reasoningStatus                  -> reasoningStatus       (NEW gate: pending|success|fail)
matchAnalysis.label              -> matchLabel
matchAnalysis.overallReason      -> overallReason
matchAnalysis.criteria[].text    -> matchAnalysis[].criterionText
matchAnalysis.criteria[].weight  -> matchAnalysis[].weight
matchAnalysis.criteria[].score   -> matchAnalysis[].score
matchAnalysis.criteria[].reason  -> matchAnalysis[].reasoning
(no `satisfied` — do NOT synthesize; DRIFT #2)
+ join ground-truth label + jobArchetype by candidate id/slug from the ledger
+ candidateIndex = streamed rank (for ranking_monotonic)
```

### 3.4 Async handling (NEW — Global was synchronous SSE)
Per job after 1.2 isSave: call 1.3 page 1 (Scenario A → all `pending`). Then **poll 1.5 `overallStatus`**
until `success/partial/fail` (or timeout), re-calling 1.3 as a **cached read** to pick up populated
scores. Config: `DB1_POLL_INTERVAL=5s`, `DB1_POLL_MAX=180s`. Respect the **5-min refresh cooldown** — do
NOT send `isRefresh:true` within 300s (409); `refreshAvailableAt` says when. Only assert scores on
candidates with `reasoningStatus == success`; record `pending/fail` counts as a data-quality metric.

### 3.5 The TWO judge axes

**Axis 1 — Scoring correctness (REUSE `llm.judge_candidate_criteria`).** For each returned scored
candidate, the judge audits every `matchAnalysis` row against the FULL profile: `reasoning_verdict`
(correct/partly/wrong), `judge_score` (0–100, re-scored independently), `score_verdict` (agree/too_high/
too_low), plus `overall_sensible` (yes/borderline/no). Metric: reasoning-correct rate, score-agreement
rate (|system − judge| ≤ tol), overall-sensible rate — per job and overall.

**Axis 2 — Relevance verdict (NEW `judge_candidate_relevance`).** Distinct question: **is this candidate
genuinely relevant to this job/search at all?** Input = job JD + weighted criteria + full profile (NOT the
system's scores — independent). Structured output:

```json
{ "relevance_verdict": "relevant | borderline | irrelevant",
  "relevant_dimensions": ["..."],
  "disqualifiers": ["..."],
  "reasoning": "cite profile evidence for the verdict" }
```

Cross-check vs ground truth: STRONG/PARTIAL/INFERRED → expect `relevant`; WEAK → `borderline`;
IRRELEVANT → `irrelevant`. Metric: **relevance precision** = fraction of returned-and-scored candidates
the judge calls relevant; **top-K relevance** = judge-relevant rate among the top-K by `matchScore`
(target: 0 irrelevant in top-K). Disagreement between judge-relevance and system `matchScore` band is a
flagged finding (e.g., system scored an irrelevant candidate high → relevance-guard failure).

### 3.6 Deterministic + ground-truth checks (carry-over + new)

| Check | Source | Verdict |
|---|---|---|
| `ranking_monotonic` (matchScore non-increasing in rank order) | reuse | hard |
| `per_criterion_score_range` (each 0–100) | reuse | hard |
| `criteria_coverage` (every configured criterion present once, weights preserved, none invented) | reuse | hard |
| `analysis_shape` (criterionText / numeric score / non-empty reasoning present when scored) | reuse | hard |
| `label_not_contradictory` (label vs score band) | reuse | hard |
| `overall_score_consistent` (matchScore == weighted-avg formula) | reuse | **EXPLORATORY** (DRIFT #1 — record Δ, don't fail; smoke resolves) |
| **band_envelope** (candidate's matchScore in its ground-truth label band) | NEW | hard (per-candidate) |
| **label_ordering** (Spearman: STRONG>PARTIAL>WEAK>IRRELEVANT median matchScore) | NEW | hard (per-job) |
| **precision_at_k** (top-K by matchScore free of IRRELEVANT / judge-irrelevant) | NEW | hard (per-job) |
| **inference_band** (each INFERRED ≥ 65; if in WEAK band → keyword-matching defect) | NEW | hard (per-candidate, flagged) |
| **pending/fail rate** (reasoningStatus distribution) | NEW | data-quality metric |

### 3.7 One-workbook multi-sheet output (`matching_report.build`)
Single `output/db1_matching_scale_<runid>.xlsx`, sheets (reusing `scale_report` builders):
1. **Dashboard** — KPI cards (scoring-correct rate, relevance precision, band-envelope pass, top-K clean,
   formula-Δ distribution [exploratory], pending/fail rate), per-check pass rates, matchScore histogram.
2. **Jobs** — one row/job: archetype, #returned, #scored, band-ordering (Spearman), precision@K, per-axis rates.
3. **Candidates** — every returned candidate: id, ground-truth label, matchScore, band-pass, formula-Δ,
   reasoningStatus, relevance_verdict, scoring-judge summary.
4. **Scoring Judge** — per-criterion verdicts (reasoning_verdict, judge_score vs system score, note).
5. **Relevance Judge** — per-candidate relevance_verdict vs ground-truth label, disqualifiers, reasoning.
6. **Failures** — every hard-check failure only.
7. **Dataset / Cases** — the ledger (what we seeded: jobs, candidates, labels).

---

## 4. OPEN QUESTIONS / RISKS + BUILD ORDER

### 4.1 Open questions (resolve empirically in smoke)
1. **Overall = formula or not?** (DRIFT #1) Pull the FULL criteria list for ≥5 scored candidates,
   recompute `Σ(score·w)/Σw`, compare to `matchScore`. If equal → promote formula check to hard; if not
   → file contract bug, keep exploratory. **Highest-priority unknown.**
2. **Async latency:** how long from 1.2 isSave to `overallStatus:success` on ss7? Sets `DB1_POLL_MAX`.
3. **Pool membership on ss7:** does an account with ≤100 candidates really return all of them (min-15
   relaxation + cap-100)? Verify `totalCount` == seeded count on a small account.
4. **Is `matchAnalysis.criteria[]` ever truncated** in the 1.3 body vs the configured criteria count?
   (`criteria_coverage` will catch it.)
5. **Does ss7 Neptune have the criteria-scoring path (`/ai-matching/llm-score/criteria`) deployed**, or
   only the 6-dim path? If criteria path is prod-only, db=1 scoring content on ss7 may be stubbed/random
   (LLD notes a local random-15 branch) → may force smoke/scale to **dev or a scoring-enabled env**.

### 4.2 Risks
- **Flag not enabled** on the throwaway account → 1.3 403. Enable `unified_ai_sourcing_matching` first.
- **Doris SQL unverified live** (LLD) → pool may misbehave on ss7; smoke reveals. If pool is random-15,
  scoring accuracy is untestable there → escalate for a scoring-enabled env.
- **LLM-generated criteria vary per run** → don't assert exact criterion strings; assert band + relevance
  (robust). Ground truth is per (job, candidate) band, keyed by id via the ledger.
- **Credits + real records** on the seeded account → always `--cleanup` from the ledger; use throwaway.
- **5-min refresh cooldown** → don't loop `isRefresh:true`; poll cached reads + 1.5 instead (409 risk).
- **Job read-access ACL disabled** in Aries (TEMP) → a 403-no-access path isn't testable now; don't add
  a test that depends on it.
- **Repo Neptune gap** (DRIFT #3) → note for follow-up Java coverage of `/llm-score/criteria`.

### 4.3 Recommended BUILD ORDER
1. **Data-prep client + script first.** Build `rcrm_public_client.py` + `prep_db1_dataset.py`; seed **ONE
   job (J1 Backend Python), ~15–20 candidates** on ss7 (single-account topology); write the ledger;
   confirm creation + `--cleanup` round-trips. (No matching yet.)
2. **Smoke (1 job, full chain).** Build `aries_matching_client.py` (1.3/1.5 + normalizer + async poll)
   and run 1.1→1.2(isSave)→1.3→poll→1.5 against J1. **Resolve open questions #1–#4** on real data. Wire
   `llm.judge_candidate_criteria` (axis 1) + the new `judge_candidate_relevance` (axis 2) on the ≤20
   candidates; hand-verify a handful of verdicts. Confirm the formula posture (hard vs exploratory).
3. **Scale (all 10 jobs).** Seed the full ~170-candidate dataset (single-account, or per-job-account if
   the irrelevant-in-pool relevance guard must be exhaustive), run `run_matching_scale.py` across all 10
   jobs with checkpoint/resume, both judge axes + deterministic checks, emit the one multi-sheet workbook.
   Sample-judge to bound credits/tokens (`JUDGE_K` per job) like the Global run; `--cleanup` at the end.

**Commit per phase** (per standing preference): prep client+script → smoke → scale, each committed before
the next.

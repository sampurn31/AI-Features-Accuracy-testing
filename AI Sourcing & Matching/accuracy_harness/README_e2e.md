# End-to-end accuracy suite — query in, candidates out

Tests the **whole chain a user actually walks**, not one endpoint at a time:

```
query ─▶ clarifying questions ─▶ answers ─▶ criteria ─▶ filters ─▶ criteria edits ─▶ search ─▶ results
        S2 materiality        S3 influence  S1/S2   S4 correctness  S5 delta       S6 what   S7 relevance
                                                                                   retrieval  S8 scoring
                                                                                   really ran S9 determinism
```

Every hop is recorded and asserted. Deterministic checks are objective (a hit is a real
defect); LLM adjudication covers the calls that need taste, and always ships its reasoning
into the workbook so a human can validate it before filing.

## Run it

```bash
source set_env.sh                       # your ANTHROPIC_API_KEY
RCRM_ENV=dev python3 run_e2e.py --run-id e2e1 --limit 4      # pilot, ~5 min
RCRM_ENV=dev python3 run_e2e.py --run-id e2e1                # full matrix (resumes)
python3 run_e2e.py --run-id e2e1 --rebuild                   # rebuild workbook offline
```

Useful flags and env vars:

| Flag / var | Effect |
|---|---|
| `--case <id>` / `--family <name>` | run one case, or one filter dimension (`location`, `skills`, `exclusions`, …) |
| `--fresh` | ignore the checkpoint and start over (the old one is renamed, not deleted) |
| `--workers N` | parallel cases (default 4; each case is ~8–14 API calls) |
| `E2E_JUDGE=0` | deterministic checks only — free and fast, good for a regression gate |
| `E2E_PAGES=N` | scored pages pulled per case (default 2 = 50 candidates; 4 = the full scored set) |
| `E2E_JUDGE_K=N` | candidates adjudicated per case (default 3 from the top, plus the tail) |

Runs are **crash-safe**: every finished case appends to `output/e2e_<run-id>.jsonl`, and
re-running the same command skips what is already done.

## What each stage asserts

**S1 config** — HTTP and state match the case's contract; criteria exist; the documented
caps hold (≤10 criteria, ≤150 chars each). Safety cases assert the gate *rejects*.

**S2 questions** — are the clarifying questions material? Flags questions whose answer the
query already gives (`REDUNDANT-QUESTION`), questions that cannot change retrieval, over-asking,
and genuinely load-bearing ambiguities that were *not* asked about.

**S3 answers** — the part nobody had tested end to end. Three probes:
- skip every question → does the run stall in `AWAITING_ANSWERS` (`SKIP-STALLS`)?
- answer every question → did criteria or filters change at all (`ANSWER-NO-EFFECT`), did it
  still re-ask (`RE-ASKS`), and did the answer land in the *right* field (`ANSWER-MISPLACED`)?
- feed the same `{question, answer}` objects the config endpoint produced to the search
  endpoint → does retrieval even accept them (`ANSWER-CONTRACT`)?

**S4 criteria → filters** — the "final query" the UI shows and lets you edit, asserted per
filter type against the case's `expect.filters` block: value present, value absent, right
bucket (`must_have` vs `good_to_have`), right field, exclusions in `must_not` rather than a
`should` clause that silently *boosts* instead of excluding. Also `SILENT-DROP`: a constraint
the user typed that no filter field expresses and nothing signalled.

**S5 mutations** — the questions "what if I change a criterion?" answered mechanically. For
each case the suite removes the top-weighted criterion, drops its weight below the must-have
threshold, and raises a low-weighted one above it, re-running filter generation each time:
- `REMOVAL-IGNORED` — the criterion's own values survived its deletion
- `COLLATERAL-LOSS` — deleting one criterion dropped values belonging to criteria that stayed
- `WEIGHT-NO-EFFECT` — weight crossed the must-have threshold, bucket did not move
- `UNSTABLE-EDIT` — a local edit regenerated more than half the unrelated filter set

Token attribution only counts terms **owned by one criterion** and only reads structured
fields, so a skill two criteria both demand never produces a false positive.

**S6 search** — the same assertions re-run against `queryUsed`, which is what retrieval
*actually* executed. `STAGE-DIVERGENCE` fires when the filters the UI displayed and the
filters that ran disagree — i.e. the user is editing something that does not drive results.

**S7 results** — location/title/skill/company match rates against expectation, plus
`EXCLUSION-LEAK` (an excluded term present in returned profiles). Then per candidate:
should this person have appeared at all, and is the scoring reasoning grounded in their
profile (`FABRICATED-REASONING` when it cites employers or skills the profile lacks)?

**S8 scoring & pagination** — `matchScore` equals the weighted average of its per-criterion
scores, rank order is non-increasing, every supplied criterion is scored, and no person
appears twice across pages.

**S9 determinism** — safety-family queries run twice; a different gate decision on identical
input is a defect.

## The workbook

`output/e2e_report_<run-id>.xlsx`, eight sheets:

| Sheet | Use it for |
|---|---|
| **Dashboard** | pass rate, defects by type / stage / filter dimension — the "how bad is it" view |
| **Issues** | the work queue: every defect, worst first, each row naming the query that triggers it |
| **Cases** | one row per case end to end — green/amber/red at a glance |
| **Filters** | expected vs actual for every filter field, side by side across config / c2f / search |
| **Q&A** | questions asked, whether they were material, what answering them did |
| **Mutations** | each criterion edit: what changed, what should have, churn |
| **Candidates** | judged results: should they have appeared, is the reasoning grounded, score gap |
| **Evidence** | raw payloads per case so any row can be re-verified by hand |

Read **Issues** first. Deterministic codes are objective. Judge-derived codes
(`IRRELEVANT-RESULT`, `FIELD-CLASSIFICATION`, `OVER-CONSTRAINED`, `ANSWER-MISPLACED`) carry
their reasoning in the Q&A / Candidates / Mutations sheets — read it before filing a ticket.

## The case matrix

`golden/e2e_cases.json` — every case declares its family, prompt size, database, and a
declarative `expect` block. Families:

`size` (one word → over-limit) · `location` (city, city+country, ambiguous geo, multi-value,
state, country, remote, radius, relocation) · `skills` (single, multi, nice-to-have, compound,
skill-vs-place ambiguity, tools, soft skills, must-have markers) · `titles` (exact, seniority,
multiple, non-standard, current-only) · `companies` (current, past, multi, hiring-company trap,
descriptor trap) · `experience` (min, max, range, implicit, fresher) · `education` (degree,
"or related field", tier college, named university, graduation year) · `languages` ·
`industry` · `exclusions` · `unsupported` (salary, notice period, visa) · `compliance`
(gender, age) · `safety` (gibberish, injection, vague) · `robustness` (typos, French,
Portuguese, boolean-style) · `my_database` (db=1 equivalents).

### Adding a case

```json
{ "id": "loc-11-metro", "family": "location", "size": "short", "db": 2,
  "query": "software engineers in the Mumbai metropolitan region",
  "expect": {
    "filters": { "locations": { "includes": ["Mumbai"] } },
    "results": { "min_total": 1, "location_any": ["Mumbai"], "min_frac": 0.8 }
  },
  "unsupported": [],
  "note": "why this case exists" }
```

The DSL per filter field: `includes` / `excludes` / `empty` / `nonempty` / `equals` /
`min` / `max` / `bucket` (`must_not` for exclusions) / `country_scope`. Canonical field
names are listed in `e2e_checks.LIST_FIELDS` + `SCALAR_FIELDS`; the normalizer maps all
three product dialects (db=2 search snake_case, db=2 config Title Case, db=1 nested) onto
them, so one expectation works everywhere.

## Files

`golden/e2e_cases.json` (matrix) · `e2e_checks.py` (normalizer + deterministic checks +
mutation analysis) · `e2e_llm.py` (six judges) · `run_e2e.py` (pipeline) ·
`e2e_report.py` (workbook).

## Notes and limits

- **Runs on dev by default.** The dev corpus is India-heavy — US/UK queries legitimately
  return zero, which is why those cases are marked `zero_ok`. Point at prod only when you
  intend to spend credits.
- **db=1 cases stop after filter generation.** My-Database retrieval goes through Aries on a
  seeded pool; that needs the seeding suite in `DB1_DAY2_PLAN.md`, not this endpoint.
- **The corpus is not deterministic run to run.** Anchor conclusions on deterministic codes;
  treat judge tallies as trend, not truth.
- **Judge cost** ≈ 8 calls per case at Sonnet 5. `E2E_JUDGE=0` gives a free deterministic
  gate suitable for CI.

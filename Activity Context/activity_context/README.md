# Activity Context (PW-6968) — accuracy test assets

Does giving the AI Matching scorer a candidate's **Notes and Call Logs** make its
reasoning better? These assets answer that against two deployments over one shared
database — one that can see activity, one that cannot.

> **Superseded by `../activity_eval/`.** That project runs the same question against the
> account's *real* candidates instead of synthetic ones, and grades at criterion level.
> This folder is kept because the twin-pair design and the judge-as-adapter split are
> still the right ideas, and because the blocked-case record is a genuine finding.

---

## Run it

```bash
cp .env.example .env                        # fill in, gitignored

python3 activity_context_judge.py --self-test   # the adapter must be reliable first
python3 activity_context_seed.py --env dev      # 20 roles, broad realistic pool
python3 activity_context_edge_cases.py --env dev  # twin pairs, known answers
python3 runner.py --env ats9                    # score + judge + referee
python3 activity_context_report.py --manifest activity_context_edge_manifest.json \
                                   --results results_ats9.json
python3 cleanup.py --manifest activity_context_edge_manifest.json
```

For the smaller hand-written dataset instead:

```bash
python3 seed_dataset.py --env dev
python3 evaluate.py --a ats9 --b dev --repeats 3
```

---

## The two ideas worth keeping

**The judge is an adapter, not a referee.** It answers one narrow question about what the
reasoning *says* — "does this state the candidate has X? yes / no / not_stated" — and
quotes the span it used. `runner.py` then compares that against the expected value
recorded at seed time. A judge asked "is this good?" gives an opinion that drifts between
runs; a judge asked what the text says gives something you can assert on, and every
failure names a concrete expectation.

**Twin pairs isolate the variable.** Two candidates identical in resume, title, skills and
experience — one with activity, one without. Any scoring difference has exactly one
possible cause. That is what makes a single pair interpretable rather than anecdotal.

---

## Things that will bite you

**Two credentials per environment, not interchangeable.** The public API takes
`<ENV>_API_KEY`; Aries and the job service take a login token from
`albatross-{env}/v1/login`. Both travel as `Bearer`, so using the wrong one returns 401
with no useful message.

**The matching endpoint is not standalone.** `search/get` on an unconfigured job returns
HTTP 400 forever — polling never resolves it. The sequence is configure → save criteria
(`isSave: true`) → search, with polling. Saving also wipes stored scores, which is what
forces a genuine re-score on the second deployment.

**Retrieval and scoring read different things.** The pool filters on the structured fields
(`position`, `skill`, `work_ex_year`, city); only the scorer reads `candidate_summary`.
Seed a perfect resume and omit the structured fields and every job returns `totalCount: 0`
with `overallStatus: "fail"` — nothing is ever scored.

**Notes cannot be backdated.** `updated_on` is server-assigned, ignores every override, and
is the field activity recency reads. Ordering therefore comes from creation order, so
consecutive items are spaced 1.1s apart. `call_started_on` *can* be backdated but nothing
reads it.

**`call_type` must be `CALL_INCOMING` or `CALL_OUTGOING`.** Anything else is a 422 and the
call log is silently lost.

**The scorer is not deterministic.** Two runs of the same job, same candidate, same pinned
criteria differ by roughly **±11 points**. Measured on candidates with no activity at all,
so activity cannot explain it. Any effect smaller than that is not a finding — which is
why `evaluate.py --repeats` exists and why the report prints the floor.

---

## Blocked by design, and recorded

Two checks cannot be exercised through the public API, and they are written into the
manifest with a reason rather than dropped:

| Check | Why |
|---|---|
| `recency_window_cutoff` | `updated_on` is server-assigned and cannot be backdated |
| `recency_window_boundary` | same cause |

They appear on the **Open Items** sheet and are never counted in the pass rate. A coverage
gap that is written down is a known limitation; one that is silently omitted is a false
pass rate.

---

## Files

| File | What it is |
|---|---|
| `credentials.py` | `.env` loading, both credential types, per-env labels |
| `activity_context_roles.py` | 20 roles across 17 industries |
| `activity_context_seed.py` | broad pool: strong / medium / weak / mismatch per role |
| `activity_context_edge_cases.py` | twin pairs with known answers |
| `dataset.py`, `dataset_more.py` | the smaller hand-written dataset |
| `seed_dataset.py` | seeds that dataset |
| `activity_context_judge.py` | the adapter — narrow questions, quoted spans |
| `runner.py` | scores a deployment, then referees judge output against expectations |
| `evaluate.py` | two-deployment comparison, with the noise floor |
| `activity_context_report.py` | seven-sheet Excel |
| `cleanup.py` | deletes everything a manifest created, in dependency order |

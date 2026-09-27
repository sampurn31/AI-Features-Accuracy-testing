# AI Sourcing V3 — boolean search validation harness

Validates that the boolean keyword query a recruiter types is **actually applied to the
results**, not merely parsed. Built after include/exclude keywords shipped unapplied and
the existing suites did not catch it.

## Why the existing suites could not have caught it

The PW-6818 accuracy suite calls exactly one endpoint — `POST /v2/ai-matching/configuration`
— and grades the *filters it returns*. That is the natural-language → filters stage. The
filters were correct the whole time; what was broken was downstream, where the filter is
turned into an OpenSearch query and run. No assertion anywhere in that suite looks at a
returned candidate, so the failure was outside its universe rather than missed inside it.

Concretely: the recruiter's boolean query was placed in `bool.should`, which in OpenSearch
**boosts ranking without restricting results**. A query can be present, parsed perfectly,
visible in the response, and still not filter anything. Only looking at returned records
distinguishes `should` from `filter`.

## Layers

**Layer 1 — count algebra** (`/ai-sourcing-v3/search/count`, ~1s a call)

Relations between queries, so no golden result set is needed and nothing goes stale as the
index changes: inclusion-exclusion, complement, De Morgan, distributivity, associativity,
idempotence, commutativity, double negation, parenthesis sensitivity, precedence.

The load-bearing one is `|A AND NOT B| == |A| - |A AND B|`. Had it been running, the
original defect would have shown up as `|A AND NOT B| == |A|` — an exclusion that changed
nothing.

**Layer 2 — record validation** (`/ai-sourcing-v3/search`, ~12s a call)

- The generated DSL must carry the query in `bool.filter` or `bool.must`, never only in
  `bool.should`. This is a direct regression test for the shipped defect.
- `highlightTerms` must not contain a term the recruiter excluded, and must not contain
  punctuation artefacts.

### The oracle trap, recorded so nobody repeats it

The candidate `description` in the response is **generated per search**, not the indexed
text. Running the same query twice returns different prose for 67 of the same 100 ids. An
early version of this harness read it as if it were the indexed `summary` field and
reported 32 exclusion violations that were not real. `highlightTerms` — what the service
itself reports as matched — is the oracle instead. The indexed text is not exposed by this
endpoint, so record-level *inclusion* cannot be verified from the API alone.

## Setup

Needs Python 3.10+ and a Recruit CRM test account with AI Sourcing V3 on the env you test. The
harness is standard-library only; `openpyxl` is used just for the Excel report.

```bash
git clone https://github.com/kowshik-recruitcrm/AI-Features-Accuracy-testing.git
cd "AI-Features-Accuracy-testing/AI Sourcing & Matching/boolean_search"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AIS_EMAIL=you@example.com      # test account login — never written to disk by the harness
export AIS_PASSWORD='...'
export AIS_ENV=ai4                    # optional; default ai4
```

The env name picks the hosts: `https://albatross-<env>.recruitcrm.net/v1` (login) and
`https://<env>-neptune.recruitcrm.net/v1` (search). `.env.example` lists the same variables.

## Running

```bash
python3 run_checks.py                      # all five layers
python3 run_checks.py --env ai4            # a specific env
python3 run_checks.py --layer 1            # algebra only, ~30s (repeat --layer to combine)
python3 run_checks.py --limit 5            # cap the layer-2 record checks
python3 run_checks.py --xlsx report.xlsx   # choose the workbook name
```

The exit code is non-zero when any check fails. Output: `results.json` (every check with expected
vs actual) and `ai_sourcing_boolean_results.xlsx`. Neither is committed.

Known findings are listed in `triage.py`. They still run and are still measured, but a re-run
reports them as settled rather than as fresh failures.

One-off probes (`probe_*.py` are hard-coded to `ai4` and `which_parser.py` checks `ai4`, `ai5` and `dev`; edit the file to change them):

```bash
python3 probe_collision.py    # do terms containing and/or/not get split?
python3 probe_highlight.py    # highlightTerms for the query shapes that were broken
python3 which_parser.py       # which envs run the old parser vs the new query_string one
```

## Files

| file | purpose |
|---|---|
| `client.py` | sign-in, the two endpoints, NDJSON stream reader, candidate text |
| `booleval.py` | independent boolean parser/evaluator — the textbook reading, used as the oracle |
| `run_checks.py` | all five layers and the report |
| `collisions.py` | layer 5 — terms that contain an operator word |
| `triage.py` | decisions on findings already raised |
| `xlsx_report.py` | the Excel workbook |
| `probe_*.py`, `which_parser.py` | one-off diagnostics |

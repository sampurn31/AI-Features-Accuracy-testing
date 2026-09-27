# AI Edit candidate-data accuracy harness

Exercises the MOM **"Proposed Changes for AI Edit"** (PW-7788 / AL-10853) against a live
environment and returns an Excel workbook.

The MOM's three testable claims:

1. The edit LLM now receives the candidate record, and must use a **real value** from it when
   one exists.
2. When the requested field holds **no** value it must make **no change** and **must not**
   generate, guess or placeholder one.
3. That no-change case must show a **warning** — and is still **billed**.

## Prerequisites

- **Python 3.10+**
- **A Recruit CRM login on the env you test** (default `ai1`), on an account with **Resume Formatting
  Agent V2** enabled. Without the v2 flag the run stops at preflight.
- **Credits on that account.** A full run costs roughly 87 credits. You can run unfunded with
  `--allow-unfunded` (see "Gate order, and running unfunded" below).
- **Admin rights.** The harness changes the agent's admin settings, creates two candidates, and
  (in category I) toggles redaction and then restores it. Use a test account.

The env name picks the hosts: `https://albatross-<env>.recruitcrm.net/v1/` and
`https://<env>-neptune.recruitcrm.net/v1/`.

## Install

```bash
git clone https://github.com/kowshik-recruitcrm/AI-Features-Accuracy-testing.git
cd "AI-Features-Accuracy-testing/Resume Formatting Agent"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 selftest.py              # offline check of the verdict engine — no network, no credits
```

## Configure

Pass the login as flags, or export it once:

```bash
export RCRM_EMAIL=you@example.com
export RCRM_PASSWORD='...'       # if neither the flag nor the variable is set, you're prompted
```

The password is never written to the workbook, the checkpoint or the raw archive.

## Run it

```bash
python3 run_accuracy.py --env ai1 --dry-run         # preflight + fixture only: no edits, no credits
python3 run_accuracy.py --env ai1 --limit 20        # smoke run
python3 run_accuracy.py --env ai1 --db test         # full run (166 scenarios)
python3 run_accuracy.py --env ai1 --resume          # continue a run that stopped
```

`--db` is only recorded in the report; the API picks the database from `--env`.

Output:
- `reports/ai-edit-accuracy-<env>-<timestamp>.xlsx` — the workbook
- `reports/checkpoint-<env>.jsonl` — what `--resume` reads
- `raw/raw-<env>-<timestamp>.jsonl` — every response body, for re-scoring

`reports/` and `raw/` are gitignored.

A full run is 166 scenarios, roughly **87 credits** (0.5 per processed edit; refusals are free) and
about **45–70 minutes**, because every scenario is a real LLM call.

### Re-score against the PRD (no API calls)

```bash
python3 rescore_prd.py raw/raw-ai1-<timestamp>.jsonl    # → reports/PRD-VALIDATION-<stamp>.xlsx
```

It re-applies the PW-7788 PRD contract to the archived responses. See its docstring for how the PRD
differs from the MOM.

### Billing check, measured from the balance

```bash
python3 verify_billing.py --env ai1       # uses RCRM_EMAIL / RCRM_PASSWORD, or --email / --password
```

About 1.5 credits per case. It brackets each refusal between two applied edits to check whether it
was billed and whether it counted toward the 15-edit cap.

### Useful flags

| Flag | Why |
|---|---|
| `--dry-run` | Preflight and fixture only. No edits, no credits. Use it first. |
| `--only "B.,D.,G."` | Category prefixes — see the Summary sheet for the letters. |
| `--limit 20` | Smoke run. |
| `--resume` | Skip scenarios already in `reports/checkpoint-<env>.jsonl`. |
| `--eeo-state on\|off` | Declare the account's Compliance setting; the H rows are recorded against it. |
| `--skip-redaction` | Skip category I, which flips account-level redaction on and back. |
| `--skip-billing` | Skip the Billing sheet's probes (they cost ~8 edits). |
| `--allow-unfunded` | Run on an account with no credits. See "Running unfunded" below. |
| `--stop-on-402` | Stop at the first insufficient-credit response instead of continuing. |


## What it builds

The harness does not rely on whatever candidates happen to exist. It creates **two of its own**:

- **rich** — every field populated with distinctive values (`Meridian Systems`, `2447500`,
  a notice period of `45`). Nothing round, nothing that appears in template boilerplate, so
  finding one of these in the result means it came from the record.
- **sparse** — a name and an email and nothing else. This is the data-unavailable fixture.

Both are on the **Fixture** sheet, field by field, so a reader can check for themselves what
was and was not available.

It then sets the agent's admin settings (nine standard fields plus one custom field — the
settings validator caps the two at ten combined) and generates one resume per candidate.
Every scenario is replayed against the **pristine** generated document rather than the
previous scenario's output, so results are independent and a late failure is attributable.

## Reading the report

| Sheet | What it holds |
|---|---|
| **Summary** | Run metadata, verdict counts, defects per category |
| **Results** | Every scenario, 27 columns |
| **Defects** | Only the defect-class verdicts, grouped |
| **Fixture** | The exact record values used |
| **Billing** | The MOM's billing claims, each measured by bracketing |
| **Raw** | Response envelope and the first 2000 characters of returned HTML |

### The verdicts

| Verdict | Meaning |
|---|---|
| `applied` | 200, the document changed, and where a field was named the record's value is in it |
| `warned correctly` | Unchanged, billed, and the data-unavailable warning was returned |
| `refused` | Non-200, as expected |
| **`SILENT NO-OP (no warning shown)`** | **The headline.** The no-data signal was raised, the document came back unchanged, and the response is a plain `"Edit applied"` with no warning field |
| **`FABRICATED value`** | A value appeared where the record holds none |
| **`applied but value not from record`** | The document changed but the record's value is not in the result |
| **`MARKER LEAKED into html`** | `RCRM_NO_CANDIDATE_DATA` reached `data.html` |
| **`CROSS-CANDIDATE data leak`** | Another candidate's value appeared |
| `unchanged with no no-data signal` | 200 and unchanged with no signal — should have been the unbilled 422 |
| `billed on a refusal` / `applied but not billed` | Billing disagreeing with the outcome |
| `recorded (not judged)` | Product decision pending; the row carries the facts |
| `skipped` | Not exercised — usually the account ran dry |

### Why the silent no-op is the headline

On the implementation branch a no-candidate-data no-op takes the **ordinary success path**:

```python
return respond(data, "Edit applied", message_types.IS_SUCCESS)   # views.py:226
```

There is no `warning` and no `warning_code` anywhere in that payload. The MOM asks for
"an appropriate warning to the user indicating that the requested data is not available",
and the scenario sheets expect `data.warning_code = "no_candidate_data"` plus the copy
*"The requested details aren't available on the candidate's record, so the resume was left
unchanged."* — that copy exists in the sheets and **not** in the branch.

So at the API level this case is indistinguishable from a real edit unless the caller diffs
the HTML itself. That is what this harness does, using neptune's own comparison —
`normalize_html_whitespace`, which strips *all* whitespace
(`prompt_injection.py:118,130`) — so its notion of "changed" matches the server's exactly.

## Gate order, and running unfunded

`/edit` runs its gates in this order (`views.py:148-180`):

1. v2 feature flag → 404
2. request shape, instruction length (15–500), prompt injection → 422
3. document ownership, **including the candidate slug** → 404
4. edit cap, 15 per session → 429
5. **credits** → 402
6. relevance judge → 422
7. candidate record read, then the edit

Two consequences worth knowing:

- **The credit gate sits before the relevance judge.** So on an unfunded account the length
  (M) and injection (L) categories still exercise real behaviour, and everything past that
  returns 402. `--allow-unfunded` is for exactly that; the run continues past a 402 rather
  than stopping, because the gates in front of it still produce real results.
- **The candidate slug is HMAC-bound to the document_id.** `generate-html` folds it into the
  signed id as a `slug_tag` segment and `/edit` compares it constant-time
  (`edit_session.is_issued_to`). Pairing one candidate's document with another's slug is a
  **404** before any record is read — which answers the open question in both scenario
  sheets ("Verify whether candidate_slug is cross-checked against the signed document_id").
  An empty-string slug normalises to absent (`_edit_inputs`), so it behaves like no slug.

## Self-test

```bash
python3 selftest.py
```

No network, no credits. Exercises the verdict engine against synthetic responses — including
the silent no-op and the fabrication case — and the value matcher. Worth running after any
edit to `classify()`; two real bugs in this harness were caught by it, one of which made
every one- and two-digit field pass regardless of the response.

## Known limits

- **EEO / Compliance is not toggled.** It is account-level, outside the agent settings, so
  category H is recorded against whatever you declare in `--eeo-state` rather than judged.
- **Redaction is toggled** (it lives in the agent settings), so category I runs as its own
  phase and restores the setting afterwards.
- **Custom-field values are best-effort.** The three "field content safety" scenarios depend
  on writing markup into a candidate custom field; if that does not stick they are recorded
  rather than judged.
- **Multi-part fields** (work history, education history, languages) are covered by
  instruction only — the harness does not create structured child rows, so a warning on
  those is expected rather than a defect.
- **`summary` is not on albatross's `STANDARD_FIELD_KEYS` allow-list**, so it may legitimately
  be absent from the candidate block. That scenario is recorded, not judged.
- One LLM call per scenario means results vary run to run. Treat a single `applied` as weaker
  evidence than a pattern across the category, and re-run a surprising row before filing it.

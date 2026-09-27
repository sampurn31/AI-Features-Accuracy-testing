"""
Turn an e2e checkpoint into a findings report: defects grouped by type, each with
the strongest concrete examples and a reproduction line.

    python3 e2e_findings.py --run-id full1              # markdown to stdout
    python3 e2e_findings.py --run-id full1 -o FINDINGS.md

Grouping is by defect CODE, because that is the unit a developer fixes — one code
is one bug, however many queries expose it.
"""
import argparse
import json
import os
from collections import defaultdict

import config

# Codes whose verdict is objective; everything else came from a judge and is labelled.
DETERMINISTIC = {
    "MISSING-VALUE", "LEAKED-VALUE", "WRONG-VALUE", "SHOULD-BE-EMPTY", "SHOULD-BE-SET",
    "WRONG-BUCKET", "NO-COUNTRY-SCOPE", "SILENT-DROP", "ZERO-OR-THIN", "TOO-BROAD",
    "WRONG-COUNTRY", "WRONG-LOCATION", "OFF-TITLE", "MISSING-SKILL", "WRONG-COMPANY",
    "EXCLUSION-LEAK", "RANK-DISORDER", "SCORE-FORMULA", "CRITERIA-COVERAGE",
    "DUPLICATE-PERSON", "REMOVAL-IGNORED", "COLLATERAL-LOSS", "WEIGHT-NO-EFFECT",
    "UNSTABLE-EDIT", "STAGE-DIVERGENCE", "ANSWER-NO-EFFECT", "ANSWER-CONTRACT",
    "RE-ASKS", "SKIP-STALLS", "NON-DETERMINISTIC", "GATE-MISS", "WRONG-STATUS",
    "CAP-BREACH", "NO-CRITERIA", "THIN-CRITERIA", "SEARCH-FAIL", "SSE-ERROR", "C2F-FAIL",
}

TITLES = {
    "MISSING-VALUE": "A requirement stated in the query never became a filter",
    "LEAKED-VALUE": "A value the query excluded appears in the filters anyway",
    "WRONG-VALUE": "A filter carries the wrong value",
    "WRONG-BUCKET": "Exclusions are built as boosts instead of exclusions",
    "SILENT-DROP": "The user's constraint is dropped with no signal",
    "ZERO-OR-THIN": "Query returns nothing (over-constrained extraction)",
    "WRONG-COUNTRY": "Results are in the wrong country (geo ambiguity)",
    "MISSING-SKILL": "Results do not have the skill that was searched for",
    "OFF-TITLE": "Results are not in the role that was searched for",
    "EXCLUSION-LEAK": "Excluded people are returned",
    "ANSWER-CONTRACT": "Clarifying answers cannot reach retrieval (contract mismatch)",
    "ANSWER-NO-EFFECT": "Answering the questions changes nothing",
    "ANSWER-MISPLACED": "The answer lands in the wrong field",
    "SKIP-STALLS": "Skipping the questions blocks the user",
    "RE-ASKS": "All questions answered, still asking",
    "REMOVAL-IGNORED": "Deleting a criterion does not remove its filters",
    "COLLATERAL-LOSS": "Deleting one criterion drops other criteria's filters",
    "WEIGHT-NO-EFFECT": "Changing a weight does not move the must-have bucket",
    "UNSTABLE-EDIT": "A local criterion edit regenerates unrelated filters",
    "STAGE-DIVERGENCE": "The filters shown are not the filters that ran",
    "SCORE-FORMULA": "matchScore is not the weighted average of its parts",
    "RANK-DISORDER": "Results are not ordered by score",
    "DUPLICATE-PERSON": "The same person appears more than once",
    "FABRICATED-REASONING": "Scoring cites employers or skills not in the profile",
    "IRRELEVANT-RESULT": "A candidate who should not be in the result set",
    "SCORE-DISAGREEMENT": "System score far above an independent re-score",
    "FIELD-CLASSIFICATION": "A value is in the wrong filter field",
    "OVER-CONSTRAINED": "The filter set is tight enough to starve the query",
    "CRITERIA-MISSING": "A stated requirement has no criterion",
    "CRITERIA-INVENTED": "A criterion the query never asked for",
    "CRITERIA-MISWEIGHTED": "A nice-to-have weighted as a hard requirement",
    "REDUNDANT-QUESTION": "Asks what the query already answered",
    "OVER-ASKING": "Too many questions for the query",
    "MISSED-AMBIGUITY": "A load-bearing ambiguity went unasked",
    "NON-DETERMINISTIC": "Identical input, different decision",
    "GATE-MISS": "Content-safety gate let something through it should reject",
    "MUTATION-WRONG": "The filter change after an edit is wrong",
    "CAP-BREACH": "A documented cap is exceeded",
    "SEARCH-FAIL": "Search returned an error status",
    "SSE-ERROR": "HTTP 200 whose stream carried an error",
}

SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def load(run_id):
    path = os.path.join(config.OUTPUT_DIR, f"e2e_{run_id}.jsonl")
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def build(rows, run_id):
    by_code = defaultdict(list)
    for r in rows:
        for f in r.get("fails") or []:
            by_code[f.get("code")].append((f, r))

    total_fails = sum(len(v) for v in by_code.values())
    high = sum(1 for v in by_code.values() for f, _ in v if f.get("severity") == "high")
    clean = [r for r in rows if not r.get("fails")]
    families = defaultdict(lambda: [0, 0])
    for r in rows:
        fam = families[r.get("family") or "?"]
        fam[0] += 1
        fam[1] += sum(1 for f in r.get("fails") or [] if f.get("severity") == "high")

    out = []
    w = out.append
    w(f"# End-to-end accuracy findings — run `{run_id}`\n")
    env = next((r.get("env") for r in rows if r.get("env")), config.RCRM_ENV)
    w(f"**Scope:** {len(rows)} cases, each driven through the full chain (query → clarifying "
      "questions → answers → criteria → filters → criteria edits → search → results). "
      f"Environment `{env}`.\n")
    w(f"**Result:** {total_fails} defects across {len(by_code)} distinct types "
      f"({high} high severity). {len(clean)}/{len(rows)} cases came through the entire chain "
      "with nothing to report.\n")

    w("\n## Defect types, worst first\n")
    w("| # | Defect | Severity | Cases hit | Evidence |")
    w("|---|---|---|---|---|")
    ranked = sorted(by_code.items(),
                    key=lambda kv: (min(SEV_ORDER.get(f.get("severity"), 3) for f, _ in kv[1]),
                                    -len(kv[1])))
    for i, (code, items) in enumerate(ranked, 1):
        sev = min((f.get("severity") for f, _ in items), key=lambda s: SEV_ORDER.get(s, 3))
        kind = "deterministic" if code in DETERMINISTIC else "adjudicated"
        cases = sorted({r["case_id"] for _, r in items})
        w(f"| {i} | **{TITLES.get(code, code)}** (`{code}`) | {sev} | {len(cases)} | {kind} |")

    w("\n---\n\n## Detail\n")
    for i, (code, items) in enumerate(ranked, 1):
        sev = min((f.get("severity") for f, _ in items), key=lambda s: SEV_ORDER.get(s, 3))
        cases = sorted({r["case_id"] for _, r in items})
        fams = sorted({r.get("family") for _, r in items if r.get("family")})
        w(f"### {i}. {TITLES.get(code, code)}  \n"
          f"`{code}` · **{sev}** · "
          f"{'objective check' if code in DETERMINISTIC else 'adjudicated, reasoning included'} · "
          f"{len(items)} occurrences across {len(cases)} cases · families: {', '.join(fams)}\n")
        seen = set()
        shown = 0
        for f, r in sorted(items, key=lambda x: SEV_ORDER.get(x[0].get("severity"), 3)):
            if r["case_id"] in seen or shown >= 4:
                continue
            seen.add(r["case_id"])
            shown += 1
            w(f"- **`{r['case_id']}`** — query: *\"{r['query'][:180]}\"*  \n"
              f"  {f.get('detail', '')[:600]}")
        if len(cases) > shown:
            w(f"- …and {len(cases) - shown} more case(s): {', '.join(cases[shown:])}")
        w("")

    w("\n## Coverage by filter dimension\n")
    w("| Family | Cases | High-severity defects |")
    w("|---|---|---|")
    for name, (nc, nh) in sorted(families.items(), key=lambda x: -x[1][1]):
        w(f"| {name} | {nc} | {nh} |")

    w("\n## Cases that passed the whole chain\n")
    w(", ".join(f"`{r['case_id']}`" for r in clean) if clean
      else "_None — every case surfaced at least one defect._")
    w("\n\n---\n_Generated by `e2e_findings.py` from the run checkpoint. "
      "Workbook with per-row evidence: `output/e2e_report_" + run_id + ".xlsx`._")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    md = build(load(a.run_id), a.run_id)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(md)
        print(f"wrote {a.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()

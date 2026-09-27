"""
Re-apply the deterministic checks to a finished run, without re-calling the API.

Every stage's raw request/response is in the checkpoint, so when a check is
corrected the run does not need repeating — only the verdicts do. Judge verdicts
are expensive and are carried through untouched.

    python3 e2e_rescore.py --run-id full1 --out full1r
    python3 run_e2e.py --run-id full1r --rebuild        # workbook from corrected verdicts
"""
import argparse
import json
import os

import config
import e2e_checks as CH

# Deterministic codes this script owns and therefore recomputes. Everything else
# (judge-derived, transport, contract) is copied through as-is.
OWNED = {"REMOVAL-IGNORED", "COLLATERAL-LOSS", "WEIGHT-NO-EFFECT", "UNSTABLE-EDIT"}


def rescore(rows):
    stats = {"before": 0, "after": 0}
    for r in rows:
        kept = [f for f in r.get("fails") or [] if f.get("code") not in OWNED]
        stats["before"] += len(r.get("fails") or []) - len(kept)
        base = CH.normalize_filters((r.get("stages", {}).get("c2f") or {}).get("llmFilters") or {})
        fresh = []
        for m in (r.get("stages", {}).get("mutations") or []):
            if m.get("http") != 200:
                continue
            mut = CH.normalize_filters(m.get("llmFilters") or {})
            crit = m.get("criteria") or []
            desc = m.get("desc") or ""
            if m.get("kind") == "remove_top":
                removed = desc.split("removed criterion ", 1)[-1].strip().strip("'\"")
                found = CH.check_removal(base, mut, removed, [c["text"] for c in crit], r.get("query", ""))
            else:
                # "lowered 'X' from weight N to 2" / "raised 'X' from weight N to 9"
                target = desc.split("'", 1)[-1].rsplit("'", 1)[0] if "'" in desc else ""
                weight = 2 if m.get("kind") == "lower_top_weight" else 9
                others = [c["text"] for c in crit if c.get("text") != target]
                found = CH.check_weight_move(base, mut, target, weight, others)
            ratio, added, dropped = CH.churn(
                base, mut, ignore_tokens=CH.distinctive_tokens(
                    desc.split("'", 1)[-1].rsplit("'", 1)[0] if "'" in desc else ""))
            m["churn"], m["added"], m["dropped"] = ratio, added[:12], dropped[:12]
            if ratio > 0.5:
                found.append({"code": "UNSTABLE-EDIT", "field": "mutation", "severity": "medium",
                              "stage": "mutation",
                              "detail": f"{desc}: {ratio:.0%} of unrelated filter values changed "
                                        f"(added {added[:6]}, dropped {dropped[:6]}) — a local edit "
                                        "regenerated the whole query"})
            m["fails"] = [f for f in found if f["code"] != "UNSTABLE-EDIT"]
            for f in found:
                fresh.append(dict(f, case_id=r.get("case_id"), mutation=m.get("kind")))
        r["fails"] = kept + fresh
        stats["after"] += len(fresh)
    return rows, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", required=True, help="run-id to write the corrected checkpoint as")
    a = ap.parse_args()
    src = os.path.join(config.OUTPUT_DIR, f"e2e_{a.run_id}.jsonl")
    dst = os.path.join(config.OUTPUT_DIR, f"e2e_{a.out}.jsonl")
    rows = [json.loads(l) for l in open(src) if l.strip()]
    rows, stats = rescore(rows)
    with open(dst, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"mutation verdicts recomputed: {stats['before']} -> {stats['after']}")
    print(f"corrected checkpoint -> {dst}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Discover each job's real candidate pool, and cache it.

The pool is the system's own judgement of which candidates are relevant to a job. Using it
means activity only ever gets written onto people who will actually be scored — activity
on someone who never pools is invisible.

Slow: the account holds ~10k candidates and the first run per job computes the pool from
scratch (~200s). It is cached, and re-running skips jobs already done.

    python3 pools.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from matching import Matching   # noqa: E402
import evaluate as E            # noqa: E402

CORPUS = os.path.join(HERE, "corpus.json")
POOLS = os.path.join(HERE, "pools.json")
ENV = "dev"


def main():
    jobs = json.load(open(CORPUS))["jobs"]
    out = json.load(open(POOLS)) if os.path.exists(POOLS) else {}
    m = Matching(ENV, verbose=False)
    for j in jobs:
        if j["slug"] in out:
            continue
        try:
            rows, crit, secs = m.run_job(j["slug"])
            out[j["slug"]] = {"name": j["name"], "n": len(rows), "secs": secs,
                              "criteria": [c.get("text") for c in crit],
                              "cands": [{"slug": E.row_slug(r), "name": E.row_name(r),
                                         "score": E.row_score(r)} for r in rows]}
            print(f"  {j['name'][:38]:<40} pool {len(rows):>3}  {secs:>7.1f}s", flush=True)
        except Exception as e:
            out[j["slug"]] = {"name": j["name"], "n": 0, "error": f"{type(e).__name__}: {e}"}
            print(f"  {j['name'][:38]:<40} FAILED {type(e).__name__}", flush=True)
        json.dump(out, open(POOLS, "w"), indent=1)
    tot = sum(v.get("n", 0) for v in out.values())
    uniq = {c["slug"] for v in out.values() for c in v.get("cands", [])}
    print(f"\ndone: {len(out)} jobs, {tot} candidate-slots, {len(uniq)} unique candidates")
    return 0


if __name__ == "__main__":
    sys.exit(main())

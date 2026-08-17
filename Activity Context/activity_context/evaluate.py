#!/usr/bin/env python3
"""
Does activity context meaningfully improve the AI's response quality?

Runs the same jobs and candidates through two deployments — one that can see notes and
call logs, one that cannot — and compares. Same database, same job, same pinned criteria;
the only intended difference is activity visibility.

WHY CRITERIA ARE PINNED
-----------------------
Criteria are generated from the JD by an LLM, so configuring twice gives two different
sets. Comparing those measures criteria drift, not activity context. We configure once and
save the same object to both sides. Saving also wipes stored scores, which is what forces
the second deployment to genuinely re-score rather than return a cache.

WHY REPEATS EXIST
-----------------
The scorer is not deterministic. Measured on candidates with NO activity at all, two runs
of the same job differ by about +/- 11 points (1 sd). Any effect smaller than that cannot
be distinguished from noise, so --repeats lets you establish the floor before believing a
difference.

    python3 evaluate.py --a ats9 --b dev
    python3 evaluate.py --a ats9 --b dev --repeats 3
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import credentials                       # noqa: E402
from credentials import env_label        # noqa: E402
from runner import Matching, reasoning_text   # noqa: E402
from activity_context_judge import Judge      # noqa: E402

MANIFEST = os.path.join(HERE, "eval_manifest.json")
OUT = os.path.join(HERE, "eval_results.json")


def activity_text(cand):
    """The activity as the judge should see it — notes then call logs, oldest first."""
    parts = []
    for n in cand.get("notes", []):
        parts.append(f"NOTE: {n}")
    for c in cand.get("calls", []):
        parts.append(f"CALL LOG: {c}")
    return "\n".join(parts)


def reasoning_of(row):
    return row.get("reasoning", "") if isinstance(row, dict) else ""


def judge_prompt(job_title, criteria, resume, activity, reasoning, signal):
    """Kept for reference — the adapter asks narrow questions instead of using this."""
    return (f"JOB: {job_title}\nCRITERIA: {', '.join(criteria)}\n\n"
            f"RESUME:\n{resume}\n\nACTIVITY:\n{activity}\n\n"
            f"REASONING:\n{reasoning}\n\nSIGNAL UNDER TEST: {signal}")


def run_env(env, manifest, repeats=1):
    """Score every job on one deployment and index the rows by candidate slug."""
    m = Matching(env)
    out = {}
    for job in manifest["jobs"]:
        try:
            runs, criteria = m.run_job(job["job_slug"], repeats)
        except Exception as e:
            print(f"  {env}/{job['key']}: FAILED {type(e).__name__}: {str(e)[:120]}")
            continue
        by_slug = {}
        for run in runs:
            for row in run["rows"]:
                slug = row.get("slug")
                if not slug:
                    continue
                by_slug.setdefault(slug, []).append({
                    "score": (row.get("matchScore") if row.get("matchScore") is not None
                              else (row.get("matchAnalysis") or {}).get("score")),
                    "reasoning": reasoning_text(row)})
        out[job["job_slug"]] = {"criteria": [c.get("text") for c in criteria],
                                "candidates": by_slug,
                                "seconds": [r["seconds"] for r in runs]}
        print(f"  {env_label(env):<26} {job['key']:<10} {len(by_slug)} scored")
    return out


def judge_all(judge, manifest, harvest, env):
    """
    For every candidate that carries a planted signal, ask whether the reasoning used it.

    One narrow question per candidate. The adapter reports; the caller decides.
    """
    rows = []
    for job in manifest["jobs"]:
        h = harvest.get(job["job_slug"], {})
        for cand in job["candidates"]:
            got = h.get("candidates", {}).get(cand["slug"])
            if not got:
                rows.append({"env": env, "job": job["key"], "candidate": cand["name"],
                             "scenario": cand.get("scenario", ""), "pooled": False})
                continue
            reasoning = got[0]["reasoning"]
            activity = activity_text(cand)
            signal = cand.get("signal")
            used = judge.fact_used(reasoning, signal, activity) if signal else None
            grounded = judge.grounded(reasoning, activity) if activity else None
            rows.append({
                "env": env, "job": job["key"], "candidate": cand["name"],
                "slug": cand["slug"], "scenario": cand.get("scenario", ""),
                "pooled": True, "has_activity": bool(activity),
                "score": got[0]["score"],
                "scores_all": [g["score"] for g in got],
                "signal": signal or "",
                "signal_used": used.answer if used else "n/a",
                "signal_quote": used.quote if used else "",
                "grounded": grounded.answer if grounded else "n/a",
                "reasoning": reasoning})
    return rows


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.fmean(xs), 2) if xs else None


def compare(rows_a, rows_b, a, b):
    """
    Side-by-side, plus the noise floor.

    The floor is measured on candidates with NO activity: activity cannot explain their
    movement, so whatever spread they show is the scorer's own variance. Any activity
    effect smaller than that is not a finding.
    """
    by_a = {r["slug"]: r for r in rows_a if r.get("pooled") and r.get("slug")}
    by_b = {r["slug"]: r for r in rows_b if r.get("pooled") and r.get("slug")}
    both = sorted(set(by_a) & set(by_b))

    used_a = sum(1 for s in both if by_a[s]["signal_used"] == "yes")
    used_b = sum(1 for s in both if by_b[s]["signal_used"] == "yes")
    deltas = [by_a[s]["score"] - by_b[s]["score"] for s in both
              if isinstance(by_a[s]["score"], (int, float))
              and isinstance(by_b[s]["score"], (int, float))]
    noise = [by_a[s]["score"] - by_b[s]["score"] for s in both
             if not by_a[s].get("has_activity")
             and isinstance(by_a[s]["score"], (int, float))
             and isinstance(by_b[s]["score"], (int, float))]

    out = {"compared": len(both),
           "signal_used": {a: used_a, b: used_b},
           "mean_score": {a: mean([by_a[s]["score"] for s in both]),
                          b: mean([by_b[s]["score"] for s in both])},
           "mean_delta": mean(deltas),
           "grounded_unsupported": {
               a: sum(1 for s in both if by_a[s]["grounded"] == "unsupported"),
               b: sum(1 for s in both if by_b[s]["grounded"] == "unsupported")}}
    if len(noise) > 1:
        sd = round(statistics.pstdev(noise), 1)
        out["noise_floor"] = {"n": len(noise), "mean": mean(noise), "sd": sd,
                              "note": "measured on candidates with NO activity — any "
                                      "effect smaller than this sd is not a finding"}
    return out


def main():
    p = argparse.ArgumentParser(description="Compare two deployments")
    p.add_argument("--a", default="ats9", help="the deployment WITH activity context")
    p.add_argument("--b", default="dev", help="the control, WITHOUT it")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--manifest", default=MANIFEST)
    p.add_argument("--out", default=OUT)
    args = p.parse_args()

    manifest = json.load(open(args.manifest))
    print(f"manifest {os.path.basename(args.manifest)}  {len(manifest['jobs'])} jobs  "
          f"repeats={args.repeats}")

    started = time.time()
    harvest_a = run_env(args.a, manifest, args.repeats)
    harvest_b = run_env(args.b, manifest, args.repeats)

    judge = Judge()
    rows_a = judge_all(judge, manifest, harvest_a, args.a)
    rows_b = judge_all(judge, manifest, harvest_b, args.b)
    summary = compare(rows_a, rows_b, args.a, args.b)

    json.dump({"created_at": datetime.now(timezone.utc).isoformat(),
               "a": args.a, "b": args.b, "labels": {args.a: env_label(args.a),
                                                    args.b: env_label(args.b)},
               "repeats": args.repeats, "summary": summary,
               "rows": {args.a: rows_a, args.b: rows_b},
               "judge_usage": judge.usage()}, open(args.out, "w"), indent=2)

    print("\n" + "=" * 66)
    print(f"compared {summary['compared']} candidates present on both")
    print(f"  signal used      {env_label(args.a)}: {summary['signal_used'][args.a]}"
          f"   {env_label(args.b)}: {summary['signal_used'][args.b]}"
          f"   <- the control should be 0")
    print(f"  mean score       {summary['mean_score']}")
    print(f"  mean delta       {summary['mean_delta']}")
    print(f"  unsupported      {summary['grounded_unsupported']}")
    if "noise_floor" in summary:
        nf = summary["noise_floor"]
        print(f"  NOISE FLOOR      +/-{nf['sd']} points (n={nf['n']}, no activity)")
        print(f"                   {nf['note']}")
    print(f"  elapsed          {(time.time() - started) / 60:.1f} min")
    print(f"  written to       {args.out}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Activity Context (PW-6968) — test runner.

Drives a full scoring run against one deployment and turns it into verdicts.

The split that makes this trustworthy:
  * the JUDGE only reports what the reasoning SAYS (see activity_context_judge.py)
  * `deterministic_checks` decides pass/fail, by comparing that report against the
    expected value recorded at seed time

So a failure is always traceable to a concrete expectation, never to an opinion.

THE MATCHING ENDPOINT IS NOT STANDALONE. Calling search/get on an unconfigured job returns
HTTP 400 forever — polling never resolves it. The sequence is configure -> save criteria
(isSave: true) -> search, with polling. Saving also wipes stored scores, which is what
forces a genuine re-score on the second deployment.

    python3 runner.py --env ats9
    python3 runner.py --env dev --repeats 2
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import credentials                                                    # noqa: E402
from credentials import (aries_base, albatross_token, env_label,      # noqa: E402
                         job_service_base, CONFIG_ENV)
from activity_context_judge import Judge                              # noqa: E402

EDGE_MANIFEST = os.path.join(HERE, "activity_context_edge_manifest.json")
MANIFEST = os.path.join(HERE, "activity_context_manifest.json")
TIMEOUT = 240
POLL_TIMEOUT = 420
POLL_STEPS = (1, 1, 2, 2, 3, 3, 5, 5, 8)


def log(m):
    print(m, flush=True)


def _interval(n):
    return POLL_STEPS[n] if n < len(POLL_STEPS) else 10


class Matching:
    """configure -> save -> poll. Auth is the LOGIN token, never the public API key."""

    def __init__(self, env, verbose=True):
        self.env = env
        self.label = env_label(env)
        self.verbose = verbose
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {albatross_token(env)}",
            "Content-Type": "application/json;charset=UTF-8",
            "accept": "application/json;charset=UTF-8"})
        self.job_ms = job_service_base(env)
        self.aries = aries_base(env)
        self.timings = {}

    def _log(self, m):
        if self.verbose:
            log(f"    [{self.label}] {m}")

    def _post(self, url, payload, what):
        t0 = time.time()
        r = self.s.post(url, json=payload, timeout=TIMEOUT)
        self.timings["last_call_s"] = round(time.time() - t0, 3)
        if r.status_code != 200:
            raise RuntimeError(f"{what} -> HTTP {r.status_code}: {r.text[:250]}")
        return r.json()

    def configure(self, job_slug):
        b = self._post(self.job_ms + "ai-matching/configuration",
                       {"jobSlug": job_slug, "databaseType": 1}, "configuration")
        return b.get("data") or b

    def save_criteria(self, job_slug, cfg):
        payload = {"jobSlug": job_slug, "databaseType": 1,
                   "criteria": cfg.get("criteria") or [],
                   "filters": cfg.get("filters") or {}, "isSave": True}
        if cfg.get("questions"):
            payload["questions"] = cfg["questions"]
        b = self._post(self.job_ms + "ai-matching/configuration/criteria",
                       payload, "save criteria")
        return b.get("data") or b

    def search(self, job_slug, size=100):
        body = {"advancedSearchContext": "CANDIDATE", "defaultFilterList": None,
                "filterSearchList": None, "booleanSearchList": None,
                "sortPriorityList": [], "jobSlug": job_slug, "databaseType": 1}
        b = self._post(f"{self.aries}advanced-search/candidates/ai-matching/search/get"
                       f"?page=1&size={size}", body, "search")
        return b.get("data") or []

    def search_with_polling(self, job_slug, size=100, timeout=POLL_TIMEOUT):
        started, last, n = time.time(), None, 0
        while time.time() - started < timeout:
            rows = self.search(job_slug, size)
            if n == 0:
                self.timings["first_response_s"] = self.timings.get("last_call_s")
            n += 1
            if rows:
                pending = sum(1 for r in rows if r.get("reasoningStatus") == "pending")
                if (len(rows), pending) != last:
                    self._log(f"polling: {len(rows)} candidates, {pending} still scoring")
                    last = (len(rows), pending)
                if pending == 0:
                    secs = round(time.time() - started, 2)
                    self.timings.update({"time_to_scored_s": secs, "polls": n,
                                         "scored": len(rows)})
                    return rows, secs
            elif last is None:
                self._log("polling: empty, matching still being generated")
                last = (0, 0)
            time.sleep(_interval(n - 1))
        raise TimeoutError(f"no complete matches for {job_slug} within {timeout}s")

    def run_job(self, job_slug, repeats=1):
        """Repeats exist because the scorer is not deterministic — see evaluate.py."""
        cfg = self.configure(job_slug)
        if not (cfg.get("criteria") or []):
            raise RuntimeError(f"configuration returned no criteria for {job_slug}")
        out = []
        for _ in range(max(1, repeats)):
            self.save_criteria(job_slug, cfg)
            rows, secs = self.search_with_polling(job_slug)
            out.append({"rows": rows, "seconds": secs,
                        "timings": dict(self.timings)})
        return out, cfg.get("criteria") or []


def reasoning_text(cand):
    """Per-criterion reasons plus the overall, as the judge should read them."""
    ma = cand.get("matchAnalysis") or {}
    out = []
    for c in (ma.get("criteria") or []):
        out.append(f"- [{c.get('text','')}] (weight {c.get('weight')}, "
                   f"score {c.get('score')})\n    {c.get('reason','')}")
    if ma.get("overallReason"):
        out.append(f"\nOVERALL ({ma.get('label','')}): {ma['overallReason']}")
    return "\n".join(out)


def context_text(case, side="both", resume_hint=""):
    """The activity as text, with timestamps — the judge needs the dates the scorer cites."""
    parts = []
    if resume_hint:
        parts.append(f"RESUME SAYS: {resume_hint}")
    if side in ("both", "notes"):
        for n in case.get("notes", []):
            parts.append(f"NOTE: {n}")
    if side in ("both", "calls"):
        for c in case.get("calls", []):
            parts.append(f"CALL LOG: {c}")
    return "\n".join(parts)


def run_job(m, job, repeats=1):
    runs, criteria = m.run_job(job["job_slug"], repeats)
    by_slug = {}
    for run in runs:
        for row in run["rows"]:
            slug = row.get("slug")
            if not slug:
                continue
            by_slug.setdefault(slug, []).append({
                "score": (row.get("matchScore") if row.get("matchScore") is not None
                          else (row.get("matchAnalysis") or {}).get("score")),
                "reasoning": reasoning_text(row),
                "status": row.get("reasoningStatus")})
    return {"job_key": job["key"], "job_slug": job["job_slug"], "title": job["title"],
            "criteria": [c.get("text") for c in criteria],
            "timings": [r["timings"] for r in runs],
            "candidates": by_slug}


def deterministic_checks(harvest):
    """
    The referee. Compares what the judge REPORTED against what the seed EXPECTED.

    Nothing here is a matter of opinion: each check has a recorded expected value, so a
    failure names the exact expectation it broke.
    """
    results = []
    for c in harvest:
        exp, got = c["expected"], c["reported"]
        blocked = c.get("blocked_reason")
        if blocked:
            verdict = "BLOCKED"
        elif got == exp:
            verdict = "PASS"
        elif got == "not_stated":
            verdict = "FAIL (silent)"
        else:
            verdict = "FAIL"
        results.append({**c, "verdict": verdict})
    return results


def evaluate(manifest, harvest, judge, env):
    """Ask the adapter one question per seeded expectation, then referee it."""
    checks = []
    for job in manifest["jobs"]:
        rows = harvest.get(job["job_slug"], {}).get("candidates", {})
        for cand in job["candidates"]:
            got = rows.get(cand["slug"])
            if not got:
                checks.append({"job": job["key"], "candidate": cand["name"],
                               "question": "pooled", "expected": "yes",
                               "reported": "not_stated",
                               "blocked_reason": "candidate never entered the pool"})
                continue
            reasoning = got[0]["reasoning"]
            activity = context_text(cand)
            for spec in cand.get("checks", []):
                q, arg, expected = spec["question"], spec.get("arg", ""), spec["expected"]
                if q == "fact_used":
                    v = judge.fact_used(reasoning, arg, activity)
                elif q == "source_of":
                    v = judge.source_of(reasoning, arg, activity)
                elif q == "grounded":
                    v = judge.grounded(reasoning, activity)
                else:
                    v = judge.value_reported(reasoning, arg)
                checks.append({"job": job["key"], "candidate": cand["name"],
                               "slug": cand["slug"], "question": q, "arg": arg,
                               "expected": expected, "reported": v.answer,
                               "quote": v.quote, "confidence": v.confidence,
                               "value_reported": v.value_reported,
                               "score": got[0]["score"], "env": env,
                               "blocked_reason": spec.get("blocked_reason")})
    return deterministic_checks(checks)


def manifest_conflict_values(job):
    """Twin pairs record the value each side should win with — used by the report."""
    return {c["slug"]: c.get("conflict_value") for c in job.get("candidates", [])
            if c.get("conflict_value")}


def main():
    p = argparse.ArgumentParser(description="Run the Activity Context test suite")
    p.add_argument("--env", default=CONFIG_ENV)
    p.add_argument("--manifest", default=None)
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--out", default=None)
    a = p.parse_args()

    path = a.manifest or (EDGE_MANIFEST if os.path.exists(EDGE_MANIFEST) else MANIFEST)
    manifest = json.load(open(path))
    log(f"manifest {os.path.basename(path)}  env {env_label(a.env)}  "
        f"{len(manifest['jobs'])} jobs")

    m = Matching(a.env)
    harvest = {}
    for job in manifest["jobs"]:
        try:
            h = run_job(m, job, a.repeats)
            harvest[job["job_slug"]] = h
            log(f"  {job['key']:<14} {len(h['candidates'])} candidates scored")
        except Exception as e:
            log(f"  {job['key']:<14} FAILED {type(e).__name__}: {str(e)[:120]}")

    judge = Judge()
    results = evaluate(manifest, harvest, judge, a.env)
    out = a.out or os.path.join(HERE, f"results_{a.env}.json")
    json.dump({"env": a.env, "label": env_label(a.env),
               "created_at": datetime.now(timezone.utc).isoformat(),
               "manifest": os.path.basename(path), "harvest": harvest,
               "checks": results, "judge_usage": judge.usage()},
              open(out, "w"), indent=2)

    from collections import Counter
    tally = Counter(r["verdict"] for r in results)
    log("")
    for k, v in tally.most_common():
        log(f"  {k:<14} {v}")
    log(f"  written to {out}")
    return 0 if not tally.get("FAIL") and not tally.get("FAIL (silent)") else 2


if __name__ == "__main__":
    sys.exit(main())

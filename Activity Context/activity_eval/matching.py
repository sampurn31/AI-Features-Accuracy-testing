#!/usr/bin/env python3
"""
Client for the AI Matching endpoints.

IMPORTANT — search/get is NOT standalone.
--------------------------------------------------
Calling the matching endpoint on a job that has never been configured returns HTTP 400
"No matching criteria/filters found for this job — call POST /v2/ai-matching/configuration
first", and it returns that forever; polling never resolves it. Verified over 12 polls.

The real sequence per job is:

  1. POST {job-ms}/v2/ai-matching/configuration            generate criteria from the JD
  2. POST {job-ms}/v2/ai-matching/configuration/criteria   save them (isSave: true)
  3. POST {aries}/v2/.../ai-matching/search/get            trigger, then POLL

Step 3 does return empty at first while scoring runs in the background — that part needs
the polling loop. Steps 1 and 2 are what make step 3 legal.

Saving in step 2 also DELETES the job's stored scores, which is what lets the same job be
re-scored on the second deployment with identical criteria.

Auth: these endpoints take the LOGIN token, not the public API key.
"""

import time

import requests

import config as C

TIMEOUT = 240
POLL_TIMEOUT = 420
# Ramped, not fixed. A flat 10s interval quantises everything to a multiple of 10 — an
# earlier run reported DEV 11.2s and AI2 11.1s, which measured the sleep, not the service.
POLL_STEPS = (1, 1, 2, 2, 3, 3, 5, 5, 8)
POLL_MAX = 10


def _interval(n):
    return POLL_STEPS[n] if n < len(POLL_STEPS) else POLL_MAX


class Matching:
    def __init__(self, env, verbose=True):
        self.env = env
        self.verbose = verbose
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {C.login_token(env)}",
            "Content-Type": "application/json;charset=UTF-8",
            "accept": "application/json;charset=UTF-8",
        })
        self.job_ms = C.job_service_base(env)
        self.aries = C.aries_base(env)
        self.last_call_s = 0.0
        self.timings = {}

    def _log(self, m):
        if self.verbose:
            print(f"    [{self.env}] {m}", flush=True)

    def _post(self, url, payload, what):
        t0 = time.time()
        r = self.s.post(url, json=payload, timeout=TIMEOUT)
        self.last_call_s = round(time.time() - t0, 3)
        if r.status_code != 200:
            raise RuntimeError(f"{what} -> HTTP {r.status_code}: {r.text[:250]}")
        return r.json()

    def configure(self, job_slug):
        """Step 1 — criteria and filters generated from the job description."""
        b = self._post(self.job_ms + "ai-matching/configuration",
                       {"jobSlug": job_slug, "databaseType": 1}, "configuration")
        return b.get("data") or b

    def save_criteria(self, job_slug, cfg):
        """Step 2 — persist. Also clears stored scores, forcing a fresh run."""
        payload = {"jobSlug": job_slug, "databaseType": 1,
                   "criteria": cfg.get("criteria") or [],
                   "filters": cfg.get("filters") or {}, "isSave": True}
        if cfg.get("questions"):
            payload["questions"] = cfg["questions"]
        b = self._post(self.job_ms + "ai-matching/configuration/criteria",
                       payload, "save criteria")
        self.timings["save_criteria_s"] = self.last_call_s
        return b.get("data") or b

    def search(self, job_slug, size=100):
        """Step 3, single call. Returns [] while scoring is still running."""
        body = {"advancedSearchContext": "CANDIDATE", "defaultFilterList": None,
                "filterSearchList": None, "booleanSearchList": None,
                "sortPriorityList": [], "jobSlug": job_slug, "databaseType": 1}
        b = self._post(f"{self.aries}advanced-search/candidates/ai-matching/search/get"
                       f"?page=1&size={size}", body, "search")
        return b.get("data") or []

    def search_with_polling(self, job_slug, size=100,
                            timeout=POLL_TIMEOUT, interval=None):
        """
        Step 3 with polling: keep asking until candidates come back scored.

        Records, into self.timings:
          first_response_s   latency of the first search/get call — a pure API round trip
          time_to_scored_s   wall clock until no row is still pending; what a user waits
          polls              how many calls it took
          scored             pool size, so the two sides compare like for like
        """
        started = time.time()
        last, n = None, 0
        while time.time() - started < timeout:
            rows = self.search(job_slug, size)
            if n == 0:
                self.timings["first_response_s"] = self.last_call_s
            n += 1
            if rows:
                pending = sum(1 for r in rows if r.get("reasoningStatus") == "pending")
                state = (len(rows), pending)
                if state != last:
                    self._log(f"polling: {len(rows)} candidates, {pending} still scoring")
                    last = state
                if pending == 0:
                    secs = round(time.time() - started, 2)
                    self.timings.update({"time_to_scored_s": secs, "polls": n,
                                         "scored": len(rows)})
                    return rows, secs
            elif last is None:
                self._log("polling: empty, matching still being generated")
                last = (0, 0)
            time.sleep(interval if interval is not None else _interval(n - 1))
        rows = self.search(job_slug, size)
        raise TimeoutError(f"no complete matches for {job_slug} within {timeout}s "
                           f"(last saw {len(rows)} rows)")

    def run_job(self, job_slug):
        """Configure -> save -> trigger -> poll. Returns (rows, criteria, seconds)."""
        cfg = self.configure(job_slug)
        criteria = cfg.get("criteria") or []
        if not criteria:
            raise RuntimeError(f"configuration returned no criteria for {job_slug} "
                               f"(status={cfg.get('status')})")
        self.save_criteria(job_slug, cfg)
        rows, secs = self.search_with_polling(job_slug)
        return rows, criteria, secs


def match_analysis_text(row):
    """The reasoning as the judge should read it: per-criterion plus the overall."""
    ma = row.get("matchAnalysis") or {}
    out = []
    for c in (ma.get("criteria") or []):
        out.append(f"- [{c.get('text','')}] (weight {c.get('weight')}, score {c.get('score')})\n"
                   f"    {c.get('reason','')}")
    if ma.get("overallReason"):
        out.append(f"\nOVERALL ({ma.get('label','')}): {ma['overallReason']}")
    return "\n".join(out)

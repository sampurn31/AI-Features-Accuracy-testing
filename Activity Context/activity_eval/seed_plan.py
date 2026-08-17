#!/usr/bin/env python3
"""
Write the planned notes and call logs onto the REAL candidates in the account.

Purely additive. This creates notes and call logs against existing candidate records and
touches nothing else — no candidate field is written, no job is created or changed. The
account's own data stays as it is.

Ordering matters: activity recency reads `updated_on`, which the server assigns and which
cannot be overridden, so consecutive items for one candidate are spaced by ORDER_GAP to
land in different seconds. Otherwise "the newer note wins" scenarios are ambiguous.

    python3 seed_plan.py --dry-run     # counts only, no writes
    python3 seed_plan.py --jobs 1      # one job, to check
    python3 seed_plan.py --resume      # skip candidates already written
    python3 seed_plan.py --undo        # delete everything this wrote
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config as C  # noqa: E402

PLAN = os.path.join(HERE, "plan.json")
WROTE = os.path.join(HERE, "wrote.json")
ENV = "dev"
ORDER_GAP = 1.1
TIMEOUT = 60
N = {"notes": 0, "calls": 0, "failed": 0, "candidates": 0}


def log(m):
    print(f"[seed] {m}", flush=True)


class Api:
    def __init__(self, env=ENV):
        self.b = C.api_base(env)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {C.api_key(env)}"})

    def post(self, path, payload, what):
        last = ""
        for i in range(3):
            try:
                r = self.s.post(self.b + path, json=payload, timeout=TIMEOUT)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code} {r.text[:140]}"
                if 400 <= r.status_code < 500 and r.status_code not in (422, 429):
                    break
            except requests.RequestException as e:
                last = type(e).__name__
            time.sleep(1.5 * (i + 1))
        N["failed"] += 1
        log(f"  !! {what}: {last}")
        return None

    def delete(self, path):
        try:
            return self.s.delete(self.b + path, timeout=TIMEOUT).status_code == 200
        except requests.RequestException:
            return False


def already_written():
    """Candidates a previous run already wrote activity for — never write twice."""
    if not os.path.exists(WROTE):
        return set(), {"note_ids": [], "call_ids": [], "candidates": []}
    w = json.load(open(WROTE))
    return {c["slug"] for c in w.get("candidates", [])}, w


def seed(api, plan, limit_jobs=0, resume=False):
    jobs = []
    for x in plan:
        if x["job_slug"] not in jobs:
            jobs.append(x["job_slug"])
    if limit_jobs:
        jobs = jobs[:limit_jobs]
    todo = [x for x in plan if x["job_slug"] in jobs]

    done, wrote = already_written() if resume else (set(), {"note_ids": [], "call_ids": [],
                                                            "candidates": []})
    if done:
        before = len(todo)
        todo = [x for x in todo if x["candidate_slug"] not in done]
        log(f"resuming — {len(done)} candidates already done, {before - len(todo)} skipped, "
            f"{len(todo)} to write")

    for i, x in enumerate(todo, 1):
        notes, calls = x["notes"], x["calls"]
        if not notes and not calls:
            continue
        slug = x["candidate_slug"]
        N["candidates"] += 1
        for k, text in enumerate(notes):
            b = api.post("notes", {"related_to": slug, "related_to_type": "candidate",
                                   "description": text}, f"note {x['candidate']}")
            if b:
                N["notes"] += 1
                wrote["note_ids"].append(b.get("id"))
            if k < len(notes) - 1:
                time.sleep(ORDER_GAP)
        for k, (text, ctype, days) in enumerate(calls):
            started = (datetime.now(timezone.utc) - timedelta(days=days)) \
                .strftime("%Y-%m-%dT%H:%M:%S.000000Z")
            b = api.post("call-logs", {
                "related_to": slug, "related_to_type": "candidate", "call_type": ctype,
                "call_notes": text, "call_started_on": started,
                "contact_number": "9876543210", "duration": "900"},
                f"call {x['candidate']}")
            if b:
                N["calls"] += 1
                wrote["call_ids"].append(b.get("id"))
            if k < len(calls) - 1:
                time.sleep(ORDER_GAP)
        wrote["candidates"].append({"slug": slug, "name": x["candidate"],
                                    "job_slug": x["job_slug"], "scenario": x["scenario"],
                                    "notes": notes, "calls": [c[0] for c in calls]})
        if i % 25 == 0:
            log(f"  {i}/{len(todo)}  notes {N['notes']}  calls {N['calls']}")
    return wrote


def undo(api):
    if not os.path.exists(WROTE):
        raise SystemExit("nothing recorded to undo")
    w = json.load(open(WROTE))
    ok = 0
    for i in w.get("note_ids", []):
        ok += api.delete(f"notes/{i}")
    for i in w.get("call_ids", []):
        ok += api.delete(f"call-logs/{i}")
    tot = len(w.get("note_ids", [])) + len(w.get("call_ids", []))
    print(f"deleted {ok}/{tot} activity records")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--jobs", type=int, default=0, help="limit to the first N jobs")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--undo", action="store_true")
    p.add_argument("--resume", action="store_true",
                   help="skip candidates a previous run already wrote activity for")
    a = p.parse_args()

    api = Api()
    if a.undo:
        return undo(api)

    plan = json.load(open(PLAN))
    with_act = [x for x in plan if x["notes"] or x["calls"]]
    items = sum(len(x["notes"]) + len(x["calls"]) for x in plan)
    gaps = sum(max(0, len(x["notes"]) - 1) + max(0, len(x["calls"]) - 1) for x in plan)
    log(f"plan: {len(plan)} pairs, {len(with_act)} receiving activity, {items} items")
    log(f"      ordering pauses add ~{gaps * ORDER_GAP / 60:.0f} min")
    if a.dry_run:
        log("dry run — nothing written")
        return 0

    started = time.time()
    wrote = seed(api, plan, a.jobs, a.resume)
    json.dump(wrote, open(WROTE, "w"), indent=1)
    log("")
    for k, v in N.items():
        log(f"  {k:<12} {v}")
    log(f"  {'elapsed':<12} {(time.time() - started) / 60:.1f} min")
    log(f"  recorded ids -> {WROTE}  (python3 seed_plan.py --undo to remove)")
    return 0 if not N["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())

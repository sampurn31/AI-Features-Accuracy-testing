#!/usr/bin/env python3
"""
Delete everything recorded in a manifest.

dev and ats9 are two application deployments over ONE shared database, so this only needs
to run once no matter how many deployments you seeded through. Deleting through either
removes the record for both.

Order matters: candidates first, then jobs, then contacts, then companies. A company with
a contact still attached will refuse to delete.

Only ever touches slugs that appear in a manifest this project wrote — it will not sweep
the account looking for things that look like ours.

    python3 cleanup.py --manifest eval_manifest.json
    python3 cleanup.py --manifest a.json --manifest b.json --dry-run
"""

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import credentials                                            # noqa: E402
from credentials import api_base, resolve_api_key, CONFIG_ENV  # noqa: E402

_lock = threading.Lock()
TIMEOUT = 45


def collect(paths):
    """Gather every slug worth deleting, de-duplicated, in dependency order."""
    out = {"candidates": [], "jobs": [], "contacts": [], "companies": []}
    seen = {k: set() for k in out}

    def add(kind, slug):
        if slug and slug not in seen[kind]:
            seen[kind].add(slug)
            out[kind].append(slug)

    for p in paths:
        if not os.path.exists(p):
            print(f"  !! no such manifest: {p}")
            continue
        m = json.load(open(p))
        for job in m.get("jobs", []):
            for c in job.get("candidates", []):
                add("candidates", c.get("slug"))
            add("jobs", job.get("job_slug"))
            add("contacts", job.get("contact_slug"))
            add("companies", job.get("company_slug"))
    return out


def delete_many(base, key, kind, slugs, workers=8):
    if not slugs:
        return 0, 0
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {key}"})

    def one(slug):
        try:
            return s.delete(f"{base}{kind}/{slug}", timeout=TIMEOUT).status_code == 200
        except requests.RequestException:
            return False

    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(one, slugs):
            ok += bool(r)
    return ok, len(slugs)


def main():
    p = argparse.ArgumentParser(description="Delete everything a manifest created")
    p.add_argument("--manifest", action="append", required=True,
                   help="repeatable")
    p.add_argument("--env", default=CONFIG_ENV)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()

    targets = collect(a.manifest)
    total = sum(len(v) for v in targets.values())
    print(f"env {a.env}  (shared database — deleting once is enough)")
    for kind in ("candidates", "jobs", "contacts", "companies"):
        print(f"  {kind:<12} {len(targets[kind])}")
    if not total:
        print("nothing to delete")
        return 0
    if a.dry_run:
        print("dry run — nothing deleted")
        return 0

    base, key = api_base(a.env), resolve_api_key(a.env)
    failed = 0
    # dependency order: a company with a contact attached will refuse to delete
    for kind in ("candidates", "jobs", "contacts", "companies"):
        ok, n = delete_many(base, key, kind, targets[kind], a.workers)
        failed += n - ok
        print(f"  deleted {ok}/{n} {kind}")
    print("done" if not failed else f"done with {failed} failures")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())

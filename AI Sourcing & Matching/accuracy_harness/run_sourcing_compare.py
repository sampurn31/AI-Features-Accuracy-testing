"""
Two-run AI Sourcing (Global/db=2) comparison — same 5 queries, run twice with a delay, then
diff every field per candidate to see what changed (scores, per-criterion score+reasoning,
label, and the profile 'description'/summary that may disappear in the later run).

IDEAL-COMPARISON guarantee: both runs send the IDENTICAL aiSearch + IDENTICAL matchingCriteria,
and run 2 REUSES run 1's historyId so it reads the same cached top-100 pool. Candidates are
matched across runs by linkedinUrl (fallback id); we diff the intersection.

  RCRM_ENV=dev python3 run_sourcing_compare.py --phase 1 --run-id cmp1   # run 1 (stores snapshot + state; keeps history)
  # ... wait however long ("after some time") ...
  RCRM_ENV=dev python3 run_sourcing_compare.py --phase 2 --run-id cmp1   # run 2 (identical inputs) + builds the diff workbook
  RCRM_ENV=dev python3 run_sourcing_compare.py --compare-only --run-id cmp1   # rebuild workbook offline

Output: output/compare_<runid>_run{1,2}.json (full snapshots) · compare_<runid>_state.json
        (pinned inputs + historyIds) · sourcing_compare_<runid>.xlsx (the diff).
"""
import argparse
import json
import os
import time

import requests

import config
import rcrm_auth
import sourcing_client

PAGES = int(os.environ.get("COMPARE_PAGES", "4"))


def _resolve_token():
    """Login for the Albatross JWT (no Anthropic key needed — the compare does no judging)."""
    if config.RCRM_ALBATROSS_TOKEN:
        return config.RCRM_ALBATROSS_TOKEN
    info = rcrm_auth.login()
    print(f"Auth: {config.LOGIN_EMAIL} on '{config.RCRM_ENV}' -> token acquired ({info.get('account')})")
    return info["token"]

# 5 fixed queries with fixed matching criteria (identical across both runs).
QUERIES = [
    ("software-dev-india", "software developer in india",
     [{"text": "Software development experience", "weight": 8}, {"text": "Based in India", "weight": 6}]),
    ("frontend-dev-india", "frontend developer in India",
     [{"text": "Frontend development (React/JavaScript) experience", "weight": 8}, {"text": "Based in India", "weight": 6}]),
    ("backend-dev-india", "backend developer in india",
     [{"text": "Backend development experience", "weight": 8}, {"text": "Based in India", "weight": 6}]),
    ("product-manager", "Product Manager",
     [{"text": "Product management experience", "weight": 8}, {"text": "Roadmap & stakeholder management", "weight": 6}]),
    ("devops-engineer", "DevOps Engineer",
     [{"text": "DevOps / CI-CD / cloud infrastructure experience", "weight": 8}, {"text": "Kubernetes or infrastructure-as-code", "weight": 6}]),
]


def _fetch_page(url, body, token, page):
    """Fetch one page's SSE stream. Returns (candidate_chunks, metadata_or_None, error_or_None)."""
    cands, meta, err = [], None, None
    try:
        resp = requests.post(url, params={"page": page}, data=json.dumps(body),
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                      "Accept": "application/json"}, timeout=240, stream=True)
    except requests.RequestException as e:
        return [], None, f"{type(e).__name__}: {e}"
    if resp.status_code != 200:
        return [], None, f"HTTP {resp.status_code} {resp.text[:200]}"
    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                d = json.loads(raw).get("data", {})
            except json.JSONDecodeError:
                continue
            t = d.get("type")
            if t == "metadata" and meta is None:
                meta = d
            elif t == "candidate":
                cands.append(d)
            elif t == "error":
                err = (err or "") + json.dumps(d)[:200]
    except requests.RequestException as e:
        err = (err or "") + f"[stream] {type(e).__name__}: {e}"
    return cands, meta, err


def _capture(ai_search, criteria, token, history_id):
    """Full SSE capture of pages 1..PAGES for one query. Returns
    {candidates:[full dict + _page/_rank], metadata, queryUsed, error}.
    An empty page is retried a few times: on a fresh account scoring is async, so pages
    2+ can race ahead of it and stream nothing before the scores are ready."""
    url = f"{config.RCRM_NEPTUNE_URL}/{config.SOURCING_SEARCH_PATH}"
    out = {"candidates": [], "metadata": None, "queryUsed": None, "error": None}
    rank = 0
    for page in range(1, PAGES + 1):
        body = {"aiSearch": ai_search, "matchingCriteria": criteria}
        if history_id is not None:
            body["historyId"] = history_id
        page_cands, page_meta, last_err = [], None, None
        for attempt in range(4):                       # retry empty pages (async-scoring race)
            page_cands, page_meta, last_err = _fetch_page(url, body, token, page)
            if page_cands or (page_meta and page_meta.get("hasNextPage") is False):
                last_err = None                        # got data / clean end — ignore transient err
                break
            time.sleep(5)                              # scoring not ready yet — wait, retry same page
        if out["metadata"] is None and page_meta is not None:
            out["metadata"] = page_meta
            out["queryUsed"] = page_meta.get("queryUsed")
        if last_err:
            out["error"] = (out["error"] or "") + f"page {page}: {last_err} "
        for d in page_cands:
            rank += 1
            prof = d.get("candidate") or {}
            cand = dict(prof)                          # full profile (description, keySkills, experience, ...)
            cand.update({"matchScore": d.get("matchScore"), "matchLabel": d.get("matchLabel"),
                         "matchAnalysis": d.get("matchAnalysis"), "candidateIndex": d.get("candidateIndex"),
                         "_page": page, "_rank": rank})
            out["candidates"].append(cand)
        if not page_cands or (page_meta and page_meta.get("hasNextPage") is False):
            break
    return out


def _paths(run_id):
    base = os.path.join(config.OUTPUT_DIR, f"compare_{run_id}")
    return base + "_run1.json", base + "_run2.json", base + "_state.json"


def _capture_all(run_id, slot, limit):
    """Capture the fixed query set on the CURRENT env (RCRM_ENV) into snapshot slot 1 or 2.
    Independent of the other slot (both use the fixed QUERIES constant + identical criteria), so
    the two envs (e.g. ts9=summary-fed vs dev=no-summary) can run IN PARALLEL. Build the diff
    afterward with --compare-only once BOTH slots exist."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    token = _resolve_token()
    queries = QUERIES[:limit] if limit else QUERIES
    base = os.path.join(config.OUTPUT_DIR, f"compare_{run_id}")
    print(f"CAPTURE run{slot} on env={config.RCRM_ENV} as {config.LOGIN_EMAIL}: "
          f"{config.RCRM_NEPTUNE_URL} | {len(queries)} queries, {PAGES} pages\n")
    snap, state = {}, {}
    for qid, ai, crit in queries:
        _, hid, _ = sourcing_client.create_history(ai, token)
        cap = _capture(ai, crit, token, hid)
        snap[qid] = cap["candidates"]
        state[qid] = {"aiSearch": ai, "criteria": crit, "history_id": hid, "queryUsed": cap["queryUsed"]}
        print(f"  {qid:<20} history={hid} candidates={len(cap['candidates'])} "
              f"total={(cap['metadata'] or {}).get('totalResults')} err={cap['error']}")
    json.dump({"run": slot, "run_id": run_id, "env": config.RCRM_ENV,
               "account_email": config.LOGIN_EMAIL, "snapshot": snap},
              open(f"{base}_run{slot}.json", "w"), ensure_ascii=False)
    json.dump({"run_id": run_id, "account_email": config.LOGIN_EMAIL, "queries": state},
              open(f"{base}_state.json", "w"), ensure_ascii=False)  # criteria identical across slots
    print(f"\nrun{slot} ({config.RCRM_ENV}) snapshot -> {base}_run{slot}.json")
    print(f"When BOTH runs are captured:  python3 run_sourcing_compare.py --compare-only --run-id {run_id}")


def phase1(run_id, limit):
    _capture_all(run_id, 1, limit)


def phase2(run_id, limit):
    _capture_all(run_id, 2, limit)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, choices=[1, 2])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int, default=0, help="phase 1: run only the first N queries")
    ap.add_argument("--compare-only", action="store_true", help="rebuild workbook from stored snapshots")
    args = ap.parse_args()
    if args.compare_only:
        import compare_report
        print("WORKBOOK ->", compare_report.build(args.run_id))
        return
    if args.phase == 1:
        phase1(args.run_id, args.limit)
    elif args.phase == 2:
        phase2(args.run_id, args.limit)
    else:
        raise SystemExit("give --phase 1 | --phase 2 | --compare-only")


if __name__ == "__main__":
    main()

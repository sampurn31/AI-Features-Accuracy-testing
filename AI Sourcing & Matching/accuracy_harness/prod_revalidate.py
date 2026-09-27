"""
READ-ONLY prod stability re-check for the 'top' AI Sourcing queries. Global retrieval
filters are LLM-generated (non-deterministic) and the index drifts, so a query that
returned candidates last week may return 0 today. This runs each selected query N times
on the CURRENT env and keeps only the ones that are reliably non-zero.

  source set_env.sh && source set_prod_urls.sh && ./.venv/bin/python prod_revalidate.py
  ...              --repeats 3 --include-jd --limit 6      # options

Page-1 only, NO history writes. Output: console table + output/prod_revalidation.txt
(STABLE = non-zero every run, FLAKY = sometimes, DEAD = always 0) and a clean keep-list.
"""
import argparse
import json
import os

import config
import rcrm_auth
import sourcing_client as SC

CHECKPOINT = os.path.join("output", "scale_checkpoint_20260723-full.jsonl")


def _select(include_jd):
    """Reproduce the top-list selection: n>=100, exclude jd_overlimit; 7 short + 7 brief
    (+ 6 jd_excerpt if --include-jd), diverse roles, ranked by pool size."""
    rows = []
    for line in open(CHECKPOINT):
        r = json.loads(line)
        if r.get("format") == "jd_overlimit" or (r.get("n_candidates") or 0) < 100:
            continue
        rows.append({"id": r["id"], "fmt": r["format"], "role": r["id"].rsplit("-", 2)[0],
                     "total": r.get("totalResults") or 0, "q": r["query"], "crit": r.get("criteria") or []})
    target = {"short": 7, "brief": 7}
    if include_jd:
        target["jd_excerpt"] = 6
    picked, seen = [], set()
    for fmt, k in target.items():
        got = 0
        for x in sorted([x for x in rows if x["fmt"] == fmt], key=lambda x: -x["total"]):
            if x["role"] in seen:
                continue
            picked.append(x); seen.add(x["role"]); got += 1
            if got >= k:
                break
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3, help="runs per query (default 3)")
    ap.add_argument("--include-jd", action="store_true", help="also re-check the 6 JD-length queries")
    ap.add_argument("--limit", type=int, default=0, help="only the first N queries")
    args = ap.parse_args()

    tok = config.RCRM_ALBATROSS_TOKEN
    if not tok:
        info = rcrm_auth.login(); tok = info["token"]; who = info.get("account")
    else:
        who = "(pre-set token)"
    queries = _select(args.include_jd)
    if args.limit:
        queries = queries[: args.limit]
    print(f"ENV={config.RCRM_ENV}  neptune={config.RCRM_NEPTUNE_URL}")
    print(f"account={config.LOGIN_EMAIL}  {who}   |   {len(queries)} queries x {args.repeats} runs\n")
    print(f"{'#':<3}{'fmt':<11}{'role':<22}{'runs (candidate count each)':<30}verdict")
    print("-" * 92)

    results = []
    for i, x in enumerate(queries, 1):
        counts = []
        for _ in range(args.repeats):
            status, res = SC.scored_search(ai_search=x["q"], matching_criteria=x["crit"],
                                           page=1, token=tok, timeout=180)
            counts.append(len(res.get("candidates") or []) if status == 200 else -1)
        nz = sum(1 for c in counts if c > 0)
        verdict = ("STABLE" if nz == args.repeats else "DEAD" if nz == 0 else "FLAKY")
        results.append({**x, "counts": counts, "nz": nz, "verdict": verdict})
        print(f"{i:<3}{x['fmt']:<11}{x['role']:<22}{str(counts):<30}{verdict} ({nz}/{args.repeats})")

    stable = [r for r in results if r["verdict"] == "STABLE"]
    flaky = [r for r in results if r["verdict"] == "FLAKY"]
    dead = [r for r in results if r["verdict"] == "DEAD"]
    print(f"\nSTABLE {len(stable)}  ·  FLAKY {len(flaky)}  ·  DEAD {len(dead)}   (of {len(results)})")

    out = os.path.join("output", "prod_revalidation.txt")
    with open(out, "w") as f:
        f.write(f"AI Sourcing prod stability re-check — env={config.RCRM_ENV}, "
                f"{args.repeats} runs/query, {len(results)} queries.\n")
        f.write("STABLE = candidates every run · FLAKY = sometimes 0 · DEAD = always 0\n" + "=" * 80 + "\n\n")
        for tag, grp in (("STABLE (safe to reuse)", stable), ("FLAKY (unreliable)", flaky),
                         ("DEAD (0 today)", dead)):
            f.write(f"--- {tag}: {len(grp)} ---\n")
            for r in grp:
                f.write(f"[{r['fmt']}] {r['role']}  runs={r['counts']}\n    {r['q']}\n")
            f.write("\n")
    print(f"\nwritten -> {out}")


if __name__ == "__main__":
    main()

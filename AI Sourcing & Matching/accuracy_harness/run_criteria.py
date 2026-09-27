"""
Final-query-generation accuracy (the criteria->filters / 1.2 step). For each golden
criteria case:
  A = current impl   (POST /v2/ai-matching/configuration/criteria, preview)
  B = reference      (Sonnet 5 on the real AI_MATCHING_CRITERIA_TO_FILTERS prompt)
  verdict = judge (criteria rubric) — is A's filter set faithful to (criteria + JD)?
Writes a colour-coded 2-sheet xlsx: output/criteria_results_<ts>.xlsx (reuses run._write_xlsx).

  source set_env.sh
  python run_criteria.py [--limit N]
"""
import argparse
import glob
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import rcrm_client
import llm
from run import _load_text, _write_xlsx, _stringify_current, _resolve_token, WORKERS


def _load_criteria_cases():
    cases = []
    for path in sorted(glob.glob(os.path.join(config.GOLDEN_DIR, "criteria", "*.json"))):
        with open(path, encoding="utf-8") as f:
            cases.extend(json.load(f))
    return cases


def _context(case):
    lines = "\n".join(f"- {c['text']} (weight: {c['weight']})" for c in case.get("criteria", []))
    return f"JOB/QUERY:\n{case['input']}\n\nFINALIZED CRITERIA:\n{lines}"


def _process(idx, case, rubric, token, prompts):
    cid = case.get("id", f"case-{idx}")
    db = int(config.FORCE_DB) if config.FORCE_DB else int(case.get("database_type", config.DB_GLOBAL_DATABASE))
    jd = case["input"]
    criteria = case.get("criteria", [])
    base = {"id": cid, "kind": case.get("kind", ""), "term": "", "database_type": db,
            "input": _context(case), "expectation": case.get("expectation", "")}
    try:
        if db == config.DB_GLOBAL_DATABASE:
            status, current = rcrm_client.criteria_to_filters(
                ai_search=jd, database_type=db, criteria=criteria, token=token)
        else:
            status, current = rcrm_client.criteria_to_filters(
                job_slug=(case.get("job_slug") or config.JOB_ANCHOR), jd_text=jd,
                database_type=db, criteria=criteria, token=token)
        reference = llm.reference_criteria_to_filters(prompts[db], jd, criteria)
        verdict = llm.judge(rubric, _context(case), _stringify_current(status, current), reference,
                            expectation=case.get("expectation"))
        base.update({
            "current_http": status,
            "current_output": json.dumps(current, ensure_ascii=False, indent=2),
            "reference_output": reference,
            "verdict": verdict.get("verdict", ""),
            "confidence": verdict.get("confidence", ""),
            "expectation_met": verdict.get("expectation_met", ""),
            "current_issues": verdict.get("current_issues", ""),
            "reference_issues": verdict.get("reference_issues", ""),
            "judge_reasoning": verdict.get("reasoning", ""),
        })
    except Exception as e:  # noqa: BLE001
        base.update({"current_http": "ERROR", "current_output": "", "reference_output": "",
                     "verdict": "error", "confidence": "", "expectation_met": "",
                     "current_issues": f"{type(e).__name__}: {e}", "reference_issues": "", "judge_reasoning": ""})
    return idx, base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    config.require_runtime_env()
    rubric = _load_text(config.CRITERIA_JUDGE_RUBRIC, "criteria judge rubric")
    cases = _load_criteria_cases()
    if args.limit:
        cases = cases[: args.limit]

    token = _resolve_token()
    eff_dbs = ({int(config.FORCE_DB)} if config.FORCE_DB
               else {int(c.get("database_type", config.DB_GLOBAL_DATABASE)) for c in cases})
    if config.FORCE_DB and int(config.FORCE_DB) != config.DB_GLOBAL_DATABASE and not config.JOB_ANCHOR:
        import rcrm_auth
        config.JOB_ANCHOR = rcrm_auth.get_job_anchor(token)
        print(f"db=1 job anchor: {config.JOB_ANCHOR}")
    prompts = {db: _load_text(config.criteria_prompt_path(db), f"criteria prompt (db={db})")
               for db in sorted(eff_dbs)}
    print(f"RCRM: {config.RCRM_JOB_SERVICE_URL}/{config.CRITERIA_PATH}  | FORCE_DB={config.FORCE_DB or '(per-case)'}")
    print(f"Running {len(cases)} criteria->filters cases, {WORKERS} workers ...\n")

    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_process, i, c, rubric, token, prompts) for i, c in enumerate(cases, 1)]
        for fut in as_completed(futs):
            i, row = fut.result()
            results[i] = row
            done += 1
            print(f"[{done}/{len(cases)}] {row['id']:<34} {str(row['verdict']):<16} exp={row.get('expectation_met')}")

    rows = [results[i] for i in sorted(results)]
    print("\nverdict:", dict(Counter(str(r['verdict']) for r in rows)))
    print("exp_met:", dict(Counter(str(r['expectation_met']) for r in rows)))
    print("http   :", dict(Counter(str(r['current_http']) for r in rows)))
    if rows:
        _write_xlsx(rows, prefix="criteria_results")


if __name__ == "__main__":
    main()

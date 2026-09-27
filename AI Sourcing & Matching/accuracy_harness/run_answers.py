"""
Answer-influence ("impact of answers on filters"). Metamorphic + judge. Per case:
  R1 = config with NO answers      -> should be AWAITING_ANSWERS with questions
  (answer each question using its OWN returned options)
  R2 = config WITH those answers   -> should be COMPLETE, and reflect the answers
Deterministic invariant: R2 COMPLETE + no questions + config changed + answer reflected.
Judge (judge_influence) confirms the change is sensible & in the right field.

  source set_env.sh
  python run_answers.py [--limit N]
Output: output/answer_influence_results_<ts>.xlsx (BEFORE in raw_io.reference_output, AFTER in current_output).
"""
import argparse
import glob
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import rcrm_client
import llm
from run import _load_text, _write_xlsx, _resolve_token, WORKERS

_STOP = {"the", "and", "for", "with", "your", "you", "our", "years", "year", "level",
         "experience", "role", "other", "none", "all", "any", "within", "prefer", "yes", "no"}


def _tokens(s):
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z+#.]{2,}", s or "") if w.lower() not in _STOP]


def _cf(o):  # criteria+filters signature for change detection
    return json.dumps({"c": o.get("criteria"), "f": o.get("filters")}, sort_keys=True, ensure_ascii=False)


def _load_cases():
    cases = []
    for p in sorted(glob.glob(os.path.join(config.GOLDEN_DIR, "answers", "*.json"))):
        with open(p, encoding="utf-8") as f:
            cases.extend(json.load(f))
    return cases


def _process(idx, case, rubric, token):
    cid = case.get("id", f"case-{idx}")
    jd = case["input"]
    db = int(config.FORCE_DB) if config.FORCE_DB else config.DB_GLOBAL_DATABASE
    base = {"id": cid, "kind": case.get("kind", "answer_influence"), "term": "",
            "database_type": db, "input": jd, "expectation": case.get("expectation", ""),
            "reference_output": "", "current_output": "", "confidence": "", "reference_issues": ""}

    def _cfg(ans=None):
        if db == config.DB_GLOBAL_DATABASE:
            return rcrm_client.get_configuration(ai_search=jd, database_type=db, answers=ans, token=token)
        return rcrm_client.get_configuration(job_slug=config.JOB_ANCHOR, jd_text=jd, database_type=db,
                                             answers=ans, token=token)
    try:
        s1, r1 = _cfg()
        q1 = (r1.get("questions") if isinstance(r1, dict) else None) or []
        st1 = r1.get("status") if isinstance(r1, dict) else None
        base["reference_output"] = json.dumps(r1, ensure_ascii=False, indent=2)
        if s1 != 200 or st1 != "AWAITING_ANSWERS" or not q1:
            base.update({"current_http": s1, "verdict": "no_question", "expectation_met": "n/a",
                         "current_issues": f"R1 status={st1} ({len(q1)}q) — not ambiguous / nothing to answer",
                         "judge_reasoning": ""})
            return idx, base
        # answer each question with its first offered option
        answers = [{"question": q.get("question"), "answer": ((q.get("options") or ["Yes"])[0])} for q in q1]
        qa_text = "\n".join(f"Q: {a['question']}  ->  A: {a['answer']}" for a in answers)
        s2, r2 = _cfg(answers)
        q2 = (r2.get("questions") if isinstance(r2, dict) else None) or []
        st2 = r2.get("status") if isinstance(r2, dict) else None
        base["current_output"] = json.dumps(r2, ensure_ascii=False, indent=2)
        completed = (st2 == "COMPLETE" and not q2)
        changed = _cf(r1) != _cf(r2)
        chosen_tokens = {t for a in answers for t in _tokens(a["answer"])}
        r2ser = _cf(r2).lower()
        reflected = any(t in r2ser for t in chosen_tokens) if chosen_tokens else False
        det_pass = completed and changed and reflected
        v = llm.judge_influence(rubric, jd, qa_text, r1, r2)
        base.update({
            "current_http": s2,
            "input": jd + "\n\nAnswers chosen:\n" + qa_text,
            "verdict": v.get("influence", ""),
            "confidence": v.get("confidence", ""),
            "expectation_met": "yes" if det_pass else "no",
            "current_issues": f"[det] R1={st1}({len(q1)}q)->R2={st2}({len(q2)}q) "
                              f"completed={completed} changed={changed} reflected={reflected} | {v.get('issues','')}",
            "judge_reasoning": v.get("reasoning", ""),
        })
    except Exception as e:  # noqa: BLE001
        base.update({"current_http": "ERROR", "verdict": "error", "confidence": "", "expectation_met": "no",
                     "current_issues": f"{type(e).__name__}: {e}", "judge_reasoning": ""})
    return idx, base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    config.require_runtime_env()
    rubric = _load_text(config.ANSWER_INFLUENCE_RUBRIC, "answer-influence rubric")
    cases = _load_cases()
    if args.limit:
        cases = cases[: args.limit]
    token = _resolve_token()
    if config.FORCE_DB and int(config.FORCE_DB) != config.DB_GLOBAL_DATABASE and not config.JOB_ANCHOR:
        import rcrm_auth
        config.JOB_ANCHOR = rcrm_auth.get_job_anchor(token)
        print(f"db=1 job anchor: {config.JOB_ANCHOR}")
    print(f"RCRM: {config.RCRM_JOB_SERVICE_URL}/{config.CONFIG_PATH}  | FORCE_DB={config.FORCE_DB or '(per-case=2)'}")
    print(f"Running {len(cases)} answer-influence cases (2 config calls each), {WORKERS} workers ...\n")
    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_process, i, c, rubric, token) for i, c in enumerate(cases, 1)]
        for fut in as_completed(futs):
            i, row = fut.result()
            results[i] = row
            done += 1
            print(f"[{done}/{len(cases)}] {row['id']:<26} influence={str(row['verdict']):<12} det_pass={row.get('expectation_met')}")
    rows = [results[i] for i in sorted(results)]
    print("\ninfluence:", dict(Counter(str(r['verdict']) for r in rows)))
    print("det_pass :", dict(Counter(str(r['expectation_met']) for r in rows)))
    if rows:
        _write_xlsx(rows, prefix="answer_influence_results")


if __name__ == "__main__":
    main()

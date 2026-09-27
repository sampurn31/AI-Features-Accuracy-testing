"""
PW-6818 — END-TO-END accuracy suite: query -> questions -> answers -> criteria ->
filters -> criteria edits -> search -> results. Every hop is recorded and checked.

    source set_env.sh
    RCRM_ENV=dev python3 run_e2e.py --run-id e2e1 --limit 4          # pilot
    RCRM_ENV=dev python3 run_e2e.py --run-id e2e1                    # full matrix (resumes)
    python3 run_e2e.py --run-id e2e1 --rebuild                       # workbook only, offline

Stages per case (each writes its raw request/response into the checkpoint):
  S1 config round 1        questions + criteria + filters, cold
  S2 questions             materiality / redundancy / over-asking
  S3 answers               skip-all vs answer-all: does the answer reach criteria AND filters
  S4 criteria -> filters   the "final query" the UI shows and lets you edit
  S5 mutations             remove a criterion / lower a weight / raise a weight -> correct delta?
  S6 search                what retrieval ACTUALLY used (queryUsed) vs what the UI showed
  S7 results               relevance of who came back, per-candidate "should this appear?"
  S8 scoring + paging      score formula, rank order, cross-page duplicate people
  S9 determinism           same query twice: same gate decision, same filters

Deterministic failures are defects. Judge verdicts are advisory and always carry
their reasoning into the workbook so a human can validate them.
"""
import argparse
import copy
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import e2e_checks as CH
import rcrm_auth
import rcrm_client
import sourcing_client as SC

CASES_PATH = os.path.join(config.GOLDEN_DIR, "e2e_cases.json")
JUDGE = os.environ.get("E2E_JUDGE", "1") != "0"
JUDGE_TOP_K = int(os.environ.get("E2E_JUDGE_K", "3"))     # candidates judged per case
PAGES = int(os.environ.get("E2E_PAGES", "2"))             # scored pages pulled per case
WORKERS = int(os.environ.get("E2E_WORKERS", "4"))
DETERMINISM_FAMILIES = {"safety"}                          # families re-run to check stability

if JUDGE:
    import e2e_llm as JL


def _ck_path(run_id):
    return os.path.join(config.OUTPUT_DIR, f"e2e_{run_id}.jsonl")


def _load_cases():
    with open(CASES_PATH) as fh:
        return json.load(fh)["cases"]


def _done_ids(path):
    if not os.path.exists(path):
        return set()
    out = set()
    with open(path) as fh:
        for line in fh:
            try:
                out.add(json.loads(line)["case_id"])
            except (ValueError, KeyError):
                continue
    return out


def _qtext(q):
    return q.get("question") if isinstance(q, dict) else str(q)


def _crit_for_search(criteria, cap=10):
    """Criteria as the search endpoint wants them: {text, weight}, <=10, weight 1-10."""
    out = []
    for c in (criteria or [])[:cap]:
        t, w = c.get("text"), c.get("weight")
        if not t:
            continue
        try:
            w = max(1, min(10, int(w)))
        except (TypeError, ValueError):
            w = 5
        out.append({"text": str(t)[:150], "weight": w})
    return out


def _crit_for_c2f(criteria):
    """Criteria as 1.2 wants them: id is REQUIRED or Neptune 500s."""
    out = []
    for i, c in enumerate(criteria or [], 1):
        out.append({"id": c.get("id") or f"c{i}", "text": c.get("text"),
                    "weight": c.get("weight")})
    return out


def _slim(c):
    ks = c.get("keySkills")
    if isinstance(ks, list):
        ks = ", ".join(str(x) for x in ks)
    name = " ".join(x for x in (c.get("firstname"), c.get("lastname")) if x)
    if not name:
        name = c.get("name") or c.get("fullName") or (c.get("linkedinUrl") or "").rstrip("/").split("/")[-1] or "(unnamed)"
    return {"name": name,
            "position": c.get("position"), "company": c.get("company"),
            "city": c.get("city"), "state": c.get("state"), "country": c.get("country"),
            "keySkills": (ks or "")[:800], "workexpyr": c.get("workexpyr"),
            "linkedinUrl": c.get("linkedinUrl"), "candidateIndex": c.get("candidateIndex"),
            "matchScore": c.get("matchScore"), "matchLabel": c.get("matchLabel"),
            "matchAnalysis": c.get("matchAnalysis"),
            "description": (str(c.get("description") or ""))[:1200]}


# ── the pipeline ─────────────────────────────────────────────────────────────
def run_case(case, token):
    cid, query, db = case["id"], case["query"], case.get("db", 2)
    exp = case.get("expect") or {}
    rec = {"case_id": cid, "family": case.get("family"), "size": case.get("size"), "db": db,
           "env": config.RCRM_ENV, "neptune": config.RCRM_NEPTUNE_URL,
           "query": query, "note": case.get("note"), "expect": exp,
           "fails": [], "judges": {}, "stages": {}, "ts": time.time()}

    def add(fails):
        for f in fails or []:
            f.setdefault("case_id", cid)
            rec["fails"].append(f)

    def bug(code, field, detail, sev="high", stage="pipeline"):
        add([{"code": code, "field": field, "detail": detail, "severity": sev, "stage": stage}])

    # ---------- S1: config, cold ----------
    t0 = time.time()
    try:
        st, cfg = rcrm_client.get_configuration(
            ai_search=query if db == 2 else None,
            job_slug=(config.JOB_ANCHOR if db == 1 else None),
            jd_text=(query if db == 1 else None),
            database_type=db, token=token, timeout=150)
    except Exception as e:  # noqa: BLE001
        rec["stages"]["config"] = {"http": "ERROR", "error": f"{type(e).__name__}: {e}"}
        bug("TRANSPORT", "config", f"{type(e).__name__}: {e}")
        return rec
    cfg = cfg if isinstance(cfg, dict) else {"_raw": cfg}
    questions = [_qtext(q) for q in (cfg.get("questions") or []) if _qtext(q)]
    criteria = cfg.get("criteria") or []
    cfg_filters = ((cfg.get("filters") or {}).get("llmFilters")) or {}
    cfg_norm = CH.normalize_filters(cfg_filters)
    rec["stages"]["config"] = {"http": st, "state": cfg.get("status"), "secs": round(time.time() - t0, 1),
                               "questions": questions, "criteria": criteria,
                               "llmFilters": cfg_filters,
                               "translatedFilters": (cfg.get("filters") or {}).get("translatedFilters"),
                               "error": None if st == 200 else json.dumps(cfg, default=str)[:600]}

    ec = exp.get("config") or {}
    if "http" in ec and st != ec["http"]:
        bug("WRONG-STATUS", "config", f"expected HTTP {ec['http']}, got {st}", "high", "config")
    if "http_in" in ec and st not in ec["http_in"]:
        bug("GATE-MISS", "config",
            f"expected one of {ec['http_in']} (content-safety rejection), got {st}", "high", "config")
    if st != 200:
        return rec                      # nothing downstream to test
    if not criteria:
        bug("NO-CRITERIA", "config", "config returned 200 with zero criteria", "high", "config")
    if ec.get("min_criteria") and len(criteria) < ec["min_criteria"]:
        bug("THIN-CRITERIA", "config",
            f"only {len(criteria)} criteria, expected >= {ec['min_criteria']}", "medium", "config")
    if len(criteria) > 10:
        bug("CAP-BREACH", "config", f"{len(criteria)} criteria exceeds the documented cap of 10",
            "medium", "config")
    for c in criteria:
        if len(str(c.get("text") or "")) > 150:
            bug("CAP-BREACH", "criteria", f"criterion text {len(str(c['text']))} chars > 150 cap",
                "low", "config")

    # ---------- S2: questions ----------
    if questions and JUDGE:
        rec["judges"]["questions"] = JL.judge_questions(query, questions)
        jq = rec["judges"]["questions"]
        if jq.get("redundant"):
            bug("REDUNDANT-QUESTION", "questions",
                f"asks what the query already states: {jq['redundant']}", "medium", "questions")
        if jq.get("over_asking") == "yes" and len(questions) >= 4:
            bug("OVER-ASKING", "questions",
                f"{len(questions)} questions for this query; judge flags over-asking", "medium", "questions")
        if jq.get("missed"):
            bug("MISSED-AMBIGUITY", "questions", f"did not ask about: {jq['missed']}", "low", "questions")
    if JUDGE and criteria:
        rec["judges"]["criteria"] = JL.judge_criteria(query, criteria)
        jc = rec["judges"]["criteria"]
        if jc.get("missing"):
            bug("CRITERIA-MISSING", "criteria", f"query requirements with no criterion: {jc['missing']}",
                "medium", "criteria")
        if jc.get("invented"):
            bug("CRITERIA-INVENTED", "criteria", f"criteria not supported by the query: {jc['invented']}",
                "medium", "criteria")
        if jc.get("misweighted"):
            bug("CRITERIA-MISWEIGHTED", "criteria", f"{jc['misweighted']}", "medium", "criteria")

    # ---------- S3: answers ----------
    # Everything downstream runs on the FINALISED criteria — i.e. what the user would
    # have after working through the clarifying step, which is the real product flow.
    final_criteria = criteria
    if questions:
        raw_qs = [q for q in (cfg.get("questions") or []) if _qtext(q)]
        answers = []
        for q in raw_qs:
            if isinstance(q, dict):
                opts = q.get("options") or []
                answers.append({"question": q.get("question"),
                                "answer": (opts[-1] if opts else "Yes")})
        # 3a: explicitly skip every question
        try:
            st_skip, cfg_skip = rcrm_client.get_configuration(
                ai_search=query if db == 2 else None,
                job_slug=(config.JOB_ANCHOR if db == 1 else None),
                jd_text=(query if db == 1 else None),
                database_type=db, answers=[], token=token, timeout=150)
        except Exception as e:  # noqa: BLE001
            st_skip, cfg_skip = "ERROR", {"error": str(e)[:200]}
        cfg_skip = cfg_skip if isinstance(cfg_skip, dict) else {}
        rec["stages"]["config_skip"] = {"http": st_skip, "state": cfg_skip.get("state") or cfg_skip.get("status"),
                                        "criteria": cfg_skip.get("criteria")}
        if st_skip == 200 and (cfg_skip.get("status") == "AWAITING_ANSWERS"):
            bug("SKIP-STALLS", "answers",
                "skipping all questions leaves the run in AWAITING_ANSWERS — the user cannot proceed "
                "without answering", "medium", "answers")

        # 3b: answer everything
        if answers:
            try:
                st_a, cfg_a = rcrm_client.get_configuration(
                    ai_search=query if db == 2 else None,
                    job_slug=(config.JOB_ANCHOR if db == 1 else None),
                    jd_text=(query if db == 1 else None),
                    database_type=db, answers=answers, token=token, timeout=150)
            except Exception as e:  # noqa: BLE001
                st_a, cfg_a = "ERROR", {"error": str(e)[:200]}
            cfg_a = cfg_a if isinstance(cfg_a, dict) else {}
            a_filters = ((cfg_a.get("filters") or {}).get("llmFilters")) or {}
            rec["stages"]["config_answered"] = {
                "http": st_a, "state": cfg_a.get("status"), "answers_sent": answers,
                "criteria": cfg_a.get("criteria"), "llmFilters": a_filters}
            if st_a == 200:
                if cfg_a.get("criteria"):
                    final_criteria = cfg_a["criteria"]
                if cfg_a.get("status") == "AWAITING_ANSWERS":
                    bug("RE-ASKS", "answers",
                        "every question was answered but the config still reports AWAITING_ANSWERS",
                        "high", "answers")
                before_txt = json.dumps({"criteria": criteria, "filters": cfg_filters}, default=str)
                after_txt = json.dumps({"criteria": cfg_a.get("criteria"), "filters": a_filters}, default=str)
                if before_txt == after_txt:
                    bug("ANSWER-NO-EFFECT", "answers",
                        "answering every clarifying question changed neither criteria nor filters",
                        "high", "answers")
                if JUDGE:
                    rec["judges"]["answers"] = JL.judge_answer_influence(
                        query, answers,
                        {"criteria": criteria, "filters": cfg_filters},
                        {"criteria": cfg_a.get("criteria"), "filters": a_filters})
                    ja = rec["judges"]["answers"]
                    if ja.get("influence") in ("wrong", "no_change"):
                        bug("ANSWER-MISPLACED", "answers",
                            f"answer influence judged '{ja.get('influence')}' — landed in "
                            f"{ja.get('landed_in')!r}, expected {ja.get('expected_in')!r}",
                            "high", "answers")
            # 3c: does the SEARCH endpoint accept the same answer shape the config produced?
            if db == 2:
                st_ca, res_ca = SC.scored_search(ai_search=query, page=1, token=token, timeout=120,
                                                 clarifying_answers=answers)
                rec["stages"]["search_with_answers"] = {"http": st_ca,
                                                        "error": (res_ca or {}).get("error", "")[:300]}
                if st_ca != 200:
                    bug("ANSWER-CONTRACT", "answers",
                        "the {question, answer} objects the config endpoint returns are rejected by "
                        f"the search endpoint (HTTP {st_ca}) — answers cannot reach retrieval",
                        "high", "answers")

    # ---------- S4: criteria -> filters ----------
    c2f_norm = None
    try:
        st_c, c2f = rcrm_client.criteria_to_filters(
            ai_search=query if db == 2 else None,
            job_slug=(config.JOB_ANCHOR if db == 1 else None),
            jd_text=(query if db == 1 else None),
            database_type=db, criteria=_crit_for_c2f(final_criteria), token=token, timeout=180)
    except Exception as e:  # noqa: BLE001
        st_c, c2f = "ERROR", {"error": f"{type(e).__name__}: {e}"}
    c2f = c2f if isinstance(c2f, dict) else {}
    c2f_filters = ((c2f.get("filters") or {}).get("llmFilters")) or {}
    rec["stages"]["c2f"] = {"http": st_c, "criteria_used": final_criteria, "llmFilters": c2f_filters,
                            "translatedFilters": (c2f.get("filters") or {}).get("translatedFilters")}
    if st_c != 200:
        bug("C2F-FAIL", "filters", f"criteria->filters returned HTTP {st_c}", "high", "c2f")
    else:
        c2f_norm = CH.normalize_filters(c2f_filters)
        add(CH.check_filters(c2f_norm, exp.get("filters"), "c2f"))
        add(CH.check_unsupported(query, c2f_norm, case.get("unsupported")))
        if JUDGE:
            rec["judges"]["filters"] = JL.judge_filters(query, final_criteria, c2f_filters)
            jf = rec["judges"]["filters"]
            if jf.get("wrong_field"):
                bug("FIELD-CLASSIFICATION", "filters", f"{jf['wrong_field']}", "high", "c2f")
            if jf.get("over_constrained") == "yes":
                bug("OVER-CONSTRAINED", "filters", jf.get("over_constraint_detail", ""), "medium", "c2f")
            if jf.get("invented_values"):
                bug("FILTER-INVENTED", "filters", f"{jf['invented_values']}", "medium", "c2f")

    # ---------- S5: mutations ----------
    if c2f_norm and final_criteria:
        ranked = sorted(_crit_for_c2f(final_criteria), key=lambda c: -(c.get("weight") or 0))
        muts = []
        top = ranked[0]
        if len(ranked) >= 2:      # removing the only criterion is not a legal product state
            muts.append(("remove_top", f"removed criterion {top['text']!r}",
                         [c for c in ranked if c["id"] != top["id"]], top))
        low = dict(top, weight=2)
        muts.append(("lower_top_weight", f"lowered {top['text']!r} from weight {top['weight']} to 2",
                     [low] + [c for c in ranked if c["id"] != top["id"]], top))
        tail = ranked[-1]
        if (tail.get("weight") or 0) < CH.MUST_HAVE_WEIGHT and tail["id"] != top["id"]:
            hi = dict(tail, weight=9)
            muts.append(("raise_low_weight", f"raised {tail['text']!r} from weight {tail['weight']} to 9",
                         [hi] + [c for c in ranked if c["id"] != tail["id"]], tail))

        rec["stages"]["mutations"] = []
        for kind, desc, new_crit, target in muts:
            try:
                st_m, m = rcrm_client.criteria_to_filters(
                    ai_search=query if db == 2 else None,
                    job_slug=(config.JOB_ANCHOR if db == 1 else None),
                    jd_text=(query if db == 1 else None),
                    database_type=db, criteria=new_crit, token=token, timeout=180)
            except Exception as e:  # noqa: BLE001
                st_m, m = "ERROR", {"error": str(e)[:200]}
            m = m if isinstance(m, dict) else {}
            m_filters = ((m.get("filters") or {}).get("llmFilters")) or {}
            entry = {"kind": kind, "desc": desc, "http": st_m, "criteria": new_crit,
                     "llmFilters": m_filters}
            if st_m == 200:
                m_norm = CH.normalize_filters(m_filters)
                if kind == "remove_top":
                    f = CH.check_removal(c2f_norm, m_norm, target["text"],
                                         [c["text"] for c in new_crit], query)
                    add([dict(x, case_id=cid, mutation=kind) for x in f])
                    entry["fails"] = f
                else:
                    w = 2 if kind == "lower_top_weight" else 9
                    others = [c["text"] for c in new_crit if c["id"] != target["id"]]
                    f = CH.check_weight_move(c2f_norm, m_norm, target["text"], w, others)
                    add([dict(x, case_id=cid, mutation=kind) for x in f])
                    entry["fails"] = f
                ratio, added_v, dropped_v = CH.churn(
                    c2f_norm, m_norm, ignore_tokens=CH.distinctive_tokens(target["text"]))
                entry.update({"churn": ratio, "added": added_v[:12], "dropped": dropped_v[:12]})
                if ratio > 0.5:
                    bug("UNSTABLE-EDIT", "mutation",
                        f"{desc}: {ratio:.0%} of unrelated filter values changed "
                        f"(added {added_v[:6]}, dropped {dropped_v[:6]}) — a local edit "
                        "regenerated the whole query", "medium", "mutation")
                if JUDGE and kind == "remove_top":
                    entry["judge"] = JL.judge_mutation(query, desc, final_criteria, c2f_filters,
                                                       new_crit, m_filters)
                    if entry["judge"].get("change_correct") == "no":
                        bug("MUTATION-WRONG", "mutation",
                            f"{desc}: {entry['judge'].get('reasoning', '')[:300]}", "high", "mutation")
            else:
                bug("C2F-FAIL", "mutation", f"{desc}: HTTP {st_m}", "high", "mutation")
            rec["stages"]["mutations"].append(entry)

    # ---------- S6/S7/S8: search + results ----------
    if db != 2:
        rec["stages"]["search"] = {"skipped": "My Database retrieval runs through Aries on a seeded "
                                              "pool — covered by the db=1 suite, not this endpoint"}
        return rec

    search_crit = _crit_for_search(final_criteria)
    hid = None
    if PAGES > 1:
        _, hid, _ = SC.create_history(query, token)
    all_c, pages_meta, first_meta = [], [], None
    for p in range(1, PAGES + 1):
        st_s, res = SC.scored_search(ai_search=query, matching_criteria=search_crit, page=p,
                                     token=token, history_id=hid, timeout=200)
        meta = res.get("metadata") or {}
        if p == 1:
            first_meta = meta
        pages_meta.append({"page": p, "http": st_s, "n": len(res.get("candidates") or []),
                           "total": meta.get("totalResults"),
                           "scoringApplied": res.get("scoring_applied"),
                           "message": res.get("message"), "error": (res.get("error") or "")[:400]})
        if st_s != 200:
            if exp.get("search_http") and st_s == exp["search_http"]:
                pages_meta[-1]["expected_failure"] = True
            else:
                bug("SEARCH-FAIL", "search", f"page {p} HTTP {st_s}: {(res.get('error') or '')[:300]}",
                    "high", "search")
            break
        if res.get("error"):
            bug("SSE-ERROR", "search",
                f"page {p} returned HTTP 200 but the stream carried an error: {res['error'][:300]}",
                "medium", "search")
        all_c += [_slim(c) for c in (res.get("candidates") or [])]

    if hid:
        SC.delete_history(hid, token)

    try:
        qu = json.loads((first_meta or {}).get("queryUsed") or "{}")
    except (ValueError, TypeError):
        qu = {}
    search_norm = CH.normalize_filters(qu)
    rec["stages"]["search"] = {"pages": pages_meta, "queryUsed": qu,
                               "total": (first_meta or {}).get("totalResults"),
                               "criteria_sent": search_crit, "candidates": all_c}

    if exp.get("search_http") and pages_meta and pages_meta[0]["http"] == exp["search_http"]:
        return rec                                    # contract case: the 4xx WAS the assertion

    if qu:
        add(CH.check_filters(search_norm, exp.get("filters"), "search_queryUsed"))
        add(CH.check_unsupported(query, search_norm, case.get("unsupported")))
        if c2f_norm:
            add(CH.compare_stages(c2f_norm, search_norm))
    add(CH.check_results(all_c, first_meta, exp.get("results")))

    # scoring + pagination invariants
    scored = [c for c in all_c if c.get("matchScore") is not None]
    prev, seen = None, {}
    for c in scored:
        ms = c["matchScore"]
        if prev is not None and ms > prev + 0.001:
            bug("RANK-DISORDER", "scoring",
                f"rank {c.get('candidateIndex')} scores {ms} after {prev} — results are not "
                "ordered by match score", "high", "scoring")
        prev = ms
        ma = c.get("matchAnalysis") or []
        try:
            den = sum(float(r.get("weight")) for r in ma)
            if den:
                wavg = sum(float(r.get("score")) * float(r.get("weight")) for r in ma) / den
                if abs(wavg - float(ms)) > config.SOURCING_SCORE_TOL:
                    bug("SCORE-FORMULA", "scoring",
                        f"{c.get('name')}: matchScore {ms} != weighted average {wavg:.1f} of the "
                        "per-criterion scores", "high", "scoring")
        except (TypeError, ValueError):
            pass
        if len(ma) and len(ma) != len(search_crit):
            bug("CRITERIA-COVERAGE", "scoring",
                f"{c.get('name')}: {len(ma)} criteria scored, {len(search_crit)} were supplied",
                "medium", "scoring")
        key = c.get("linkedinUrl") or f"{c.get('name')}|{c.get('company')}"
        if key in seen:
            bug("DUPLICATE-PERSON", "pagination",
                f"{c.get('name')} appears at {seen[key]} and again at rank {c.get('candidateIndex')}",
                "high", "pagination")
        else:
            seen[key] = f"rank {c.get('candidateIndex')}"

    # per-candidate adjudication: top of the list plus the tail
    if JUDGE and all_c:
        picks = all_c[:JUDGE_TOP_K] + ([all_c[-1]] if len(all_c) > JUDGE_TOP_K else [])
        rec["judges"]["candidates"] = []
        for c in picks:
            v = JL.judge_candidate(query, search_crit, c)
            v["candidate"] = c.get("name")
            v["position"] = c.get("position")
            v["rank"] = c.get("candidateIndex")
            v["matchScore"] = c.get("matchScore")
            rec["judges"]["candidates"].append(v)
            if v.get("should_appear") == "no":
                bug("IRRELEVANT-RESULT", "results",
                    f"rank {c.get('candidateIndex')} {c.get('name')} ({c.get('position')}) judged "
                    f"irrelevant: {'; '.join(v.get('mismatch_reasons') or [])[:250]}", "high", "results")
            if v.get("grounding") == "fabricated":
                bug("FABRICATED-REASONING", "scoring",
                    f"{c.get('name')}: scoring reasoning cites {v.get('fabricated_claims')} not in "
                    "the profile", "high", "scoring")
            if v.get("score_verdict") in ("too_high", "too_low"):
                gap = abs((v.get("judge_score") or 0) - (c.get("matchScore") or 0))
                if gap >= 25:
                    bug("SCORE-DISAGREEMENT", "scoring",
                        f"{c.get('name')}: system {c.get('matchScore')} vs judge "
                        f"{v.get('judge_score')} ({v.get('score_verdict')})", "medium", "scoring")

    # ---------- S9: determinism ----------
    if case.get("family") in DETERMINISM_FAMILIES:
        st2, cfg2 = rcrm_client.get_configuration(ai_search=query, database_type=2,
                                                  token=token, timeout=150)
        rec["stages"]["determinism"] = {"http_1": st, "http_2": st2,
                                        "state_1": cfg.get("status"),
                                        "state_2": (cfg2 or {}).get("status")}
        if st2 != st:
            bug("NON-DETERMINISTIC", "config",
                f"identical query returned HTTP {st} then {st2} — the content-safety gate is "
                "not deterministic", "high", "determinism")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--family")
    ap.add_argument("--case")
    ap.add_argument("--rebuild", action="store_true", help="build the workbook from the checkpoint only")
    ap.add_argument("--fresh", action="store_true", help="ignore the existing checkpoint")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    ck = _ck_path(args.run_id)
    import e2e_report

    if args.rebuild:
        rows = [json.loads(l) for l in open(ck) if l.strip()]
        path = e2e_report.build(rows, args.run_id)
        print(f"workbook -> {path}")
        return

    cases = _load_cases()
    if args.family:
        cases = [c for c in cases if c.get("family") == args.family]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if args.fresh and os.path.exists(ck):
        os.rename(ck, ck + f".bak{int(time.time())}")
    done = _done_ids(ck)
    todo = [c for c in cases if c["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"env={config.RCRM_ENV}  neptune={config.RCRM_NEPTUNE_URL}\n"
          f"cases: {len(todo)} to run ({len(done)} already in checkpoint)  judge={'on' if JUDGE else 'off'}  "
          f"pages={PAGES}  workers={args.workers}")
    if not todo:
        if not os.path.exists(ck):
            print(f"no cases matched (--case/--family filter) and no checkpoint at {ck}")
            return
        print("nothing to run — rebuilding workbook")
        rows = [json.loads(l) for l in open(ck) if l.strip()]
        print(f"workbook -> {e2e_report.build(rows, args.run_id)}")
        return

    token = config.RCRM_ALBATROSS_TOKEN or rcrm_auth.login()["token"]
    if any(c.get("db") == 1 for c in todo) and not config.JOB_ANCHOR:
        config.JOB_ANCHOR = rcrm_auth.get_job_anchor(token)
        print(f"db=1 job anchor: {config.JOB_ANCHOR}")

    lock = __import__("threading").Lock()
    fh = open(ck, "a")
    t0 = time.time()

    def work(c):
        try:
            return run_case(c, token)
        except Exception:  # noqa: BLE001
            return {"case_id": c["id"], "family": c.get("family"), "query": c.get("query"),
                    "fails": [{"code": "HARNESS-ERROR", "field": "-", "severity": "high",
                               "stage": "harness", "detail": traceback.format_exc()[-900:]}],
                    "judges": {}, "stages": {}}

    n = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, c): c for c in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            n += 1
            with lock:
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                fh.flush()
            hard = sum(1 for f in rec["fails"] if f.get("severity") == "high")
            print(f"[{n}/{len(todo)}] {rec['case_id']:<28} fails={len(rec['fails'])} (high={hard})"
                  + (f"  {rec['fails'][0]['code']}" if rec["fails"] else ""))
    fh.close()
    print(f"\nran {n} cases in {round(time.time() - t0)}s -> {ck}")
    rows = [json.loads(l) for l in open(ck) if l.strip()]
    print(f"workbook -> {e2e_report.build(rows, args.run_id)}")


if __name__ == "__main__":
    sys.exit(main())

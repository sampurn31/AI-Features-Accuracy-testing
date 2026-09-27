"""
db=1 (My Database) Aries scored-search client.

  1.3  POST {aries}/advanced-search/candidates/ai-matching/search/get   -> scored candidates
  1.5  POST {aries}/advanced-search/candidates/ai-matching/search/count/get -> run status

Both authenticate with the Albatross login JWT (Bearer). Scoring is INLINE but ASYNC: the
first 1.3 call returns candidates with matchScore=null / reasoningStatus="pending"; scores
land seconds later. `wait_for_scores` polls 1.5 `overallStatus` to a terminal state then
re-reads 1.3 (cached) to pick up the populated scores.

`_normalize_candidate` maps the raw db=1 shape onto the internal shape the judges + checks
expect (criterionText/weight/score/reasoning, matchScore, matchLabel) — with NO `satisfied`
field (that is a Global-only artifact; DRIFT #2).
"""
import json
import time

import requests

import config

_TERMINAL = {"success", "partial", "fail", "completed", "error"}


def _post(path, job_slug, token, timeout=120, params=None):
    url = f"{config.RCRM_ARIES_URL}/{path}"
    body = {"jobSlug": job_slug, "databaseType": config.DB_MY_DATABASE, "isRefresh": False}
    resp = requests.post(url, params=(params or {}), data=json.dumps(body),
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                  "Accept": "application/json"}, timeout=timeout)
    try:
        payload = resp.json()
    except ValueError:
        return resp.status_code, {"_raw": resp.text[:600]}
    return resp.status_code, payload


def _normalize_candidate(raw, rank):
    """Map one raw db=1 candidate (~150 fields + 3 appended scoring fields) to the internal
    shape. Profile-evidence fields are best-effort from the response; the runner overlays the
    authoritative authored profile + ground-truth label from the ledger."""
    ma = raw.get("matchAnalysis") or {}
    crits = []
    for c in (ma.get("criteria") or []):
        crits.append({"criterionText": c.get("text"), "weight": c.get("weight"),
                      "score": c.get("score"), "reasoning": c.get("reason")})
    skill = raw.get("skill") or ""
    return {
        "id": raw.get("id"), "slug": raw.get("slug"),
        "candidateIndex": rank,
        "matchScore": raw.get("matchScore"),
        "reasoningStatus": raw.get("reasoningStatus"),
        "matchLabel": ma.get("label"),
        "overallReason": ma.get("overallReason"),
        "matchAnalysis": crits if crits else (None if ma == {} else []),
        # best-effort evidence (runner overlays ledger profile, which is authoritative)
        "firstname": raw.get("first_name"), "lastname": raw.get("last_name"),
        "position": raw.get("position") or raw.get("current_designation"),
        "company": raw.get("current_organization"),
        "city": raw.get("city"), "state": raw.get("state"), "country": raw.get("country"),
        "keySkills": [s.strip() for s in skill.split(",") if s.strip()] if skill else [],
        "workexpyr": raw.get("work_ex_year"),
        "description": raw.get("candidate_summary"),
        "experience": raw.get("work_history") or raw.get("workHistory") or [],
        "education": raw.get("education_history") or [],
    }


def matching_search(job_slug, token, page=1, size=100, timeout=180):
    """1.3 — returns (status, {candidates:[normalized], error, message}). data is a plain array."""
    status, payload = _post(config.MATCH_SEARCH_PATH, job_slug, token, timeout,
                            params={"page": page, "size": size})
    if status != 200:
        return status, {"candidates": [], "error": json.dumps(payload)[:500], "message": None}
    data = payload.get("data")
    if not isinstance(data, list):
        return status, {"candidates": [], "error": None,
                        "message": (payload.get("meta") or {}).get("message")}
    cands = [_normalize_candidate(c, i) for i, c in enumerate(data, 1 + (page - 1) * size)]
    return status, {"candidates": cands, "error": None, "message": None}


def matching_count(job_slug, token, timeout=60):
    """1.5 — returns (status, {totalCount, overallStatus, canRefresh, jdUpdated, refreshAvailableAt})."""
    status, payload = _post(config.MATCH_COUNT_PATH, job_slug, token, timeout)
    d = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    return status, {"totalCount": d.get("totalCount"), "overallStatus": d.get("overallStatus"),
                    "canRefresh": d.get("canRefresh"), "jdUpdated": d.get("jdUpdated"),
                    "refreshAvailableAt": d.get("refreshAvailableAt"),
                    "error": None if status == 200 else json.dumps(payload)[:400]}


def wait_for_scores(job_slug, token, sleeper=time.sleep):
    """Poll 1.5 to a terminal overallStatus (or timeout), then re-read 1.3 for populated scores.
    Returns (candidates, count_data, polls, timed_out)."""
    status, first = matching_search(job_slug, token)   # kick off + first (likely pending) read
    polls, waited, count = 0, 0, {}
    while waited <= config.DB1_POLL_MAX:
        cstatus, count = matching_count(job_slug, token)
        polls += 1
        st = (count.get("overallStatus") or "").lower()
        if cstatus == 200 and st in _TERMINAL:
            break
        sleeper(config.DB1_POLL_INTERVAL)
        waited += config.DB1_POLL_INTERVAL
    timed_out = ((count.get("overallStatus") or "").lower() not in _TERMINAL)
    fstatus, final = matching_search(job_slug, token)   # cached read with populated scores
    cands = final.get("candidates") or first.get("candidates") or []
    return cands, count, polls, timed_out

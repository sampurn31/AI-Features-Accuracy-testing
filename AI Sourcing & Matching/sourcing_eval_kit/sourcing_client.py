"""
CURRENT-implementation client for the AI Sourcing V3 scored search (Global / db=2).

  POST {neptune}/v1/ai-sourcing-v3/search?page=1
  body: { aiSearch | keywordSearch,  matchingCriteria:[{text,weight}],  jobId?, clarifyingAnswers? }

The response is an SSE stream (one JSON object per line, `{"data": {...}}`):
  metadata chunk  -> {type:"metadata", scoringApplied, totalResults, totalCandidates, ...}
  candidate chunk -> {type:"candidate", candidate:{...profile...}, candidateIndex,
                      matchScore, matchLabel, matchAnalysis:[{criterionText,weight,satisfied,reasoning}]}
  complete chunk  -> {type:"complete"}   (may carry a "message" like "No candidates found")
  error chunk     -> {type:"error", ...} (mid-stream failure)

Scoring is applied on pages 1-4 when matchingCriteria (and/or jobId) is supplied; the
same retrieved top-100 is re-scored, so holding aiSearch fixed and varying only
matchingCriteria is a clean metamorphic test.
"""
import json

import requests

import config

# The candidate-profile fields we keep for the judge / for the reviewer (the raw
# profile has many more; these are the ones the scoring reasoning should be grounded in).
_PROFILE_KEYS = ("id", "firstname", "lastname", "position", "company", "city", "state",
                 "country", "keySkills", "workexpyr", "linkedinUrl", "description",
                 "experience", "education", "highlightTerms")


def _slim_candidate(chunk):
    """Flatten one candidate chunk into {profile fields..., matchScore, matchLabel, matchAnalysis, candidateIndex}."""
    prof = chunk.get("candidate") or {}
    slim = {k: prof.get(k) for k in _PROFILE_KEYS}
    slim["candidateIndex"] = chunk.get("candidateIndex")
    slim["matchScore"] = chunk.get("matchScore")
    slim["matchLabel"] = chunk.get("matchLabel")
    slim["matchAnalysis"] = chunk.get("matchAnalysis")
    return slim


def create_history(ai_search, token, timeout=60):
    """3.7 POST /ai-sourcing-v3/history {query_type:0(AI_SEARCH), query_data:<aiSearch>}.
    Pagination of a SCORED run (page>1) requires this row's id passed back as historyId.
    Returns (status, history_id_or_None, error_or_None)."""
    url = f"{config.RCRM_NEPTUNE_URL}/ai-sourcing-v3/history"
    try:
        resp = requests.post(
            url, data=json.dumps({"query_type": 0, "query_data": ai_search[:1000]}),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Accept": "application/json"}, timeout=timeout)
        data = (resp.json() or {}).get("data") or {}
        hid = data.get("id")
        return resp.status_code, hid, (None if resp.status_code == 200 and hid
                                       else resp.text[:300])
    except (requests.RequestException, ValueError) as e:
        return "ERROR", None, f"{type(e).__name__}: {e}"


def delete_history(history_id, token, timeout=60):
    """3.4 DELETE /ai-sourcing-v3/history/{id} — cleanup so scale runs leave no rows behind."""
    url = f"{config.RCRM_NEPTUNE_URL}/ai-sourcing-v3/history/{history_id}"
    try:
        resp = requests.delete(url, headers={"Authorization": f"Bearer {token}",
                                             "Accept": "application/json"}, timeout=timeout)
        return resp.status_code
    except requests.RequestException:
        return "ERROR"


def scored_search(ai_search=None, keyword_search=None, matching_criteria=None,
                  job_id=None, clarifying_answers=None, page=1, token=None, timeout=180,
                  history_id=None):
    """
    Call the scored search and parse the SSE stream.

    Returns (http_status, result) where result is:
      { "metadata": {...} | None,
        "scoring_applied": bool,
        "candidates": [ slim_candidate, ... ],   # in the order streamed (== rank order)
        "message": <complete-chunk message or None>,
        "error": <str or None> }                 # transport / 4xx-5xx / mid-stream error

    aiSearch XOR keywordSearch (exactly one). `token` is the Bearer (login JWT).
    """
    if bool(ai_search) == bool(keyword_search):
        raise ValueError("Provide exactly one of ai_search / keyword_search")

    body = {}
    if ai_search:
        body["aiSearch"] = ai_search
    if keyword_search:
        body["keywordSearch"] = keyword_search
    if matching_criteria:
        body["matchingCriteria"] = matching_criteria
    if job_id is not None:
        body["jobId"] = job_id
    if clarifying_answers:
        body["clarifyingAnswers"] = clarifying_answers
    if history_id is not None:
        body["historyId"] = history_id

    bearer = token or config.RCRM_ALBATROSS_TOKEN
    url = f"{config.RCRM_NEPTUNE_URL}/{config.SOURCING_SEARCH_PATH}"
    result = {"metadata": None, "scoring_applied": False, "candidates": [],
              "message": None, "error": None}
    try:
        resp = requests.post(
            url, params={"page": page}, data=json.dumps(body),
            headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json",
                     "Accept": "application/json"},
            timeout=timeout, stream=True,
        )
    except requests.RequestException as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return "ERROR", result

    status = resp.status_code
    if status != 200:
        # error envelope is a single JSON body, not a stream
        try:
            result["error"] = json.dumps(resp.json(), ensure_ascii=False)[:600]
        except ValueError:
            result["error"] = resp.text[:600]
        return status, result

    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                data = json.loads(raw).get("data", {})
            except json.JSONDecodeError:
                result["error"] = (result["error"] or "") + f"[non-JSON line] {raw[:120]} "
                continue
            ctype = data.get("type")
            if ctype == "metadata":
                result["metadata"] = data
                result["scoring_applied"] = bool(data.get("scoringApplied"))
            elif ctype == "candidate":
                result["candidates"].append(_slim_candidate(data))
            elif ctype == "complete":
                if data.get("message"):
                    result["message"] = data.get("message")
            elif ctype == "error":
                result["error"] = (result["error"] or "") + json.dumps(data, ensure_ascii=False)[:400]
    except requests.RequestException as e:
        # stream died mid-read (ChunkedEncodingError etc.) — surface it as a normal,
        # retryable error instead of blowing up the whole variation
        result["error"] = (result["error"] or "") + f"[stream] {type(e).__name__}: {e}"
    return status, result

"""
Calls the CURRENT implementation — RCRM job-microservice 1.1
POST /v2/ai-matching/configuration — and returns its questions/criteria/filters.

This is "Output A" in the comparison. It uses the account's real pipeline
(Neptune behind job-microservice), so it needs a flag-enabled account token.
"""
import json
import requests

import config


def get_configuration(job_slug=None, ai_search=None, database_type=config.DB_MY_DATABASE,
                      jd_text=None, answers=None, token=None, timeout=120):
    """
    Returns (http_status, data_dict). `data_dict` is the `data` object of the
    response (status/questions/criteria/jdText/filters) or the raw body on error.
    Exactly one of job_slug / ai_search should be supplied. `token` overrides
    config.RCRM_ALBATROSS_TOKEN (the harness passes the login token).
    """
    body = {"databaseType": int(database_type)}
    if job_slug:
        body["jobSlug"] = job_slug
    if ai_search:
        body["aiSearch"] = ai_search
    if jd_text:
        body["jdText"] = jd_text
    if answers:
        body["answers"] = answers

    bearer = token or config.RCRM_ALBATROSS_TOKEN
    url = f"{config.RCRM_JOB_SERVICE_URL}/{config.CONFIG_PATH}"
    resp = requests.post(
        url,
        data=json.dumps(body),
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except ValueError:
        return resp.status_code, {"_raw": resp.text}

    # Unwrap the { meta, data } envelope when present.
    if isinstance(payload, dict) and "data" in payload:
        return resp.status_code, payload["data"]
    return resp.status_code, payload


def criteria_to_filters(ai_search=None, job_slug=None, database_type=config.DB_GLOBAL_DATABASE,
                        criteria=None, jd_text=None, token=None, timeout=120,
                        is_save=False, filters=None, questions=None):
    """
    1.2: send finalized weighted criteria (+ optional JD/query context) and get back the
    generated filters (the "final query"). Returns (http_status, data_dict) with
    criteria / filters{llmFilters,translatedFilters} / id.
    `criteria` is a list of {"id","text","weight"} — id is REQUIRED (Neptune 500s without it).
    For db=1 the Aries search reads the PERSISTED config, so pass is_save=True (+ the filters
    and questions from the 1.1 response) to persist to ai_matching_filter_t before searching.
    """
    body = {"databaseType": int(database_type)}
    if job_slug:
        body["jobSlug"] = job_slug
    if ai_search:
        body["aiSearch"] = ai_search
    if jd_text:
        body["jdText"] = jd_text
    if criteria:
        body["criteria"] = criteria
    if filters is not None:
        body["filters"] = filters
    if questions is not None:
        body["questions"] = questions
    if is_save:
        body["isSave"] = True

    bearer = token or config.RCRM_ALBATROSS_TOKEN
    url = f"{config.RCRM_JOB_SERVICE_URL}/{config.CRITERIA_PATH}"
    resp = requests.post(
        url,
        data=json.dumps(body),
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except ValueError:
        return resp.status_code, {"_raw": resp.text}
    if isinstance(payload, dict) and "data" in payload:
        return resp.status_code, payload["data"]
    return resp.status_code, payload

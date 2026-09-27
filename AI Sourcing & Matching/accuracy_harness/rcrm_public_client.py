"""
Public-API create/delete client for db=1 (My Database) dataset seeding.

Uses the account's PUBLIC API KEY as a Bearer token on the public v1 base
(`config.RCRM_PUBLIC_API_URL`, e.g. https://<env>api.recruitcrm.net/v1). This is the
SAME surface the Java `commanFunction.createNew*` helpers hit:
  POST candidates | companies | contacts | jobs        (create)
  DELETE candidates/{slug} | jobs/{slug} | ...          (cleanup)

Every created record carries a run marker in a name/summary field so a run's data is
identifiable and cleanable; the caller ledgers every {type, slug} for exact --cleanup.
NB: seeding writes REAL records (on prod: real records + match credits) — always clean up.
"""
import json

import requests

import config


def _hdr():
    key = config.RCRM_ACCOUNT_API_KEY
    if not key:
        raise SystemExit(
            "Missing RCRM_ACCOUNT_API_KEY (the account's PUBLIC API key, Settings->API in the app).\n"
            "Seeding uses the public create API, which authenticates with the API key — not the "
            "login password and not the Albatross JWT. Add it to set_env.sh (never committed).")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Accept": "application/json"}


def _post(path, body, timeout=60):
    url = f"{config.RCRM_PUBLIC_API_URL}/{path}"
    resp = requests.post(url, data=json.dumps(body), headers=_hdr(), timeout=timeout)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"_raw": resp.text[:500]}
    return resp.status_code, payload


def _delete(path, timeout=60):
    url = f"{config.RCRM_PUBLIC_API_URL}/{path}"
    try:
        resp = requests.delete(url, headers=_hdr(), timeout=timeout)
        return resp.status_code
    except requests.RequestException:
        return "ERROR"


def _slug(payload):
    """Public-API create responses put the record under the top level or `data`."""
    if not isinstance(payload, dict):
        return None
    for src in (payload, payload.get("data") or {}):
        if isinstance(src, dict) and src.get("slug"):
            return src["slug"]
    return None


# ── candidate ────────────────────────────────────────────────────────────────

def create_candidate(fields, timeout=60):
    """fields: dict of Candidate columns (first_name,last_name,email,position,skill,
    candidate_summary,work_ex_year,relevant_experience,city,state,country,specialization,
    current_organization,qualification_id,...). Returns (status, slug, raw)."""
    status, payload = _post("candidates", fields, timeout)
    return status, _slug(payload), payload


def add_work_history(candidate_slug, work_items, timeout=60):
    """work_items: list of {title, company, work_description, start_date?, end_date?, is_current?}.
    The narrative `work_description` is the key inference lever."""
    body = [{**w, "candidate_slug": candidate_slug} for w in work_items]
    return _post("candidates/work-history/create", body, timeout)


def delete_candidate(slug):
    return _delete(f"candidates/{slug}")


# ── company / contact / job ────────────────────────────────────────────────

def create_company(name, timeout=60):
    status, payload = _post("companies", {"company_name": name}, timeout)
    return status, _slug(payload), payload


def create_contact(company_slug, first_name, last_name, email, timeout=60):
    body = {"first_name": first_name, "last_name": last_name, "email": email,
            "company_slug": company_slug}
    status, payload = _post("contacts", body, timeout)
    return status, _slug(payload), payload


def create_job(company_slug, contact_slug, name, jd_text, timeout=60):
    """The rich `job_description_text` is what 1.1 turns into weighted criteria."""
    body = {"name": name, "company_slug": company_slug, "contact_slug": contact_slug,
            "number_of_openings": 1, "job_type": 4,
            "job_description_text": jd_text, "enable_job_application_form": 1}
    status, payload = _post("jobs", body, timeout)
    return status, _slug(payload), payload


def delete_company(slug):
    return _delete(f"companies/{slug}")


def delete_contact(slug):
    return _delete(f"contacts/{slug}")


def delete_job(slug):
    return _delete(f"jobs/{slug}")

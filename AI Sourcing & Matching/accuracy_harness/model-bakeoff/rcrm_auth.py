"""
Gets an Albatross session token for the pre-fed, flag-enabled ss5 test account by
logging in — no reaper account creation (the account already has
unified_ai_sourcing_matching enabled).

  POST {albatross}/login   (form: email, password)
  -> {"status": "success", "data": {"token": "<364-char JWT, ~30-day expiry>"}, "user": {...}}

That token is the Bearer the /v2/ai-matching/configuration endpoint wants.
"""
import json

import requests

import config


def login():
    """Log in the configured test account; return {token, email, account, account_id}."""
    resp = requests.post(
        config.LOGIN_URL,
        data={"email": config.LOGIN_EMAIL, "password": config.LOGIN_PASSWORD},  # x-www-form-urlencoded
        headers={"Accept": "application/json"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Login failed (HTTP {resp.status_code}) at {config.LOGIN_URL}: {resp.text[:300]}")
    payload = resp.json() or {}
    if payload.get("status") != "success":
        raise SystemExit(f"Login rejected: {payload.get('message')}")
    token = (payload.get("data") or {}).get("token")
    if not token:
        raise SystemExit(f"Login response had no data.token: {resp.text[:300]}")
    user = payload.get("user") or {}
    return {
        "token": token,
        "email": user.get("email"),
        "account": user.get("account"),
        "account_id": user.get("accountid"),
    }


def get_job_anchor(token):
    """Fetch a real jobSlug (the db=1 access anchor) via albatross jobs search.
    db=1 config/criteria calls need a job the account can view; jdText carries the
    actual per-case JD, so any real job works as the anchor."""
    resp = requests.post(
        f"{config.ALBATROSS_URL}/jobs/search/get",
        data=json.dumps({"page": 1, "limit": 1}),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        timeout=90,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Job-anchor fetch failed (HTTP {resp.status_code}): {resp.text[:200]}")
    records = ((resp.json() or {}).get("data") or {}).get("records") or []
    for r in records:
        if r.get("slug"):
            return r["slug"]
    raise SystemExit("No job found to use as the db=1 anchor (account has no jobs).")

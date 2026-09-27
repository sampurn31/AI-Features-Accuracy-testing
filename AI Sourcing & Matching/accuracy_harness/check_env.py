"""
Pre-flight for the accuracy harness. Pure stdlib (only imports config.py, which is
also stdlib) — runs in any python3 without pip install.

Tests the REAL flow end to end, printing no secrets:
  1. ANTHROPIC_API_KEY present (+ shows env/URL config).
  2. Anthropic auth + claude-sonnet-5 access (free GET, no tokens spent).
  3. login the pre-fed ss5 test account -> Albatross token.
  4. that token drives POST /v2/ai-matching/configuration to a real 200.

Run this ON THE NETWORK where the ss5 job host resolves (corp/VPN).

Usage:
  export ANTHROPIC_API_KEY=...        # only strictly required var
  python3 check_env.py
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import config

OK, BAD, WARN = "OK ", "!! ", ".. "


def _http(method, url, headers, body=None, timeout=90):
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return None, f"URLError: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def main():
    import os
    akey = os.environ.get("ANTHROPIC_API_KEY", "")
    pasted_tok = config.RCRM_ALBATROSS_TOKEN

    print("=== 1. Config (no secrets printed) ===")
    print(f"  ANTHROPIC_API_KEY : {OK+'set' if akey else BAD+'NOT set (required)'}")
    print(f"  RCRM_ENV          : {config.RCRM_ENV}")
    print(f"  login URL         : {config.LOGIN_URL}  (account: {config.LOGIN_EMAIL})")
    print(f"  job-service URL   : {config.RCRM_JOB_SERVICE_URL}")
    print(f"  RCRM token        : {'pasted (login skipped)' if pasted_tok else 'via login'}")

    print("\n=== 2. Anthropic API (auth + claude-sonnet-5) ===")
    if not akey:
        print(f"  {WARN}skipped — ANTHROPIC_API_KEY not set")
    else:
        status, body = _http(
            "GET", "https://api.anthropic.com/v1/models/claude-sonnet-5",
            {"x-api-key": akey, "anthropic-version": "2023-06-01"},
        )
        if status == 200:
            print(f"  {OK}auth OK, claude-sonnet-5 reachable")
        elif status == 401:
            print(f"  {BAD}401 — key rejected")
        elif status == 404:
            print(f"  {BAD}404 — key valid but claude-sonnet-5 not available to it")
        else:
            print(f"  {BAD}status={status} :: {body[:200]}")

    print("\n=== 3. Login (pre-fed test account) ===")
    token = pasted_tok
    if pasted_tok:
        print(f"  {WARN}skipped — using pasted RCRM_ALBATROSS_TOKEN")
    else:
        form = urllib.parse.urlencode({"email": config.LOGIN_EMAIL, "password": config.LOGIN_PASSWORD})
        status, body = _http("POST", config.LOGIN_URL, {
            "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
        }, form)
        if status == 200:
            try:
                d = json.loads(body)
                token = (d.get("data") or {}).get("token")
                print(f"  {OK}login OK (account: {(d.get('user') or {}).get('account')})"
                      if token else f"  {BAD}200 but no data.token :: {body[:200]}")
            except Exception:  # noqa: BLE001
                print(f"  {BAD}200 but unexpected body :: {body[:200]}")
        elif status is None:
            print(f"  {BAD}unreachable — {body} (VPN/network? albatross URL?)")
        else:
            print(f"  {BAD}status={status} :: {body[:200]}")

    print("\n=== 4. Config endpoint (URL + token + flag + Neptune) ===")
    if not token:
        print(f"  {WARN}skipped — no token available")
    else:
        payload = json.dumps({
            "databaseType": 2,
            "aiSearch": "Senior software engineer with 5 years Python and AWS experience",
        })
        status, body = _http("POST", f"{config.RCRM_JOB_SERVICE_URL}/{config.CONFIG_PATH}", {
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json",
        }, payload)
        if status == 200:
            try:
                data = json.loads(body).get("data", {})
                print(f"  {OK}200 — pipeline live (status={data.get('status','?')}, "
                      f"questions={len(data.get('questions') or [])}, "
                      f"criteria={len(data.get('criteria') or [])}, "
                      f"filters={'yes' if data.get('filters') else 'no'})")
            except Exception:  # noqa: BLE001
                print(f"  {WARN}200 but unexpected body :: {body[:200]}")
        elif status == 401:
            print(f"  {BAD}401 — token invalid/expired")
        elif status == 403:
            print(f"  {BAD}403 — unified_ai_sourcing_matching flag not enabled for this account")
        elif status == 400:
            print(f"  {WARN}400 — reachable & authed, request rejected :: {body[:200]}")
        elif status is None:
            print(f"  {BAD}unreachable — {body} (ss5 job host is internal; run on corp/VPN, or set RCRM_JOB_SERVICE_URL)")
        else:
            print(f"  {BAD}status={status} :: {body[:200]}")

    print("\nGreen in 2 & 4 = ready to run `python run.py --limit 3`.")


if __name__ == "__main__":
    main()

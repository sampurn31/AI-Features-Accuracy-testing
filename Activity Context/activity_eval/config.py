#!/usr/bin/env python3
"""
Credentials, service URLs and the login token.

Secrets live in `.env` in this folder (gitignored). A real exported shell variable always
wins over the file.

    AI2_API_KEY=...   AI2_EMAIL=...   AI2_PASSWORD=...
    DEV_API_KEY=...   DEV_EMAIL=...   DEV_PASSWORD=...
    ANTHROPIC_API_KEY=...

TWO credentials per environment, and they are not interchangeable:

  public API   https://{env}api.recruitcrm.net/v1/    <ENV>_API_KEY
               jobs, candidates, notes, call logs
  Aries        https://{env}-aries.recruitcrm.net/v2/ login token
               the AI matching endpoints

The login token comes from POSTing email+password to
https://albatross-{env}.recruitcrm.net/v1/login and reading data.token.

ai2 and dev are two deployments over ONE database. The ai2 public-API host frequently has
no DNS, so all public-API reads go through dev — same database, no difference.
"""

import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")

_file, _loaded, _tokens = {}, False, {}


def _load():
    global _file, _loaded
    if _loaded:
        return _file
    vals = {}
    if os.path.exists(ENV_PATH):
        for raw in open(ENV_PATH):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            line = line[7:] if line.startswith("export ") else line
            k, _, v = line.partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k.strip():
                vals[k.strip()] = v
    _file, _loaded = vals, True
    return _file


def get(name):
    return os.environ.get(name) or _load().get(name) or None


def api_base(env):
    return f"https://{env}api.recruitcrm.net/v1/"


def aries_base(env):
    return f"https://{env}-aries.recruitcrm.net/v2/"


def job_service_base(env):
    return f"https://{env}job.recruitcrm.net/v2/"


def albatross_base(env):
    return f"https://albatross-{env}.recruitcrm.net/v1/"


def api_key(env):
    k = get(f"{env.upper()}_API_KEY")
    if not k:
        raise SystemExit(f"Missing {env.upper()}_API_KEY — add it to {ENV_PATH}")
    return k


def login_token(env, force=False):
    if not force and env in _tokens:
        return _tokens[env]
    email, pw = get(f"{env.upper()}_EMAIL"), get(f"{env.upper()}_PASSWORD")
    if not email or not pw:
        raise SystemExit(f"Missing {env.upper()}_EMAIL / {env.upper()}_PASSWORD in {ENV_PATH}")
    r = requests.post(albatross_base(env) + "login",
                      json={"email": email, "password": pw}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"Login failed on {env}: HTTP {r.status_code} {r.text[:200]}")
    tok = (r.json().get("data") or {}).get("token")
    if not tok:
        raise SystemExit(f"Login on {env} returned no token")
    _tokens[env] = tok
    return tok


def anthropic_key():
    """None means let the SDK resolve it from ANTHROPIC_API_KEY or an `ant` profile."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return _load().get("ANTHROPIC_API_KEY") or None

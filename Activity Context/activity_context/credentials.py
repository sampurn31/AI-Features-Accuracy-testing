#!/usr/bin/env python3
"""
Credential loading for the Activity Context test assets.

WHERE TO PUT YOUR KEYS
----------------------
A `.env` file next to this module (gitignored). A real exported shell variable always
wins over the file, so CI can override without editing anything.

    ATS9_API_KEY=...   ATS9_EMAIL=...   ATS9_PASSWORD=...
    DEV_API_KEY=...    DEV_EMAIL=...    DEV_PASSWORD=...
    ANTHROPIC_API_KEY=...

TWO CREDENTIALS PER ENVIRONMENT, AND THEY ARE NOT INTERCHANGEABLE
----------------------------------------------------------------
  public API   https://{env}api.recruitcrm.net/v1/      <ENV>_API_KEY
               creating jobs, candidates, notes, call logs
  Aries        https://{env}-aries.recruitcrm.net/v2/   login token
  job service  https://{env}job.recruitcrm.net/v2/      login token

The login token is obtained by POSTing email+password to
https://albatross-{env}.recruitcrm.net/v1/login and reading data.token. Both travel as
`Authorization: Bearer`, which is why they get confused — using the API key on Aries
returns 401 with no useful message.

SHARED DATABASE
---------------
Two deployments sit over ONE database. Seed once; `--env` only chooses which deployment
issues the writes.
"""

import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
EXAMPLE_PATH = os.path.join(HERE, ".env.example")

_loaded = False
_file_values = {}
_token_cache = {}


def load_env(path=ENV_PATH, override=False):
    """Parse a minimal .env. No dependency — the format is trivial and this stays honest."""
    global _loaded, _file_values
    if _loaded and not override:
        return _file_values
    vals = {}
    if os.path.exists(path):
        for raw in open(path):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, _, v = line.partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k.strip():
                vals[k.strip()] = v
    _file_values, _loaded = vals, True
    return _file_values


def _get(name):
    return os.environ.get(name) or load_env().get(name) or None


def _prefix(env):
    return env.strip().upper()


def api_base(env):
    return f"https://{env}api.recruitcrm.net/v1/"


def albatross_base(env):
    return f"https://albatross-{env}.recruitcrm.net/v1/"


def aries_base(env):
    return f"https://{env}-aries.recruitcrm.net/v2/"


def job_service_base(env):
    return f"https://{env}job.recruitcrm.net/v2/"


def env_label(env):
    """Human label, overridable per environment so reports read the way people speak."""
    return _get(f"{_prefix(env)}_LABEL") or env


def known_envs():
    """Environments that have at least an API key configured."""
    seen = set()
    for src in (os.environ, load_env()):
        for k in src:
            if k.endswith("_API_KEY"):
                seen.add(k[:-len("_API_KEY")].lower())
    return sorted(seen)


def _missing(env, what, names):
    raise SystemExit(
        f"Missing {what} for environment {env!r}.\n"
        f"  Set one of: {', '.join(names)}\n"
        f"  in {ENV_PATH} (see {os.path.basename(EXAMPLE_PATH)}) or export it.\n"
        f"  Configured environments: {', '.join(known_envs()) or '(none)'}")


def describe(env):
    """One line for logs — never prints a secret."""
    return (f"{env_label(env)}  api={api_base(env)}  aries={aries_base(env)}  "
            f"key={'set' if _get(f'{_prefix(env)}_API_KEY') else 'MISSING'}")


def resolve_api_key(env, explicit=None):
    if explicit:
        return explicit
    k = _get(f"{_prefix(env)}_API_KEY")
    if not k:
        _missing(env, "public API key", [f"{_prefix(env)}_API_KEY"])
    return k


def resolve_login(env, email=None, password=None):
    e = email or _get(f"{_prefix(env)}_EMAIL")
    p = password or _get(f"{_prefix(env)}_PASSWORD")
    if not e or not p:
        _missing(env, "login credentials",
                 [f"{_prefix(env)}_EMAIL", f"{_prefix(env)}_PASSWORD"])
    return e, p


def albatross_token(env, email=None, password=None, force=False):
    """Cached per environment — the AI matching endpoints need this, NOT the API key."""
    if not force and env in _token_cache:
        return _token_cache[env]
    e, p = resolve_login(env, email, password)
    r = requests.post(albatross_base(env) + "login",
                      json={"email": e, "password": p}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"Login failed on {env}: HTTP {r.status_code} {r.text[:200]}")
    tok = (r.json().get("data") or {}).get("token")
    if not tok:
        raise SystemExit(f"Login on {env} returned no token")
    _token_cache[env] = tok
    return tok


def login_context(env, email=None, password=None):
    """Everything a matching client needs, in one call."""
    return {"env": env, "label": env_label(env),
            "token": albatross_token(env, email, password),
            "aries": aries_base(env), "job_service": job_service_base(env),
            "api": api_base(env)}


def resolve_anthropic_key():
    """None means let the SDK resolve it from ANTHROPIC_API_KEY or an `ant` profile."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return load_env().get("ANTHROPIC_API_KEY") or None


CONFIG_ENV = os.environ.get("ACTIVITY_CONTEXT_ENV", "dev")

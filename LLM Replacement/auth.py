import requests
from constants import BASE_URL_A, BASE_URL_B, CREDENTIALS_A, CREDENTIALS_B, LOGIN_PATH, REQUEST_TIMEOUT_SECONDS

_token_cache: dict[str, str] = {}


def _fetch_token(base_url: str, credentials: dict) -> str:
    url = base_url + LOGIN_PATH
    response = requests.post(
        url,
        json=credentials,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    response.raise_for_status()
    body = response.json()
    token = body.get("data", {}).get("token")
    if not token:
        raise ValueError(f"Token not found in login response from {url}: {body}")
    return token


def get_token(env: str) -> str:
    """
    Returns a cached (or freshly fetched) Bearer token for the given env.

    Args:
        env: "A" or "B"
    """
    if env in _token_cache:
        return _token_cache[env]

    if env == "A":
        token = _fetch_token(BASE_URL_A, CREDENTIALS_A)
    elif env == "B":
        token = _fetch_token(BASE_URL_B, CREDENTIALS_B)
    else:
        raise ValueError(f"Unknown env: '{env}'. Use 'A' or 'B'.")

    _token_cache[env] = token
    return token


def refresh_token(env: str) -> str:
    """Force re-fetch — call this when a 401 is received."""
    _token_cache.pop(env, None)
    return get_token(env)


def auth_headers(env: str) -> dict:
    """Convenience: returns the Authorization header dict for an env."""
    return {"Authorization": f"Bearer {get_token(env)}"}

import time
import json
import requests
from typing import Any

from constants import (
    BASE_URL_A,
    BASE_URL_B,
    REQUEST_TIMEOUT_SECONDS,
    INTER_PAIR_DELAY_SECONDS,
)
from auth import auth_headers, refresh_token

_last_call_both_end_ts = 0.0


def _build_url(env: str, path: str, base_url: str | None = None) -> str:
    if base_url:
        return base_url + path
    return (BASE_URL_A if env == "A" else BASE_URL_B) + path


def _call(env: str, path: str, payload: dict, stream: bool = False, base_url: str | None = None) -> dict[str, Any]:
    """
    Core call wrapper used by both environments.

    Returns a result dict with:
        - status_code
        - latency_ms
        - ttft_ms          (time-to-first-token, streaming only)
        - response_text    (full reconstructed response)
        - chunks           (list of raw chunk strings, streaming only)
        - error            (populated only on failure)
    """
    url = _build_url(env, path, base_url)
    headers = {
        **auth_headers(env),
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    }

    result: dict[str, Any] = {
        "env": env,
        "url": url,
        "status_code": None,
        "latency_ms": None,
        "ttft_ms": None,
        "response_text": "",
        "chunks": [],
        "error": None,
    }

    start = time.perf_counter()

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            stream=stream,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        if response.status_code == 401:
            headers["Authorization"] = f"Bearer {refresh_token(env)}"
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                stream=stream,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

        result["status_code"] = response.status_code

        if stream:
            ttft_recorded = False
            chunks = []
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                if chunk:
                    if not ttft_recorded:
                        result["ttft_ms"] = round((time.perf_counter() - start) * 1000, 2)
                        ttft_recorded = True
                    chunks.append(chunk)
            result["chunks"] = chunks
            result["response_text"] = "".join(chunks)
        else:
            result["response_text"] = response.text

    except requests.exceptions.Timeout:
        result["error"] = "timeout"
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"connection_error: {e}"
    except Exception as e:
        result["error"] = f"unexpected: {e}"
    finally:
        result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)

    return result


def call_baseline_env(path: str, payload: dict, stream: bool = False, base_url: str | None = None) -> dict[str, Any]:
    """Hit Env A (baseline) and return a result dict."""
    return _call("A", path, payload, stream, base_url)


def call_new_env(path: str, payload: dict, stream: bool = False, base_url: str | None = None) -> dict[str, Any]:
    """Hit Env B (new) and return a result dict."""
    return _call("B", path, payload, stream, base_url)


def call_both(
    path: str,
    payload: dict,
    stream: bool = False,
    base_url_a: str | None = None,
    base_url_b: str | None = None,
    pair_delay_seconds: float | None = None,
) -> tuple[dict, dict]:
    """
    Sends identical input to both environments.

    To avoid rate spikes / overlap between back-to-back comparisons, this helper
    enforces a minimum pause between two consecutive `call_both(...)` runs.

    Returns:
        (result_a, result_b)
    """
    global _last_call_both_end_ts
    delay = INTER_PAIR_DELAY_SECONDS if pair_delay_seconds is None else max(0.0, pair_delay_seconds)

    if _last_call_both_end_ts > 0 and delay > 0:
        elapsed = time.perf_counter() - _last_call_both_end_ts
        if elapsed < delay:
            time.sleep(delay - elapsed)

    result_a = call_baseline_env(path, payload, stream, base_url_a)
    result_b = call_new_env(path, payload, stream, base_url_b)
    _last_call_both_end_ts = time.perf_counter()
    return result_a, result_b

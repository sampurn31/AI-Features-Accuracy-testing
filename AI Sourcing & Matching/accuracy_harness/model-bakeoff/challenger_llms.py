"""
Challenger-model registry + provider adapters for the scoring bake-off
(run_model_bakeoff.py).

WHY this file exists
--------------------
The env's POST /v1/ai-sourcing-v3/search scores candidates with ONE model
(today: Gemini 2.5 Flash Lite). To decide which model could REPLACE it we need
to send the *same* criteria + the *same* candidate profiles to several other
models — each with its own API key and its own endpoint — and compare scores,
latency and throughput against the env.

Nothing here is Anthropic-specific: `anthropic`, `google`, `openai` and
`openai_compat` (DeepSeek / Grok / Groq / Mistral / Together / OpenRouter /
anything that speaks /chat/completions) are all supported. No new dependencies —
everything goes over `requests`, except Anthropic which reuses the installed SDK.

Keys are NEVER stored here. A slot names the ENV VAR that holds its key; a slot
whose env var is empty is skipped (reported as "no key") instead of failing the run.

    python3 run_model_bakeoff.py --list-models     # show slots + which keys resolved
"""
import json
import os
import re
import time

import requests

# ══════════════════════════════════════════════════════════════════════════════
#  ►►►  EDIT HERE — THE 4-6 CHALLENGER SLOTS  ◄◄◄
#
#  One entry per model you want to pit against the env. Fill in:
#     model    : the provider's exact model id            (leave "" = slot ignored)
#     key_env  : env var holding THAT provider's API key   (never the key itself)
#  Everything else is optional and documented under SLOT FIELDS below.
#
#  Override this list at run time without editing code:
#     python3 run_model_bakeoff.py --models my_slots.json
#     BAKEOFF_SLOTS_JSON=/path/my_slots.json python3 run_model_bakeoff.py
#  (`--init-models my_slots.json` writes this list out as a starter file.)
# ══════════════════════════════════════════════════════════════════════════════
SLOTS = [
    # ── slot 1 ── control: the env's own model, called directly, so "is the env
    #              slow because of the model or because of retrieval?" is answerable.
    {"id": "gemini-lite-direct", "provider": "google", "model": "gemini-2.5-flash-lite",
     "key_env": "GEMINI_API_KEY", "label": "Gemini 2.5 Flash Lite (direct)",
     "extra_generation_config": {"thinkingConfig": {"thinkingBudget": 0}}},

    # ── slot 2 ──
    {"id": "gemini-flash", "provider": "google", "model": "gemini-2.5-flash",
     "key_env": "GEMINI_API_KEY", "label": "Gemini 2.5 Flash"},

    # ── slot 3 ──
    {"id": "claude-haiku", "provider": "anthropic", "model": "claude-haiku-4-5-20251001",
     "key_env": "ANTHROPIC_API_KEY", "label": "Claude Haiku 4.5"},

    # ── slot 4 ── (fill in model + key_env when you have the key)
    {"id": "openai", "provider": "openai", "model": "",
     "key_env": "OPENAI_API_KEY", "label": "OpenAI"},

    # ── slot 5 ── any OpenAI-compatible provider: set base_url + model + key_env
    {"id": "compat-1", "provider": "openai_compat", "model": "",
     "key_env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/v1",
     "label": "DeepSeek"},

    # ── slot 6 ──
    {"id": "compat-2", "provider": "openai_compat", "model": "",
     "key_env": "XAI_API_KEY", "base_url": "https://api.x.ai/v1", "label": "Grok"},
]

# ── SLOT FIELDS ───────────────────────────────────────────────────────────────
#  id        (req) short unique key — becomes the column name in the report
#  provider  (req) "google" | "anthropic" | "openai" | "openai_compat"
#  model     (req) provider model id; "" disables the slot
#  key_env   (req) env var holding the API key
#  label     ( - ) pretty name for the report (defaults to "<model> (<provider>)")
#  base_url  ( - ) REQUIRED for openai_compat; override for openai/anthropic proxies
#  max_tokens( - ) output cap for one candidate scoring (default MAX_TOKENS)
#  temperature( - ) omitted entirely unless you set it (Sonnet 5 / gpt-5 reject it)
#  json_mode ( - ) ask for native JSON output (default True; auto-retried off on 400)
#  token_param(- ) "max_completion_tokens" (openai default) | "max_tokens" (compat default)
#  extra_body( - ) dict merged into the request body — provider-specific knobs
#  extra_generation_config ( - ) google only: merged into generationConfig
#                                e.g. {"thinkingConfig": {"thinkingBudget": 0}}
# ──────────────────────────────────────────────────────────────────────────────

MAX_TOKENS = int(os.environ.get("BAKEOFF_MAX_TOKENS", "1600"))
TIMEOUT = int(os.environ.get("BAKEOFF_TIMEOUT", "120"))
RETRIES = int(os.environ.get("BAKEOFF_RETRIES", "2"))          # extra attempts after the first
BACKOFF = float(os.environ.get("BAKEOFF_BACKOFF", "2.0"))      # seconds, doubled per retry

_ANTHROPIC_VERSION = "2023-06-01"
_RETRYABLE = (408, 409, 425, 429, 500, 502, 503, 504, 529)


class ChallengerError(Exception):
    """One scoring call failed. Always caught by score() — never aborts a run."""

    def __init__(self, msg, status=None, retryable=False):
        super().__init__(msg)
        self.status = status
        self.retryable = retryable


# ── slot loading ──────────────────────────────────────────────────────────────

def load_slots(path=None):
    """SLOTS above, or a JSON list from `path` / $BAKEOFF_SLOTS_JSON. Adds
    `key` (resolved secret), `enabled`, `skip_reason` to each slot."""
    path = path or os.environ.get("BAKEOFF_SLOTS_JSON")
    slots = json.load(open(path, encoding="utf-8")) if path else [dict(s) for s in SLOTS]
    out, seen = [], set()
    for s in slots:
        s = dict(s)
        sid = s.get("id") or s.get("model") or f"slot{len(out) + 1}"
        while sid in seen:
            sid += "_2"
        seen.add(sid)
        s["id"] = sid
        s["label"] = s.get("label") or f"{s.get('model') or '?'} ({s.get('provider')})"
        s["key"] = os.environ.get(s.get("key_env") or "", "").strip()
        reason = None
        if s.get("provider") not in ADAPTERS:
            reason = f"unknown provider '{s.get('provider')}' (use: {', '.join(ADAPTERS)})"
        elif not s.get("model"):
            reason = "no model id set in the slot"
        elif not s["key"]:
            reason = f"env var {s.get('key_env') or '?'} is empty"
        elif s["provider"] == "openai_compat" and not s.get("base_url"):
            reason = "openai_compat needs base_url"
        s["enabled"] = reason is None
        s["skip_reason"] = reason
        out.append(s)
    return out


def write_template(path):
    json.dump(SLOTS, open(path, "w", encoding="utf-8"), indent=2)
    return path


def describe(slots):
    lines = []
    for s in slots:
        mark = "ON " if s["enabled"] else "off"
        tail = "" if s["enabled"] else f"   [skipped: {s['skip_reason']}]"
        lines.append(f"  [{mark}] {s['id']:<20} {s['provider']:<14} {s.get('model') or '-':<34}"
                     f" key={s.get('key_env') or '-'}{tail}")
    return "\n".join(lines)


# ── JSON extraction (models wrap JSON in prose/fences often enough to matter) ──

def parse_json(text):
    """First JSON object in `text`, or None. Tolerates ``` fences and prose."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE).strip()
    try:
        return json.loads(t)
    except (ValueError, TypeError):
        pass
    start = t.find("{")
    while start != -1:                      # brace-match forward from each "{"
        depth, instr, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
                continue
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except ValueError:
                        break
        start = t.find("{", start + 1)
    return None


# ── provider adapters ─────────────────────────────────────────────────────────
# Each returns (text, in_tokens, out_tokens, http_status) or raises ChallengerError.

def _http(url, headers, body, timeout):
    try:
        resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=timeout)
    except requests.RequestException as e:
        raise ChallengerError(f"{type(e).__name__}: {e}", retryable=True) from e
    if resp.status_code != 200:
        raise ChallengerError(f"HTTP {resp.status_code}: {resp.text[:300]}",
                              status=resp.status_code,
                              retryable=resp.status_code in _RETRYABLE)
    try:
        return resp.json(), resp.status_code
    except ValueError as e:
        raise ChallengerError(f"non-JSON response: {resp.text[:200]}", retryable=True) from e


def _google(slot, system, user, max_tokens, timeout, json_mode):
    base = (slot.get("base_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    url = f"{base}/models/{slot['model']}:generateContent"
    gen = {"maxOutputTokens": max_tokens}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    if slot.get("temperature") is not None:
        gen["temperature"] = slot["temperature"]
    gen.update(slot.get("extra_generation_config") or {})
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen}
    body.update(slot.get("extra_body") or {})
    data, status = _http(url, {"x-goog-api-key": slot["key"],
                               "Content-Type": "application/json"}, body, timeout)
    cands = data.get("candidates") or []
    if not cands:
        fb = (data.get("promptFeedback") or {}).get("blockReason")
        raise ChallengerError(f"no candidates returned (blockReason={fb})")
    c0 = cands[0]
    text = "".join(p.get("text") or "" for p in ((c0.get("content") or {}).get("parts") or []))
    if not text.strip():
        raise ChallengerError(f"empty text (finishReason={c0.get('finishReason')}) — "
                             "raise max_tokens or disable thinking for this model")
    um = data.get("usageMetadata") or {}
    return text, um.get("promptTokenCount") or 0, um.get("candidatesTokenCount") or 0, status


def _openai_like(slot, system, user, max_tokens, timeout, json_mode, default_base):
    base = (slot.get("base_url") or default_base).rstrip("/")
    tok_param = slot.get("token_param") or (
        "max_completion_tokens" if slot["provider"] == "openai" else "max_tokens")
    body = {"model": slot["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            tok_param: max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if slot.get("temperature") is not None:
        body["temperature"] = slot["temperature"]
    body.update(slot.get("extra_body") or {})
    data, status = _http(f"{base}/chat/completions",
                         {"Authorization": f"Bearer {slot['key']}",
                          "Content-Type": "application/json"}, body, timeout)
    choices = data.get("choices") or []
    if not choices:
        raise ChallengerError(f"no choices in response: {json.dumps(data)[:200]}")
    msg = choices[0].get("message") or {}
    text = msg.get("content") or ""
    if not text.strip():                       # reasoning models can spend the whole budget
        raise ChallengerError(f"empty content (finish_reason={choices[0].get('finish_reason')}) — "
                             "raise max_tokens for this model")
    u = data.get("usage") or {}
    return text, u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0, status


def _openai(slot, system, user, max_tokens, timeout, json_mode):
    return _openai_like(slot, system, user, max_tokens, timeout, json_mode,
                        "https://api.openai.com/v1")


def _openai_compat(slot, system, user, max_tokens, timeout, json_mode):
    return _openai_like(slot, system, user, max_tokens, timeout, json_mode, "")


_ANTHROPIC_CLIENTS = {}


def _anthropic(slot, system, user, max_tokens, timeout, json_mode):
    """Uses the installed SDK (already a dependency). json_mode is prompt-enforced —
    kept uniform across providers so every model gets the identical instruction."""
    from anthropic import Anthropic                      # local: keeps import cost off other providers
    key = slot["key"]
    client = _ANTHROPIC_CLIENTS.get(key)
    if client is None:
        kwargs = {"api_key": key, "timeout": timeout, "max_retries": 0}   # we own the retry loop
        if slot.get("base_url"):
            kwargs["base_url"] = slot["base_url"]
        client = _ANTHROPIC_CLIENTS[key] = Anthropic(**kwargs)
    body = {"model": slot["model"], "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],   # same prefix every call
            "messages": [{"role": "user", "content": user}]}
    if slot.get("temperature") is not None:
        body["temperature"] = slot["temperature"]
    body.update(slot.get("extra_body") or {})
    try:
        resp = client.messages.create(**body)
    except Exception as e:                                # noqa: BLE001 - SDK raises many types
        status = getattr(e, "status_code", None)
        raise ChallengerError(f"{type(e).__name__}: {str(e)[:300]}", status=status,
                              retryable=status in _RETRYABLE or status is None) from e
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise ChallengerError(f"empty text (stop_reason={getattr(resp, 'stop_reason', None)})")
    u = getattr(resp, "usage", None)
    return (text, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0, 200)


ADAPTERS = {"google": _google, "openai": _openai, "openai_compat": _openai_compat,
            "anthropic": _anthropic}


# ── the one call the runner makes ─────────────────────────────────────────────

def score(slot, system, user, max_tokens=None, timeout=None, retries=None):
    """
    ONE scoring call. NEVER raises: every failure comes back as ok=False so one bad
    model (or one bad candidate) can't stop the bake-off.

    Returns {ok, text, parsed, ms, in_tok, out_tok, attempts, http, error}
    where `ms` is wall-clock latency of the LAST attempt (the one being timed) and
    `parsed` is the extracted JSON object (None if the model didn't return JSON).
    """
    fn = ADAPTERS[slot["provider"]]
    max_tokens = max_tokens or int(slot.get("max_tokens") or MAX_TOKENS)
    timeout = timeout or TIMEOUT
    tries = (retries if retries is not None else RETRIES) + 1
    json_mode = slot.get("json_mode", True)
    last, attempts = None, 0
    for attempt in range(1, tries + 1):
        attempts = attempt
        t0 = time.perf_counter()
        try:
            text, itok, otok, status = fn(slot, system, user, max_tokens, timeout, json_mode)
            ms = (time.perf_counter() - t0) * 1000
            return {"ok": True, "text": text, "parsed": parse_json(text), "ms": ms,
                    "in_tok": itok, "out_tok": otok, "attempts": attempts,
                    "http": status, "error": None}
        except ChallengerError as e:
            last = e
            # a 400 with json_mode on usually means "this model has no JSON mode" — drop it once
            if json_mode and e.status == 400:
                json_mode = False
                continue
            if not e.retryable or attempt == tries:
                break
            time.sleep(BACKOFF * (2 ** (attempt - 1)))
    return {"ok": False, "text": "", "parsed": None, "ms": None, "in_tok": 0, "out_tok": 0,
            "attempts": attempts, "http": getattr(last, "status", None),
            "error": str(last) if last else "unknown error"}

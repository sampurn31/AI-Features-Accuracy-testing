"""
Shared Neptune stream parsing for JSON object concatenation (generate-summary,
generate-job-description, and similar endpoints).
"""

import json
import re


def normalize_neptune_stream_raw(raw: str) -> str:
    """
    Neptune sometimes streams **SSE**-framed lines::

        data: {"data":{...},"meta":{...}}

    Our JSON decoders expect **concatenated JSON objects** with no ``data:`` prefix.
    If the body does not start with ``{``, strip ``data:`` per line and join payloads.
    Skip sentinel lines like ``[DONE]``.
    """
    if not raw:
        return raw
    text = raw.strip()
    if text.startswith("{"):
        return text
    parts: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("data:"):
            s = s[5:].strip()
        if s.upper() == "[DONE]":
            continue
        if s.startswith("{"):
            parts.append(s)
    return "".join(parts) if parts else text


def extract_generated_text(raw: str) -> str:
    """
    Decode back-to-back JSON objects and join every ``data.generated_text`` value.
    """
    decoder = json.JSONDecoder()
    pos     = 0
    parts: list[str] = []
    text    = normalize_neptune_stream_raw(raw).strip()

    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text, pos)
            if not isinstance(obj, dict):
                pos = end
                while pos < len(text) and text[pos] in " \n\r\t":
                    pos += 1
                continue
            data = obj.get("data") or {}
            chunk = data.get("generated_text", "")
            if chunk:
                parts.append(chunk)
            pos = end
            while pos < len(text) and text[pos] in " \n\r\t":
                pos += 1
        except json.JSONDecodeError:
            break

    return "".join(parts)


def _conversation_id_from_chunk(data: dict, meta: dict) -> str | None:
    """
    Neptune streams store the thread id on ``data.id`` (e.g. ``conv_...``), not ``chat_id``.
    The next request still sends it as JSON field ``chat_id``.

    Also accepts legacy ``data.chat_id`` / ``meta.chat_id`` if present.
    """
    d, m = data or {}, meta or {}
    for bucket in (d, m):
        xid = bucket.get("id")
        if isinstance(xid, str) and xid.startswith("conv_"):
            return xid
    for bucket in (d, m):
        ck = bucket.get("chat_id")
        if isinstance(ck, str) and ck.strip():
            return ck.strip()
    return None


def extract_chat_id(raw: str) -> str | None:
    """Last conversation id seen in the stream (for chaining as ``chat_id`` on the next POST)."""
    decoder = json.JSONDecoder()
    pos     = 0
    text    = normalize_neptune_stream_raw(raw).strip()
    last_id: str | None = None

    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text, pos)
            if not isinstance(obj, dict):
                pos = end
                while pos < len(text) and text[pos] in " \n\r\t":
                    pos += 1
                continue
            data = obj.get("data") or {}
            meta = obj.get("meta") or {}
            cid = _conversation_id_from_chunk(data, meta)
            if cid:
                last_id = cid
            pos = end
            while pos < len(text) and text[pos] in " \n\r\t":
                pos += 1
        except json.JSONDecodeError:
            break

    return last_id


def extract_complete_message(raw: str) -> str | None:
    """
    ``/v1/get-suggestions`` streams token chunks in ``generated_text``, then emits a final
    object with ``data.complete_message`` containing the full JSON string.  Return the **last**
    non-empty ``complete_message`` seen.
    """
    decoder = json.JSONDecoder()
    pos     = 0
    text    = normalize_neptune_stream_raw(raw).strip()
    last: str | None = None

    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text, pos)
            if not isinstance(obj, dict):
                pos = end
                while pos < len(text) and text[pos] in " \n\r\t":
                    pos += 1
                continue
            data = obj.get("data") or {}
            cm = data.get("complete_message")
            if isinstance(cm, str) and cm.strip():
                last = cm.strip()
            pos = end
            while pos < len(text) and text[pos] in " \n\r\t":
                pos += 1
        except json.JSONDecodeError:
            break

    return last


def suggestions_stream_text(raw: str) -> str:
    """
    Prefer ``complete_message`` when present; else joined ``generated_text`` (streaming fallback).
    """
    cm = extract_complete_message(raw)
    if cm:
        return cm
    return extract_generated_text(raw)


def validate_suggestions_output(text: str) -> dict:
    """
    Valid if ``complete_message`` parses as JSON with intent + prompts structure, or non-trivial text.
    """
    result = {"schema_valid": True, "error": None}
    t = (text or "").strip()
    if not t:
        result["schema_valid"] = False
        result["error"] = "empty_response"
        return result
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and (
            "senders_intent" in obj
            or "sender_intent" in obj
            or "prompts" in obj
        ):
            return result
    except json.JSONDecodeError:
        pass
    if len(t) >= 40:
        return result
    result["schema_valid"] = False
    result["error"] = "expected_json_or_long_text"
    return result


def validate_streamed_markup(text: str) -> dict:
    """
    Non-empty response that looks like HTML / rich text from Neptune streams.
    Job descriptions often start with <h1> without a full <html> wrapper.
    """
    result = {"schema_valid": True, "error": None}

    if not text or not text.strip():
        result["schema_valid"] = False
        result["error"] = "empty_response"
        return result

    lower = text.lower()
    markers = (
        "<html", "<body", "<p", "<h1", "<h2", "<h3", "<h4",
        "<ul", "<ol", "<li", "<div", "<section", "<article",
        "<strong", "<em", "<br",
    )
    if not any(m in lower for m in markers):
        result["schema_valid"] = False
        result["error"] = "no_markup_tags_detected"

    return result


def validate_streamed_text_output(text: str, min_chars: int = 2) -> dict:
    """
    Note-summary and similar endpoints often stream **plain text** (no HTML).
    """
    result = {"schema_valid": True, "error": None}
    t = (text or "").strip()
    if len(t) < min_chars:
        result["schema_valid"] = False
        result["error"] = "empty_or_too_short_response"
    return result


email_sequence_stream_text = suggestions_stream_text


_STEP_BLOCK_RE = re.compile(r"```\s*(\{.*?\})\s*```", re.DOTALL)


def parse_email_sequence_steps(text: str) -> list[dict]:
    """
    Extract the ordered list of step dicts from a ``complete_message`` value.

    The API wraps each step's JSON in triple back-ticks::

        ```{ "step_number": 1, "type": "Email", ... }```

    Returns an empty list if nothing can be parsed (caller decides how to
    handle fallback / validation).
    """
    if not text:
        return []
    steps: list[dict] = []
    for m in _STEP_BLOCK_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                steps.append(obj)
        except json.JSONDecodeError:
            pass
    steps.sort(key=lambda s: int(s.get("step_number", 0)))
    return steps


def validate_email_sequence_output(text: str) -> dict:
    """
    Valid when:
    * ``complete_message`` text contains ≥ 1 parseable step with ``step_number``
      and ``type`` fields, **or**
    * substantial generated_text was streamed (no back-tick blocks but > 200
      chars — degraded mode).

    Returns ``{"schema_valid": bool, "steps": [...], "error": None|str}``.
    """
    t = (text or "").strip()
    if not t:
        return {"schema_valid": False, "steps": [], "error": "empty_response"}

    steps = parse_email_sequence_steps(t)
    if steps:
        for s in steps:
            if "step_number" not in s or "type" not in s:
                return {
                    "schema_valid": False,
                    "steps": steps,
                    "error": f"step_missing_required_fields:{s}",
                }
        return {"schema_valid": True, "steps": steps, "error": None}

    if len(t) > 200:
        return {"schema_valid": True, "steps": [], "error": "no_step_blocks_but_long_text"}

    return {"schema_valid": False, "steps": [], "error": "no_steps_and_short_text"}

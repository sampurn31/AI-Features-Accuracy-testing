import json
from typing import Any


ACTION_RULES = {
    "summarize":      {"must_be_text": True,  "no_long_paragraphs": False},
    "extract":        {"must_have_bullets": True},
    "key_points":     {"must_have_bullets": True},
    "email":          {"required_sections": ["subject", "body"]},
    "email_sequence": {"required_sections": ["steps"]},
    "suggest":        {"must_be_list": True},
    "default":        {},
}


def _try_parse_json(text: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False, None


def validate_schema(response_text: str, action: str = "default") -> dict[str, Any]:
    """
    Validates a raw response string against the expected format for the given action.

    Returns:
        {
            "schema_valid": bool,
            "parsed": <dict | list | str>,   # best-effort parsed value
            "action": str,
            "error": str | None
        }
    """
    result = {
        "schema_valid": True,
        "parsed": None,
        "action": action,
        "error": None,
    }

    if not response_text or not response_text.strip():
        result["schema_valid"] = False
        result["error"] = "empty_response"
        return result

    is_json, parsed = _try_parse_json(response_text)
    if is_json:
        result["parsed"] = parsed
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if value is None or value == "":
                    result["schema_valid"] = False
                    result["error"] = f"null_or_empty_field: {key}"
                    return result
    else:
        result["parsed"] = response_text

    rules = ACTION_RULES.get(action, ACTION_RULES["default"])

    if rules.get("must_have_bullets"):
        has_bullets = "-" in response_text or "•" in response_text or "\n*" in response_text
        if not has_bullets and (not is_json or not isinstance(parsed, list)):
            result["schema_valid"] = False
            result["error"] = "expected_bullet_list_or_json_array"
            return result

    if rules.get("must_be_list"):
        is_list = is_json and isinstance(parsed, list)
        if not is_list:
            result["schema_valid"] = False
            result["error"] = "expected_list_format"
            return result

    if "required_sections" in rules:
        for section in rules["required_sections"]:
            if isinstance(parsed, dict) and section not in parsed:
                result["schema_valid"] = False
                result["error"] = f"missing_required_section: {section}"
                return result
            elif isinstance(parsed, str) and section.lower() not in parsed.lower():
                result["schema_valid"] = False
                result["error"] = f"missing_section_keyword: {section}"
                return result

    return result

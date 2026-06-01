import json
import logging
from datetime import datetime, timezone
from pathlib import Path


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_log_file = LOG_DIR / f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"

JUDGE_LLM_PAYLOAD_LOG = LOG_DIR / "judge_llm_payloads.jsonl"

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger("eval")


def log_result(
    endpoint: str,
    action: str,
    latency_ms_a: float | None,
    latency_ms_b: float | None,
    schema_valid_a: bool,
    schema_valid_b: bool,
    judge_score: float | None,
    dimension_scores: dict | None,
    final_status: str,
    extra: dict | None = None,
    text_a: str | None = None,
    text_b: str | None = None,
    input_text: str | None = None,
) -> dict:
    """
    Builds and persists a structured log entry for one test run.

    ``text_a``, ``text_b``, ``input_text`` are stored in the in-memory entry
    (full length) so the Excel reporter can display them, and truncated to
    ``_TEXT_CAP`` chars in the JSONL file to keep log sizes manageable.
    """
    entry = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "endpoint":        endpoint,
        "action":          action,
        "latency_ms_a":    latency_ms_a,
        "latency_ms_b":    latency_ms_b,
        "schema_valid_a":  schema_valid_a,
        "schema_valid_b":  schema_valid_b,
        "judge_score":     judge_score,
        "dimension_scores": dimension_scores,
        "final_status":    final_status,
        "text_a":     text_a or "",
        "text_b":     text_b or "",
        "input_text": input_text or "",
    }
    if extra:
        entry["extra"] = extra

    line = json.dumps(entry, ensure_ascii=False)
    _logger.info(line)

    with _log_file.open("a") as f:
        f.write(line + "\n")

    return entry


def log_judge_llm_payload(
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    extra: dict | None = None,
) -> None:
    """
    Append the exact chat payload sent to the judge LLM (after all processing).
    Lines are JSON; read with ``jq`` or any JSONL tool.
    """
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    if extra:
        entry["meta"] = extra

    line = json.dumps(entry, ensure_ascii=False)
    with JUDGE_LLM_PAYLOAD_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

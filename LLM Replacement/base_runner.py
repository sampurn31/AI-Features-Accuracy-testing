"""
base_runner.py
--------------
Orchestrates a single test run (single / conversational / multi-action).
The actual curl path + payload will be injected per test case in later files.
"""

from api_client import call_both
from schema_validator import validate_schema
from judge import evaluate_response
from logger import log_result
from constants import PASS_THRESHOLD, REVIEW_THRESHOLD


def _decide(schema_valid_a: bool, schema_valid_b: bool, judge: dict) -> str:
    if "error" in judge:
        return "ERROR"
    if not schema_valid_b:
        return "FAIL"
    score = judge.get("score", 0)
    if score >= PASS_THRESHOLD:
        return "PASS"
    if score >= REVIEW_THRESHOLD:
        return "REVIEW"
    return "FAIL"


def run_single(path: str, payload: dict, action: str = "default", stream: bool = False) -> dict:
    """One request → compare both envs → judge → log."""
    result_a, result_b = call_both(path, payload, stream)

    schema_a = validate_schema(result_a["response_text"], action)
    schema_b = validate_schema(result_b["response_text"], action)

    judge = evaluate_response(
        response_existing=result_a["response_text"],
        response_new=result_b["response_text"],
        task_input=payload,
        extra_context=f"endpoint={path}; action={action}; env_A=baseline existing; env_B=new candidate",
    )

    status = _decide(schema_a["schema_valid"], schema_b["schema_valid"], judge)

    entry = log_result(
        endpoint=path,
        action=action,
        latency_ms_a=result_a["latency_ms"],
        latency_ms_b=result_b["latency_ms"],
        schema_valid_a=schema_a["schema_valid"],
        schema_valid_b=schema_b["schema_valid"],
        judge_score=judge.get("score"),
        dimension_scores=None,
        final_status=status,
        extra={
            "schema_error_a": schema_a.get("error"),
            "schema_error_b": schema_b.get("error"),
            "judge_reason":   judge.get("reason"),
            "ttft_ms_a":      result_a.get("ttft_ms"),
            "ttft_ms_b":      result_b.get("ttft_ms"),
        },
    )
    return entry


def run_conversational(path: str, turns: list[dict], action: str = "default", stream: bool = False) -> list[dict]:
    """
    Multi-turn conversation test.

    Args:
        turns: list of {"role": "user", "content": "..."} dicts
               The runner accumulates history and sends the full history each turn.
    """
    history_a: list[dict] = []
    history_b: list[dict] = []
    results = []

    for i, turn in enumerate(turns):
        history_a.append(turn)
        history_b.append(turn)

        payload_a = {"messages": history_a}
        payload_b = {"messages": history_b}

        result_a = call_both.__wrapped__("A", path, payload_a, stream) if hasattr(call_both, "__wrapped__") \
                   else __import__("api_client").call_baseline_env(path, payload_a, stream)
        result_b = __import__("api_client").call_new_env(path, payload_b, stream)

        schema_a = validate_schema(result_a["response_text"], action)
        schema_b = validate_schema(result_b["response_text"], action)

        judge = evaluate_response(
            response_existing=result_a["response_text"],
            response_new=result_b["response_text"],
            task_input={"messages": history_a, "turn": i + 1, "total_turns": len(turns)},
            extra_context=f"endpoint={path}; action={action}; env_A=baseline existing; env_B=new candidate",
        )

        status = _decide(schema_a["schema_valid"], schema_b["schema_valid"], judge)

        entry = log_result(
            endpoint=path,
            action=action,
            latency_ms_a=result_a["latency_ms"],
            latency_ms_b=result_b["latency_ms"],
            schema_valid_a=schema_a["schema_valid"],
            schema_valid_b=schema_b["schema_valid"],
            judge_score=judge.get("score"),
            dimension_scores=None,
            final_status=status,
            extra={"turn": i + 1, "judge_reason": judge.get("reason")},
        )
        results.append(entry)

        history_a.append({"role": "assistant", "content": result_a["response_text"]})
        history_b.append({"role": "assistant", "content": result_b["response_text"]})

    return results


def run_multi_action(path: str, base_payload: dict, actions: list[str], stream: bool = False) -> list[dict]:
    """
    Same input, different actions.

    Args:
        actions: list of action strings, e.g. ["summarize", "extract", "suggest"]
                 Each action is injected into the payload as payload["action"] = action.
    """
    results = []
    for action in actions:
        payload = {**base_payload, "action": action}
        entry = run_single(path, payload, action=action, stream=stream)
        results.append(entry)
    return results

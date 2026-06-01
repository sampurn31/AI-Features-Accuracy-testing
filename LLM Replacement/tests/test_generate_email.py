"""
tests/test_generate_email.py
----------------------------
Endpoint : POST /v1/generate-email

Full **key × tone** matrix (product-supported keys and tones).  Each cell uses the
same minimal body shape as the app: ``prompt``, ``key``, ``tone``, ``last_response``
(empty strings allowed for cold generation).

Keys (8):
  schedule_interview, candidate_outreach, application_status, interview_feedback,
  rejection_email, job_offer_email, client_outreach, manual_prompt

Tones (4):
  casual, friendly, formal, neutral

→ 8 × 4 = 32 standalone comparisons (env A vs B).

**Direct manual_prompt** — non-empty ``prompt`` + ``manual_prompt`` + ``tone`` (no prior
turn); 3 extra cases **after** the matrix, **before** chained tests.

Additional **chained** cases: first call with a structured key → ``extract_*`` →
``manual_prompt`` with ``last_response`` + ``chat_id`` (per env), varying tones.

Streaming + Groq judge; suite tag ``generate_email``.
"""

from __future__ import annotations

import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT      = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _TESTS_DIR)

from api_client import call_baseline_env, call_new_env
from judge import evaluate_response
from logger import log_result
from constants import NEPTUNE_URL_A, NEPTUNE_URL_B, PASS_THRESHOLD, REVIEW_THRESHOLD

from stream_helpers import (
    extract_generated_text,
    extract_chat_id,
    validate_streamed_text_output,
)

PATH = "/v1/generate-email"

EMAIL_KEYS = [
    "schedule_interview",
    "candidate_outreach",
    "application_status",
    "interview_feedback",
    "rejection_email",
    "job_offer_email",
    "client_outreach",
]

TONES = ["casual", "friendly", "formal", "neutral"]

_JUDGE_PROMPT_CAP = 2000
_JUDGE_LR_CAP = 4000


def _decide(schema_valid_b: bool, judge: dict) -> str:
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


def _generate_email_judge_task_input(payload: dict) -> dict:
    ti: dict = {
        "task": "generate_email",
        "key":  payload.get("key"),
        "tone": payload.get("tone"),
    }
    p = payload.get("prompt") or ""
    if p.strip():
        if len(p) <= _JUDGE_PROMPT_CAP:
            ti["prompt"] = p
        else:
            ti["prompt"] = p[:_JUDGE_PROMPT_CAP] + "…"
            ti["prompt_total_chars"] = len(p)
    lr = payload.get("last_response") or ""
    if lr.strip():
        if len(lr) <= _JUDGE_LR_CAP:
            ti["last_response"] = lr
        else:
            ti["last_response"] = lr[:_JUDGE_LR_CAP] + "…"
            ti["last_response_total_chars"] = len(lr)
    if payload.get("chat_id"):
        ti["chat_id_present"] = True
    return ti


def run_generate_email_test(
    test_name: str,
    payload_a: dict,
    payload_b: dict | None = None,
) -> dict:
    payload_b = payload_b if payload_b is not None else payload_a

    print(f"\n{'='*60}")
    print(f"  TEST: {test_name}")
    print(f"  KEY : {payload_a.get('key')}  |  TONE: {payload_a.get('tone')}")
    print(f"{'='*60}")

    result_a = call_baseline_env(PATH, payload_a, stream=True, base_url=NEPTUNE_URL_A)
    result_b = call_new_env(PATH, payload_b, stream=True, base_url=NEPTUNE_URL_B)

    text_a = extract_generated_text(result_a["response_text"])
    text_b = extract_generated_text(result_b["response_text"])

    schema_a = validate_streamed_text_output(text_a, min_chars=2)
    schema_b = validate_streamed_text_output(text_b, min_chars=2)

    if result_a["error"]:
        print(f"  [ENV A ERROR] {result_a['error']}")
    if result_b["error"]:
        print(f"  [ENV B ERROR] {result_b['error']}")

    judge = evaluate_response(
        response_existing=text_a or "[empty]",
        response_new=text_b or "[empty]",
        task_input=_generate_email_judge_task_input(payload_a),
        extra_context=f"endpoint={PATH}; env_A=baseline existing; env_B=new candidate",
    )
    status = _decide(schema_b["schema_valid"], judge)

    entry = log_result(
        endpoint=PATH,
        action=f"{payload_a.get('key')}:{payload_a.get('tone')}",
        latency_ms_a=result_a["latency_ms"],
        latency_ms_b=result_b["latency_ms"],
        schema_valid_a=schema_a["schema_valid"],
        schema_valid_b=schema_b["schema_valid"],
        judge_score=judge.get("score"),
        dimension_scores=None,
        final_status=status,
        text_a=judge.get("_response_a", text_a),
        text_b=judge.get("_response_b", text_b),
        input_text=judge.get("_input_text", ""),
        extra={
            "test_name":      test_name,
            "suite":          "generate_email",
            "key":            payload_a.get("key"),
            "tone":           payload_a.get("tone"),
            "prompt":         payload_a.get("prompt", ""),
            "schema_error_a": schema_a.get("error"),
            "schema_error_b": schema_b.get("error"),
            "judge_reason":   judge.get("reason"),
            "ttft_ms_a":      result_a.get("ttft_ms"),
            "ttft_ms_b":      result_b.get("ttft_ms"),
        },
    )

    print(f"  STATUS        : {status}")
    print(f"  JUDGE SCORE   : {judge.get('score', 'n/a')}")
    print(f"  JUDGE REASON  : {judge.get('reason', judge.get('error', ''))}")
    print(f"  LATENCY A     : {result_a['latency_ms']} ms  |  TTFT A: {result_a.get('ttft_ms')} ms")
    print(f"  LATENCY B     : {result_b['latency_ms']} ms  |  TTFT B: {result_b.get('ttft_ms')} ms")

    return entry


def _cold_payload(key: str, tone: str) -> dict:
    """Matches sample app: empty prompt and last_response for structured generation."""
    return {
        "prompt":         "",
        "key":            key,
        "tone":           tone,
        "last_response":  "",
    }


def build_full_matrix() -> list[tuple[str, dict]]:
    """Every (key, tone) pair — 8 × 4 = 32 cases."""
    out: list[tuple[str, dict]] = []
    for key in EMAIL_KEYS:
        for tone in TONES:
            name = f"GE-{key}-{tone}"
            out.append((name, _cold_payload(key, tone)))
    return out


DIRECT_MANUAL_PROMPT_CASES: list[tuple[str, dict]] = [
    (
        "GE-DM1 manual_prompt + formal — offer acknowledgment",
        {
            "prompt": (
                "Write a short email asking the candidate to confirm they received the offer PDF "
                "and list two documents we still need before the start date."
            ),
            "key":            "manual_prompt",
            "tone":           "formal",
            "last_response":  "",
        },
    ),
    (
        "GE-DM2 manual_prompt + friendly — interview reschedule",
        {
            "prompt": (
                "The panel had to move Thursday’s slot; ask for two alternative afternoons next week "
                "and apologize once in the opening line."
            ),
            "key":            "manual_prompt",
            "tone":           "friendly",
            "last_response":  "",
        },
    ),
    (
        "GE-DM3 manual_prompt + casual — quick client ping",
        {
            "prompt": (
                "Ping the HM: one line on three profiles in pipeline, ask if they want a call Friday "
                "or async feedback — keep it light."
            ),
            "key":            "manual_prompt",
            "tone":           "casual",
            "last_response":  "",
        },
    ),
]


def run_chained_manual_test(
    test_name: str,
    first_key: str,
    first_tone: str,
    second_prompt: str,
    second_tone: str,
) -> dict:
    """
    First: cold email with ``first_key`` + ``first_tone``.
    Second: ``manual_prompt`` + ``second_tone`` + ``last_response`` + ``chat_id`` per env.
    """
    print(f"\n{'='*60}")
    print(f"  CHAIN → manual_prompt: {test_name}")
    print(f"{'='*60}")

    first = _cold_payload(first_key, first_tone)

    gen_a = call_baseline_env(PATH, first, stream=True, base_url=NEPTUNE_URL_A)
    gen_b = call_new_env(PATH, first, stream=True, base_url=NEPTUNE_URL_B)

    html_a = extract_generated_text(gen_a["response_text"])
    html_b = extract_generated_text(gen_b["response_text"])
    cid_a  = extract_chat_id(gen_a["response_text"])
    cid_b  = extract_chat_id(gen_b["response_text"])

    if gen_a["error"]:
        print(f"  [FIRST A ERROR] {gen_a['error']}")
    if gen_b["error"]:
        print(f"  [FIRST B ERROR] {gen_b['error']}")

    base = {
        "prompt":        second_prompt,
        "key":           "manual_prompt",
        "tone":          second_tone,
        "last_response": html_a,
    }
    payload_a = dict(base)
    if cid_a:
        payload_a["chat_id"] = cid_a

    payload_b = {
        "prompt":        second_prompt,
        "key":           "manual_prompt",
        "tone":          second_tone,
        "last_response": html_b,
    }
    if cid_b:
        payload_b["chat_id"] = cid_b

    print(f"  [CHAIN] first={first_key}/{first_tone}  len A={len(html_a)} B={len(html_b)}  chat_id A={cid_a!r} B={cid_b!r}")

    return run_generate_email_test(test_name, payload_a, payload_b)


def _count(entry: dict, c: dict) -> None:
    s = entry["final_status"]
    if s == "PASS":
        c["passed"] += 1
    elif s == "FAIL":
        c["failed"] += 1
    elif s == "REVIEW":
        c["review"] += 1
    else:
        c["errored"] += 1


CHAINED_CASES = [
    (
        "GE-CH1 schedule_interview/formal → manual/friendly",
        "schedule_interview",
        "formal",
        "Shorten the email to under 120 words and add a clear single call-to-action line.",
        "friendly",
    ),
    (
        "GE-CH2 rejection_email/neutral → manual/casual",
        "rejection_email",
        "neutral",
        "Soften the opening; keep the rest; still professional.",
        "casual",
    ),
    (
        "GE-CH3 job_offer_email/formal → manual/formal",
        "job_offer_email",
        "formal",
        "Add a bullet list of next steps (background check, documents, joining date placeholder).",
        "formal",
    ),
    (
        "GE-CH4 client_outreach/friendly → manual/neutral",
        "client_outreach",
        "friendly",
        "Rewrite in neutral tone for legal review; remove exclamation marks.",
        "neutral",
    ),
]


def run_all():
    results: list = []
    c = {"passed": 0, "failed": 0, "review": 0, "errored": 0}

    matrix = build_full_matrix()
    print(f"\n  Running full matrix: {len(matrix)} key×tone combinations (8 keys × 4 tones).\n")

    for name, payload in matrix:
        entry = run_generate_email_test(name, payload)
        results.append(entry)
        _count(entry, c)

    print(f"\n  Direct manual_prompt (non-empty prompt): {len(DIRECT_MANUAL_PROMPT_CASES)} cases.\n")
    for name, payload in DIRECT_MANUAL_PROMPT_CASES:
        entry = run_generate_email_test(name, payload)
        results.append(entry)
        _count(entry, c)

    for name, fk, ft, sp, st in CHAINED_CASES:
        entry = run_chained_manual_test(name, fk, ft, sp, st)
        results.append(entry)
        _count(entry, c)

    print(f"\n{'='*60}")
    print("  GENERATE EMAIL — TEST RUN COMPLETE")
    n_direct = len(DIRECT_MANUAL_PROMPT_CASES)
    print(
        f"  Total: {len(results)}  (matrix {len(matrix)} + direct manual {n_direct} + "
        f"chained {len(CHAINED_CASES)})  |  "
        f"PASS: {c['passed']}  |  REVIEW: {c['review']}  |  FAIL: {c['failed']}  |  ERROR: {c['errored']}"
    )
    scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    if scores:
        print(f"  Avg Judge Score: {sum(scores) / len(scores):.2f}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    run_all()

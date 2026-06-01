"""
tests/test_email_template.py
----------------------------
Endpoint : POST /v1/generate-email-template

This feature only uses ``key: "manual_prompt"`` — there is no separate
``summarise`` / ``keypoints`` / ``improve`` flow in the product contract we model.

Payload shapes:
  - Cold: ``{ "prompt": "...", "key": "manual_prompt" }``
  - With history: same + ``last_response`` (prior template HTML/text) + ``chat_id`` (``conv_…`` from stream)

Chaining: first POST (prompt only) → ``extract_generated_text`` + ``extract_chat_id`` per env →
second POST adds ``last_response`` + ``chat_id`` per env (like job/candidate manual refine).
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

PATH = "/v1/generate-email-template"
KEY_MANUAL = "manual_prompt"
_JUDGE_LAST_CAP = 4000


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


def _email_template_judge_task_input(payload: dict) -> dict:
    ti: dict = {
        "task": "email_template",
        "key":  payload.get("key"),
        "prompt": payload.get("prompt") or "",
    }
    lr = payload.get("last_response")
    if isinstance(lr, str) and lr.strip():
        if len(lr) <= _JUDGE_LAST_CAP:
            ti["last_response"] = lr
        else:
            ti["last_response"] = lr[:_JUDGE_LAST_CAP] + "…"
            ti["last_response_total_chars"] = len(lr)
    if payload.get("chat_id"):
        ti["chat_id_present"] = True
    return ti


def run_email_template_test(
    test_name: str,
    payload_a: dict,
    payload_b: dict | None = None,
) -> dict:
    payload_b = payload_b if payload_b is not None else payload_a

    print(f"\n{'='*60}")
    print(f"  TEST: {test_name}")
    print(f"  KEY : {payload_a.get('key')}")
    print(f"{'='*60}")

    result_a = call_baseline_env(PATH, payload_a, stream=True, base_url=NEPTUNE_URL_A)
    result_b = call_new_env(PATH, payload_b, stream=True, base_url=NEPTUNE_URL_B)

    text_a = extract_generated_text(result_a["response_text"])
    text_b = extract_generated_text(result_b["response_text"])

    schema_a = validate_streamed_text_output(text_a, min_chars=3)
    schema_b = validate_streamed_text_output(text_b, min_chars=3)

    if result_a["error"]:
        print(f"  [ENV A ERROR] {result_a['error']}")
    if result_b["error"]:
        print(f"  [ENV B ERROR] {result_b['error']}")

    judge = evaluate_response(
        response_existing=text_a or "[empty]",
        response_new=text_b or "[empty]",
        task_input=_email_template_judge_task_input(payload_a),
        extra_context=f"endpoint={PATH}; env_A=baseline existing; env_B=new candidate",
    )
    status = _decide(schema_b["schema_valid"], judge)

    entry = log_result(
        endpoint=PATH,
        action=payload_a.get("key", KEY_MANUAL),
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
            "suite":          "email_template",
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


def run_email_template_chained_test(
    test_name: str,
    first_prompt: str,
    second_prompt: str,
) -> dict:
    """
    First call: ``manual_prompt`` + ``prompt`` only.
    Second call: same + ``last_response`` from first stream + ``chat_id`` if present.
    """
    print(f"\n{'='*60}")
    print(f"  CHAIN — email template → manual_prompt (refine): {test_name}")
    print(f"{'='*60}")

    first = {"prompt": first_prompt, "key": KEY_MANUAL}

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

    base = {"key": KEY_MANUAL, "prompt": second_prompt}

    payload_a = {**base, "last_response": html_a}
    if cid_a:
        payload_a["chat_id"] = cid_a

    payload_b = {**base, "last_response": html_b}
    if cid_b:
        payload_b["chat_id"] = cid_b

    print(
        f"  [CHAIN] first output len  A={len(html_a)}  B={len(html_b)}  |  "
        f"chat_id A={cid_a!r}  B={cid_b!r}"
    )

    return run_email_template_test(test_name, payload_a, payload_b)


def _p(prompt: str) -> dict:
    return {"prompt": prompt, "key": KEY_MANUAL}


ET_COLD_1 = _p(
    "hiring stage updation — write a short professional email to the candidate with "
    "subject line and placeholders {Candidate Name}, {Role}, {Company}."
)
ET_COLD_2 = _p(
    "Interview invitation for a senior backend role: include date/time placeholder, "
    "video link placeholder, duration 60 minutes, and what to prepare (system design)."
)
ET_COLD_3 = _p(
    "Write a polite rejection email after final round — thank them, mention strong pool, "
    "offer to consider for future roles, no specific feedback."
)
ET_COLD_4 = _p(
    "Client update email to the hiring manager: 3 profiles submitted this week, "
    "1 in final round, ask for feedback SLA by EOD Friday."
)
ET_COLD_5 = _p(
    "Offer rollout email to candidate: congratulate, attach next steps for background check, "
    "mention joining date placeholder and single point of contact placeholder."
)
ET_COLD_6 = _p(
    "Reminder email — candidate has not responded to interview availability request for 48h; "
    "tone friendly, one clear CTA to reply with slots."
)
ET_COLD_7 = _p(
    "Internal handoff email to sales: new enterprise AE hire starting next month, "
    "ask for laptop + access checklist, BCC recruiting ops."
)
ET_COLD_8 = _p(
    "Referral thank-you email to employee: acknowledge referral for {Referred Candidate}, "
    "state referral bonus policy placeholder, keep under 120 words."
)

ALL_STANDALONE_CASES = [
    ("ET-1 Hiring stage update",              ET_COLD_1),
    ("ET-2 Interview invite (backend)",       ET_COLD_2),
    ("ET-3 Final-round rejection",            ET_COLD_3),
    ("ET-4 Client HM weekly update",          ET_COLD_4),
    ("ET-5 Offer / background check",         ET_COLD_5),
    ("ET-6 Interview reminder (no response)", ET_COLD_6),
    ("ET-7 Internal IT handoff",              ET_COLD_7),
    ("ET-8 Referral thank-you",               ET_COLD_8),
]


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


def run_all():
    results: list = []
    c = {"passed": 0, "failed": 0, "review": 0, "errored": 0}

    for name, payload in ALL_STANDALONE_CASES:
        entry = run_email_template_test(name, payload)
        results.append(entry)
        _count(entry, c)

    ch1 = run_email_template_chained_test(
        "ET-C1 Chain — stage update → rejection tone",
        first_prompt=(
            "Write a warm email notifying the candidate they moved to the next hiring stage "
            "with placeholders for name and role."
        ),
        second_prompt=(
            "Rewrite the same template as a rejection email — keep structure but change tone; "
            "still use placeholders."
        ),
    )
    results.append(ch1)
    _count(ch1, c)

    ch2 = run_email_template_chained_test(
        "ET-C2 Chain — interview invite → shorter + bullet CTA",
        first_prompt=(
            "Draft interview invitation email with Subject, greeting, time placeholder, and "
            "preparation bullets."
        ),
        second_prompt=(
            "Make it under 100 words, replace bullets with a single short paragraph, "
            "keep subject line."
        ),
    )
    results.append(ch2)
    _count(ch2, c)

    print(f"\n{'='*60}")
    print("  EMAIL TEMPLATE — TEST RUN COMPLETE")
    print(
        f"  Total: {len(results)}  |  PASS: {c['passed']}  |  REVIEW: {c['review']}  |  "
        f"FAIL: {c['failed']}  |  ERROR: {c['errored']}"
    )
    scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    if scores:
        print(f"  Avg Judge Score: {sum(scores) / len(scores):.2f}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    run_all()

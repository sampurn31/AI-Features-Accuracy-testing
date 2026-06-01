"""
tests/test_get_suggestions.py
-----------------------------
Endpoint : POST /v1/get-suggestions

Body : ``{ "data": "<html or plain text from the editor>" }`` — no ``key`` field.

Neptune streams many JSON objects with ``data.generated_text`` fragments, then a **final**
chunk with ``data.complete_message`` holding the full suggestions JSON string
(``senders_intent``, ``prompts``, etc.).  We evaluate using
``suggestions_stream_text`` which prefers ``complete_message`` when present.

Realistic recruiter ``data`` fixtures below cover common compose / note / email snippets.
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

from stream_helpers import suggestions_stream_text, validate_suggestions_output

PATH = "/v1/get-suggestions"
_JUDGE_DATA_CAP = 4500


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


def _suggestions_judge_task_input(payload: dict) -> dict:
    raw = payload.get("data") or ""
    ti: dict = {"task": "get_suggestions"}
    if len(raw) <= _JUDGE_DATA_CAP:
        ti["editor_data"] = raw
    else:
        ti["editor_data"] = raw[:_JUDGE_DATA_CAP] + "…"
        ti["editor_data_total_chars"] = len(raw)
    return ti


def run_get_suggestions_test(test_name: str, payload_a: dict, payload_b: dict | None = None) -> dict:
    payload_b = payload_b if payload_b is not None else payload_a

    print(f"\n{'='*60}")
    print(f"  TEST: {test_name}")
    print(f"{'='*60}")

    result_a = call_baseline_env(PATH, payload_a, stream=True, base_url=NEPTUNE_URL_A)
    result_b = call_new_env(PATH, payload_b, stream=True, base_url=NEPTUNE_URL_B)

    text_a = suggestions_stream_text(result_a["response_text"])
    text_b = suggestions_stream_text(result_b["response_text"])

    schema_a = validate_suggestions_output(text_a)
    schema_b = validate_suggestions_output(text_b)

    if result_a["error"]:
        print(f"  [ENV A ERROR] {result_a['error']}")
    if result_b["error"]:
        print(f"  [ENV B ERROR] {result_b['error']}")

    judge = evaluate_response(
        response_existing=text_a or "[empty]",
        response_new=text_b or "[empty]",
        task_input=_suggestions_judge_task_input(payload_a),
        extra_context=f"endpoint={PATH}; output should be JSON from complete_message when present; env_A=baseline; env_B=new",
    )
    status = _decide(schema_b["schema_valid"], judge)

    entry = log_result(
        endpoint=PATH,
        action="get_suggestions",
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
            "suite":          "get_suggestions",
            "schema_error_a": schema_a.get("error"),
            "schema_error_b": schema_b.get("error"),
            "judge_reason":   judge.get("reason"),
            "ttft_ms_a":      result_a.get("ttft_ms"),
            "ttft_ms_b":      result_b.get("ttft_ms"),
        },
    )

    print(f"  STATUS        : {status}")
    print(f"  JUDGE SCORE   : {judge.get('score', 'n/a')}")
    print(f"  OUT LEN A/B   : {len(text_a)} / {len(text_b)} chars")
    print(f"  LATENCY A     : {result_a['latency_ms']} ms  |  TTFT A: {result_a.get('ttft_ms')} ms")
    print(f"  LATENCY B     : {result_b['latency_ms']} ms  |  TTFT B: {result_b.get('ttft_ms')} ms")

    return entry


def _p(html: str) -> dict:
    return {"data": html}


GS_01 = _p("<p>Interview update</p>")

GS_02 = _p(
    "<p>Hi team — quick note: candidate cleared tech round 2. "
    "HM wants panel on Thu if calendar works. Please ping me slots.</p>"
)

GS_03 = _p(
    "<p>Dear candidate,</p><p>Thank you for your time yesterday. "
    "We will revert by EOW with feedback.</p><p>Regards,<br>Recruiting</p>"
)

GS_04 = _p(
    "<p><strong>Rejection draft</strong></p>"
    "<p>We enjoyed learning about your experience. We will not be moving forward "
    "for this role but encourage you to apply again in the future.</p>"
)

GS_05 = _p(
    "<p>Offer discussion — verbal agreed 42L + variable. "
    "Need written acceptance by Monday. Spouse has relocation questions.</p>"
)

GS_06 = _p(
    "<p>Client email — please share 2 more senior Java profiles by Friday COB. "
    "Budget cap 45L. Remote IST only.</p>"
)

GS_07 = _p(
    "<ul><li>Follow up on reference from former manager</li>"
    "<li>Background check initiated — ETA 5 days</li>"
    "<li>Offer letter pending legal review</li></ul>"
)

GS_08 = _p(
    "<p>Scheduling — candidate prefers after 6pm IST; panel in US West. "
    "Find 30-min overlap and send Meet link.</p>"
)

GS_09 = _p(
    "Plain text only: pls review this note for grammar and make it sound more professional "
    "without changing facts."
)

GS_10 = _p(
    "<p>Namaste — shortlisted for onsite. Travel from Hyderabad; need flight policy check "
    "before we confirm dates.</p>"
)

GS_11 = _p(
    "<p><em>Internal</em>: Hiring freeze exception requested for req #991. "
    "VP approval pending — do not communicate to candidate yet.</p>"
)

GS_12 = _p(
    "<p>Subject: Reminder — documents</p>"
    "<p>Please upload last payslip and ID proof to the portal to avoid delay.</p>"
)

ALL_CASES = [
    ("GS-01 Minimal interview line",           GS_01),
    ("GS-02 Tech round / scheduling",         GS_02),
    ("GS-03 Thank-you after interview",       GS_03),
    ("GS-04 Rejection draft",                 GS_04),
    ("GS-05 Offer / relocation",              GS_05),
    ("GS-06 Client sourcing ask",             GS_06),
    ("GS-07 Bullet checklist",                GS_07),
    ("GS-08 Timezone scheduling",             GS_08),
    ("GS-09 Plain grammar help",              GS_09),
    ("GS-10 Travel / onsite",                 GS_10),
    ("GS-11 Internal hiring freeze",          GS_11),
    ("GS-12 Document reminder",               GS_12),
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

    print(f"\n  get-suggestions: {len(ALL_CASES)} recruiter-style editor payloads.\n")

    for name, payload in ALL_CASES:
        entry = run_get_suggestions_test(name, payload)
        results.append(entry)
        _count(entry, c)

    print(f"\n{'='*60}")
    print("  GET SUGGESTIONS — TEST RUN COMPLETE")
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

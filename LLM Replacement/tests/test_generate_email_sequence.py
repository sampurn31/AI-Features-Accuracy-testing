"""
tests/test_generate_email_sequence.py
--------------------------------------
Endpoint : POST /v1/generate-email-sequence

The API generates a multi-step drip sequence (Email + Task steps) and streams
JSON chunks with ``data.generated_text``.  The **last** chunk carries
``data.complete_message`` — a string of back-tick-wrapped step JSON objects.

Request body:
    {
      "entity_type"   : "candidates" | "contacts",
      "seq_step_type" : ""  |  <int step_number>,   # "" = initial, int = regen
      "last_response" : ""  |  "<step content>",    # "" = initial, HTML = regen
      "prompt"        : "<user instruction>"
    }

Test phases
-----------
Phase 1 – Initial generation (entity_type × prompt variety)
Phase 2 – Fixed regeneration (known last_response + new prompt → both envs)
Phase 3 – Live-chained regeneration
            a) Call env A with an initial prompt.
            b) Parse the steps from complete_message.
            c) Pick step N and regenerate it with a new prompt on both envs.
"""

from __future__ import annotations

import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _TESTS_DIR)

from api_client import call_baseline_env, call_new_env
from judge import evaluate_response
from logger import log_result
from constants import NEPTUNE_URL_A, NEPTUNE_URL_B, PASS_THRESHOLD, REVIEW_THRESHOLD

from stream_helpers import (
    email_sequence_stream_text,
    parse_email_sequence_steps,
    validate_email_sequence_output,
)

PATH = "/v1/generate-email-sequence"
_PROMPT_CAP = 3000


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


def _judge_task_input(payload: dict, mode: str = "initial") -> dict:
    ti: dict = {"task": "generate_email_sequence", "mode": mode}
    for k in ("entity_type", "prompt", "seq_step_type"):
        if payload.get(k) not in (None, ""):
            ti[k] = payload[k]
    lr = payload.get("last_response") or ""
    if lr:
        ti["last_response"] = lr[:_PROMPT_CAP] + ("…" if len(lr) > _PROMPT_CAP else "")
    return ti


def run_sequence_test(
    test_name: str,
    payload_a: dict,
    payload_b: dict | None = None,
    mode: str = "initial",
) -> dict:
    payload_b = payload_b if payload_b is not None else payload_a

    print(f"\n{'='*64}")
    print(f"  TEST [{mode.upper()}]: {test_name}")
    print(f"{'='*64}")

    result_a = call_baseline_env(PATH, payload_a, stream=True, base_url=NEPTUNE_URL_A)
    result_b = call_new_env(PATH, payload_b, stream=True, base_url=NEPTUNE_URL_B)

    text_a = email_sequence_stream_text(result_a["response_text"])
    text_b = email_sequence_stream_text(result_b["response_text"])

    val_a = validate_email_sequence_output(text_a)
    val_b = validate_email_sequence_output(text_b)

    if result_a["error"]:
        print(f"  [ENV A ERROR] {result_a['error']}")
    if result_b["error"]:
        print(f"  [ENV B ERROR] {result_b['error']}")

    steps_a = val_a.get("steps", [])
    steps_b = val_b.get("steps", [])
    print(f"  Steps parsed  A:{len(steps_a)}  B:{len(steps_b)}")

    judge = evaluate_response(
        response_existing=text_a or "[empty]",
        response_new=text_b or "[empty]",
        task_input=_judge_task_input(payload_a, mode),
        extra_context=(
            f"endpoint={PATH}; mode={mode}; entity_type={payload_a.get('entity_type')}; "
            f"env_A=baseline; env_B=new; evaluate completeness, relevance, tone, step variety"
        ),
    )
    status = _decide(val_b["schema_valid"], judge)

    entry = log_result(
        endpoint=PATH,
        action="generate_email_sequence",
        latency_ms_a=result_a["latency_ms"],
        latency_ms_b=result_b["latency_ms"],
        schema_valid_a=val_a["schema_valid"],
        schema_valid_b=val_b["schema_valid"],
        judge_score=judge.get("score"),
        dimension_scores=None,
        final_status=status,
        text_a=judge.get("_response_a", text_a),
        text_b=judge.get("_response_b", text_b),
        input_text=judge.get("_input_text", ""),
        extra={
            "test_name":      test_name,
            "suite":          "generate_email_sequence",
            "mode":           mode,
            "entity_type":    payload_a.get("entity_type"),
            "seq_step_type":  payload_a.get("seq_step_type"),
            "steps_a":        len(steps_a),
            "steps_b":        len(steps_b),
            "schema_error_a": val_a.get("error"),
            "schema_error_b": val_b.get("error"),
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


def _p(entity_type: str, prompt: str, seq_step_type="", last_response="") -> dict:
    return {
        "entity_type":   entity_type,
        "seq_step_type": seq_step_type,
        "last_response": last_response,
        "prompt":        prompt,
    }


PHASE1_CANDIDATES = [
    ("ES-C1  Outbound – Senior Backend Engineers",
     _p("candidates", "Outbound sourcing for Senior Backend Engineers")),

    ("ES-C2  Passive nurture – Python/ML engineers (startup)",
     _p("candidates", "Nurture passive Python/ML engineers for an early-stage AI startup")),

    ("ES-C3  Re-engagement – cold candidates",
     _p("candidates", "Re-engage cold candidates who haven't responded in 90 days")),

    ("ES-C4  Campus recruitment – fresh graduates",
     _p("candidates", "Campus recruitment drive targeting final-year CS graduates")),

    ("ES-C5  Executive search – VP Product",
     _p("candidates", "Executive search sequence for VP of Product, fintech sector")),

    ("ES-C6  Post-interview follow-up",
     _p("candidates", "Follow-up sequence after a technical interview – keep candidate warm")),

    ("ES-C7  Rejection with talent pool invite",
     _p("candidates", "Polite rejection email followed by talent pool nurture for future roles")),

    ("ES-C8  Offer negotiation & acceptance",
     _p("candidates", "Offer extended – close the candidate with compensation & perks highlights")),

    ("ES-C9  Diversity hiring initiative",
     _p("candidates", "Diversity-focused outreach for engineering roles – inclusive tone")),

    ("ES-C10 Urgent backfill – immediate joiner",
     _p("candidates", "Urgent hiring – need a DevOps engineer who can join in 2 weeks")),
]


PHASE1_CONTACTS = [
    ("ES-K1  Client acquisition – new account",
     _p("contacts", "Client acquisition sequence targeting HR heads at Series B startups")),

    ("ES-K2  Panel / interviewer engagement",
     _p("contacts", "Engage hiring managers to participate as interviewers for open roles")),

    ("ES-K3  Quarterly business review invite",
     _p("contacts", "Invite existing clients to a quarterly business review meeting")),

    ("ES-K4  RPO pitch to enterprise HR",
     _p("contacts", "Pitch Recruitment Process Outsourcing (RPO) services to enterprise clients")),
]


_EMAIL_STEP2_LAST = (
    "Hi {candidate_first_name},<br><br>"
    "I wanted to follow up on my previous message about the Senior Backend Engineer position.<br><br>"
    "We believe your skills would be a great fit. Please let me know if you're open to a quick chat.<br><br>"
    "Best Regards,<br>[Your Signature]"
)

_TASK_STEP4_LAST = (
    "Send a LinkedIn connection request to potential candidates who have not responded. "
    "Include a note about the Senior Backend Engineer role."
)

_EMAIL_STEP1_LAST = (
    "Hi {candidate_first_name},<br><br>"
    "We are excited about an opening for a Data Scientist at our company.<br><br>"
    "If you have experience with Python, ML pipelines, and NLP, we'd love to connect.<br><br>"
    "Best Regards,<br>[Your Signature]"
)

PHASE2_FIXED_REGEN = [
    ("ES-R1  Regen step 2 – shorter & casual tone",
     _p("candidates", "make it shorter and more casual", seq_step_type=2,
        last_response=_EMAIL_STEP2_LAST)),

    ("ES-R2  Regen step 2 – emphasise remote & compensation",
     _p("candidates",
        "focus on remote work flexibility and competitive compensation in this follow-up",
        seq_step_type=2, last_response=_EMAIL_STEP2_LAST)),

    ("ES-R3  Regen step 4 task – add personalised LinkedIn note text",
     _p("candidates",
        "include a suggested personalised connection note mentioning the candidate's background",
        seq_step_type=4, last_response=_TASK_STEP4_LAST)),

    ("ES-R4  Regen step 1 – add social proof & company culture",
     _p("candidates",
        "add a sentence about company culture and mention a recent product launch",
        seq_step_type=1, last_response=_EMAIL_STEP1_LAST)),

    ("ES-R5  Regen step 1 – hiring stage update prompt (from sample curl)",
     _p("candidates",
        "hiring stage updation",
        seq_step_type=2,
        last_response=(
            "<p>Prepare and send feedback to candidates who have completed interviews, "
            "regardless of the outcome.</p>"
        ))),
]


def _pick_step_for_regen(steps: list[dict], preferred_type: str = "Email") -> dict | None:
    """Return the first step of ``preferred_type``; fall back to any step."""
    for s in steps:
        if s.get("type") == preferred_type:
            return s
    return steps[0] if steps else None


def _step_last_response(step: dict) -> str:
    """Return the regeneration-eligible content of a step (email_body or description)."""
    return step.get("email_body") or step.get("description") or ""


def run_chained_regen_test(
    test_name: str,
    seed_payload: dict,
    regen_prompt: str,
    preferred_step_type: str = "Email",
) -> list[dict]:
    """
    1. Call env A with ``seed_payload`` (initial).
    2. Parse the steps from env A's complete_message.
    3. Pick a step and regenerate it on **both** envs with ``regen_prompt``.
    Returns log entries for the seed call + regen call.
    """
    print(f"\n{'*'*64}")
    print(f"  CHAINED REGEN SEED: {test_name}")
    print(f"{'*'*64}")

    entries: list[dict] = []

    seed_result_a = call_baseline_env(PATH, seed_payload, stream=True, base_url=NEPTUNE_URL_A)
    seed_text_a = email_sequence_stream_text(seed_result_a["response_text"])
    seed_val_a = validate_email_sequence_output(seed_text_a)
    steps_a = seed_val_a.get("steps", [])

    print(f"  Seed steps parsed (env A): {len(steps_a)}")

    if not steps_a:
        print("  [SKIP] No steps parsed from env A seed – cannot chain.")
        return entries

    target_step = _pick_step_for_regen(steps_a, preferred_step_type)
    if not target_step:
        print("  [SKIP] No suitable step found for regeneration.")
        return entries

    step_num = int(target_step.get("step_number", 1))
    last_resp = _step_last_response(target_step)
    print(f"  Regenerating step {step_num} ({target_step.get('type')}) with: \"{regen_prompt}\"")

    regen_payload = _p(
        seed_payload["entity_type"],
        regen_prompt,
        seq_step_type=step_num,
        last_response=last_resp,
    )

    regen_entry = run_sequence_test(
        f"{test_name} → regen step {step_num}",
        regen_payload,
        mode="regen_chained",
    )
    entries.append(regen_entry)

    return entries


PHASE3_CHAINED = [
    (
        "ES-L1  Outbound Backend → add urgency to first email",
        _p("candidates", "Outbound sourcing for Senior Backend Engineers"),
        "add urgency – role needs to be filled by end of month",
        "Email",
    ),
    (
        "ES-L2  ML Engineer nurture → make task step more specific",
        _p("candidates", "Nurture passive ML engineers for a healthcare AI company"),
        "add a LinkedIn InMail template with a personalised intro line",
        "Task",
    ),
    (
        "ES-L3  Client acquisition → regen follow-up email in formal tone",
        _p("contacts", "Client acquisition sequence targeting HR directors at Fortune 500 companies"),
        "rewrite in a formal executive tone and reference ROI metrics",
        "Email",
    ),
    (
        "ES-L4  Post-interview warm-up → shorten and add emoji",
        _p("candidates", "Follow-up sequence after a technical interview – keep candidate warm"),
        "make it shorter, friendly, and add a relevant emoji to the subject",
        "Email",
    ),
]


def _count(entry: dict, c: dict) -> None:
    s = entry.get("final_status", "ERROR")
    if s == "PASS":
        c["passed"] += 1
    elif s == "FAIL":
        c["failed"] += 1
    elif s == "REVIEW":
        c["review"] += 1
    else:
        c["errored"] += 1


def run_all():
    results: list[dict] = []
    c = {"passed": 0, "failed": 0, "review": 0, "errored": 0}

    print("\n" + "─" * 64)
    print("  PHASE 1 — INITIAL GENERATION (candidates)")
    print("─" * 64)
    for name, payload in PHASE1_CANDIDATES:
        e = run_sequence_test(name, payload, mode="initial")
        results.append(e); _count(e, c)

    print("\n" + "─" * 64)
    print("  PHASE 1 — INITIAL GENERATION (contacts / clients)")
    print("─" * 64)
    for name, payload in PHASE1_CONTACTS:
        e = run_sequence_test(name, payload, mode="initial")
        results.append(e); _count(e, c)

    print("\n" + "─" * 64)
    print("  PHASE 2 — FIXED REGENERATION (known last_response)")
    print("─" * 64)
    for name, payload in PHASE2_FIXED_REGEN:
        e = run_sequence_test(name, payload, mode="regen_fixed")
        results.append(e); _count(e, c)

    print("\n" + "─" * 64)
    print("  PHASE 3 — LIVE-CHAINED REGENERATION")
    print("─" * 64)
    for name, seed_payload, regen_prompt, pref in PHASE3_CHAINED:
        chain_entries = run_chained_regen_test(name, seed_payload, regen_prompt, pref)
        for e in chain_entries:
            results.append(e); _count(e, c)

    print(f"\n{'='*64}")
    print("  GENERATE EMAIL SEQUENCE — TEST RUN COMPLETE")
    total = len(results)
    print(
        f"  Total: {total}  |  PASS: {c['passed']}  |  REVIEW: {c['review']}  "
        f"|  FAIL: {c['failed']}  |  ERROR: {c['errored']}"
    )
    scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    if scores:
        print(f"  Avg Judge Score : {sum(scores) / len(scores):.2f}")
    print(f"{'='*64}\n")

    return results


if __name__ == "__main__":
    run_all()

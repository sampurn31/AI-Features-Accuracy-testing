"""
tests/test_call_log_summary.py
------------------------------
Endpoint : POST /v1/generate-call-log-summary

Same contract as note summary: body uses ``note`` + ``key`` (``summarise``,
``keypoints``, ``improve``, ``manual_prompt``).  Streaming JSON chunks;
``data.id`` (``conv_...``) chained as request field ``chat_id`` on follow-up.

Suite mirrors ``test_note_summary`` with **call / phone / transcript** style
``note`` content.  Plain-text output validation via ``validate_streamed_text_output``.
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

PATH = "/v1/generate-call-log-summary"
_JUDGE_NOTE_CAP = 5000


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


def _call_log_judge_task_input(payload: dict) -> dict:
    raw_note = payload.get("note") or ""
    ti: dict = {
        "task": "call_log_summary",
        "key":  payload.get("key"),
    }
    if len(raw_note) <= _JUDGE_NOTE_CAP:
        ti["note"] = raw_note
    else:
        ti["note"] = raw_note[:_JUDGE_NOTE_CAP] + "…"
        ti["note_total_chars"] = len(raw_note)
    if payload.get("prompt"):
        ti["user_refinement_instruction"] = payload["prompt"]
    if payload.get("chat_id"):
        ti["chat_id_present"] = True
    return ti


def run_call_log_summary_test(
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

    schema_a = validate_streamed_text_output(text_a)
    schema_b = validate_streamed_text_output(text_b)

    if result_a["error"]:
        print(f"  [ENV A ERROR] {result_a['error']}")
    if result_b["error"]:
        print(f"  [ENV B ERROR] {result_b['error']}")

    judge = evaluate_response(
        response_existing=text_a or "[empty]",
        response_new=text_b or "[empty]",
        task_input=_call_log_judge_task_input(payload_a),
        extra_context=f"endpoint={PATH}; env_A=baseline existing; env_B=new candidate",
    )
    status = _decide(schema_b["schema_valid"], judge)

    entry = log_result(
        endpoint=PATH,
        action=payload_a.get("key", "summarise"),
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
            "suite":          "call_log_summary",
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


def run_call_log_chained_manual_test(
    test_name: str,
    first_payload: dict,
    user_prompt: str,
) -> dict:
    print(f"\n{'='*60}")
    print(f"  CHAIN → manual_prompt: {test_name}")
    print(f"  FIRST KEY: {first_payload.get('key')}")
    print(f"{'='*60}")

    gen_a = call_baseline_env(PATH, first_payload, stream=True, base_url=NEPTUNE_URL_A)
    gen_b = call_new_env(PATH, first_payload, stream=True, base_url=NEPTUNE_URL_B)

    cid_a = extract_chat_id(gen_a["response_text"])
    cid_b = extract_chat_id(gen_b["response_text"])

    if gen_a["error"]:
        print(f"  [FIRST A ERROR] {gen_a['error']}")
    if gen_b["error"]:
        print(f"  [FIRST B ERROR] {gen_b['error']}")

    note = first_payload["note"]
    base = {"note": note, "key": "manual_prompt", "prompt": user_prompt}

    payload_a = dict(base)
    if cid_a:
        payload_a["chat_id"] = cid_a
    payload_b = dict(base)
    if cid_b:
        payload_b["chat_id"] = cid_b

    print(f"  [CHAIN] chat_id A={cid_a!r}  B={cid_b!r}")

    return run_call_log_summary_test(test_name, payload_a, payload_b)


CALL_INTERVIEW_SCHEDULE_HTML = """<p><strong>Outbound — Interview scheduling</strong><br>
Candidate: <strong>Neha Kapoor</strong> · Req #8821 (Product Designer)<br>
Duration: 14 min · Recruiter: me · Dialer logged 10:42 IST<br><br>
Confirmed panel for <em>Thu 3 Apr</em>, 3:00–4:30 PM IST (Figma deep-dive + portfolio walk).<br>
She asked for 15-min buffer before — added to calendar invite.<br>
Timezone: IST; no travel — fully remote Meet link (send from Greenhouse template v3).<br>
Reminder: she’s observing Ramadan; avoid lunch slot conflicts — done.<br>
Action: ping hiring manager Ritu to attach take-home rubric to invite by EOD.</p>"""

CALL_CLIENT_INTAKE = """Client intake — voice — TechNova (Series B) — 27 Mar 2026
HM: Sanjay (Head of Platform). Pain: hiring 2 senior backend for payments rail migration.
Timeline aggressive: first offer target 45 days. Interview loop = HM → system design → pair (Go) → exec (CTO).
Budget signed off up to 48L all-in per role; open to remote India, EU overlap 3h preferred.
Red flag mentioned: previous agency subs were “too junior”; wants production incident stories.
Next: send 3 profiles by Mon; standing sync Fri 4pm for feedback SLA."""

CALL_OFFER_NEGOTIATION = """Call log — candidate offer discussion — Arjun Menon — 26 Mar 2026
Joined from car, audio ok after 1 min. Confirmed our verbal: 42L base + 15% variable + Band-2 ESOP.
He asked about cliff (1 yr) and refresh policy — explained standard policy doc link on portal.
Competing verbal from “large e-comm” ~44L cash, less equity — he values equity long-term.
Asked for written offer by Wed; spouse wants to see relocation clause (Bangalore) even though role hybrid.
Committed to send FAQ + draft letter in 24h. Tagged #legal-review if relocation stipend requested."""

CALL_REFERENCE_STYLE = """Reference call notes — quick — former manager (would not go on record for recording)
Role overlap: 2019–2022, same team as our candidate Meera Shah.
Performance: “strong IC, reliable delivery”; promotion to senior was “deserved”.
Caveat: “can be slow to push back on PM scope creep” — coaching opportunity.
Would rehire if headcount allowed. No PIP, no HR issues known.
Relationship: still friendly LinkedIn; reference was warm not effusive."""

CALL_TECH_SCREEN_LONG = """Call debrief — technical phone screen — Backend (Go) — 45 min — candidate: Diego Alvarez
Opened with resume walk; 10 min on past Kafka outage story (clear ownership, metrics).
Live exercise: rate-limiter design — started token bucket, discussed Redis vs in-memory tradeoffs.
Missed edge case on clock skew until nudged; recovered well. SQL: window function for running total — correct.
English fluent; slight latency on video. Panel prep: recommend pair session focus on observability (OpenTelemetry).
Overall: proceed to onsite loop if HM approves; concern = limited K8s operator experience (flag for infra buddy)."""

CALL_ROUGH_SCRIBBLE = """called candidate re interview - mon??? maybe tues
said ok for zoom
salary ??? 40 something
need to send cal link
forgot to ask notice period - callback"""

CALL_CLIENT_SHORTHAND = """hm call - wants senior PM for growth team
mentioned okr tool experience nice to have
interviews start asap
send jd link again they lost it"""

CALL_NO_SHOW_FOLLOWUP = """<p><strong>Follow-up call — no-show recovery</strong><br>
Candidate: Li Wei · scheduled screen 11:00.<br>
Rescheduled to Fri 09:30 after apology (hospital visit — not verified).<br>
Sent SMS + email with new link; asked to confirm 24h prior.<br>
If second no-show → close file per policy.</p>"""


def _p(note: str, key: str) -> dict:
    return {"note": note, "key": key}


CL_SUM_1 = _p(CALL_INTERVIEW_SCHEDULE_HTML, "summarise")
CL_SUM_2 = _p(CALL_CLIENT_INTAKE, "summarise")
CL_SUM_3 = _p(CALL_OFFER_NEGOTIATION, "summarise")
CL_SUM_4 = _p(CALL_REFERENCE_STYLE, "summarise")

CL_KP_1 = _p(CALL_TECH_SCREEN_LONG, "keypoints")
CL_KP_2 = _p(CALL_CLIENT_INTAKE, "keypoints")
CL_KP_3 = _p(CALL_OFFER_NEGOTIATION, "keypoints")

CL_IM_1 = _p(CALL_ROUGH_SCRIBBLE, "improve")
CL_IM_2 = _p(CALL_CLIENT_SHORTHAND, "improve")
CL_IM_3 = _p(CALL_NO_SHOW_FOLLOWUP, "improve")

CL_MAN_1 = {
    "note": CALL_INTERVIEW_SCHEDULE_HTML,
    "key": "manual_prompt",
    "prompt": (
        "Turn this into a CRM call log suitable for the candidate record: Date/time, Participants, "
        "Agreements, and Follow-ups — use short bullets only."
    ),
}
CL_MAN_2 = {
    "note": CALL_CLIENT_INTAKE,
    "key": "manual_prompt",
    "prompt": (
        "Extract a client-ready summary: role count, timeline, interview stages, and budget — "
        "one paragraph each, professional tone."
    ),
}
CL_MAN_3 = {
    "note": CALL_TECH_SCREEN_LONG,
    "key": "manual_prompt",
    "prompt": (
        "Give a hiring recommendation (Proceed / Hold / Reject) with 3 bullet reasons and "
        "the top one risk to validate in the next round."
    ),
}
CL_MAN_4 = {
    "note": CALL_OFFER_NEGOTIATION,
    "key": "manual_prompt",
    "prompt": (
        "Produce an internal legal/ops handoff: compensation summary, competing offer context, "
        "and open items needing written confirmation — numbered list."
    ),
}

CALL_EXTRA_VENDOR = """Vendor call — background check provider — 12 min
Pricing: 3 packages discussed; enterprise bundle ~18k per candidate annual minimum 200 checks.
SLA: 5 business days standard; 48h rush available at 1.5x.
Action: send MSA draft to procurement; loop legal on DPA by Apr 7."""

CALL_EXTRA_INTERNAL = """<p><strong>Calibration call — EM + Recruiter</strong><br>
Bar for L5 raised: need system design + 2+ years people management.<br>
Paused one req until JD updated.<br>
Next calibration: same time next week.</p>"""

CL_MAN_5 = {
    "note": CALL_EXTRA_VENDOR,
    "key": "manual_prompt",
    "prompt": "Summarize for finance: total cost drivers, SLA, and the two follow-up owners.",
}
CL_MAN_6 = {
    "note": CALL_EXTRA_INTERNAL,
    "key": "manual_prompt",
    "prompt": "Extract decisions and action items as a numbered list for the hiring committee notes.",
}

ALL_SUMMARISE_CASES = [
    ("CL-S1 Interview scheduling (HTML)", CL_SUM_1),
    ("CL-S2 Client intake call",          CL_SUM_2),
    ("CL-S3 Offer / negotiation",         CL_SUM_3),
    ("CL-S4 Reference-style notes",       CL_SUM_4),
]

ALL_KEYPOINTS_CASES = [
    ("CL-K1 Tech screen debrief", CL_KP_1),
    ("CL-K2 Client intake",       CL_KP_2),
    ("CL-K3 Offer call",          CL_KP_3),
]

ALL_IMPROVE_CASES = [
    ("CL-I1 Rough scribble",           CL_IM_1),
    ("CL-I2 HM shorthand",             CL_IM_2),
    ("CL-I3 No-show follow-up (HTML)", CL_IM_3),
]

ALL_STANDALONE_MANUAL_CASES = [
    ("CL-M1 Manual — CRM bullets",          CL_MAN_1),
    ("CL-M2 Manual — client summary",       CL_MAN_2),
    ("CL-M3 Manual — hiring decision",      CL_MAN_3),
    ("CL-M4 Manual — legal handoff",        CL_MAN_4),
    ("CL-M5 Manual — vendor / finance",     CL_MAN_5),
    ("CL-M6 Manual — calibration decisions", CL_MAN_6),
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

    for name, payload in ALL_SUMMARISE_CASES:
        entry = run_call_log_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    for name, payload in ALL_KEYPOINTS_CASES:
        entry = run_call_log_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    for name, payload in ALL_IMPROVE_CASES:
        entry = run_call_log_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    entry_ch1 = run_call_log_chained_manual_test(
        "CL-C1 Chain summarise → manual (CRM format)",
        CL_SUM_1,
        "Rewrite as a single-paragraph executive summary under 100 words; mention date and next owner.",
    )
    results.append(entry_ch1)
    _count(entry_ch1, c)

    entry_ch2 = run_call_log_chained_manual_test(
        "CL-C2 Chain keypoints → manual (email to HM)",
        CL_KP_1,
        "Format as a 5-sentence email I can send to the hiring manager — no markdown, plain sentences.",
    )
    results.append(entry_ch2)
    _count(entry_ch2, c)

    for name, payload in ALL_STANDALONE_MANUAL_CASES:
        entry = run_call_log_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    print(f"\n{'='*60}")
    print("  CALL LOG SUMMARY — TEST RUN COMPLETE")
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

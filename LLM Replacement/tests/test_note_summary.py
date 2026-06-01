"""
tests/test_note_summary.py
--------------------------
Endpoint : POST /v1/generate-note-summary

Keys covered:
  - ``summarise`` — condense recruiter note
  - ``keypoints`` — extract bullets / takeaways
  - ``improve`` — rewrite for clarity & tone
  - ``manual_prompt`` — with optional ``chat_id`` (chained from prior call) or cold (no history); 6 standalone + 2 chained

Streaming + env A/B compare + Groq judge.  Plain-text outputs use
``validate_streamed_text_output`` (not HTML).

Conversational multi-turn is **out of scope** here (per QA plan).
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

PATH = "/v1/generate-note-summary"

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


def _note_judge_task_input(payload: dict) -> dict:
    raw_note = payload.get("note") or ""
    ti: dict = {
        "task": "note_summary",
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


def run_note_summary_test(
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
        task_input=_note_judge_task_input(payload_a),
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
            "suite":          "note_summary",
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


def run_note_chained_manual_test(
    test_name: str,
    first_payload: dict,
    user_prompt: str,
) -> dict:
    """
    First call (e.g. ``summarise`` or ``keypoints``) → capture ``chat_id`` per env;
    second call ``manual_prompt`` with same ``note``, ``prompt``, and that env's ``chat_id``.
    No ``last_response`` in body (matches product curl).
    """
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

    return run_note_summary_test(test_name, payload_a, payload_b)


NOTE_PHONE_SCREEN_HTML = """<p><strong>Phone screen — Priya Nair (Sr. Backend)</strong><br>
Source: LinkedIn inbound · Referred by Amit K.<br>
Available to join in 30 days (NP negotiable if buyout discussed).<br>
Good communication, confident on system design basics; slight hesitation on AWS cost optimization (reserved instances, Savings Plans).<br>
Salary ask: 38L fixed + standard benefits. Current: 32L at Razorpay.<br>
Open to hybrid 3d Bangalore; visa not required.<br>
Next: send take-home (API design + SQL) — deadline 5 business days.</p>"""

NOTE_CLIENT_REJECTION = """Client update — Acme FinTech (req #4412)
Submitted Marcus Chen on Tue; hiring manager (Lisa) replied Thu AM.
Reason: they want someone who has led a team of 5+ in a regulated environment. Marcus was IC-only.
They liked his Kotlin + event-driven story but need prior SOC2 / audit exposure.
Lisa asked to keep him warm for a Staff role opening in Q3.
Action: send polite decline template + add Marcus to nurture sequence "enterprise-lead-2026".
Ping BD: check if we can get clarity on team-size requirement before next sub."""

NOTE_INTERVIEW_DEBRIEF = """Panel debrief — Data Engineer (contract-to-hire) — 18 Mar 2026
Attendees: me, Ravi (EM), Sneha (DS lead)
Candidate: Isha Gupta — 4.5 yrs, last 2 at Meesho on ingestion pipelines.

Strengths: solid Airflow + Spark; explained data quality checks clearly; good attitude.
Gaps: never owned on-call for a production lakehouse; weaker on dbt testing patterns we use.
Ravi: hire at L4 if we pair with senior for 90 days. Sneha: concerned about stakeholder pushback experience.
Decision: **conditional offer** pending reference on reliability (ask former manager).
Comp band discussed: 22–26L contractor budget approved by finance."""

NOTE_OFFER_STAGE = """Offer call notes — Vikram Joshi (PM)
He has competing offer from Swiggy (similar level, slightly higher equity).
Our offer: 45L + 12% variable + ESOP band 3.
He wants until Monday EOD; spouse prefers less travel — reassured max 1 trip/month.
Send revised FAQ on ESOP vesting and clawback.
If he pushes on base, cap at +3L with VP approval only.
Recruiter: update Greenhouse stage to "Offer — competing" and flag #exec-review."""

NOTE_ROUGH_FOR_IMPROVE = """met candidate at namma yatri conf - name is probably karthik?? 
works on maps infra 6yr
wants remote mostly 
good vibe technical deep on routing algos 
not sure about salary said something 45+ 
follow up next week maybe thursday"""

NOTE_TECH_KEYPOINTS = """Technical round — ML Platform (45 min) — candidate: Elena Petrova
Stack discussed: PyTorch training loops, distributed data loading, experiment tracking (MLflow vs W&B).
She described migrating a training job from 1 GPU to 4-node cluster; mentioned NCCL timeouts and how she debugged.
Coding: implemented a simple batching iterator with padding; edge cases on variable-length sequences handled.
Concerns: limited production serving experience (only batch inference); no direct K8s GPU operator exposure.
Hiring bar: we need someone who can pair with infra on Triton deployment within 60 days.
Rating from panel: 4/5 technical, 3.5/5 production readiness."""

NOTE_SOURCING_BLURB = """Sourcing note — LinkedIn campaign "India-SRE-March"
Messaged 42 profiles; 11 replies; 4 interested; 2 scheduled screens.
Top funnel: ex-Flipkart SRE with K8s + Istio cert (screen Thu 2pm).
Blockers: two candidates dropped after learning on-call rotation (1-in-6 weeks).
Suggest JD tweak: spell out rotation explicitly to reduce drop-off.
Budget reminder: req capped at 40L; escalate if exceptional + director sign-off."""


def _p(note: str, key: str) -> dict:
    return {"note": note, "key": key}


NS_SUM_1 = _p(NOTE_PHONE_SCREEN_HTML, "summarise")
NS_SUM_2 = _p(NOTE_CLIENT_REJECTION, "summarise")
NS_SUM_3 = _p(NOTE_INTERVIEW_DEBRIEF, "summarise")
NS_SUM_4 = _p(NOTE_OFFER_STAGE, "summarise")

NS_KP_1 = _p(NOTE_TECH_KEYPOINTS, "keypoints")
NS_KP_2 = _p(NOTE_INTERVIEW_DEBRIEF, "keypoints")
NS_KP_3 = _p(NOTE_SOURCING_BLURB, "keypoints")

NS_IM_1 = _p(NOTE_ROUGH_FOR_IMPROVE, "improve")
NS_IM_2 = _p(
    "called client. they want faster subs. also mentioned budget frozen til april?? "
    "send 2 more profiles by fri anyway",
    "improve",
)
NS_IM_3 = _p(
    "<p>interview no-show — rescheduled once already. candidate cited medical emergency. "
    "verify on whatsapp before blocking slot again.</p>",
    "improve",
)

NS_MAN_STANDALONE_1 = {
    "note": NOTE_PHONE_SCREEN_HTML,
    "key": "manual_prompt",
    "prompt": (
        "Rewrite as a crisp client-facing summary: candidate name, notice period, salary expectation, "
        "and one-line risk (AWS cost depth). Use bullet points only."
    ),
}
NS_MAN_STANDALONE_2 = {
    "note": NOTE_CLIENT_REJECTION,
    "key": "manual_prompt",
    "prompt": (
        "Turn this into a structured internal handoff: Client decision, Reason, Candidate disposition, "
        "and Next actions — each as a short paragraph."
    ),
}
NS_MAN_STANDALONE_3 = {
    "note": NOTE_TECH_KEYPOINTS,
    "key": "manual_prompt",
    "prompt": (
        "Extract a hiring recommendation: Hire / No-hire / Conditional, with 3 bullet rationale points "
        "and the single biggest risk to mitigate in onboarding."
    ),
}
NS_MAN_STANDALONE_4 = {
    "note": NOTE_SOURCING_BLURB,
    "key": "manual_prompt",
    "prompt": (
        "Produce an exec one-liner for the weekly sourcing standup plus 4 numbered metrics "
        "(e.g. outreach, replies, screens scheduled, blockers)."
    ),
}

NOTE_STANDALONE_EXTRA_BRIEF = """<p><strong>Screening note — Contract FE</strong><br>
15 min call. Candidate asked about WFH policy and laptop shipment timeline.
Rate: 2200/day discussed; client cap 2000 — flagged for approval.</p>"""

NOTE_STANDALONE_EXTRA_RECRUITER = """Internal sync — recruiter pod — week of 24 Mar
Backlog: 14 reqs, 6 critical. Two reqs paused (legal review).
Agreed: prioritize fintech reqs first; pause non-billable internal roles until Q2."""

NS_MAN_STANDALONE_5 = {
    "note": NOTE_STANDALONE_EXTRA_BRIEF,
    "key": "manual_prompt",
    "prompt": "One short paragraph for the deal desk: rate gap, decision needed, owner.",
}
NS_MAN_STANDALONE_6 = {
    "note": NOTE_STANDALONE_EXTRA_RECRUITER,
    "key": "manual_prompt",
    "prompt": "Bullet-only ops update: backlog, pauses, and the one priority change for this week.",
}

ALL_SUMMARISE_CASES = [
    ("NS-S1 Phone screen (HTML)", NS_SUM_1),
    ("NS-S2 Client rejection",      NS_SUM_2),
    ("NS-S3 Interview debrief",     NS_SUM_3),
    ("NS-S4 Offer / competing",    NS_SUM_4),
]

ALL_KEYPOINTS_CASES = [
    ("NS-K1 Technical panel",   NS_KP_1),
    ("NS-K2 Debrief multi",     NS_KP_2),
    ("NS-K3 Sourcing campaign", NS_KP_3),
]

ALL_IMPROVE_CASES = [
    ("NS-I1 Rough phone notes",     NS_IM_1),
    ("NS-I2 Client call shorthand", NS_IM_2),
    ("NS-I3 No-show note (HTML)",   NS_IM_3),
]

ALL_STANDALONE_MANUAL_CASES = [
    ("NS-M1 Manual — client-facing bullets", NS_MAN_STANDALONE_1),
    ("NS-M2 Manual — internal handoff",      NS_MAN_STANDALONE_2),
    ("NS-M3 Manual — hire recommendation",   NS_MAN_STANDALONE_3),
    ("NS-M4 Manual — standup metrics",       NS_MAN_STANDALONE_4),
    ("NS-M5 Manual — rate / deal desk",      NS_MAN_STANDALONE_5),
    ("NS-M6 Manual — recruiter ops sync",   NS_MAN_STANDALONE_6),
]


def _count(entry, counters: dict) -> None:
    s = entry["final_status"]
    if s == "PASS":
        counters["passed"] += 1
    elif s == "FAIL":
        counters["failed"] += 1
    elif s == "REVIEW":
        counters["review"] += 1
    else:
        counters["errored"] += 1


def run_all():
    results: list = []
    c = {"passed": 0, "failed": 0, "review": 0, "errored": 0}

    for name, payload in ALL_SUMMARISE_CASES:
        entry = run_note_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    for name, payload in ALL_KEYPOINTS_CASES:
        entry = run_note_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    for name, payload in ALL_IMPROVE_CASES:
        entry = run_note_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    entry_ch1 = run_note_chained_manual_test(
        "NS-C1 Chain summarise → manual (tighten + next steps)",
        NS_SUM_1,
        "Tighten the summary to under 120 words and add a 'Next steps for recruiter' line.",
    )
    results.append(entry_ch1)
    _count(entry_ch1, c)

    entry_ch2 = run_note_chained_manual_test(
        "NS-C2 Chain keypoints → manual (exec email)",
        NS_KP_1,
        "Reformat as a 4-sentence exec email I can paste to the VP Eng — no bullets.",
    )
    results.append(entry_ch2)
    _count(entry_ch2, c)

    for name, payload in ALL_STANDALONE_MANUAL_CASES:
        entry = run_note_summary_test(name, payload)
        results.append(entry)
        _count(entry, c)

    print(f"\n{'='*60}")
    print("  NOTE SUMMARY — TEST RUN COMPLETE")
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

"""
tests/test_job_description.py
-----------------------------
Endpoint : POST /v1/jobs/generate-job-description
Keys     : ``generate`` (4) + chained ``manual_prompt`` (JD5) + standalone ``manual_prompt`` (4, no history)

Same streaming + judge flow as ``test_candidate_summary``; payloads use
``job`` + ``key``. ``company_id`` is omitted from all test bodies per QA spec.
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
    validate_streamed_markup,
)

PATH = "/v1/jobs/generate-job-description"
KEY_GENERATE = "generate"


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


def _job_judge_task_input(payload: dict) -> dict:
    """Job fields + action key + optional manual refinement (excerpt for prior HTML)."""
    ti: dict = {
        "task": "job_description",
        "key":  payload.get("key"),
        "job":  payload.get("job"),
    }
    if payload.get("prompt"):
        ti["user_refinement_instruction"] = payload["prompt"]
    lr = payload.get("last_response")
    if isinstance(lr, str) and lr.strip():
        cap = 1500
        ti["prior_description_total_chars"] = len(lr)
        ti["prior_description_excerpt"] = lr[:cap] + ("…" if len(lr) > cap else "")
    return ti


def run_job_description_test(
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
    result_b = call_new_env(PATH, payload_b, stream=True,     base_url=NEPTUNE_URL_B)

    text_a = extract_generated_text(result_a["response_text"])
    text_b = extract_generated_text(result_b["response_text"])

    schema_a = validate_streamed_markup(text_a)
    schema_b = validate_streamed_markup(text_b)

    if result_a["error"]:
        print(f"  [ENV A ERROR] {result_a['error']}")
    if result_b["error"]:
        print(f"  [ENV B ERROR] {result_b['error']}")

    judge = evaluate_response(
        response_existing=text_a or "[empty]",
        response_new=text_b or "[empty]",
        task_input=_job_judge_task_input(payload_a),
        extra_context=f"endpoint={PATH}; env_A=baseline existing; env_B=new candidate",
    )
    status = _decide(schema_b["schema_valid"], judge)

    entry = log_result(
        endpoint=PATH,
        action=payload_a.get("key", KEY_GENERATE),
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
            "suite":          "job_description",
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


def run_job_manual_chained_test(
    test_name: str,
    generate_payload: dict,
    user_prompt: str,
) -> dict:
    """``generate`` on A/B, then ``manual_prompt`` with per-env HTML + chat_id."""
    print(f"\n{'='*60}")
    print(f"  CHAIN — generate → manual_prompt: {test_name}")
    print(f"{'='*60}")

    gen_a = call_baseline_env(PATH, generate_payload, stream=True, base_url=NEPTUNE_URL_A)
    gen_b = call_new_env(PATH, generate_payload, stream=True,     base_url=NEPTUNE_URL_B)

    html_a = extract_generated_text(gen_a["response_text"])
    html_b = extract_generated_text(gen_b["response_text"])
    cid_a  = extract_chat_id(gen_a["response_text"])
    cid_b  = extract_chat_id(gen_b["response_text"])

    if gen_a["error"]:
        print(f"  [GENERATE A ERROR] {gen_a['error']}")
    if gen_b["error"]:
        print(f"  [GENERATE B ERROR] {gen_b['error']}")

    job = generate_payload["job"]
    base = {"job": job, "key": "manual_prompt", "prompt": user_prompt}

    payload_a = {**base, "last_response": html_a}
    if cid_a:
        payload_a["chat_id"] = cid_a

    payload_b = {**base, "last_response": html_b}
    if cid_b:
        payload_b["chat_id"] = cid_b

    print(
        f"  [CHAIN] generate HTML len  A={len(html_a)}  B={len(html_b)}  |  "
        f"chat_id A={cid_a!r}  B={cid_b!r}"
    )

    return run_job_description_test(test_name, payload_a, payload_b)


def _job(**fields) -> dict:
    base = {
        "name":                    "Software Engineer",
        "number_of_openings":       1,
        "job_type":                None,
        "job_category":            None,
        "minimum_experience":      None,
        "maximum_experience":      None,
        "salary_type":             "annual",
        "currency":                "Rupees (₹ - India)",
        "minimum_salary":          None,
        "maximum_salary":          None,
        "educational_qualification": "Not Available",
        "educational_specialization": None,
        "city":                    None,
        "locality":                None,
        "state":                   None,
        "country":                 None,
        "full_address":            None,
        "skills":                  None,
    }
    base.update(fields)
    return base


JD_SOFTWARE_CHENNAI = {
    "job": _job(
        name="Software Engineer",
        number_of_openings=1,
        city="Chennai",
        full_address="705 Parnassus Ave, San Francisco",
        skills="Python, REST APIs, PostgreSQL, Docker",
    ),
    "key": KEY_GENERATE,
}

JD_DATA_ANALYST_BENGALURU = {
    "job": _job(
        name="Senior Data Analyst",
        number_of_openings=2,
        city="Bengaluru",
        state="Karnataka",
        full_address="Indiranagar, Bengaluru",
        minimum_experience=3,
        maximum_experience=7,
        skills="SQL, dbt, Looker, Airflow, stakeholder reporting",
        educational_qualification="Bachelor's in Mathematics, Statistics, or related",
    ),
    "key": KEY_GENERATE,
}

JD_NURSE_PRACTITIONER_DELHI = {
    "job": _job(
        name="Nurse Practitioner",
        number_of_openings=1,
        city="New Delhi",
        country="India",
        full_address="Connaught Place, New Delhi",
        minimum_experience=2,
        maximum_experience=5,
        salary_type="annual",
        minimum_salary=1200000,
        maximum_salary=1800000,
        educational_specialization="Family Practice / Primary Care",
        skills="Patient triage, EMR documentation, chronic care management",
    ),
    "key": KEY_GENERATE,
}

JD_PRODUCT_DESIGNER_REMOTE = {
    "job": _job(
        name="Lead Product Designer",
        number_of_openings=1,
        city="Remote",
        locality="India",
        full_address="Remote — IST overlap 10:00–19:00",
        job_type="full_time",
        job_category="Design",
        skills="Figma, design systems, UX research, prototyping, accessibility (WCAG)",
        minimum_salary=2500000,
        maximum_salary=4000000,
    ),
    "key": KEY_GENERATE,
}

ALL_GENERATE_CASES = [
    ("JD1 - Software Engineer (Chennai)", JD_SOFTWARE_CHENNAI),
    ("JD2 - Senior Data Analyst (Bengaluru)", JD_DATA_ANALYST_BENGALURU),
    ("JD3 - Nurse Practitioner (Delhi)", JD_NURSE_PRACTITIONER_DELHI),
    ("JD4 - Lead Product Designer (Remote)", JD_PRODUCT_DESIGNER_REMOTE),
]

MANUAL_PROMPT_TEXT = "make it precise"


JD_MANUAL_STANDALONE_CSM = {
    "job": _job(
        name="Customer Success Manager",
        number_of_openings=2,
        city="Mumbai",
        state="Maharashtra",
        full_address="BKC, Mumbai",
        minimum_experience=3,
        maximum_experience=8,
        skills="SaaS onboarding, QBRs, churn analysis, Zendesk, Gainsight basics",
        educational_qualification="Bachelor's degree or equivalent experience",
    ),
    "key": "manual_prompt",
    "prompt": (
        "Draft a full job description for a SaaS CSM: include Role overview, "
        "Key responsibilities (bullets), Must-have skills, and a **How to apply** section with "
        "careers@example.com as placeholder only."
    ),
}

JD_MANUAL_STANDALONE_BACKEND = {
    "job": _job(
        name="Backend Engineer — Go",
        number_of_openings=1,
        city="Hyderabad",
        state="Telangana",
        full_address="HITEC City, Hyderabad",
        minimum_experience=2,
        maximum_experience=6,
        job_type="full_time",
        job_category="Engineering",
        skills="Go, gRPC, PostgreSQL, Redis, Kubernetes, observability (Prometheus)",
        minimum_salary=1800000,
        maximum_salary=3200000,
    ),
    "key": "manual_prompt",
    "prompt": (
        "Write a concise job posting: lead with tech stack (Go, PostgreSQL, microservices). "
        "Use bullet lists only for Requirements and Nice-to-have. "
        "Add one line: Applications: talent@example.com (placeholder)."
    ),
}

JD_MANUAL_STANDALONE_SRE = {
    "job": _job(
        name="Site Reliability Engineer",
        number_of_openings=1,
        city="Pune",
        state="Maharashtra",
        full_address="Kharadi, Pune",
        minimum_experience=4,
        maximum_experience=9,
        job_type="full_time",
        job_category="Engineering",
        skills="Kubernetes, Terraform, Prometheus, on-call, incident response, SLOs",
        minimum_salary=2200000,
        maximum_salary=3800000,
    ),
    "key": "manual_prompt",
    "prompt": (
        "Emphasize on-call rotation (1-in-5) and incident command experience; add a short "
        "**What you’ll work on** section with 4 bullets."
    ),
}

JD_MANUAL_STANDALONE_RECRUITER = {
    "job": _job(
        name="Technical Recruiter",
        number_of_openings=1,
        city="Bengaluru",
        state="Karnataka",
        full_address="HSR Layout, Bengaluru",
        minimum_experience=2,
        maximum_experience=6,
        skills="Sourcing, Greenhouse, LinkedIn Recruiter, stakeholder management, offer negotiation",
        educational_qualification="Bachelor's in any field; HR preferred",
    ),
    "key": "manual_prompt",
    "prompt": (
        "Write an inclusive JD: avoid gendered language; include **Equal opportunity** paragraph "
        "and one line on hybrid work (3 days office)."
    ),
}

ALL_STANDALONE_MANUAL_CASES = [
    ("JD6 - Manual only (Customer Success Manager)", JD_MANUAL_STANDALONE_CSM),
    ("JD7 - Manual only (Backend Engineer — Go)",   JD_MANUAL_STANDALONE_BACKEND),
    ("JD8 - Manual only (Site Reliability Engineer)", JD_MANUAL_STANDALONE_SRE),
    ("JD9 - Manual only (Technical Recruiter)",       JD_MANUAL_STANDALONE_RECRUITER),
]


def run_all():
    results = []
    passed = failed = review = errored = 0

    for name, payload in ALL_GENERATE_CASES:
        entry = run_job_description_test(name, payload)
        results.append(entry)
        s = entry["final_status"]
        if s == "PASS":
            passed += 1
        elif s == "FAIL":
            failed += 1
        elif s == "REVIEW":
            review += 1
        else:
            errored += 1

    entry5 = run_job_manual_chained_test(
        "JD5 - Manual prompt (chained from JD1)",
        JD_SOFTWARE_CHENNAI,
        MANUAL_PROMPT_TEXT,
    )
    results.append(entry5)
    s5 = entry5["final_status"]
    if s5 == "PASS":
        passed += 1
    elif s5 == "FAIL":
        failed += 1
    elif s5 == "REVIEW":
        review += 1
    else:
        errored += 1

    def _count(entry):
        nonlocal passed, failed, review, errored
        s = entry["final_status"]
        if s == "PASS":
            passed += 1
        elif s == "FAIL":
            failed += 1
        elif s == "REVIEW":
            review += 1
        else:
            errored += 1

    for name, payload in ALL_STANDALONE_MANUAL_CASES:
        entry = run_job_description_test(name, payload)
        results.append(entry)
        _count(entry)

    print(f"\n{'='*60}")
    print("  JOB DESCRIPTION — TEST RUN COMPLETE")
    print(
        f"  Total: {len(results)}  |  PASS: {passed}  |  REVIEW: {review}  |  "
        f"FAIL: {failed}  |  ERROR: {errored}"
    )
    scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    if scores:
        print(f"  Avg Judge Score: {sum(scores) / len(scores):.2f}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    run_all()

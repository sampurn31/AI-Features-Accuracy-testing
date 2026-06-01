"""
tests/test_candidate_summary.py
--------------------------------
Endpoint : POST /v1/candidates/generate-summary
Keys     : "summarise" (4) + chained "manual_prompt" (1) + standalone "manual_prompt" (4, no history)
Stream   : Yes — response is a sequence of concatenated JSON objects.
           We reconstruct the full HTML by joining every data.generated_text chunk.

================================================================================
FLOW (end-to-end)
================================================================================

1. HTTP (per environment)
   - Baseline (A) and New (B) each use the same *logical* test input, except for
     manual_prompt (see below) where `last_response` / `chat_id` come from that
     env's own prior summarise stream.

2. Streaming body
   - Neptune returns many back-to-back JSON objects.  We parse them with
     `json.JSONDecoder.raw_decode`, walking the buffer until it is consumed.

3. Reconstruction
   - `extract_generated_text`  → concatenates every `data.generated_text` slice
     into one string (the final HTML).
   - `extract_chat_id`         → last thread id from `data.id` (`conv_...`) or legacy
     `data.chat_id` / `meta`; next request sends it as JSON field `chat_id`.

4. Schema check (local)
   - `validate_html_response` ensures the reconstructed string is non-empty HTML.

5. Judge (`judge.evaluate_response`)
   - Sends **INPUT** (candidate JSON + key + optional manual fields) and both
     outputs: **existing** (baseline) vs **new** (candidate env).
   - One comparative **overall score** 1–5 (5 = new better, 4 ≈ as good as existing)
     plus a short `reason`.

6. Decision (`_decide`)
   - If judge errors → ERROR.
   - If B fails schema → FAIL.
   - Else if mean score ≥ PASS_THRESHOLD → PASS; ≥ REVIEW_THRESHOLD → REVIEW; else FAIL.

7. Logging (`log_result`)
   - Appends one JSON line per case (latency, schema flags, judge output, status).

================================================================================
manual_prompt chain (TC5)
================================================================================

No hardcoded `last_response`.

  Step A1 / B1 : POST same candidate + key=summarise on env A and B (streaming).
  Step A2 / B2 : Build manual body with:
                   last_response = HTML from *that* env's step 1 stream
                   chat_id       = extracted from *that* env's stream (if present)
                   prompt        = user refinement instruction (same for both)
  Step 3       : POST manual_prompt on A and B; compare + judge as usual.

This matches the product flow (summarise first, then refine) and avoids shipping
stale HTML.  If the API omits `chat_id`, we only send `last_response` + `prompt`.
"""

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

from stream_helpers import extract_generated_text, extract_chat_id


PATH   = "/v1/candidates/generate-summary"
ACTION = "summarise"


def validate_html_response(text: str) -> dict:
    """
    Validates that the reconstructed text is non-empty HTML.
    """
    result = {"schema_valid": True, "error": None}

    if not text or not text.strip():
        result["schema_valid"] = False
        result["error"] = "empty_response"
        return result

    lower = text.lower()
    if "<html" not in lower and "<body" not in lower and "<p" not in lower:
        result["schema_valid"] = False
        result["error"] = "no_html_tags_detected"

    return result


def _candidate_judge_task_input(payload: dict) -> dict:
    """Candidate profile + action key + optional manual-prompt fields (excerpt only for huge HTML)."""
    ti: dict = {
        "task":      "candidate_summary",
        "key":       payload.get("key"),
        "candidate": payload.get("candidate"),
    }
    if payload.get("prompt"):
        ti["user_refinement_instruction"] = payload["prompt"]
    lr = payload.get("last_response")
    if isinstance(lr, str) and lr.strip():
        cap = 1500
        ti["prior_summary_total_chars"] = len(lr)
        ti["prior_summary_excerpt"] = lr[:cap] + ("…" if len(lr) > cap else "")
    return ti


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


def run_candidate_summary_test(
    test_name: str,
    payload_a: dict,
    payload_b: dict | None = None,
) -> dict:
    """
    1. Calls env A with payload_a and env B with payload_b (default: same as A).
    2. Extracts generated_text from each streamed response.
    3. Validates HTML schema.
    4. Sends to judge (B scored against A).
    5. Logs and returns result entry.
    """
    payload_b = payload_b if payload_b is not None else payload_a

    print(f"\n{'='*60}")
    print(f"  TEST: {test_name}")
    print(f"  KEY : {payload_a.get('key')}")
    print(f"{'='*60}")

    result_a = call_baseline_env(PATH, payload_a, stream=True, base_url=NEPTUNE_URL_A)
    result_b = call_new_env(PATH, payload_b, stream=True,     base_url=NEPTUNE_URL_B)

    text_a = extract_generated_text(result_a["response_text"])
    text_b = extract_generated_text(result_b["response_text"])

    schema_a = validate_html_response(text_a)
    schema_b = validate_html_response(text_b)

    if result_a["error"]:
        print(f"  [ENV A ERROR] {result_a['error']}")
    if result_b["error"]:
        print(f"  [ENV B ERROR] {result_b['error']}")

    judge = evaluate_response(
        response_existing=text_a or "[empty]",
        response_new=text_b or "[empty]",
        task_input=_candidate_judge_task_input(payload_a),
        extra_context=f"endpoint={PATH}; env_A=baseline existing; env_B=new candidate",
    )

    status = _decide(schema_b["schema_valid"], judge)

    entry = log_result(
        endpoint=PATH,
        action=payload_a.get("key", ACTION),
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


def run_manual_prompt_chained_test(
    test_name: str,
    summarize_payload: dict,
    user_prompt: str,
) -> dict:
    """
    Runs summarise on A and B, then manual_prompt on each using that env's own
    HTML + chat_id from the first stream (no hardcoded last_response).
    """
    print(f"\n{'='*60}")
    print(f"  CHAIN — summarise → manual_prompt for: {test_name}")
    print(f"{'='*60}")

    sum_a = call_baseline_env(PATH, summarize_payload, stream=True, base_url=NEPTUNE_URL_A)
    sum_b = call_new_env(PATH, summarize_payload, stream=True,     base_url=NEPTUNE_URL_B)

    html_a = extract_generated_text(sum_a["response_text"])
    html_b = extract_generated_text(sum_b["response_text"])
    cid_a  = extract_chat_id(sum_a["response_text"])
    cid_b  = extract_chat_id(sum_b["response_text"])

    if sum_a["error"]:
        print(f"  [SUMMARISE A ERROR] {sum_a['error']}")
    if sum_b["error"]:
        print(f"  [SUMMARISE B ERROR] {sum_b['error']}")

    cand = summarize_payload["candidate"]
    base = {"candidate": cand, "key": "manual_prompt", "prompt": user_prompt}

    payload_a = {**base, "last_response": html_a}
    if cid_a:
        payload_a["chat_id"] = cid_a

    payload_b = {**base, "last_response": html_b}
    if cid_b:
        payload_b["chat_id"] = cid_b

    print(f"  [CHAIN] summarise HTML len  A={len(html_a)}  B={len(html_b)}  |  chat_id A={cid_a!r}  B={cid_b!r}")

    return run_candidate_summary_test(test_name, payload_a, payload_b)


TC_DEVOPS = {
    "candidate": {
        "name": "Kowshik Reddy",
        "skills": "Linux, ArgoCD, Kubernetes, Terraform, Jenkins, AWS SDK, Ansible, Docker, ELK Stack, Python, Bash, YAML",
        "title": "Associate Software Engineer - DevOps",
        "language_skills": "English",
        "current_organization": "RecruitCRM",
        "total_experience": 3,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": None,
        "salary_expectation": None,
        "employment_status": None,
        "work_history": [
            {
                "title": "Associate Software Engineer - DevOps",
                "work_company_name": "RecruitCRM",
                "work_location": "Pune, Maharashtra",
                "is_currently_working": 1,
                "work_start_date": "01-10-2023",
                "work_end_date": "01-01-1970",
                "work_description": "Engineered a fully automated environment provisioning system with 20+ services, enabling spin-up in under 10 minutes.",
                "accountid": 1510637,
            },
            {
                "title": "Python, AWS and DevOps Intern",
                "work_company_name": "Technical Hub",
                "work_location": "Surampalem, Andhra Pradesh",
                "is_currently_working": 0,
                "work_start_date": "01-08-2022",
                "work_end_date": "01-08-2023",
                "work_description": "Contributed to AWS infrastructure setup and implemented Python-based automation.",
                "accountid": 1510637,
            },
        ],
        "education_history": [
            {
                "institute_name": "Aditya College of Engineering",
                "educational_qualification": "B.Tech in Electronics and Communication Engineering",
                "educational_specialization": "Electronics, Communication Systems, DSA",
                "education_location": "Surampalem, Andhra Pradesh",
                "education_start_date": "01-08-2019",
                "education_end_date": "01-04-2023",
                "accountid": 1510637,
            }
        ],
    },
    "key": "summarise",
}

TC_FRONTEND = {
    "candidate": {
        "name": "Priya Sharma",
        "skills": "React, TypeScript, JavaScript, HTML, CSS, Next.js, Tailwind CSS, GraphQL, Redux, Jest, Webpack",
        "title": "Senior Frontend Developer",
        "language_skills": "English, Hindi",
        "current_organization": "Flipkart",
        "total_experience": 5,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": 2200000,
        "salary_expectation": 2800000,
        "employment_status": "Employed",
        "work_history": [
            {
                "title": "Senior Frontend Developer",
                "work_company_name": "Flipkart",
                "work_location": "Bengaluru, Karnataka",
                "is_currently_working": 1,
                "work_start_date": "01-03-2022",
                "work_end_date": "01-01-1970",
                "work_description": "Led the redesign of Flipkart's checkout flow using React and TypeScript, reducing cart abandonment by 18%.",
                "accountid": 1510638,
            },
            {
                "title": "Frontend Developer",
                "work_company_name": "Zomato",
                "work_location": "Bengaluru, Karnataka",
                "is_currently_working": 0,
                "work_start_date": "01-06-2020",
                "work_end_date": "01-03-2022",
                "work_description": "Built responsive web components for the Zomato restaurant dashboard serving 50k+ restaurant partners.",
                "accountid": 1510638,
            },
        ],
        "education_history": [
            {
                "institute_name": "BITS Pilani",
                "educational_qualification": "B.E. in Computer Science",
                "educational_specialization": "Software Engineering",
                "education_location": "Pilani, Rajasthan",
                "education_start_date": "01-08-2016",
                "education_end_date": "01-05-2020",
                "accountid": 1510638,
            }
        ],
    },
    "key": "summarise",
}

TC_DATA_SCIENTIST = {
    "candidate": {
        "name": "Arjun Mehta",
        "skills": "Python, R, TensorFlow, PyTorch, Scikit-learn, SQL, Spark, Airflow, NLP, Tableau, Pandas, NumPy",
        "title": "Data Scientist",
        "language_skills": "English",
        "current_organization": "Razorpay",
        "total_experience": 4,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": 1800000,
        "salary_expectation": 2400000,
        "employment_status": "Employed",
        "work_history": [
            {
                "title": "Data Scientist",
                "work_company_name": "Razorpay",
                "work_location": "Bengaluru, Karnataka",
                "is_currently_working": 1,
                "work_start_date": "01-07-2022",
                "work_end_date": "01-01-1970",
                "work_description": "Built fraud detection models reducing false positives by 30%. Developed real-time transaction scoring pipeline handling 1M+ daily transactions.",
                "accountid": 1510639,
            },
            {
                "title": "Junior Data Analyst",
                "work_company_name": "Paytm",
                "work_location": "Noida, Uttar Pradesh",
                "is_currently_working": 0,
                "work_start_date": "01-07-2021",
                "work_end_date": "01-07-2022",
                "work_description": "Analyzed payment funnel data and created Tableau dashboards for business stakeholders.",
                "accountid": 1510639,
            },
        ],
        "education_history": [
            {
                "institute_name": "IIT Bombay",
                "educational_qualification": "M.Tech in Data Science",
                "educational_specialization": "Machine Learning, Statistics",
                "education_location": "Mumbai, Maharashtra",
                "education_start_date": "01-07-2019",
                "education_end_date": "01-05-2021",
                "accountid": 1510639,
            }
        ],
    },
    "key": "summarise",
}

TC_PRODUCT_MANAGER = {
    "candidate": {
        "name": "Sunita Iyer",
        "skills": "Product Strategy, Agile, Jira, Roadmapping, A/B Testing, SQL, Figma, User Research, OKRs, Stakeholder Management",
        "title": "Senior Product Manager",
        "language_skills": "English, Tamil",
        "current_organization": "Swiggy",
        "total_experience": 7,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": 3500000,
        "salary_expectation": 4500000,
        "employment_status": "Employed",
        "work_history": [
            {
                "title": "Senior Product Manager",
                "work_company_name": "Swiggy",
                "work_location": "Bengaluru, Karnataka",
                "is_currently_working": 1,
                "work_start_date": "01-01-2021",
                "work_end_date": "01-01-1970",
                "work_description": "Owned the Swiggy Instamart product line, growing GMV from ₹200Cr to ₹900Cr in 18 months by launching hyperlocal features and optimizing delivery SLAs.",
                "accountid": 1510640,
            },
            {
                "title": "Product Manager",
                "work_company_name": "Ola",
                "work_location": "Bengaluru, Karnataka",
                "is_currently_working": 0,
                "work_start_date": "01-06-2018",
                "work_end_date": "01-01-2021",
                "work_description": "Launched Ola Money wallet features. Increased wallet adoption by 40% through contextual nudges and UX improvements.",
                "accountid": 1510640,
            },
        ],
        "education_history": [
            {
                "institute_name": "IIM Ahmedabad",
                "educational_qualification": "MBA",
                "educational_specialization": "Strategy and Marketing",
                "education_location": "Ahmedabad, Gujarat",
                "education_start_date": "01-06-2016",
                "education_end_date": "01-04-2018",
                "accountid": 1510640,
            }
        ],
    },
    "key": "summarise",
}


MANUAL_PROMPT_USER_TEXT = (
    "make it more precise and focus only on technical achievements"
)


CS_MANUAL_STANDALONE_MARKETING = {
    "candidate": {
        "name": "Ananya Krishnan",
        "skills": "Brand strategy, Meta Ads, Google Ads, content calendars, SEO, HubSpot, stakeholder workshops",
        "title": "Senior Marketing Manager",
        "language_skills": "English, Malayalam",
        "current_organization": "Freshworks",
        "total_experience": 6,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": None,
        "salary_expectation": None,
        "employment_status": "Employed",
        "work_history": [
            {
                "title": "Senior Marketing Manager",
                "work_company_name": "Freshworks",
                "work_location": "Chennai, Tamil Nadu",
                "is_currently_working": 1,
                "work_start_date": "01-06-2021",
                "work_end_date": "01-01-1970",
                "work_description": "Led paid-acquisition and lifecycle campaigns; improved demo-to-trial conversion by 22% YoY through tighter audience segmentation and creative testing.",
                "accountid": 1520001,
            },
        ],
        "education_history": [
            {
                "institute_name": "MICA Ahmedabad",
                "educational_qualification": "Post Graduate Diploma in Communications",
                "educational_specialization": "Digital Marketing",
                "education_location": "Ahmedabad, Gujarat",
                "education_start_date": "01-06-2017",
                "education_end_date": "01-04-2018",
                "accountid": 1520001,
            }
        ],
    },
    "key": "manual_prompt",
    "prompt": (
        "Summarize the candidate in bullet form: Overview, strengths, and recent impact. "
        "End with a single line: Contact: candidate.email@example.com (placeholder only)."
    ),
}

CS_MANUAL_STANDALONE_SALES = {
    "candidate": {
        "name": "Rahul Verma",
        "skills": "Enterprise SaaS sales, MEDDPICC, Salesforce, negotiation, POC orchestration",
        "title": "Account Executive — Enterprise",
        "language_skills": "English, Hindi",
        "current_organization": "Zoho",
        "total_experience": 5,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": 2400000,
        "salary_expectation": 3200000,
        "employment_status": "Employed",
        "work_history": [
            {
                "title": "Account Executive — Enterprise",
                "work_company_name": "Zoho",
                "work_location": "Chennai, Tamil Nadu",
                "is_currently_working": 1,
                "work_start_date": "01-03-2022",
                "work_end_date": "01-01-1970",
                "work_description": "Closed 7-figure ARR deals across BFSI; ran executive workshops and RFP responses; partnered with solutions engineers on scoping.",
                "accountid": 1520002,
            },
            {
                "title": "Business Development Representative",
                "work_company_name": "Freshworks",
                "work_location": "Chennai, Tamil Nadu",
                "is_currently_working": 0,
                "work_start_date": "01-07-2020",
                "work_end_date": "01-03-2022",
                "work_description": "Qualified inbound and outbound pipelines; exceeded SQL quota for 6 consecutive quarters.",
                "accountid": 1520002,
            },
        ],
        "education_history": [
            {
                "institute_name": "Delhi University",
                "educational_qualification": "B.Com (Hons)",
                "educational_specialization": "Finance",
                "education_location": "New Delhi",
                "education_start_date": "01-07-2015",
                "education_end_date": "01-05-2018",
                "accountid": 1520002,
            }
        ],
    },
    "key": "manual_prompt",
    "prompt": (
        "Write a tight executive summary: lead with total years in sales, then top 3 wins with metrics. "
        "Use formal tone. Do not invent employer contacts beyond placeholder email line if required."
    ),
}

CS_MANUAL_STANDALONE_QA = {
    "candidate": {
        "name": "Fatima Noor",
        "skills": "Selenium, Cypress, API testing, Postman, Jira, CI/CD test gates, accessibility (a11y)",
        "title": "Senior QA Engineer",
        "language_skills": "English, Urdu",
        "current_organization": "Meesho",
        "total_experience": 7,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": None,
        "salary_expectation": None,
        "employment_status": "Employed",
        "work_history": [
            {
                "title": "Senior QA Engineer",
                "work_company_name": "Meesho",
                "work_location": "Bengaluru, Karnataka",
                "is_currently_working": 1,
                "work_start_date": "01-04-2021",
                "work_end_date": "01-01-1970",
                "work_description": "Owned release QA for checkout; cut P1 escapes by 40% via contract tests and canary checks.",
                "accountid": 1520003,
            },
        ],
        "education_history": [
            {
                "institute_name": "Anna University",
                "educational_qualification": "B.E. Computer Science",
                "educational_specialization": "Software Engineering",
                "education_location": "Chennai, Tamil Nadu",
                "education_start_date": "01-07-2013",
                "education_end_date": "01-05-2017",
                "accountid": 1520003,
            }
        ],
    },
    "key": "manual_prompt",
    "prompt": (
        "Produce a candidate blurb for the client: 4 sentences max, highlight testing automation "
        "and release quality metrics; no salary numbers."
    ),
}

CS_MANUAL_STANDALONE_CONTENT = {
    "candidate": {
        "name": "Siddharth Bose",
        "skills": "Content strategy, SEO writing, LinkedIn, newsletters, Webflow, GA4",
        "title": "Lead Content Strategist",
        "language_skills": "English, Bengali",
        "current_organization": "CRED",
        "total_experience": 5,
        "currency_symbol": "Rupees (₹ - India)",
        "current_salary": 1800000,
        "salary_expectation": None,
        "employment_status": "Employed",
        "work_history": [
            {
                "title": "Lead Content Strategist",
                "work_company_name": "CRED",
                "work_location": "Bengaluru, Karnataka",
                "is_currently_working": 1,
                "work_start_date": "01-08-2022",
                "work_end_date": "01-01-1970",
                "work_description": "Led lifecycle email and in-app copy; improved activation rates on two flagship campaigns.",
                "accountid": 1520004,
            },
        ],
        "education_history": [
            {
                "institute_name": "Jadavpur University",
                "educational_qualification": "B.A. English Literature",
                "educational_specialization": "Creative writing",
                "education_location": "Kolkata, West Bengal",
                "education_start_date": "01-07-2014",
                "education_end_date": "01-05-2017",
                "accountid": 1520004,
            }
        ],
    },
    "key": "manual_prompt",
    "prompt": (
        "Summarize for a marketing hiring manager: tone creative but concise; include 3 headline bullets only."
    ),
}

ALL_SUMMARISE_CASES = [
    ("TC1 - DevOps Engineer",    TC_DEVOPS),
    ("TC2 - Frontend Developer", TC_FRONTEND),
    ("TC3 - Data Scientist",     TC_DATA_SCIENTIST),
    ("TC4 - Product Manager",    TC_PRODUCT_MANAGER),
]

ALL_STANDALONE_MANUAL_CASES = [
    ("TC6 - Manual only (Marketing Manager)", CS_MANUAL_STANDALONE_MARKETING),
    ("TC7 - Manual only (Enterprise AE)",     CS_MANUAL_STANDALONE_SALES),
    ("TC8 - Manual only (Senior QA)",         CS_MANUAL_STANDALONE_QA),
    ("TC9 - Manual only (Content Strategist)", CS_MANUAL_STANDALONE_CONTENT),
]


def run_all():
    results   = []
    passed    = 0
    failed    = 0
    review    = 0
    errored   = 0

    for name, payload in ALL_SUMMARISE_CASES:
        entry = run_candidate_summary_test(name, payload)
        results.append(entry)
        status = entry["final_status"]
        if status == "PASS":
            passed  += 1
        elif status == "FAIL":
            failed  += 1
        elif status == "REVIEW":
            review  += 1
        else:
            errored += 1

    entry5 = run_manual_prompt_chained_test(
        "TC5 - Manual Prompt Refine (chained)",
        TC_DEVOPS,
        MANUAL_PROMPT_USER_TEXT,
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
        entry = run_candidate_summary_test(name, payload)
        results.append(entry)
        _count(entry)

    print(f"\n{'='*60}")
    print(f"  CANDIDATE SUMMARY — TEST RUN COMPLETE")
    print(f"  Total: {len(results)}  |  PASS: {passed}  |  REVIEW: {review}  |  FAIL: {failed}  |  ERROR: {errored}")
    scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    if scores:
        print(f"  Avg Judge Score: {sum(scores)/len(scores):.2f}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    run_all()

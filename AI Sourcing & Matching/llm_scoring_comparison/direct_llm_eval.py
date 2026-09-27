#!/usr/bin/env python3
"""
Direct LLM Evaluation: Claude Sonnet 4.5 vs Gemini 2.5 Flash Lite
===================================================================
Fetches:
  - The single job in the test account (titans10)
  - All 49 candidates directly from /v1/candidates/search/get  (NOT the
    matching endpoint — we do NOT want its AI-ranking filter)

For each candidate, calls both LLMs with:
  - The system prompt  (System_Prompt.txt  OR  New_systemprompt.txt)
  - User message: JD text  +  candidate resumetext

Runs twice — once per prompt — each run writes its **own** files (never overwrites the other):

  OLD prompt (--prompt old):
    direct_eval_raw_old.json
    direct_eval_report_old.txt / .json / .xlsx

  NEW prompt (--prompt new):
    direct_eval_raw_new.json
    direct_eval_report_new.txt / .json / .xlsx

  --fresh only clears the checkpoint for the prompt you are running.

CLI:
  python3 direct_llm_eval.py --prompt old          # System_Prompt.txt   (default)
  python3 direct_llm_eval.py --prompt new          # New_systemprompt.txt
  python3 direct_llm_eval.py --fresh               # clear checkpoint before run
  python3 direct_llm_eval.py --report-only         # build report from checkpoint (no API calls)
  python3 direct_llm_eval.py --sonnet-only         # skip Gemini (debug / cost control)
  python3 direct_llm_eval.py --gemini-only         # skip Sonnet (debug / cost control)
  python3 direct_llm_eval.py --limit 1             # only process first N candidates (test run)
"""

import json
import os
import re
import sys
import time

import anthropic
from google import genai as google_genai
from google.genai import types as genai_types
import requests

from build_direct_excel import build_excel_from_json

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SONNET_MODEL      = "claude-sonnet-4-5"

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL      = "gemini-2.5-flash-lite"

# Account / environment
CRM_HOST          = "https://albatross-titans10.recruitcrm.net"
LOGIN_EMAIL       = os.getenv("RCRM_LOGIN_EMAIL", "")
LOGIN_PASSWORD    = os.getenv("RCRM_LOGIN_PASSWORD", "")

# ── Hardcoded JD text (paste the real JD here so all LLM calls use it) ──────
# If left empty, the script falls back to fetching from CRM.
HARDCODED_JD_TEXT = """Job Title: Senior Full Stack Software Engineer

Job Summary: We are seeking a seasoned and highly motivated Senior Full Stack Software Engineer to join our team. The ideal candidate has a strong background in both backend and frontend technologies. This role will involve the full development lifecycle, from design to deployment, including system architecture.

You'll work closely with cross-functional teams to deliver high-quality, scalable software solutions, leveraging your deep knowledge of system design, cloud architecture, best coding practices, and industry standards.

Responsibilities:

● Full Stack Development: Design, develop, test, and maintain robust web applications using Java Spring Boot for the backend and React or Vue for the frontend.

● Database Management: Design, optimise, and manage relational databases, primarily using MySQL, including writing and optimising complex queries.

● System Design & Architecture: Create and review architecture designs, ensuring scalability, reliability, and performance, with an emphasis on using AWS for cloud infrastructure.

● Code Review & Mentorship: Provide technical guidance to junior engineers, perform code reviews, and advocate for best practices in software development.

● Collaboration: Work closely with cross-functional teams, including product managers, designers, and other engineers, to deliver high-quality features aligned with business goals.

● Continuous Improvement: Implement CI/CD practices, contribute to system monitoring, and continuously optimise applications for performance, security, and scalability.

Requirements:

● Experience: 5+ years of professional experience in software engineering, with expertise in full stack development.

● Backend Proficiency: In-depth understanding of backend frameworks, primarily Java-Spring ecosystem. Really solid fundamentals in OOP, SOLID Principles.

● Frontend Proficiency: Proficiency with popular front end JS/TS frameworks such as react/vue. Solid understanding of component-based architecture and state management. In depth understanding and experience with package managers, bundle optimizations.

● Testing: Understanding of testing best practices, shift-left testing. Ability to write testable code and also write test cases of different types including unit, integration tests, e2e tests, etc.

● Database Skills: Proficient in working with relational databases, particularly MySQL, including experience with database optimization and complex queries. Understanding of non-relational databases.

● Performance monitoring: Ability to use performance monitoring tools to build, maintain and monitor highly performant and scalable systems.

● Cloud Knowledge: Solid experience with AWS (or similar cloud platforms), including system design and deployment of cloud-native applications.

● System Design: Strong understanding of software architecture, including designing scalable and resilient systems.

● Agile Development: Experience working in Agile environments, using tools such as JIRA, Git, and CI/CD pipelines.

● Problem-Solving Skills: Demonstrated ability to solve complex technical challenges and troubleshoot issues effectively."""

WORKSPACE         = os.path.dirname(os.path.abspath(__file__))

# Prompt selection via --prompt flag
_prompt_arg = "old"
if "--prompt" in sys.argv:
    _i = sys.argv.index("--prompt")
    if _i + 1 < len(sys.argv):
        _prompt_arg = sys.argv[_i + 1].lower()

if _prompt_arg in ("new", "new_systemprompt", "newsystemprompt"):
    SYSTEM_PROMPT_FILE = os.path.join(WORKSPACE, "New_systemprompt.txt")
    PROMPT_TAG = "new"
else:
    SYSTEM_PROMPT_FILE = os.path.join(WORKSPACE, "System_Prompt.txt")
    PROMPT_TAG = "old"

RAW_RECORDS_PATH = os.path.join(WORKSPACE, f"direct_eval_raw_{PROMPT_TAG}.json")
REPORT_TXT_PATH  = os.path.join(WORKSPACE, f"direct_eval_report_{PROMPT_TAG}.txt")
REPORT_JSON_PATH = os.path.join(WORKSPACE, f"direct_eval_report_{PROMPT_TAG}.json")
REPORT_XLSX_PATH = os.path.join(WORKSPACE, f"direct_eval_report_{PROMPT_TAG}.xlsx")

# Runtime flags
FRESH       = "--fresh"       in sys.argv
REPORT_ONLY = "--report-only" in sys.argv
SONNET_ONLY = "--sonnet-only" in sys.argv
GEMINI_ONLY = "--gemini-only" in sys.argv

# --limit N  → only process first N candidates (safety valve for test runs)
LIMIT = None
if "--limit" in sys.argv:
    _li = sys.argv.index("--limit")
    if _li + 1 < len(sys.argv):
        try:
            LIMIT = int(sys.argv[_li + 1])
        except ValueError:
            raise SystemExit("--limit requires an integer")

# Scoring constants
DIM_KEYS    = ["fn", "sk", "lv", "tr", "dm", "lc"]
DIM_LABELS  = {"fn": "Function", "sk": "Skills",  "lv": "Level",
               "tr": "Trajectory", "dm": "Domain", "lc": "Location"}
DIM_WEIGHTS = {"fn": 25, "sk": 25, "lv": 20, "tr": 15, "dm": 10, "lc": 5}
KNOWN_LABELS = {"Excellent", "Strong", "Good", "Partial", "Weak", "No Fit"}

FLAG_OVERALL_DIFF = 10
FLAG_DIM_DIFF     = 15
GEMINI_SLEEP_SEC  = 3.0   # reduce Gemini rate-limit failures on long batch runs

# ──────────────────────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────────────────────

_crm_token = ""

def fetch_crm_token(retries=5, backoff_sec=15):
    """Login to CRM and return a fresh Bearer token."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"{CRM_HOST}/v1/login",
                headers={
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/x-www-form-urlencoded",
                },
                data={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") == 429 or "Too many login" in body.get("message", ""):
                wait = 60
                print(f"    ⚠ Rate limited. Waiting {wait}s before retry {attempt}/{retries}...")
                time.sleep(wait)
                continue
            if body.get("status") not in ("success", None) and "token" not in body.get("data", {}):
                raise RuntimeError(f"Login failed: {body.get('message')}")
            token = (body.get("data") or {}).get("token")
            if not token:
                raise RuntimeError("Login response missing data.token")
            return token
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"    ⚠ Login attempt {attempt}/{retries} failed ({e}) — retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
    raise last_err


def crm_headers():
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {_crm_token}",
        "content-type": "application/json",
    }


def ensure_token():
    global _crm_token
    _crm_token = fetch_crm_token()

# ──────────────────────────────────────────────────────────────
# DATA FETCHING
# ──────────────────────────────────────────────────────────────

def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_job():
    """
    Fetch the single job in the account.
    Returns dict with at least: slug, title, jd_text
    """
    resp = requests.post(
        f"{CRM_HOST}/v1/jobs/search/get",
        headers=crm_headers(),
        json={"page": 1, "page_size": 5},
        timeout=30,
    )
    resp.raise_for_status()
    j = resp.json()
    if isinstance(j, list):
        j = j[0] if j else {}
    records = (j.get("data") or {}).get("records") or j.get("records") or []
    if not records:
        raise RuntimeError("No jobs found in account")

    job = records[0]
    slug  = job.get("slug") or ""
    title = job.get("name") or job.get("jobtitle") or "Unknown Job"

    # Try to get JD text — it's stored as an S3 file path in jdtextdetails/jdtext.
    # We'll build the user prompt from the job title + whatever text fields exist.
    jd_raw = ""
    for k in ["jdtext", "jdtextdetails", "description", "details"]:
        v = str(job.get(k) or "").strip()
        if v and len(v) > 30 and "/" not in v[:10]:   # real text, not a path
            jd_raw = v
            break

    jd_text = strip_html(jd_raw)

    # Fallback: try to get JD text via matching endpoint (it sometimes embeds it)
    if not jd_text and slug:
        try:
            mr = requests.post(
                f"{CRM_HOST}/v1/jobs/{slug}/job-candidate-matching",
                headers=crm_headers(),
                json={"page": 1, "page_size": 1},
                timeout=30,
            )
            mj = mr.json()
            if isinstance(mj, list):
                mj = mj[0] if mj else {}
            md = mj.get("data", mj) if isinstance(mj, dict) else mj
            for k in ["jd_text", "jdtext", "description"]:
                v = str((md or {}).get(k) or "").strip()
                if v and len(v) > 30 and "/" not in v[:10]:
                    jd_text = strip_html(v)
                    break
        except Exception as e:
            print(f"  ⚠ Could not fetch JD from matching endpoint: {e}")

    # Prefer hardcoded JD text if provided
    if HARDCODED_JD_TEXT.strip():
        jd_text = HARDCODED_JD_TEXT.strip()

    return {"slug": slug, "title": title, "jd_text": jd_text}


def fetch_candidates(page_size=50):
    """
    Fetch all candidates via /v1/candidates/search/get.
    Returns list of dicts: {id, name, resumetext}
    """
    minimal_payload = {
        "page_size": page_size,
        "page":      1,
        "sort_by":   "updatedon",
        "sortOrder": "desc",
        "andsearch": [],
        "orsearch":  [],
        "notsearch": [],
        "searchinnotes": "",
        "columns":   {},
        "fullTextSearch": True,
    }
    resp = requests.post(
        f"{CRM_HOST}/v1/candidates/search/get",
        headers=crm_headers(),
        json=minimal_payload,
        timeout=30,
    )
    resp.raise_for_status()
    j = resp.json()
    if isinstance(j, list):
        j = j[0] if j else {}
    records = (j.get("data") or {}).get("records") or j.get("records") or []

    candidates = []
    for rec in records:
        cid  = rec.get("id") or rec.get("srno") or rec.get("sourceid")
        name = (rec.get("candidatename")
                or f"{rec.get('firstname','')} {rec.get('lastname','')}".strip()
                or f"Candidate-{cid}")
        rt   = strip_html(rec.get("resumetext") or "")
        candidates.append({"id": cid, "name": name, "resumetext": rt})

    return candidates

# ──────────────────────────────────────────────────────────────
# LLM CALLS
# ──────────────────────────────────────────────────────────────

def build_user_message(jd_title, jd_text, resumetext):
    """Construct the user-turn message exactly as the web app does."""
    jd_part = jd_text if jd_text else f"[Job: {jd_title}]"
    return f"JD:\n{jd_part}\n\nCandidate Resume:\n{resumetext}"


def parse_llm_json(raw_text):
    """
    Robustly extract the JSON scoring object from LLM output.
    Strips markdown fences, preamble, etc.
    Returns parsed dict or None.
    """
    if not raw_text:
        return None
    text = re.sub(r"```(?:json)?", "", raw_text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    # Truncated JSON (MAX_TOKENS): close open braces/brackets heuristically
    repaired = blob.rstrip().rstrip(",")
    for _ in range(blob.count("{") - blob.count("}")):
        repaired += "}"
    try:
        return json.loads(repaired)
    except Exception:
        return None


def valid_scoring_json(parsed):
    """True when all six dimension scores are present."""
    if not parsed or not isinstance(parsed, dict):
        return False
    for dk in DIM_KEYS:
        block = parsed.get(dk)
        if not isinstance(block, dict) or block.get("s") is None:
            return False
    return True


def extract_gemini_text(resp):
    raw, finish = "", None
    if hasattr(resp, "candidates") and resp.candidates:
        cand = resp.candidates[0]
        finish = getattr(cand, "finish_reason", None)
        parts = cand.content.parts if cand.content else []
        for p in (parts or []):
            if not getattr(p, "thought", False) and getattr(p, "text", None):
                raw += p.text
    if not raw and hasattr(resp, "text") and resp.text:
        raw = resp.text
    return raw, finish


def call_sonnet(client, system_prompt, user_message):
    """Call Claude Sonnet 4.5. Returns (parsed_dict, usage_dict)."""
    usage = {}
    try:
        resp = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = resp.content[0].text if resp.content else ""
        usage = {
            "input_tokens":                 getattr(resp.usage, "input_tokens", 0),
            "output_tokens":                getattr(resp.usage, "output_tokens", 0),
            "cache_creation_input_tokens":  getattr(resp.usage, "cache_creation_input_tokens", 0),
            "cache_read_input_tokens":      getattr(resp.usage, "cache_read_input_tokens", 0),
        }
        return parse_llm_json(raw), usage
    except Exception as e:
        print(f"      ✗ Sonnet error: {e}")
        return None, usage


def call_gemini(gemini_client, system_prompt, user_message, retries=6):
    """Call Gemini 2.5 Flash Lite. No thinking mode — keeps full budget for JSON output."""
    token_limits = [8192, 12288, 16384]
    last_err = None
    for attempt in range(1, retries + 1):
        max_tok = token_limits[min(attempt - 1, len(token_limits) - 1)]
        try:
            resp = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0,
                    max_output_tokens=max_tok,
                ),
            )
            raw, finish = extract_gemini_text(resp)
            parsed = parse_llm_json(raw)
            if parsed and valid_scoring_json(parsed):
                return parsed
            last_err = (
                f"invalid/incomplete JSON (finish={finish}, chars={len(raw)}, "
                f"max_tok={max_tok})"
            )
            print(f"      ⚠ Gemini attempt {attempt}/{retries}: {last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"      ✗ Gemini error attempt {attempt}/{retries}: {e}")
        if attempt < retries:
            time.sleep(5 * attempt)
    return None

# ──────────────────────────────────────────────────────────────
# SCORING HELPERS
# ──────────────────────────────────────────────────────────────

def score_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("s")
    return None


def reason_val(d, key):
    if d and key in d and isinstance(d[key], dict):
        return d[key].get("r", "")
    return ""


def label_from_score(score):
    """Derive label from numeric score (used when prompt doesn't return 'l')."""
    if score is None:
        return "N/A"
    if score >= 85:  return "Excellent"
    if score >= 70:  return "Strong"
    if score >= 55:  return "Good"
    if score >= 40:  return "Partial"
    if score >= 20:  return "Weak"
    return "No Fit"


def clean_label(raw):
    if not raw:
        return "N/A"
    s = str(raw).strip()
    for lab in ("No Fit", "Excellent", "Strong", "Good", "Partial", "Weak"):
        if s.lower().startswith(lab.lower()):
            return lab
    first = s.split()[0].rstrip(".,;:")
    return first if first in KNOWN_LABELS else s[:15]


def formula_score(d):
    """Weighted formula overall from dimension scores."""
    if not d:
        return None
    vals = {dk: score_val(d, dk) for dk in DIM_KEYS}
    if any(v is None for v in vals.values()):
        return None
    return round(sum(vals[dk] * DIM_WEIGHTS[dk] for dk in DIM_KEYS) / 100, 1)


def diff_str(a, b):
    if a is None or b is None:
        return "N/A"
    d = a - b
    if isinstance(d, float) and not d.is_integer():
        return f"{d:+.1f}"
    return f"{int(d):+d}"


def flag(a, b, threshold):
    if a is None or b is None:
        return ""
    return " ⚠" if abs(a - b) > threshold else ""


def get_overall(d, fs):
    """Get overall from parsed dict; fall back to formula."""
    if d is None:
        return None
    o = d.get("o")
    if o is None:
        o = fs
    return o


def get_label(d, overall, use_prompt_label):
    """Get label — from dict if new prompt, else derive from score."""
    if use_prompt_label and d and d.get("l"):
        return clean_label(d["l"])
    return label_from_score(overall)

# ──────────────────────────────────────────────────────────────
# CHECKPOINT
# ──────────────────────────────────────────────────────────────

def save_raw_records(records):
    payload = {
        "saved_at":          time.strftime("%Y-%m-%d %H:%M:%S"),
        "prompt_tag":        PROMPT_TAG,
        "system_prompt_file": os.path.basename(SYSTEM_PROMPT_FILE),
        "record_count":      len(records),
        "records":           records,
    }
    tmp = RAW_RECORDS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, RAW_RECORDS_PATH)


def load_raw_records():
    if not os.path.isfile(RAW_RECORDS_PATH):
        return [], {}
    with open(RAW_RECORDS_PATH) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data, {}
    return data.get("records") or [], data

# ──────────────────────────────────────────────────────────────
# REPORT GENERATION
# ──────────────────────────────────────────────────────────────

def build_and_write_report(all_results, use_prompt_label):
    if not all_results:
        print("\n✗ No results to report.")
        return False

    lines = []
    json_out = []

    total = 0
    label_matches = 0
    s_vs_g_diffs = []
    dim_flags = 0

    job_name = all_results[0].get("job_title", "Job") if all_results else "Job"
    job_slug = all_results[0].get("job_slug", "") if all_results else ""

    lines.append(f"{'═'*70}")
    lines.append(f"DIRECT LLM EVAL: Sonnet 4.5 vs Gemini 2.5 Flash Lite")
    lines.append(f"Prompt:  {PROMPT_TAG.upper()} ({os.path.basename(SYSTEM_PROMPT_FILE)})")
    lines.append(f"Job:     {job_name}  (slug: {job_slug})")
    lines.append(f"{'═'*70}")

    for r in all_results:
        sn = r.get("sonnet")
        gm = r.get("gemini")
        su = r.get("sonnet_usage") or {}

        lines.append(f"\n  CANDIDATE: {r['candidate_name']}  (ID {r['candidate_id']})")
        lines.append(f"  {'─'*66}")
        lines.append(f"  {'DIM':<10} {'SONNET 4.5':>12} {'GEMINI':>10} {'Δ (S-G)':>10}")
        lines.append(f"  {'─'*10} {'─'*12} {'─'*10} {'─'*10}")

        dim_rows = []
        for dk in DIM_KEYS:
            sn_s = score_val(sn, dk)
            gm_s = score_val(gm, dk)
            d    = diff_str(sn_s, gm_s)
            fl   = flag(sn_s, gm_s, FLAG_DIM_DIFF)
            if fl:
                dim_flags += 1
            label_col = f"{DIM_LABELS[dk]} ({dk})"
            lines.append(
                f"  {label_col:<22} {str(sn_s) if sn_s is not None else 'N/A':>8} "
                f"{str(gm_s) if gm_s is not None else 'N/A':>10} {d:>10}{fl}"
            )
            dim_rows.append({"dim": dk, "sonnet": sn_s, "gemini": gm_s})

        sn_fs = formula_score(sn)
        gm_fs = formula_score(gm)
        sn_o  = get_overall(sn, sn_fs)
        gm_o  = get_overall(gm, gm_fs)

        sn_l  = get_label(sn, sn_o, use_prompt_label)
        gm_l  = get_label(gm, gm_o, use_prompt_label)

        o_diff = diff_str(sn_o, gm_o)
        o_flag = flag(sn_o, gm_o, FLAG_OVERALL_DIFF)
        if sn_o is not None and gm_o is not None:
            s_vs_g_diffs.append(abs(sn_o - gm_o))

        l_match = "✓" if sn_l == gm_l else "✗"
        if sn_l == gm_l:
            label_matches += 1

        lines.append(f"  {'─'*10} {'─'*12} {'─'*10} {'─'*10}")
        lines.append(
            f"  {'FORMULA SCORE':<22} {str(sn_fs) if sn_fs is not None else 'N/A':>8} "
            f"{str(gm_fs) if gm_fs is not None else 'N/A':>10}"
        )
        lines.append(
            f"  {'LLM OVERALL':<22} {str(sn_o) if sn_o is not None else 'N/A':>8} "
            f"{str(gm_o) if gm_o is not None else 'N/A':>10} {o_diff:>10}{o_flag}"
        )
        lines.append(
            f"  {'LABEL':<22} {'Sonnet: '+sn_l:>12}  Gemini: {gm_l}  {l_match}"
        )

        # Overall reasoning (or field — only in new prompt)
        sn_or = (sn or {}).get("or", "")
        gm_or = (gm or {}).get("or", "")
        if sn_or or gm_or:
            lines.append(f"\n  OVERALL REASONING (or):")
            if sn_or:
                lines.append(f"  Sonnet: {sn_or}")
            if gm_or:
                lines.append(f"  Gemini: {gm_or}")

        if su:
            lines.append(
                f"  {'SONNET TOKENS':<22} in={su.get('input_tokens','—')}  "
                f"out={su.get('output_tokens','—')}  "
                f"(cache writes {su.get('cache_creation_input_tokens','—')}, "
                f"reads {su.get('cache_read_input_tokens','—')})"
            )

        lines.append(f"\n  REASONING:")
        for dk in DIM_KEYS:
            sn_r = reason_val(sn, dk)
            gm_r = reason_val(gm, dk)
            lines.append(f"  {dk} | Sonnet: {sn_r}")
            lines.append(f"     | Gemini: {gm_r}")

        total += 1
        json_out.append({
            "job_title":    r["job_title"],
            "job_slug":     r["job_slug"],
            "candidate":    r["candidate_name"],
            "cid":          r["candidate_id"],
            "dims":         dim_rows,
            "formula_score": {"sonnet": sn_fs, "gemini": gm_fs},
            "overall": {
                "sonnet":   sn_o,
                "gemini":   gm_o,
                "diff_s_vs_g": (sn_o - gm_o) if (sn_o is not None and gm_o is not None) else None,
                "sonnet_or": sn_or,
                "gemini_or": gm_or,
            },
            "label": {
                "sonnet": sn_l,
                "gemini": gm_l,
                "match": sn_l == gm_l,
            },
            "sonnet_raw":   sn,
            "gemini_raw":   gm,
            "sonnet_usage": su,
        })

    avg_diff = sum(s_vs_g_diffs) / len(s_vs_g_diffs) if s_vs_g_diffs else 0
    max_diff = max(s_vs_g_diffs) if s_vs_g_diffs else 0
    max_diff_cand = ""
    for r in json_out:
        d = r["overall"]["diff_s_vs_g"]
        if d is not None and abs(d) == max_diff:
            max_diff_cand = r["candidate"]
            break

    # Who is closer to Sonnet (lower avg abs diff = better challenger)
    lines.append(f"\n\n{'═'*70}")
    lines.append(f"AGGREGATE SUMMARY")
    lines.append(f"{'═'*70}")
    lines.append(f"  Prompt:                          {PROMPT_TAG.upper()}  ({os.path.basename(SYSTEM_PROMPT_FILE)})")
    lines.append(f"  Candidates evaluated:            {total}")
    lines.append(f"  Avg |Sonnet vs Gemini| overall:  {avg_diff:.1f} pts")
    lines.append(f"  Max |Sonnet vs Gemini| overall:  {max_diff} pts  ({max_diff_cand})")
    lines.append(f"  Label match rate (S vs G):       {label_matches} / {total}  "
                 f"({100*label_matches//total if total else 0}%)")
    lines.append(f"  Dimension flags ⚠ (>±{FLAG_DIM_DIFF} pts):    {dim_flags} instances")
    lines.append(f"  Overall flags   ⚠ (>±{FLAG_OVERALL_DIFF} pts):    "
                 f"{sum(1 for d in s_vs_g_diffs if d > FLAG_OVERALL_DIFF)} candidates")

    # Sonnet token totals
    usage_totals = {}
    sonnet_calls_with_usage = 0
    for r in all_results:
        su = r.get("sonnet_usage") or {}
        if su:
            sonnet_calls_with_usage += 1
            for k, v in su.items():
                if isinstance(v, (int, float)):
                    usage_totals[k] = usage_totals.get(k, 0) + int(v)

    tin  = usage_totals.get("input_tokens", 0)
    tout = usage_totals.get("output_tokens", 0)
    sonnet_ok = sum(1 for r in all_results if r.get("sonnet"))
    gemini_ok = sum(1 for r in all_results if r.get("gemini"))

    lines.append(f"\n  SONNET API CALLS:                {sonnet_ok} ok / {total - sonnet_ok} failed")
    if sonnet_calls_with_usage:
        lines.append(f"  Sum input_tokens:                {tin}")
        lines.append(f"  Sum output_tokens:               {tout}")
        lines.append(f"  Total (excl cache):              {tin + tout}")
    lines.append(f"\n  GEMINI API CALLS:                {gemini_ok} ok / {total - gemini_ok} failed")

    report_text = "\n".join(lines)
    with open(REPORT_TXT_PATH, "w") as f:
        f.write(report_text)

    payload = {
        "prompt_tag":   PROMPT_TAG,
        "prompt_file":  os.path.basename(SYSTEM_PROMPT_FILE),
        "sonnet_model": SONNET_MODEL,
        "gemini_model": GEMINI_MODEL,
        "total":        total,
        "anthropic_usage": {
            "model":                  SONNET_MODEL,
            "successful_calls":       sonnet_ok,
            "calls_with_usage":       sonnet_calls_with_usage,
            "totals":                 usage_totals,
        },
        "records": json_out,
    }
    with open(REPORT_JSON_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    print(report_text)
    print(f"\n✓ Report text  → {REPORT_TXT_PATH}")
    print(f"✓ Report JSON  → {REPORT_JSON_PATH}")

    try:
        xlsx = build_excel_from_json(REPORT_JSON_PATH, REPORT_XLSX_PATH)
        print(f"✓ Report Excel → {xlsx}")
    except Exception as e:
        print(f"\n✗ Excel generation failed: {e}")
        print(f"  Rebuild with: python3 build_direct_excel.py --prompt {PROMPT_TAG}")
        print(f"  Raw checkpoint safe at: {RAW_RECORDS_PATH}")

    return True


def print_output_paths():
    """Show which files this run will write (separate from the other prompt)."""
    other = "new" if PROMPT_TAG == "old" else "old"
    print("Output files for THIS run (will NOT touch the other prompt):")
    print(f"  Checkpoint:  {RAW_RECORDS_PATH}")
    print(f"  Report txt:  {REPORT_TXT_PATH}")
    print(f"  Report json: {REPORT_JSON_PATH}")
    print(f"  Report xlsx: {REPORT_XLSX_PATH}")
    print(f"  (Other prompt '{other}' keeps its own direct_eval_*_{other}.* files)\n")

# ──────────────────────────────────────────────────────────────
# GEMINI-ONLY RETRY (no full CRM re-run)
# ──────────────────────────────────────────────────────────────

def run_gemini_retry_only(system_prompt, use_prompt_label):
    """Re-call Gemini only for checkpoint rows where gemini is missing."""
    all_results, _ = load_raw_records()
    pending = [r for r in all_results if not r.get("gemini")]
    if not pending:
        print("✓ All Gemini scores already present — rebuilding report only\n")
        build_and_write_report(all_results, use_prompt_label)
        return

    print(f"Retrying Gemini for {len(pending)} candidate(s)...\n")
    gemini_client = google_genai.Client(api_key=GEMINI_API_KEY)

    job = {
        "title": all_results[0]["job_title"],
        "slug":  all_results[0]["job_slug"],
        "jd_text": HARDCODED_JD_TEXT.strip(),
    }

    print("Fetching resumetext from CRM...")
    ensure_token()
    rt_map = {c["id"]: c["resumetext"] for c in fetch_candidates()}
    print(f"✓ Loaded resumetext for {len(rt_map)} candidates\n")

    for i, rec in enumerate(pending, 1):
        cid  = rec["candidate_id"]
        name = rec["candidate_name"]
        rt   = rec.get("resumetext") or rt_map.get(cid, "")
        if not rt:
            print(f"  [{i:>2}/{len(pending)}] {name} — ⚠ no resumetext, skipping")
            continue

        print(f"  [{i:>2}/{len(pending)}] {name} (ID {cid})")
        user_msg = build_user_message(job["title"], job["jd_text"], rt)
        time.sleep(GEMINI_SLEEP_SEC)
        gm_result = call_gemini(gemini_client, system_prompt, user_msg)
        gm_o = get_overall(gm_result, formula_score(gm_result))
        print(f"      Gemini: overall={gm_o}")

        rec["resumetext"] = rt
        rec["gemini"] = gm_result
        save_raw_records(all_results)

    still_fail = [r for r in all_results if not r.get("gemini")]
    if still_fail:
        names = ", ".join(r["candidate_name"] for r in still_fail[:5])
        extra = f" (+{len(still_fail)-5} more)" if len(still_fail) > 5 else ""
        raise SystemExit(
            f"\n✗ Gemini still failed for {len(still_fail)} candidate(s): {names}{extra}"
        )

    print(f"\n✓ Gemini retry complete — {len(all_results)}/{len(all_results)} OK\n")
    build_and_write_report(all_results, use_prompt_label)


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    print(f"{'═'*60}")
    print(f"Direct LLM Eval — prompt: {PROMPT_TAG.upper()}")
    print(f"System prompt file: {os.path.basename(SYSTEM_PROMPT_FILE)}")
    print(f"{'═'*60}\n")
    print_output_paths()

    if FRESH and os.path.isfile(RAW_RECORDS_PATH):
        os.remove(RAW_RECORDS_PATH)
        print(f"✓ Cleared prior checkpoint: {RAW_RECORDS_PATH}\n")

    if REPORT_ONLY:
        all_results, env = load_raw_records()
        if not all_results:
            raise SystemExit(
                f"No checkpoint at {RAW_RECORDS_PATH}. Run without --report-only first."
            )
        print(f"Mode: REPORT ONLY — {len(all_results)} records from checkpoint")
        if env.get("saved_at"):
            print(f"  Saved at: {env['saved_at']}\n")
        use_prompt_label = (PROMPT_TAG == "new")
        build_and_write_report(all_results, use_prompt_label)
        return

    # ── Load system prompt ──────────────────────────────────────
    if not os.path.isfile(SYSTEM_PROMPT_FILE):
        raise SystemExit(f"System prompt not found: {SYSTEM_PROMPT_FILE}")
    with open(SYSTEM_PROMPT_FILE) as f:
        system_prompt = f.read().strip()
    print(f"✓ Loaded system prompt from {os.path.basename(SYSTEM_PROMPT_FILE)}\n")

    use_prompt_label = (PROMPT_TAG == "new")

    if GEMINI_ONLY:
        run_gemini_retry_only(system_prompt, use_prompt_label)
        return

    # ── Set up API clients ──────────────────────────────────────
    sonnet_client = None
    gemini_client = None
    gemini_model  = None

    if not GEMINI_ONLY:
        sonnet_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        print("✓ Anthropic client initialised")

    if not SONNET_ONLY:
        gemini_client = google_genai.Client(api_key=GEMINI_API_KEY)
        gemini_model  = gemini_client   # pass client; model name used in call
        print(f"✓ Gemini client initialised: {GEMINI_MODEL}")

    print()

    # ── Get CRM token ───────────────────────────────────────────
    print("Logging in to CRM...")
    ensure_token()
    print("✓ CRM token obtained\n")

    # ── Fetch job ───────────────────────────────────────────────
    print("Fetching job...")
    job = fetch_job()
    print(f"✓ Job: {job['title']}  (slug: {job['slug']})")
    if job["jd_text"]:
        print(f"  JD text: {len(job['jd_text'])} chars\n")
    else:
        print(f"  ⚠ No JD text found — will use job title as JD placeholder\n")

    # ── Fetch candidates ────────────────────────────────────────
    print("Fetching candidates...")
    candidates = fetch_candidates()
    print(f"✓ {len(candidates)} candidates fetched\n")

    # ── Load checkpoint ─────────────────────────────────────────
    all_results, _ = load_raw_records()
    existing_by_id = {r["candidate_id"]: r for r in all_results}
    if all_results:
        print(f"✓ Resuming from checkpoint — {len(all_results)} record(s) on disk\n")

    # ── Score each candidate ────────────────────────────────────
    if LIMIT:
        print(f"⚠ --limit {LIMIT}: only processing first {LIMIT} candidate(s)\n")
        candidates = candidates[:LIMIT]

    for i, cand in enumerate(candidates, 1):
        cid  = cand["id"]
        name = cand["name"]
        rt   = cand["resumetext"]
        existing = existing_by_id.get(cid)

        if GEMINI_ONLY and existing and existing.get("gemini"):
            print(f"  [{i:>2}/{len(candidates)}] {name} — skipped (Gemini OK)")
            continue
        if SONNET_ONLY and existing and existing.get("sonnet"):
            print(f"  [{i:>2}/{len(candidates)}] {name} — skipped (Sonnet OK)")
            continue
        if not GEMINI_ONLY and not SONNET_ONLY and existing:
            print(f"  [{i:>2}/{len(candidates)}] {name} — skipped (checkpoint)")
            continue

        if not rt:
            print(f"  [{i:>2}/{len(candidates)}] {name} — ⚠ no resumetext, skipping")
            if not existing:
                all_results.append({
                    "job_title":    job["title"],
                    "job_slug":     job["slug"],
                    "candidate_name": name,
                    "candidate_id": cid,
                    "sonnet":       None,
                    "gemini":       None,
                    "sonnet_usage": {},
                    "skip_reason":  "no resumetext",
                })
                save_raw_records(all_results)
            continue

        print(f"  [{i:>2}/{len(candidates)}] {name} (ID {cid})")

        user_msg = build_user_message(job["title"], job["jd_text"], rt)

        # Call Sonnet
        sn_result, sn_usage = None, {}
        if GEMINI_ONLY and existing:
            sn_result = existing.get("sonnet")
            sn_usage = existing.get("sonnet_usage") or {}
        elif not GEMINI_ONLY and sonnet_client:
            sn_result, sn_usage = call_sonnet(sonnet_client, system_prompt, user_msg)
            sn_o = get_overall(sn_result, formula_score(sn_result))
            print(f"      Sonnet: overall={sn_o}  tokens_in={sn_usage.get('input_tokens','?')}  tokens_out={sn_usage.get('output_tokens','?')}")

        # Call Gemini
        gm_result = None
        if not SONNET_ONLY and gemini_client:
            time.sleep(GEMINI_SLEEP_SEC)
            gm_result = call_gemini(gemini_client, system_prompt, user_msg)
            gm_o = get_overall(gm_result, formula_score(gm_result))
            print(f"      Gemini: overall={gm_o}")

        record = {
            "job_title":    job["title"],
            "job_slug":     job["slug"],
            "candidate_name": name,
            "candidate_id": cid,
            "resumetext":   rt,
            "sonnet":       sn_result,
            "gemini":       gm_result,
            "sonnet_usage": sn_usage,
        }
        if existing:
            existing.update(record)
        else:
            all_results.append(record)
            existing_by_id[cid] = record
        save_raw_records(all_results)

    print(f"\n✓ All candidates processed ({len(all_results)} records)\n")

    # ── Build report ────────────────────────────────────────────
    try:
        build_and_write_report(all_results, use_prompt_label)
    except Exception as e:
        print(f"\n✗ Report generation failed: {e}")
        print(f"  Raw records are safe in: {RAW_RECORDS_PATH}")
        print(f"  Re-run with --report-only to rebuild.")
        raise


if __name__ == "__main__":
    main()

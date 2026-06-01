import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from constants import GROQ_API_BASE_URL, JUDGE_MODEL
from logger import log_judge_llm_payload

load_dotenv(Path(__file__).resolve().parent / ".env")

_groq_key = os.environ.get("GROQ_API_KEY")
if not _groq_key:
    raise RuntimeError(
        "GROQ_API_KEY is missing. Add it to .env in the project root for the judge."
    )

_client = OpenAI(api_key=_groq_key.strip(), base_url=GROQ_API_BASE_URL)


def _strip_json_fence(s: str) -> str:
    """Remove optional ``` / ```json fences if the model wraps JSON."""
    s = s.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def format_task_input(task_input: str | dict) -> str:
    """Render API/business input for the judge (candidate profile, job fields, keys, etc.)."""
    if isinstance(task_input, str):
        return task_input.strip()
    return json.dumps(task_input, indent=2, ensure_ascii=False, default=str)


JUDGE_SYSTEM_PROMPT = """You are a strict, unbiased evaluator comparing two AI-generated outputs for the SAME input.

You always receive:

1. INPUT — the data used to generate the response (context only)
2. Response 1 — existing (baseline / env A) → treat as reference
3. Response 2 — new (candidate / env B) → evaluate this

Your task:
Evaluate how well the NEW response (Response 2) performs compared to the EXISTING response (Response 1) for fulfilling the same task.

---

### Evaluation Principles

* Treat **Response 1 as the reference (ground truth proxy)** for expected content, structure, and completeness
* Compare **Response 2 strictly against Response 1**
* Use INPUT only to understand the task context, NOT as the primary scoring baseline

---

### What to evaluate (implicitly)

* Does Response 2 preserve all important information and sections present in Response 1?
* Is anything missing, reduced, or degraded compared to Response 1?
* Is the structure and format (e.g., sections, HTML, bullets) as good as or worse than Response 1?
* Does Response 2 maintain the same usefulness and clarity as Response 1?

---

### Scoring (single integer 1–5)

* 5 — New response is clearly better than existing
* 4 — New response is about as good as existing (no meaningful loss)
* 3 — New response is somewhat worse but still acceptable (minor loss)
* 2 — New response is noticeably worse (missing sections, weaker structure, reduced usefulness)
* 1 — New response is much worse or fails the task

---

### Critical Rules (STRICT)

* If Response 2 is missing ANY important section present in Response 1, the score MUST be ≤ 3
* If Response 2 has significantly worse structure or formatting (e.g., missing sections, poor HTML, no clear formatting), reduce the score
* Do NOT reward shorter responses if they omit useful content
* Minor wording differences are acceptable, but loss of content or structure is NOT
* Always prioritize completeness and structural parity with Response 1
* **If BOTH responses are empty or whitespace-only**, the task produced no usable output — score **1** (do **not** treat empty-vs-empty as "equal quality" or give 4)

---

### Output (STRICT JSON ONLY)

{
"score": <integer 1–5>,
"reason": "<one or two short sentences explaining key differences>"
}
"""


def evaluate_response(
    *,
    response_existing: str,
    response_new: str,
    task_input: str | dict[str, Any],
    extra_context: str = "",
) -> dict:
    """
    Groq judge: scores **response_new** vs **response_existing** given **task_input**.

    Args:
        response_existing: baseline / env A output
        response_new:      new model / env B output
        task_input:        Structured input (e.g. candidate + key, job + key) or a string summary
        extra_context:     Optional free-text (endpoint name, test id, etc.)

    Returns:
        dict with keys ``score`` (int 1–5), ``reason``, or ``error`` on failure.
        Always includes ``_response_a``, ``_response_b``, ``_input_text`` for logging.
    """
    input_block = format_task_input(task_input)
    extra = extra_context.strip()

    _meta = {
        "_response_a": response_existing or "",
        "_response_b": response_new or "",
        "_input_text":  input_block,
    }

    ex = (response_existing or "").strip()
    nw = (response_new or "").strip()
    if not ex and not nw:
        return {
            "score": 1,
            "reason": "Both responses are empty; no output to compare — task failed or upstream returned nothing.",
            **_meta,
        }

    sections = [
        "### INPUT (what the feature used)",
        input_block,
    ]
    if extra:
        sections.extend(["### Additional context", extra])
    sections.extend(
        [
            "---",
            "### Response — existing (baseline)",
            response_existing,
            "---",
            "### Response — new (candidate)",
            response_new,
        ]
    )
    user_message = "\n".join(sections)

    log_judge_llm_payload(
        model=JUDGE_MODEL,
        system_prompt=JUDGE_SYSTEM_PROMPT,
        user_message=user_message,
        extra={"extra_context": extra} if extra else None,
    )

    raw = ""
    try:
        completion = _client.chat.completions.create(
            model=JUDGE_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
        )
        message = completion.choices[0].message
        raw = _strip_json_fence(message.content or "")
        if not raw:
            return {"error": "judge_empty_content", "raw_output": None, **_meta}

        out = json.loads(raw)
        sc = out.get("score")
        if sc is None:
            return {"error": "judge_missing_score", "raw_output": raw, **_meta}
        try:
            out["score"] = int(round(float(sc)))
        except (TypeError, ValueError):
            return {"error": "judge_invalid_score", "raw_output": raw, **_meta}
        out["score"] = max(1, min(5, out["score"]))
        out.update(_meta)
        return out

    except json.JSONDecodeError as e:
        return {"error": f"judge_invalid_json: {e}", "raw_output": raw, **_meta}
    except Exception as e:
        return {"error": f"judge_call_failed: {e}", **_meta}

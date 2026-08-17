#!/usr/bin/env python3
"""
Activity Context (PW-6968) — LLM judge.

The judge is an ADAPTER, not a referee. Its only job is turning the free text the product
produced into a structured answer to one narrow question, so a deterministic check can be
applied to it afterwards. It never decides whether the product passed — `runner.py` does
that against the expected value recorded at seed time.

That distinction matters. A judge asked "is this good?" produces an opinion that drifts
between runs. A judge asked "does this reasoning state the candidate has X? answer yes,
no, or not-stated" produces something you can assert on.

DETERMINISM
-----------
`temperature` is NOT settable on Claude Sonnet 5 — a non-default value returns HTTP 400.
What is available:
  * thinking disabled  — adaptive thinking is on by default and is the main variance source
  * effort "low"       — this is classification, not judgement
  * json_schema output — the shape is fixed, so only the answer varies

Structured outputs do not support numeric minimum/maximum; an enum bounds an integer.

    python3 activity_context_judge.py --self-test
    python3 activity_context_judge.py --calibrate labels.json
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field

import anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from credentials import resolve_anthropic_key  # noqa: E402

JUDGE_MODEL = "claude-sonnet-5"
JUDGE_EFFORT = "low"
JUDGE_MAX_TOKENS = 700
MAX_RETRIES = 3

SYSTEM = (
    "You read AI-generated candidate match reasoning and answer one narrow question about "
    "what it SAYS. You are not assessing whether the reasoning is good, fair or correct — "
    "another layer does that. Report only what the text states.\n\n"
    "Rules:\n"
    "  - Answer from the reasoning text alone. Do not infer from the resume or your own "
    "knowledge of the candidate.\n"
    "  - 'not_stated' is a real answer and the right one whenever the text does not "
    "address the question. Do not guess.\n"
    "  - Quote the exact span you based the answer on. If you cannot quote it, the answer "
    "is 'not_stated'.\n"
    "  - A statement of ABSENCE ('no evidence of Kubernetes') is a statement, not a "
    "hallucination. Report it as such.\n"
    "  - Dates the reasoning cites are real and are shown to you. A correct date citation "
    "is not an invention."
)


def _schema(answer_enum, extra_props=None, extra_required=None):
    props = {"answer": {"type": "string", "enum": list(answer_enum)},
             "quote": {"type": "string"},
             "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}
    props.update(extra_props or {})
    req = ["answer", "quote", "confidence"] + list(extra_required or [])
    return {"type": "object", "properties": props, "required": req,
            "additionalProperties": False}


FACT_USED_SCHEMA = _schema(["yes", "no", "not_stated"])
SOURCE_SCHEMA = _schema(["resume", "note", "call_log", "both", "not_stated"])
GROUNDED_SCHEMA = _schema(["grounded", "unsupported", "not_stated"])
VALUE_SCHEMA = _schema(["yes", "no", "not_stated"],
                       {"value_reported": {"type": "string"}}, ["value_reported"])


@dataclass
class Verdict:
    question: str
    answer: str
    quote: str = ""
    confidence: str = "low"
    value_reported: str = ""
    error: str = ""

    def as_dict(self):
        return asdict(self)


class Judge:
    """One question per call. Cheap, bounded, and each answer is independently checkable."""

    def __init__(self, model=JUDGE_MODEL, effort=JUDGE_EFFORT):
        self.model, self.effort = model, effort
        key = resolve_anthropic_key()
        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.calls = self.in_tok = self.out_tok = 0

    def _ask(self, prompt, schema, question):
        last = ""
        for i in range(1, MAX_RETRIES + 1):
            try:
                r = self.client.messages.create(
                    model=self.model, max_tokens=JUDGE_MAX_TOKENS, system=SYSTEM,
                    thinking={"type": "disabled"},
                    output_config={"effort": self.effort,
                                   "format": {"type": "json_schema", "schema": schema}},
                    messages=[{"role": "user", "content": prompt}])
                self.calls += 1
                self.in_tok += r.usage.input_tokens
                self.out_tok += r.usage.output_tokens
                d = json.loads(next(b.text for b in r.content if b.type == "text"))
                return Verdict(question=question, answer=d["answer"],
                               quote=d.get("quote", ""),
                               confidence=d.get("confidence", "low"),
                               value_reported=d.get("value_reported", ""))
            except anthropic.APIStatusError as e:
                last = f"HTTP {e.status_code}: {str(e.message)[:150]}"
                if 400 <= e.status_code < 500 and e.status_code != 429:
                    break
            except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
                last = type(e).__name__
            except (KeyError, ValueError, StopIteration) as e:
                last = f"bad judge output: {type(e).__name__}"
            if i < MAX_RETRIES:
                time.sleep(2 * i)
        return Verdict(question=question, answer="not_stated", error=last)

    def _frame(self, reasoning, activity=""):
        out = f"===== THE AI'S REASONING =====\n{reasoning.strip()}\n"
        if activity:
            out += (f"\n===== THE CANDIDATE'S NOTES AND CALL LOGS =====\n"
                    f"{activity.strip()}\n")
        return out

    def fact_used(self, reasoning, fact, activity=""):
        """Does the reasoning state this specific fact?"""
        return self._ask(
            self._frame(reasoning, activity) +
            f"\nQUESTION: does the reasoning state that {fact}?\n"
            f"Answer yes / no / not_stated, and quote the span.",
            FACT_USED_SCHEMA, f"fact_used: {fact}")

    def source_of(self, reasoning, claim, activity=""):
        """Where does the reasoning say a claim came from?"""
        return self._ask(
            self._frame(reasoning, activity) +
            f"\nQUESTION: where does the reasoning say it got this — {claim}?\n"
            f"Answer resume / note / call_log / both / not_stated, and quote the span.",
            SOURCE_SCHEMA, f"source_of: {claim}")

    def grounded(self, reasoning, activity):
        """
        Is every positive claim traceable to the material supplied?

        Deliberately narrow: only INVENTED POSITIVE claims count as unsupported. Statements
        of absence and reasonable inference are legitimate analysis — an earlier version
        flagged both and produced 19-24 false failures out of 34.
        """
        return self._ask(
            self._frame(reasoning, activity) +
            "\nQUESTION: does the reasoning assert any POSITIVE claim about the candidate "
            "that the material above does not support at all?\n"
            "Statements that something is absent are NOT unsupported. Reasonable inference "
            "from stated evidence is NOT unsupported. Only outright invention counts.\n"
            "Answer grounded / unsupported / not_stated, and quote the span.",
            GROUNDED_SCHEMA, "grounded")

    def value_reported(self, reasoning, field_name):
        """Which value did the reasoning settle on, when two sources disagree?"""
        return self._ask(
            self._frame(reasoning) +
            f"\nQUESTION: does the reasoning state a value for {field_name}? If so, report "
            f"the value exactly as written in value_reported.\n"
            f"Answer yes / no / not_stated, and quote the span.",
            VALUE_SCHEMA, f"value_reported: {field_name}")

    def usage(self):
        return {"model": self.model, "effort": self.effort, "calls": self.calls,
                "input_tokens": self.in_tok, "output_tokens": self.out_tok}


# ---------------------------------------------------------------------------
# Self-test — known answers. The adapter must be reliable before anything else is.
# ---------------------------------------------------------------------------

_R_USES_NOTE = ("- [Production Kubernetes] (score 90) A call log dated 04 Aug 2026 states "
                "the candidate led a migration of 40 services onto EKS.")
_R_RESUME_ONLY = ("- [Production Kubernetes] (score 40) The resume lists Kubernetes as a "
                  "skill but shows no production usage.")
_R_ABSENCE = ("- [Spring Boot] (score 0) Neither the resume nor the notes provide any "
              "evidence of Spring Boot.")
_R_INVENTED = ("- [Leadership] (score 85) The candidate managed a team of twelve at "
               "Google, which is not mentioned anywhere in the material.")
_ACT = "[2026-08-04 09:00] Call log: led a migration of 40 services onto EKS."


def self_test(judge):
    print(f"judge self-test — {judge.model}, effort={judge.effort}, thinking disabled")
    print("(temperature is not settable on Sonnet 5 — a non-default value returns 400)\n")
    cases = [
        ("states the EKS migration", judge.fact_used(_R_USES_NOTE,
                                                     "the candidate led an EKS migration",
                                                     _ACT), "yes"),
        ("does not state it", judge.fact_used(_R_RESUME_ONLY,
                                              "the candidate led an EKS migration"),
         "not_stated"),
        ("attributes to the call log", judge.source_of(_R_USES_NOTE,
                                                       "the EKS migration", _ACT),
         "call_log"),
        ("attributes to the resume", judge.source_of(_R_RESUME_ONLY,
                                                     "the Kubernetes assessment"), "resume"),
        ("absence is not hallucination", judge.grounded(_R_ABSENCE, _ACT), "grounded"),
        ("invention is caught", judge.grounded(_R_INVENTED, _ACT), "unsupported"),
    ]
    ok = 0
    for name, v, want in cases:
        good = v.answer == want
        ok += good
        print(f"  {'PASS' if good else 'FAIL'}  {name:<32} got {v.answer!r} want {want!r}")
        if not good and v.error:
            print(f"        error: {v.error}")
    print(f"\n  {ok}/{len(cases)} correct")
    print(f"  {json.dumps(judge.usage())}")
    return ok == len(cases)


def calibrate(judge, labels_path):
    """
    Score the adapter against a hand-labelled file.

    Format: [{"reasoning": "...", "activity": "...", "question": "fact_used",
              "arg": "...", "expected": "yes"}]
    """
    labels = json.load(open(labels_path))
    agree = 0
    for row in labels:
        q = row["question"]
        if q == "fact_used":
            v = judge.fact_used(row["reasoning"], row["arg"], row.get("activity", ""))
        elif q == "source_of":
            v = judge.source_of(row["reasoning"], row["arg"], row.get("activity", ""))
        elif q == "grounded":
            v = judge.grounded(row["reasoning"], row.get("activity", ""))
        else:
            v = judge.value_reported(row["reasoning"], row["arg"])
        ok = v.answer == row["expected"]
        agree += ok
        if not ok:
            print(f"  MISMATCH {q}/{row.get('arg','')}: got {v.answer!r} "
                  f"want {row['expected']!r}")
    pct = 100 * agree / len(labels) if labels else 0
    print(f"\nagreement with human labels: {agree}/{len(labels)}  ({pct:.0f}%)")
    if pct < 90:
        print("  Below 90% — do not gate on this adapter until the prompt is fixed.")
    return pct


def main():
    p = argparse.ArgumentParser(description="Activity Context judge adapter")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--calibrate", metavar="LABELS_JSON")
    p.add_argument("--effort", default=JUDGE_EFFORT,
                   choices=["low", "medium", "high", "xhigh", "max"])
    a = p.parse_args()
    judge = Judge(effort=a.effort)
    if a.self_test:
        return 0 if self_test(judge) else 2
    if a.calibrate:
        return 0 if calibrate(judge, a.calibrate) >= 90 else 2
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

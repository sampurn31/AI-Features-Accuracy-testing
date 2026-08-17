#!/usr/bin/env python3
"""
The Judge — Claude Sonnet 5.

FLOW 1  grade a single AI2 analysis at CRITERION level: did the activity reach the
        criterion it bears on, and was that criterion scored correctly?
FLOW 2  compare the AI2 and DEV analyses of the same candidate and pick a winner.

DETERMINISM
-----------
`temperature` is NOT settable on Claude Sonnet 5 — a non-default value returns HTTP 400.
The levers that do exist and are used here:
  * thinking disabled  - adaptive thinking is ON by default on Sonnet 5 and is the main
                         source of run-to-run variance
  * effort "medium"    - this is judgement work, not classification
  * json_schema output - the shape is constrained, so only the content varies

Structured outputs do not support numeric minimum/maximum; an enum of the allowed values
is the supported way to bound an integer.

FLOW 2 BIAS CONTROL
-------------------
The two analyses are presented as "System A" and "System B" with the assignment RANDOMISED
per candidate, and the judge is never told which is which. Without that a judge favours
whichever it believes is the newer system, and activity context also makes answers longer,
which judges reward on its own. The caller maps the winner back afterwards.

Requires: pip install anthropic
"""

import json
import os
import sys
import time

import anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config as C  # noqa: E402

MODEL = "claude-sonnet-5"
EFFORT = "medium"
MAX_TOKENS = 2048
RETRIES = 3

SCORE_1_5 = {"type": "integer", "enum": [1, 2, 3, 4, 5]}
YES_NO = {"type": "string", "enum": ["Yes", "No"]}

FLOW1_SCHEMA = {
    "type": "object",
    "properties": {
        "judge_score": SCORE_1_5,
        "reasoning_factually_correct": YES_NO,
        "score_justified": YES_NO,
        "activity_context_used": YES_NO,
        "activity_criterion_scored_correctly": {"type": "string",
                                                "enum": ["Yes", "No", "Not applicable"]},
        "which_criterion": {"type": "string"},
        "activity_improved_reasoning": YES_NO,
        "activity_wrongly_influenced": YES_NO,
        "important_details_ignored": YES_NO,
        "contradictions_handled_correctly": {"type": "string",
                                             "enum": ["Yes", "No", "Not applicable"]},
        "analysis_verdict": {"type": "string",
                             "enum": ["Correct", "Partially correct", "Incorrect"]},
        "detailed_reasoning": {"type": "string"},
        "suggested_improvements": {"type": "string"},
    },
    "required": ["judge_score", "reasoning_factually_correct", "score_justified",
                 "activity_context_used", "activity_criterion_scored_correctly",
                 "which_criterion", "activity_improved_reasoning",
                 "activity_wrongly_influenced", "important_details_ignored",
                 "contradictions_handled_correctly", "analysis_verdict",
                 "detailed_reasoning", "suggested_improvements"],
    "additionalProperties": False,
}

FLOW2_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["System A", "System B", "Tie"]},
        "confidence": {"type": "string", "enum": ["Low", "Medium", "High"]},
        "comparison_reasoning": {"type": "string"},
        "a_references_activity": YES_NO,
        "b_references_activity": YES_NO,
        "activity_added_measurable_value": YES_NO,
        "better_explanations": {"type": "string",
                                "enum": ["System A", "System B", "Neither"]},
        "any_regression": YES_NO,
        "regression_detail": {"type": "string"},
        "incorrect_reasoning_introduced": YES_NO,
        "more_useful_for_recruiter": {"type": "string",
                                      "enum": ["System A", "System B", "Neither"]},
        "suggested_improvements": {"type": "string"},
    },
    "required": ["winner", "confidence", "comparison_reasoning", "a_references_activity",
                 "b_references_activity", "activity_added_measurable_value",
                 "better_explanations", "any_regression", "regression_detail",
                 "incorrect_reasoning_introduced", "more_useful_for_recruiter",
                 "suggested_improvements"],
    "additionalProperties": False,
}

FLOW1_SYSTEM = (
    "You are a senior recruitment lead auditing how an AI match analysis used a "
    "candidate's Notes and Call Logs.\n\n"
    "The analysis scores the candidate against a list of CRITERIA, each with its own "
    "weight, score and reason. You are judging the CRITERION-LEVEL work, not the overall "
    "number.\n\n"
    "IGNORE THE OVERALL SCORE ENTIRELY. It is a weighted sum across every criterion, so a "
    "single fact from the notes can only ever move it a little. Judging it would measure "
    "the aggregation formula, not whether the activity was used well. If the analysis "
    "scored the right criterion correctly, that is a success even when the overall number "
    "looks too high.\n\n"
    "Work out which criterion (or criteria) the activity is actually relevant to, then ask "
    "whether THAT criterion was reasoned and scored correctly.\n\n"
    "  5 - the relevant criterion cites the activity and its score reflects it: a blocker "
    "drove the score down, a revealed strength drove it up\n"
    "  4 - the criterion cites the activity and the score is broadly right\n"
    "  3 - the activity is mentioned but the criterion score barely reflects it\n"
    "  2 - a decisive fact in the activity is missing from every criterion it should have "
    "touched, or it is cited under a criterion it does not support\n"
    "  1 - a dealbreaker is ignored outright, or a soft signal (enthusiasm, rapport, "
    "communication) is used as evidence for a hard technical or qualification criterion\n\n"
    "WHEN THERE IS NO ACTIVITY (no notes, no call logs) there is nothing to use. Score the "
    "resume-only reasoning: 4-5 if the criteria are sensibly argued from the profile, 3 if "
    "something obvious is missed, 1-2 only if claims are actually wrong. Set "
    "activity_criterion_scored_correctly to 'Not applicable'.\n\n"
    "BE FAIR:\n"
    "  - Judge the criteria the activity bears on. Criteria it has nothing to do with are "
    "not your concern.\n"
    "  - Only call a claim factually incorrect if it contradicts the profile or the "
    "activity. A fair summary is correct.\n"
    "  - Reasonable inference is analysis, not invention.\n"
    "  - Dates cited are REAL and shown to you in brackets. A correct date is not a "
    "fabrication.\n"
    "  - Do not penalise brevity, wording, or the overall number.\n"
    "  - Partial credit counts. If the activity nudged the right criterion in the right "
    "direction, that is a 4 even if you would have moved it further.\n"
    "  - Do not require the analysis to restate the note verbatim; paraphrase is fine.\n"
    "  - When torn between two scores, give the higher one.\n\n"
    "Still unacceptable: a dealbreaker in the notes that no criterion reflects, and "
    "enthusiasm treated as proof of a technical skill."
)

FLOW2_SYSTEM = (
    "You are a senior recruitment lead comparing two AI match analyses of the SAME "
    "candidate for the SAME job. One system could see the candidate's notes and call "
    "logs; the other could not. You are not told which is which.\n\n"
    "You are answering ONE question: did seeing the activity produce a better answer for "
    "a recruiter?\n\n"
    "BE GENEROUS and be decisive about what counts:\n"
    "  - Prefer an analysis only for a REAL difference in usefulness - a constraint "
    "surfaced, a risk flagged, a score corrected. Wording, ordering, length and tone are "
    "not reasons to prefer one.\n"
    "  - Longer is not better. An analysis that mentions the notes but draws nothing "
    "actionable from them has added nothing.\n"
    "  - If both reach the same substantive conclusion on the same evidence, say Tie. Tie "
    "is a perfectly good answer and should be common.\n"
    "  - Dates either analysis cites are REAL and shown to you in brackets - a correct "
    "date citation is not an invention.\n"
    "  - Do not hunt for faults. Only report a regression or incorrect reasoning if it "
    "would actually mislead a recruiter.\n\n"
    "Judge only what each analysis says, on the material shown to you."
)


def _block(title, body):
    return f"===== {title} =====\n{body.strip() if body else '(none)'}\n"


def _activity_block(notes, calls):
    """
    Each item is prefixed with its timestamp in [brackets].

    The scoring prompt tells the model to name the source and date of every claim, so its
    reasoning cites things like "a call log dated 04 Aug 2026". Those dates are real and
    are shown here — do not let the judge read a correct citation as an invention.
    """
    out = ""
    if notes:
        out += ("NOTES — timestamp in brackets is updated_on, the field the system orders "
                "recency by. Oldest first.\n" + "\n".join(f"- {n}" for n in notes) + "\n")
    if calls:
        out += ("CALL LOGS — timestamp in brackets is updated_on. Oldest first.\n"
                + "\n".join(f"- {c}" for c in calls) + "\n")
    return out


def flow1_prompt(job_title, jd, resume, notes, calls, score, analysis):
    return (
        _block("JOB", f"{job_title}\n\n{jd}") +
        _block("CANDIDATE PROFILE / RESUME", resume) +
        _block("CANDIDATE NOTES AND CALL LOGS", _activity_block(notes, calls)) +
        _block("THE AI'S MATCH SCORE", str(score)) +
        _block("THE AI'S MATCH ANALYSIS", analysis) +
        "\nJudge how the activity was used, CRITERION BY CRITERION:\n"
        "  - Which criterion (or criteria) does the activity actually bear on? Name it in "
        "which_criterion. If none, say 'none'.\n"
        "  - Does that criterion's REASON cite the activity, or does it rest on the profile "
        "alone while something material sits unused in the notes?\n"
        "  - Is that criterion's SCORE right given the activity? A stated blocker should "
        "push it near zero; a revealed strength should raise it. Set "
        "activity_criterion_scored_correctly accordingly.\n"
        "  - Was a soft signal (enthusiasm, rapport, communication, availability) used as "
        "evidence for a hard technical or qualification criterion? That is wrong even if "
        "the score looks reasonable.\n"
        "  - Where sources conflict, was the most recent followed? Timestamps are shown.\n"
        "  - Was any decisive fact in the activity absent from every criterion?\n\n"
        "Do NOT comment on or penalise the overall match score.\n"
        "Set contradictions_handled_correctly to 'Not applicable' when nothing conflicts.\n"
        "Keep detailed_reasoning to three or four sentences, naming the criterion you "
        "judged. When torn, score higher."
    )


def flow2_prompt(job_title, jd, resume, notes, calls, a_score, a_text, b_score, b_text):
    return (
        _block("JOB", f"{job_title}\n\n{jd}") +
        _block("CANDIDATE PROFILE / RESUME", resume) +
        _block("CANDIDATE NOTES AND CALL LOGS ON FILE", _activity_block(notes, calls)) +
        _block(f"SYSTEM A - match score {a_score}", a_text) +
        _block(f"SYSTEM B - match score {b_score}", b_text) +
        "\nCompare the two analyses:\n"
        "  - Which is more useful to a recruiter deciding whether to progress this "
        "candidate, and why?\n"
        "  - Does either reference the notes or call logs above?\n"
        "  - If one does, did that add something a recruiter could act on - or just extra "
        "words?\n"
        "  - Is there a real regression: something the other one gets right that this one "
        "gets wrong or drops?\n"
        "  - Did either assert something the material above does not support?\n\n"
        "Set regression_detail to '(none)' when there is no regression.\n"
        "Keep comparison_reasoning to three or four sentences. Say Tie when the two are "
        "substantively equivalent."
    )


class Judge:
    def __init__(self, model=MODEL, effort=EFFORT):
        self.model, self.effort = model, effort
        key = C.anthropic_key()
        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.calls = self.in_tok = self.out_tok = 0

    def _ask(self, system, prompt, schema):
        last = ""
        for i in range(1, RETRIES + 1):
            try:
                r = self.client.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS, system=system,
                    thinking={"type": "disabled"},   # adaptive is ON by default on Sonnet 5
                    output_config={"effort": self.effort,
                                   "format": {"type": "json_schema", "schema": schema}},
                    messages=[{"role": "user", "content": prompt}])
                self.calls += 1
                self.in_tok += r.usage.input_tokens
                self.out_tok += r.usage.output_tokens
                return json.loads(next(b.text for b in r.content if b.type == "text"))
            except anthropic.APIStatusError as e:
                last = f"HTTP {e.status_code}: {str(e.message)[:160]}"
                if 400 <= e.status_code < 500 and e.status_code != 429:
                    print(f"    !! judge {last}")
                    return None
            except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
                last = type(e).__name__
            except (KeyError, ValueError, StopIteration) as e:
                last = f"bad judge output: {type(e).__name__}"
            if i < RETRIES:
                time.sleep(2 * i)
        print(f"    !! judge failed: {last}")
        return None

    def flow1(self, **kw):
        v = self._ask(FLOW1_SYSTEM, flow1_prompt(**kw), FLOW1_SCHEMA)
        if v and not kw.get("notes") and not kw.get("calls"):
            # Not a judgement call: with no notes and no call logs there is no activity to
            # use, improve on, or be misled by. The model gets this wrong often enough
            # that the headline metric cannot be left to it.
            v["activity_context_used"] = "No"
            v["activity_improved_reasoning"] = "No"
            v["activity_wrongly_influenced"] = "No"
            v["activity_criterion_scored_correctly"] = "Not applicable"
        return v

    def flow2(self, **kw):
        return self._ask(FLOW2_SYSTEM, flow2_prompt(**kw), FLOW2_SCHEMA)

    def usage(self):
        return {"judge_model": self.model, "judge_effort": self.effort,
                "judge_calls": self.calls, "input_tokens": self.in_tok,
                "output_tokens": self.out_tok}


# ---------------------------------------------------------------------------
# Self-test — known-answer cases. Run before trusting a report.
# ---------------------------------------------------------------------------

_JD = ("Senior Backend Engineer. Requires 6+ years Python and production Kubernetes. "
       "IMPORTANT: three days a week on site in Berlin.")
_CV = ("SENIOR BACKEND ENGINEER. 8 years Python, Kafka, PostgreSQL, production "
       "Kubernetes at Feedzai. M.Sc. Computer Engineering.")
_NOTES = ["[2026-08-04 09:00:00] Screening call: Sofia will NOT do the three days on "
          "site. Fully remote only. This is firm."]

_GOOD = ("- [6+ years Python] (weight 10, score 90)\n    Profile shows 8 years of Python.\n"
         "- [On-site in Berlin] (weight 9, score 10)\n    A note from the screening call "
         "states she will not do the three days on site, which conflicts with the "
         "requirement.\nOVERALL: Technically strong but the on-site constraint is "
         "disqualifying.")
_BAD = ("- [6+ years Python] (weight 10, score 90)\n    Profile shows 8 years of Python.\n"
        "- [On-site in Berlin] (weight 9, score 70)\n    Candidate is based in Lisbon, "
        "likely open to relocation.\nOVERALL: Strong fit, recommend progressing.")
_CLEAN = ("- [6+ years Python] (weight 10, score 95)\n    Profile shows 8 years of Python "
          "at Feedzai including production Kubernetes.\nOVERALL: Strong technical match.")


def self_test(judge):
    print(f"judge self-test — {judge.model}, effort={judge.effort}, thinking disabled")
    print("(temperature is not settable on Sonnet 5 — a non-default value returns 400)\n")
    ok = 0

    v = judge.flow1(job_title="Senior Backend Engineer", jd=_JD, resume=_CV, notes=_NOTES,
                    calls=[], score=45, analysis=_GOOD)
    good = v and v["judge_score"] >= 4 and v["activity_context_used"] == "Yes"
    ok += bool(good)
    print(f"  {'PASS' if good else 'FAIL'}  activity used well -> score "
          f"{v and v['judge_score']} (want >=4)")

    v = judge.flow1(job_title="Senior Backend Engineer", jd=_JD, resume=_CV, notes=_NOTES,
                    calls=[], score=88, analysis=_BAD)
    bad = v and v["judge_score"] <= 3 and v["important_details_ignored"] == "Yes"
    ok += bool(bad)
    print(f"  {'PASS' if bad else 'FAIL'}  ignores the blocker -> score "
          f"{v and v['judge_score']} (want <=3)")

    v = judge.flow1(job_title="Senior Backend Engineer", jd=_JD, resume=_CV, notes=[],
                    calls=[], score=90, analysis=_CLEAN)
    na = v and v["activity_context_used"] == "No" and v["judge_score"] >= 4
    ok += bool(na)
    print(f"  {'PASS' if na else 'FAIL'}  no activity, sound analysis -> score "
          f"{v and v['judge_score']} (want >=4)")

    v = judge.flow2(job_title="Senior Backend Engineer", jd=_JD, resume=_CV, notes=_NOTES,
                    calls=[], a_score=45, a_text=_GOOD, b_score=88, b_text=_BAD)
    w = v and v["winner"] == "System A"
    ok += bool(w)
    print(f"  {'PASS' if w else 'FAIL'}  A better -> winner={v and v['winner']}")

    v = judge.flow2(job_title="Senior Backend Engineer", jd=_JD, resume=_CV, notes=_NOTES,
                    calls=[], a_score=88, a_text=_BAD, b_score=45, b_text=_GOOD)
    w2 = v and v["winner"] == "System B"
    ok += bool(w2)
    print(f"  {'PASS' if w2 else 'FAIL'}  order swapped -> winner={v and v['winner']} "
          f"(position bias check)")

    v = judge.flow2(job_title="Senior Backend Engineer", jd=_JD, resume=_CV, notes=_NOTES,
                    calls=[], a_score=45, a_text=_GOOD, b_score=45, b_text=_GOOD)
    t = v and v["winner"] == "Tie"
    ok += bool(t)
    print(f"  {'PASS' if t else 'FAIL'}  identical inputs -> winner={v and v['winner']}")

    v = judge.flow1(job_title="Senior Backend Engineer", jd=_JD, resume=_CV,
                    notes=["[2026-08-04 09:00:00] Lovely person, everyone liked her, very "
                           "keen. No technical assessment was done."],
                    calls=[], score=92,
                    analysis="- [6+ years Python] (weight 10, score 95)\n    Notes confirm "
                             "she is impressive and highly capable.\nOVERALL: Outstanding.")
    mis = v and v["activity_wrongly_influenced"] == "Yes" and v["judge_score"] <= 2
    ok += bool(mis)
    print(f"  {'PASS' if mis else 'FAIL'}  enthusiasm as technical evidence -> score "
          f"{v and v['judge_score']} (want <=2)")

    print(f"\n  {ok}/7 correct")
    print(f"  {json.dumps(judge.usage())}")
    return ok == 7


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Judge self-test")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--effort", default=EFFORT,
                    choices=["low", "medium", "high", "xhigh", "max"])
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if self_test(Judge(effort=a.effort)) else 2)
    ap.print_help()

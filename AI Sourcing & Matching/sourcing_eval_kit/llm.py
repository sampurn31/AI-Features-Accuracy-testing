"""
Anthropic calls: the reference-generation leg (Output B) and the judge.

Both use Sonnet 5. The Anthropic SDK reads ANTHROPIC_API_KEY from the env; we
never touch the key value here. The shared system prompt / rubric is sent with
cache_control so repeated calls in a batch pay cache-read (~0.1x) on the prefix.
"""
import json

from anthropic import Anthropic

import config

_client = Anthropic()  # reads ANTHROPIC_API_KEY


def _system(text):
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _text_of(resp):
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def reference_configuration(neptune_system_prompt, jd_or_query):
    """
    Output B: run the REAL Neptune system prompt through Sonnet 5 on the same
    JD/query the current implementation received. Same prompt, better model.
    """
    resp = _client.messages.create(
        model=config.MODEL,
        max_tokens=config.REFERENCE_MAX_TOKENS,
        output_config={"effort": config.REFERENCE_EFFORT},
        system=_system(neptune_system_prompt),
        messages=[{"role": "user", "content": jd_or_query}],
    )
    return _text_of(resp)


def reference_criteria_to_filters(system_prompt, jd, criteria):
    """
    Output B for the criteria->filters step. Builds the EXACT user message Neptune
    sends (service.py: "Criteria:\\n- <text> (weight: N)\\n...\\n\\nOriginal job
    description/query, for context:\\n<jd>") so it's same-prompt / different-model.
    """
    lines = "\n".join(f"- {c.get('text')} (weight: {c.get('weight')})" for c in (criteria or []))
    user = f"Criteria:\n{lines}"
    if jd:
        user += f"\n\nOriginal job description/query, for context:\n{jd}"
    resp = _client.messages.create(
        model=config.MODEL,
        max_tokens=config.REFERENCE_MAX_TOKENS,
        output_config={"effort": config.REFERENCE_EFFORT},
        system=_system(system_prompt),
        messages=[{"role": "user", "content": user}],
    )
    return _text_of(resp)


# The judge returns a small, strict JSON verdict so results tabulate cleanly.
JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["current_better", "reference_better", "equivalent", "both_poor"],
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "current_issues": {"type": "string", "description": "concrete problems in Output A, or \"\" if none"},
        "reference_issues": {"type": "string", "description": "concrete problems in Output B, or \"\" if none"},
        "reasoning": {"type": "string"},
        "expectation_met": {"type": "string", "enum": ["yes", "no", "not_applicable"]},
    },
    # All declared properties are required (strict json_schema decoding is happiest
    # this way); the judge returns "" for current_issues/reference_issues when none.
    "required": ["verdict", "confidence", "current_issues", "reference_issues",
                 "reasoning", "expectation_met"],
}


def judge(rubric, jd_or_query, current_output, reference_output, expectation=None):
    """
    Compares Output A (current) vs Output B (reference) against the rubric and
    the JD. Returns the parsed JSON verdict. `expectation` (optional) states the
    correct interpretation for disambiguation cases so the judge can flag misses.
    """
    parts = [
        "JOB DESCRIPTION / QUERY:\n" + jd_or_query,
        "\n\nOUTPUT A — CURRENT IMPLEMENTATION:\n" + json.dumps(current_output, indent=2, ensure_ascii=False),
        "\n\nOUTPUT B — REFERENCE (Sonnet 5, same prompt):\n" + reference_output,
    ]
    if expectation:
        parts.append("\n\nEXPECTED INTERPRETATION (for disambiguation): " + expectation)
    parts.append(
        "\n\nGrade per the rubric. Return JSON only. In `reasoning`, be specific and "
        "cite the concrete difference — this is read by a human reviewer to validate you."
    )

    resp = _client.messages.create(
        model=config.MODEL,
        max_tokens=config.JUDGE_MAX_TOKENS,
        output_config={"effort": config.JUDGE_EFFORT,
                       "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        system=_system(rubric),
        messages=[{"role": "user", "content": "".join(parts)}],
    )
    raw = _text_of(resp)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"verdict": "both_poor", "confidence": "low",
                "reasoning": "Judge did not return valid JSON: " + raw[:500],
                "expectation_met": "not_applicable"}


INFLUENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "influence": {"type": "string", "enum": ["correct", "partial", "wrong", "no_change"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "completed": {"type": "string", "enum": ["yes", "no"]},
        "reasoning": {"type": "string"},
        "issues": {"type": "string"},
    },
    "required": ["influence", "confidence", "completed", "reasoning", "issues"],
}


def judge_influence(rubric, jd, qa_text, before, after):
    """Grade whether AFTER (config with answers) correctly incorporated the answers vs BEFORE."""
    parts = [
        "JOB/QUERY:\n" + jd,
        "\n\nCLARIFYING Q&A (what was asked, and the answer supplied):\n" + qa_text,
        "\n\nBEFORE (config with NO answers):\n" + json.dumps(before, indent=2, ensure_ascii=False)[:4500],
        "\n\nAFTER (config once answers supplied):\n" + json.dumps(after, indent=2, ensure_ascii=False)[:4500],
        "\n\nGrade per the rubric. Return JSON only; cite the specific answer and the field in AFTER that changed.",
    ]
    resp = _client.messages.create(
        model=config.MODEL,
        max_tokens=config.JUDGE_MAX_TOKENS,
        output_config={"effort": config.JUDGE_EFFORT,
                       "format": {"type": "json_schema", "schema": INFLUENCE_SCHEMA}},
        system=_system(rubric),
        messages=[{"role": "user", "content": "".join(parts)}],
    )
    raw = _text_of(resp)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"influence": "wrong", "confidence": "low", "completed": "no",
                "reasoning": "Judge did not return valid JSON: " + raw[:400], "issues": ""}


# --- Day 2: AI Sourcing V3 scored-candidate judge -----------------------------

SOURCING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # Is each per-criterion `reasoning` actually supported by the candidate profile?
        "grounding": {"type": "string", "enum": ["grounded", "partly_grounded", "hallucinated"]},
        # Are the per-criterion 0-100 `score`s justified by the evidence?
        "scores_justified": {"type": "string", "enum": ["correct", "partly", "wrong"]},
        # Is matchScore a sensible overall fit for this profile given query + criteria?
        "score_sensible": {"type": "string", "enum": ["yes", "borderline", "no"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reasoning": {"type": "string"},
        "issues": {"type": "string", "description": "concrete problems, or \"\" if none"},
    },
    "required": ["grounding", "scores_justified", "score_sensible",
                 "confidence", "reasoning", "issues"],
}


def _profile_for_judge(candidate):
    """Full evidence the judge may rely on — ALL experience entries (never truncate a role
    away, or the judge falsely flags genuinely-grounded reasoning as hallucinated)."""
    exp = []
    for e in (candidate.get("experience") or []):
        exp.append({"title": e.get("title"), "company": e.get("company"),
                    "start": e.get("start_date"), "end": e.get("end_date"),
                    "is_current": e.get("is_current"),
                    "description": (e.get("description") or "")[:500],
                    "location": e.get("location")})
    return {"position": candidate.get("position"), "company": candidate.get("company"),
            "workexpyr": candidate.get("workexpyr"), "city": candidate.get("city"),
            "state": candidate.get("state"), "country": candidate.get("country"),
            "keySkills": candidate.get("keySkills"), "description": candidate.get("description"),
            "education": candidate.get("education"), "experience": exp}


def judge_sourcing(rubric, query, criteria, candidate):
    """
    Grade ONE scored candidate: is the scoring output faithful to the profile?
    `candidate` is the slim dict from sourcing_client (profile fields + matchScore /
    matchLabel / matchAnalysis, where each row has a 0-100 `score`). Returns the verdict.
    """
    crit_lines = "\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in (criteria or []))
    parts = [
        "SEARCH QUERY:\n" + (query or ""),
        "\n\nWEIGHTED CRITERIA:\n" + crit_lines,
        "\n\nCANDIDATE PROFILE (the ONLY evidence the scoring may rely on — read the FULL "
        "experience list):\n"
        + json.dumps(_profile_for_judge(candidate), indent=2, ensure_ascii=False)[:16000],
        "\n\nSYSTEM'S SCORING OUTPUT (per-criterion `score` is 0-100; overall matchScore is "
        "their weighted average):\n"
        + json.dumps({"matchScore": candidate.get("matchScore"),
                      "matchLabel": candidate.get("matchLabel"),
                      "matchAnalysis": candidate.get("matchAnalysis")},
                     indent=2, ensure_ascii=False)[:6000],
        "\n\nGrade per the rubric. Return JSON only. In `reasoning`, cite the specific "
        "criterion + profile evidence — a human reviewer reads this to validate you. Only "
        "call reasoning 'hallucinated' if it asserts something the profile does NOT support "
        "(check the FULL experience list before deciding).",
    ]
    resp = _client.messages.create(
        model=config.MODEL,
        max_tokens=config.JUDGE_MAX_TOKENS,
        output_config={"effort": config.JUDGE_EFFORT,
                       "format": {"type": "json_schema", "schema": SOURCING_SCHEMA}},
        system=_system(rubric),
        messages=[{"role": "user", "content": "".join(parts)}],
    )
    raw = _text_of(resp)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"grounding": "hallucinated", "scores_justified": "wrong", "score_sensible": "no",
                "confidence": "low", "reasoning": "Judge did not return valid JSON: " + raw[:400],
                "issues": "judge-parse-error"}


# --- Scale run: PER-CRITERION judge (one verdict per matchAnalysis row) --------

CRITERION_JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "criterion": {"type": "string"},
                    "reasoning_verdict": {"type": "string", "enum": ["correct", "partly", "wrong"]},
                    "reasoning_note": {"type": "string"},
                    # NOTE: no minimum/maximum — Anthropic structured-output rejects them on
                    # integers (400 "properties maximum, minimum are not supported"); the 0-100
                    # range is enforced by the rubric text and clamped by the caller.
                    "judge_score": {"type": "integer"},
                    "score_verdict": {"type": "string", "enum": ["agree", "too_high", "too_low"]},
                },
                "required": ["criterion", "reasoning_verdict", "reasoning_note",
                             "judge_score", "score_verdict"],
            },
        },
        "overall_sensible": {"type": "string", "enum": ["yes", "borderline", "no"]},
        "summary": {"type": "string"},
    },
    "required": ["criteria", "overall_sensible", "summary"],
}


def judge_candidate_criteria(rubric, query, criteria, candidate):
    """
    Audit ONE scored candidate at per-criterion depth: for EACH matchAnalysis row the
    judge verifies the reasoning against the FULL profile and re-scores the criterion
    itself (judge_score) so we can measure agreement with the system's score.
    Returns the parsed verdict dict; on failure returns {"_error": "..."} so the caller
    can record it without fabricating verdicts.
    """
    crit_lines = "\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in (criteria or []))
    sys_rows = [{"criterion": a.get("criterionText"), "weight": a.get("weight"),
                 "score": a.get("score"), "reasoning": a.get("reasoning")}
                for a in (candidate.get("matchAnalysis") or [])]
    parts = [
        "SEARCH QUERY:\n" + (query or ""),
        "\n\nWEIGHTED CRITERIA (as configured):\n" + crit_lines,
        "\n\nCANDIDATE PROFILE (the ONLY evidence — read the FULL experience list):\n"
        + json.dumps(_profile_for_judge(candidate), indent=2, ensure_ascii=False)[:16000],
        "\n\nSYSTEM'S PER-CRITERION OUTPUT (audit every row, in order; overall matchScore="
        + str(candidate.get("matchScore")) + "):\n"
        + json.dumps(sys_rows, indent=2, ensure_ascii=False)[:6000],
        "\n\nAudit per the rubric: one entry per system row, same order. Cite concrete profile "
        "evidence in every reasoning_note. Return JSON only.",
    ]
    try:
        resp = _client.messages.create(
            model=config.MODEL,
            max_tokens=2000,
            output_config={"effort": config.JUDGE_EFFORT,
                           "format": {"type": "json_schema", "schema": CRITERION_JUDGE_SCHEMA}},
            system=_system(rubric),
            messages=[{"role": "user", "content": "".join(parts)}],
        )
        return json.loads(_text_of(resp))
    except Exception as e:  # noqa: BLE001 — recorded, never fabricated
        return {"_error": f"{type(e).__name__}: {e}"}


# --- db=1 axis 2: per-candidate RELEVANCE judge -------------------------------

RELEVANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevance_verdict": {"type": "string", "enum": ["relevant", "borderline", "irrelevant"]},
        "relevant_dimensions": {"type": "array", "items": {"type": "string"}},
        "disqualifiers": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["relevance_verdict", "relevant_dimensions", "disqualifiers", "reasoning"],
}


def judge_candidate_relevance(rubric, job_title, jd_text, criteria, candidate):
    """
    Axis 2: is this candidate genuinely RELEVANT to the job/search at all? Independent of the
    system's scores — the judge sees the job + criteria + full profile only. Returns the parsed
    verdict, or {"_error": ...} on failure (recorded, never fabricated).
    """
    crit_lines = "\n".join(f"- {c.get('text') or c.get('criterionText')} (weight {c.get('weight')})"
                           for c in (criteria or []))
    parts = [
        "JOB TITLE: " + (job_title or ""),
        "\n\nJOB DESCRIPTION:\n" + (jd_text or "(none)")[:4000],
        "\n\nWEIGHTED MATCHING CRITERIA:\n" + crit_lines,
        "\n\nCANDIDATE PROFILE (the ONLY evidence — read the FULL work history):\n"
        + json.dumps(_profile_for_judge(candidate), indent=2, ensure_ascii=False)[:12000],
        "\n\nDecide per the rubric whether this candidate is genuinely relevant to THIS job. "
        "Ignore any system score. Cite concrete profile evidence in `reasoning`. Return JSON only.",
    ]
    try:
        resp = _client.messages.create(
            model=config.MODEL,
            max_tokens=config.JUDGE_MAX_TOKENS,
            output_config={"effort": config.JUDGE_EFFORT,
                           "format": {"type": "json_schema", "schema": RELEVANCE_SCHEMA}},
            system=_system(rubric),
            messages=[{"role": "user", "content": "".join(parts)}],
        )
        return json.loads(_text_of(resp))
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


# --- A/B comparative judge: which of two scorings of the SAME candidate is better -----

COMPARE_SCORING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["A_better", "B_better", "equivalent"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "key_differences": {"type": "string", "description": "concrete differences that drove the verdict"},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "confidence", "key_differences", "reasoning"],
}


def judge_compare_scoring(rubric, query, criteria, profile, scoring_a, scoring_b):
    """
    BLIND head-to-head: given the candidate's objective profile + the criteria, decide whether
    Scoring A or Scoring B is the more ACCURATE / better-justified scoring (or equivalent). The
    caller randomizes which run maps to A vs B to remove order bias. Returns the parsed verdict
    or {"_error": ...}. Each scoring is {matchScore, matchAnalysis:[{criterionText,weight,score,reasoning}]}.
    """
    crit_lines = "\n".join(f"- {c.get('text') or c.get('criterionText')} (weight {c.get('weight')})"
                           for c in (criteria or []))

    def _fmt(s):
        return json.dumps({"matchScore": s.get("matchScore"),
                           "perCriterion": [{"criterion": a.get("criterionText"), "weight": a.get("weight"),
                                             "score": a.get("score"), "reasoning": a.get("reasoning")}
                                            for a in (s.get("matchAnalysis") or [])]},
                          indent=2, ensure_ascii=False)[:4000]

    parts = [
        "SEARCH QUERY: " + (query or ""),
        "\n\nWEIGHTED CRITERIA:\n" + crit_lines,
        "\n\nCANDIDATE PROFILE (the objective evidence — the SAME candidate scored two ways):\n"
        + json.dumps(profile, indent=2, ensure_ascii=False)[:6000],
        "\n\nSCORING A:\n" + _fmt(scoring_a),
        "\n\nSCORING B:\n" + _fmt(scoring_b),
        "\n\nPer the rubric, decide which scoring is more accurate and better justified by the "
        "profile evidence (or 'equivalent'). Judge grounding + calibration, NOT which score is "
        "higher. You do not know which system produced A vs B. Return JSON only; cite the "
        "specific criterion + evidence in `key_differences`.",
    ]
    try:
        resp = _client.messages.create(
            model=config.MODEL,
            max_tokens=config.JUDGE_MAX_TOKENS,
            output_config={"effort": config.JUDGE_EFFORT,
                           "format": {"type": "json_schema", "schema": COMPARE_SCORING_SCHEMA}},
            system=_system(rubric),
            messages=[{"role": "user", "content": "".join(parts)}],
        )
        return json.loads(_text_of(resp))
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


# ── AI Sourcing candidate EVALUATION (independent score + API verdict + relevance) ──
SOURCING_EVAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "llm_overall_score": {"type": "integer"},          # Sonnet's own 0-100 overall fit
        "per_criterion": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "criterion": {"type": "string"},
                "llm_score": {"type": "integer"},           # Sonnet's own 0-100 for this criterion
                "reasoning": {"type": "string"},
                "api_verdict": {"type": "string", "enum": ["agree", "api_too_high", "api_too_low"]},
            },
            "required": ["criterion", "llm_score", "reasoning", "api_verdict"]}},
        "api_overall_good_enough": {"type": "string", "enum": ["yes", "borderline", "no"]},
        "api_overall_note": {"type": "string"},
        "relevant": {"type": "string", "enum": ["yes", "partial", "no"]},
        "relevance_reason": {"type": "string"},
    },
    "required": ["llm_overall_score", "per_criterion", "api_overall_good_enough",
                 "api_overall_note", "relevant", "relevance_reason"],
}


def _clamp(v, lo=0, hi=100):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return v


def judge_sourcing_eval(rubric, query, criteria, candidate):
    """
    One Sonnet-5 call per candidate that returns EVERYTHING the report needs:
      - the model's OWN overall + per-criterion scores (independent re-scoring),
      - a per-criterion verdict on the API score (agree / api_too_high / api_too_low),
      - whether the API's overall score is 'good enough' (yes/borderline/no + note),
      - whether the candidate is RELEVANT to the query (yes/partial/no + reason).
    `candidate` = slim dict (profile + matchScore + matchAnalysis). Returns parsed dict
    (scores clamped 0-100) or {"_error": ...}.
    """
    crit_lines = "\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in (criteria or []))
    api = {"matchScore": candidate.get("matchScore"), "matchLabel": candidate.get("matchLabel"),
           "perCriterion": [{"criterion": a.get("criterionText"), "weight": a.get("weight"),
                             "score": a.get("score"), "reasoning": a.get("reasoning")}
                            for a in (candidate.get("matchAnalysis") or [])]}
    parts = [
        "SEARCH QUERY:\n" + (query or ""),
        "\n\nWEIGHTED CRITERIA:\n" + crit_lines,
        "\n\nCANDIDATE PROFILE (the ONLY evidence — read the FULL experience list):\n"
        + json.dumps(_profile_for_judge(candidate), indent=2, ensure_ascii=False)[:16000],
        "\n\nOUR API's SCORING (per-criterion `score` 0-100; overall matchScore = their weighted avg):\n"
        + json.dumps(api, indent=2, ensure_ascii=False)[:6000],
        "\n\nDo three things per the rubric: (1) score this candidate yourself — overall 0-100 and "
        "each criterion 0-100 — grounded ONLY in the profile; (2) judge whether OUR API's scores are "
        "good enough (per criterion: agree / api_too_high / api_too_low; overall: yes/borderline/no); "
        "(3) decide if the candidate is RELEVANT to the search query (yes/partial/no) and why. Return "
        "JSON only; cite specific profile evidence.",
    ]
    try:
        resp = _client.messages.create(
            model=config.MODEL, max_tokens=config.JUDGE_MAX_TOKENS,
            output_config={"effort": config.JUDGE_EFFORT,
                           "format": {"type": "json_schema", "schema": SOURCING_EVAL_SCHEMA}},
            system=_system(rubric),
            messages=[{"role": "user", "content": "".join(parts)}],
        )
        v = json.loads(_text_of(resp))
        v["llm_overall_score"] = _clamp(v.get("llm_overall_score"))
        for c in v.get("per_criterion") or []:
            c["llm_score"] = _clamp(c.get("llm_score"))
        return v
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


DERIVE_CRITERIA_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"criteria": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"text": {"type": "string"}, "weight": {"type": "integer"}},
        "required": ["text", "weight"]}}},
    "required": ["criteria"],
}


def derive_criteria(query):
    """FALLBACK criteria generation (used only when the product's config endpoint is unavailable):
    Sonnet 5 extracts 3-6 weighted matching criteria (weight 1-10) from a search query.
    Returns [{"text","weight"}] or [] on error."""
    sys = ("You convert a recruiter search query into 3-6 weighted matching criteria for candidate "
           "scoring. Each criterion is a short requirement (role/skill/domain/location/experience) "
           "with weight 1 (nice-to-have) to 10 (must-have). Cover the query's core intent. JSON only.")
    try:
        resp = _client.messages.create(
            model=config.MODEL, max_tokens=600,
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": DERIVE_CRITERIA_SCHEMA}},
            system=_system(sys),
            messages=[{"role": "user", "content": "SEARCH QUERY:\n" + (query or "")}],
        )
        out = json.loads(_text_of(resp)).get("criteria") or []
        return [{"text": c.get("text"), "weight": _clamp(c.get("weight"), 1, 10)} for c in out if c.get("text")]
    except Exception:  # noqa: BLE001
        return []

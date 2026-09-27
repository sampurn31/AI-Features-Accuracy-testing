"""
LLM adjudication for the end-to-end suite — the judgement calls a deterministic
check cannot make.

Six judges, one per hop of the chain:
  questions   are the clarifying questions material, and do they ask about things
              the query already stated?
  criteria    do the criteria faithfully represent the query — complete, nothing invented?
  filters     is every filter value in the RIGHT FIELD, and does the filter set
              over- or under-constrain the search?
  answers     did supplying an answer change criteria/filters the way it should?
  mutation    is the filter delta after editing a criterion correct and proportionate?
  candidate   should this person have come up at all, and is the stated reasoning
              grounded in their actual profile?

Every judge returns strict JSON. Verdicts are advisory — read the reasoning
alongside the deterministic result before filing anything.
"""
import json

from anthropic import Anthropic

import config

_client = Anthropic()

_EFFORT = "low"
_MAXTOK = 1400


def _system(text):
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _call(system, user, schema, max_tokens=_MAXTOK, effort=_EFFORT):
    try:
        resp = _client.messages.create(
            model=config.MODEL, max_tokens=max_tokens,
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": schema}},
            system=_system(system), messages=[{"role": "user", "content": user}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001 - judge must never kill a run
        return {"_judge_error": f"{type(e).__name__}: {str(e)[:200]}"}


def _schema(props, required=None):
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": required or list(props)}


_VERDICT3 = {"type": "string", "enum": ["good", "borderline", "bad"]}
_YN = {"type": "string", "enum": ["yes", "no"]}

# ── 1. clarifying questions ──────────────────────────────────────────────────
Q_SYS = """You audit the clarifying questions an AI recruiting-search product asks after a
user types a search query or pastes a job description.

A question is GOOD only if answering it would materially change which candidates are
retrieved, and the query does not already state the answer. Judge against these rules:
- Asking about something the query already states is a defect (redundant).
- Asking about something that cannot affect retrieval is a defect (immaterial).
- Asking many questions for a short, clear query is friction: prefer completing.
- Failing to ask about a genuinely load-bearing ambiguity is also a defect (missed).
Return JSON only."""

Q_SCHEMA = _schema({
    "verdict": _VERDICT3,
    "redundant": {"type": "array", "items": {"type": "string"},
                  "description": "questions whose answer the query already gives, verbatim"},
    "immaterial": {"type": "array", "items": {"type": "string"},
                   "description": "questions that cannot change retrieval"},
    "missed": {"type": "array", "items": {"type": "string"},
               "description": "load-bearing ambiguities that should have been asked about"},
    "over_asking": _YN,
    "reasoning": {"type": "string"},
})


def judge_questions(query, questions):
    qs = "\n".join(f"- {q}" for q in questions) or "(none asked)"
    return _call(Q_SYS, f"USER QUERY:\n{query}\n\nCLARIFYING QUESTIONS ASKED:\n{qs}", Q_SCHEMA)


# ── 2. criteria ──────────────────────────────────────────────────────────────
C_SYS = """You audit the weighted matching criteria an AI recruiting-search product derives
from a user's query or job description. Criteria drive both filtering and scoring.

Defects to find:
- MISSING: a requirement stated in the query that no criterion covers.
- INVENTED: a criterion asserting a requirement the query never stated.
- MISWEIGHTED: an explicitly essential requirement given a low weight, or an
  incidental "nice to have" given a high weight. Weights run 1-10; >= 7 is treated
  as a hard must-have downstream, so a "bonus"/"a plus" item weighted >= 7 is a defect.
- THIN: too few criteria to represent a substantive brief.
Return JSON only."""

C_SCHEMA = _schema({
    "verdict": _VERDICT3,
    "missing": {"type": "array", "items": {"type": "string"}},
    "invented": {"type": "array", "items": {"type": "string"}},
    "misweighted": {"type": "array", "items": {"type": "string"}},
    "coverage_pct": {"type": "integer", "description": "0-100: share of stated requirements covered"},
    "reasoning": {"type": "string"},
})


def judge_criteria(query, criteria):
    cs = "\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in criteria) or "(none)"
    return _call(C_SYS, f"USER QUERY:\n{query}\n\nGENERATED CRITERIA:\n{cs}", C_SCHEMA)


# ── 3. filters ───────────────────────────────────────────────────────────────
F_SYS = """You audit the structured search filters an AI recruiting product generates from a
query and its weighted criteria. These filters are executed against a candidate index,
so a wrong field or an over-tight constraint returns the wrong people — or nobody.

Field semantics:
- job_titles: the candidate's role. skills: hard, resume-listed technical skills only.
- keywords: qualitative or descriptive requirements ("startup experience", "strong
  communicator") — these must NOT be in skills, because skills are matched strictly.
- locations: where the CANDIDATE is. companies: employers the CANDIDATE must have
  worked at — NEVER the company doing the hiring.
- languages: spoken languages, never programming languages.
- education / universities / graduation_year: academic constraints.
- excluded_keywords: terms that must DISQUALIFY a candidate.

Defects to find: value in the wrong field; a qualitative phrase used as a hard skill;
the hiring company treated as a candidate employer; a "nice to have" promoted to a hard
requirement; a stated requirement missing from every field; a constraint the user typed
that no field expresses (silently dropped); over-constraint likely to return zero results.
Return JSON only."""

F_SCHEMA = _schema({
    "verdict": _VERDICT3,
    "wrong_field": {"type": "array", "items": {"type": "string"},
                    "description": "each as 'value: is in X, belongs in Y'"},
    "over_constrained": _YN,
    "over_constraint_detail": {"type": "string"},
    "dropped_requirements": {"type": "array", "items": {"type": "string"}},
    "invented_values": {"type": "array", "items": {"type": "string"}},
    "reasoning": {"type": "string"},
})


def judge_filters(query, criteria, filters):
    cs = "\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in (criteria or [])) or "(none)"
    return _call(F_SYS,
                 f"USER QUERY:\n{query}\n\nCRITERIA:\n{cs}\n\nGENERATED FILTERS:\n"
                 + json.dumps(filters, ensure_ascii=False, indent=1)[:6000], F_SCHEMA)


# ── 4. answer influence ──────────────────────────────────────────────────────
A_SYS = """You audit whether answering a clarifying question correctly changed an AI recruiting
product's generated criteria and filters.

Correct behaviour: the answer's content appears in the RIGHT field (a seniority answer
should move years-of-experience or titles; a location answer should move locations; a
work-arrangement answer such as "Remote" belongs in locations, not loose keywords), the
run completes rather than asking again, and unrelated parts of the config stay stable.

Defects: answer had no effect at all; answer landed in a loosely-related field; the
answer changed things it had no business changing.
Return JSON only."""

A_SCHEMA = _schema({
    "influence": {"type": "string", "enum": ["correct", "partial", "wrong", "no_change"]},
    "landed_in": {"type": "string", "description": "field(s) the answer actually affected"},
    "expected_in": {"type": "string"},
    "unrelated_changes": {"type": "array", "items": {"type": "string"}},
    "reasoning": {"type": "string"},
})


def judge_answer_influence(query, qa_pairs, before, after):
    qa = "\n".join(f"Q: {p.get('question')}\nA: {p.get('answer')}" for p in qa_pairs)
    body = (f"USER QUERY:\n{query}\n\nQUESTION(S) ANSWERED:\n{qa}\n\n"
            f"BEFORE (criteria + filters):\n{json.dumps(before, ensure_ascii=False)[:3500]}\n\n"
            f"AFTER:\n{json.dumps(after, ensure_ascii=False)[:3500]}")
    return _call(A_SYS, body, A_SCHEMA)


# ── 5. mutation ──────────────────────────────────────────────────────────────
M_SYS = """You audit whether editing the matching criteria produced the RIGHT change in the
generated search filters. A user removed a criterion, changed a criterion's weight, or
rewrote a criterion's text, and the product regenerated the filters.

Correct behaviour is LOCAL and PREDICTABLE:
- Removing a criterion removes the filter values that existed only because of it, and
  leaves every other filter untouched.
- Raising a weight to 7 or above promotes that requirement to a hard must-have;
  lowering it below 7 demotes it to nice-to-have.
- Rewriting a criterion's text swaps that criterion's values, nothing else.
Defects: no change at all; the edit changed unrelated filters (unpredictable); the
change went in the wrong direction; values that should have gone away are still there.
Return JSON only."""

M_SCHEMA = _schema({
    "verdict": _VERDICT3,
    "change_correct": _YN,
    "unrelated_changed": {"type": "array", "items": {"type": "string"}},
    "expected_change": {"type": "string"},
    "actual_change": {"type": "string"},
    "reasoning": {"type": "string"},
})


def judge_mutation(query, mutation_desc, base_criteria, base_filters, new_criteria, new_filters):
    def cs(c):
        return "\n".join(f"- {x.get('text')} (w{x.get('weight')})" for x in (c or []))
    body = (f"USER QUERY:\n{query}\n\nEDIT MADE: {mutation_desc}\n\n"
            f"BEFORE CRITERIA:\n{cs(base_criteria)}\n\nBEFORE FILTERS:\n"
            f"{json.dumps(base_filters, ensure_ascii=False)[:3000]}\n\n"
            f"AFTER CRITERIA:\n{cs(new_criteria)}\n\nAFTER FILTERS:\n"
            f"{json.dumps(new_filters, ensure_ascii=False)[:3000]}")
    return _call(M_SYS, body, M_SCHEMA)


# ── 6. candidate ─────────────────────────────────────────────────────────────
R_SYS = """You audit a single candidate returned by an AI recruiting search.

Two independent questions:
1. RELEVANCE — given only the user's query, should this person be in the result set at
   all? Judge the profile on its merits: role, seniority, skills, location. "Borderline"
   means a recruiter would tolerate seeing them; "irrelevant" means seeing them erodes
   trust in the product.
2. GROUNDING — if a match score and per-criterion reasoning are supplied, is that
   reasoning supported by the profile? Flag reasoning that cites a role, employer or
   skill the profile does not contain (fabrication), and reasoning that leans on a
   self-reported summary line contradicted by the actual work history.
Judge only what the profile shows. Absence of evidence is weak evidence, not strong.
Return JSON only."""

R_SCHEMA = _schema({
    "relevance": {"type": "string", "enum": ["relevant", "borderline", "irrelevant"]},
    "should_appear": _YN,
    "mismatch_reasons": {"type": "array", "items": {"type": "string"},
                         "description": "concrete reasons this profile does not fit, if any"},
    "grounding": {"type": "string", "enum": ["grounded", "partly", "fabricated", "not_applicable"]},
    "fabricated_claims": {"type": "array", "items": {"type": "string"}},
    "judge_score": {"type": "integer", "description": "0-100, your own overall fit score"},
    "score_verdict": {"type": "string", "enum": ["agree", "too_high", "too_low", "not_applicable"]},
    "reasoning": {"type": "string"},
})

_PROFILE_KEYS = ("firstname", "lastname", "position", "company", "city", "state", "country",
                 "keySkills", "workexpyr", "description", "experience", "education")


def judge_candidate(query, criteria, candidate):
    prof = {k: candidate.get(k) for k in _PROFILE_KEYS if candidate.get(k) not in (None, "", [])}
    scoring = {"matchScore": candidate.get("matchScore"), "matchLabel": candidate.get("matchLabel"),
               "matchAnalysis": candidate.get("matchAnalysis")}
    cs = "\n".join(f"- {c.get('text')} (w{c.get('weight')})" for c in (criteria or [])) or "(unscored run)"
    body = (f"USER QUERY:\n{query}\n\nCRITERIA USED FOR SCORING:\n{cs}\n\n"
            f"CANDIDATE PROFILE:\n{json.dumps(prof, ensure_ascii=False, default=str)[:5000]}\n\n"
            f"WHAT THE SYSTEM SAID:\n{json.dumps(scoring, ensure_ascii=False, default=str)[:3000]}")
    return _call(R_SYS, body, R_SCHEMA, max_tokens=1600)

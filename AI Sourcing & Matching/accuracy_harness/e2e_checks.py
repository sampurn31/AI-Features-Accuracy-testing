"""
Deterministic checks for the end-to-end accuracy suite (PW-6818).

Three responsibilities:
  1. `normalize_filters` — collapse the THREE filter shapes the product emits
     (db=2 search `queryUsed` snake_case, db=2 config `llmFilters` Title Case,
     db=1 config `llmFilters` nested) into one canonical dict, so a single
     expectation DSL can be asserted against any of them.
  2. `check_filters` / `check_results` — evaluate a case's declarative `expect`
     block. Every failure is a dict {code, field, detail, severity} — a hit is a
     real defect, no judgement involved.
  3. Mutation analysis — what SHOULD change when a criterion is removed, or its
     weight crosses the must-have threshold, and what should stay put.

Nothing here calls an LLM. Anything requiring taste lives in e2e_llm.py.
"""
import os
import re

# ── canonical field names ────────────────────────────────────────────────────
LIST_FIELDS = ("titles", "skills_must", "skills_good", "tools_must", "tools_good",
               "locations", "location_country", "companies", "industries", "company_tags",
               "education", "universities", "languages", "keywords", "excluded", "names")
SCALAR_FIELDS = ("company_employment", "title_employment", "graduation_year",
                 "yoe_min", "yoe_max", "radius", "excluded_bucket")

# Weight at/above which a criterion is expected to become a MUST-HAVE.
# Observed convention in the Neptune criteria->filters prompts (weight >= 7 -> must_have).
MUST_HAVE_WEIGHT = 7

_STOP = {"the", "a", "an", "and", "or", "of", "in", "with", "for", "to", "at", "on",
         "is", "are", "be", "have", "has", "must", "should", "experience", "strong",
         "good", "solid", "years", "year", "plus", "based", "from", "degree", "skills",
         "knowledge", "proficiency", "expert", "hands", "track", "record", "using"}


def _as_list(v):
    """Coerce any of the shapes a filter field arrives in into a flat list of strings."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, (int, float)):
        return [str(v)]
    if isinstance(v, dict):
        out = []
        for key in ("must_have", "good_to_have", "must", "should", "primary", "specified",
                    "synonyms", "variations", "family", "acronyms", "required", "preferred",
                    "city", "country", "values", "title"):
            if key in v:
                out += _as_list(v[key])
        if not out:                                  # unknown dict: take leaf strings
            for val in v.values():
                out += _as_list(val)
        return out
    if isinstance(v, (list, tuple)):
        out = []
        for item in v:
            if isinstance(item, dict):
                # language rows look like {"title": "Hindi", "subtitle": "..."}
                out += _as_list(item.get("title") or item.get("name") or item)
            else:
                out += _as_list(item)
        return out
    return [str(v)]


def _bucket(v, which):
    """Pull one bucket ('must_have' / 'good_to_have') out of a skills-like field."""
    if isinstance(v, dict):
        if which in v:
            return _as_list(v[which])
        return []
    # flat list: the schema gives no bucketing, so everything counts as unbucketed
    return _as_list(v) if which == "must_have" else []


def _key(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _get(raw, *aliases):
    """Case/format-insensitive lookup across the three schema dialects."""
    if not isinstance(raw, dict):
        return None
    index = {_key(k): k for k in raw}
    for a in aliases:
        k = index.get(_key(a))
        if k is not None:
            return raw[k]
    return None


def normalize_filters(raw):
    """Any product filter payload -> canonical dict. Never raises."""
    n = {f: [] for f in LIST_FIELDS}
    n.update({f: None for f in SCALAR_FIELDS})
    n["boolean_logic"] = {}
    n["_raw"] = raw
    if not isinstance(raw, dict):
        return n

    n["titles"] = _as_list(_get(raw, "job_titles", "jobTitles", "Job Titles"))
    skills = _get(raw, "skills", "Skills")
    n["skills_must"] = _bucket(skills, "must_have")
    n["skills_good"] = _bucket(skills, "good_to_have")
    tools = _get(raw, "tools_tech", "toolsTech", "Tools Tech")
    n["tools_must"] = _bucket(tools, "must_have")
    n["tools_good"] = _bucket(tools, "good_to_have")

    loc = _get(raw, "locations", "location", "Locations")
    n["locations"] = _as_list(loc)
    if isinstance(loc, dict):
        n["location_country"] = _as_list(loc.get("country"))
        n["radius"] = loc.get("radius_miles")

    n["companies"] = _as_list(_get(raw, "companies", "Companies"))
    n["company_employment"] = _get(raw, "company_employment_filter", "Company Employment Filter")
    n["title_employment"] = _get(raw, "title_employment_filter", "Title Employment Filter")
    n["company_tags"] = _as_list(_get(raw, "company_tags", "Company Tags"))
    n["industries"] = _as_list(_get(raw, "industries", "Industries"))
    n["education"] = _as_list(_get(raw, "education", "Education"))
    n["universities"] = _as_list(_get(raw, "universities", "Universities"))
    n["languages"] = _as_list(_get(raw, "languages", "Languages"))
    n["keywords"] = _as_list(_get(raw, "keywords", "Keywords",
                                  "additional_keywords", "Additional Keywords"))
    n["excluded"] = _as_list(_get(raw, "excluded_keywords", "Excluded Keywords"))
    n["names"] = _as_list(_get(raw, "names", "Names"))

    gy = _get(raw, "graduation_year", "Graduation Year", "graduationYear")
    n["graduation_year"] = gy if gy not in ("", [], {}) else None

    yoe = _get(raw, "years_of_experience", "Years of Experience", "experience", "Experience")
    if isinstance(yoe, dict):
        n["yoe_min"] = yoe.get("min", yoe.get("min_years"))
        n["yoe_max"] = yoe.get("max", yoe.get("max_years"))
    if n["radius"] is None:
        n["radius"] = _get(raw, "radius_miles", "radius")

    bl = _get(raw, "boolean_logic", "Boolean Logic")
    n["boolean_logic"] = bl if isinstance(bl, dict) else {}
    # which bucket did the exclusion terms actually land in?
    exc = (n["boolean_logic"].get("excluded_keywords")
           or n["boolean_logic"].get("Excluded Keywords") or {})
    if isinstance(exc, dict):
        for b in ("must_not", "must", "should"):
            if _as_list(exc.get(b)):
                n["excluded_bucket"] = b
                break
    n["skills_any"] = n["skills_must"] + n["skills_good"] + n["tools_must"] + n["tools_good"]
    n["tools_any"] = n["tools_must"] + n["tools_good"]
    return n


# ── expectation engine ───────────────────────────────────────────────────────
def _has(haystack, needle):
    nd = str(needle).lower().strip()
    return any(nd in str(h).lower() for h in haystack)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_filters(norm, expect, stage):
    """Assert a case's `expect.filters` block against normalized filters.

    Returns a list of failure dicts. `stage` labels where the filters came from
    (config_llm / c2f_llm / search_queryUsed) so the same rule can pass in one
    place and fail in another — which is itself a finding.
    """
    fails = []

    def fail(code, field, detail, sev="high"):
        fails.append({"code": code, "field": field, "detail": detail,
                      "severity": sev, "stage": stage})

    for field, rule in (expect or {}).items():
        if field not in norm and field not in ("skills_any", "tools_any"):
            fail("UNKNOWN-FIELD", field, f"no canonical field '{field}'", "low")
            continue
        val = norm.get(field)
        listy = isinstance(val, list)

        if rule.get("empty") and (val not in (None, [], {}, "")):
            fail("SHOULD-BE-EMPTY", field, f"expected empty, got {val!r}")
        if rule.get("nonempty") and (val in (None, [], {}, "")):
            fail("SHOULD-BE-SET", field, "expected a value, got nothing")

        for want in rule.get("includes", []) or []:
            if listy and not _has(val, want):
                fail("MISSING-VALUE", field, f"expected {want!r} among {val!r}")
            elif not listy and val is not None and str(want).lower() not in str(val).lower():
                fail("MISSING-VALUE", field, f"expected {want!r}, got {val!r}")
            elif not listy and val is None:
                fail("MISSING-VALUE", field, f"expected {want!r}, field is empty")

        for bad in rule.get("excludes", []) or []:
            if listy and _has(val, bad):
                fail("LEAKED-VALUE", field, f"{bad!r} must NOT appear; got {val!r}")
            elif not listy and val is not None and str(bad).lower() in str(val).lower():
                fail("LEAKED-VALUE", field, f"{bad!r} must NOT appear; got {val!r}")

        if "equals" in rule:
            want, got = rule["equals"], val
            if _num(want) is not None and _num(got) is not None:
                if _num(want) != _num(got):
                    fail("WRONG-VALUE", field, f"expected {want!r}, got {got!r}")
            elif str(want).lower() != str(got).lower():
                fail("WRONG-VALUE", field, f"expected {want!r}, got {got!r}")

        if "min" in rule and (_num(val) is None or _num(val) < rule["min"]):
            fail("WRONG-VALUE", field, f"expected >= {rule['min']}, got {val!r}")
        if "max" in rule and (_num(val) is None or _num(val) > rule["max"]):
            fail("WRONG-VALUE", field, f"expected <= {rule['max']}, got {val!r}")

        if "bucket" in rule and norm.get("excluded_bucket") not in (None, rule["bucket"]):
            fail("WRONG-BUCKET", field,
                 f"exclusion landed in boolean_logic.{norm['excluded_bucket']!r}, "
                 f"expected {rule['bucket']!r} (a 'should' clause BOOSTS instead of excluding)")
        if "bucket" in rule and norm.get("excluded_bucket") is None and norm.get("excluded"):
            fail("WRONG-BUCKET", field,
                 f"exclusion terms {norm['excluded']!r} present but no boolean_logic bucket — "
                 "unclear whether they are enforced", "medium")

        if "country_scope" in rule:
            pool = list(norm.get("locations") or []) + list(norm.get("location_country") or [])
            if not any(_has(pool, c) for c in rule["country_scope"]):
                fail("NO-COUNTRY-SCOPE", field,
                     f"query named a country but filters carry no country token; got {pool!r}",
                     "medium")
    return fails


def check_unsupported(query, norm, unsupported):
    """A constraint the user typed that the schema never expressed.

    Not automatically a bug — but silence about it is. Reported so product can
    decide: express it, ask about it, or tell the user it was ignored.
    """
    probes = {
        "salary": (r"\b(lpa|ctc|salary|compensation|package|\$\d|k per|per annum)\b",
                   ("keywords", "excluded")),
        "notice_period": (r"\b(notice period|immediate join|immediately available|serving notice)\b",
                          ("keywords",)),
        "work_authorization": (r"\b(visa|sponsorship|work permit|h1b|green card|citizen)\b",
                               ("keywords",)),
        "gender": (r"\b(female|male|women|men|woman|man)\b", ("keywords",)),
        "age": (r"\b(under \d\d|over \d\d|age|years old)\b", ("keywords",)),
        "radius": (r"\b(within \d+\s*(km|miles|mi)\b|radius)", ("radius",)),
        "relocation_willingness": (r"\b(relocat|willing to move)\w*\b", ("keywords",)),
    }
    out = []
    for name in unsupported or []:
        pat, fields = probes.get(name, (None, ()))
        if not pat or not re.search(pat, query, re.I):
            continue
        landed = any(norm.get(f) not in (None, [], "", {}) for f in fields)
        if not landed:
            out.append({"code": "SILENT-DROP", "field": name, "severity": "medium",
                        "stage": "filters",
                        "detail": f"query states a {name} constraint; no filter field carries it "
                                  "and nothing signals it was ignored"})
    return out


def _cand_text(c, *fields):
    parts = []
    for f in fields:
        v = c.get(f)
        if isinstance(v, list):
            v = " ".join(str(x) for x in v)
        parts.append(str(v or ""))
    return " ".join(parts).lower()


def check_results(cands, metadata, expect, ):
    """Assert a case's `expect.results` block against the returned candidates."""
    fails = []
    exp = expect or {}
    total = (metadata or {}).get("totalResults")

    def fail(code, field, detail, sev="high"):
        fails.append({"code": code, "field": field, "detail": detail,
                      "severity": sev, "stage": "results"})

    if "min_total" in exp and not exp.get("zero_ok"):
        if total is None or total < exp["min_total"]:
            fail("ZERO-OR-THIN", "totalResults",
                 f"expected >= {exp['min_total']} results, got {total!r} — "
                 "over-constrained query extraction is the usual cause")
    if "max_total" in exp and total is not None and total > exp["max_total"]:
        fail("TOO-BROAD", "totalResults", f"expected <= {exp['max_total']}, got {total}", "medium")
    if not cands:
        return fails

    frac = exp.get("min_frac", 0.7)

    if exp.get("location_country"):
        want = exp["location_country"]
        ok = [c for c in cands if want.lower() in _cand_text(c, "country")]
        if len(ok) < len(cands) * frac:
            bad = [f"{c.get('firstname')} {c.get('lastname')} ({c.get('city')}, {c.get('country')})"
                   for c in cands if want.lower() not in _cand_text(c, "country")][:5]
            fail("WRONG-COUNTRY", "results",
                 f"only {len(ok)}/{len(cands)} candidates are in {want}; e.g. {bad}")

    for key, fields, code in (("location_any", ("city", "state", "country"), "WRONG-LOCATION"),
                              ("title_any", ("position",), "OFF-TITLE"),
                              ("skill_any", ("keySkills", "description", "position"), "MISSING-SKILL"),
                              ("company_any", ("company",), "WRONG-COMPANY")):
        wants = exp.get(key)
        if not wants:
            continue
        ok = [c for c in cands if any(w.lower() in _cand_text(c, *fields) for w in wants)]
        if len(ok) < len(cands) * frac:
            bad = [(f"{c.get('firstname')} {c.get('lastname')}", c.get("position"),
                    f"{c.get('city')},{c.get('country')}")
                   for c in cands if not any(w.lower() in _cand_text(c, *fields) for w in wants)][:5]
            fail(code, "results",
                 f"only {len(ok)}/{len(cands)} match {wants}; e.g. {bad}")

    for bad_skill in exp.get("must_not_skill", []) or []:
        hits = [f"{c.get('firstname')} {c.get('lastname')} [{c.get('position')}]"
                for c in cands if bad_skill.lower() in _cand_text(c, "keySkills", "position")]
        if hits:
            fail("EXCLUSION-LEAK", "results",
                 f"{len(hits)}/{len(cands)} results carry excluded term {bad_skill!r}: {hits[:5]}")
    return fails


# ── mutation analysis ────────────────────────────────────────────────────────
def distinctive_tokens(text):
    """Content words of a criterion — the tokens whose presence in the filters is
    attributable to THAT criterion."""
    toks = re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,}", str(text or ""))
    return [t for t in toks if t.lower() not in _STOP and len(t) > 2]


# Free-text fields hold whole phrases ("write production-quality code"), so a token
# found there says nothing about which criterion put it in the query. Attribution
# checks read structured fields only; churn reads everything.
_FREE_TEXT_FIELDS = ("keywords", "education", "excluded")


def _flat_values(norm, structured_only=False):
    fields = [f for f in LIST_FIELDS if not (structured_only and f in _FREE_TEXT_FIELDS)]
    out = []
    for f in fields:
        out += [str(x) for x in (norm.get(f) or [])]
    for f in ("graduation_year", "yoe_min", "yoe_max", "radius"):
        if norm.get(f) not in (None, "", []):
            out.append(str(norm[f]))
    return out


def _same_stem(a, b):
    """Loose word identity: 'Analyst'/'analysts', 'development'/'developers'.

    Attribution must not hinge on morphology — 'Engineer' surviving because the
    query said 'engineers' is correct behaviour, not a defect.
    """
    a, b = a.lower(), b.lower()
    if a == b or a.rstrip("s") == b.rstrip("s"):
        return True
    common = os.path.commonprefix([a, b])
    return len(common) >= 6 and min(len(a), len(b)) >= 7


def _own_tokens(text, other_texts):
    """Tokens attributable to THIS criterion alone.

    A token that also appears in a criterion we kept — or in the user's own query —
    is not evidence of anything: it is legitimately still in the filters because
    something else put it there.
    """
    shared = [t for o in (other_texts or []) if o for t in distinctive_tokens(o)]
    return [t for t in distinctive_tokens(text)
            if not any(_same_stem(t, s) for s in shared)]


def check_removal(base, mutated, removed_text, kept_texts, query=""):
    """Removing a criterion must remove ITS contribution and leave the rest intact.

    `query` matters: a term the user typed drives the filters directly, so it is
    entitled to survive the deletion of any criterion that happens to repeat it.
    """
    fails = []
    context = list(kept_texts) + ([query] if query else [])
    base_vals = _flat_values(base, structured_only=True)
    mut_vals = _flat_values(mutated, structured_only=True)
    removed_toks = [t for t in _own_tokens(removed_text, context) if _has(base_vals, t)]
    still = [t for t in removed_toks if _has(mut_vals, t)]
    if removed_toks and len(still) == len(removed_toks):
        fails.append({"code": "REMOVAL-IGNORED", "field": "criteria", "severity": "high",
                      "stage": "mutation",
                      "detail": f"removed criterion {removed_text!r} but every one of its tokens "
                                f"{still!r} is still in the filters — deleting a criterion did not "
                                "change the query"})
    # Scalar criteria carry no distinctive words, so token attribution cannot see them.
    # Handle them explicitly: if the ONLY criterion expressing a numeric constraint is
    # deleted, the corresponding filter must clear.
    scalar_probes = (
        (r"\b(\d+\s*[-+]|\d+\s*to\s*\d+|minimum|at least|more than|less than)\s*\d*\s*year",
         ("yoe_min", "yoe_max"), "years-of-experience"),
        (r"\bgraduat\w*\s+(in\s+)?\d{4}\b|\b\d{4}\s+graduat", ("graduation_year",), "graduation-year"),
    )
    for pat, fields, label in scalar_probes:
        if not re.search(pat, removed_text, re.I):
            continue
        if any(re.search(pat, k or "", re.I) for k in kept_texts):
            continue                    # another criterion still asks for it
        before = {f: base.get(f) for f in fields if base.get(f) not in (None, "", [])}
        if before and all(mutated.get(f) == base.get(f) for f in before):
            fails.append({"code": "REMOVAL-IGNORED", "field": label, "severity": "high",
                          "stage": "mutation",
                          "detail": f"removed the only {label} criterion ({removed_text!r}) but the "
                                    f"filter is unchanged: {before} — the deleted requirement is "
                                    "still constraining the search"})

    # collateral: tokens owned by criteria we KEPT must not vanish
    lost = []
    for kt in kept_texts:
        others = [x for x in kept_texts if x != kt] + [removed_text, query]
        for t in _own_tokens(kt, others):
            if _has(base_vals, t) and not _has(mut_vals, t):
                lost.append(t)
    if lost:
        fails.append({"code": "COLLATERAL-LOSS", "field": "criteria", "severity": "high",
                      "stage": "mutation",
                      "detail": f"removing {removed_text!r} also dropped {sorted(set(lost))!r}, "
                                "which belong to criteria that were kept"})
    return fails


def check_weight_move(base, mutated, text, new_weight, other_texts=()):
    """Crossing the must-have threshold must move the criterion's terms between buckets.

    Only tokens owned solely by this criterion are checked — a skill another
    criterion also demands is entitled to stay where it is.
    """
    fails = []
    toks = [t for t in _own_tokens(text, other_texts)
            if _has(base["skills_must"] + base["skills_good"] +
                    base["tools_must"] + base["tools_good"], t)]
    if not toks:
        return fails
    to_must = new_weight >= MUST_HAVE_WEIGHT
    must_after = mutated["skills_must"] + mutated["tools_must"]
    good_after = mutated["skills_good"] + mutated["tools_good"]
    for t in toks:
        in_must, in_good = _has(must_after, t), _has(good_after, t)
        if to_must and in_good and not in_must:
            fails.append({"code": "WEIGHT-NO-EFFECT", "field": "skills", "severity": "medium",
                          "stage": "mutation",
                          "detail": f"weight raised to {new_weight} (>= {MUST_HAVE_WEIGHT}) but "
                                    f"{t!r} stayed in good_to_have"})
        if not to_must and in_must and not in_good:
            fails.append({"code": "WEIGHT-NO-EFFECT", "field": "skills", "severity": "medium",
                          "stage": "mutation",
                          "detail": f"weight lowered to {new_weight} (< {MUST_HAVE_WEIGHT}) but "
                                    f"{t!r} stayed a hard must_have — this is what over-filters "
                                    "and returns zero candidates"})
    return fails


def churn(base, mutated, ignore_tokens=()):
    """How much of the filter set moved that had no reason to move.

    A criterion edit should be a local change. Wholesale re-generation (high
    churn) means the user cannot predict what their edit will do.
    """
    ign = {t.lower() for t in ignore_tokens}
    def bag(n):
        return {v.lower() for v in _flat_values(n)
                if not any(i in v.lower() for i in ign)}
    b, m = bag(base), bag(mutated)
    if not b and not m:
        return 0.0, [], []
    added, dropped = m - b, b - m
    ratio = round(len(added | dropped) / max(len(b | m), 1), 3)
    return ratio, sorted(added), sorted(dropped)


def compare_stages(config_norm, search_norm):
    """The filters the UI shows (config) vs the filters retrieval actually used (search).

    Divergence means a user editing filters is editing something that does not
    drive the result set.
    """
    fails = []
    for field in ("locations", "companies", "skills_any", "titles"):
        a = {str(x).lower() for x in (config_norm.get(field) or [])}
        b = {str(x).lower() for x in (search_norm.get(field) or [])}
        if not a and not b:
            continue
        overlap = len(a & b) / max(len(a | b), 1)
        if overlap < 0.5:
            fails.append({"code": "STAGE-DIVERGENCE", "field": field, "severity": "medium",
                          "stage": "config_vs_search",
                          "detail": f"config filters {sorted(a)!r} vs retrieval filters "
                                    f"{sorted(b)!r} — overlap {overlap:.0%}"})
    return fails

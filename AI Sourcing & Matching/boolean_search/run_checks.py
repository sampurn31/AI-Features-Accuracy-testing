"""
AI Sourcing V3 boolean search — validation harness.

Answers the question the configuration-stage suites structurally could not: is the
boolean query the recruiter typed actually applied to the records that come back?

Four layers.

LAYER 1 — count algebra (`/search/count`, ~1s a call)
    Relations between queries, so no golden result set is needed and nothing goes stale
    as the index changes. Inclusion-exclusion, complement, absorption, De Morgan (both
    forms), distributivity (both directions), associativity, commutativity, idempotence,
    parenthesis sensitivity and nesting depth.
    The load-bearing one is |A AND NOT B| == |A| - |A AND B|. Had it been running, the
    shipped defect would have read as |A AND NOT B| == |A| — an exclusion changing nothing.

LAYER 2 — record validation (`/search`, ~12s a call)
    The generated DSL must carry the query in `bool.filter` or `bool.must`, never only in
    `bool.should`: `should` boosts ranking without restricting results, which is exactly
    how a filter can be present, parsed and echoed back while filtering nothing.

    The record oracle is `highlightTerms`, NOT the record's text. The returned
    `description` is generated per search — the same query twice yields different prose
    for most of the same ids — so a term found in it says nothing about what the index
    matched. An early version of this harness read it as the indexed `summary` and
    produced a false exclusion finding.

LAYER 3 — malformed and edge input (`/search/count`)
    Empty, whitespace, operators alone, dangling operators, unbalanced parens and quotes,
    operator casing, punctuation-bearing terms, unicode, deep nesting, a very long query.
    The requirement is a defined answer rather than a 500.

LAYER 4 — boolean combined with the other filters (`/search/count`)
    The boolean query is one filter among several. Adding a further constraint must
    never widen the result set, and the boolean must keep binding once others are present.

LAYER 5 — terms that CONTAIN an operator word
    The classic failure of a hand-rolled tokenizer is matching its operators as
    substrings: the parser this feature used to run split `stand up` into `st AND up`
    and `Android` into `` AND `roid`. Both are ordinary recruiting vocabulary. Every term
    here carries `and`, `or` or `not` inside it, and each is checked to survive as one
    term in both the parsed keywords and the highlight list. See collisions.py.

Usage:
    export AIS_EMAIL=... AIS_PASSWORD=...        # never stored here
    python3 run_checks.py                        # all layers
    python3 run_checks.py --layer 1              # algebra only
    python3 run_checks.py --env ai4 --xlsx report.xlsx
"""
import argparse
import json
import sys
import time

from booleval import parse, terms
from client import SearchClient, SearchError
from collisions import (COLLISION_TERMS, COMPOSITES, HIGHLIGHT_SUBSET, PHRASE_CASES,
                        UNION_TERMS)
from triage import classify

# Terms common enough to give non-trivial counts, and disjoint enough that their
# intersections are small — a relation over two terms that never co-occur proves little.
A, B, C, D, E = "Python", "Kubernetes", "Docker", "Java", "React"
PHRASE = '"Machine Learning"'
NEG = "Intern"


class Results:

    def __init__(self):
        self.rows = []

    def add(self, layer, name, query, expected, actual, passed, detail=""):
        status, note = classify(name, passed)
        self.rows.append(dict(layer=layer, name=name, query=str(query),
                              expected=str(expected), actual=str(actual),
                              status=status, note=note, detail=detail))
        return status

    def by_status(self, status):
        return [r for r in self.rows if r["status"] == status]

    def report(self):
        current = None
        for row in self.rows:
            if row["layer"] != current:
                current = row["layer"]
                print(f"\n{'=' * 96}\n{current}\n{'=' * 96}")
            print(f"  [{row['status']:<5}] {row['name']}")
            if row["status"] != "PASS":
                print(f"          query    : {row['query']}")
                print(f"          expected : {row['expected']}   got: {row['actual']}")
                for line in (row["note"], row["detail"]):
                    if line:
                        print(f"          {line}")
        passed, failed = len(self.by_status("PASS")), len(self.by_status("FAIL"))
        known = len(self.by_status("KNOWN"))
        print(f"\n{'=' * 96}")
        print(f"  {passed} passed | {failed} FAILED | {known} known/accepted"
              f"   (total {len(self.rows)})")
        for row in self.by_status("FAIL"):
            print(f"    FAIL  [{row['layer'].split('-')[0].strip()}] {row['name']}")
        print("=" * 96)


# =================================================================================
# Layer 1 — count algebra
# =================================================================================

L1 = "Layer 1 - count algebra"


def layer1(client, results):
    cache = {}

    def n(query):
        if query not in cache:
            try:
                cache[query] = client.count(query)
            except SearchError as exc:
                cache[query] = exc
            time.sleep(0.1)
        return cache[query]

    def ok(*values):
        return all(not isinstance(v, SearchError) for v in values)

    def relation(name, left_q, right_q, detail=""):
        """Two queries that must return the same count."""
        left, right = n(left_q), n(right_q)
        results.add(L1, name, f"{left_q}   ==   {right_q}", left, right,
                    ok(left, right) and left == right, detail)

    def differs(name, left_q, right_q, detail=""):
        left, right = n(left_q), n(right_q)
        results.add(L1, name, f"{left_q}   !=   {right_q}", f"{left} != {right}",
                    f"{left} vs {right}", ok(left, right) and left != right, detail)

    nA, nB, nC = n(A), n(B), n(C)
    results.add(L1, "baseline terms return results", f"{A} / {B} / {C}", "> 0",
                f"{nA} / {nB} / {nC}", ok(nA, nB, nC) and min(nA, nB, nC) > 0,
                "a zero baseline would make every relation below vacuously true")

    nAB, nAorB = n(f"{A} AND {B}"), n(f"{A} OR {B}")
    results.add(L1, "inclusion-exclusion", f"|{A} OR {B}| == |{A}|+|{B}|-|{A} AND {B}|",
                nA + nB - nAB if ok(nA, nB, nAB) else "n/a", nAorB,
                ok(nA, nB, nAB, nAorB) and nA + nB - nAB == nAorB,
                f"|A|={nA} |B|={nB} |A AND B|={nAB} |A OR B|={nAorB}")
    results.add(L1, "AND bounded by its narrower side", f"{A} AND {B}",
                f"<= {min(nA, nB)}", nAB, ok(nAB) and nAB <= min(nA, nB))
    results.add(L1, "OR bounded by its wider side", f"{A} OR {B}",
                f">= {max(nA, nB)}", nAorB, ok(nAorB) and nAorB >= max(nA, nB))

    # --- NOT -------------------------------------------------------------------
    nAnotB = n(f"{A} AND NOT {B}")
    results.add(L1, "complement |A AND NOT B| = |A| - |A AND B|", f"{A} AND NOT {B}",
                nA - nAB if ok(nA, nAB) else "n/a", nAnotB,
                ok(nA, nAB, nAnotB) and nA - nAB == nAnotB,
                "THE check that would have caught the shipped defect: an ignored "
                "exclusion makes this equal |A|")
    results.add(L1, "NOT is not silently ignored", f"{A} AND NOT {B}", f"!= {nA}",
                nAnotB, ok(nAnotB) and nAnotB != nA)
    nContra = n(f"{A} AND NOT {A}")
    results.add(L1, "contradiction", f"{A} AND NOT {A}", 0, nContra,
                ok(nContra) and nContra == 0)
    nDouble = n(f"{A} AND NOT NOT {B}")
    results.add(L1, "double negation is handled", f"{A} AND NOT NOT {B}", nAB, nDouble,
                ok(nDouble) and nDouble == nAB)
    nTwoNot = n(f"{A} AND NOT {B} AND NOT {C}")
    results.add(L1, "two exclusions narrow further", f"{A} AND NOT {B} AND NOT {C}",
                f"<= {nAnotB}", nTwoNot, ok(nAnotB, nTwoNot) and nTwoNot <= nAnotB)
    relation("exclusion order is immaterial", f"{A} AND NOT {B} AND NOT {C}",
             f"{A} AND NOT {C} AND NOT {B}")

    # --- structural laws --------------------------------------------------------
    relation("idempotence (AND)", f"{A} AND {A}", A)
    relation("idempotence (OR)", f"{A} OR {A}", A)
    relation("commutativity (AND)", f"{A} AND {B}", f"{B} AND {A}")
    relation("commutativity (OR)", f"{A} OR {B}", f"{B} OR {A}")
    relation("associativity (AND)", f"({A} AND {B}) AND {C}", f"{A} AND ({B} AND {C})")
    relation("associativity (OR)", f"({A} OR {B}) OR {C}", f"{A} OR ({B} OR {C})")
    relation("absorption A OR (A AND B)", f"{A} OR ({A} AND {B})", A,
             "a record matching A AND B already matches A, so the OR adds nothing")
    relation("absorption A AND (A OR B)", f"{A} AND ({A} OR {B})", A)
    relation("distributivity of AND over OR", f"({A} OR {B}) AND {C}",
             f"({A} AND {C}) OR ({B} AND {C})")
    relation("distributivity of OR over AND", f"({A} AND {B}) OR {C}",
             f"({A} OR {C}) AND ({B} OR {C})")
    relation("De Morgan - NOT over OR", f"{A} AND NOT ({B} OR {C})",
             f"{A} AND NOT {B} AND NOT {C}")
    relation("De Morgan - NOT over AND", f"{A} AND NOT ({B} AND {C})",
             f"{A} AND (NOT {B} OR NOT {C})")
    relation("redundant parentheses are inert", f"(({A}))", A)
    relation("parentheses around single terms", f"({A}) AND ({B})", f"{A} AND {B}")

    # --- grouping actually groups ----------------------------------------------
    differs("parentheses change the answer", f"({A} OR {B}) AND {C}",
            f"{A} OR ({B} AND {C})",
            "identical counts would mean the grouping was ignored")
    differs("nested parentheses differ from a flat regrouping",
            f"(({A} OR {D}) AND {C}) OR {B}", f"({A} OR {D}) AND ({C} OR {B})")
    relation("three-deep nesting", f"((({A} OR {D}) AND {C}) OR {B})",
             f"(({A} OR {D}) AND {C}) OR {B}")
    nNoParen, nParen = n(f"{A} OR {B} AND {C}"), n(f"{A} OR ({B} AND {C})")
    results.add(L1, "AND binds tighter than OR without parentheses",
                f"{A} OR {B} AND {C}", nParen, nNoParen,
                ok(nNoParen, nParen) and nNoParen == nParen)

    # --- phrases -----------------------------------------------------------------
    nPhrase = n(PHRASE)
    results.add(L1, "quoted phrase returns results", PHRASE, "> 0", nPhrase,
                ok(nPhrase) and nPhrase > 0)
    differs("quoting changes the meaning", PHRASE, "Machine Learning",
            "a quoted phrase is stricter than the same two words unquoted")
    nPhraseNot = n(f"{PHRASE} AND NOT {NEG}")
    results.add(L1, "phrase with exclusion narrows", f"{PHRASE} AND NOT {NEG}",
                f"< {nPhrase}", nPhraseNot, ok(nPhrase, nPhraseNot) and nPhraseNot < nPhrase)
    relation("phrase inside a group", f"({PHRASE} OR {A}) AND {C}",
             f"({PHRASE} AND {C}) OR ({A} AND {C})")

    # --- degenerate ---------------------------------------------------------------
    nBareNot = n(f"NOT {B}")
    results.add(L1, "bare NOT returns nothing", f"NOT {B}", 0, nBareNot,
                ok(nBareNot) and nBareNot == 0,
                "a pure exclusion has no positive criteria; 0 rather than the whole index")
    taut = n(f"{A} OR NOT {A}")
    results.add(L1, "tautology A OR NOT A returns everything", f"{A} OR NOT {A}",
                f">= {nA}", taut, ok(taut) and taut >= nA,
                "every record satisfies A OR NOT A, so anything below |A| is wrong")

    # --- more terms ---------------------------------------------------------------
    relation("four-term AND is order independent", f"{A} AND {B} AND {C} AND {D}",
             f"{D} AND {C} AND {B} AND {A}")
    relation("five-term OR is order independent", f"{A} OR {B} OR {C} OR {D} OR {E}",
             f"{E} OR {D} OR {C} OR {B} OR {A}")
    nWide = n(f"{A} OR {B} OR {C} OR {D} OR {E}")
    results.add(L1, "wide OR is at least as wide as any part",
                f"{A} OR {B} OR {C} OR {D} OR {E}", f">= {nA}", nWide,
                ok(nWide) and nWide >= nA)
    nNarrow = n(f"{A} AND {B} AND {C} AND {D}")
    results.add(L1, "deep AND is no wider than its narrowest part",
                f"{A} AND {B} AND {C} AND {D}", f"<= {min(nA, nB, nC)}", nNarrow,
                ok(nNarrow) and nNarrow <= min(nA, nB, nC))
    relation("group AND group is commutative", f"({A} OR {D}) AND ({C} OR {E})",
             f"({C} OR {E}) AND ({A} OR {D})")
    relation("exclusion outside vs inside a group", f"({A} OR {D}) AND NOT {B}",
             f"({A} AND NOT {B}) OR ({D} AND NOT {B})")


# =================================================================================
# Layer 2 — record validation
# =================================================================================

L2 = "Layer 2 - record validation"

RECORD_QUERIES = [
    ("single term", A),
    ("two-term AND", f"{A} AND {C}"),
    ("two-term OR", f"{A} OR {B}"),
    ("three-term AND", f"{A} AND {C} AND {D}"),
    ("single exclusion", f"{A} AND NOT {B}"),
    ("two exclusions", f"{A} AND NOT {B} AND NOT {NEG}"),
    ("exclusion of a phrase", f'{A} AND NOT "{NEG} Engineer"'),
    ("NOT over a group", f"{A} AND NOT ({B} OR {C})"),
    ("group then AND", f"({A} OR {D}) AND {C}"),
    ("group AND group", f"({A} OR {D}) AND ({C} OR {E})"),
    ("nested group with exclusion", f"(({A} OR {D}) AND {C}) AND NOT {B}"),
    ("three-deep nesting", f"((({A} OR {D}) AND {C}) OR {B}) AND NOT {NEG}"),
    ("quoted phrase", PHRASE),
    ("phrase AND term NOT term", f"{PHRASE} AND {A} AND NOT {NEG}"),
    ("complex mixed", f"({A} OR {D}) AND ({C} OR {E}) AND NOT {NEG}"),
    ("lowercase operators", f"{A} and {C}"),
]


def layer2(client, results, limit=None):
    for name, query in (RECORD_QUERIES if limit is None else RECORD_QUERIES[:limit]):
        try:
            metadata, candidates = client.search(query)
        except SearchError as exc:
            results.add(L2, f"{name}: call succeeds", query, "a page of records",
                        f"HTTP {exc.status}", False)
            continue
        if not candidates:
            results.add(L2, f"{name}: returns records", query, "> 0", 0, False)
            continue

        dsl = metadata.get("openSearchQuery")
        if isinstance(dsl, str):
            try:
                dsl = json.loads(dsl)
            except json.JSONDecodeError:
                dsl = {}
        section = _binding_section(dsl or {}, query)
        results.add(L2, f"{name}: query is a binding clause", query,
                    "bool.filter or bool.must", section or "only bool.should / absent",
                    bool(section),
                    "a query_string only under bool.should ranks results without "
                    "restricting them - the exact shape of the shipped defect")

        parsed = parse(query)
        negated = {t.lower().strip('"') for t, neg in terms(parsed) if neg}
        positive = {t.lower().strip('"') for t, neg in terms(parsed) if not neg}

        bad_negated, punctuated, samples = 0, 0, []
        for candidate in candidates:
            highlights = [str(t) for t in (candidate.get("highlightTerms") or [])]
            if any(h.lower().strip('"') in negated for h in highlights):
                bad_negated += 1
                if len(samples) < 2:
                    samples.append(f"id {candidate.get('id')}: {highlights}")
            if any(h != h.strip("()") for h in highlights):
                punctuated += 1
                if len(samples) < 2:
                    samples.append(f"id {candidate.get('id')}: {highlights}")

        if negated:
            results.add(L2, f"{name}: excluded term never highlighted", query, 0,
                        bad_negated, bad_negated == 0,
                        f"excluded {sorted(negated)} over {len(candidates)} records. "
                        + "; ".join(samples[:1]))

        results.add(L2, f"{name}: highlight terms are clean tokens", query, 0, punctuated,
                    punctuated == 0,
                    f"{punctuated}/{len(candidates)} records carry a bracket in a "
                    f"highlight term; these come from the flat keyword list, which splits "
                    f"on whitespace and never strips parentheses. " + "; ".join(samples[:1]))

        if positive:
            matched = sum(1 for c in candidates
                          if any(str(t).lower().strip('"') in positive
                                 for t in (c.get("highlightTerms") or [])))
            results.add(L2, f"{name}: positive terms highlighted", query, "> 0",
                        f"{matched}/{len(candidates)}", matched > 0,
                        f"positive terms {sorted(positive)}")


def _binding_section(dsl, query):
    root = (dsl.get("query") or {}).get("bool") or {}
    for section in ("filter", "must"):
        for clause in root.get(section) or []:
            qs = clause.get("query_string") if isinstance(clause, dict) else None
            if qs and _same(qs.get("query", ""), query):
                return f"bool.{section}"
    return None


def _same(left, right):
    return "".join(left.split()).upper() == "".join(right.split()).upper()


# =================================================================================
# Layer 3 — malformed and edge input
# =================================================================================

L3 = "Layer 3 - malformed and edge input"

EDGE_CASES = [
    ("empty string", ""),
    ("whitespace only", "   "),
    ("operator alone - AND", "AND"),
    ("operator alone - NOT", "NOT"),
    ("dangling AND", f"{A} AND"),
    ("leading OR", f"OR {A}"),
    ("double operator", f"{A} AND AND {B}"),
    ("unbalanced open paren", f"({A} OR {B}"),
    ("unbalanced close paren", f"{A} OR {B})"),
    ("empty parentheses", f"{A} AND ()"),
    ("unbalanced quote", f'"{A} AND {B}'),
    ("lowercase operators", f"{A} and {B}"),
    ("mixed-case operators", f"{A} And {B} Or {C}"),
    ("term with a dot", "Node.js"),
    ("term with a plus", "C++"),
    ("term with a hash", "C#"),
    ("term with a hyphen", "front-end"),
    ("term with digits", "Python3"),
    ("accented term", "Ingenieur"),
    ("non-Latin term", "工程师"),
    ("very long query", " OR ".join([A, B, C, D, E] * 12)),
    ("deeply nested", "(" * 8 + A + ")" * 8),
    ("term containing an operator word", "Andover"),
    ("reserved char - slash", "AI/ML"),
    ("reserved char - colon", "skill:Python"),
    ("wildcard star", "Pyth*"),
]


def layer3(client, results):
    for name, query in EDGE_CASES:
        try:
            count = client.count(query)
            results.add(L3, f"{name}: answers without failing", repr(query),
                        "a defined count", count, isinstance(count, int),
                        "malformed input must produce an answer, not a 500")
        except SearchError as exc:
            results.add(L3, f"{name}: answers without failing", repr(query),
                        "a defined count", f"HTTP {exc.status}", False, exc.body[:160])
        time.sleep(0.1)


# =================================================================================
# Layer 4 — boolean combined with the other filters
# =================================================================================

L4 = "Layer 4 - combined with other filters"

COMBINATIONS = [
    ("plus job title", {"jobTitle": {"values": ["Software Engineer"], "currentOnly": False}}),
    ("plus job title current-only", {"jobTitle": {"values": ["Software Engineer"], "currentOnly": True}}),
    ("plus skills", {"skills": {"values": ["Python"], "mustHave": []}}),
    ("plus must-have skills", {"skills": {"values": [], "mustHave": ["Python"]}}),
    ("plus location", {"location": {"value": "Algeria"}}),
    ("plus experience range", {"experience": {"min": 2, "max": 10}}),
    ("plus company", {"company": {"values": ["Freelance"], "currentOnly": False}}),
    ("plus education degree", {"education": {"universities": [], "degrees": ["Computer Science"]}}),
]


def layer4(client, results):
    base_query = f"{A} AND NOT {B}"
    try:
        base = client.count(base_query)
    except SearchError as exc:
        results.add(L4, "baseline for combinations", base_query, "a count",
                    f"HTTP {exc.status}", False)
        return
    results.add(L4, "baseline for combinations", base_query, "> 0", base, base > 0)

    for name, extra in COMBINATIONS:
        try:
            combined = client.count(base_query, **extra)
        except SearchError as exc:
            results.add(L4, f"{name}: answers", base_query, "a count",
                        f"HTTP {exc.status}", False, json.dumps(extra))
            continue
        results.add(L4, f"{name}: adding a filter never widens", base_query,
                    f"<= {base}", combined, combined <= base,
                    f"filter added {json.dumps(extra)} - a further constraint cannot "
                    f"increase the result count")
        time.sleep(0.1)

    # The boolean must keep binding once other filters are present.
    title = {"jobTitle": {"values": ["Software Engineer"], "currentOnly": False}}
    try:
        with_not = client.count(f"{A} AND NOT {B}", **title)
        without = client.count(A, **title)
        results.add(L4, "exclusion still binds alongside a job title",
                    f"{A} AND NOT {B}  + jobTitle", f"< {without}", with_not,
                    with_not < without,
                    "if the boolean stopped binding once another filter was present, "
                    "these two counts would be equal")
    except SearchError as exc:
        results.add(L4, "exclusion still binds alongside a job title", base_query,
                    "a count", f"HTTP {exc.status}", False)



# =================================================================================
# Layer 5 — terms containing an operator word
# =================================================================================

L5 = "Layer 5 - operator-substring collisions"


def layer5(client, results):
    counts = {}

    def n(query):
        if query not in counts:
            try:
                counts[query] = client.count(query)
            except SearchError as exc:
                counts[query] = exc
            time.sleep(0.1)
        return counts[query]

    # --- every collision term must answer, and not be split ----------------------
    for label, term, operator, position in COLLISION_TERMS:
        where = f"contains '{operator}' at the {position}" if operator != "-" else "control term"
        value = n(term)
        results.add(L5, f"{label}: answers without failing", term, "a defined count",
                    value, not isinstance(value, SearchError), where)
        if isinstance(value, SearchError):
            continue
        # Idempotence on a collision term: if the term were split, AND-ing it with
        # itself would not necessarily reproduce the same count.
        same = n(f"{term} AND {term}")
        results.add(L5, f"{label}: idempotent under AND", f"{term} AND {term}", value,
                    same, not isinstance(same, SearchError) and same == value, where)

    # --- the parsed keyword list must carry the whole term -----------------------
    for label in HIGHLIGHT_SUBSET:
        term = next(t for lbl, t, _, _ in COLLISION_TERMS if lbl == label)
        try:
            metadata, candidates = client.search(term)
        except SearchError as exc:
            results.add(L5, f"{label}: parses as one term", term, f"['{term}']",
                        f"HTTP {exc.status}", False)
            continue
        parsed = _parsed_keywords(metadata)
        results.add(L5, f"{label}: parses as one term", term, [term], parsed,
                    parsed == [term],
                    "a split here is the substring-operator bug: the term would arrive "
                    "as fragments and search for something the recruiter never typed")
        highlights = sorted({str(t) for c in candidates
                             for t in (c.get("highlightTerms") or [])})
        results.add(L5, f"{label}: highlights as one term", term, [term], highlights,
                    highlights == [term],
                    "the highlight list is built from its own parse and can regress "
                    "independently of the search")

    # --- composites: a collision term beside real operators ----------------------
    for label, query, expected_terms in COMPOSITES:
        try:
            metadata, candidates = client.search(query)
        except SearchError as exc:
            results.add(L5, f"{label}: answers", query, "a page of records",
                        f"HTTP {exc.status}", False)
            continue
        # `queryUsed.keywords` carries the POSITIVE terms only. A negated term is not
        # listed there and, on this path, `excluded_keywords` stays empty too — the
        # exclusion lives solely in the raw booleanQuery handed to OpenSearch. So the
        # expectation is the positive subset, not every term in the expression.
        parsed = _parsed_keywords(metadata)
        expected_positive = sorted(t for t in expected_terms
                                   if t.lower() not in _negated_terms(query))
        results.add(L5, f"{label}: every term survives whole", query,
                    expected_positive, sorted(parsed),
                    sorted(parsed) == expected_positive,
                    "the real operators must split the query and the operator letters "
                    "inside a term must not; negated terms are carried in the raw query "
                    "rather than in the keyword list")
        highlights = sorted({str(t) for c in candidates
                             for t in (c.get("highlightTerms") or [])})
        positives = sorted(t for t in expected_terms
                           if t.lower() not in _negated_terms(query))
        results.add(L5, f"{label}: highlights only the positive terms", query,
                    positives, highlights, highlights == positives)

    # --- the reported case, verified by three-set inclusion-exclusion ------------
    a, b, c_ = UNION_TERMS
    singles = [n(t) for t in UNION_TERMS]
    pairs = [n(f"{a} AND {b}"), n(f"{a} AND {c_}"), n(f"{b} AND {c_}")]
    triple = n(f"{a} AND {b} AND {c_}")
    union = n(f"{a} OR {b} OR {c_}")
    if all(not isinstance(v, SearchError) for v in singles + pairs + [triple, union]):
        expected = sum(singles) - sum(pairs) + triple
        results.add(L5, "reported case: union count is exact",
                    f"{a} OR {b} OR {c_}", expected, union, expected == union,
                    f"|{a}|={singles[0]} |{b}|={singles[1]} |{c_}|={singles[2]} "
                    f"pairs={pairs} triple={triple} - three-set inclusion-exclusion, so "
                    f"the count is verified rather than just the parse")
    else:
        results.add(L5, "reported case: union count is exact", f"{a} OR {b} OR {c_}",
                    "a count", "a call failed", False)

    # --- an operator word used as a genuine word inside a phrase ----------------
    for label, query in PHRASE_CASES:
        try:
            metadata, _ = client.search(query)
        except SearchError as exc:
            results.add(L5, f"{label}: answers", query, "a page of records",
                        f"HTTP {exc.status}", False)
            continue
        parsed = _parsed_keywords(metadata)
        inner = query.strip('"')
        results.add(L5, f"{label}: stays one literal phrase", query, [inner], parsed,
                    parsed == [inner],
                    "an operator word inside quotes is text the recruiter typed, not an "
                    "instruction to the parser")


def _parsed_keywords(metadata):
    """The keyword list the service says it derived from the query."""
    raw = metadata.get("queryUsed")
    if not raw:
        return []
    try:
        return [str(k) for k in (json.loads(raw).get("keywords") or [])]
    except (json.JSONDecodeError, TypeError):
        return []


def _negated_terms(query):
    return {t.lower().strip('"') for t, negated in terms(parse(query)) if negated}


# =================================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="ai4")
    parser.add_argument("--layer", type=int, choices=[1, 2, 3, 4, 5], action="append")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--xlsx", default="ai_sourcing_boolean_results.xlsx")
    args = parser.parse_args()

    layers = args.layer or [1, 2, 3, 4, 5]
    client = SearchClient(env=args.env)
    results = Results()
    started = time.time()

    print(f"AI Sourcing V3 boolean search validation - env {args.env}")
    if 1 in layers:
        print("  layer 1: count algebra ...")
        layer1(client, results)
    if 3 in layers:
        print("  layer 3: malformed and edge input ...")
        layer3(client, results)
    if 4 in layers:
        print("  layer 4: combined with other filters ...")
        layer4(client, results)
    if 5 in layers:
        print("  layer 5: operator-substring collisions ...")
        layer5(client, results)
    if 2 in layers:
        print("  layer 2: record validation (~12s a query) ...")
        layer2(client, results, args.limit)

    results.report()
    elapsed = time.time() - started
    json.dump(results.rows, open("results.json", "w"), indent=2)

    from xlsx_report import write_report
    write_report(results.rows, args.xlsx, env=args.env, elapsed=elapsed)
    print(f"  elapsed {elapsed:.0f}s | results.json | {args.xlsx}")
    return 1 if results.by_status("FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())

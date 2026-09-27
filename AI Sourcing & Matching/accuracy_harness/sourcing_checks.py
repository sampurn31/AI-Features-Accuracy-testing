"""
Deterministic + metamorphic checks for AI Sourcing V3 scored results.

REAL response schema (confirmed against prod, and it differs from LLD 4015292441):
each matchAnalysis row is {criterionText, weight, score (0-100), reasoning}. There is
NO boolean `satisfied` field — the per-criterion signal is a 0-100 `score`. The overall
`matchScore` is the WEIGHTED AVERAGE of those per-criterion scores:
    matchScore ≈ round( Σ(score_i · weight_i) / Σ(weight_i) )
(verified exact on prod: 90·9+100·8+100·7+95·6 /30 = 96.0 -> matchScore 96).

These checks are the OBJECTIVE anchor. The external corpus is non-deterministic, so we
don't assert absolute scores — we assert relationships that must always hold:
  1. ranking_monotonic        — results are reordered by score, so matchScore is non-increasing.
  2. overall_score_consistent — matchScore matches the weighted avg of per-criterion scores (±tol).
  3. per_criterion_score_range— each per-criterion score is within 0-100.
  4. criteria_coverage        — every supplied criterion appears once, weight preserved.
  5. analysis_shape           — each row is well-formed (criterionText / weight / numeric score / reasoning).
  6. label_not_contradictory  — matchLabel must not grossly contradict matchScore.

Each function returns a list of human-readable issue strings ([] == pass).
"""
import json
import re

import config


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def weighted_avg(analysis):
    """Σ(score·weight)/Σweight over per-criterion scores. None if no usable score/weight."""
    tot_w, acc = 0.0, 0.0
    for a in analysis or []:
        w, s = a.get("weight"), a.get("score")
        if isinstance(w, (int, float)) and isinstance(s, (int, float)):
            tot_w += w
            acc += s * w
    if tot_w <= 0:
        return None
    return acc / tot_w


def ranking_monotonic(candidates):
    """matchScore must be non-increasing in streamed (rank) order."""
    issues, prev = [], None
    for c in candidates:
        s = c.get("matchScore")
        if s is None:
            continue
        if prev is not None and s > prev + 1e-9:
            issues.append(f"rank inversion at index {c.get('candidateIndex')}: score {s} > previous {prev}")
        prev = s
    return issues


def overall_score_consistent(candidate):
    """matchScore must equal the weighted average of per-criterion scores (within tolerance)."""
    analysis = candidate.get("matchAnalysis") or []
    score = candidate.get("matchScore")
    if score is None or not analysis:
        return []
    exp = weighted_avg(analysis)
    if exp is None:
        return []
    if abs(score - exp) > config.SOURCING_SCORE_TOL:
        return [f"idx {candidate.get('candidateIndex')}: matchScore {score} != weighted-avg "
                f"{exp:.1f} of per-criterion scores (Δ{abs(score - exp):.1f} > {config.SOURCING_SCORE_TOL})"]
    return []


def per_criterion_score_range(candidate):
    issues = []
    idx = candidate.get("candidateIndex")
    for a in (candidate.get("matchAnalysis") or []):
        s = a.get("score")
        if s is None:
            continue
        if not isinstance(s, (int, float)) or s < 0 or s > 100:
            issues.append(f"idx {idx}: per-criterion score out of 0-100: {s!r} for '{a.get('criterionText')}'")
    return issues


def criteria_coverage(candidate, supplied_criteria):
    """Every supplied criterion present once in matchAnalysis, weight preserved; none invented."""
    analysis = candidate.get("matchAnalysis")
    if analysis is None:
        return []  # unscored — coverage not applicable
    issues = []
    idx = candidate.get("candidateIndex")
    want_text = {_norm(c.get("text")) for c in supplied_criteria}
    got_text = {_norm(a.get("criterionText")) for a in analysis}
    if want_text == got_text:
        return []
    if len(analysis) != len(supplied_criteria):
        issues.append(f"idx {idx}: analysis has {len(analysis)} rows for "
                      f"{len(supplied_criteria)} supplied criteria")
    want_w = sorted(c.get("weight") for c in supplied_criteria)
    got_w = sorted(a.get("weight") for a in analysis)
    if want_w != got_w:
        issues.append(f"idx {idx}: criterion weights {got_w} != supplied {want_w}")
    return issues


def analysis_shape(candidate):
    """When scored, each analysis row must be well-formed (criterionText / weight / numeric score / reasoning)."""
    analysis = candidate.get("matchAnalysis")
    if analysis is None:
        return []
    issues, idx = [], candidate.get("candidateIndex")
    if not analysis:
        return [f"idx {idx}: matchAnalysis is empty though scoring applied"]
    for a in analysis:
        if not _norm(a.get("criterionText")):
            issues.append(f"idx {idx}: analysis row missing criterionText")
        if not isinstance(a.get("score"), (int, float)):
            issues.append(f"idx {idx}: per-criterion 'score' not numeric ({a.get('score')!r})")
        if not _norm(a.get("reasoning")):
            issues.append(f"idx {idx}: empty reasoning for '{a.get('criterionText')}'")
    return issues


_STRONG_WORDS = ("strong", "excellent", "great", "high", "top")
_WEAK_WORDS = ("weak", "low", "poor", "partial", "limited")


def label_not_contradictory(candidate):
    label = _norm(candidate.get("matchLabel"))
    score = candidate.get("matchScore")
    if not label or score is None:
        return []
    idx = candidate.get("candidateIndex")
    if any(w in label for w in _STRONG_WORDS) and score < 50:
        return [f"idx {idx}: label '{candidate.get('matchLabel')}' but score {score}"]
    if any(w in label for w in _WEAK_WORDS) and score > 70:
        return [f"idx {idx}: label '{candidate.get('matchLabel')}' but score {score}"]
    return []


def run_all(candidates, supplied_criteria):
    """Run every deterministic check over a result set. Returns (passed:bool, issues:list)."""
    issues = ranking_monotonic(candidates)
    for c in candidates:
        issues += overall_score_consistent(c)
        issues += per_criterion_score_range(c)
        issues += criteria_coverage(c, supplied_criteria)
        issues += analysis_shape(c)
        issues += label_not_contradictory(c)
    return (not issues), issues


# --- Metamorphic: same query, criteria C vs C+extra, track candidates by id ----

def _extra_score(candidate, extra_text):
    et = _norm(extra_text)
    for a in (candidate.get("matchAnalysis") or []):
        if _norm(a.get("criterionText")) == et:
            return a.get("score")
    return None


def metamorphic_extra_criterion(run_a, run_b, extra_text):
    """
    Same aiSearch, run_b added one extra weighted criterion. Direction invariant, per
    candidate present in both runs — consistent with matchScore = weighted average:
      - extra criterion scored ABOVE the candidate's base matchScore -> overall must not DROP
      - extra criterion scored BELOW it                              -> overall must not RISE
    Returns list of violations. Candidates missing from either run are skipped (pool churn).
    """
    tol = config.SOURCING_SCORE_TOL
    a_by_id = {c.get("id"): c for c in run_a if c.get("id") is not None}
    issues = []
    for cb in run_b:
        cid = cb.get("id")
        ca = a_by_id.get(cid)
        if ca is None:
            continue
        sa, sb = ca.get("matchScore"), cb.get("matchScore")
        es = _extra_score(cb, extra_text)
        if sa is None or sb is None or es is None:
            continue
        if es > sa + tol and sb < sa - tol:
            issues.append(f"id {cid}: extra criterion scored {es} (>base {sa}) yet overall dropped {sa}->{sb}")
        if es < sa - tol and sb > sa + tol:
            issues.append(f"id {cid}: extra criterion scored {es} (<base {sa}) yet overall rose {sa}->{sb}")
    return issues


# ── Scale run: pool-level checks across pages 1-4 (structured failures) ──────

def run_pool_checks(pages, candidates, criteria, cohort):
    """
    Full validation of one variation's multi-page pool. `pages` is a list of
    {"page", "http", "metadata", "n_candidates"}; `candidates` is the concatenated
    slim list (each with "_page" set); `cohort` is "full" | "single" | "none".
    Returns a list of {"check": name, "detail": str} failure dicts ([] = all pass).
    """
    fails = []

    def add(check, details):
        fails.extend({"check": check, "detail": d} for d in details)

    scored = cohort != "none"
    # 1. ordering across the WHOLE pool (pages concatenated in fetch order)
    if scored:
        add("ranking_monotonic", ranking_monotonic(candidates))
    # 2-5. per-candidate checks
    for c in candidates:
        if scored:
            add("score_formula", overall_score_consistent(c))
            add("score_range", per_criterion_score_range(c))
            add("criteria_coverage", criteria_coverage(c, criteria))
            add("analysis_shape", analysis_shape(c))
            add("label_consistency", label_not_contradictory(c))
    # 6. pagination integrity: the same PERSON must not appear twice across pages 1-4.
    # NOTE: the response `id` is NOT a person key (verified on prod: the same id maps to
    # different people on different pages) — identity is linkedinUrl, falling back to
    # normalized name+company.
    seen = {}
    for c in candidates:
        li = _norm(c.get("linkedinUrl"))
        key = ("li", li) if li else (
            "nc", _norm(f"{c.get('firstname')} {c.get('lastname')}"), _norm(c.get("company")))
        if key[-1] in ("", None) and not li:
            continue  # no usable identity — skip rather than guess
        if key in seen:
            prev_pg, prev_rank = seen[key]
            add("pagination_dup", [
                f"same person on page {prev_pg} (rank {prev_rank}) AND page {c.get('_page')} "
                f"(rank {c.get('_rank')}): {c.get('firstname')} {c.get('lastname')} — "
                f"{c.get('linkedinUrl') or c.get('company')}"])
        else:
            seen[key] = (c.get("_page"), c.get("_rank"))
    # 7. metadata consistency across pages — only pages that actually succeeded;
    # an errored page's metadata (e.g. the historyId error chunk) is not a data point
    metas = [(p["page"], p.get("metadata") or {}) for p in pages
             if p.get("metadata") and not p.get("error") and p.get("http") == 200]
    if metas:
        totals = {m.get("totalResults") for _, m in metas}
        if len(totals) > 1:
            add("meta_consistency", [f"totalResults differs across pages: "
                                     f"{[(pg, m.get('totalResults')) for pg, m in metas]}"])
        for pg, m in metas:
            sa = m.get("scoringApplied")
            if scored and sa is not True:
                add("meta_consistency", [f"page {pg}: scoringApplied={sa} though criteria were supplied"])
            if not scored and sa is True:
                add("meta_consistency", [f"page {pg}: scoringApplied=True though NO criteria were supplied"])
        # hasNextPage direction: if page N+1 actually returned candidates, page N must not say False
        got = {p["page"]: p.get("n_candidates", 0) for p in pages}
        for pg, m in metas:
            if m.get("hasNextPage") is False and got.get(pg + 1, 0) > 0:
                add("meta_consistency", [f"page {pg}: hasNextPage=False but page {pg+1} returned "
                                         f"{got[pg+1]} candidates"])
    # 8. cohort contracts
    if cohort == "none":
        for c in candidates:
            if c.get("matchScore") is not None or c.get("matchAnalysis") is not None \
               or c.get("matchLabel") is not None:
                add("cohort_none_null", [f"idx {c.get('candidateIndex')} (page {c.get('_page')}): scoring fields "
                                         f"present (score={c.get('matchScore')}) though no criteria supplied"])
    if cohort == "single":
        for c in candidates:
            analysis = c.get("matchAnalysis") or []
            ms = c.get("matchScore")
            if ms is None or not analysis:
                continue
            if len(analysis) != 1:
                add("cohort_single", [f"idx {c.get('candidateIndex')}: {len(analysis)} analysis rows for a "
                                      f"single supplied criterion"])
            elif isinstance(analysis[0].get("score"), (int, float)) \
                    and abs(ms - analysis[0]["score"]) > config.SOURCING_SCORE_TOL:
                add("cohort_single", [f"idx {c.get('candidateIndex')}: matchScore {ms} != its only criterion "
                                      f"score {analysis[0]['score']}"])
    return fails


# ── Scale run: retrieval-relevance heuristics vs the parsed queryUsed ────────

_SENIORITY_STOP = {"senior", "junior", "staff", "lead", "principal", "sr", "jr",
                   "ii", "iii", "iv", "i", "of", "the", "and"}


def _title_tokens(title):
    return {t for t in re.findall(r"[a-z][a-z+#./-]*", (title or "").lower())
            if t not in _SENIORITY_STOP and len(t) > 1}


def relevance_rates(candidates, metadata):
    """
    Cheap deterministic retrieval sanity vs the parsed structured query
    (metadata.queryUsed is a JSON string with job_titles/skills/locations).
    Returns {"n", "title_rate", "skill_rate", "loc_rate"} with rates in 0-1
    (None when that facet wasn't in the parsed query). Also stamps per-candidate
    booleans c["_title_m"] / c["_skill_m"] / c["_loc_m"] for the Candidates sheet.
    """
    out = {"n": len(candidates), "title_rate": None, "skill_rate": None, "loc_rate": None}
    try:
        q = json.loads((metadata or {}).get("queryUsed") or "{}")
    except (TypeError, ValueError):
        q = {}
    titles = [t for t in (q.get("job_titles") or []) if t]
    skills = [s.lower() for s in (q.get("skills") or []) if s]
    locs = [l.lower() for l in (q.get("locations") or []) if l]
    title_sets = [_title_tokens(t) for t in titles]
    tm = sm = lm = 0
    for c in candidates:
        cand_titles = [c.get("position") or ""] + [e.get("title") or "" for e in (c.get("experience") or [])]
        cand_tok = set().union(*[_title_tokens(t) for t in cand_titles]) if cand_titles else set()
        c["_title_m"] = bool(title_sets) and any(
            len(ts & cand_tok) >= (2 if len(ts) >= 2 else 1) for ts in title_sets)
        hay = " ".join(filter(None, [
            " ".join(c.get("keySkills") or []), c.get("description") or "",
            " ".join((e.get("description") or "") for e in (c.get("experience") or []))])).lower()
        c["_skill_m"] = bool(skills) and any(s in hay for s in skills)
        where = " ".join(filter(None, [c.get("city") or "", c.get("state") or "",
                                       c.get("country") or ""])).lower()
        c["_loc_m"] = bool(locs) and any(l in where or where and where in l for l in locs if l)
        tm += c["_title_m"]; sm += c["_skill_m"]; lm += c["_loc_m"]
    n = len(candidates) or 1
    if titles:
        out["title_rate"] = tm / n
    if skills:
        out["skill_rate"] = sm / n
    if locs:
        out["loc_rate"] = lm / n
    return out

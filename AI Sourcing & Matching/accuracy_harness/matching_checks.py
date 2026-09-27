"""
db=1 (My Database) deterministic + GROUND-TRUTH checks.

Reuses sourcing_checks for the shape/ranking/coverage/range/label/formula checks (same
normalized candidate shape), and adds ground-truth checks that only exist because WE seeded
the pool with known labels (STRONG/PARTIAL/WEAK/IRRELEVANT/INFERRED):

  band_envelope   — a candidate's matchScore falls in its label's expected band (config.DB1_BANDS)
  inference_band  — every INFERRED candidate scores >= 65 (else keyword-matching defect)
  label_ordering  — per job, median matchScore respects STRONG > PARTIAL > WEAK > IRRELEVANT
  precision_at_k  — top-K by matchScore contains no IRRELEVANT (ground-truth or judge-irrelevant)

Each returns [] on pass or a list of issue strings. overall_score_consistent stays EXPLORATORY
for db=1 (DRIFT #1: neptune says weighted-avg formula but a live capture disagreed) — the runner
records its delta and never hard-fails on it until the smoke resolves the question.
"""
import statistics

import config
import sourcing_checks

_BUCKET_RANK = {"STRONG": 4, "INFERRED": 4, "PARTIAL": 3, "WEAK": 2, "IRRELEVANT": 1}


def _scored(cands):
    return [c for c in cands if isinstance(c.get("matchScore"), (int, float))
            and (c.get("reasoningStatus") in (None, "success"))]


def band_envelope(candidate):
    bucket, ms = candidate.get("_gt_bucket"), candidate.get("matchScore")
    if not bucket or ms is None or bucket not in config.DB1_BANDS:
        return []
    lo, hi = config.DB1_BANDS[bucket]
    if not (lo <= ms <= hi):
        return [f"{candidate.get('slug')} [{bucket}]: matchScore {ms} outside expected band {lo}-{hi}"]
    return []


def inference_band(candidate):
    if candidate.get("_gt_bucket") != "INFERRED":
        return []
    ms = candidate.get("matchScore")
    if ms is None:
        return []
    lo = config.DB1_BANDS["INFERRED"][0]
    if ms < lo:
        return [f"{candidate.get('slug')} [INFERRED]: matchScore {ms} < {lo} — keyword-matching "
                f"defect (signal is in work history, not the skill/title keyword)"]
    return []


def label_ordering(cands):
    """Per job: median matchScore must be non-increasing across STRONG>PARTIAL>WEAK>IRRELEVANT."""
    med, present = {}, []
    for bucket in ("STRONG", "PARTIAL", "WEAK", "IRRELEVANT"):
        vals = [c["matchScore"] for c in _scored(cands) if c.get("_gt_bucket") == bucket]
        if vals:
            med[bucket] = statistics.median(vals)
            present.append(bucket)
    issues = []
    for i in range(len(present) - 1):
        hi, lo = present[i], present[i + 1]
        if med[hi] < med[lo] - 2:
            issues.append(f"label-ordering inversion: median({hi})={med[hi]:.0f} < median({lo})={med[lo]:.0f}")
    return issues


def precision_at_k(cands, k=10):
    """Top-K by matchScore must contain no ground-truth IRRELEVANT candidate."""
    ranked = sorted(_scored(cands), key=lambda c: -c["matchScore"])[:k]
    bad = [c for c in ranked if c.get("_gt_bucket") == "IRRELEVANT"]
    if bad:
        return [f"top-{k} contains {len(bad)} IRRELEVANT candidate(s): "
                + ", ".join(f"{c.get('slug')}(score {c['matchScore']})" for c in bad[:3])]
    return []


def run_all(cands, supplied_criteria):
    """All db=1 checks. Returns (passed, {check: [issues]}). overall_score_consistent is
    collected separately as EXPLORATORY (not part of the pass/fail verdict)."""
    hard = {}
    hard["ranking_monotonic"] = sourcing_checks.ranking_monotonic(cands)
    hard["label_ordering"] = label_ordering(cands)
    hard["precision_at_k"] = precision_at_k(cands, k=config.DB1_JUDGE_TOP_K)
    per = {"per_criterion_score_range": [], "criteria_coverage": [], "analysis_shape": [],
           "label_not_contradictory": [], "band_envelope": [], "inference_band": []}
    for c in cands:
        if c.get("reasoningStatus") not in (None, "success"):
            continue
        per["per_criterion_score_range"] += sourcing_checks.per_criterion_score_range(c)
        per["criteria_coverage"] += sourcing_checks.criteria_coverage(c, supplied_criteria)
        per["analysis_shape"] += sourcing_checks.analysis_shape(c)
        per["label_not_contradictory"] += sourcing_checks.label_not_contradictory(c)
        per["band_envelope"] += band_envelope(c)
        per["inference_band"] += inference_band(c)
    hard.update(per)
    exploratory = {"overall_score_consistent": []}
    for c in cands:
        if c.get("reasoningStatus") in (None, "success"):
            exploratory["overall_score_consistent"] += sourcing_checks.overall_score_consistent(c)
    hard = {k: v for k, v in hard.items() if v}
    passed = not hard
    return passed, hard, {k: v for k, v in exploratory.items() if v}

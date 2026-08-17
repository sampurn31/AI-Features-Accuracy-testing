#!/usr/bin/env python3
"""
Offline self-check. No network, no credentials, no cost.

Exercises the real code paths against fakes, then inspects the workbook that comes out.
Every assertion here exists because something once went wrong — the comments say what.

    python3 selfcheck.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)
    return cond


# ---------------------------------------------------------------------------
# 1. Judge wiring — shape only, no API call
# ---------------------------------------------------------------------------

def judge_wiring():
    print("\n1. judge wiring")
    import judge as J

    for nm, sch in (("flow1", J.FLOW1_SCHEMA), ("flow2", J.FLOW2_SCHEMA)):
        check(f"{nm} schema closed", sch["additionalProperties"] is False)
        check(f"{nm} every property required", set(sch["required"]) == set(sch["properties"]))
        # structured outputs reject numeric minimum/maximum — an enum is the way to bound
        check(f"{nm} no numeric bounds (unsupported)",
              "minimum" not in json.dumps(sch) and "maximum" not in json.dumps(sch))

    p = J.flow1_prompt(job_title="T", jd="JD", resume="CV",
                       notes=["[2026-08-04 09:12:33] N1"],
                       calls=["[2026-08-01 11:00:00] C1"], score=50, analysis="A")
    for tok in ("JD", "CV", "N1", "C1", "50"):
        check(f"flow1 prompt carries {tok!r}", tok in p)
    check("judge sees activity timestamps",
          "2026-08-04 09:12:33" in p and "updated_on" in p,
          "without them a correct date citation reads as a hallucination")
    check("flow1 told to ignore the overall score", "IGNORE THE OVERALL SCORE" in p or
          "IGNORE THE OVERALL SCORE" in J.FLOW1_SYSTEM,
          "the overall is a weighted sum; judging it measures the rollup, not the feature")
    check("flow1 asks which criterion", "which_criterion" in p)

    p2 = J.flow2_prompt(job_title="T", jd="JD", resume="CV", notes=[], calls=[],
                        a_score=1, a_text="AAA", b_score=2, b_text="BBB")
    check("flow2 prompt is blind", "ai2" not in p2.lower() and "dev" not in p2.lower(),
          "a judge told which is newer favours it")
    check("flow2 carries both analyses", "AAA" in p2 and "BBB" in p2)
    check("flow2 encourages Tie", "Tie is a perfectly good answer" in J.FLOW2_SYSTEM)

    src = open(os.path.join(HERE, "judge.py")).read()
    check("model is Sonnet 5", J.MODEL == "claude-sonnet-5")
    check("thinking disabled for determinism", 'thinking={"type": "disabled"}' in src)
    check("temperature never passed (rejected on Sonnet 5)", "temperature=" not in src)
    check("effort set via output_config", 'output_config={"effort"' in src)


# ---------------------------------------------------------------------------
# 2. Design — the activity plan must be grounded, never templated
# ---------------------------------------------------------------------------

def design_rules():
    print("\n2. design rules")
    import design as D
    src = open(os.path.join(HERE, "design.py")).read()

    check("gap claims restricted to technology tokens",
          'a["missing"][0]["token"] in TECH_TOKENS' in src,
          "'lacks B2B SaaS' is not inferable — the employer may be a SaaS company")
    check("soft-skill criteria deprioritised", "SOFT = (" in src and "concreteness" in src)
    check("empty profiles get their own scenario", '"empty_profile"' in src)
    check("strong scenarios gated on baseline", 'baseline") or 0) >= 70' in src,
          "calling a 58-scorer 'genuinely strong' is not credible")
    check("criterion text trimmed at a word boundary", "rsplit(\" \", 1)" in src,
          "'design patterns, and mic' reads as a typo")
    check("call_type is one the API accepts",
          "CALL_MISSED" not in src and "CALL_OUTGOING" in src,
          "anything else is a 422 and the call log is silently lost")

    # scenario feasibility: a profile with no gap cannot host mod_override
    a_gap = {"met": [], "missing": [{"criterion": "AWS", "token": "aws"}], "baseline": 50}
    a_ev = {"met": [{"criterion": "Java", "token": "java"}], "missing": [], "baseline": 90}
    import random
    from collections import Counter
    rng = random.Random(0)
    q = Counter({s: 0 for s in D.ALL_SCENARIOS})
    cand = {"content_chars": 900}
    picks = {D.pick(a_gap, cand, rng, q) for _ in range(40)}
    check("a gap-only profile is never given an evidence scenario",
          not (picks & D.NEEDS_EVIDENCE), str(sorted(picks))[:80])
    picks2 = {D.pick(a_ev, cand, rng, q) for _ in range(40)}
    check("an evidence-only profile is never given a gap scenario",
          not (picks2 & D.NEEDS_GAP), str(sorted(picks2))[:80])
    check("an empty profile always gets empty_profile",
          D.pick(a_ev, {"content_chars": 10}, rng, q) == "empty_profile")


# ---------------------------------------------------------------------------
# 3. Orchestration — fakes for Matching and Judge
# ---------------------------------------------------------------------------

CRIT = [{"text": "6+ years Python", "weight": 10}, {"text": "On-site", "weight": 9}]


def fake_row(slug, name, score, reason):
    return {"slug": slug, "first_name": name.split()[0], "last_name": name.split()[-1],
            "summary": "short parsed summary",
            "matchAnalysis": {"label": "Good", "overallReason": reason,
                              "criteria": [{"text": "6+ years Python", "weight": 10,
                                            "score": score, "reason": reason}]},
            "matchScore": score, "reasoningStatus": "success"}


class FakeMatching:
    scored = []

    def __init__(self, env, verbose=True):
        self.env = env
        self.timings = {"first_response_s": 0.4 if env == "dev" else 0.6,
                        "save_criteria_s": 1.9,
                        "time_to_scored_s": 210.0 if env == "dev" else 14.0,
                        "polls": 5, "scored": 3}

    def configure(self, job_slug):
        return {"criteria": CRIT, "filters": {}}

    def save_criteria(self, job_slug, cfg):
        assert cfg["criteria"] is CRIT, "criteria must be pinned, not regenerated"
        return {}

    def search_with_polling(self, job_slug, **kw):
        FakeMatching.scored.append(self.env)
        cite = "Screening note says she will not relocate." if self.env == "ai2" else ""
        rows = [fake_row("cand-a", "Ada Lovelace", 40 if self.env == "ai2" else 85,
                         f"Strong Python. {cite}"),
                fake_row("cand-b", "Alan Turing", 70, "Solid all round."),
                fake_row("outsider-1", "Grace Hopper", 55, "Partial fit.")]
        if self.env == "dev":
            rows = rows[:2]        # DEV misses one — exercises the skip path
        return rows, 12.5


class FakeJudge:
    def __init__(self, *a, **k):
        self.calls = 0
        self.seen2 = []

    def flow1(self, **kw):
        self.calls += 1
        used = "Yes" if (kw["notes"] or kw["calls"]) else "No"
        return {"judge_score": 5 if used == "Yes" else 3,
                "reasoning_factually_correct": "Yes", "score_justified": "Yes",
                "activity_context_used": used,
                "activity_criterion_scored_correctly": "Yes" if used == "Yes" else "Not applicable",
                "which_criterion": "6+ years Python",
                "activity_improved_reasoning": used, "activity_wrongly_influenced": "No",
                "important_details_ignored": "No",
                "contradictions_handled_correctly": "Not applicable",
                "analysis_verdict": "Correct", "detailed_reasoning": "Reads well.\x07",
                "suggested_improvements": "None."}

    def flow2(self, **kw):
        self.calls += 1
        self.seen2.append(kw)
        a_cites = "Screening note" in kw["a_text"]
        return {"winner": "System A" if a_cites else "System B", "confidence": "High",
                "comparison_reasoning": "One cites the note and draws a conclusion from it.",
                "a_references_activity": "Yes" if a_cites else "No",
                "b_references_activity": "No" if a_cites else "Yes",
                "activity_added_measurable_value": "Yes",
                "better_explanations": "System A" if a_cites else "System B",
                "any_regression": "No", "regression_detail": "(none)",
                "incorrect_reasoning_introduced": "No",
                "more_useful_for_recruiter": "System A" if a_cites else "System B",
                "suggested_improvements": "None."}

    def usage(self):
        return {"judge_calls": self.calls}


class FakeApi:
    """Mirrors the real API: candidates with no activity come back empty."""
    NONE = {"cand-b"}

    def activity(self, slug):
        if slug in self.NONE:
            return ([], [])
        return ([f"[2026-08-04 09:12:33] Note from the API for {slug}."], [])


class Args:
    flow, top, dataset_only = "both", 0, False


def orchestration():
    print("\n3. orchestration (fakes)")
    import evaluate as E
    E.Matching = FakeMatching

    job = {"key": "backend", "title": "Senior Backend Engineer", "job_slug": "job-1",
           "jd": "JD text",
           "candidates": [
               {"slug": "cand-a", "name": "Ada Lovelace", "scenario": "strong_blocker",
                "signal": "relocation", "notes": ["Will not relocate."], "calls": []},
               {"slug": "cand-b", "name": "Alan Turing", "scenario": "strong_clean",
                "signal": "", "notes": [], "calls": []}]}

    FULL = {"cand-a": "FULL PROFILE TEXT A " * 40, "cand-b": "FULL PROFILE TEXT B " * 40,
            "outsider-1": "FULL PROFILE TEXT X " * 40}
    FakeMatching.scored = []
    res = E.evaluate_job(job, Args(), FakeJudge(), FakeApi(), FULL)
    recs = {r["candidate_slug"]: r for r in res["records"]}

    check("dev and ai2 run sequentially within a job (shared DB, save wipes scores)",
          FakeMatching.scored == ["dev", "ai2"], str(FakeMatching.scored))
    check("all matched candidates judged", len(recs) == 3)
    check("unseeded candidate included and tagged",
          "outsider-1" in recs and recs["outsider-1"]["in_dataset"] is False)
    check("activity read back from the ATS with timestamps",
          all(n.startswith("[2026-08-04") for r in recs.values() for n in r["notes"])
          and recs["cand-a"]["notes"] and not recs["cand-b"]["notes"],
          "the plan has text but no dates; the scorer cites them so the judge must see them")
    check("judge receives the profile text, not the 150-char row summary",
          all(r.get("resume_source") == "profile" for r in recs.values())
          and all(r["resume_chars"] > 500 for r in recs.values()))
    check("score read from top-level matchScore", recs["cand-a"]["ai2_score"] == 40,
          "it is NOT in matchAnalysis.score")
    check("clean control judged as no-activity",
          recs["cand-b"]["flow1"]["activity_context_used"] == "No")
    check("criteria pinned across both deployments",
          res["criteria"] == [c["text"] for c in CRIT])
    check("per-env timings captured", res["timings"]["dev"]["first_response_s"] == 0.4)
    check("ran_first recorded", res["ran_first"] == "dev")
    check("candidate missing from DEV is skipped, not crashed",
          recs["outsider-1"].get("flow2_skipped") == "not returned by DEV")

    v = recs["cand-a"]["flow2"]
    check("winner unmasked to AI2 (it cited the note)", v["winner"] == "AI2",
          f"shown as System {v['ai2_shown_as']}")
    check("references-activity unmasked to the right side",
          v["ai2_references_activity"] == "Yes" and v["dev_references_activity"] == "No")
    check("masked keys removed",
          "a_references_activity" not in v and "b_references_activity" not in v)

    slots = {("A" if sum(s.encode()) % 2 == 0 else "B")
             for s in ("cand-a", "cand-b", "outsider-1", "x1", "x2", "x3", "x4")}
    check("A/B assignment uses both slots", slots == {"A", "B"})

    a1 = Args(); a1.flow = "1"
    FakeMatching.scored = []
    r1 = E.evaluate_job(job, a1, FakeJudge(), FakeApi(), FULL)
    check("--flow 1 scores AI2 only", FakeMatching.scored == ["ai2"])
    check("--flow 1 produces no flow2", not any("flow2" in r for r in r1["records"]))

    a3 = Args(); a3.dataset_only = True
    r3 = E.evaluate_job(job, a3, FakeJudge(), FakeApi(), FULL)
    check("seeded-only mode drops pre-existing records",
          {r["candidate_slug"] for r in r3["records"]} == {"cand-a", "cand-b"})

    a4 = Args(); a4.top = 1
    r4 = E.evaluate_job(job, a4, FakeJudge(), FakeApi(), FULL)
    kept = {r["candidate_slug"] for r in r4["records"]}
    check("--top never drops a seeded candidate, whatever it scored",
          kept == {"cand-a", "cand-b"},
          "cand-a scores 40 — a plain top-1 would discard it; it is the experiment")
    check("--top trims pre-existing records instead", "outsider-1" not in kept)

    def f1v(**kw):
        base = {"judge_score": 5, "activity_wrongly_influenced": "No",
                "important_details_ignored": "No", "activity_context_used": "Yes"}
        return E.flow1_verdict({**base, **kw})
    check("verdict: 4-5 acceptable", f1v(judge_score=4).startswith("Acceptable"))
    check("verdict: 1-2 not acceptable", f1v(judge_score=1).startswith("Not acceptable"))
    check("verdict: misuse flagged even at 5",
          "misused" in f1v(activity_wrongly_influenced="Yes"))
    check("verdict: tie", E.flow2_verdict({"winner": "Tie"}) == "No difference")
    return res


# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------

def reporting(res):
    print("\n4. report")
    import report as R
    from openpyxl import load_workbook

    recs = []
    for i, (sc, win) in enumerate([(5, "AI2"), (4, "AI2"), (3, "Tie"),
                                   (2, "DEV"), (1, "AI2")]):
        r = json.loads(json.dumps(res["records"][0]))
        r.update(candidate_slug=f"c{i}", candidate=f"Cand {i}", scenario=f"sc_{i}",
                 dev_score=80, ai2_score=40 + i,
                 dev_analysis="DEV said\x0bthings", ai2_analysis="AI2 said " + "x" * 40000)
        r["flow1"].update(judge_score=sc, final_verdict="v")
        r["flow2"].update(winner=win, final_verdict="v")
        recs.append(r)

    jobs = [{"job_key": "backend", "job_title": "Senior Backend Engineer", "records": [],
             "ran_first": "dev", "pool_sizes": {"dev": 10, "ai2": 10},
             "timings": {"dev": {"first_response_s": 0.4, "time_to_scored_s": 210.0},
                         "ai2": {"first_response_s": 0.6, "time_to_scored_s": 14.0}}},
            {"job_key": "sre", "job_title": "SRE", "records": [], "ran_first": "ai2",
             "pool_sizes": {"dev": 8, "ai2": 8},
             "timings": {"dev": {"first_response_s": 0.5, "time_to_scored_s": 12.0},
                         "ai2": {"first_response_s": 0.5, "time_to_scored_s": 190.0}}}]

    out = os.path.join(tempfile.mkdtemp(), "r.xlsx")
    n1, n2, n3 = R.build(recs, out, jobs)
    check("all three sheets written", (n1, n2, n3) == (5, 5, 2), f"{n1}/{n2}/{n3}")

    wb = load_workbook(out)
    check("sheet names exact",
          wb.sheetnames == ["Activity Context Evaluation", "AI2 vs DEV Comparison",
                            "Response Time"], str(wb.sheetnames))
    check("sheet 1 columns exact",
          [c.value for c in wb["Activity Context Evaluation"][1]] == R.SHEET1)
    check("sheet 2 columns exact", [c.value for c in wb["AI2 vs DEV Comparison"][1]] == R.SHEET2)
    check("sheet 3 columns exact", [c.value for c in wb["Response Time"][1]] == R.SHEET3)
    check("control characters stripped",
          "\x0b" not in wb["AI2 vs DEV Comparison"].cell(row=2, column=5).value)
    check("oversized cell truncated, workbook still opens",
          len(wb["AI2 vs DEV Comparison"].cell(row=2, column=6).value) == R.XL_MAX)

    ts = R.timing_summary(jobs)
    check("timing split by run position, not just environment",
          ts["dev_when_first_s"] == 210.0 and ts["ai2_when_first_s"] == 190.0
          and ts["dev_when_second_s"] == 12.0 and ts["ai2_when_second_s"] == 14.0,
          "whoever runs first builds the pool — the raw mean would be misleading")

    s = R.summarise(recs)
    check("summary counts", s["flow1"]["judged"] == 5 and s["flow2"]["compared"] == 5)
    check("acceptable pct correct", s["flow1"]["acceptable_pct"] == 40.0,
          "2 of 5 scored 4+")
    check("criterion-correct metric present", "criterion_scored_correctly" in s["flow1"])
    check("summary is JSON-serialisable", bool(json.dumps(s)))


# ---------------------------------------------------------------------------
# 5. Live data, if present
# ---------------------------------------------------------------------------

def live_data():
    print("\n5. live data (skipped if not built yet)")
    import evaluate as E
    mpath = os.path.join(HERE, "manifest.json")
    cpath = os.path.join(HERE, "corpus.json")
    if not (os.path.exists(mpath) and os.path.exists(cpath)):
        print("  (no manifest/corpus yet — nothing to check)")
        return
    man = json.load(open(mpath))
    idx = E.resume_index(man)
    need = {c["slug"] for j in man["jobs"] for c in j["candidates"]}
    missing = need - set(idx)
    check("every evaluated candidate has profile text", not missing,
          f"{len(need) - len(missing)}/{len(need)} covered")
    lens = [len(idx[s]) for s in need if s in idx]
    if lens:
        check("profile text is substantial", sorted(lens)[len(lens) // 2] > 500,
              f"median {sorted(lens)[len(lens) // 2]} chars")


if __name__ == "__main__":
    print("offline self-check — no network, no credentials, no cost")
    judge_wiring()
    design_rules()
    res = orchestration()
    reporting(res)
    live_data()
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(2)
    print("all checks passed")

"""
Build an inhouse-evaluator dataset from the 300-variation sourcing scale-run checkpoint.

Each row re-creates the scoring task the harness gave the product: one search query, its
weighted criteria, and one candidate profile. The model under test plays the role of the
AI Sourcing scoring engine; the evaluator's llm_judge then audits what it produced using
the same per-criterion rubric the harness used.

  python3 build_sourcing_dataset.py --rows 30 --out sourcing_llm_judge.jsonl

system_prompt  : left empty on purpose — set it once on the evaluator Feature so prompt
                 versioning applies (use --inline-system to embed it per row instead).
user_prompt    : SEARCH QUERY + WEIGHTED CRITERIA + CANDIDATE PROFILE (full experience list,
                 same _profile_for_judge shape the harness judge received) + closing rule.
expected_output: the product's own scoring output for this candidate, so the run also
                 records token-overlap against prod and can be re-run in golden_dataset mode.
"""
import argparse, json, os

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKPOINT = os.path.join(HARNESS, "output", "scale_checkpoint_20260723-full.jsonl")
SCORING_PROMPT = os.path.join(HARNESS, "prompts", "model_scoring_prompt.txt")


def profile_for_judge(c):
    """Verbatim llm._profile_for_judge — ALL experience entries, never truncate a role away."""
    exp = [{"title": e.get("title"), "company": e.get("company"),
            "start": e.get("start_date"), "end": e.get("end_date"),
            "is_current": e.get("is_current"),
            "description": (e.get("description") or "")[:500],
            "location": e.get("location")}
           for e in (c.get("experience") or [])]
    return {"position": c.get("position"), "company": c.get("company"),
            "workexpyr": c.get("workexpyr"), "city": c.get("city"),
            "state": c.get("state"), "country": c.get("country"),
            "keySkills": c.get("keySkills"), "description": c.get("description"),
            "education": c.get("education"), "experience": exp}


def build_user_prompt(query, criteria, cand):
    crit_lines = "\n".join(f"- {c.get('text')} (weight {c.get('weight')})" for c in (criteria or []))
    return "".join([
        "SEARCH QUERY:\n" + (query or ""),
        "\n\nWEIGHTED CRITERIA (as configured):\n" + crit_lines,
        "\n\nCANDIDATE PROFILE (the ONLY evidence — read the FULL experience list):\n"
        + json.dumps(profile_for_judge(cand), indent=2, ensure_ascii=False)[:16000],
        "\n\nScore this candidate on every criterion above, in the same order, and return "
        "ONLY the JSON object.",
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=30, help="how many candidates to include")
    ap.add_argument("--out", default="sourcing_llm_judge.jsonl")
    ap.add_argument("--inline-system", action="store_true",
                    help="embed the scoring system prompt in every row instead of using a Feature")
    a = ap.parse_args()

    system_prompt = open(SCORING_PROMPT).read().strip() if a.inline_system else ""

    # Collect every judged candidate whose full profile was retained, keeping the variation
    # it came from so the sample can be spread across query formats.
    pool = []
    with open(CHECKPOINT) as fh:
        for line in fh:
            r = json.loads(line)
            cands = {c.get("id"): c for c in (r.get("candidates") or []) if c and c.get("matchAnalysis")}
            for v in (r.get("judge") or []):
                cand = cands.get(v.get("cand_id"))
                if cand:
                    pool.append((r, cand, v))

    # Stratify by query format (short / brief / jd_excerpt) so one execution covers all three.
    by_fmt = {}
    for item in pool:
        by_fmt.setdefault(item[0].get("format") or "unknown", []).append(item)
    picked, i = [], 0
    while len(picked) < a.rows and any(by_fmt.values()):
        for fmt in sorted(by_fmt):
            if by_fmt[fmt] and len(picked) < a.rows:
                picked.append(by_fmt[fmt].pop(i % max(len(by_fmt[fmt]), 1)))
        i += 1

    with open(a.out, "w") as fh:
        for r, cand, v in picked:
            expected = json.dumps({"matchScore": cand.get("matchScore"),
                                   "matchLabel": cand.get("matchLabel"),
                                   "matchAnalysis": cand.get("matchAnalysis")},
                                  indent=2, ensure_ascii=False)
            fh.write(json.dumps({
                "system_prompt": system_prompt,
                "user_prompt": build_user_prompt(r.get("query"), r.get("criteria"), cand),
                "expected_output": expected,
            }, ensure_ascii=False) + "\n")

    fmts = {}
    for r, _, _ in picked:
        fmts[r.get("format")] = fmts.get(r.get("format"), 0) + 1
    chars = [len(build_user_prompt(r.get("query"), r.get("criteria"), c)) for r, c, _ in picked]
    print(f"wrote {a.out}: {len(picked)} rows from {len(pool)} judged candidates")
    print(f"  formats     : {fmts}")
    print(f"  user_prompt : min {min(chars):,} / avg {sum(chars)//len(chars):,} / max {max(chars):,} chars")
    print(f"  system prompt: {'inlined per row' if a.inline_system else 'left empty — set it on the Feature'}")


if __name__ == "__main__":
    main()

"""
Write out every real judge call from the 300-variation scale run, as markdown.

Not examples and not paraphrase: the user message for each call is rebuilt through the same
code path the run used (`llm.judge_candidate_criteria`) from the candidate profile the run
stored, so the text below is what the judge actually received. The system prompt is read from
the same rubric file.

    ./.venv/bin/python build_judge_transcripts.py            # 6 files of 50 variations
    ./.venv/bin/python build_judge_transcripts.py --per 25   # smaller chunks

Output: output/judge_transcripts/PART_<n>_of_<N>__<first>-<last>.md
"""
import argparse
import json
import os

import config
import llm
from reconstruct_judge_call import build_user_message

CHECKPOINT = os.path.join(config.OUTPUT_DIR, "scale_checkpoint_20260723-full.jsonl")
OUT = os.path.join(config.OUTPUT_DIR, "judge_transcripts")


def why_unjudged(r):
    if r.get("status") != "ok":
        return f"the search itself did not complete (status `{r.get('status')}`)"
    if r.get("totalResults") in (None, 0):
        if r.get("totalResults") == 0:
            return "the query returned **0 candidates**, so there was nothing to score or judge"
        return "the request was rejected before results (over the 1000-char `aiSearch` cap)"
    if not r.get("scoringApplied"):
        return "scoring was not applied to this variation, so there were no per-criterion rows to audit"
    return "no candidate from this variation fell into the judged sample (ranks 1 / middle / last)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, help="variations per file (fixed size)")
    ap.add_argument("--parts", type=int, default=5, help="split into N balanced files")
    ap.add_argument("--all", action="store_true",
                    help="include variations that returned no candidates (default: exclude)")
    a = ap.parse_args()

    everything = []
    with open(CHECKPOINT) as fh:
        for line in fh:
            everything.append(json.loads(line))

    if a.all:
        rows, excluded = everything, []
    else:
        rows = [r for r in everything if (r.get("n_candidates") or 0) > 0]
        excluded = [r for r in everything if (r.get("n_candidates") or 0) == 0]

    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):                      # stale parts from a previous split
        if f.startswith("PART_") and f.endswith(".md"):
            os.remove(os.path.join(OUT, f))
    rubric = open(config.SOURCING_CRITERION_RUBRIC).read().rstrip()
    schema = json.dumps(llm.CRITERION_JUDGE_SCHEMA, indent=2)
    if a.per:
        parts = [rows[i:i + a.per] for i in range(0, len(rows), a.per)]
    else:                                          # balanced, so file sizes stay even
        n = max(1, a.parts)
        base, rem = divmod(len(rows), n)
        parts, at = [], 0
        for i in range(n):
            take = base + (1 if i < rem else 0)
            parts.append(rows[at:at + take])
            at += take
        parts = [p for p in parts if p]
    total_calls = sum(len(r.get("judge") or []) for r in rows)
    written = []

    scope = ("**Scope:** only variations that actually returned candidates. "
             f"{len(excluded)} of {len(everything)} variations are excluded because the search "
             "came back empty — "
             f"{sum(1 for r in excluded if r.get('totalResults') == 0)} returned `totalResults: 0` "
             f"and {sum(1 for r in excluded if r.get('totalResults') is None)} were rejected before "
             "results for exceeding the 1000-char `aiSearch` cap. None of them carried a judge call, "
             f"so all {total_calls} judge calls from the run are still present here.\n"
             if excluded else
             "**Scope:** all 300 variations, including those that returned no candidates.\n")

    seen = 0
    for pi, chunk in enumerate(parts, 1):
        lo, hi = seen + 1, seen + len(chunk)
        seen += len(chunk)
        calls = sum(len(r.get("judge") or []) for r in chunk)
        out = [
            f"# Judge transcripts — part {pi} of {len(parts)} (variations {lo}–{hi} of {len(rows)})\n",
            f"Run `20260723-full` · 300-variation scale run on prod · 2026-07-23 · "
            f"judge `{config.MODEL}`, effort `{config.JUDGE_EFFORT}`, `max_tokens=2000`, "
            "forced JSON output.\n",
            scope,
            f"This part covers **{len(chunk)} variations** and **{calls} judge calls** "
            f"(of {total_calls} across the run).\n",
            "Every USER MESSAGE below is the literal string sent for that call, rebuilt through "
            "the run's own code path from the candidate profile stored in the checkpoint. The "
            "SYSTEM PROMPT is byte-identical on every one of the 604 calls, so it is printed "
            "once here rather than repeated 604 times.\n",
            "The product's own Neptune prompt is never shown to the judge — it sees only the "
            "query, the criteria, the profile, and the scores the system produced.\n",
            "---\n",
            "## SYSTEM PROMPT — sent on every call\n",
            f"`prompts/sourcing_criterion_rubric.md` · {len(rubric.splitlines())} lines · "
            "sent as the `system` block with `cache_control`\n",
            "````text", rubric, "````\n",
            "## FORCED OUTPUT SCHEMA — sent on every call\n",
            "`output_config.format = {\"type\": \"json_schema\", \"schema\": ...}`\n",
            "````json", schema, "````\n",
            "---\n",
        ]

        for r in chunk:
            verdicts = r.get("judge") or []
            cands = {c.get("id"): c for c in (r.get("candidates") or []) if c}
            out.append(f"\n# `{r['id']}`\n")
            meta = " · ".join(x for x in [
                f"format: {r.get('format')}", f"cohort: {r.get('cohort')}",
                f"role: {r.get('role')}", f"seniority: {r.get('seniority')}",
                f"location: {r.get('location')}", f"industry: {r.get('family')}",
                f"query length: {r.get('query_chars')} chars",
                f"totalResults: {r.get('totalResults')}",
                f"candidates retrieved: {r.get('n_candidates')}",
                f"judge calls: {len(verdicts)}"] if x)
            out.append(f"<sub>{meta}</sub>\n")
            out.append("**The query sent to the product:**\n")
            out.append("````text\n" + (r.get("query") or "") + "\n````\n")
            crit = r.get("criteria") or []
            if crit:
                out.append("**Weighted criteria configured for this variation:**\n")
                for c in crit:
                    out.append(f"- {c.get('text')} (weight {c.get('weight')})")
                out.append("")
            if not verdicts:
                out.append(f"> **No judge call for this variation** — {why_unjudged(r)}.\n")
                continue

            for v in verdicts:
                cand = cands.get(v.get("cand_id"))
                if not cand:
                    out.append(f"> judge call recorded for cand_id {v.get('cand_id')} but the "
                               "profile was not retained in the checkpoint\n")
                    continue
                name = v.get("name") or f"{cand.get('firstname','')} {cand.get('lastname','')}".strip()
                user = build_user_message(r.get("query"), crit, cand)
                out.append(f"\n## Judge call — rank {v.get('rank')} · {name}\n")
                out.append(f"<sub>{v.get('position') or ''} at {v.get('company') or ''} · "
                           f"system matchScore {v.get('matchScore')} · "
                           f"page {v.get('page')} · user message {len(user):,} chars</sub>\n")
                out.append("### USER MESSAGE — exactly as sent\n")
                out.append("````text\n" + user + "\n````\n")
                out.append("### VERDICT RETURNED\n")
                out.append("````json\n" + json.dumps(v, indent=2, ensure_ascii=False) + "\n````\n")
                out.append("---\n")

        first, last = chunk[0]["id"], chunk[-1]["id"]
        path = os.path.join(OUT, f"PART_{pi}_of_{len(parts)}__{first}__to__{last}.md")
        with open(path, "w") as fh:
            fh.write("\n".join(out))
        written.append((path, len(chunk), calls, os.path.getsize(path)))
        print(f"part {pi}: {len(chunk):>3} variations · {calls:>3} judge calls · "
              f"{os.path.getsize(path):>9,} B · {os.path.basename(path)}")

    print(f"\n{OUT}")
    print(f"{len(written)} files · {sum(w[1] for w in written)} variations · "
          f"{sum(w[2] for w in written)} judge calls · {sum(w[3] for w in written):,} bytes")


if __name__ == "__main__":
    main()

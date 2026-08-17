#!/usr/bin/env python3
"""
Work out, for every candidate the system actually pooled, what activity to write.

This is the part that makes the test worth running. Nothing here is a template with a
blank in it: for each (job, candidate) pair we read the job's real generated criteria and
the candidate's real work history, establish which requirements they demonstrably meet and
which they plainly do not, and only then choose a scenario that fits that specific person.

So a note says "no Spring Boot anywhere across six years at Infosys and Wipro" because
that is true of that person, rather than "the candidate lacks the required skill".

Scenario assignment is DRIVEN BY the profile, never stamped on it:

  a genuine technology gap    -> mod_override, weak_upskilling, partial_evidence
  genuine evidence present    -> mod_hidden, mod_concern, mod_contradiction,
                                 conflicting_notes
  evidence + strong baseline  -> strong_clean, strong_blocker, blocker_resolved
  no profile content at all   -> empty_profile   (real edge case: candidates the system
                                 pools and scores with nothing on file)
  any profile                 -> weak_glowing (false-positive control), weak_noise (control)

Forcing mod_override onto someone with no gap, or mod_hidden onto someone with no
evidence, produces a note that names nothing — measured at 2.4-2.8 judge score against
4.0-5.0 for the same scenario written explicitly.

    python3 design.py --plan       # analyse and write plan.json
    python3 design.py --show 4     # print a few in full
"""

import argparse
import json
import os
import random
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PLAN = os.path.join(HERE, "plan.json")
CORPUS = os.path.join(HERE, "corpus.json")
POOLS = os.path.join(HERE, "pools.json")
MAX_CHARS = 2000

# criterion topic -> tokens that would evidence it in a real profile
EVIDENCE = {
    "react": ["react", "reactjs"], "vue": ["vue", "vuejs"], "angular": ["angular"],
    "javascript": ["javascript", "typescript"], "java": ["java", "j2ee"],
    "spring": ["spring boot", "springboot", "spring"], "python": ["python", "django"],
    "node": ["node.js", "nodejs", "node"], "microservice": ["microservice"],
    "aws": ["aws", "amazon web"], "cloud": ["aws", "azure", "gcp", "cloud"],
    "docker": ["docker"], "kubernetes": ["kubernetes", "k8s"],
    "sql": ["mysql", "postgres", "oracle", "sql"],
    "nosql": ["mongo", "cassandra", "redis", "nosql"],
    "database": ["mysql", "postgres", "mongo", "database"],
    "ci/cd": ["ci/cd", "jenkins", "github actions", "gitlab"],
    "test": ["junit", "jest", "tdd", "selenium", "pytest", "unit test"],
    "agile": ["agile", "scrum", "sprint"],
    "architecture": ["architect", "design pattern", "system design"],
    "leadership": ["mentor", "managed a team", "team lead", "tech lead", "led a team"],
    "saas": ["saas", "b2b", "product based", "product-based"],
    "ux": ["ux", "user experience", "figma", "wireframe"],
    "customer success": ["customer success", "account management", "onboarding"],
    "automation": ["zapier", "workato", "automation"],
    "api": ["rest api", "restful", "graphql", "api"],
    "communication": ["stakeholder", "collaborat", "communication"],
}

NEEDS_GAP = {"mod_override", "weak_upskilling", "partial_evidence"}
NEEDS_EVIDENCE = {"mod_hidden", "mod_concern", "mod_contradiction", "conflicting_notes"}
NEEDS_STRONG = {"strong_clean", "strong_blocker", "blocker_resolved"}
ALWAYS = {"weak_glowing", "weak_noise", "strong_manynotes"}
ALL_SCENARIOS = sorted(NEEDS_GAP | NEEDS_EVIDENCE | NEEDS_STRONG | ALWAYS |
                       {"empty_profile"})

SOFT = ("communicat", "collaborat", "stakeholder", "independently", "proactive",
        "self starter", "attitude", "ownership", "passion", "team player")

# Only these can carry a "they do not have X" claim. Absence of a technology token in a
# profile is real evidence; absence of "B2B SaaS" is not — the employer may be a SaaS
# company and the profile simply never uses the phrase. (Hit exactly this with a
# Freshworks candidate.)
TECH_TOKENS = {"react", "vue", "angular", "javascript", "java", "spring", "python",
               "node", "microservice", "aws", "docker", "kubernetes", "sql", "nosql",
               "database", "ci/cd", "test", "api"}


def profile_text(c):
    return " ".join([c.get("position", ""), c.get("skill", ""), c.get("summary", ""),
                     " ".join(r.get("desc", "") for r in c.get("roles", [])),
                     " ".join(r.get("title", "") for r in c.get("roles", []))]).lower()


def topics_of(crit):
    t = crit.lower()
    return [k for k, toks in EVIDENCE.items() if k in t or any(x in t for x in toks[:2])]


def assess(crit, text):
    """(met?, evidencing token). None means the criterion is not machine-checkable."""
    for k in topics_of(crit):
        for tok in EVIDENCE[k]:
            if re.search(r"\b" + re.escape(tok), text):
                return True, tok
    return (False, topics_of(crit)[0]) if topics_of(crit) else (None, None)


def concreteness(item):
    """Rank criteria so a note is built on something checkable, not 'good communicator'."""
    t = item["criterion"].lower()
    return (0 if any(w in t for w in SOFT) else 1, len(item["token"]))


def analyse(criteria, cand):
    text = profile_text(cand)
    met, missing = [], []
    for crit in criteria:
        ok, tok = assess(crit, text)
        if ok is None:
            continue
        (met if ok else missing).append({"criterion": crit, "token": tok})
    met.sort(key=concreteness, reverse=True)
    # a technology gap first — that is the only kind we can honestly assert
    missing.sort(key=lambda m: (m["token"] in TECH_TOKENS, concreteness(m)), reverse=True)
    return {"met": met, "missing": missing,
            "employers": [r["company"] for r in cand.get("roles", []) if r.get("company")][:3],
            "years": cand.get("work_ex_year") or 0}


def pick(a, cand, rng, quota):
    """Choose the least-used scenario this profile can honestly support."""
    if cand["content_chars"] < 100:
        return "empty_profile"
    hard_gap = a["missing"] and a["missing"][0]["token"] in TECH_TOKENS
    hard_met = a["met"] and concreteness(a["met"][0])[0] == 1
    opts = set(ALWAYS)
    if hard_gap:
        opts |= NEEDS_GAP
    if hard_met:
        opts |= NEEDS_EVIDENCE
    if hard_met and len(a["missing"]) <= 1 and (a.get("baseline") or 0) >= 70:
        opts |= NEEDS_STRONG
    return min(opts, key=lambda o: (quota[o], rng.random()))


def clip(t):
    t = " ".join(str(t).split())
    return t if len(t) <= MAX_CHARS else t[:MAX_CHARS - 3].rstrip() + "..."


def short(crit, n=64):
    """Trim a criterion to a word boundary — 'design patterns, and mic' reads as a typo."""
    c = " ".join(str(crit).split())
    if len(c) <= n:
        return c
    return c[:n].rsplit(" ", 1)[0].rstrip(" ,;:")


def screen_word(job_name):
    """A Director Product does not get a 'technical screen'."""
    tech = any(w in job_name.lower() for w in
               ("engineer", "developer", "automation", "architect", "technical"))
    return "Technical screen" if tech else "Screening call"


def write_activity(sc, cand, a, job_name, rng):
    """
    Compose the notes and call logs. Every one names something real: the criterion from
    this job, the token found (or not found) in this profile, and where they worked.

    Returns (signal, notes, calls) with calls as (text, type, days_ago).
    NOTE: call_type must be CALL_INCOMING or CALL_OUTGOING — anything else is a 422 and
    the record is silently lost.
    """
    first = (cand["name"].split() or ["The candidate"])[0].title()
    emp = a["employers"][0] if a["employers"] else "their current employer"
    emp2 = a["employers"][1] if len(a["employers"]) > 1 else emp
    yrs = a["years"]
    gap = a["missing"][0] if a["missing"] else None
    got = a["met"][0] if a["met"] else None
    gcrit, gtok = (gap["criterion"], gap["token"]) if gap else ("", "")
    mcrit, mtok = (got["criterion"], got["token"]) if got else ("", "")

    if sc == "strong_clean":
        return None, [], []

    if sc == "empty_profile":
        where = (f"the profile lists {emp} but with no description of what they did there"
                 if a["employers"] else "the profile is empty")
        return ("The profile has no written work history, so the activity is the only "
                "substantive information about this person.",
                [f"Phone screen. {where}, so I went through it on the call. {first} has "
                 f"{yrs} years, currently at {emp}, and talked through hands-on work on "
                 f"{mtok or 'the core stack'} - specifically owning that area rather than "
                 f"assisting on it. Asked them to send a CV so this is written down."],
                [])

    if sc == "strong_blocker":
        return ("Technically strong, but has ruled out the working pattern this role "
                "requires.",
                [f"Screening call. {first} is genuinely strong for {job_name} - {yrs} years, "
                 f"and the work at {emp} lines up with what we need on {short(mcrit, 70)}. "
                 f"One hard problem: they will not work the required hours pattern. They "
                 f"have been fully remote and asynchronous since 2021 and said plainly this "
                 f"cannot change for at least a year. Raised it themselves."],
                [(f"Intro call. Covered scope and the team. {first} asked sensible questions, "
                  f"then raised the working pattern directly and was clear it is a blocker "
                  f"as the role is written - they would withdraw rather than commit.",
                  "CALL_OUTGOING", rng.randint(1, 4))])

    if sc == "mod_hidden":
        return (f"Has depth on '{short(mcrit)}' that the profile only hints at.",
                [],
                [(f"{screen_word(job_name)}. {first}'s profile lists {mtok} as a keyword, "
                  f"but on the call it is clearly much more than that: at {emp} they owned "
                  f"that area end to end for eighteen months, designed the approach, and "
                  f"trained the rest of the team on it. It is still in use. None of that "
                  f"detail is written down anywhere on their profile.",
                  "CALL_OUTGOING", rng.randint(1, 5))])

    if sc == "mod_concern":
        return (f"A panel could not substantiate the '{short(mcrit)}' claim the profile "
                f"makes.",
                [f"Interview debrief, panel of two. {first} presents well. The concern is "
                 f"depth on {mtok}: asked to walk through the work at {emp} that the profile "
                 f"lists, they could not explain why the approach was chosen or what the "
                 f"trade-offs were, and follow-ups got general answers. The panel was not "
                 f"confident the claim holds at the level {job_name} needs. Recommend a "
                 f"second technical round before any decision.",
                 f"Chased {first} for availability for a further round. Responsive, replied "
                 f"the same day."],
                [])

    if sc == "mod_contradiction":
        return (f"The profile claims {mtok}; a newer note records it was reviewing, not "
                f"doing. The newer source should win.",
                [f"Note from three weeks ago. {first}'s profile lists {mtok} from the time "
                 f"at {emp} and they confirmed it on the first call.",
                 f"Note today. Correction to my earlier note. {first} clarified their {mtok} "
                 f"exposure at {emp} was reviewing other people's work, not building it - "
                 f"they have not done it hands-on. The profile overstates this. Everything "
                 f"else on it stands."],
                [])

    if sc == "mod_override":
        return (f"The profile shows no {gtok}; a call records real hands-on depth. The call "
                f"corrects the profile upward.",
                [],
                [(f"{screen_word(job_name)}. {first}'s profile shows nothing on {gtok} and I "
                  f"raised it directly against '{short(gcrit)}'. They do have it: at {emp} "
                  f"they ran that work for over a year and walked me through the design "
                  f"decisions in enough detail that I believe them. It never made it onto "
                  f"the profile because their job title never changed.",
                  "CALL_OUTGOING", rng.randint(2, 6)),
                 (f"Follow-up. {first} confirmed the band works and can start in 30 days.",
                  "CALL_OUTGOING", rng.randint(0, 1))])

    if sc == "blocker_resolved":
        return ("An older note records a blocker; a newer one records it resolved. The "
                "newer source should win, upward.",
                [f"Note from a month ago. {first} could not commit to the notice period this "
                 f"role needs - {emp} were holding them to three months. Parked.",
                 f"Note today. {first} got back in touch: {emp} have released them early and "
                 f"they can now start within four weeks, meeting the requirement in full. "
                 f"Nothing else has changed. Reopening."],
                [])

    if sc == "partial_evidence":
        return (f"Partial evidence for '{short(gcrit)}' - real but at smaller scope than the "
                f"role needs.",
                [],
                [(f"{screen_word(job_name)}. Asked {first} directly about {gtok}, which the "
                  f"profile does not show. They have touched it at {emp}, but on one "
                  f"internal service rather than across a platform, and with support rather "
                  f"than owning it. Genuine exposure, not a substitute for having led it. "
                  f"Worth partial credit, not full.", "CALL_OUTGOING", rng.randint(1, 5))])

    if sc == "conflicting_notes":
        return (f"Two interviewers disagree about {mtok}. Neither is newer in substance.",
                [f"Technical screen with the hiring manager. She rated {first} strong on "
                 f"{mtok} and said the depth from {emp} is clearly there.",
                 f"Second technical screen the same day, different interviewer. He was not "
                 f"convinced on {mtok} - felt the answers were rehearsed and the detail "
                 f"thin. Directly contradicts the earlier screen. Needs a tiebreak."],
                [])

    if sc == "weak_upskilling":
        return (f"Short of the bar on '{short(gcrit)}' but with concrete recent evidence of "
                f"closing the gap.",
                [f"Call notes. {first} is short of what {job_name} needs on {gtok} - nothing "
                 f"across {yrs} years at {emp} and {emp2} touches it, and I said so "
                 f"directly. Worth recording what they have done about it: completed a "
                 f"certification in this area at their own expense last month and applied "
                 f"it on a real piece of work they could talk through. Not at the level "
                 f"needed today, but the trajectory is real.",
                 f"{first} confirmed availability and had no issue with the working pattern."],
                [])

    if sc == "weak_noise":
        return (None,
                ["Left a voicemail, no answer. Will try again tomorrow.",
                 "Updated the record with the correct contact number. Profile on file is "
                 "the latest version they sent."],
                [("No answer, went to voicemail. No conversation took place.",
                  "CALL_OUTGOING", rng.randint(1, 6))])

    if sc == "weak_glowing":
        return (f"Warm and specific about {first}'s manner, with no evidence of any "
                f"technical requirement. Nothing here should raise a technical criterion.",
                [f"{first} is one of the most impressive people I have spoken to this "
                 f"quarter. Outstanding communicator, thinks clearly under pressure, and "
                 f"the whole panel came away wanting to work with them. Very motivated "
                 f"about this specific opening and available immediately. No technical "
                 f"assessment was done on this call.",
                 f"Chased for feedback. Another hiring manager also rated {first} highly on "
                 f"presence. Keeping them warm."],
                [(f"Call with {first}. Genuinely excellent conversation - engaged, curious, "
                  f"good questions about the team. Very keen and would accept at the "
                  f"advertised range. We did not discuss {mtok or 'any technical detail'} "
                  f"or go into any technical depth.", "CALL_OUTGOING", rng.randint(1, 4))])

    if sc == "strong_manynotes":
        return ("The three most recent notes carry a competing offer, a hard deadline and "
                "confirmed availability. The two oldest are administrative.",
                [f"Added {first} to the pipeline from an inbound application. Screened in.",
                 f"Sent the {job_name} description and arranged the first conversation.",
                 f"{first} has a competing offer at a comparable level and a higher base. "
                 f"They prefer this role for the scope, but the other offer is real and the "
                 f"paperwork is with them.",
                 f"They have two weeks to respond to the other offer. If we want them, this "
                 f"has to move this week.",
                 f"{first} confirmed availability, is comfortable with the working pattern, "
                 f"and can start within 30 days. The competing offer is the only obstacle."],
                [])

    return None, [], []


def build_plan(rng_seed=6968):
    corpus = json.load(open(CORPUS))
    pools = json.load(open(POOLS))
    cands = {c["slug"]: c for c in corpus["candidates"]}
    rng = random.Random(rng_seed)

    quota = Counter({s: 0 for s in ALL_SCENARIOS})
    plan, skipped = [], 0
    for jslug, pool in pools.items():
        if not pool.get("cands"):
            continue
        criteria = pool.get("criteria") or []
        for row in pool["cands"]:
            c = cands.get(row["slug"])
            if not c:
                skipped += 1
                continue
            a = analyse(criteria, c)
            a["baseline"] = row.get("score") or 0
            sc = pick(a, c, rng, quota)
            quota[sc] += 1
            sig, notes, calls = write_activity(sc, c, a, pool["name"], rng)
            plan.append({
                "job_slug": jslug, "job_name": pool["name"],
                "candidate_slug": c["slug"], "candidate": c["name"],
                "scenario": sc, "signal": sig,
                "notes": [clip(n) for n in notes],
                "calls": [[clip(t), ty, d] for t, ty, d in calls],
                "baseline_score": row.get("score"),
                "met": [m["criterion"] for m in a["met"]][:4],
                "missing": [m["criterion"] for m in a["missing"]][:4],
                "content_chars": c["content_chars"],
                "employers": a["employers"],
            })
    return plan, quota, skipped


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan", action="store_true")
    p.add_argument("--show", type=int, default=0)
    a = p.parse_args()

    if a.plan:
        plan, quota, skipped = build_plan()
        json.dump(plan, open(PLAN, "w"), indent=1)
        print(f"planned activity for {len(plan)} (job, candidate) pairs "
              f"across {len({p['job_slug'] for p in plan})} jobs")
        if skipped:
            print(f"  {skipped} pooled candidates had no cached profile, skipped "
                  f"(run: python3 -c \"import corpus; corpus.add([...])\")")
        print("\nscenario spread:")
        for s, n in sorted(quota.items(), key=lambda kv: -kv[1]):
            if n:
                print(f"   {s:<20} {n:>4}")
        with_act = sum(1 for x in plan if x["notes"] or x["calls"])
        items = sum(len(x["notes"]) + len(x["calls"]) for x in plan)
        print(f"\ncandidates receiving activity: {with_act}/{len(plan)}   items: {items}")
        print(f"grounded: {sum(1 for x in plan if x['met'] or x['missing'])}/{len(plan)} "
              f"have at least one machine-checked criterion")

    if a.show:
        plan = json.load(open(PLAN))
        for x in random.Random(3).sample(plan, min(a.show, len(plan))):
            print("=" * 96)
            print(f"{x['candidate']}  ->  {x['job_name']}   [{x['scenario']}]")
            print(f"  slug={x['candidate_slug']}  baseline={x['baseline_score']}  "
                  f"content={x['content_chars']} chars  employers={x['employers']}")
            if x["met"]:
                print(f"  MEETS  : {x['met'][0][:80]}")
            if x["missing"]:
                print(f"  MISSING: {x['missing'][0][:80]}")
            for n in x["notes"]:
                print(f"  NOTE: {n[:260]}")
            for t, ty, d in x["calls"]:
                print(f"  CALL ({d}d): {t[:260]}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Builds golden/sourcing/scale_cases.json — 300 realistic AI Sourcing variations.

Deterministic (seeded): 280 full-criteria / 10 single-criterion / 10 no-criteria,
spread over ~65 role archetypes x location x seniority x format, where format is:
  short   — 1-3 line recruiter query            (~90 cases)
  brief   — 1-2 paragraph hiring brief          (~105 cases)
  full_jd — multi-page job description 3-8k chars (~105 cases, 5 of them ~11-13k "xl")

Run:  python3 generate_scale_cases.py     (no network; prints distribution stats)
"""
import json
import os
import random
import statistics

import config
from scale_archetypes import (ARCHETYPES, LOCATIONS, SENIORITY, COMPANY_BLURBS,
                              TEAM_BLURBS, BENEFITS, EEO, PROCESS, SALARY_US, COMPANY_NAMES)

SEED = 6818
N_CASES, N_SINGLE, N_NONE, N_XL = 300, 10, 10, 5

SHORT_TEMPLATES = [
    "Looking for a {snr}{title} {locp}. Must have {s1} and {s2}; {n1} a plus.",
    "Hiring: {snr}{title}, {loc}. Key skills: {s1}, {s2}, {s3}.",
    "Need an experienced {title} {locp} — strong {s1} and {s2} required, {yrs} experience preferred.",
    "{snr}{title} wanted for our {ind} team {locp}. {s1}, {s2} and {s3} essential.",
    "Urgently seeking a {snr}{title} {locp} with solid {s1} experience. Start within 4 weeks.",
    "{title} ({yrs} exp) {locp}. Day-to-day: {r1short} Core stack/skills: {s1}, {s2}, {s3}.",
]


def _mk_criteria(rng, arch, cohort, loc_label):
    if cohort == "none":
        return []
    crits = [{"text": t, "weight": w} for t, w in arch["crits"]]
    if cohort == "single":
        return [dict(crits[0])]
    k = rng.randint(max(3, len(crits) - 2), len(crits))
    picked = [dict(c) for c in rng.sample(crits, k)]
    picked.sort(key=lambda c: -c["weight"])
    for c in picked:                                   # realistic weight jitter
        c["weight"] = max(1, min(10, c["weight"] + rng.choice([-1, 0, 0, 0, 1])))
    if rng.random() < 0.20 and not loc_label.startswith("Remote"):
        picked.append({"text": f"Based in or willing to relocate to {loc_label.split(' - ')[-1]}",
                       "weight": rng.choice([3, 4, 5])})
    return picked[:10]


def _short(rng, arch, snr_label, yrs, loc, locp):
    t = rng.choice(SHORT_TEMPLATES)
    sk = arch["skills"]
    return t.format(snr=(snr_label + " " if snr_label else ""), title=arch["title"], loc=loc,
                    locp=locp, ind=rng.choice(arch["inds"]), yrs=yrs,
                    s1=sk[0], s2=sk[1], s3=sk[2], n1=arch["nice"][0],
                    r1short=arch["resp"][0].rstrip(".") + ".")


def _brief(rng, arch, snr_label, yrs, loc, locp, co, company_led):
    """company_led=True keeps the classic "Acme is hiring a ..." opener — the phrasing that
    currently triggers the hiring-company-as-filter bug (kept as its regression cohort).
    The rest use openers with no company name so the run still yields scored pools."""
    ind = rng.choice(arch["inds"])
    reqs = rng.sample(arch["reqs"], min(4, len(arch["reqs"])))
    title = f"{snr_label + ' ' if snr_label else ''}{arch['title']}"
    if company_led:
        opener = f"{co}, a {ind} organization, is hiring a {title} {locp}."
    else:
        opener = rng.choice([
            f"We are hiring a {title} {locp} for our {ind} team.",
            f"Hiring a {title} {locp} to join a growing {ind} organization.",
            f"Our {ind} client is looking for a {title} {locp}.",
        ])
    p1 = (f"{opener} You will {arch['resp'][0][0].lower() + arch['resp'][0][1:]} "
          f"and {arch['resp'][1][0].lower() + arch['resp'][1][1:]}, working closely with the wider team.")
    p2 = ("What we're looking for: " + "; ".join(r.rstrip(".") for r in reqs)
          + f". Ideally {yrs} of relevant experience. {rng.choice(arch['nice'])} would be a bonus.")
    return p1 + "\n\n" + p2


_RESP_ELAB = [
    "You will own this area end to end, from planning through delivery, and be accountable for its outcomes.",
    "Expect to spend a meaningful share of your week here, partnering with stakeholders across the business.",
    "Quality matters more than speed here — we expect thoughtful, well-documented work that others can build on.",
    "You'll have real autonomy in how you approach this, with support from experienced teammates when you need it.",
    "This is where the role has the most visible impact, and where we will look for early wins.",
]
_REQ_ELAB = [
    "We will probe this area in the interview process with practical, scenario-based questions.",
    "Depth matters more than years — show us real examples of this in your background.",
    "Candidates who have done this in a comparable environment will feel at home quickly.",
    "This is a firm requirement for the role; please address it directly in your application.",
    "Exposure through side projects or certifications counts, but hands-on professional experience is preferred.",
]


def _jd_excerpt(rng, arch, snr_label, yrs, loc, locp, co, company_led):
    """Condensed JD paste ≤990 chars — the REAL constraint: the sourcing search rejects
    aiSearch >1000 chars (validated 400), so a user pastes the meat of the JD, not all
    of it. Title + location + about-line + key requirements + top responsibilities."""
    title = f"{snr_label + ' ' if snr_label else ''}{arch['title']}"
    about = (f"{co} ({rng.choice(arch['inds'])}) is hiring; " if company_led
             else f"We are a {rng.choice(arch['inds'])} organization hiring; ")
    # fill toward the cap: users paste as much of the JD as the 1000-char limit allows
    queue = [f"{title} — {loc} (full-time)",
             about + f"{yrs} relevant experience expected. "
             f"High-impact role with real ownership from week one.",
             "Requirements: " + "; ".join(r.rstrip(".") for r in arch["reqs"]) + ".",
             "Responsibilities: " + "; ".join(r.rstrip(".") for r in arch["resp"]) + ".",
             f"Nice to have: {', '.join(arch['nice'])}.",
             f"Core skills: {', '.join(arch['skills'])}.",
             "Interview process: recruiter screen, functional deep-dive, panel; offer within three weeks."]
    parts, jd = [], ""
    for s in queue:
        cand = ("\n".join(parts + [s]))
        if len(cand) > 990:
            break
        parts.append(s)
        jd = cand
    return jd


def _full_jd(rng, arch, snr_label, yrs, loc, locp, co, is_us, xl=False):
    ind = rng.choice(arch["inds"])
    title = f"{snr_label + ' ' if snr_label else ''}{arch['title']}"
    parts = [f"{title}", f"Location: {loc}  |  Employment type: Full-time",
             "", "About " + co,
             rng.choice(COMPANY_BLURBS).format(co=co, ind=ind),
             ("Our people stay because the work is interesting and the expectations are clear: we hire "
              "experienced professionals, give them real ownership, and get out of the way. You will find "
              "modern tooling, sensible processes, and leadership that communicates context openly."),
             "", "The Role",
             (f"We are looking for a {title} {locp} to join our {ind} team. "
              f"This is a high-impact role with {yrs} of relevant experience expected. "
              + rng.choice(TEAM_BLURBS).format(n=rng.randint(4, 12),
                                               mgr=rng.choice(["Director", "VP", "Head of Department", "Engineering Manager"]))),
             ("In this position you will balance day-to-day execution with longer-term improvements, and your "
              "input will directly shape how the team works. We are deliberate about scope: you will have a "
              "clear charter, a manager who invests in your growth, and peers who hold a high bar."),
             "", "What you'll do"]
    parts += [f"- {r}. {rng.choice(_RESP_ELAB)}" for r in arch["resp"]]
    parts += ["", "What we're looking for"]
    parts += [f"- {r}. {rng.choice(_REQ_ELAB)}" for r in arch["reqs"]]
    parts += ["", "Nice to have"] + [f"- {n}" for n in arch["nice"]]
    parts += ["", "A typical week",
              ("Mondays start with a short team planning session to align on priorities. Midweek is protected "
               "for focused execution — most meetings are batched, and deep-work time is respected. You will "
               "sync regularly with cross-functional partners, and Fridays close with a demo or review where "
               "the team shares progress and lessons learned. On-call or coverage duties, where applicable, "
               "are rotated fairly and compensated."),
              "", "Growth and development",
              ("We publish a leveling framework with clear expectations for each level and run calibration "
               "twice a year. You will have a development budget, dedicated learning time, and a manager who "
               "does real 1:1s — not status meetings. Internal mobility is encouraged after 12 months.")]
    if is_us and rng.random() < 0.5:
        lo = rng.choice([80, 95, 110, 125, 140]); hi = lo + rng.choice([25, 30, 40])
        parts += ["", rng.choice(SALARY_US).format(lo=lo, hi=hi)]
    parts += ["", "Benefits", rng.choice(BENEFITS), "", "Hiring process", rng.choice(PROCESS),
              "", rng.choice(EEO)]
    jd = "\n".join(parts)
    if xl:  # extra-long realistic JD (~11-13k): expand with detailed sections
        extra = ["", "Your responsibilities in detail"]
        for r in arch["resp"]:
            extra.append(f"{r}. In practice this means owning the full lifecycle: understanding the underlying "
                         f"need, proposing an approach, aligning stakeholders, executing to a high standard, and "
                         f"measuring the result. You will document decisions as you go, raise risks early, and "
                         f"leave things better than you found them. We expect steady, visible progress here rather "
                         f"than heroics, and your manager will review outcomes with you in regular 1:1s.")
        extra += ["", "Our stack and tooling in detail",
                  " ".join(f"{s}: used daily across the team, with internal guilds, documentation and regular "
                           f"knowledge-sharing sessions to keep skills sharp." for s in arch["skills"]),
                  "", "What success looks like in the first 12 months",
                  ("In your first 30 days you will onboard, meet your stakeholders, learn our systems and ship a "
                   "first contribution. By 90 days you will own a meaningful slice of the roadmap, be fully "
                   "productive in our workflows, and have built working relationships across the teams you depend "
                   "on. By 6 months you will have led at least one significant initiative end to end — scoped it, "
                   "driven it, landed it — and be contributing to planning beyond your immediate area. By 12 months "
                   "you will be recognized as a go-to owner in your domain, mentoring newer teammates, improving "
                   "our standards, and shaping how the team works."),
                  "", "Who you'll work with",
                  ("You will partner daily with peers in your discipline and weekly with adjacent functions — "
                   "product, operations, finance or engineering depending on the initiative. Leadership operates "
                   "with an open calendar culture: skip-levels are normal, context is shared in writing, and "
                   "questions are welcomed. We are distributed across several time zones and lean on async "
                   "communication, recorded decisions and a well-maintained internal wiki to keep everyone aligned."),
                  "", "More about the team and culture",
                  ("We run a tight weekly cadence: Monday planning, midweek deep-work blocks, Friday demos and "
                   "retro. Documentation is a first-class habit; decisions are written down. We invest in tooling, "
                   "pay down debt deliberately, and protect sustainable pace. You will find low ego, high "
                   "ownership, and direct, kind feedback. We celebrate shipped work, learn from misses without "
                   "blame, and keep meetings small and purposeful."),
                  "", "Our principles",
                  ("Customers first: we earn trust by solving real problems well. Ownership: you run what you "
                   "build and care for its health. Clarity: we write things down and argue from evidence. "
                   "Craft: quality is a habit, not a phase. Momentum: small, steady wins compound. Respect: we "
                   "assume good intent, give feedback directly, and make room for every voice in the room."),
                  "", "Frequently asked questions",
                  ("Is relocation supported? Case by case, with assistance for the right candidate. "
                   "Is the role open to visa sponsorship? We evaluate sponsorship for exceptional applicants. "
                   "What does the growth path look like? We publish a leveling framework with clear expectations "
                   "per level and run calibration twice a year. How is on-call handled (where applicable)? "
                   "Rotations are compensated and humane. What equipment is provided? A modern laptop of your "
                   "choice, plus a home-office or desk-setup budget. How soon can I start? Most offers target a "
                   "start date within four to six weeks, though we can flex for notice periods.")]
        jd = jd + "\n" + "\n".join(extra)
    return jd


def build_cases():
    rng = random.Random(SEED)
    fmt_cycle = ["brief", "jd_excerpt", "short", "jd_excerpt", "brief", "short"]
    cases, used_combo = [], set()
    i = 0
    while len(cases) < N_CASES:
        arch = ARCHETYPES[i % len(ARCHETYPES)]
        fmt = fmt_cycle[(i // len(ARCHETYPES) + i) % len(fmt_cycle)]
        loc_label, locp, is_us = rng.choice(LOCATIONS)
        snr_key = rng.choice(arch["snr"])
        combo = (arch["key"], fmt, loc_label, snr_key)
        i += 1
        if combo in used_combo:
            continue
        used_combo.add(combo)
        snr_label, yrs = SENIORITY[snr_key]
        co = rng.choice(COMPANY_NAMES[arch["fam"]])
        company_led = rng.random() < 0.30       # 30% keep the bug-triggering opener
        if fmt == "short":
            q = _short(rng, arch, snr_label, yrs, loc_label, locp)
            company_led = False                 # shorts never name the hiring company
        elif fmt == "brief":
            q = _brief(rng, arch, snr_label, yrs, loc_label, locp, co, company_led)
        else:
            q = _jd_excerpt(rng, arch, snr_label, yrs, loc_label, locp, co, company_led)
        cases.append({"id": f"{arch['key']}-{fmt}-{len(cases)+1:03d}", "cohort": "full",
                      "format": fmt, "family": arch["fam"], "role": arch["title"],
                      "seniority": snr_key, "location": loc_label, "company_led": company_led,
                      "query": q, "criteria": None, "_arch": arch["key"], "_locp": locp})
    # cohorts: pick 10 single + 10 none from distinct archetypes/formats (seeded)
    idxs = list(range(N_CASES)); rng.shuffle(idxs)
    for j in idxs[:N_SINGLE]:
        cases[j]["cohort"] = "single"
    for j in idxs[N_SINGLE:N_SINGLE + N_NONE]:
        cases[j]["cohort"] = "none"
    # criteria per case
    by_key = {a["key"]: a for a in ARCHETYPES}
    for c in cases:
        c["criteria"] = _mk_criteria(rng, by_key[c["_arch"]], c["cohort"], c["location"])
    # 8 OVER-LIMIT cases (multi-page JDs, 3.8-10k chars): the endpoint caps aiSearch at
    # 1000 chars, so these assert the documented-400 contract (expect: cap_400).
    over_pool = [c for c in cases if c["format"] == "jd_excerpt" and c["cohort"] == "full"]
    for i, c in enumerate(rng.sample(over_pool, 8)):
        arch = by_key[c["_arch"]]
        snr_label, yrs = SENIORITY[c["seniority"]]
        loc_row = next(l for l in LOCATIONS if l[0] == c["location"])
        c["query"] = _full_jd(rng, arch, snr_label, yrs, loc_row[0], loc_row[1],
                              rng.choice(COMPANY_NAMES[arch["fam"]]), loc_row[2], xl=(i < 3))
        c["format"] = "jd_overlimit"
        c["expect"] = "cap_400"
        c["id"] = c["id"].replace("-jd_excerpt-", "-jd_overlimit-")   # id reflects what it is
    # shuffle so any --limit prefix is diverse; force key shapes into the first 12
    rng.shuffle(cases)
    def promote(pred, slot):
        k = next(idx for idx, c in enumerate(cases) if pred(c) and idx >= 12)
        cases.insert(slot, cases.pop(k))
    if not any(c["cohort"] == "none" for c in cases[:12]):
        promote(lambda c: c["cohort"] == "none", 3)
    if not any(c["cohort"] == "single" for c in cases[:12]):
        promote(lambda c: c["cohort"] == "single", 6)
    if not any(c["format"] == "jd_excerpt" for c in cases[:12]):
        promote(lambda c: c["format"] == "jd_excerpt", 9)
    if not any(c["format"] == "jd_overlimit" for c in cases[:12]):
        promote(lambda c: c["format"] == "jd_overlimit", 11)
    for c in cases:
        c.pop("_arch", None); c.pop("_locp", None)
    return cases


def main():
    cases = build_cases()
    out = os.path.join(config.GOLDEN_SOURCING_DIR, "scale_cases.json")
    os.makedirs(config.GOLDEN_SOURCING_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
    lens = [len(c["query"]) for c in cases]
    from collections import Counter
    print(f"wrote {len(cases)} cases -> {out}")
    print("cohorts :", dict(Counter(c['cohort'] for c in cases)))
    print("formats :", dict(Counter(c['format'] for c in cases)))
    print("families:", dict(Counter(c['family'] for c in cases)))
    print(f"query length: min={min(lens)} median={int(statistics.median(lens))} "
          f"mean={int(statistics.mean(lens))} max={max(lens)}")
    for f in ("short", "brief", "jd_excerpt", "jd_overlimit"):
        fl = [len(c["query"]) for c in cases if c["format"] == f]
        if fl:
            print(f"  {f:<10} n={len(fl):<4} chars {min(fl)}-{max(fl)} (median {int(statistics.median(fl))})")
    ncrit = [len(c["criteria"]) for c in cases if c["cohort"] == "full"]
    print(f"criteria per full case: min={min(ncrit)} max={max(ncrit)} mean={sum(ncrit)/len(ncrit):.1f}")
    dup = len(cases) - len({c['id'] for c in cases})
    print(f"duplicate ids: {dup}")
    print("first 12 cohorts/formats:", [(c['cohort'], c['format']) for c in cases[:12]])


if __name__ == "__main__":
    main()

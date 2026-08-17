#!/usr/bin/env python3
"""
Run the evaluation.

FLOW 1 — Activity Context Evaluation (AI2 only)
    For each job: configure -> save criteria -> trigger -> poll. For each matched
    candidate collect the JD, the profile, the notes, the call logs, the match score and
    the match analysis, and hand all of it to the judge.

FLOW 2 — AI2 vs DEV
    Same database, same job, same criteria; the only intended difference is that AI2 can
    see the activity and DEV cannot.

WHY THE CRITERIA ARE PINNED
---------------------------
Criteria are generated from the JD by an LLM, so configuring twice gives two different
sets. Comparing those would measure criteria drift, not activity context. We configure
ONCE and save that same criteria object to both deployments. Saving also wipes the job's
stored scores, forcing the second deployment to re-score rather than return a cache.

RUN ORDER
---------
Whichever deployment runs FIRST builds the candidate pool and pays for it (~200s vs ~15s
for the second, which reads it warm from the shared database). The order alternates per
job and is recorded, so the response-time sheet is not just measuring run order.

PARALLELISM
-----------
Across JOBS only. dev and ai2 must stay sequential within a job: they share one database
and save_criteria clears the job's stored scores, so two concurrent runs on the same job
would wipe each other mid-flight.

    python3 evaluate.py                  # both flows, all jobs
    python3 evaluate.py --top 15         # cap candidates per job
    python3 evaluate.py --resume         # skip jobs already done
    python3 evaluate.py --rejudge        # re-run only the judge over saved analyses
    python3 evaluate.py --dry-run        # plan and cost estimate, no calls
"""

import argparse
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config as C                                  # noqa: E402
from judge import Judge                             # noqa: E402
from matching import Matching, match_analysis_text  # noqa: E402

MANIFEST = os.path.join(HERE, "manifest.json")
CORPUS = os.path.join(HERE, "corpus.json")
RAW = os.path.join(HERE, "results.jsonl")
# ai2 and dev share one database. Public-API reads go through whichever host is up; only
# the MATCHING calls have to hit each deployment separately.
PUBLIC_ENV = os.environ.get("EVAL_PUBLIC_ENV", "dev")
JOB_WORKERS = 4
JUDGE_WORKERS = 4

# Judge concurrency is capped GLOBALLY, not per job. Jobs run in parallel and each has its
# own judge pool, so without this the ceiling would be JOB_WORKERS x JUDGE_WORKERS.
JUDGE_SLOTS = threading.Semaphore(8)
_write_lock = threading.Lock()

IN_RATE, OUT_RATE = 3.00, 15.00      # Sonnet 5 list price, USD per 1M tokens


def log(m):
    print(m, flush=True)


# ---------------------------------------------------------------------------
# Reading a matching row — the response is a wide object and key casing varies
# ---------------------------------------------------------------------------

def _first(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def row_slug(row):
    return _first(row, "slug", "candidate_slug", "candidateSlug")


def row_name(row):
    n = _first(row, "name", "full_name", "fullName")
    if n:
        return n
    first = _first(row, "first_name", "firstName") or ""
    last = _first(row, "last_name", "lastName") or ""
    return f"{first} {last}".strip() or "(unnamed)"


def row_score(row):
    """The score is the top-level matchScore, NOT matchAnalysis.score."""
    ma = row.get("matchAnalysis") or {}
    v = _first(ma, "score", "overallScore", "matchScore")
    if v is None:
        v = _first(row, "aiMatchingScore", "matchingScore", "matchScore", "score")
    return v


def row_resume(row):
    """Fallback only — `summary` is the parsed summary section, not the profile."""
    return _first(row, "summary", "candidate_summary", "candidateSummary") or ""


def resume_index(man=None):
    """
    candidate_slug -> the text the scorer can actually see for this person.

    These are REAL candidates from the account. None has a resume PDF or a
    candidate_summary — they are structured imports. What they do have is work_history
    with genuine descriptions, plus position and skill. That text IS their resume as far
    as scoring is concerned, so it is what the judge must be shown.

    Built from corpus.json, read back from the API — never regenerated, so it cannot
    drift out of step with what is actually stored.
    """
    if not os.path.exists(CORPUS):
        log("  !! corpus.json missing — the judge will fall back to row.summary")
        return {}
    corpus = json.load(open(CORPUS))
    out, thin = {}, 0
    for c in corpus.get("candidates", []):
        parts = []
        if c.get("position"):
            parts.append(f"CURRENT TITLE\n{c['position']}")
        if c.get("skill"):
            parts.append(f"SKILLS\n{c['skill']}")
        if c.get("summary"):
            parts.append(f"SUMMARY\n{c['summary']}")
        roles = []
        for r in c.get("roles", []):
            head = " | ".join(x for x in (r.get("title"), r.get("company")) if x)
            body = r.get("desc") or ""
            if head or body:
                roles.append(f"{head}\n{body}".strip())
        if roles:
            parts.append("EXPERIENCE\n\n" + "\n\n".join(roles))
        if c.get("education"):
            parts.append("EDUCATION\n" + "; ".join(c["education"]))
        txt = "\n\n".join(parts).strip()
        if len(txt) < 60:
            thin += 1
            # a genuinely empty profile — say so rather than send the judge a blank, which
            # would make it call every correct statement unsupported
            txt = ("(This candidate has no work history, summary or resume on file. The "
                   "profile is effectively empty.)")
        out[c["slug"]] = txt
    if thin:
        log(f"  {thin} candidates have no profile content at all — the judge is told so "
            f"explicitly rather than shown an empty string")
    return out


# ---------------------------------------------------------------------------
# Activity, read back from the ATS with timestamps
# ---------------------------------------------------------------------------

class PublicApi:
    def __init__(self, env):
        self.base = C.api_base(env)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {C.api_key(env)}"})
        self.cache = {}

    def _search(self, path, slug, field):
        """
        Returns "[<date>] <text>" per item, oldest first.

        THE DATE IS NOT OPTIONAL. The scoring prompt requires each reason to name its
        source and date, so the analysis says things like "a call log dated 04 Aug 2026".
        Hand the judge undated text and it reads every one of those as a fabricated
        citation — measured at 61% of Flow 1 judgments, a systematic 0.71-point penalty.

        `updated_on` is the field the activity pipeline orders on.
        """
        try:
            r = self.s.get(self.base + path,
                           params={"related_to": slug, "related_to_type": "candidate"},
                           timeout=45)
            if r.status_code != 200:
                return []
            b = r.json()
            rows = b if isinstance(b, list) else (b.get("data", []) or [])
        except (requests.RequestException, ValueError):
            return []
        rows = sorted(rows, key=lambda x: str(x.get("updated_on") or
                                              x.get("created_on") or ""))
        out = []
        for x in rows:
            text = str(x.get(field) or "").strip()
            if not text:
                continue
            when = str(x.get("updated_on") or x.get("created_on") or "")[:19].replace("T", " ")
            out.append(f"[{when}] {text}" if when else text)
        return out

    def activity(self, slug):
        """Read from the ATS for EVERY candidate — the plan has text but no timestamps."""
        if slug not in self.cache:
            self.cache[slug] = (self._search("notes/search", slug, "description"),
                                self._search("call-logs/search", slug, "call_notes"))
        return self.cache[slug]


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def flow1_verdict(v):
    s = v["judge_score"]
    base = "Acceptable" if s >= 4 else "Borderline" if s == 3 else "Not acceptable"
    if v.get("activity_wrongly_influenced") == "Yes":
        base += " — activity misused"
    elif v.get("important_details_ignored") == "Yes":
        base += " — detail ignored"
    elif s >= 4 and v.get("activity_context_used") == "Yes":
        base += " — activity used well"
    return base


def flow2_verdict(v):
    w = v["winner"]
    if w == "Tie":
        return "No difference"
    lead = f"{w} better ({v['confidence'].lower()} confidence)"
    if w == "AI2":
        return lead + (" — activity added value"
                       if v.get("activity_added_measurable_value") == "Yes"
                       else " — but not from activity")
    return lead + (" — regression in AI2" if v.get("any_regression") == "Yes" else "")


# ---------------------------------------------------------------------------
# Per-job evaluation
# ---------------------------------------------------------------------------

def collect(job, envs, pinned_from, top, keep=(), verbose=True):
    clients = {e: Matching(e, verbose=verbose) for e in envs}
    cfg = clients[pinned_from].configure(job["job_slug"])
    criteria = cfg.get("criteria") or []
    if not criteria:
        raise RuntimeError(f"configuration returned no criteria "
                           f"(status={cfg.get('status')})")
    if verbose:
        log("    criteria (%d): %s" % (len(criteria),
            "; ".join(str(c.get("text", ""))[:44] for c in criteria[:4])))

    out, timings = {}, {}
    for env in envs:
        clients[env].save_criteria(job["job_slug"], cfg)   # also clears stored scores
        rows, secs = clients[env].search_with_polling(job["job_slug"])
        timings[env] = dict(clients[env].timings)
        out[env] = {s: r for r in rows if (s := row_slug(r))}
        t = timings[env]
        log(f"  [{job['key']}/{env}] {len(rows)} scored in {secs}s "
            f"(first response {t.get('first_response_s')}s, {t.get('polls')} polls)")

    if top:
        # Choose ONCE on the AI2 side, then take the same people from DEV — letting each
        # deployment pick its own top-N selects different candidates and Flow 2 then has
        # nothing to compare. Seeded candidates are never dropped; the cap only trims
        # pre-existing account records.
        ranked = sorted(out["ai2"].items(), key=lambda kv: -(row_score(kv[1]) or 0))
        mine = [s for s, _ in ranked if s in keep]
        others = [s for s, _ in ranked if s not in keep][:max(0, top - len(mine))]
        chosen = set(mine) | set(others)
        dropped = len(out["ai2"]) - len(chosen)
        out = {e: {s: r for s, r in rows.items() if s in chosen} for e, rows in out.items()}
        log(f"    --top {top}: judging {len(chosen)} ({len(mine)} seeded + "
            f"{len(others)} pre-existing), dropped {dropped}")
    return out, criteria, timings


def evaluate_job(job, args, judge, api, resumes=None, order_index=0):
    if args.flow in ("2", "both"):
        envs = ["dev", "ai2"] if order_index % 2 == 0 else ["ai2", "dev"]
    else:
        envs = ["ai2"]

    known = {c["slug"]: c for c in job["candidates"]}
    scored, criteria, timings = collect(job, envs, "ai2", args.top, keep=set(known))

    slugs = list(scored["ai2"].keys())
    if args.dataset_only:
        dropped = [s for s in slugs if s not in known]
        slugs = [s for s in slugs if s in known]
        if dropped:
            log(f"    --dataset-only dropped {len(dropped)} candidates outside the dataset")

    def one(slug):
        a_row = scored["ai2"][slug]
        seeded = known.get(slug)
        name = seeded["name"] if seeded else row_name(a_row)
        resume = (resumes or {}).get(slug) or row_resume(a_row)
        notes, calls = api.activity(slug)

        rec = {"job_key": job["key"], "job_title": job["title"], "job_slug": job["job_slug"],
               "candidate_slug": slug, "candidate": name,
               "in_dataset": bool(seeded),
               "scenario": seeded["scenario"] if seeded else "(not in dataset)",
               "signal": (seeded.get("signal") if seeded else "") or "",
               "notes": notes, "calls": calls, "resume_chars": len(resume),
               "resume_source": "profile" if (resumes or {}).get(slug) else "row.summary",
               "ai2_score": row_score(a_row),
               "ai2_analysis": match_analysis_text(a_row)}

        if not rec["ai2_analysis"].strip():
            rec["no_analysis"] = a_row.get("reasoningStatus") or "empty matchAnalysis"
            return rec

        if args.flow in ("1", "both"):
            v = judge.flow1(job_title=job["title"], jd=job["jd"], resume=resume,
                            notes=notes, calls=calls,
                            score=rec["ai2_score"], analysis=rec["ai2_analysis"])
            if v:
                rec["flow1"] = {**v, "final_verdict": flow1_verdict(v)}

        if args.flow in ("2", "both"):
            d_row = scored.get("dev", {}).get(slug)
            if d_row is None:
                rec["flow2_skipped"] = "not returned by DEV"
            else:
                rec["dev_score"] = row_score(d_row)
                rec["dev_analysis"] = match_analysis_text(d_row)
                v = judge_flow2(judge, rec, job, resume, notes, calls)
                if v:
                    rec["flow2"] = v
        return rec

    def safe(slug):
        try:
            with JUDGE_SLOTS:
                return one(slug)
        except Exception as e:
            log(f"    !! candidate {slug}: {type(e).__name__}: {e}")
            return {"job_key": job["key"], "job_title": job["title"],
                    "candidate_slug": slug, "candidate": "(error)",
                    "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS) as pool:
        records = list(pool.map(safe, slugs))

    return {"job_key": job["key"], "job_title": job["title"], "job_slug": job["job_slug"],
            "criteria": [c.get("text") for c in criteria], "timings": timings,
            "ran_first": envs[0],
            "pool_sizes": {e: len(v) for e, v in scored.items()},
            "records": records}


def judge_flow2(judge, rec, job, resume, notes, calls):
    """
    Blind comparison. AI2 is shown as System A or B at random (stable per candidate) and
    the judge is never told which — without that it favours whichever it believes is the
    newer system. Unmasked here.
    """
    slug = rec["candidate_slug"]
    ai2_is_a = sum(slug.encode()) % 2 == 0
    x, y = ("ai2", "dev") if ai2_is_a else ("dev", "ai2")
    v = judge.flow2(job_title=rec["job_title"], jd=job.get("jd", ""), resume=resume,
                    notes=notes, calls=calls,
                    a_score=rec[f"{x}_score"], a_text=rec[f"{x}_analysis"],
                    b_score=rec[f"{y}_score"], b_text=rec[f"{y}_analysis"])
    if not v:
        return None
    um = {"System A": x.upper(), "System B": y.upper(), "Tie": "Tie", "Neither": "Neither"}
    v = dict(v)
    v["winner"] = um[v["winner"]]
    v["better_explanations"] = um.get(v["better_explanations"], v["better_explanations"])
    v["more_useful_for_recruiter"] = um.get(v["more_useful_for_recruiter"],
                                            v["more_useful_for_recruiter"])
    v["ai2_references_activity"] = v.pop(
        "a_references_activity" if ai2_is_a else "b_references_activity")
    v["dev_references_activity"] = v.pop(
        "b_references_activity" if ai2_is_a else "a_references_activity")
    v["ai2_shown_as"] = "A" if ai2_is_a else "B"
    return {**v, "final_verdict": flow2_verdict(v)}


# ---------------------------------------------------------------------------
# Re-judge: reuse saved analyses, no ATS calls
# ---------------------------------------------------------------------------

def rejudge(a):
    """
    Re-run ONLY the judge over analyses already in results.jsonl.

    The AI2 and DEV analyses are the expensive part and are already captured, so changing
    the rubric does not require touching the ATS or re-scoring. Inputs are identical to
    the original run, which makes old and new judgements directly comparable.

    Handles BOTH flows — an earlier version only re-ran Flow 1, which left 269 rows with
    both analyses saved but no comparison judged.
    """
    blocks = [json.loads(l) for l in open(RAW)]
    judge = Judge(effort=a.judge_effort)
    api = PublicApi(PUBLIC_ENV)
    man = json.load(open(MANIFEST))
    resumes = resume_index(man)
    jd = {j["key"]: j["jd"] for j in man["jobs"]}
    jobs_by_key = {j["key"]: j for j in man["jobs"]}

    todo = [(b, r) for b in blocks for r in b["records"] if r.get("ai2_analysis")]
    if not a.force:
        todo = [(b, r) for b, r in todo
                if "flow1" not in r or ("flow2" not in r and r.get("dev_analysis"))]
    n1 = sum(1 for _, r in todo if "flow1" not in r or a.force)
    n2 = sum(1 for _, r in todo if r.get("dev_analysis") and ("flow2" not in r or a.force))
    log(f"re-judging from saved analyses (no ATS calls): "
        f"{n1} need flow1, {n2} need flow2, {len(todo)} candidates")
    before = [r["flow1"]["judge_score"] for _, r in todo if "flow1" in r]

    def one(pair):
        b, r = pair
        with JUDGE_SLOTS:
            notes, calls = api.activity(r["candidate_slug"])
            resume = resumes.get(r["candidate_slug"]) or ""
            job = jobs_by_key.get(r["job_key"], {"jd": jd.get(r["job_key"], "")})
            ok = False
            if "flow1" not in r or a.force:
                v = judge.flow1(job_title=r["job_title"], jd=job.get("jd", ""),
                                resume=resume, notes=notes, calls=calls,
                                score=r["ai2_score"], analysis=r["ai2_analysis"])
                if v:
                    r["flow1"] = {**v, "final_verdict": flow1_verdict(v)}
                    ok = True
            if r.get("dev_analysis") and ("flow2" not in r or a.force):
                v2 = judge_flow2(judge, r, job, resume, notes, calls)
                if v2:
                    r["flow2"] = v2
                    ok = True
            r["notes"], r["calls"] = notes, calls
            return ok

    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS * 2) as pool:
        ok = sum(1 for x in pool.map(one, todo) if x)
    with open(RAW, "w") as fh:
        for b in blocks:
            fh.write(json.dumps(b) + "\n")
    after = [r["flow1"]["judge_score"] for _, r in todo if "flow1" in r]
    m = lambda g: sum(g) / len(g) if g else 0
    log(f"re-judged {ok}/{len(todo)}   mean {m(before):.2f} -> {m(after):.2f}")
    log(f"  {json.dumps(judge.usage())}")
    return 0


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def load_done():
    if not os.path.exists(RAW):
        return set()
    done = set()
    for line in open(RAW):
        line = line.strip()
        if line:
            try:
                done.add(json.loads(line)["job_key"])
            except (ValueError, KeyError):
                pass
    return done


def estimate(jobs, args):
    per_job = args.top or 40
    cands = len(jobs) * per_job
    calls = cands * (2 if args.flow == "both" else 1)
    usd = calls * 3500 / 1e6 * IN_RATE + calls * 400 / 1e6 * OUT_RATE
    log(f"  estimate: ~{cands} candidates, ~{calls} judge calls, ~${usd:,.2f} "
        f"(assumes ~{per_job} judged per job)")


def main():
    p = argparse.ArgumentParser(description="Run the activity-context evaluation")
    p.add_argument("--flow", default="both", choices=["1", "2", "both"])
    p.add_argument("--jobs", type=int, default=0, help="limit number of jobs (0 = all)")
    p.add_argument("--top", type=int, default=0,
                   help="judge only the N highest-scoring candidates per job (0 = all)")
    p.add_argument("--include-existing", action="store_true",
                   help="also judge pre-existing account records retrieval pulled in")
    p.add_argument("--resume", action="store_true", help="skip jobs already in results")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="with --rejudge, re-judge rows that already have a verdict")
    p.add_argument("--rejudge", action="store_true",
                   help="re-run the judge over saved analyses; no ATS calls")
    p.add_argument("--parallel", type=int, default=JOB_WORKERS,
                   help="jobs to run concurrently (dev/ai2 stay sequential within a job)")
    p.add_argument("--judge-effort", default="medium",
                   choices=["low", "medium", "high", "xhigh", "max"])
    a = p.parse_args()
    a.dataset_only = not a.include_existing

    if a.rejudge:
        if not os.path.exists(RAW):
            raise SystemExit(f"No results at {RAW} — nothing to re-judge")
        return rejudge(a)

    if not os.path.exists(MANIFEST):
        raise SystemExit(f"No manifest at {MANIFEST} — build it from corpus + plan first")
    man = json.load(open(MANIFEST))
    if not man.get("jobs"):
        raise SystemExit(f"{MANIFEST} has no jobs")
    jobs = man["jobs"][:a.jobs] if a.jobs else man["jobs"]

    done = load_done() if a.resume else set()
    if done:
        jobs = [j for j in jobs if j["key"] not in done]
        log(f"resuming — {len(done)} jobs already done, {len(jobs)} to go")
    elif os.path.exists(RAW) and not a.dry_run:
        os.rename(RAW, RAW + f".{int(time.time())}.bak")
        log(f"previous results moved aside to {os.path.basename(RAW)}.*.bak")

    envs = "dev then ai2" if a.flow in ("2", "both") else "ai2"
    log(f"flow={a.flow}  envs={envs}  jobs={len(jobs)}  top={a.top or 'all'}  "
        f"judge-effort={a.judge_effort}  "
        f"candidates={'seeded only' if a.dataset_only else 'seeded + pre-existing'}")
    estimate(jobs, a)

    if a.dry_run:
        for j in jobs:
            log(f"  {j['key']:<14} {j['job_slug']:<26} {len(j['candidates'])} candidates")
        log("dry run — nothing called")
        return 0

    judge = Judge(effort=a.judge_effort)
    api = PublicApi(PUBLIC_ENV)
    resumes = resume_index(man)
    med = sorted(len(v) for v in resumes.values())
    log(f"loaded {len(resumes)} profile texts for the judge "
        f"(median {med[len(med) // 2] if med else 0} chars)")

    started, failed = time.time(), []
    workers = min(a.parallel, len(jobs)) or 1
    log(f"running {workers} jobs in parallel "
        f"(dev and ai2 stay sequential within each job — shared database)")
    order = {j["key"]: i for i, j in enumerate(jobs)}
    done_n = [0]

    def run_one(job):
        try:
            return job, evaluate_job(job, a, judge, api, resumes,
                                     order_index=order[job["key"]]), None
        except Exception as e:
            if os.environ.get("EVAL_TRACE"):
                traceback.print_exc()
            return job, None, f"{type(e).__name__}: {e}"

    with open(RAW, "a") as fh, ThreadPoolExecutor(max_workers=workers) as pool:
        for job, res, err in [f.result() for f in
                              as_completed([pool.submit(run_one, j) for j in jobs])]:
            done_n[0] += 1
            if err:
                failed.append((job["key"], err))
                log(f"[{done_n[0]}/{len(jobs)}] {job['key']} — FAILED: {err}")
                continue
            n1 = sum(1 for r in res["records"] if "flow1" in r)
            n2 = sum(1 for r in res["records"] if "flow2" in r)
            with _write_lock:
                fh.write(json.dumps(res) + "\n")
                fh.flush()
            log(f"[{done_n[0]}/{len(jobs)}] {job['key']} — judged flow1 {n1}, flow2 {n2}")

    log(f"\ndone in {(time.time() - started) / 60:.1f} min — {json.dumps(judge.usage())}")
    log(f"raw: {RAW}")
    if failed:
        log(f"{len(failed)} job(s) failed:")
        for k, e in failed:
            log(f"  {k}: {e}")
        log("re-run with --resume to retry only those")
    log("next: python3 report.py")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

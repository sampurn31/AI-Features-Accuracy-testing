"""
db=1 (My Database) AI Matching workbook. Reuses the Global scale report's styled builders
(scale_report._append/_header/_fill/_card/_section/_verdict_fill + palette) and adds db=1 sheets:
Dashboard / Read Me / Jobs / Candidates / Scoring Judge / Relevance Judge / Failures / Dataset.
"""
import os
import statistics
from collections import Counter

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

import config
import scale_report as SR


def _agg(rows):
    a = {"n_jobs": len(rows), "jobs_ok": sum(1 for r in rows if r["status"] == "ok"),
         "jobs_error": sum(1 for r in rows if r["status"] == "error")}
    a["n_ours"] = sum(r.get("n_ours", 0) or 0 for r in rows)
    a["n_scored"] = sum(r.get("n_ours_scored", 0) or 0 for r in rows)
    a["band_pass"] = sum(r.get("band_pass", 0) or 0 for r in rows)
    a["fails_by_check"] = Counter(f["check"] for r in rows for f in r.get("fails", []))
    a["exploratory_deltas"] = [f["detail"] for r in rows for f in r.get("exploratory", [])]
    cr, sv, gaps = Counter(), Counter(), []
    for r in rows:
        for j in r.get("judge_scoring", []):
            if j.get("error"):
                continue
            for cv in j.get("crit_verdicts", []):
                if cv.get("reasoning_verdict"):
                    cr[cv["reasoning_verdict"]] += 1
                if cv.get("score_verdict"):
                    sv[cv["score_verdict"]] += 1
                if cv.get("gap") is not None:
                    gaps.append(cv["gap"])
    a["reason"], a["score"], a["avg_gap"] = cr, sv, (statistics.mean(gaps) if gaps else None)
    rel_agree, rel = 0, Counter()
    reln = 0
    for r in rows:
        for j in r.get("judge_relevance", []):
            if j.get("error") or not j.get("relevance_verdict"):
                continue
            rel[j["relevance_verdict"]] += 1
            reln += 1
            if j.get("agrees"):
                rel_agree += 1
    a["rel"], a["rel_agree"], a["rel_n"] = rel, rel_agree, reln
    return a


def _dashboard(ws, a, run_id):
    ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False
    for c in range(1, 13):
        ws.column_dimensions[SR.get_column_letter(c)].width = 12
    ws.merge_cells("A1:L1")
    ws["A1"] = "PW-6818 · My Database (db=1) AI Matching — Scoring + Relevance Accuracy"
    ws["A1"].font = Font(bold=True, size=15, color="FFFFFFFF"); ws["A1"].fill = SR._fill(SR.NAVY)
    ws.merge_cells("A2:L2")
    ws["A2"] = (f"run {run_id} · {a['n_jobs']} jobs ({a['jobs_ok']} ok) · {a['n_ours']} seeded "
                f"candidates evaluated · {a['n_scored']} scored")
    ws["A2"].font = Font(size=10, color="595959")

    def frac(cnt, good):
        t = sum(cnt.values())
        return (cnt.get(good, 0) / t) if t else None

    r = 4
    r = SR._section(ws, r, "Scoring correctness (axis 1 — per-criterion judge)")
    SR._card(ws, r, 1, "REASONING CORRECT", "—" if not a["reason"] else f"{frac(a['reason'],'correct'):.0%}",
             f"wrong {a['reason'].get('wrong',0)} · partly {a['reason'].get('partly',0)}",
             SR._rate_color(frac(a["reason"], "correct"), 0.9, 0.75))
    SR._card(ws, r, 4, "SCORE AGREEMENT", "—" if not a["score"] else f"{frac(a['score'],'agree'):.0%}",
             f"too_high {a['score'].get('too_high',0)} · too_low {a['score'].get('too_low',0)}",
             SR._rate_color(frac(a["score"], "agree"), 0.9, 0.75))
    SR._card(ws, r, 7, "AVG SCORE GAP", "—" if a["avg_gap"] is None else f"{a['avg_gap']:.1f}",
             "|judge − AI| per criterion",
             SR.GREEN_T if (a["avg_gap"] or 99) <= 10 else SR.AMBER_T if (a["avg_gap"] or 99) <= 18 else SR.RED_T)
    bp = (a["band_pass"] / a["n_scored"]) if a["n_scored"] else None
    SR._card(ws, r, 10, "BAND-ENVELOPE", "—" if bp is None else f"{bp:.0%}",
             "score in ground-truth band", SR._rate_color(bp, 0.85, 0.6))
    r += 4
    r = SR._section(ws, r, "Relevance (axis 2 — per-candidate judge vs ground truth)")
    SR._card(ws, r, 1, "RELEVANCE AGREEMENT", "—" if not a["rel_n"] else f"{a['rel_agree']/a['rel_n']:.0%}",
             f"judge vs seeded label ({a['rel_n']} judged)",
             SR._rate_color((a["rel_agree"]/a["rel_n"]) if a["rel_n"] else None, 0.85, 0.6))
    SR._card(ws, r, 4, "JUDGED RELEVANT", str(a["rel"].get("relevant", 0)), "of returned+judged")
    SR._card(ws, r, 7, "JUDGED IRRELEVANT", str(a["rel"].get("irrelevant", 0)),
             "should be low-scored + out of top-K")
    pk = a["fails_by_check"].get("precision_at_k", 0)
    SR._card(ws, r, 10, "TOP-K CLEAN", f"{a['n_jobs']-pk}/{a['n_jobs']}",
             "jobs w/ no irrelevant in top-K", SR._rate_color((a["n_jobs"]-pk)/a["n_jobs"] if a["n_jobs"] else None))
    r += 4
    r = SR._section(ws, r, "Deterministic + ground-truth check failures")
    top = a["fails_by_check"].most_common(10)
    if not top:
        ws.cell(row=r, column=1, value="No hard-check failures 🎉").font = Font(bold=True, color=SR.GREEN_T)
    for i, (name, cnt) in enumerate(top, 1):
        ws.merge_cells(start_row=r+i, start_column=1, end_row=r+i, end_column=3)
        ws.cell(row=r+i, column=1, value=SR.CHECK_LABELS.get(name, name))
        ws.cell(row=r+i, column=4, value=cnt).font = Font(bold=True, color=SR.RED_T)
    r += max(len(top), 1) + 2
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=12)
    ws.cell(row=r, column=1, value=(f"Overall-score-formula (exploratory, DRIFT#1): "
            f"{len(a['exploratory_deltas'])} candidate(s) where matchScore != Σ(s·w)/Σw — see "
            f"Candidates sheet 'formula_dev'. Not a failure until the smoke resolves the formula question.")
            ).font = Font(italic=True, size=9, color="808080")


def _jobs_sheet(wb, rows):
    ws = wb.create_sheet("Jobs")
    cols = ["job_key", "title", "overallStatus", "polls", "n_returned", "n_ours", "n_scored",
            "band_pass", "n_fails", "fail_checks", "reasoning_correct", "score_agree",
            "relevance_agree", "score_top", "score_low", "elapsed_s", "error"]
    SR._header(ws, cols, {"job_key": 22, "title": 24, "overallStatus": 12, "polls": 6, "n_returned": 9,
                          "n_ours": 7, "n_scored": 8, "band_pass": 9, "n_fails": 7, "fail_checks": 30,
                          "reasoning_correct": 11, "score_agree": 10, "relevance_agree": 11,
                          "score_top": 8, "score_low": 8, "elapsed_s": 8, "error": 40})
    for r in sorted(rows, key=lambda x: x["job_key"]):
        js = r.get("judge_scoring", [])
        cr = Counter(cv.get("reasoning_verdict") for j in js for cv in j.get("crit_verdicts", []))
        sv = Counter(cv.get("score_verdict") for j in js for cv in j.get("crit_verdicts", []))
        rel = [j for j in r.get("judge_relevance", []) if not j.get("error")]
        rel_ag = sum(1 for j in rel if j.get("agrees"))
        def pct(cnt, good):
            t = sum(cnt.values()); return round(cnt.get(good, 0)/t, 2) if t else None
        SR._append(ws, [r["job_key"], r.get("title"), r.get("overallStatus"), r.get("polls"),
                        r.get("n_returned"), r.get("n_ours"), r.get("n_ours_scored"),
                        r.get("band_pass"), len(r.get("fails", [])),
                        ", ".join(sorted({SR.CHECK_LABELS.get(f["check"], f["check"]) for f in r.get("fails", [])})),
                        pct(cr, "correct"), pct(sv, "agree"),
                        (round(rel_ag/len(rel), 2) if rel else None),
                        r.get("score_top"), r.get("score_low"), r.get("elapsed_s"), (r.get("error") or "")[:200]])
    for rc in ws.iter_rows(min_row=2):
        for c in rc:
            c.alignment = Alignment(vertical="top", wrap_text=True)
        SR._verdict_fill(rc[cols.index("overallStatus")], ("success",), ("fail", "error"))
    return ws


def _candidates_sheet(wb, rows):
    ws = wb.create_sheet("Candidates")
    cols = ["job_key", "slug", "gt_bucket", "matchScore", "expected_band", "band_ok",
            "formula_dev", "reasoningStatus", "matchLabel", "crit_scores(w)"]
    SR._header(ws, cols, {"job_key": 22, "slug": 18, "gt_bucket": 11, "matchScore": 10,
                          "expected_band": 12, "band_ok": 8, "formula_dev": 10, "reasoningStatus": 12,
                          "matchLabel": 13, "crit_scores(w)": 26},
               display={"expected_band": "expected band", "band_ok": "in band?",
                        "formula_dev": "|score−Σsw/Σw|"})
    import sourcing_checks
    for r in sorted(rows, key=lambda x: x["job_key"]):
        for c in r.get("candidates", []):
            bucket = c.get("_gt_bucket"); ms = c.get("matchScore")
            band = config.DB1_BANDS.get(bucket)
            band_ok = ("" if ms is None or not band else ("OK" if band[0] <= ms <= band[1] else "OFF"))
            wa = sourcing_checks.weighted_avg(c.get("matchAnalysis") or [])
            dev = (round(abs(ms - wa), 1) if wa is not None and isinstance(ms, (int, float)) else None)
            SR._append(ws, [r["job_key"], c.get("slug"), bucket, ms,
                            f"{band[0]}-{band[1]}" if band else "", band_ok, dev,
                            c.get("reasoningStatus"), c.get("matchLabel"),
                            "|".join(f"{a.get('score')}(w{a.get('weight')})" for a in (c.get("matchAnalysis") or []))])
            rc = ws[ws.max_row]
            SR._verdict_fill(rc[cols.index("band_ok")], ("OK",), ("OFF",))
    return ws


def _scoring_judge_sheet(wb, rows):
    ws = wb.create_sheet("Scoring Judge")
    cols = ["job_key", "slug", "gt_bucket", "criterion", "weight", "ai_score", "judge_score",
            "gap", "score_verdict", "reasoning_verdict", "ai_reasoning", "judge_note"]
    SR._header(ws, cols, {"job_key": 20, "slug": 16, "gt_bucket": 10, "criterion": 38, "weight": 6,
                          "ai_score": 8, "judge_score": 9, "gap": 6, "score_verdict": 10,
                          "reasoning_verdict": 11, "ai_reasoning": 60, "judge_note": 60})
    for r in sorted(rows, key=lambda x: x["job_key"]):
        for j in r.get("judge_scoring", []):
            for cv in j.get("crit_verdicts", []):
                SR._append(ws, [r["job_key"], j.get("slug"), j.get("gt_bucket"), cv.get("criterion"),
                                cv.get("weight"), cv.get("ai_score"), cv.get("judge_score"), cv.get("gap"),
                                cv.get("score_verdict"), cv.get("reasoning_verdict"),
                                cv.get("ai_reasoning"), cv.get("judge_note")])
                rc = ws[ws.max_row]
                SR._verdict_fill(rc[cols.index("score_verdict")], ("agree",), ("too_high", "too_low"))
                SR._verdict_fill(rc[cols.index("reasoning_verdict")], ("correct",), ("wrong",))
    return ws


def _relevance_judge_sheet(wb, rows):
    ws = wb.create_sheet("Relevance Judge")
    cols = ["job_key", "slug", "gt_bucket", "matchScore", "expected", "relevance_verdict",
            "agrees", "disqualifiers", "reasoning"]
    SR._header(ws, cols, {"job_key": 20, "slug": 16, "gt_bucket": 10, "matchScore": 10, "expected": 11,
                          "relevance_verdict": 12, "agrees": 8, "disqualifiers": 40, "reasoning": 80})
    for r in sorted(rows, key=lambda x: x["job_key"]):
        for j in r.get("judge_relevance", []):
            SR._append(ws, [r["job_key"], j.get("slug"), j.get("gt_bucket"), j.get("matchScore"),
                            j.get("expected"), j.get("relevance_verdict"),
                            ("yes" if j.get("agrees") else "no" if j.get("agrees") is False else ""),
                            j.get("disqualifiers"), j.get("reasoning")])
            rc = ws[ws.max_row]
            SR._verdict_fill(rc[cols.index("agrees")], ("yes",), ("no",))
    return ws


def _failures_sheet(wb, rows):
    ws = wb.create_sheet("Failures")
    cols = ["job_key", "check", "issue", "detail"]
    SR._header(ws, cols, {"job_key": 22, "check": 20, "issue": 32, "detail": 100})
    n = 0
    for r in sorted(rows, key=lambda x: x["job_key"]):
        for f in r.get("fails", []):
            SR._append(ws, [r["job_key"], f["check"], SR.CHECK_LABELS.get(f["check"], f["check"]), f["detail"]])
            ws[ws.max_row][2].fill = SR._fill(SR.RED); n += 1
    if n == 0:
        SR._append(ws, ["(no hard-check failures)", "", "", ""])
        ws["A2"].fill = SR._fill(SR.GREEN); ws["A2"].font = Font(bold=True, color=SR.GREEN_T)
    return ws


def _dataset_sheet(wb, jobs):
    ws = wb.create_sheet("Dataset")
    cols = ["job_key", "job_slug", "title"]
    SR._header(ws, cols, {"job_key": 24, "job_slug": 26, "title": 30})
    for k, j in sorted(jobs.items()):
        SR._append(ws, [k, j.get("slug"), j.get("title")])
    return ws


def build(rows, run_id, jobs):
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    out = os.path.join(config.OUTPUT_DIR, f"db1_matching_scale_{run_id}.xlsx")
    a = _agg(rows)
    wb = Workbook()
    _dashboard(wb.active, a, run_id)
    _jobs_sheet(wb, rows)
    _candidates_sheet(wb, rows)
    _scoring_judge_sheet(wb, rows)
    _relevance_judge_sheet(wb, rows)
    _failures_sheet(wb, rows)
    _dataset_sheet(wb, jobs or {})
    wb.save(out)
    return out

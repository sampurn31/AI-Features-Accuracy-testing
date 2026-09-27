"""
Diff workbook for the two-run AI Sourcing comparison. Matches candidates across run 1 and run 2
by linkedinUrl (fallback id) and diffs every field — matchScore, matchLabel, per-criterion
score+reasoning, and profile fields (esp. `description`, the summary that may vanish in run 2).
Sheets: Dashboard / Candidate Diff / Criterion Diff / Field Changes / Pool Churn.
"""
import json
import os
import re
import statistics
from collections import Counter

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

import config
import scale_report as SR

# profile fields we track for "appeared/disappeared" (the summary is `description`)
_TRACK = ["description", "keySkills", "experience", "education", "position", "company",
          "city", "state", "country", "photoUrl", "linkedinUrl", "highlightTerms", "workexpyr"]


def _key(c):
    li = re.sub(r"\s+", "", (c.get("linkedinUrl") or "").strip().lower())
    return ("li", li) if li else ("id", c.get("id"))


def _truthy(v):
    return bool(v) and (not isinstance(v, str) or v.strip() != "")


def _crit_map(c):
    return {(a.get("criterionText") or "").strip().lower(): a for a in (c.get("matchAnalysis") or [])}


def _diff_candidate(c1, c2):
    d = {"score1": c1.get("matchScore"), "score2": c2.get("matchScore"),
         "label1": c1.get("matchLabel"), "label2": c2.get("matchLabel"),
         "desc1_len": len(c1.get("description") or ""), "desc2_len": len(c2.get("description") or ""),
         "name": f"{c1.get('firstname') or ''} {c1.get('lastname') or ''}".strip(),
         "linkedin": c1.get("linkedinUrl")}
    d["score_delta"] = ((d["score2"] - d["score1"]) if isinstance(d["score1"], (int, float))
                        and isinstance(d["score2"], (int, float)) else None)
    d["label_changed"] = d["label1"] != d["label2"]
    d["desc_lost"] = _truthy(c1.get("description")) and not _truthy(c2.get("description"))
    d["desc_gained"] = not _truthy(c1.get("description")) and _truthy(c2.get("description"))
    # fields present in run1 but gone in run2 (and vice-versa)
    gone, added = [], []
    for f in _TRACK:
        t1, t2 = _truthy(c1.get(f)), _truthy(c2.get(f))
        if t1 and not t2:
            gone.append(f)
        elif t2 and not t1:
            added.append(f)
    d["fields_gone"], d["fields_added"] = gone, added
    # per-criterion diff
    m1, m2 = _crit_map(c1), _crit_map(c2)
    crit = []
    for ct in sorted(set(m1) | set(m2)):
        a1, a2 = m1.get(ct, {}), m2.get(ct, {})
        s1, s2 = a1.get("score"), a2.get("score")
        r1, r2 = (a1.get("reasoning") or ""), (a2.get("reasoning") or "")
        crit.append({"criterionText": a1.get("criterionText") or a2.get("criterionText"),
                     "weight": a1.get("weight") or a2.get("weight"), "score1": s1, "score2": s2,
                     "delta": (s2 - s1) if isinstance(s1, (int, float)) and isinstance(s2, (int, float)) else None,
                     "reason1": r1, "reason2": r2, "reason_changed": r1.strip() != r2.strip()})
    d["criteria"] = crit
    d["criteria_reason_changed"] = sum(1 for c in crit if c["reason_changed"])
    d["criteria_score_changed"] = sum(1 for c in crit if c["delta"] not in (None, 0))
    return d


def _load(run_id):
    base = os.path.join(config.OUTPUT_DIR, f"compare_{run_id}")
    r1 = json.load(open(base + "_run1.json"))["snapshot"]
    r2 = json.load(open(base + "_run2.json"))["snapshot"]
    return r1, r2


def build(run_id):
    r1, r2 = _load(run_id)
    per_query = {}          # qid -> {common:[diff], only1:[c], only2:[c]}
    for qid in sorted(set(r1) | set(r2)):
        a = {_key(c): c for c in r1.get(qid, [])}
        b = {_key(c): c for c in r2.get(qid, [])}
        common = [_diff_candidate(a[k], b[k]) for k in a if k in b]
        per_query[qid] = {"common": common,
                          "only1": [a[k] for k in a if k not in b],
                          "only2": [b[k] for k in b if k not in a],
                          "n1": len(a), "n2": len(b)}
    verdicts = {}
    vpath = os.path.join(config.OUTPUT_DIR, f"compare_{run_id}_verdicts.json")
    if os.path.exists(vpath):
        try:
            verdicts = json.load(open(vpath))
        except ValueError:
            verdicts = {}
    out = os.path.join(config.OUTPUT_DIR, f"sourcing_compare_{run_id}.xlsx")
    wb = Workbook()
    _dashboard(wb.active, per_query, run_id, verdicts)
    _candidate_diff(wb, per_query, verdicts)
    _criterion_summary(wb, per_query)
    _criterion_diff(wb, per_query)
    _field_changes(wb, per_query)
    _pool_churn(wb, per_query)
    wb.save(out)
    return out


def _dashboard(ws, pq, run_id, verdicts=None):
    verdicts = verdicts or {}
    ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False
    for c in range(1, 13):
        ws.column_dimensions[SR.get_column_letter(c)].width = 12
    ws.merge_cells("A1:L1")
    ws["A1"] = "AI Sourcing — Run 1 vs Run 2 comparison (same queries, same filters)"
    ws["A1"].font = Font(bold=True, size=15, color="FFFFFFFF"); ws["A1"].fill = SR._fill(SR.NAVY)
    all_common = [d for q in pq.values() for d in q["common"]]
    deltas = [abs(d["score_delta"]) for d in all_common if d["score_delta"] is not None]
    desc_lost = sum(1 for d in all_common if d["desc_lost"])
    reason_ch = sum(1 for d in all_common if d["criteria_reason_changed"])
    score_ch = sum(1 for d in all_common if d["score_delta"] not in (None, 0))
    r = 3
    r = SR._section(ws, r, "Overall (candidates present in BOTH runs)")
    SR._card(ws, r, 1, "MATCHED", str(len(all_common)), "in both runs (by linkedinUrl)")
    SR._card(ws, r, 4, "SCORE CHANGED", f"{score_ch}", f"of {len(all_common)} matched")
    SR._card(ws, r, 7, "AVG |SCORE Δ|", "—" if not deltas else f"{statistics.mean(deltas):.1f}",
             f"max {max(deltas) if deltas else 0}")
    SR._card(ws, r, 10, "SUMMARY LOST", str(desc_lost), "had description in run1, gone in run2",
             SR.RED_T if desc_lost else SR.GREEN_T)
    r += 4
    SR._card(ws, r, 1, "REASONING CHANGED", str(reason_ch), "candidates w/ ≥1 criterion reason changed")
    fields_gone = Counter(f for d in all_common for f in d["fields_gone"])
    SR._card(ws, r, 4, "FIELDS DROPPED", str(sum(fields_gone.values())),
             "profile-field disappearances (run1→run2)")
    SR._card(ws, r, 7, "LABEL CHANGED", str(sum(1 for d in all_common if d["label_changed"])), "match label differs")
    SR._card(ws, r, 10, "QUERIES", str(len(pq)), "compared")
    r += 4
    if verdicts:
        vt = Counter(v.get("winner") for v in verdicts.values())
        nn = sum(vt[w] for w in ("run1", "run2", "equivalent"))
        w1 = vt["run1"] / nn if nn else 0
        w2 = vt["run2"] / nn if nn else 0
        r = SR._section(ws, r, "SCORING QUALITY VERDICT — blind LLM judge (run 1 = summary fed to scorer vs run 2 = no summary)")
        SR._card(ws, r, 1, "RUN 1 BETTER", f"{vt['run1']} ({w1:.0%})", "summary fed to scorer",
                 SR.GREEN_T if w1 > w2 else SR.NAVY)
        SR._card(ws, r, 4, "RUN 2 BETTER", f"{vt['run2']} ({w2:.0%})", "no summary",
                 SR.GREEN_T if w2 > w1 else SR.NAVY)
        SR._card(ws, r, 7, "EQUIVALENT", str(vt["equivalent"]), "no meaningful difference")
        SR._card(ws, r, 10, "JUDGED", str(nn), "matched candidates")
        r += 4
        headline = ("Run 1 (summary-fed) scores BETTER" if w1 - w2 > 0.1 else
                    "Run 2 (no summary) scores better" if w2 - w1 > 0.1 else "Roughly equivalent")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=12)
        ws.cell(row=r, column=1, value=f"VERDICT: {headline}   (run1 {w1:.0%} vs run2 {w2:.0%}, "
                f"per-candidate in the Candidate Diff 'better scoring (judge)' column)").font = Font(
                    bold=True, size=12, color=SR.GREEN_T)
        r += 2
    r = SR._section(ws, r, "Per-query")
    hdr = ["query", "run1 pool", "run2 pool", "matched", "score-changed", "avg|Δ|", "summary-lost", "reason-changed"]
    ws.append(hdr)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True, size=10, color="FFFFFFFF"); c.fill = SR._fill(SR.BLUE)
    for qid, q in sorted(pq.items()):
        com = q["common"]
        dl = [abs(d["score_delta"]) for d in com if d["score_delta"] is not None]
        ws.append([qid, q["n1"], q["n2"], len(com),
                   sum(1 for d in com if d["score_delta"] not in (None, 0)),
                   round(statistics.mean(dl), 1) if dl else None,
                   sum(1 for d in com if d["desc_lost"]),
                   sum(1 for d in com if d["criteria_reason_changed"])])
    r2 = ws.max_row + 2
    ws.cell(row=r2, column=1, value=("Read: matched = same person in both runs. 'Summary lost' = had a "
            "profile description in run 1 but not run 2 (the disappearing summary). Score/reason changes "
            "on identical inputs indicate scoring non-determinism between runs.")).font = Font(italic=True, size=9, color="808080")


def _vk(li):
    return re.sub(r"\s+", "", (li or "").strip().lower())


def _candidate_diff(wb, pq, verdicts=None):
    verdicts = verdicts or {}
    ws = wb.create_sheet("Candidate Diff")
    cols = ["query", "name", "linkedin", "score1", "score2", "score_delta",
            "scoring_verdict", "verdict_conf", "label1", "label2",
            "desc1_len", "desc2_len", "summary_lost", "reason_changed", "score_ch_crit",
            "fields_gone", "fields_added"]
    SR._header(ws, cols, {"query": 18, "name": 20, "linkedin": 30, "score1": 8, "score2": 8,
                          "score_delta": 9, "scoring_verdict": 13, "verdict_conf": 10,
                          "label1": 13, "label2": 13, "desc1_len": 9, "desc2_len": 9,
                          "summary_lost": 10, "reason_changed": 11, "score_ch_crit": 10,
                          "fields_gone": 26, "fields_added": 20},
               display={"scoring_verdict": "better scoring (judge)", "verdict_conf": "judge conf",
                        "score1": "run1 score", "score2": "run2 score"})
    for qid, q in sorted(pq.items()):
        for d in sorted(q["common"], key=lambda x: (abs(x["score_delta"]) if x["score_delta"] is not None else -1), reverse=True):
            v = verdicts.get(f"{qid}|{_vk(d['linkedin'])}") or {}
            SR._append(ws, [qid, d["name"], d["linkedin"], d["score1"], d["score2"], d["score_delta"],
                            v.get("winner", ""), v.get("confidence", ""),
                            d["label1"], d["label2"], d["desc1_len"], d["desc2_len"],
                            "YES" if d["desc_lost"] else "", d["criteria_reason_changed"],
                            d["criteria_score_changed"], ", ".join(d["fields_gone"]), ", ".join(d["fields_added"])])
            rc = ws[ws.max_row]
            SR._verdict_fill(rc[cols.index("scoring_verdict")], ("run1",), ("run2",))
            SR._verdict_fill(rc[cols.index("summary_lost")], (), ("YES",))
            sd = rc[cols.index("score_delta")]
            if isinstance(sd.value, (int, float)) and abs(sd.value) > 5:
                sd.fill = SR._fill(SR.AMBER)
    return ws


def _criterion_diff(wb, pq):
    ws = wb.create_sheet("Criterion Diff")
    cols = ["query", "name", "criterionText", "weight", "score1", "score2", "delta",
            "reason_changed", "reason1", "reason2"]
    SR._header(ws, cols, {"query": 16, "name": 18, "criterionText": 34, "weight": 6, "score1": 7,
                          "score2": 7, "delta": 6, "reason_changed": 10, "reason1": 60, "reason2": 60})
    for qid, q in sorted(pq.items()):
        for d in q["common"]:
            for c in d["criteria"]:
                if c["delta"] in (None, 0) and not c["reason_changed"]:
                    continue   # only show criteria that actually changed
                SR._append(ws, [qid, d["name"], c["criterionText"], c["weight"], c["score1"],
                                c["score2"], c["delta"], "YES" if c["reason_changed"] else "",
                                c["reason1"], c["reason2"]])
                SR._verdict_fill(ws[ws.max_row][cols.index("reason_changed")], (), ("YES",))
    if ws.max_row == 1:
        ws.append(["(no per-criterion score or reasoning changed between runs)"])
        ws["A2"].font = Font(bold=True, color=SR.GREEN_T)
    return ws


def _criterion_summary(wb, pq):
    """Per-criterion (query × criterion) score comparison across matched candidates:
    run1 avg vs run2 avg, avg |Δ|, who scored higher more often, reasoning-change rate."""
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "s1": 0.0, "s2": 0.0, "d": 0.0, "r1hi": 0, "r2hi": 0, "eq": 0, "rchg": 0})
    for qid, q in pq.items():
        for d in q["common"]:
            for c in d["criteria"]:
                s1, s2 = c.get("score1"), c.get("score2")
                if not (isinstance(s1, (int, float)) and isinstance(s2, (int, float))):
                    continue
                g = agg[(qid, c.get("criterionText"), c.get("weight"))]
                g["n"] += 1; g["s1"] += s1; g["s2"] += s2; g["d"] += abs(s2 - s1)
                if s1 > s2: g["r1hi"] += 1
                elif s2 > s1: g["r2hi"] += 1
                else: g["eq"] += 1
                if c.get("reason_changed"): g["rchg"] += 1
    ws = wb.create_sheet("Criterion Comparison")
    cols = ["query", "criterion", "weight", "n", "run1_avg", "run2_avg", "avg_score_delta",
            "run1_higher", "run2_higher", "equal", "better_run", "reasoning_changed_%"]
    SR._header(ws, cols, {"query": 18, "criterion": 42, "weight": 6, "n": 5, "run1_avg": 8,
                          "run2_avg": 8, "avg_score_delta": 9, "run1_higher": 10, "run2_higher": 10,
                          "equal": 6, "better_run": 10, "reasoning_changed_%": 11},
               display={"run1_avg": "run1 avg score", "run2_avg": "run2 avg score",
                        "avg_score_delta": "avg |Δ|", "better_run": "higher avg"})
    for (qid, ct, w), g in sorted(agg.items()):
        if not g["n"]:
            continue
        r1, r2 = g["s1"] / g["n"], g["s2"] / g["n"]
        better = "run1" if r1 - r2 > 1 else "run2" if r2 - r1 > 1 else "~equal"
        SR._append(ws, [qid, ct, w, g["n"], round(r1, 1), round(r2, 1), round(g["d"] / g["n"], 1),
                        g["r1hi"], g["r2hi"], g["eq"], better, round(g["rchg"] / g["n"] * 100)])
        SR._verdict_fill(ws[ws.max_row][cols.index("better_run")], ("run1",), ("run2",))
    return ws


def _field_changes(wb, pq):
    ws = wb.create_sheet("Field Changes")
    gone = Counter(f for q in pq.values() for d in q["common"] for f in d["fields_gone"])
    added = Counter(f for q in pq.values() for d in q["common"] for f in d["fields_added"])
    SR._header(ws, ["field", "dropped run1→run2", "appeared run2 only"], {"field": 22, "dropped run1→run2": 18, "appeared run2 only": 18})
    for f in _TRACK:
        if gone.get(f) or added.get(f):
            SR._append(ws, [f, gone.get(f, 0), added.get(f, 0)])
    if ws.max_row == 1:
        ws.append(["(no profile fields changed presence between runs)"])
    return ws


def _pool_churn(wb, pq):
    ws = wb.create_sheet("Pool Churn")
    SR._header(ws, ["query", "in run1 only", "in run2 only", "note"],
               {"query": 20, "in run1 only": 12, "in run2 only": 12, "note": 60})
    for qid, q in sorted(pq.items()):
        SR._append(ws, [qid, len(q["only1"]), len(q["only2"]),
                        "candidates that did NOT appear in both runs (retrieval/cache churn) — "
                        "excluded from the diff"])
    return ws

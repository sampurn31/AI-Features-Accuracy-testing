#!/usr/bin/env python3
"""
Writes the resume-checked (ground-truth) verdict into Dev_vs_Staging_Report.xlsx.

Rebuild the base report first (clean, without earlier annotations):
    python3 Dev_vs_Staging_Comparator.py --report-only
    python3 score_vs_truth.py
    python3 add_truth_verdict_to_report.py

  • Final Verdict (first sheet) — who parsed better out of 100, per-field accuracy
    vs the resume, work-history/education entry stats, reliability & speed
  • Summary       — agreement columns relabelled; adds Better Env (vs resume),
                    accuracy per env, gap and the decisive reasons
  • Per resume    — adds "Resume says", Staging/Dev status vs resume, winner, note,
                    plus a job-by-job and degree-by-degree table
"""
import json, re
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from score_vs_truth import WEIGHTS, FIELDS, CRITICAL, fmt_date, parse_truth_date

SCRIPT_DIR = Path(__file__).resolve().parent
REPORT     = SCRIPT_DIR / "Dev_vs_Staging_Report.xlsx"
SCORES     = SCRIPT_DIR / "truth_scores.json"
STATE_FILE = SCRIPT_DIR / "dev_vs_staging_state.json"
LOG        = SCRIPT_DIR / "logs" / "dev_vs_staging.log"
ENVS       = ("Staging", "Dev")

BLUE_HDR  = PatternFill("solid", fgColor="1F4E79")
BLUE_FONT = Font(bold=True, color="FFFFFF", size=10)
BOLD      = Font(bold=True)
TITLE     = Font(bold=True, size=14)
WRAP      = Alignment(wrap_text=True, vertical="top")
FILL = {
    "Dev":     PatternFill("solid", fgColor="DAEEF3"),
    "Staging": PatternFill("solid", fgColor="FFD580"),
    "Tie":     PatternFill("solid", fgColor="F2F2F2"),
    "Correct": PatternFill("solid", fgColor="E2EFDA"),
    "Partial": PatternFill("solid", fgColor="FFFFE0"),
    "Unverified extra": PatternFill("solid", fgColor="FFFFE0"),
    "Wrong":   PatternFill("solid", fgColor="FFDDC1"),
    "Missing": PatternFill("solid", fgColor="FFDDC1"),
    "Hallucinated": PatternFill("solid", fgColor="F4B6B6"),
}
SUMMARY_RENAMES = {
    "Score": "Agreement Score (vs Staging)",
    "Verdict": "Agreement w/ Staging (not accuracy)",
    "Critical Failures": "Critical fields that differ",
    "Field Mismatches": "Fields that differ",
}


def hdr(cell, text):
    cell.value, cell.fill, cell.font = text, BLUE_HDR, BLUE_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def pct(n, d):
    return round(n / d * 100, 1) if d else 0.0


def percentile(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))] if vals else None


def field_winner(s, d):
    if abs(d["score"] - s["score"]) < 1e-9:
        return "Tie"
    return "Dev" if d["score"] > s["score"] else "Staging"


def entry_text(e, kind):
    if not e:
        return ""
    if kind == "work":
        return f"{e.get('title','')} @ {e.get('company','')} ({fmt_date(e['start']) if isinstance(e.get('start'), (list, tuple)) or e.get('start') is None else e['start']}–{fmt_date(e['end']) if isinstance(e.get('end'), (list, tuple)) or e.get('end') is None else e['end']})"
    return f"{e.get('degree','')} {e.get('specialization','')} @ {e.get('institute','')} ({fmt_date(e['start']) if isinstance(e.get('start'), (list, tuple)) or e.get('start') is None else e['start']}–{fmt_date(e['end']) if isinstance(e.get('end'), (list, tuple)) or e.get('end') is None else e['end']})"


def _as_date(v):
    return tuple(v) if isinstance(v, list) else v


def parsed_entry_text(e, kind):
    if not e:
        return ""
    s, t = fmt_date(_as_date(e.get("start"))), fmt_date(_as_date(e.get("end")))
    if kind == "work":
        return f"{e.get('title','')} @ {e.get('company','')} ({s}–{t})"
    return f"{e.get('degree','')} {e.get('specialization','')} @ {e.get('institute','')} ({s}–{t})".replace("  ", " ")


def truth_entry_text(e, kind):
    s = fmt_date(parse_truth_date(e.get("start"))) or e.get("start", "")
    t = fmt_date(parse_truth_date(e.get("end"))) or e.get("end", "")
    if kind == "work":
        return f"{e.get('title','')} @ {e.get('company','')} ({s}–{t})"
    return f"{e.get('degree','')} {e.get('specialization','')} @ {e.get('institute','')} ({s}–{t})".replace("  ", " ")


# ─────────────────────────────── Summary ───────────────────────────────────

def annotate_summary(ws, scores):
    for c in ws[1]:
        if c.value in SUMMARY_RENAMES:
            c.value = SUMMARY_RENAMES[c.value]
    col = ws.max_column + 1
    heads = ["Better Env (checked vs resume)", "Staging accuracy %", "Dev accuracy %",
             "Gap (Dev − Staging)", "Why (decisive fields, checked vs resume)"]
    for i, h in enumerate(heads):
        hdr(ws.cell(row=1, column=col + i), h)
    for r in range(2, ws.max_row + 1):
        res = scores.get(ws.cell(row=r, column=1).value)
        if not res:
            continue
        vals = [res["winner"], res["score"]["Staging"], res["score"]["Dev"], res["gap"], res["why"]]
        for i, v in enumerate(vals):
            c = ws.cell(row=r, column=col + i, value=v)
            c.fill = FILL[res["winner"]]
            if i == 4:
                c.alignment = WRAP
    for i, w in enumerate([22, 12, 12, 12, 90]):
        ws.column_dimensions[get_column_letter(col + i)].width = w
    ws.freeze_panes = "B2"


# ─────────────────────────────── per resume ────────────────────────────────

def annotate_resume_sheet(ws, res):
    col = ws.max_column + 1
    heads = ["Resume says (truth)", "Staging vs resume", "Dev vs resume", "Better", "Check detail"]
    for i, h in enumerate(heads):
        hdr(ws.cell(row=1, column=col + i), h)
    S, D = res["fields"]["Staging"], res["fields"]["Dev"]
    for r in range(2, ws.max_row + 1):
        f = ws.cell(row=r, column=1).value
        if f not in S:
            continue
        w = field_winner(S[f], D[f])
        note = "; ".join(x for x in (f"Staging: {S[f]['note']}" if S[f]["note"] else "",
                                     f"Dev: {D[f]['note']}" if D[f]["note"] else "") if x)
        vals = [str(S[f]["truth"]), S[f]["status"], D[f]["status"], w, note]
        for i, v in enumerate(vals):
            c = ws.cell(row=r, column=col + i, value=v)
            c.alignment = WRAP
            if i == 1:
                c.fill = FILL.get(S[f]["status"], FILL["Tie"])
            elif i == 2:
                c.fill = FILL.get(D[f]["status"], FILL["Tie"])
            elif i == 3:
                c.fill = FILL[w]
    for i, w in enumerate([40, 16, 16, 10, 70]):
        ws.column_dimensions[get_column_letter(col + i)].width = w

    # accuracy lines + entry tables below the existing metadata
    row = ws.max_row + 2
    for label, val in (("ACCURACY vs RESUME — STAGING", f"{res['score']['Staging']}%"),
                       ("ACCURACY vs RESUME — DEV", f"{res['score']['Dev']}%"),
                       ("BETTER ENV (vs resume)", res["winner"]),
                       ("WHY", res["why"]),
                       ("ANSWER-KEY NOTES", res.get("truth_notes", ""))):
        ws.cell(row=row, column=1, value=label).font = BOLD
        c = ws.cell(row=row, column=2, value=val)
        c.alignment = WRAP
        row += 1

    for field, kind, title in (("workHistory", "work", "WORK HISTORY — JOB BY JOB"),
                               ("education", "edu", "EDUCATION — DEGREE BY DEGREE")):
        row += 1
        ws.cell(row=row, column=1, value=title).font = BOLD
        row += 1
        for i, h in enumerate(["#", "Resume says", "Staging entry", "Staging", "Dev entry", "Dev"]):
            hdr(ws.cell(row=row, column=i + 1), h)
        row += 1
        truth_items = {}
        for env in ENVS:
            det = res["fields"][env][field]["detail"]
            for m in det.get("rows", []):
                truth_items.setdefault(json.dumps(m["truth"], sort_keys=True), {})[env] = m
            for t in det.get("missed_items", []):
                truth_items.setdefault(json.dumps(t, sort_keys=True), {})[env] = None
        for n, (tk, per_env) in enumerate(truth_items.items(), 1):
            t = json.loads(tk)
            vals = [n, truth_entry_text(t, kind)]
            fills = []
            for env in ENVS:
                m = per_env.get(env)
                if m is None:
                    vals += ["—", "Missed"]
                    fills.append("Missing")
                else:
                    st = "Found" if m["date_score"] == 1 else "Found, date error"
                    vals += [parsed_entry_text(m["parsed"], kind), st]
                    fills.append("Correct" if m["date_score"] == 1 else "Partial")
            for i, v in enumerate(vals):
                c = ws.cell(row=row, column=i + 1, value=v)
                c.alignment = WRAP
            ws.cell(row=row, column=4).fill = FILL[fills[0]]
            ws.cell(row=row, column=6).fill = FILL[fills[1]]
            row += 1
        for env, colp in (("Staging", 3), ("Dev", 5)):
            for e in res["fields"][env][field]["detail"].get("extra_items", []):
                ws.cell(row=row, column=1, value="+")
                ws.cell(row=row, column=2, value="(not in resume / duplicate)")
                ws.cell(row=row, column=colp, value=parsed_entry_text(e, kind)).alignment = WRAP
                ws.cell(row=row, column=colp + 1, value="Extra").fill = FILL["Hallucinated"]
                row += 1


# ─────────────────────────────── Final Verdict ─────────────────────────────

def initial_failures():
    fails = {"Staging": set(), "Dev": set()}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            m = re.search(r"\[(Staging|Dev)\] FAILED (\S+):", line)
            if m:
                fails[m.group(1)].add(m.group(2))
    return fails


def write_final_verdict(wb, scores, state):
    ws = wb.create_sheet(title="Final Verdict", index=0)
    res = list(scores.values())
    n = len(res)
    wins = {k: sum(r["winner"] == k for r in res) for k in ("Dev", "Staging", "Tie")}
    avg = {e: round(sum(r["score"][e] for r in res) / n, 1) for e in ENVS}

    def facc(env, f):
        return round(sum(r["fields"][env][f]["score"] for r in res) / n * 100, 1)

    def fcount(env, f, st):
        return sum(r["fields"][env][f]["status"] == st for r in res)

    crit = {e: round(sum(facc(e, f) for f in CRITICAL) / len(CRITICAL), 1) for e in ENVS}
    fw = {e: sum(field_winner(r["fields"]["Staging"][f], r["fields"]["Dev"][f]) == e
                 for r in res for f in FIELDS) for e in ENVS}

    def entry_stats(env, f):
        keys = ("truth", "matched", "missed", "extra", "date_errors")
        tot = {k: 0 for k in keys}
        for r in res:
            d = r["fields"][env][f]["detail"]
            for k in keys:
                tot[k] += d.get(k, 0) or 0
        return tot

    ws_stats = {e: entry_stats(e, "workHistory") for e in ENVS}
    ed_stats = {e: entry_stats(e, "education") for e in ENVS}

    def sk(env, key):
        vals = [r["fields"][env]["skills"]["detail"].get(key) for r in res
                if r["fields"][env]["skills"]["detail"].get(key) is not None]
        return round(sum(vals) / len(vals) * 100, 1) if vals else "—"

    halluc = {e: sum(r["fields"][e][f]["status"] == "Hallucinated" for r in res for f in FIELDS) for e in ENVS}

    times = {e: [x[e]["elapsed"] for x in state.values() if x.get(e) and x[e]["error"] is None] for e in ENVS}
    first_fail = initial_failures()

    gap = avg["Dev"] - avg["Staging"]
    if wins["Dev"] > wins["Staging"] and gap > 0:
        better = "DEV (new model provider)"
    elif wins["Staging"] > wins["Dev"] and gap < 0:
        better = "STAGING (current implementation)"
    else:
        better = "NO CLEAR WINNER"

    row = 1
    ws.cell(row=row, column=1, value="FINAL VERDICT — checked against each resume (not against Staging)").font = TITLE
    row += 2
    ws.cell(row=row, column=1, value="Better parser").font = BOLD
    c = ws.cell(row=row, column=2, value=better)
    c.font = Font(bold=True, size=12)
    c.fill = FILL["Dev"] if better.startswith("DEV") else (FILL["Staging"] if better.startswith("STAGING") else FILL["Tie"])
    row += 2

    def block(title, headers, rows, fills=None):
        nonlocal row
        ws.cell(row=row, column=1, value=title).font = BOLD
        row += 1
        for i, h in enumerate(headers, 1):
            hdr(ws.cell(row=row, column=i), h)
        row += 1
        for vals in rows:
            for i, v in enumerate(vals, 1):
                ws.cell(row=row, column=i, value=v).alignment = WRAP
            row += 1
        row += 1

    block(f"1. Which env parsed each resume better (out of {n})",
          ["Outcome", "Resumes", "%"],
          [("Dev more accurate", wins["Dev"], f"{pct(wins['Dev'], n)}%"),
           ("Staging more accurate", wins["Staging"], f"{pct(wins['Staging'], n)}%"),
           (f"Tie (accuracy gap < 2 points)", wins["Tie"], f"{pct(wins['Tie'], n)}%")])

    block("2. Overall accuracy vs resume",
          ["Metric", "Dev", "Staging", "Δ Dev − Staging"],
          [("Average resume accuracy (weighted, 0–100)", avg["Dev"], avg["Staging"], round(gap, 1)),
           ("Critical fields (name, email, phone, city) %", crit["Dev"], crit["Staging"], round(crit["Dev"] - crit["Staging"], 1)),
           ("Fields won head-to-head (of 1,500)", fw["Dev"], fw["Staging"], fw["Dev"] - fw["Staging"]),
           ("Values not in the resume (hallucinated)", halluc["Dev"], halluc["Staging"], halluc["Dev"] - halluc["Staging"])])

    block("3. Per-field accuracy vs resume (Correct = 100, Partial = 50)",
          ["Field", "Weight", "Dev %", "Staging %", "Δ pp", "Dev correct", "Staging correct",
           "Dev wrong/missing/halluc.", "Staging wrong/missing/halluc."],
          [(f, WEIGHTS[f], facc("Dev", f), facc("Staging", f), round(facc("Dev", f) - facc("Staging", f), 1),
            fcount("Dev", f, "Correct"), fcount("Staging", f, "Correct"),
            sum(fcount("Dev", f, s) for s in ("Wrong", "Missing", "Hallucinated")),
            sum(fcount("Staging", f, s) for s in ("Wrong", "Missing", "Hallucinated")))
           for f in FIELDS])

    block("4. Work history — job by job (all 100 resumes)",
          ["Metric", "Dev", "Staging"],
          [("Jobs in the resumes", ws_stats["Dev"]["truth"], ws_stats["Staging"]["truth"]),
           ("Jobs captured", ws_stats["Dev"]["matched"], ws_stats["Staging"]["matched"]),
           ("Jobs missed", ws_stats["Dev"]["missed"], ws_stats["Staging"]["missed"]),
           ("Extra / duplicate / invented entries", ws_stats["Dev"]["extra"], ws_stats["Staging"]["extra"]),
           ("Captured jobs with a date error", ws_stats["Dev"]["date_errors"], ws_stats["Staging"]["date_errors"]),
           ("Job recall %", pct(ws_stats["Dev"]["matched"], ws_stats["Dev"]["truth"]),
            pct(ws_stats["Staging"]["matched"], ws_stats["Staging"]["truth"])),
           ("Date accuracy on captured jobs %",
            pct(ws_stats["Dev"]["matched"] - ws_stats["Dev"]["date_errors"], ws_stats["Dev"]["matched"]),
            pct(ws_stats["Staging"]["matched"] - ws_stats["Staging"]["date_errors"], ws_stats["Staging"]["matched"]))])

    block("5. Education — degree by degree",
          ["Metric", "Dev", "Staging"],
          [("Degrees in the resumes", ed_stats["Dev"]["truth"], ed_stats["Staging"]["truth"]),
           ("Degrees captured", ed_stats["Dev"]["matched"], ed_stats["Staging"]["matched"]),
           ("Degrees missed", ed_stats["Dev"]["missed"], ed_stats["Staging"]["missed"]),
           ("Extra / duplicate entries", ed_stats["Dev"]["extra"], ed_stats["Staging"]["extra"]),
           ("Captured degrees with a date error", ed_stats["Dev"]["date_errors"], ed_stats["Staging"]["date_errors"])])

    block("6. Skills",
          ["Metric", "Dev", "Staging"],
          [("Extracted skills actually in the resume (precision %)", sk("Dev", "precision"), sk("Staging", "precision")),
           ("Key resume skills captured (recall %)", sk("Dev", "recall"), sk("Staging", "recall"))])

    block("7. Reliability & speed",
          ["Metric", "Dev", "Staging"],
          [("Parse failures on first run (env 503 / timeout)", len(first_fail["Dev"]), len(first_fail["Staging"])),
           ("Failures after retry", sum(1 for x in state.values() if x["Dev"]["error"]),
            sum(1 for x in state.values() if x["Staging"]["error"])),
           ("Avg parse time (s)", round(sum(times["Dev"]) / len(times["Dev"]), 2),
            round(sum(times["Staging"]) / len(times["Staging"]), 2)),
           ("p50 / p95 parse time (s)", f"{percentile(times['Dev'], 50)} / {percentile(times['Dev'], 95)}",
            f"{percentile(times['Staging'], 50)} / {percentile(times['Staging'], 95)}")])

    ws.cell(row=row, column=1, value="Method").font = BOLD
    ws.cell(row=row + 1, column=1, value=(
        "1) A blind answer key was built for every resume from the resume text only (truth/*.json) — reviewers never saw "
        "either parser's output; values are taken verbatim as printed (e.g. 123.pdf name 'Ca In Fr A Ncesco Sum'). "
        "2) score_vs_truth.py marks each env per field: Correct / Partial / Wrong / Missing / Hallucinated; jobs and degrees "
        "are matched one by one and their dates checked. 3) Resume accuracy = weighted mean (weights in section 3); "
        "the more accurate env wins a resume when the gap is ≥ 2 points, otherwise Tie. "
        "The Summary's 'Agreement' columns only show whether Dev matched Staging and are not accuracy.")).alignment = WRAP
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1, end_column=9)
    ws.row_dimensions[row + 1].height = 95
    for i, w in enumerate([52, 10, 12, 12, 12, 13, 15, 22, 24], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    return {"better": better, "wins": wins, "avg": avg, "crit": crit, "field_wins": fw,
            "hallucinated": halluc, "work": ws_stats, "edu": ed_stats,
            "skills": {e: (sk(e, "precision"), sk(e, "recall")) for e in ENVS},
            "fields": {f: (facc("Dev", f), facc("Staging", f)) for f in FIELDS},
            "first_fail": {k: len(v) for k, v in first_fail.items()},
            "times": {e: (round(sum(t) / len(t), 2), percentile(t, 50), percentile(t, 95)) for e, t in times.items()}}


def main():
    scores = json.loads(SCORES.read_text(encoding="utf-8"))
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    wb = openpyxl.load_workbook(REPORT)
    if "Final Verdict" in wb.sheetnames:
        raise SystemExit("Report already annotated — rebuild it with --report-only first.")
    annotate_summary(wb["Summary"], scores)
    for ws in wb.worksheets:
        if ws.title in ("Summary", "Field Agreement"):
            continue
        res = scores.get(ws.title) or next((v for k, v in scores.items() if k[:31] == ws.title), None)
        if res:
            annotate_resume_sheet(ws, res)
    stats = write_final_verdict(wb, scores, state)
    wb.save(REPORT)
    print(json.dumps(stats, indent=1, default=str))


if __name__ == "__main__":
    main()

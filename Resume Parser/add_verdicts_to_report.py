#!/usr/bin/env python3
"""
Merges the ground-truth review (review_verdicts/*.json) into
Dev_vs_Staging_Report.xlsx:

  • Summary sheet     → adds "Better Env (Analysis)", "Why", "Dev fields won",
                         "Staging fields won" columns
  • Per-resume sheets → adds "Ground-Truth Winner" + "Reviewer Note" columns
  • Final Verdict     → new first sheet: overall wins out of 100, per-field
                         correctness for each env, reliability & speed, verdict

Run after Dev_vs_Staging_Comparator.py has written the report.
"""
import json, re, glob
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

SCRIPT_DIR = Path(__file__).resolve().parent
REPORT     = SCRIPT_DIR / "Dev_vs_Staging_Report.xlsx"
STATE_FILE = SCRIPT_DIR / "dev_vs_staging_state.json"
VERDICTS   = SCRIPT_DIR / "review_verdicts"
LOG        = SCRIPT_DIR / "logs" / "dev_vs_staging.log"

FIELDS = ["fullName", "email", "phone", "city", "state", "country", "currentTitle", "currentOrg",
          "workExpYears", "skills", "qualification", "workHistory", "education", "linkedin", "postalCode"]
CRITICAL = ["fullName", "email", "phone", "city"]

BLUE_HDR   = PatternFill("solid", fgColor="1F4E79")
BLUE_FONT  = Font(bold=True, color="FFFFFF", size=10)
BOLD       = Font(bold=True)
TITLE      = Font(bold=True, size=14)
WRAP       = Alignment(wrap_text=True, vertical="top")
FILL = {
    "Dev":     PatternFill("solid", fgColor="DAEEF3"),
    "Staging": PatternFill("solid", fgColor="FFD580"),
    "Both":    PatternFill("solid", fgColor="E2EFDA"),
    "Tie":     PatternFill("solid", fgColor="F2F2F2"),
    "Neither": PatternFill("solid", fgColor="FFDDC1"),
}


def load_verdicts() -> dict:
    out = {}
    for f in sorted(glob.glob(str(VERDICTS / "*.json"))):
        for v in json.loads(Path(f).read_text(encoding="utf-8")):
            out[v["resume"]] = v
    return out


def winner(v, field):
    return (v["fields"].get(field) or {}).get("winner", "Both")


def _hdr(cell, text):
    cell.value, cell.fill, cell.font = text, BLUE_HDR, BLUE_FONT
    cell.alignment = Alignment(horizontal="center", wrap_text=True)


def pct(n, d):
    return round(n / d * 100, 1) if d else 0.0


def percentile(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))] if vals else None


def annotate_summary(ws, verdicts):
    col = ws.max_column + 1
    for i, h in enumerate(["Better Env (Analysis)", "Why", "Dev fields won", "Staging fields won"]):
        _hdr(ws.cell(row=1, column=col + i), h)
    for r in range(2, ws.max_row + 1):
        name = ws.cell(row=r, column=1).value
        v = verdicts.get(name)
        if not v:
            continue
        dev_w = sum(winner(v, f) == "Dev" for f in FIELDS)
        stg_w = sum(winner(v, f) == "Staging" for f in FIELDS)
        vals = [v["overall"], v.get("overall_reason", ""), dev_w, stg_w]
        for i, val in enumerate(vals):
            c = ws.cell(row=r, column=col + i, value=val)
            c.fill = FILL.get(v["overall"], FILL["Tie"])
            if i == 1:
                c.alignment = WRAP
    ws.column_dimensions[get_column_letter(col)].width = 20
    ws.column_dimensions[get_column_letter(col + 1)].width = 70
    ws.column_dimensions[get_column_letter(col + 2)].width = 14
    ws.column_dimensions[get_column_letter(col + 3)].width = 16


def annotate_resume_sheet(ws, v):
    col = ws.max_column + 1
    _hdr(ws.cell(row=1, column=col), "Ground-Truth Winner")
    _hdr(ws.cell(row=1, column=col + 1), "Reviewer Note")
    for r in range(2, ws.max_row + 1):
        field = ws.cell(row=r, column=1).value
        if field not in v["fields"]:
            continue
        w = winner(v, field)
        ws.cell(row=r, column=col, value=w).fill = FILL.get(w, FILL["Tie"])
        ws.cell(row=r, column=col + 1, value=v["fields"][field].get("note", "")).alignment = WRAP
    ws.column_dimensions[get_column_letter(col)].width = 20
    ws.column_dimensions[get_column_letter(col + 1)].width = 70


def initial_failures():
    """Resumes that errored on first run (before --retry-failed), per env, from the log."""
    fails = {"Staging": set(), "Dev": set()}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            m = re.search(r"\[(Staging|Dev)\] FAILED (\S+):", line)
            if m:
                fails[m.group(1)].add(m.group(2))
    return fails


def write_final_verdict(wb, verdicts, state):
    ws = wb.create_sheet(title="Final Verdict", index=0)
    n = len(verdicts)
    overall = {k: sum(v["overall"] == k for v in verdicts.values()) for k in ("Dev", "Staging", "Tie")}

    # per-field correctness: env is correct when winner is Both or itself
    field_rows = []
    for f in FIELDS:
        ws_ = [winner(v, f) for v in verdicts.values()]
        c = {k: ws_.count(k) for k in ("Dev", "Staging", "Both", "Neither")}
        field_rows.append((f, c, pct(c["Both"] + c["Dev"], n), pct(c["Both"] + c["Staging"], n)))

    crit_dev = pct(sum(r[1]["Both"] + r[1]["Dev"] for r in field_rows if r[0] in CRITICAL), n * len(CRITICAL))
    crit_stg = pct(sum(r[1]["Both"] + r[1]["Staging"] for r in field_rows if r[0] in CRITICAL), n * len(CRITICAL))
    all_dev  = pct(sum(r[1]["Both"] + r[1]["Dev"] for r in field_rows), n * len(FIELDS))
    all_stg  = pct(sum(r[1]["Both"] + r[1]["Staging"] for r in field_rows), n * len(FIELDS))
    dev_field_wins = sum(r[1]["Dev"] for r in field_rows)
    stg_field_wins = sum(r[1]["Staging"] for r in field_rows)

    times = {l: [e[l]["elapsed"] for e in state.values() if e.get(l) and e[l]["error"] is None]
             for l in ("Staging", "Dev")}
    first_fail = initial_failures()
    final_fail = {l: sum(1 for e in state.values() if e.get(l, {}).get("error")) for l in ("Staging", "Dev")}

    if overall["Dev"] > overall["Staging"] and all_dev >= all_stg and crit_dev >= crit_stg:
        better = "Dev (new model provider)"
    elif overall["Staging"] > overall["Dev"] and all_stg >= all_dev and crit_stg >= crit_dev:
        better = "Staging (current implementation)"
    else:
        better = "Mixed — see field breakdown"

    row = 1
    ws.cell(row=row, column=1, value="FINAL VERDICT — Dev (new provider) vs Staging (current)").font = TITLE
    row += 2
    ws.cell(row=row, column=1, value="Better parser").font = BOLD
    c = ws.cell(row=row, column=2, value=better)
    c.font = Font(bold=True, size=12)
    c.fill = FILL["Dev"] if better.startswith("Dev") else (FILL["Staging"] if better.startswith("Staging") else FILL["Tie"])
    row += 2

    def block(title, rows, headers=None):
        nonlocal row
        ws.cell(row=row, column=1, value=title).font = BOLD
        row += 1
        if headers:
            for i, h in enumerate(headers, 1):
                _hdr(ws.cell(row=row, column=i), h)
            row += 1
        for vals in rows:
            for i, val in enumerate(vals, 1):
                ws.cell(row=row, column=i, value=val)
            row += 1
        row += 1

    block(f"Resume-level: which env parsed better (out of {n})",
          [("Dev better", overall["Dev"], f"{pct(overall['Dev'], n)}%"),
           ("Staging better", overall["Staging"], f"{pct(overall['Staging'], n)}%"),
           ("Tie (no material difference)", overall["Tie"], f"{pct(overall['Tie'], n)}%")],
          ["Outcome", "Resumes", "%"])

    block("Field-level accuracy vs resume (correct = judged right or equal-best)",
          [("All fields", f"{all_dev}%", f"{all_stg}%"),
           ("Critical fields (name, email, phone, city)", f"{crit_dev}%", f"{crit_stg}%"),
           ("Field-level outright wins", dev_field_wins, stg_field_wins)],
          ["Metric", "Dev", "Staging"])

    block("Per-field breakdown",
          [(f, fc["Dev"], fc["Staging"], fc["Both"], fc["Neither"], f"{d}%", f"{s}%", round(d - s, 1))
           for f, fc, d, s in field_rows],
          ["Field", "Dev won", "Staging won", "Both correct", "Neither", "Dev correct %", "Staging correct %", "Δ (Dev−Stg) pp"])

    block("Reliability & speed",
          [("Parse failures on first run", len(first_fail["Dev"]), len(first_fail["Staging"])),
           ("Failures after retry", final_fail["Dev"], final_fail["Staging"]),
           ("Avg parse time (s)", round(sum(times["Dev"]) / len(times["Dev"]), 2) if times["Dev"] else "—",
            round(sum(times["Staging"]) / len(times["Staging"]), 2) if times["Staging"] else "—"),
           ("p50 parse time (s)", percentile(times["Dev"], 50), percentile(times["Staging"], 50)),
           ("p95 parse time (s)", percentile(times["Dev"], 95), percentile(times["Staging"], 95))],
          ["Metric", "Dev", "Staging"])

    ws.cell(row=row, column=1, value="Method").font = BOLD
    ws.cell(row=row + 1, column=1, value=(
        "Each resume's text was compared against both parsers' output, field by field, using "
        "review_rubric.md. 'Both' = both correct or only cosmetic difference; a winner is only "
        "given for a material difference. Resume-level 'Better Env' is the holistic call, weighting "
        "contact fields and work history most.")).alignment = WRAP
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1, end_column=8)
    ws.row_dimensions[row + 1].height = 60

    widths = [44, 14, 14, 14, 12, 16, 18, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    return dict(better=better, overall=overall, n=n, all_dev=all_dev, all_stg=all_stg,
                crit_dev=crit_dev, crit_stg=crit_stg, field_rows=field_rows,
                first_fail={k: len(v) for k, v in first_fail.items()}, final_fail=final_fail,
                times={k: (round(sum(v) / len(v), 2), percentile(v, 50), percentile(v, 95)) for k, v in times.items() if v})


def main():
    verdicts = load_verdicts()
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    wb = openpyxl.load_workbook(REPORT)
    annotate_summary(wb["Summary"], verdicts)
    for ws in wb.worksheets:
        name = ws.cell(row=1, column=1).value
        if ws.title in ("Summary", "Field Agreement"):
            continue
        # per-resume sheet titles are the (possibly truncated) resume names
        v = verdicts.get(ws.title) or next((x for k, x in verdicts.items() if k[:31] == ws.title), None)
        if v:
            annotate_resume_sheet(ws, v)
    stats = write_final_verdict(wb, verdicts, state)
    wb.save(REPORT)
    print(json.dumps(stats, indent=1, default=str))


if __name__ == "__main__":
    main()

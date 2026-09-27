"""
Workbook builder for the end-to-end suite.

One file, eight sheets, written so a reviewer can go from "what broke" to the raw
request/response without leaving the workbook:

  Dashboard   pass rate per stage, issue counts by type, worst families
  Issues      every deterministic failure + every judge-raised defect, one row each
  Cases       one row per case: what went in, what came out, how many defects
  Filters     expected vs actual per filter field, side by side across the 3 stages
  Q&A         questions asked, materiality verdict, and what answering them did
  Mutations   criterion edits: what changed, what should have, churn
  Candidates  judged results: should this person have appeared, is the reasoning grounded
  Evidence    per-case JSON payloads (truncated) so any row can be verified by hand
"""
import json
import os
import re

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config

INK = "1C2733"
NAVY = "0E7C74"
RED = "B3362B"
AMBER = "A26A08"
GREEN = "2E7D46"
GREY = "7A8794"
HEAD_FILL = PatternFill("solid", fgColor="F1EFE9")
RED_FILL = PatternFill("solid", fgColor="F7E9E7")
AMBER_FILL = PatternFill("solid", fgColor="F7EFDE")
GREEN_FILL = PatternFill("solid", fgColor="E7F1E9")
THIN = Side(style="thin", color="D8D5CE")
BORDER = Border(bottom=THIN)
ILLEGAL = re.compile(r"[\000-\010\013\014\016-\037]")

SEV_FILL = {"high": RED_FILL, "medium": AMBER_FILL, "low": None}


def _clean(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    v = str(v)
    v = ILLEGAL.sub("", v)
    return v[:32000]


def _header(ws, cols, widths=None):
    ws.append(cols)
    for i, c in enumerate(cols, 1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, size=9, color=INK)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, 18)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"


def _row(ws, vals, fill=None):
    ws.append([_clean(v) for v in vals])
    r = ws.max_row
    for i in range(1, len(vals) + 1):
        c = ws.cell(row=r, column=i)
        c.alignment = Alignment(vertical="top", wrap_text=True)
        c.font = Font(size=9)
        if fill:
            c.fill = fill
    return r


def _card(ws, row, col, title, value, sub="", color=NAVY):
    t = ws.cell(row=row, column=col, value=title)
    t.font = Font(bold=True, size=8, color=GREY)
    v = ws.cell(row=row + 1, column=col, value=value)
    v.font = Font(bold=True, size=20, color=color)
    s = ws.cell(row=row + 2, column=col, value=sub)
    s.font = Font(size=8, color=GREY)


def _pct(a, b):
    return "n/a" if not b else f"{a / b:.0%}"


# ── sheets ───────────────────────────────────────────────────────────────────
def _sheet_issues(wb, rows):
    ws = wb.create_sheet("Issues")
    _header(ws, ["Severity", "Code", "Stage", "Case", "Family", "Field", "What happened",
                 "Query (the input that triggers it)", "Mutation"],
            {1: 9, 2: 22, 3: 14, 4: 24, 5: 13, 6: 16, 7: 78, 8: 52, 9: 15})
    order = {"high": 0, "medium": 1, "low": 2}
    items = []
    for r in rows:
        for f in r.get("fails") or []:
            items.append((order.get(f.get("severity"), 3), f, r))
    items.sort(key=lambda x: (x[0], x[1].get("code", "")))
    for _, f, r in items:
        _row(ws, [f.get("severity"), f.get("code"), f.get("stage"), r.get("case_id"),
                  r.get("family"), f.get("field"), f.get("detail"), r.get("query"),
                  f.get("mutation", "")], SEV_FILL.get(f.get("severity")))
    return len(items)


def _sheet_cases(wb, rows):
    ws = wb.create_sheet("Cases")
    _header(ws, ["Case", "Family", "Size", "DB", "Query", "Config HTTP", "State", "#Q", "#Criteria",
                 "C2F HTTP", "Search HTTP", "totalResults", "#Returned", "High", "Med", "Low",
                 "Top issue", "Note"],
            {1: 24, 2: 13, 3: 11, 4: 5, 5: 50, 6: 10, 7: 18, 8: 5, 9: 9, 10: 9, 11: 11,
             12: 12, 13: 10, 14: 6, 15: 6, 16: 6, 17: 30, 18: 40})
    for r in rows:
        st = r.get("stages") or {}
        fails = r.get("fails") or []
        hi = sum(1 for f in fails if f.get("severity") == "high")
        me = sum(1 for f in fails if f.get("severity") == "medium")
        lo = sum(1 for f in fails if f.get("severity") == "low")
        search = st.get("search") or {}
        pages = search.get("pages") or []
        _row(ws, [r.get("case_id"), r.get("family"), r.get("size"), r.get("db"), r.get("query"),
                  (st.get("config") or {}).get("http"), (st.get("config") or {}).get("state"),
                  len((st.get("config") or {}).get("questions") or []),
                  len((st.get("config") or {}).get("criteria") or []),
                  (st.get("c2f") or {}).get("http"),
                  pages[0]["http"] if pages else search.get("skipped", ""),
                  search.get("total"), len(search.get("candidates") or []),
                  hi, me, lo, fails[0]["code"] if fails else "", r.get("note")],
             RED_FILL if hi else (AMBER_FILL if me else GREEN_FILL))


def _sheet_filters(wb, rows):
    ws = wb.create_sheet("Filters")
    _header(ws, ["Case", "Query", "Stage", "Titles", "Skills must", "Skills nice", "Locations",
                 "Companies", "Employment (co/title)", "Industries", "Education", "Universities",
                 "Grad year", "Languages", "Keywords", "Excluded (bucket)", "YoE", "Expected"],
            {1: 22, 2: 40, 3: 14, 4: 26, 5: 22, 6: 20, 7: 22, 8: 18, 9: 16, 10: 20, 11: 20,
             12: 16, 13: 10, 14: 16, 15: 26, 16: 24, 17: 12, 18: 40})
    import e2e_checks as CH
    for r in rows:
        st = r.get("stages") or {}
        exp = json.dumps((r.get("expect") or {}).get("filters") or {}, ensure_ascii=False)
        stages = [("config", (st.get("config") or {}).get("llmFilters")),
                  ("c2f", (st.get("c2f") or {}).get("llmFilters")),
                  ("search queryUsed", (st.get("search") or {}).get("queryUsed"))]
        for label, raw in stages:
            if not raw:
                continue
            n = CH.normalize_filters(raw)
            _row(ws, [r.get("case_id"), r.get("query"), label,
                      ", ".join(n["titles"]), ", ".join(n["skills_must"] + n["tools_must"]),
                      ", ".join(n["skills_good"] + n["tools_good"]), ", ".join(n["locations"]),
                      ", ".join(n["companies"]),
                      f"{n['company_employment']} / {n['title_employment']}",
                      ", ".join(n["industries"]), ", ".join(n["education"]),
                      ", ".join(n["universities"]), n["graduation_year"],
                      ", ".join(n["languages"]), ", ".join(n["keywords"]),
                      f"{', '.join(n['excluded'])} [{n['excluded_bucket']}]" if n["excluded"] else "",
                      f"{n['yoe_min']}-{n['yoe_max']}" if (n["yoe_min"] or n["yoe_max"]) else "",
                      exp if label == "c2f" else ""])


def _sheet_qa(wb, rows):
    ws = wb.create_sheet("Q&A")
    _header(ws, ["Case", "Query", "#Q", "Questions asked", "Verdict", "Redundant (already in query)",
                 "Immaterial", "Missed ambiguity", "Skip-all state", "Answered state",
                 "Answer influence", "Landed in", "Expected in", "Search accepts answers?",
                 "Judge reasoning"],
            {1: 22, 2: 38, 3: 5, 4: 46, 5: 10, 6: 34, 7: 28, 8: 32, 9: 16, 10: 16, 11: 13,
             12: 20, 13: 20, 14: 16, 15: 52})
    for r in rows:
        st, j = r.get("stages") or {}, r.get("judges") or {}
        cfg = st.get("config") or {}
        jq, ja = j.get("questions") or {}, j.get("answers") or {}
        sa = st.get("search_with_answers") or {}
        fill = None
        if ja.get("influence") in ("wrong", "no_change") or (sa and sa.get("http") not in (200, None)):
            fill = RED_FILL
        elif jq.get("redundant") or jq.get("over_asking") == "yes":
            fill = AMBER_FILL
        _row(ws, [r.get("case_id"), r.get("query"), len(cfg.get("questions") or []),
                  "\n".join(cfg.get("questions") or []), jq.get("verdict"),
                  jq.get("redundant"), jq.get("immaterial"), jq.get("missed"),
                  (st.get("config_skip") or {}).get("state"),
                  (st.get("config_answered") or {}).get("state"),
                  ja.get("influence"), ja.get("landed_in"), ja.get("expected_in"),
                  f"HTTP {sa.get('http')}" if sa else "",
                  (jq.get("reasoning") or "") + (("\n\nANSWERS: " + ja["reasoning"]) if ja.get("reasoning") else "")],
             fill)


def _sheet_mutations(wb, rows):
    ws = wb.create_sheet("Mutations")
    _header(ws, ["Case", "Query", "Edit", "HTTP", "Churn (unrelated change)", "Values added",
                 "Values dropped", "Deterministic failures", "Judge: change correct?",
                 "Judge reasoning"],
            {1: 22, 2: 34, 3: 46, 4: 7, 5: 14, 6: 34, 7: 34, 8: 50, 9: 14, 10: 52})
    for r in rows:
        for m in (r.get("stages") or {}).get("mutations") or []:
            jm = m.get("judge") or {}
            fails = m.get("fails") or []
            fill = RED_FILL if (fails or jm.get("change_correct") == "no") else (
                AMBER_FILL if (m.get("churn") or 0) > 0.5 else None)
            _row(ws, [r.get("case_id"), r.get("query"), m.get("desc"), m.get("http"),
                      m.get("churn"), m.get("added"), m.get("dropped"),
                      "; ".join(f"{f['code']}: {f['detail']}" for f in fails),
                      jm.get("change_correct"), jm.get("reasoning")], fill)


def _sheet_candidates(wb, rows):
    ws = wb.create_sheet("Candidates")
    _header(ws, ["Case", "Query", "Rank", "Name", "Position", "Company", "Location",
                 "System score", "Judge score", "Score verdict", "Relevance", "Should appear?",
                 "Why not", "Grounding", "Fabricated claims", "Judge reasoning"],
            {1: 22, 2: 34, 3: 6, 4: 22, 5: 30, 6: 22, 7: 20, 8: 11, 9: 10, 10: 12, 11: 11,
             12: 12, 13: 40, 14: 12, 15: 30, 16: 52})
    for r in rows:
        cands = {c.get("name"): c for c in ((r.get("stages") or {}).get("search") or {}).get("candidates") or []}
        for v in (r.get("judges") or {}).get("candidates") or []:
            c = cands.get(v.get("candidate")) or {}
            fill = RED_FILL if v.get("should_appear") == "no" or v.get("grounding") == "fabricated" else (
                AMBER_FILL if v.get("relevance") == "borderline" else None)
            _row(ws, [r.get("case_id"), r.get("query"), v.get("rank"), v.get("candidate"),
                      v.get("position"), c.get("company"),
                      f"{c.get('city') or ''} {c.get('country') or ''}".strip(),
                      v.get("matchScore"), v.get("judge_score"), v.get("score_verdict"),
                      v.get("relevance"), v.get("should_appear"), v.get("mismatch_reasons"),
                      v.get("grounding"), v.get("fabricated_claims"), v.get("reasoning")], fill)


def _sheet_evidence(wb, rows):
    ws = wb.create_sheet("Evidence")
    _header(ws, ["Case", "Query", "Config response", "C2F filters", "Search queryUsed",
                 "Pages", "Criteria sent to search"],
            {1: 22, 2: 34, 3: 60, 4: 60, 5: 60, 6: 40, 7: 40})
    for r in rows:
        st = r.get("stages") or {}
        _row(ws, [r.get("case_id"), r.get("query"),
                  json.dumps(st.get("config") or {}, ensure_ascii=False, default=str)[:12000],
                  json.dumps((st.get("c2f") or {}).get("llmFilters") or {}, ensure_ascii=False)[:8000],
                  json.dumps((st.get("search") or {}).get("queryUsed") or {}, ensure_ascii=False)[:8000],
                  json.dumps((st.get("search") or {}).get("pages") or [], ensure_ascii=False)[:4000],
                  json.dumps((st.get("search") or {}).get("criteria_sent") or [], ensure_ascii=False)[:4000]])


def _sheet_dashboard(ws, rows, run_id):
    ws.sheet_view.showGridLines = False
    for col, w in ((1, 30), (2, 20), (3, 20), (4, 20), (5, 20), (6, 22), (7, 22)):
        ws.column_dimensions[get_column_letter(col)].width = w
    t = ws.cell(row=1, column=1, value=f"PW-6818 · end-to-end accuracy · run {run_id}")
    t.font = Font(bold=True, size=15, color=INK)
    ws.cell(row=2, column=1, value=f"env {config.RCRM_ENV} · {len(rows)} cases · "
                                   "query → questions → answers → criteria → filters → edits → "
                                   "search → results").font = Font(size=9, color=GREY)

    fails = [f for r in rows for f in (r.get("fails") or [])]
    hi = [f for f in fails if f.get("severity") == "high"]
    clean = [r for r in rows if not r.get("fails")]
    zero = [r for r in rows
            if ((r.get("stages") or {}).get("search") or {}).get("total") == 0]
    _card(ws, 4, 1, "CASES RUN", len(rows), "full chain each")
    _card(ws, 4, 2, "CLEAN CASES", f"{len(clean)} ({_pct(len(clean), len(rows))})",
          "no defect at any hop", GREEN if len(clean) > len(rows) * 0.6 else AMBER)
    _card(ws, 4, 3, "HIGH-SEV DEFECTS", len(hi), f"{len(fails)} total", RED if hi else GREEN)
    _card(ws, 4, 4, "ZERO-RESULT QUERIES", len(zero), _pct(len(zero), len(rows)),
          RED if zero else GREEN)
    judged = [v for r in rows for v in (r.get("judges") or {}).get("candidates") or []]
    bad = [v for v in judged if v.get("should_appear") == "no"]
    _card(ws, 4, 5, "RESULTS JUDGED", len(judged), "top + tail per case")
    _card(ws, 4, 6, "SHOULD NOT APPEAR", f"{len(bad)} ({_pct(len(bad), len(judged))})",
          "judged irrelevant", RED if bad else GREEN)

    r0 = 9
    ws.cell(row=r0, column=1, value="DEFECTS BY TYPE").font = Font(bold=True, size=10, color=INK)
    counts = {}
    for f in fails:
        counts[f.get("code")] = counts.get(f.get("code"), 0) + 1
    ws.cell(row=r0 + 1, column=1, value="Code").font = Font(bold=True, size=9)
    ws.cell(row=r0 + 1, column=2, value="Count").font = Font(bold=True, size=9)
    ws.cell(row=r0 + 1, column=3, value="High").font = Font(bold=True, size=9)
    for i, (code, n) in enumerate(sorted(counts.items(), key=lambda x: -x[1]), 1):
        ws.cell(row=r0 + 1 + i, column=1, value=code).font = Font(size=9)
        ws.cell(row=r0 + 1 + i, column=2, value=n).font = Font(size=9)
        nh = sum(1 for f in fails if f.get("code") == code and f.get("severity") == "high")
        c = ws.cell(row=r0 + 1 + i, column=3, value=nh)
        c.font = Font(size=9, color=RED if nh else GREY)
    if counts:
        ch = BarChart()
        ch.type = "bar"
        ch.title = "Defects by type"
        ch.height, ch.width = min(2 + len(counts) * 0.7, 16), 12
        data = Reference(ws, min_col=2, min_row=r0 + 1, max_row=r0 + 1 + len(counts))
        cats = Reference(ws, min_col=1, min_row=r0 + 2, max_row=r0 + 1 + len(counts))
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.legend = None
        ws.add_chart(ch, "E9")

    r1 = r0 + len(counts) + 4
    ws.cell(row=r1, column=1, value="BY STAGE").font = Font(bold=True, size=10, color=INK)
    stages = {}
    for f in fails:
        s = f.get("stage") or "?"
        stages.setdefault(s, [0, 0])
        stages[s][0] += 1
        if f.get("severity") == "high":
            stages[s][1] += 1
    for i, (s, (n, nh)) in enumerate(sorted(stages.items(), key=lambda x: -x[1][0]), 1):
        ws.cell(row=r1 + i, column=1, value=s).font = Font(size=9)
        ws.cell(row=r1 + i, column=2, value=f"{n} defects").font = Font(size=9)
        ws.cell(row=r1 + i, column=3, value=f"{nh} high").font = Font(
            size=9, color=RED if nh else GREY)

    r2 = r1 + len(stages) + 3
    ws.cell(row=r2, column=1, value="BY FAMILY (filter dimension)").font = Font(bold=True, size=10, color=INK)
    fam = {}
    for r in rows:
        f = r.get("family") or "?"
        fam.setdefault(f, [0, 0, 0])
        fam[f][0] += 1
        fam[f][1] += len(r.get("fails") or [])
        fam[f][2] += sum(1 for x in r.get("fails") or [] if x.get("severity") == "high")
    ws.cell(row=r2 + 1, column=1, value="Family").font = Font(bold=True, size=9)
    ws.cell(row=r2 + 1, column=2, value="Cases").font = Font(bold=True, size=9)
    ws.cell(row=r2 + 1, column=3, value="Defects").font = Font(bold=True, size=9)
    ws.cell(row=r2 + 1, column=4, value="High").font = Font(bold=True, size=9)
    for i, (name, (nc, nf, nh)) in enumerate(sorted(fam.items(), key=lambda x: -x[1][2]), 1):
        ws.cell(row=r2 + 1 + i, column=1, value=name).font = Font(size=9)
        ws.cell(row=r2 + 1 + i, column=2, value=nc).font = Font(size=9)
        ws.cell(row=r2 + 1 + i, column=3, value=nf).font = Font(size=9)
        c = ws.cell(row=r2 + 1 + i, column=4, value=nh)
        c.font = Font(size=9, color=RED if nh else GREY)

    r3 = r2 + len(fam) + 4
    ws.cell(row=r3, column=1, value="HOW TO READ THIS").font = Font(bold=True, size=10, color=INK)
    for i, line in enumerate([
        "Issues sheet is the work queue — sorted worst first, each row names the exact query that triggers it.",
        "Deterministic codes (MISSING-VALUE, WRONG-BUCKET, EXCLUSION-LEAK, SCORE-FORMULA, DUPLICATE-PERSON,",
        "  REMOVAL-IGNORED, COLLATERAL-LOSS, STAGE-DIVERGENCE) are objective: a hit is a real defect.",
        "Judge codes (IRRELEVANT-RESULT, FIELD-CLASSIFICATION, OVER-CONSTRAINED, ANSWER-MISPLACED) carry",
        "  reasoning in the Q&A / Candidates / Mutations sheets — read it before filing.",
        "SILENT-DROP means the user typed a constraint no filter field expresses and nothing told them.",
        "STAGE-DIVERGENCE means the filters shown in the UI differ from the ones retrieval actually ran.",
        "Evidence sheet holds the raw payloads for every case so any row can be re-verified by hand.",
    ], 1):
        ws.cell(row=r3 + i, column=1, value=line).font = Font(size=9, color=GREY)


def build(rows, run_id):
    wb = Workbook()
    _sheet_dashboard(wb.active, rows, run_id)
    wb.active.title = "Dashboard"
    _sheet_issues(wb, rows)
    _sheet_cases(wb, rows)
    _sheet_filters(wb, rows)
    _sheet_qa(wb, rows)
    _sheet_mutations(wb, rows)
    _sheet_candidates(wb, rows)
    _sheet_evidence(wb, rows)
    path = os.path.join(config.OUTPUT_DIR, f"e2e_report_{run_id}.xlsx")
    wb.save(path)
    return path

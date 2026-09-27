#!/usr/bin/env python3
"""
Re-score a completed run against the PW-7788 PRD, without re-running anything.

The original verdicts in run_accuracy.py encode the MOM's wording — a per-response
data-unavailable *warning*, and "adding or updating a specific candidate field". The PRD
asks for something materially different:

  1. Source data for Edit is the resume text/work history AND "candidate profile
     fields/values SELECTED BY THE USER". So declining to supply a field the user never
     selected is the requirement, not a defect — which turns most of the original
     "silent no-op" findings into passes.
  2. The user-facing message is a "fixed, static banner" that must NOT identify the missing
     fields. So the absence of a per-response warning string is not a defect either; the
     API only has to expose a trigger, which it does as data.usage.no_candidate_data.
  3. "Do not add placeholder text to the resume" is an outright prohibition — which makes
     an inserted N/A a flat violation rather than a judgement call.
  4. Ungrounded requests must still deduct credits AND count towards the edit limit.
  5. The Credit Log must record one row per SESSION, with the edit count in a sub-row.

Every judgement is recomputed from the archived response bodies, so the numbers are the
same observations scored against the right contract.

    python3 rescore_prd.py raw/raw-ai1-20260908-010423.jsonl

Writes reports/PRD-VALIDATION-<stamp>.xlsx.
"""

import json
import os
import sys
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from run_accuracy import cell_safe, visible_text

# ---------------------------------------------------------------------------
# PRD clauses, as the report groups them
# ---------------------------------------------------------------------------
C_SOURCE = "1a. Source data = resume text + fields the user selected"
C_VALIDATE = "1b. Validate prompts not grounded in source data"
C_NO_PLACEHOLDER = "1c. Do not add placeholder text to the resume"
C_BILLING = "1d. Ungrounded requests: credits deducted AND counted"
C_BANNER = "2. Fixed static banner (frontend — not covered by an API run)"
C_UNCHANGED = "0. Instruction validation unchanged"
C_OTHER = "Outside the PRD's stated scope"

P_PASS = "PASS"
P_DEFECT = "DEFECT"
P_INFO = "recorded — needs a Product/dev answer"
P_NA = "not applicable"

FILL_DEFECT = PatternFill("solid", fgColor="F4CCCC")
FILL_PASS = PatternFill("solid", fgColor="D9EAD3")
FILL_INFO = PatternFill("solid", fgColor="FFF2CC")
FILL_HEADER = PatternFill("solid", fgColor="DDDDDD")

# Placeholder text the PRD forbids outright. Matched against the document's VISIBLE text so
# a CSS token or an attribute cannot trigger it.
PLACEHOLDERS = ["n/a", "not available", "not specified", "not provided", "unknown",
                "to be confirmed", "tbd", "no data", "none provided"]

VALIDATION_CATEGORIES = ("K. Relevance", "L. Prompt injection", "M. Length")

# Rows whose automatic verdict was checked by hand against the archived HTML and found
# wrong. Recorded here rather than silently dropped, so the reasoning stays auditable.
KNOWN_FALSE_POSITIVES = {
    "awk-instruction-field":
        "Verified against the archived HTML: the custom field's value was rendered "
        "VERBATIM as data under Additional Information, and it is already present in the "
        "generated resume before any edit. The word that triggered the check is part of "
        "the stored value, not evidence the instruction was obeyed — instruction-like text "
        "in a candidate field is being treated as data, which is correct.",
}

# Redaction masks a field the record does hold, so its absence from the result is the
# redaction feature working, not a source-data failure.
REDACTION_IDS_PREFIX = "red-"


# Categories that ask nothing of the candidate record. A failed edit in one of these is a
# model capability limit, not a source-data question, so the PRD's billing clause for
# ungrounded requests does not apply to it.
NON_DATA_CATEGORIES = ("A. Styling", "J. Images", "AD. Request-shape", "M. Length",
                       "K. Relevance", "L. Prompt injection")


def is_data_request(row):
    """True when the instruction asks for information from the candidate record."""
    if row.get("expected_value") not in (None, "", "None"):
        return True
    return not (row.get("category") or "").startswith(NON_DATA_CATEGORIES)


def clause_for(row):
    cat = row.get("category") or ""
    if cat.startswith(VALIDATION_CATEGORIES):
        return C_UNCHANGED
    if cat.startswith(("N. Security", "AD. Request-shape", "P. Billing")):
        return C_OTHER
    if cat.startswith("S. User-supplied"):
        return C_SOURCE
    return None            # decided per observation below


def score(row):
    """(clause, verdict, reason) for one archived response, judged against the PRD."""
    status = row.get("effective_status")
    changed = row.get("html_changed") == "yes"
    ungrounded = bool(row.get("no_candidate_data"))
    selected = row.get("field_selected") or ""
    credits = row.get("credits_consumed")
    counted = isinstance(row.get("edit_count"), int)
    expect = row.get("expect")
    cat = row.get("category") or ""
    html = (row.get("__raw") or {}).get("html") or ""
    vis = visible_text(html).lower()

    # -- placeholder text is an outright prohibition, checked first --------
    if changed:
        hit = [p for p in PLACEHOLDERS if p in vis]
        # Only count it when the instruction invited it or the value was absent: a resume
        # may legitimately contain the phrase in candidate-authored prose.
        invited = any(w in row["instruction"].lower()
                      for w in ("n/a", "missing", "default", "if it is not set",
                                "if not set", "sensible", "empty"))
        if hit and invited:
            return (C_NO_PLACEHOLDER, P_DEFECT,
                    "The PRD forbids adding placeholder text. The instruction asked for a "
                    "placeholder and the document came back containing %s." % ", ".join(hit[:3]))
        forbidden = row.get("forbidden_found") or ""
        if forbidden and not cat.startswith("S. User-supplied") \
                and row["id"] not in KNOWN_FALSE_POSITIVES:
            return (C_NO_PLACEHOLDER, P_DEFECT,
                    "A value not present in the source data was inserted (%s). The PRD "
                    "requires no value rather than an invented one." % forbidden)
        if row["id"] in KNOWN_FALSE_POSITIVES:
            return (C_NO_PLACEHOLDER, P_PASS, KNOWN_FALSE_POSITIVES[row["id"]])

    # -- the edit request itself is source data ----------------------------
    if cat.startswith("S. User-supplied"):
        return (C_SOURCE, P_PASS,
                "The PRD names \"the edit request\" as source data, so using the value the "
                "user supplied is correct. Record value was %s." % row.get("expected_value"))

    # -- instruction validation, explicitly unchanged ----------------------
    if cat.startswith(VALIDATION_CATEGORIES):
        ok = (status != 200) if expect == "refused" else (status == 200)
        return (C_UNCHANGED, P_PASS if ok else P_DEFECT,
                "%s with %s: %s" % ("Refused" if status != 200 else "Accepted",
                                    status, row.get("message")))

    # -- ungrounded request: detected, and must be billed AND counted ------
    if not changed and ungrounded:
        if selected == "selected":
            return (C_SOURCE, P_DEFECT,
                    "The field IS in the user's selected source data, yet the request was "
                    "treated as ungrounded and nothing was added.")
        if credits in (None, 0) or not counted:
            return (C_BILLING, P_DEFECT,
                    "Correctly declined, but the PRD requires the request to be billed and "
                    "counted: credits_consumed=%s, edit_count=%s." % (credits, row.get("edit_count")))
        why = ("no candidate field holds this" if not selected
               else "the field is not in the user's selected source data")
        return (C_SOURCE, P_PASS,
                "Correctly added nothing (%s), no placeholder text, billed %s and counted as "
                "edit %s." % (why, credits, row.get("edit_count")))

    # -- no change, NOT flagged as ungrounded: the unbilled 422 path -------
    if not changed and str(row.get("message", "")).startswith("The requested change could not"):
        # The PRD's billing clause covers requests for information not in the source data.
        # A STYLING edit the model simply could not perform is a different thing: nothing was
        # asked of the candidate record, so the pre-existing unbilled rejection is right.
        # Counting those as violations overstated this clause by 12 rows on the first pass.
        if not is_data_request(row):
            return (C_OTHER, P_PASS,
                    "A styling or layout edit the model could not perform. No candidate data "
                    "was requested, so the pre-existing unbilled 422 is correct — the PRD's "
                    "billing clause is about requests for unavailable information.")
        return (C_BILLING, P_DEFECT,
                "Asked for candidate information, produced no change, but was NOT flagged as "
                "ungrounded — so it took the generic unbilled 422 instead: not billed, not "
                "counted, and the banner cannot trigger. The PRD requires such requests to be "
                "billed and counted towards the AI Edit limit.")

    if not changed and status == 200:
        return (C_VALIDATE, P_INFO,
                "Returned 200 with the document unchanged and no ungrounded signal — no "
                "placeholder was added, but nothing identifies why. Message: %s"
                % row.get("message"))

    # -- refusals outside the validation categories ------------------------
    if status != 200:
        if selected == "selected" or row.get("expected_value") not in (None, "", "None"):
            return (C_BILLING, P_DEFECT,
                    "Refused with %s on a request whose data is in source data, so it was "
                    "neither applied nor billed nor counted: %s" % (status, row.get("message")))
        return (C_VALIDATE, P_INFO,
                "Refused with %s: %s" % (status, row.get("message")))

    # -- applied ------------------------------------------------------------
    if credits in (None, 0):
        return (C_BILLING, P_DEFECT,
                "The edit was applied but nothing was billed (credits_consumed=%s)." % credits)
    if row.get("expected_value") not in (None, "", "None") and row.get("occ_after") == 0:
        if row["id"].startswith(REDACTION_IDS_PREFIX) or (row.get("phase") or "").startswith("redact"):
            return (C_SOURCE, P_PASS,
                    "Redaction was enabled for this field, so leaving its value out of the "
                    "result is the redaction feature working rather than a source-data miss.")
        if selected != "selected":
            return (C_NO_PLACEHOLDER, P_INFO,
                    "The document changed and was billed, but this field is NOT in the user's "
                    "selected source data and its value is absent from the result — so "
                    "something else was inserted. Read the Raw sheet to see whether that is "
                    "an invented value (a PRD violation) or an unrelated edit.")
        return (C_SOURCE, P_DEFECT,
                "Applied and billed, but the source-data value (%s) is not in the result, so "
                "what was inserted did not come from the record."
                % str(row.get("expected_value"))[:50])
    return (C_SOURCE, P_PASS,
            "Applied from source data and billed %s (edit %s)." % (credits, row.get("edit_count")))


COLUMNS = [
    ("prd_clause", 46), ("prd_verdict", 30), ("prd_reason", 96), ("id", 24),
    ("category", 30), ("instruction", 58), ("fixture", 9), ("field_selected", 14),
    ("expected_value", 30), ("occ_before", 11), ("occ_after", 11),
    ("http_status", 11), ("effective_status", 15), ("message", 50),
    ("html_changed", 12), ("no_candidate_data", 17), ("credits_consumed", 16),
    ("edit_count", 11), ("was_scored_as (MOM)", 34), ("note", 56),
]


def style_header(ws, n):
    for c in ws[1][:n]:
        c.font = Font(bold=True)
        c.fill = FILL_HEADER
        c.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "raw/raw-ai1-20260908-010423.jsonl"
    rows = [json.loads(l) for l in open(src) if l.strip()]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join("reports", "PRD-VALIDATION-PW7788-%s.xlsx" % stamp)

    scored = []
    for r in rows:
        clause, verdict, reason = score(r)
        scored.append((clause, verdict, reason, r))

    wb = Workbook()

    # -- Summary ----------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    ws.append(["PW-7788 — Include Candidate Data for AI Edits: validation against the PRD"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    for k, v in [
        ("Source run", os.path.basename(src)),
        ("Scenarios", len(rows)),
        ("Environment", "ai1 / test db"),
        ("Re-scored", stamp),
        ("Scored against", "PW-7788 PRD (Scope 1 and 2), NOT the earlier MOM"),
        ("What changed", "The PRD names the user's SELECTED fields as source data and asks "
                         "for a fixed static banner, so declining an unselected field and "
                         "returning no per-response warning are both correct."),
    ]:
        ws.append([k, str(v)])
    ws.append([])
    ws.append(["PRD clause", "PASS", "DEFECT", "recorded", "total"])
    style_header(ws, 5)
    clauses = [C_UNCHANGED, C_SOURCE, C_VALIDATE, C_NO_PLACEHOLDER, C_BILLING, C_OTHER]
    for cl in clauses:
        mine = [s for s in scored if s[0] == cl]
        if not mine:
            continue
        p = sum(1 for s in mine if s[1] == P_PASS)
        d = sum(1 for s in mine if s[1] == P_DEFECT)
        i = sum(1 for s in mine if s[1] == P_INFO)
        ws.append([cl, p, d, i, len(mine)])
        if d:
            ws.cell(row=ws.max_row, column=3).fill = FILL_DEFECT
        elif p:
            ws.cell(row=ws.max_row, column=2).fill = FILL_PASS
    ws.append([])
    ws.append([C_BANNER, "", "", "not covered", ""])
    ws.append(["3. Credit Log: one row per SESSION with Edit Count",
               "", "DEFECT", "ledger writes one row per EDIT (insert_usage on each "
               "charge_edit, tagging document_id + edit_number)", ""])
    ws.cell(row=ws.max_row, column=3).fill = FILL_DEFECT
    for col, w in zip("ABCDE", (52, 10, 12, 46, 10)):
        ws.column_dimensions[col].width = w

    # -- Results ----------------------------------------------------------
    ws = wb.create_sheet("Results")
    ws.append([c for c, _ in COLUMNS])
    style_header(ws, len(COLUMNS))
    for i, (_, w) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    order = {P_DEFECT: 0, P_INFO: 1, P_PASS: 2}
    for clause, verdict, reason, r in sorted(scored, key=lambda s: (order[s[1]], s[0], s[3]["id"])):
        vals = {"prd_clause": clause, "prd_verdict": verdict, "prd_reason": reason,
                "was_scored_as (MOM)": r.get("verdict")}
        ws.append([cell_safe(vals.get(c, r.get(c, ""))) for c, _ in COLUMNS])
        fill = {P_DEFECT: FILL_DEFECT, P_PASS: FILL_PASS, P_INFO: FILL_INFO}[verdict]
        ws.cell(row=ws.max_row, column=2).fill = fill
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(COLUMNS)), ws.max_row)

    # -- Defects ----------------------------------------------------------
    ws = wb.create_sheet("Defects")
    ws.append(["prd_clause", "id", "instruction", "prd_reason", "message", "credits_consumed"])
    style_header(ws, 6)
    for col, w in zip("ABCDEF", (46, 24, 58, 100, 50, 16)):
        ws.column_dimensions[col].width = w
    for clause, verdict, reason, r in sorted(scored, key=lambda s: (s[0], s[3]["id"])):
        if verdict == P_DEFECT:
            ws.append([cell_safe(x) for x in (clause, r["id"], r["instruction"], reason,
                                              r.get("message"), r.get("credits_consumed"))])

    # -- Retractions ------------------------------------------------------
    ws = wb.create_sheet("Reclassified")
    ws.append(["id", "category", "was (scored against the MOM)", "now (scored against the PRD)",
               "why it changed"])
    style_header(ws, 5)
    for col, w in zip("ABCDE", (24, 30, 34, 16, 104)):
        ws.column_dimensions[col].width = w
    for clause, verdict, reason, r in scored:
        was = r.get("verdict") or ""
        moved = (was in ("SILENT NO-OP (no warning shown)", "FABRICATED value",
                         "unchanged with no no-data signal") and verdict == P_PASS)
        if not moved:
            continue
        why = ("The PRD names the user's SELECTED fields as the source data, so adding "
               "nothing here is the requirement. The missing per-response warning is not a "
               "defect either: the PRD asks for a fixed static banner that must NOT identify "
               "the missing fields, and the API exposes the trigger."
               if was == "SILENT NO-OP (no warning shown)" else
               "The PRD names \"the edit request\" as source data, so a value the user typed "
               "into the instruction is legitimate.")
        ws.append([cell_safe(x) for x in (r["id"], r.get("category"), was, verdict, why)])

    # -- Raw --------------------------------------------------------------
    ws = wb.create_sheet("Raw")
    ws.append(["id", "response envelope", "returned html (first 30000 chars)"])
    style_header(ws, 3)
    for col, w in zip("ABC", (24, 90, 120)):
        ws.column_dimensions[col].width = w
    for _, _, _, r in scored:
        raw = r.get("__raw") or {}
        ws.append([cell_safe(r["id"]), cell_safe(raw.get("envelope", ""))[:32000],
                   cell_safe(raw.get("html", ""))[:30000]])

    wb.save(out)

    d = sum(1 for s in scored if s[1] == P_DEFECT)
    p = sum(1 for s in scored if s[1] == P_PASS)
    i = sum(1 for s in scored if s[1] == P_INFO)
    print("Wrote %s" % out)
    print("  %d PASS · %d DEFECT · %d recorded" % (p, d, i))
    moved = sum(1 for s in scored
                if s[3].get("verdict") in ("SILENT NO-OP (no warning shown)",
                                           "FABRICATED value") and s[1] == P_PASS)
    print("  %d rows reclassified from defect to pass by the PRD" % moved)
    for cl in clauses:
        mine = [s for s in scored if s[0] == cl]
        if mine:
            print("  %-46s %2d pass  %2d defect  %2d recorded" % (
                cl[:46], sum(1 for s in mine if s[1] == P_PASS),
                sum(1 for s in mine if s[1] == P_DEFECT),
                sum(1 for s in mine if s[1] == P_INFO)))


if __name__ == "__main__":
    main()

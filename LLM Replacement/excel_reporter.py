"""
excel_reporter.py
-----------------
Writes a structured Excel workbook from a list of log entries produced by the
test suites.

Layout
------
One sheet per suite  +  one "Summary" sheet at the front.

Each suite sheet columns (in order):
  #  | Test Name | Mode | Input | Response A | Response B | Judge Score
  Judge Reason | Status | Schema A | Schema B | Latency A (ms) | Latency B (ms)
  TTFT A (ms) | TTFT B (ms)

Public API
----------
    write_excel_report(entries: list[dict]) -> str   # returns output file path
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import (
    Alignment, Border, Font, GradientFill, PatternFill, Side
)
from openpyxl.utils import get_column_letter


C_WHITE        = "FFFFFFFF"
C_DARK_BG      = "FF1E2A3A"
C_MID_BG       = "FF243447"
C_LIGHT_BG     = "FFF0F4F8"
C_PASS_BG      = "FFD6F5E3"
C_REVIEW_BG    = "FFFFF3CD"
C_FAIL_BG      = "FFFDE2E2"

C_PASS_TEXT    = "FF0A5C2E"
C_REVIEW_TEXT  = "FF7A4A00"
C_FAIL_TEXT    = "FF7A0000"

C_HEADER_TEXT  = C_WHITE
C_BODY_TEXT    = "FF1E2A3A"
C_BORDER       = "FFCCCED2"
C_ACCENT_BLUE  = "FF2663A8"

SUITE_TAB_COLOURS = [
    "FF2663A8", "FF0F7B6C", "FF7B5F0F", "FF6B0F7B",
    "FF0F2E7B", "FF7B0F35", "FF1E6B3A", "FF5A3A00",
]


def _fill(hex_argb: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_argb)


def _thin_border() -> Border:
    s = Side(style="thin", color=C_BORDER)
    return Border(left=s, right=s, top=s, bottom=s)


def _font(bold: bool = False, colour: str = C_BODY_TEXT, size: int = 10) -> Font:
    return Font(name="Segoe UI", bold=bold, color=colour, size=size)


def _align(wrap: bool = True, h: str = "left", v: str = "top") -> Alignment:
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)


def _header_font() -> Font:
    return Font(name="Segoe UI", bold=True, color=C_HEADER_TEXT, size=10)


def _row_fill(status: str, row_idx: int) -> PatternFill | None:
    """Return a fill for status-highlighted rows; alternating light rows otherwise."""
    s = (status or "").upper()
    if s == "PASS":
        return _fill(C_PASS_BG)
    if s == "REVIEW":
        return _fill(C_REVIEW_BG)
    if s in ("FAIL", "ERROR"):
        return _fill(C_FAIL_BG)
    return _fill(C_LIGHT_BG) if row_idx % 2 == 0 else None


def _status_font(status: str) -> Font:
    s = (status or "").upper()
    if s == "PASS":
        return Font(name="Segoe UI", bold=True, color=C_PASS_TEXT, size=10)
    if s == "REVIEW":
        return Font(name="Segoe UI", bold=True, color=C_REVIEW_TEXT, size=10)
    if s in ("FAIL", "ERROR"):
        return Font(name="Segoe UI", bold=True, color=C_FAIL_TEXT, size=10)
    return _font()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE  = re.compile(r"\s+")


def _strip_html(text: str, max_chars: int = 0) -> str:
    """Strip HTML tags and normalise whitespace. max_chars=0 means no limit."""
    if not text:
        return ""
    out = _TAG_RE.sub(" ", text)
    out = _WS_RE.sub(" ", out).strip()
    if max_chars and len(out) > max_chars:
        out = out[:max_chars] + " …"
    return out


_SUITE_COLUMNS: list[tuple[str, Any, int, bool]] = [
    ("#",                     lambda e, i: i,                                              5,  False),
    ("Test Name",             lambda e, i: e.get("extra", {}).get("test_name", ""),       30,  True),
    ("Mode / Key",            lambda e, i: _mode_key_cell(e),                             20,  True),
    ("Prompt",                lambda e, i: e.get("extra", {}).get("prompt", ""),          50,  True),
    ("Input (full context)",  lambda e, i: _strip_html(e.get("input_text", "")),          50,  True),
    ("Response A",            lambda e, i: _strip_html(e.get("text_a", "")),              55,  True),
    ("Response B",            lambda e, i: _strip_html(e.get("text_b", "")),              55,  True),
    ("Response A (HTML)",     lambda e, i: e.get("text_a", ""),                           55,  True),
    ("Response B (HTML)",     lambda e, i: e.get("text_b", ""),                           55,  True),
    ("Judge Score",           lambda e, i: e.get("judge_score"),                          12,  False),
    ("Judge Reason",          lambda e, i: e.get("extra", {}).get("judge_reason", ""),    55,  True),
    ("Status",                lambda e, i: e.get("final_status", ""),                     12,  False),
    ("Schema A",              lambda e, i: "✓" if e.get("schema_valid_a") else "✗",       10,  False),
    ("Schema B",              lambda e, i: "✓" if e.get("schema_valid_b") else "✗",       10,  False),
    ("Latency A (ms)",        lambda e, i: e.get("latency_ms_a"),                         15,  False),
    ("Latency B (ms)",        lambda e, i: e.get("latency_ms_b"),                         15,  False),
    ("TTFT A (ms)",           lambda e, i: e.get("extra", {}).get("ttft_ms_a"),           14,  False),
    ("TTFT B (ms)",           lambda e, i: e.get("extra", {}).get("ttft_ms_b"),           14,  False),
]


def _mode_key_cell(entry: dict) -> str:
    """
    Compact label shown in the Mode/Key column.
    Picks up mode, key, tone, entity_type, seq_step_type — whichever exist.
    """
    ex = entry.get("extra") or {}
    parts: list[str] = []
    for field in ("mode", "key", "tone", "entity_type", "seq_step_type"):
        v = ex.get(field)
        if v not in (None, ""):
            parts.append(f"{field}: {v}")
    return "\n".join(parts)


def _build_input_cell(entry: dict) -> str:
    """Produce a concise human-readable input description from whatever the suite logged."""
    ex = entry.get("extra") or {}
    parts: list[str] = []

    ep = entry.get("endpoint", "")
    if ep:
        parts.append(f"Endpoint: {ep}")

    for k in ("suite", "action", "seq_step_type", "entity_type"):
        v = ex.get(k)
        if v not in (None, "", ""):
            parts.append(f"{k}: {v}")

    raw_input = entry.get("_input_text", "")
    if raw_input:
        cleaned = _strip_html(raw_input, max_chars=800)
        parts.append(cleaned)

    return "\n".join(parts) if parts else ep


_KNOWN_SUITES = [
    "candidate_summary",
    "job_description",
    "note_summary",
    "call_log_summary",
    "email_template",
    "generate_email",
    "generate_email_sequence",
    "get_suggestions",
]

_SUITE_LABELS = {
    "candidate_summary":       "Candidate Summary",
    "job_description":         "Job Description",
    "note_summary":            "Note Summary",
    "call_log_summary":        "Call Log Summary",
    "email_template":          "Email Template",
    "generate_email":          "Generate Email",
    "generate_email_sequence": "Email Sequence",
    "get_suggestions":         "Get Suggestions",
}


def _suite_of(entry: dict) -> str:
    ex = entry.get("extra") or {}
    s = ex.get("suite", "")
    if s in _SUITE_LABELS:
        return s
    ep = entry.get("endpoint", "")
    for k in _KNOWN_SUITES:
        if k.replace("_", "-") in ep or k in ep:
            return k
    return s or "other"


def _write_summary_sheet(ws, all_entries: list[dict], suite_order: list[str]) -> None:
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:J1")
    c = ws["A1"]
    c.value   = "Neptune LLM Replacement — Test Run Summary"
    c.font    = Font(name="Segoe UI", bold=True, color=C_HEADER_TEXT, size=14)
    c.fill    = _fill(C_DARK_BG)
    c.alignment = _align(wrap=False, h="center", v="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells("A2:J2")
    c = ws["A2"]
    c.value = f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    c.font  = Font(name="Segoe UI", color="FFAABBCC", size=9)
    c.fill  = _fill(C_DARK_BG)
    c.alignment = _align(wrap=False, h="center")

    headers = ["Suite", "Total", "PASS", "REVIEW", "FAIL", "ERROR",
               "Avg Score", "Pass %", "Avg Latency A (ms)", "Avg Latency B (ms)"]
    row = 4
    for col_i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=col_i, value=h)
        c.font      = _header_font()
        c.fill      = _fill(C_MID_BG)
        c.alignment = _align(wrap=False, h="center")
        c.border    = _thin_border()
    ws.row_dimensions[row].height = 20

    suite_entries: dict[str, list[dict]] = defaultdict(list)
    for e in all_entries:
        suite_entries[_suite_of(e)].append(e)

    totals = [0] * 8
    data_row_start = row + 1

    for r_i, suite_key in enumerate(suite_order):
        entries = suite_entries.get(suite_key, [])
        label   = _SUITE_LABELS.get(suite_key, suite_key)
        row += 1
        n       = len(entries)
        passed  = sum(1 for e in entries if e.get("final_status") == "PASS")
        review  = sum(1 for e in entries if e.get("final_status") == "REVIEW")
        failed  = sum(1 for e in entries if e.get("final_status") == "FAIL")
        errored = sum(1 for e in entries if e.get("final_status") == "ERROR")
        scores  = [e["judge_score"] for e in entries if e.get("judge_score") is not None]
        avg_sc  = round(sum(scores) / len(scores), 2) if scores else None
        pass_pct = round(passed / n * 100, 1) if n else 0.0
        lats_a  = [e["latency_ms_a"] for e in entries if e.get("latency_ms_a")]
        lats_b  = [e["latency_ms_b"] for e in entries if e.get("latency_ms_b")]
        avg_la  = round(sum(lats_a) / len(lats_a), 0) if lats_a else None
        avg_lb  = round(sum(lats_b) / len(lats_b), 0) if lats_b else None

        row_values = [label, n, passed, review, failed, errored, avg_sc, pass_pct, avg_la, avg_lb]
        bg = _fill(C_LIGHT_BG) if r_i % 2 == 0 else None

        for col_i, val in enumerate(row_values, 1):
            c = ws.cell(row=row, column=col_i, value=val)
            c.font   = _font()
            c.border = _thin_border()
            c.alignment = _align(wrap=False, h="center" if col_i > 1 else "left")
            if bg:
                c.fill = bg

        pct_cell = ws.cell(row=row, column=8)
        pct_cell.number_format = '0.0"%"'
        if pass_pct >= 80:
            pct_cell.font = Font(name="Segoe UI", color=C_PASS_TEXT, bold=True, size=10)
        elif pass_pct >= 50:
            pct_cell.font = Font(name="Segoe UI", color=C_REVIEW_TEXT, bold=True, size=10)
        else:
            pct_cell.font = Font(name="Segoe UI", color=C_FAIL_TEXT, bold=True, size=10)

        for idx, v in enumerate([n, passed, review, failed, errored]):
            totals[idx] += v

    row += 1
    footer = ["TOTAL", totals[0], totals[1], totals[2], totals[3], totals[4],
              None, None, None, None]
    for col_i, val in enumerate(footer, 1):
        c = ws.cell(row=row, column=col_i, value=val)
        c.font   = Font(name="Segoe UI", bold=True, color=C_HEADER_TEXT, size=10)
        c.fill   = _fill(C_DARK_BG)
        c.border = _thin_border()
        c.alignment = _align(wrap=False, h="center" if col_i > 1 else "left")

    col_widths = [28, 8, 8, 8, 8, 8, 10, 10, 20, 20]
    for col_i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col_i)].width = w


def _write_suite_sheet(ws, suite_label: str, entries: list[dict], tab_colour: str) -> None:
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = tab_colour.lstrip("FF")

    n_cols = len(_SUITE_COLUMNS)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    c = ws.cell(row=1, column=1, value=suite_label)
    c.font      = Font(name="Segoe UI", bold=True, color=C_HEADER_TEXT, size=13)
    c.fill      = _fill(C_DARK_BG)
    c.alignment = _align(wrap=False, h="center", v="center")
    ws.row_dimensions[1].height = 28

    for col_i, (hdr, _, width, _wrap) in enumerate(_SUITE_COLUMNS, 1):
        c = ws.cell(row=2, column=col_i, value=hdr)
        c.font      = _header_font()
        c.fill      = _fill(C_MID_BG)
        c.border    = _thin_border()
        c.alignment = _align(wrap=False, h="center", v="center")
        ws.column_dimensions[get_column_letter(col_i)].width = width
    ws.row_dimensions[2].height = 20

    ws.freeze_panes = "A3"

    for row_i, entry in enumerate(entries, 1):
        excel_row = row_i + 2
        status    = entry.get("final_status", "")
        fill      = _row_fill(status, row_i)

        for col_i, (_, extractor, _, wrap) in enumerate(_SUITE_COLUMNS, 1):
            val = extractor(entry, row_i)
            c   = ws.cell(row=excel_row, column=col_i, value=val)
            c.font      = _font() if col_i != 9 else _status_font(status)
            c.border    = _thin_border()
            c.alignment = _align(wrap=wrap, h="left" if wrap else "center")
            if fill:
                c.fill = fill

        ws.row_dimensions[excel_row].height = 80

    ws.auto_filter.ref = (
        f"A2:{get_column_letter(n_cols)}{len(entries) + 2}"
    )


def write_excel_report(entries: list[dict], out_dir: str | None = None) -> str:
    """
    Build and write a workbook from *entries* (list of log_result dicts).

    Returns the absolute path to the written file.
    """
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    Path(out_dir).mkdir(exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path  = os.path.join(out_dir, f"report_{timestamp}.xlsx")

    wb = Workbook()
    wb.remove(wb.active)

    suite_order: list[str] = []
    seen: set[str] = set()
    for e in entries:
        sk = _suite_of(e)
        if sk and sk not in seen:
            suite_order.append(sk)
            seen.add(sk)
    for sk in _KNOWN_SUITES:
        if sk not in seen:
            suite_order.append(sk)

    ws_sum = wb.create_sheet("Summary")
    _write_summary_sheet(ws_sum, entries, suite_order)

    suite_entries: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        suite_entries[_suite_of(e)].append(e)

    for idx, suite_key in enumerate(suite_order):
        label   = _SUITE_LABELS.get(suite_key, suite_key.replace("_", " ").title())
        entries_for_suite = suite_entries.get(suite_key, [])
        if not entries_for_suite:
            continue
        tab_col = SUITE_TAB_COLOURS[idx % len(SUITE_TAB_COLOURS)]
        ws = wb.create_sheet(label[:31])
        _write_suite_sheet(ws, label, entries_for_suite, tab_col)

    wb.save(out_path)
    return out_path

"""
Writes the run's results to a workbook: a summary tab, then one tab per layer.

Summary counts are written as literal values, not COUNTIF formulas. openpyxl writes a
formula with no cached value, so until something recalculates the file those cells read
as blank in Numbers, Google Sheets, pandas and most previewers — and there is no
LibreOffice on the machines this runs on to do it. The sheet is regenerated in full by
every harness run rather than edited by hand, so a live formula buys nothing here and
costs a report that looks empty. Each figure is reproducible by filtering the Status
column on the matching detail tab.
"""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FONT = "Arial"

STATUS_FILL = {
    "PASS": PatternFill("solid", fgColor="C6EFCE"),
    "FAIL": PatternFill("solid", fgColor="FFC7CE"),
    "KNOWN": PatternFill("solid", fgColor="FFEB9C"),
    "INFO": PatternFill("solid", fgColor="DDEBF7"),
}
STATUS_FONT = {
    "PASS": Font(name=FONT, size=10, bold=True, color="006100"),
    "FAIL": Font(name=FONT, size=10, bold=True, color="9C0006"),
    "KNOWN": Font(name=FONT, size=10, bold=True, color="9C6500"),
    "INFO": Font(name=FONT, size=10, bold=True, color="1F4E79"),
}

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(name=FONT, size=10, bold=True, color="FFFFFF")
BODY_FONT = Font(name=FONT, size=10)
TITLE_FONT = Font(name=FONT, size=14, bold=True)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

COLUMNS = [("Check", 46), ("Query / relation", 60), ("Expected", 20),
           ("Actual", 20), ("Status", 10), ("Notes", 74)]

# Worksheet titles are capped at 31 characters by the format.
def _tab(layer):
    return layer.replace("Layer ", "L").replace(" - ", " ")[:31]


def _write_sheet(ws, rows):
    for index, (heading, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=index, value=heading)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BORDER
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(rows) + 1}"

    for offset, row in enumerate(rows, start=2):
        note = " ".join(x for x in (row.get("note", ""), row.get("detail", "")) if x)
        values = [row["name"], row["query"], row["expected"], row["actual"],
                  row["status"], note]
        for index, value in enumerate(values, start=1):
            cell = ws.cell(row=offset, column=index, value=value)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=index in (2, 6))
            if index == 5:
                cell.font = STATUS_FONT.get(row["status"], BODY_FONT)
                cell.fill = STATUS_FILL.get(row["status"], PatternFill())
                cell.alignment = Alignment(horizontal="center", vertical="top")
            else:
                cell.font = BODY_FONT


def write_report(rows, path, env="ai4", elapsed=0.0):
    layers = []
    for row in rows:
        if row["layer"] not in layers:
            layers.append(row["layer"])

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"

    summary["A1"] = "AI Sourcing V3 — boolean search validation"
    summary["A1"].font = TITLE_FONT
    summary["A2"] = f"Environment: {env}    Run time: {elapsed:.0f}s    Checks: {len(rows)}"
    summary["A2"].font = Font(name=FONT, size=10, italic=True, color="595959")
    summary["A3"] = ("PASS = behaved as required · FAIL = open, someone must act · "
                     "KNOWN = triaged and accepted (UI-blocked or by design)")
    summary["A3"].font = Font(name=FONT, size=10, italic=True, color="595959")

    headers = ["Layer", "Checks", "Pass", "Fail", "Known/accepted"]
    for index, heading in enumerate(headers, start=1):
        cell = summary.cell(row=5, column=index, value=heading)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BORDER
    for width, letter in zip([34, 10, 10, 10, 16], "ABCDE"):
        summary.column_dimensions[letter].width = width

    totals = [0, 0, 0, 0]
    for offset, layer in enumerate(layers, start=6):
        layer_rows = [r for r in rows if r["layer"] == layer]
        counts = [len(layer_rows)] + [sum(1 for r in layer_rows if r["status"] == s)
                                      for s in ("PASS", "FAIL", "KNOWN")]
        totals = [t + c for t, c in zip(totals, counts)]
        summary.cell(row=offset, column=1, value=layer).font = BODY_FONT
        for column, value in enumerate(counts, start=2):
            summary.cell(row=offset, column=column, value=value).font = BODY_FONT
        for column in range(1, 6):
            summary.cell(row=offset, column=column).border = BORDER

    total_row = 6 + len(layers)
    summary.cell(row=total_row, column=1, value="TOTAL").font = Font(name=FONT, size=10, bold=True)
    for column, value in enumerate(totals, start=2):
        summary.cell(row=total_row, column=column, value=value).font = Font(
            name=FONT, size=10, bold=True)
    for column in range(1, 6):
        summary.cell(row=total_row, column=column).border = BORDER

    note_row = total_row + 2
    summary.cell(row=note_row, column=1,
                 value="Open items and who holds them").font = Font(name=FONT, size=11, bold=True)
    open_rows = [r for r in rows if r["status"] == "FAIL"]
    for offset, row in enumerate(open_rows, start=note_row + 1):
        summary.cell(row=offset, column=1, value=row["name"]).font = BODY_FONT
        cell = summary.cell(row=offset, column=2, value=row["note"] or row["detail"])
        cell.font = BODY_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    if not open_rows:
        summary.cell(row=note_row + 1, column=1,
                     value="None — every check passed or is triaged.").font = BODY_FONT

    for layer in layers:
        worksheet = workbook.create_sheet(_tab(layer))
        _write_sheet(worksheet, [r for r in rows if r["layer"] == layer])

    workbook.save(path)
    return path

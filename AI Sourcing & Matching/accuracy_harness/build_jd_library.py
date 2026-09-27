"""
Assemble every Job Description used for Global-database / AI Sourcing testing into one
folder of PDFs — the six already-designed ones, plus a PDF rendered for each JD that
until now only existed as JSON.

    ./.venv/bin/python build_jd_library.py            # -> output/jd_library/

Layout:
  01_designed_jds/        the 6 multi-page designed JDs (copied) + All_JDs.pdf
  02_config_jds/          golden/jds.json                     (Day-1 config accuracy)
  03_config_jds_industry/ golden/jds_industries.json          (Day-1, 50 industries)
  04_scale_jd_excerpt/    scale_cases.json format=jd_excerpt  (just under the 1000-char cap)
  05_scale_jd_overlimit/  scale_cases.json format=jd_overlimit(deliberately over the cap)
  _combined/              one combined PDF per set, for reading rather than attaching
  INDEX.pdf / INDEX.md    what each set is and when it was used

Individual files are what you attach in the UI; the combined ones are for review.
"""
import json
import os
import re
import shutil

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

NAVY = colors.HexColor("#1F3A5F")
ACCENT = colors.HexColor("#2E6E9E")
GREY = colors.HexColor("#5B6670")
LIGHT = colors.HexColor("#EEF3F8")

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "output", "jd_library")

SETS = [
    ("02_config_jds", "golden/jds.json", "json",
     "Day-1 config accuracy — the original 4 JDs",
     "Jul 22-23 · drove questions + criteria + filters generation on the Global database (db=2). "
     "Includes the deliberately vague one used to test clarifying-question behaviour."),
    ("03_config_jds_industry", "golden/jds_industries.json", "json",
     "Day-1 config accuracy — 50 industries",
     "Jul 22-23 · the same config chain across 50 industry verticals, to catch domain-specific "
     "extraction failures."),
    ("04_scale_jd_excerpt", "golden/sourcing/scale_cases.json", "jd_excerpt",
     "Scale run — JD excerpts (just under the 1000-char cap)",
     "Jul 23 · part of the 300-variation prod run. Sized to pass validation, so these exercise "
     "retrieval and scoring on realistic JD text."),
    ("05_scale_jd_overlimit", "golden/sourcing/scale_cases.json", "jd_overlimit",
     "Scale run — over-limit JDs (3.7k-10k chars)",
     "Jul 23 · deliberately longer than the 1000-char aiSearch cap. These are the evidence for "
     "the finding that a real multi-page JD cannot be pasted (HTTP 400 Validation error)."),
]


def styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontSize=19, textColor=colors.white,
                                leading=23, spaceAfter=2, alignment=TA_LEFT),
        "sub": ParagraphStyle("s", parent=ss["Normal"], fontSize=10, textColor=colors.white,
                              leading=13),
        "h": ParagraphStyle("h", parent=ss["Heading2"], fontSize=12.5, textColor=NAVY,
                            spaceBefore=13, spaceAfter=5, leading=15),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontSize=10,
                               textColor=colors.HexColor("#232B33"), leading=15, spaceAfter=7),
        "small": ParagraphStyle("sm", parent=ss["Normal"], fontSize=8.5, textColor=GREY, leading=12),
        "mono": ParagraphStyle("m", parent=ss["Normal"], fontSize=8.5, fontName="Courier",
                               textColor=GREY, leading=12),
    }


def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def banner(S, title, subtitle):
    tbl = Table([[Paragraph(esc(title), S["title"])],
                 [Paragraph(esc(subtitle), S["sub"])]], colWidths=[170 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (0, 0), 12), ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
    ]))
    return tbl


def facts(S, pairs):
    rows = [[Paragraph(f"<b>{esc(k)}</b>", S["small"]), Paragraph(esc(v), S["small"])]
            for k, v in pairs if v not in (None, "", [])]
    if not rows:
        return None
    t = Table(rows, colWidths=[34 * mm, 136 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
    ]))
    return t


def jd_flow(S, item, set_title):
    """One JD as a list of flowables — used both standalone and in the combined file."""
    text = item.get("input") or item.get("query") or ""
    role = item.get("role") or item.get("id", "").replace("-", " ").title()
    flow = [banner(S, role if item.get("role") else item.get("id", "Job Description"),
                   set_title), Spacer(1, 8)]
    f = facts(S, [
        ("Case id", item.get("id")),
        ("Seniority", item.get("seniority")),
        ("Location", item.get("location")),
        ("Industry", item.get("family")),
        ("Format", item.get("format")),
        ("Database", "Global (db=2)" if (item.get("database_type") == 2 or item.get("format")) else None),
        ("Length", f"{len(text):,} characters"),
    ])
    if f:
        flow += [f, Spacer(1, 10)]
    flow.append(Paragraph("Job Description", S["h"]))
    flow.append(HRFlowable(width="100%", color=ACCENT, thickness=1, spaceAfter=8))
    for para in re.split(r"\n\s*\n|(?<=[.!?])\s{2,}", text.strip()):
        para = para.strip()
        if para:
            flow.append(Paragraph(esc(para), S["body"]))
    if item.get("expectation"):
        flow += [Spacer(1, 6), Paragraph("What this case checks", S["h"]),
                 HRFlowable(width="100%", color=ACCENT, thickness=1, spaceAfter=8),
                 Paragraph(esc(item["expectation"]), S["body"])]
    if item.get("note"):
        flow.append(Paragraph(f"Note: {esc(item['note'])}", S["small"]))
    return flow


def doc(path, flow):
    SimpleDocTemplate(path, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=18 * mm, bottomMargin=18 * mm,
                      title=os.path.basename(path)).build(flow)


def load(src, kind):
    d = json.load(open(os.path.join(ROOT, src)))
    items = d["cases"] if isinstance(d, dict) and "cases" in d else d
    if kind in ("jd_excerpt", "jd_overlimit"):
        items = [x for x in items if x.get("format") == kind]
    return items


def main():
    S = styles()
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "_combined"))
    index = []

    # 1. the already-designed PDFs
    d1 = os.path.join(OUT, "01_designed_jds")
    os.makedirs(d1)
    src = os.path.join(ROOT, "output", "jd_pdfs")
    n1 = 0
    for f in sorted(os.listdir(src)):
        if f.endswith(".pdf"):
            shutil.copy2(os.path.join(src, f), os.path.join(d1, f))
            n1 += 1 if f != "All_JDs.pdf" else 0
    index.append(("01_designed_jds", "Designed multi-page JDs", n1,
                  "Jul 28 · fully formatted JDs for the six longest-form roles from the top-20 "
                  "proven prod queries, each grounded in its scale archetype. Built for the "
                  "attach-a-JD-file flow. All_JDs.pdf is the six combined."))

    # 2-5. render the JSON-only sets
    for folder, src_path, kind, title, when in SETS:
        items = load(src_path, kind)
        dd = os.path.join(OUT, folder)
        os.makedirs(dd)
        combined = []
        for i, it in enumerate(items):
            name = re.sub(r"[^A-Za-z0-9._-]+", "-", it.get("id", f"jd-{i+1}"))[:70]
            doc(os.path.join(dd, f"{name}.pdf"), jd_flow(S, it, title))
            combined += jd_flow(S, it, title)
            if i < len(items) - 1:
                combined.append(PageBreak())
        doc(os.path.join(OUT, "_combined", f"{folder}_ALL.pdf"), combined)
        index.append((folder, title, len(items), when + f"  ·  source: {src_path}"))
        print(f"{folder:26} {len(items):>4} PDFs")

    # index, as markdown and as a cover PDF
    md = ["# JD library — Global database / AI Sourcing\n",
          "Every job description used in AI Sourcing / Global-database testing, as PDF. "
          "Individual files are what you attach in the UI; `_combined/` holds one file per set "
          "for reading.\n",
          "| Folder | Set | JDs | When it was used |", "|---|---|---|---|"]
    flow = [banner(S, "JD library", "Global database / AI Sourcing — every JD used in testing"),
            Spacer(1, 12),
            Paragraph("Individual PDFs are for attaching in the UI. <b>_combined/</b> holds one "
                      "file per set for reading.", S["body"]), Spacer(1, 6)]
    rows = [[Paragraph("<b>Folder</b>", S["small"]), Paragraph("<b>Set</b>", S["small"]),
             Paragraph("<b>JDs</b>", S["small"]), Paragraph("<b>When used</b>", S["small"])]]
    for folder, title, n, when in index:
        md.append(f"| `{folder}` | {title} | {n} | {when} |")
        rows.append([Paragraph(f"<font face='Courier'>{esc(folder)}</font>", S["small"]),
                     Paragraph(esc(title), S["small"]), Paragraph(str(n), S["small"]),
                     Paragraph(esc(when), S["small"])])
    t = Table(rows, colWidths=[38 * mm, 44 * mm, 12 * mm, 76 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE6")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    flow.append(t)
    total = sum(n for _, _, n, _ in index)
    flow += [Spacer(1, 10), Paragraph(f"Total: {total} job descriptions.", S["body"])]
    doc(os.path.join(OUT, "INDEX.pdf"), flow)
    md.append(f"\n**Total: {total} job descriptions.**\n")
    open(os.path.join(OUT, "INDEX.md"), "w").write("\n".join(md))

    n_files = sum(len(fs) for _, _, fs in os.walk(OUT))
    print(f"\n{OUT}\n{total} JDs · {n_files} files")


if __name__ == "__main__":
    main()

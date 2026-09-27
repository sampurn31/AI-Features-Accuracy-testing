"""
Generate polished, multi-page Job Description PDFs for the 'bigger' (JD-length) roles
from the top-20 proven-good AI Sourcing prod queries. Each JD is grounded in its
scale_archetypes profile (title, skills, responsibilities, requirements, nice-to-haves,
weighted matching criteria) and the exact location/seniority/company the run used.

  ./.venv/bin/python make_jd_pdfs.py
Output: output/jd_pdfs/<role>_JD.pdf  (+ combined output/jd_pdfs/All_JDs.pdf)
"""
import json
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (ListFlowable, ListItem, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle, HRFlowable, PageBreak)

import scale_archetypes as SA

NAVY = colors.HexColor("#1F3A5F")
ACCENT = colors.HexColor("#2E6E9E")
GREY = colors.HexColor("#5B6670")
LIGHT = colors.HexColor("#EEF3F8")

ROLES = ["digital-marketing", "product-marketing", "retail-district",
         "civil-engineer", "medical-coder", "fullstack-node"]

# short human intro per industry family, used for "About the company"
IND_BLURB = {
    "D2C brands": "a fast-growing direct-to-consumer brand",
    "marketplaces": "a high-scale online marketplace",
    "SaaS": "a product-led SaaS company",
    "cybersecurity": "an enterprise cybersecurity company",
    "fintech": "a regulated fintech company",
    "apparel": "a multi-store apparel retailer",
    "convenience": "a regional convenience-retail chain",
    "specialty retail": "a specialty retail group",
    "civil consultancies": "a civil engineering consultancy",
    "developers": "a real-estate development firm",
    "municipal": "a municipal infrastructure body",
    "health systems": "a multi-site health system",
    "billing companies": "a medical billing services company",
    "payers": "a health-insurance payer",
    "startups": "an early-stage technology startup",
    "proptech": "a proptech scale-up",
    "healthtech": "a healthtech company",
}

OFFER = [
    "Competitive salary benchmarked to market, plus performance bonus",
    "Health, dental and vision coverage for you and your dependents",
    "Flexible / hybrid working and a generous paid-time-off policy",
    "Annual learning budget and clear progression framework",
    "Modern tooling and a collaborative, low-ego team",
]


def _meta():
    """Pull location/seniority/company/criteria for the jd_excerpt variation of each role."""
    want = set(ROLES)
    best = {}
    path = os.path.join("output", "scale_checkpoint_20260723-full.jsonl")
    for line in open(path):
        r = json.loads(line)
        if r.get("format") != "jd_excerpt":
            continue
        role = r["id"].rsplit("-", 2)[0]
        if role not in want:
            continue
        tot = r.get("totalResults") or 0
        if role not in best or tot > best[role]["_tot"]:
            best[role] = {"id": r["id"], "loc": r.get("location"), "snr": r.get("seniority"),
                          "company": r.get("company"), "n": r.get("n_candidates"),
                          "crit": r.get("criteria") or [], "_tot": tot}
    return best


def _styles():
    ss = getSampleStyleSheet()
    S = {}
    S["title"] = ParagraphStyle("jt", parent=ss["Title"], fontSize=22, textColor=colors.white,
                                leading=26, spaceAfter=2, alignment=TA_LEFT)
    S["sub"] = ParagraphStyle("js", parent=ss["Normal"], fontSize=10.5, textColor=colors.white,
                              leading=14)
    S["h"] = ParagraphStyle("jh", parent=ss["Heading2"], fontSize=13, textColor=NAVY,
                            spaceBefore=14, spaceAfter=6, leading=15)
    S["body"] = ParagraphStyle("jb", parent=ss["Normal"], fontSize=10, textColor=colors.HexColor("#232B33"),
                               leading=15, spaceAfter=4)
    S["bul"] = ParagraphStyle("jbu", parent=S["body"], leftIndent=2, spaceAfter=3)
    S["small"] = ParagraphStyle("jsm", parent=ss["Normal"], fontSize=8.5, textColor=GREY, leading=12)
    S["chip"] = ParagraphStyle("jc", parent=ss["Normal"], fontSize=9, textColor=NAVY, leading=13)
    return S


def _bullets(items, S):
    return ListFlowable(
        [ListItem(Paragraph(x, S["bul"]), leftIndent=12, value="•") for x in items],
        bulletType="bullet", start="•", leftIndent=10, bulletFontSize=9, bulletColor=ACCENT)


def _titlebar(role_title, company_line, sub_line, S):
    inner = [[Paragraph(role_title, S["title"])],
             [Paragraph(company_line, S["sub"])],
             [Paragraph(sub_line, S["sub"])]]
    t = Table(inner, colWidths=[170 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (0, 0), 12), ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
    ]))
    return t


def _criteria_table(crit, S):
    hdr = ParagraphStyle("jch", parent=S["small"], textColor=colors.white, fontSize=9)
    rows = [[Paragraph("<b>Criterion</b>", hdr), Paragraph("<b>Weight</b>", hdr)]]
    for c in crit:
        rows.append([Paragraph(c["text"], S["body"]),
                     Paragraph(f"{c['weight']} / 10", S["body"])])
    t = Table(rows, colWidths=[140 * mm, 30 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7D3DE")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def build_role(role, arch, meta, S):
    loc = meta.get("loc") or "Remote"
    snr = (meta.get("snr") or "").capitalize()
    ind = (arch["inds"] or ["SaaS"])[0]
    blurb = IND_BLURB.get(ind, f"a growing {ind} organization")
    title = f"{snr + ' ' if snr else ''}{arch['title']}".strip()
    company_line = f"Confidential client — {blurb.capitalize()}"
    sub_line = f"{loc}   ·   Full-time   ·   {ind.title()}"

    story = []
    story.append(_titlebar(title, company_line, sub_line, S))
    story.append(Spacer(1, 10))

    story.append(Paragraph("About the role", S["h"]))
    story.append(Paragraph(
        f"We are {blurb} hiring a <b>{arch['title']}</b> to be based in {loc}. This is a "
        f"{('senior, high-ownership' if snr.lower() in ('senior', 'staff', 'lead') else 'high-impact')} "
        f"position where you will {arch['resp'][0][0].lower() + arch['resp'][0][1:]} and work "
        f"closely with cross-functional teams to deliver measurable outcomes.", S["body"]))

    story.append(Paragraph("Key responsibilities", S["h"]))
    story.append(_bullets(arch["resp"], S))

    story.append(Paragraph("What you'll bring (requirements)", S["h"]))
    story.append(_bullets(arch["reqs"], S))

    story.append(Paragraph("Preferred qualifications", S["h"]))
    story.append(_bullets(arch["nice"], S))

    story.append(Paragraph("Core skills &amp; tools", S["h"]))
    story.append(Paragraph("  ·  ".join(arch["skills"]), S["chip"]))

    story.append(Paragraph("How we evaluate candidates", S["h"]))
    story.append(Paragraph(
        "Applications are assessed against the weighted criteria below (this JD's search profile):",
        S["body"]))
    story.append(Spacer(1, 4))
    story.append(_criteria_table(meta.get("crit", []), S))

    story.append(Paragraph("What we offer", S["h"]))
    story.append(_bullets(OFFER, S))

    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#C7D3DE")))
    story.append(Paragraph(
        f"Location: {loc}  ·  Employment type: Full-time  ·  Industry: {ind.title()}  ·  "
        f"Ref: {meta.get('id')}", S["small"]))
    story.append(Paragraph(
        "Generated for AI Sourcing test data (PW-6818). Company withheld; profile mirrors a proven "
        "prod search query that returned a full 100-candidate pool.", S["small"]))
    return story


def main():
    outdir = os.path.join("output", "jd_pdfs")
    os.makedirs(outdir, exist_ok=True)
    S = _styles()
    archmap = {a["key"]: a for a in SA.ARCHETYPES}
    meta = _meta()
    combined = []
    made = []
    for i, role in enumerate(ROLES):
        arch = archmap[role]
        m = meta.get(role, {})
        path = os.path.join(outdir, f"{role}_JD.pdf")
        SimpleDocTemplate(path, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm,
                          leftMargin=20 * mm, rightMargin=20 * mm,
                          title=f"{arch['title']} — Job Description").build(build_role(role, arch, m, S))
        made.append((role, arch["title"], m.get("loc"), path))
        combined.extend(build_role(role, arch, m, S))   # fresh flowables (reportlab mutates them)
        if i != len(ROLES) - 1:
            combined.append(PageBreak())
    allpath = os.path.join(outdir, "All_JDs.pdf")
    SimpleDocTemplate(allpath, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm,
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      title="AI Sourcing — Job Descriptions (6 roles)").build(combined)
    for role, title, loc, path in made:
        print(f"  {role:<18} {title:<34} {loc or '':<18} -> {path}")
    print(f"\ncombined -> {allpath}")


if __name__ == "__main__":
    main()

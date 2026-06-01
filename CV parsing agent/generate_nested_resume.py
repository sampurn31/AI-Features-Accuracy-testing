#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate one additional nested custom field test resume + add to ground_truth.json
"""

import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY   = colors.HexColor("#1e3a5f")
TEAL   = colors.HexColor("#2b6cb0")
GREY   = colors.HexColor("#4a5568")
LIGHT  = colors.HexColor("#edf2f7")
RULE   = colors.HexColor("#cbd5e0")
CONTENT_W = 174 * mm

def _make_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle(
            "Name", parent=base["Title"],
            fontName="Helvetica-Bold", fontSize=22, leading=26,
            textColor=NAVY, spaceAfter=2 * mm, alignment=TA_LEFT,
        ),
        "contact": ParagraphStyle(
            "Contact", parent=base["Normal"],
            fontName="Helvetica", fontSize=9, leading=12,
            textColor=GREY, spaceAfter=0, alignment=TA_LEFT,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontName="Helvetica", fontSize=9.5, leading=14,
            textColor=colors.black, spaceAfter=2 * mm,
        ),
        "job_title": ParagraphStyle(
            "JobTitle", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=10, leading=13,
            textColor=NAVY, spaceAfter=0.5 * mm,
        ),
        "job_meta": ParagraphStyle(
            "JobMeta", parent=base["Normal"],
            fontName="Helvetica-Oblique", fontSize=9, leading=12,
            textColor=TEAL, spaceAfter=1.5 * mm,
        ),
        "bullet": ParagraphStyle(
            "Bullet", parent=base["Normal"],
            fontName="Helvetica", fontSize=9.5, leading=13,
            textColor=colors.black, spaceAfter=1 * mm,
            leftIndent=10 * mm, bulletIndent=4 * mm,
        ),
        "skills": ParagraphStyle(
            "Skills", parent=base["Normal"],
            fontName="Helvetica", fontSize=9.5, leading=14,
            textColor=colors.black, spaceAfter=0,
            backColor=LIGHT, borderPadding=6,
        ),
    }

S = _make_styles()

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(_esc(text), S[style])

def _accent_rule() -> list:
    return [
        HRFlowable(width="100%", thickness=2.5, color=TEAL,
                   spaceBefore=1 * mm, spaceAfter=4 * mm),
    ]

def _header_block(name: str, contact: str) -> list:
    return [
        _p(name, "name"),
        _p(contact, "contact"),
        *_accent_rule(),
    ]

def _section_heading(title: str) -> list:
    cell = Paragraph(
        f'<font color="white"><b>{_esc(title).upper()}</b></font>',
        ParagraphStyle(
            "SecHead", fontName="Helvetica-Bold", fontSize=10,
            leading=12, textColor=colors.white, alignment=TA_LEFT,
        ),
    )
    tbl = Table([[cell]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return [Spacer(1, 5 * mm), tbl, Spacer(1, 2.5 * mm)]

def _job(role: str, company: str, dates: str, location: str) -> list:
    return [
        _p(role, "job_title"),
        _p(f"{company}  ·  {dates}  ·  {location}", "job_meta"),
    ]

def _bul(text: str) -> Paragraph:
    return Paragraph(f"• {_esc(text)}", S["bullet"])

def _skills_block(text: str) -> list:
    tbl = Table([[_p(text, "skills")]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return [tbl]

def _build_pdf(path: Path, story: list) -> None:
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
    )
    doc.build(story)

def _story_alex_kumar() -> list:
    return [
        *_header_block(
            "Alex Kumar",
            "alex.kumar@gmail.com  |  +1 408 555 0299  |  San Jose, CA, USA",
        ),
        *_section_heading("Work Preferences"),
        _p(
            "I am open to traveling regularly for client work or team meetings, typically "
            "within a 200 mile radius of my base location. I prefer Remote work arrangements "
            "and have set up a Dedicated room in my home for focused engineering work."
        ),
        _p(
            "My technical expertise spans both Backend and Data engineering. "
            "For backend development, I primarily work with Django and FastAPI frameworks. "
            "On the data side, I am proficient with Apache Spark and Airflow for pipeline orchestration."
        ),
        _p(
            "I am available to start in 4 weeks and typically work on a contract basis "
            "with a preferred rate of $150/hour for independent consulting projects."
        ),
        *_section_heading("Professional Summary"),
        _p(
            "Full-stack engineer with 9 years of experience building scalable data platforms "
            "and backend services. Expertise in Python, distributed systems, and cloud infrastructure."
        ),
        *_section_heading("Work Experience"),
        *_job("Senior Backend Engineer", "Databricks", "2021 – Present", "San Jose, CA"),
        _bul("Architected real-time data ingestion pipeline processing 50TB+ daily"),
        _bul("Led migration from monolith to microservices, improving deploy frequency 10x"),
        Spacer(1, 2 * mm),
        *_job("Backend Engineer", "Uber", "2017 – 2021", "San Francisco, CA"),
        _bul("Built fraud detection ML pipeline reducing false positives by 35%"),
        _bul("Optimized core API endpoints, cutting P95 latency from 800ms to 120ms"),
        Spacer(1, 2 * mm),
        *_job("Software Engineer", "LinkedIn", "2015 – 2017", "Mountain View, CA"),
        _bul("Developed recommendation engine serving 500M+ members"),
        *_section_heading("Education"),
        _p("M.S. Computer Science  |  Stanford University  |  2015"),
        _p("B.Tech Computer Science  |  IIT Bombay  |  2013"),
        *_section_heading("Skills"),
        *_skills_block(
            "Python, Django, FastAPI, Apache Spark, Airflow, Kafka, PostgreSQL, Redis, AWS, Kubernetes"
        ),
    ]

NEW_TEST_CASE = {
    "tc_id": "TC006_NESTED",
    "resume_file": "006_alex_kumar_nested.pdf",
    "candidate_name": "Alex Kumar",
    "standard_fields": {
        "firstname": "Alex",
        "lastname": "Kumar",
        "emailid": "alex.kumar@gmail.com",
        "contactnumber": "+1 408 555 0299",
        "city": "San Jose",
        "state": "California",
        "country": "United States",
        "position": "Senior Backend Engineer",
        "lastorganisation": "Databricks",
        "workexpyr": 9,
        "skill": "Python, Django, FastAPI, Apache Spark, Airflow, Kafka, PostgreSQL",
        "qualification": "M.S. Computer Science",
    },
    "nested_custom_fields": {
        "custcolumn1": "1",
        "custcolumn2": "Remote",
        "custcolumn3": ["Backend", "Data"],
        "custcolumn4": "200 mile radius",
        "custcolumn5": "Dedicated room",
        "custcolumn6": None,
        "custcolumn7": ["Django", "FastAPI"],
        "custcolumn8": "Apache Spark and Airflow",
        "custcolumn9": "4",
        "custcolumn10": "$150/hour",
    },
    "validation_notes": {
        "col4": "Visible because col1=1 (willing to travel)",
        "col5": "Visible and filtered to {Dedicated room, Shared space} because col2=Remote",
        "col6": "Hidden because col2=Remote (only visible when Onsite)",
        "col7": "Visible and union of Backend+Data filters: all 4 options valid (Django, Spring Boot, FastAPI, Express)",
        "col8": "Visible because Data is selected in col3",
    },
}

def main():
    test_data_dir = Path("test_data")
    resumes_dir = test_data_dir / "resumes"
    gt_file = test_data_dir / "ground_truth.json"
    
    pdf_path = resumes_dir / "006_alex_kumar_nested.pdf"
    _build_pdf(pdf_path, _story_alex_kumar())
    print(f"✓ Generated: {pdf_path}")
    
    if gt_file.exists():
        gt_data = json.loads(gt_file.read_text(encoding="utf-8"))
    else:
        print(f"Warning: {gt_file} not found. Creating new structure.")
        gt_data = {
            "schema_version": "1.0",
            "generated_on": "2026-05-20",
            "test_cases": []
        }
    
    if "nested_test_cases" not in gt_data:
        gt_data["nested_test_cases"] = []
    
    existing_ids = {tc.get("tc_id") for tc in gt_data.get("nested_test_cases", [])}
    if NEW_TEST_CASE["tc_id"] not in existing_ids:
        gt_data["nested_test_cases"].append(NEW_TEST_CASE)
        gt_file.write_text(json.dumps(gt_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Added {NEW_TEST_CASE['tc_id']} to {gt_file}")
    else:
        print(f"Note: {NEW_TEST_CASE['tc_id']} already exists in ground_truth.json")
    
    print("\nDone. Resume generated with nested custom fields:")
    print("  - Willing to Travel: Yes → Travel Radius visible")
    print("  - Work Mode: Remote → Office Setup visible (filtered), Onsite Location hidden")
    print("  - Technical Focus: Backend+Data → Backend Stack visible, Data Tool visible")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate test resume PDFs + ground truth JSON for API automation testing.

Strategy: each resume embeds custom-field values in short natural sentences
(not label:value lines).  Exact strings from ground_truth.json are woven
into prose so the parser can extract them without hallucination.

Run:
    python3 generate_test_data.py

Output:
    test_data/resumes/001_sarah_johnson.pdf   … 005_priya_sharma.pdf
    test_data/ground_truth.json
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

OUTPUT_DIR = Path("test_data")
RESUME_DIR = OUTPUT_DIR / "resumes"
OUTPUT_DIR.mkdir(exist_ok=True)
RESUME_DIR.mkdir(exist_ok=True)


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
    """Top banner: name + contact + accent rule."""
    return [
        _p(name, "name"),
        _p(contact, "contact"),
        *_accent_rule(),
    ]


def _section_heading(title: str) -> list:
    """Full-width colored section bar (common on modern resumes)."""
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
    """Role line (bold) + company / dates / location (accent)."""
    return [
        _p(role, "job_title"),
        _p(f"{company}  ·  {dates}  ·  {location}", "job_meta"),
    ]


def _bul(text: str) -> Paragraph:
    return Paragraph(f"• {_esc(text)}", S["bullet"])


def _skills_block(text: str) -> list:
    """Skills in a light shaded box."""
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


def _contact_when(window: str) -> str:
    """Turn custcolumn1 text into a natural time phrase (value unchanged)."""
    w = window.strip()
    low = w.lower()
    if low.startswith(("during ", "on ", "in ", "between ", "around ")):
        return w
    if any(k in low for k in ("morning", "afternoon", "evening", "am", "pm")):
        return f"during {w}"
    return f"around {w}"


def _arrangements_prose(options: list) -> str:
    """Describe multiselect options in prose; option labels appear verbatim."""
    if len(options) == 1:
        o = options[0]
        if o == "Remote":
            return "I am set up for fully Remote work and collaborate well with distributed teams"
        if o == "Hybrid":
            return "I do my best work in a Hybrid rhythm with regular in-person touchpoints"
        if o == "On-site":
            return "I am looking for On-site roles with daily team presence"
        if o == "Weekly travel":
            return "I can take on roles that involve Weekly travel to client or regional sites"
        if o == "Client-site":
            return "I am comfortable with Client-site engagements when the project requires it"
        return f"I am open to {o} arrangements"
    if len(options) == 2:
        return (
            f"I am flexible across {options[0]} and {options[1]} setups "
            f"depending on team needs"
        )
    joined = ", ".join(options[:-1]) + f", and {options[-1]}"
    return f"I can adapt to {joined} arrangements as the role requires"


def _relocation_prose(open_to_reloc: bool) -> str:
    if open_to_reloc:
        return (
            "I would consider relocating for the right long-term opportunity "
            "if the role and team are a strong fit"
        )
    return (
        "I plan to remain in my current location and am not seeking "
        "a relocation package at this time"
    )


def _employment_prose(emp_type: str) -> str:
    if emp_type == "Contract":
        return "My search is focused on Contract engagements with clear scope and duration"
    if emp_type == "Part-time":
        return "I am exploring Part-time opportunities that align with my current commitments"
    if emp_type == "Internship":
        return "I am interested in Internship roles where I can learn while contributing"
    if emp_type == "Freelance":
        return "I am open to Freelance project work alongside the right longer-term fit"
    return "I am pursuing Full-time roles where I can commit fully to one team"


def _natural_prefs_section(cf: dict) -> list:
    """
    Availability & Preferences as real resume prose.
    Every value is taken from cf (ground truth); field names never appear in text.
    """
    contact   = cf["custcolumn1"]
    objective = cf["custcolumn2"]
    cert_exp  = cf["custcolumn3"]
    relocate  = cf["custcolumn4"] == "1"
    emp_type  = cf["custcolumn5"]
    arrange   = cf["custcolumn6"]
    alt_phone = cf["custcolumn7"]
    alt_email = cf["custcolumn8"]
    portfolio = cf["custcolumn9"]
    salary    = cf["custcolumn10"]

    when      = _contact_when(contact)
    work_mode = _arrangements_prose(arrange)
    reloc     = _relocation_prose(relocate)
    employment = _employment_prose(emp_type)

    story = _section_heading("Availability & Preferences")
    story.append(_p(
        f"{employment}. {work_mode}. {reloc}."
    ))
    story.append(_p(
        f"When scheduling recruiter or hiring-manager conversations, "
        f"{when} tends to work best on my calendar."
    ))
    story.append(_p(objective))
    story.append(_p(
        f"My primary professional credential stays valid through {cert_exp}, "
        f"and I am targeting total annual compensation around {salary} "
        f"for the next step in my career."
    ))
    story.append(_p(
        f"If my main line is unavailable, you can also reach me at {alt_phone} "
        f"or {alt_email}. A wider collection of work and references lives at {portfolio}."
    ))
    return story


def _story_sarah_johnson(cf: dict) -> list:
    cert = cf["custcolumn3"]
    return [
        *_header_block(
            "Sarah Johnson",
            "sarah.johnson@gmail.com  |  +1 415 555 0198  |  San Francisco, CA, USA",
        ),
        *_natural_prefs_section(cf),
        *_section_heading("Professional Summary"),
        _p("Backend software engineer with 6 years of experience building distributed "
           "payment systems at scale. Deep expertise in Python, Go, and AWS cloud infrastructure."),
        *_section_heading("Work Experience"),
        *_job("Senior Software Engineer", "Stripe", "2020 – Present", "San Francisco, CA"),
        _bul("Led Kubernetes migration of core payment processing services, cutting deploy time by 60%"),
        _bul("Built real-time fraud detection pipeline handling 10,000 TPS at sub-10 ms P99 latency"),
        Spacer(1, 2 * mm),
        *_job("Software Engineer", "Airbnb", "2018 – 2020", "San Francisco, CA"),
        _bul("Developed booking and payments APIs serving 50 million active users"),
        _bul("Reduced API latency by 40% through Redis caching layer implementation"),
        *_section_heading("Education"),
        _p("B.S. Computer Science  |  Stanford University  |  2018"),
        *_section_heading("Certifications"),
        _p(f"AWS Certified Solutions Architect — active through {cert}"),
        *_section_heading("Skills"),
        *_skills_block("Python, Go, AWS, PostgreSQL, Kubernetes, Redis, gRPC"),
    ]


def _story_james_patel(cf: dict) -> list:
    cert = cf["custcolumn3"]
    return [
        *_header_block(
            "James Patel",
            "james.patel@gmail.com  |  +1 212 555 0198  |  New York, NY, USA",
        ),
        *_natural_prefs_section(cf),
        *_section_heading("Professional Summary"),
        _p("Senior Data Scientist with 5 years of experience in financial data modeling, "
           "NLP, and large-scale ML systems. Proven track record shipping models to production at Bloomberg."),
        *_section_heading("Work Experience"),
        *_job("Senior Data Scientist", "Bloomberg LP", "2021 – Present", "New York, NY"),
        _bul("Built NLP pipeline classifying 500K daily news articles with 94% accuracy"),
        _bul("Designed A/B testing framework cutting experiment cycle from 2 weeks to 3 days"),
        Spacer(1, 2 * mm),
        *_job("Data Scientist", "JPMorgan Chase", "2019 – 2021", "New York, NY"),
        _bul("Developed credit risk scoring model that reduced default rate by 18%"),
        _bul("Automated daily reporting pipeline saving 40 analyst-hours per week"),
        *_section_heading("Education"),
        _p("M.S. Statistics  |  Columbia University  |  2019"),
        _p("B.S. Mathematics  |  University of Michigan  |  2017"),
        *_section_heading("Certifications"),
        _p(f"Google Professional Data Engineer — active through {cert}"),
        *_section_heading("Skills"),
        *_skills_block("Python, R, TensorFlow, Spark, SQL, Airflow, Kafka, dbt"),
    ]


def _story_emily_chen(cf: dict) -> list:
    cert = cf["custcolumn3"]
    return [
        *_header_block(
            "Emily Chen",
            "emily.chen@gmail.com  |  +1 650 555 0198  |  Austin, TX, USA",
        ),
        *_natural_prefs_section(cf),
        *_section_heading("Professional Summary"),
        _p("UX Designer with 7 years of experience crafting end-to-end product experiences "
           "for enterprise software and consumer apps. Strong background in design systems and usability research."),
        *_section_heading("Work Experience"),
        *_job("Senior UX Designer", "Dell Technologies", "2020 – Present", "Austin, TX"),
        _bul("Led redesign of enterprise dashboard used by 20,000 IT administrators globally"),
        _bul("Ran 60+ usability sessions; reduced task completion time by 35%"),
        Spacer(1, 2 * mm),
        *_job("UX Designer", "IBM", "2017 – 2020", "Austin, TX"),
        _bul("Designed 4 core product modules for IBM Cloud Console"),
        _bul("Established component library adopted by 12 product teams"),
        *_section_heading("Education"),
        _p("B.F.A. Interaction Design  |  Pratt Institute  |  2017"),
        *_section_heading("Certifications"),
        _p(f"Nielsen Norman Group UX Certification — active through {cert}"),
        *_section_heading("Skills"),
        *_skills_block("Figma, Sketch, UserTesting, Axure, HTML, CSS, Framer"),
    ]


def _story_michael_torres(cf: dict) -> list:
    cert = cf["custcolumn3"]
    return [
        *_header_block(
            "Michael Torres",
            "m.torres@gmail.com  |  +1 206 555 0198  |  Seattle, WA, USA",
        ),
        *_natural_prefs_section(cf),
        *_section_heading("Professional Summary"),
        _p("DevOps Engineer with 8 years of experience managing large-scale AWS infrastructure. "
           "Specialist in Terraform, Kubernetes, and CI/CD pipeline automation."),
        *_section_heading("Work Experience"),
        *_job("Senior DevOps Engineer", "Amazon Web Services", "2020 – Present", "Seattle, WA"),
        _bul("Managed multi-region Kubernetes clusters serving 1M+ daily requests"),
        _bul("Reduced infrastructure costs by 30% through right-sizing and spot instance migration"),
        Spacer(1, 2 * mm),
        *_job("DevOps Engineer", "Microsoft", "2016 – 2020", "Seattle, WA"),
        _bul("Built CI/CD pipelines for 200+ microservices using Azure DevOps"),
        _bul("Implemented Prometheus + Grafana observability stack adopted by 50 teams"),
        *_section_heading("Education"),
        _p("B.S. Information Systems  |  University of Washington  |  2016"),
        *_section_heading("Certifications"),
        _p(f"HashiCorp Certified Terraform Associate — active through {cert}"),
        *_section_heading("Skills"),
        *_skills_block("Terraform, Kubernetes, AWS, CI/CD, Docker, Prometheus, Grafana, Ansible"),
    ]


def _story_priya_sharma(cf: dict) -> list:
    cert = cf["custcolumn3"]
    return [
        *_header_block(
            "Priya Sharma",
            "priya.sharma@gmail.com  |  +1 312 555 0198  |  Chicago, IL, USA",
        ),
        *_natural_prefs_section(cf),
        *_section_heading("Professional Summary"),
        _p("Product Manager with 9 years of experience in B2B SaaS and enterprise software. "
           "Consistent record of shipping products that grow ARR and reduce churn."),
        *_section_heading("Work Experience"),
        *_job("Senior Product Manager", "Salesforce", "2020 – Present", "Chicago, IL"),
        _bul("Owned roadmap for CRM Analytics module used by 15,000+ customers globally"),
        _bul("Drove 25% increase in feature adoption through data-driven prioritization"),
        Spacer(1, 2 * mm),
        *_job("Product Manager", "HubSpot", "2015 – 2020", "Chicago, IL"),
        _bul("Launched 3 new integrations contributing $4M ARR in the first year"),
        _bul("Reduced onboarding time by 50% through a self-serve wizard redesign"),
        *_section_heading("Education"),
        _p("MBA  |  Booth School of Business, University of Chicago  |  2015"),
        _p("B.Tech Computer Science  |  IIT Delhi  |  2013"),
        *_section_heading("Certifications"),
        _p(f"Certified Scrum Product Owner (CSPO) — active through {cert}"),
        *_section_heading("Skills"),
        *_skills_block("Product Strategy, Roadmapping, SQL, A/B Testing, JIRA, Figma, Amplitude"),
    ]


GROUND_TRUTH = {
    "schema_version": "1.0",
    "generated_on": "2026-05-14",
    "notes": {
        "custcolumn4": "checkbox — '1' = open to relocation (Yes in resume), '0' = not open (No in resume)",
        "custcolumn5": "dropdown — must match exactly: Full-time, Part-time, Contract, Internship, Freelance",
        "custcolumn6": "multiselect — assert each listed element is present in parsed response",
        "custcolumn10": "number — expected as numeric string (no currency symbol)",
        "date_format": "YYYY-MM-DD",
        "resume_section": "Custom-field values appear in natural sentences under 'Availability & Preferences' (same strings as custcolumn values)",
    },
    "field_map": {
        "1":  {"name": "Preferred Contact Window",        "type": "text",           "custcolumn": "custcolumn1"},
        "2":  {"name": "Career Objective Statement",       "type": "longtext",       "custcolumn": "custcolumn2"},
        "3":  {"name": "Professional Certification Expiry","type": "date",           "custcolumn": "custcolumn3"},
        "4":  {"name": "Open to Relocation",               "type": "checkbox",       "custcolumn": "custcolumn4"},
        "5":  {"name": "Preferred Employment Type",        "type": "dropdown",       "custcolumn": "custcolumn5"},
        "6":  {"name": "Acceptable Work Arrangements",     "type": "multiselect",    "custcolumn": "custcolumn6"},
        "7":  {"name": "Alternate Contact Phone",          "type": "phonenumber",    "custcolumn": "custcolumn7"},
        "8":  {"name": "Secondary Professional Email",     "type": "email",          "custcolumn": "custcolumn8"},
        "9":  {"name": "Portfolio Website URL",            "type": "social_profile", "custcolumn": "custcolumn9"},
        "10": {"name": "Expected Annual Salary",           "type": "number",         "custcolumn": "custcolumn10"},
    },
    "test_cases": [
        {
            "tc_id": "TC001",
            "resume_file": "001_sarah_johnson.pdf",
            "candidate_name": "Sarah Johnson",
            "standard_fields": {
                "firstname":       "Sarah",
                "lastname":        "Johnson",
                "emailid":         "sarah.johnson@gmail.com",
                "contactnumber":   "+1 415 555 0198",
                "city":            "San Francisco",
                "state":           "California",
                "country":         "United States",
                "position":        "Senior Software Engineer",
                "lastorganisation":"Stripe",
                "workexpyr":       6,
                "skill":           "Python, Go, AWS, PostgreSQL, Kubernetes, Redis, gRPC",
                "qualification":   "B.S. Computer Science",
            },
            "custom_fields": {
                "custcolumn1":  "weekday mornings",
                "custcolumn2":  "Looking to move into a principal engineering role at a high-growth fintech company where I can own platform reliability and mentor junior engineers.",
                "custcolumn3":  "2027-03-31",
                "custcolumn4":  "1",
                "custcolumn5":  "Full-time",
                "custcolumn6":  ["Remote", "Hybrid"],
                "custcolumn7":  "+1 415 555 0142",
                "custcolumn8":  "sarah.work@protonmail.com",
                "custcolumn9":  "https://github.com/sarahjohnson",
                "custcolumn10": "120000",
            },
            "assert_hints": {
                "custcolumn1":  {"match": "contains"},
                "custcolumn2":  {"match": "semantic", "note": "longtext — semantic match preferred"},
                "custcolumn3":  {"match": "exact"},
                "custcolumn4":  {"match": "exact"},
                "custcolumn5":  {"match": "exact"},
                "custcolumn6":  {"match": "all_items_present"},
                "custcolumn7":  {"match": "contains", "note": "phone format may vary"},
                "custcolumn8":  {"match": "exact"},
                "custcolumn9":  {"match": "contains"},
                "custcolumn10": {"match": "exact"},
            },
        },
        {
            "tc_id": "TC002",
            "resume_file": "002_james_patel.pdf",
            "candidate_name": "James Patel",
            "standard_fields": {
                "firstname":       "James",
                "lastname":        "Patel",
                "emailid":         "james.patel@gmail.com",
                "contactnumber":   "+1 212 555 0198",
                "city":            "New York",
                "state":           "New York",
                "country":         "United States",
                "position":        "Senior Data Scientist",
                "lastorganisation":"Bloomberg LP",
                "workexpyr":       5,
                "skill":           "Python, R, TensorFlow, Spark, SQL, Airflow",
                "qualification":   "M.S. Statistics",
            },
            "custom_fields": {
                "custcolumn1":  "afternoons after 2pm",
                "custcolumn2":  "Aiming to transition from data science into a machine learning engineering role where I can build production-grade ML pipelines and recommendation systems.",
                "custcolumn3":  "2026-11-30",
                "custcolumn4":  "0",
                "custcolumn5":  "Contract",
                "custcolumn6":  ["Remote"],
                "custcolumn7":  "+1 212 555 0187",
                "custcolumn8":  "james.patel.pro@outlook.com",
                "custcolumn9":  "https://www.kaggle.com/jamespatel",
                "custcolumn10": "145000",
            },
            "assert_hints": {
                "custcolumn1":  {"match": "contains"},
                "custcolumn2":  {"match": "semantic"},
                "custcolumn3":  {"match": "exact"},
                "custcolumn4":  {"match": "exact"},
                "custcolumn5":  {"match": "exact"},
                "custcolumn6":  {"match": "all_items_present"},
                "custcolumn7":  {"match": "contains"},
                "custcolumn8":  {"match": "exact"},
                "custcolumn9":  {"match": "contains"},
                "custcolumn10": {"match": "exact"},
            },
        },
        {
            "tc_id": "TC003",
            "resume_file": "003_emily_chen.pdf",
            "candidate_name": "Emily Chen",
            "standard_fields": {
                "firstname":       "Emily",
                "lastname":        "Chen",
                "emailid":         "emily.chen@gmail.com",
                "contactnumber":   "+1 650 555 0198",
                "city":            "Austin",
                "state":           "Texas",
                "country":         "United States",
                "position":        "Senior UX Designer",
                "lastorganisation":"Dell Technologies",
                "workexpyr":       7,
                "skill":           "Figma, Sketch, UserTesting, HTML, CSS, Framer",
                "qualification":   "B.F.A. Interaction Design",
            },
            "custom_fields": {
                "custcolumn1":  "evenings between 6pm and 8pm",
                "custcolumn2":  "Passionate about building intuitive consumer products; seeking a senior UX designer role at a company where design drives product decisions.",
                "custcolumn3":  "2025-12-15",
                "custcolumn4":  "1",
                "custcolumn5":  "Full-time",
                "custcolumn6":  ["On-site", "Hybrid"],
                "custcolumn7":  "+1 650 555 0231",
                "custcolumn8":  "emily.chen.design@gmail.com",
                "custcolumn9":  "https://www.behance.net/emilychen",
                "custcolumn10": "95000",
            },
            "assert_hints": {
                "custcolumn1":  {"match": "contains"},
                "custcolumn2":  {"match": "semantic"},
                "custcolumn3":  {"match": "exact"},
                "custcolumn4":  {"match": "exact"},
                "custcolumn5":  {"match": "exact"},
                "custcolumn6":  {"match": "all_items_present"},
                "custcolumn7":  {"match": "contains"},
                "custcolumn8":  {"match": "exact"},
                "custcolumn9":  {"match": "contains"},
                "custcolumn10": {"match": "exact"},
            },
        },
        {
            "tc_id": "TC004",
            "resume_file": "004_michael_torres.pdf",
            "candidate_name": "Michael Torres",
            "standard_fields": {
                "firstname":       "Michael",
                "lastname":        "Torres",
                "emailid":         "m.torres@gmail.com",
                "contactnumber":   "+1 206 555 0198",
                "city":            "Seattle",
                "state":           "Washington",
                "country":         "United States",
                "position":        "Senior DevOps Engineer",
                "lastorganisation":"Amazon Web Services",
                "workexpyr":       8,
                "skill":           "Terraform, Kubernetes, AWS, CI/CD, Docker, Prometheus",
                "qualification":   "B.S. Information Systems",
            },
            "custom_fields": {
                "custcolumn1":  "Monday to Friday, 9am to noon",
                "custcolumn2":  "Seeking a senior DevOps or SRE role where I can build scalable cloud infrastructure and drive cost optimization initiatives across engineering teams.",
                "custcolumn3":  "2026-06-30",
                "custcolumn4":  "1",
                "custcolumn5":  "Full-time",
                "custcolumn6":  ["Remote", "Weekly travel"],
                "custcolumn7":  "+1 512 555 0087",
                "custcolumn8":  "mike.torres.devops@yahoo.com",
                "custcolumn9":  "https://github.com/mitorres",
                "custcolumn10": "135000",
            },
            "assert_hints": {
                "custcolumn1":  {"match": "contains"},
                "custcolumn2":  {"match": "semantic"},
                "custcolumn3":  {"match": "exact"},
                "custcolumn4":  {"match": "exact"},
                "custcolumn5":  {"match": "exact"},
                "custcolumn6":  {"match": "all_items_present"},
                "custcolumn7":  {"match": "contains"},
                "custcolumn8":  {"match": "exact"},
                "custcolumn9":  {"match": "contains"},
                "custcolumn10": {"match": "exact"},
            },
        },
        {
            "tc_id": "TC005",
            "resume_file": "005_priya_sharma.pdf",
            "candidate_name": "Priya Sharma",
            "standard_fields": {
                "firstname":       "Priya",
                "lastname":        "Sharma",
                "emailid":         "priya.sharma@gmail.com",
                "contactnumber":   "+1 312 555 0198",
                "city":            "Chicago",
                "state":           "Illinois",
                "country":         "United States",
                "position":        "Senior Product Manager",
                "lastorganisation":"Salesforce",
                "workexpyr":       9,
                "skill":           "Product Strategy, Roadmapping, SQL, A/B Testing, JIRA, Figma",
                "qualification":   "MBA",
            },
            "custom_fields": {
                "custcolumn1":  "Tuesday and Thursday mornings",
                "custcolumn2":  "Aiming for a senior PM role in a B2B SaaS company where I can drive product strategy and lead cross-functional teams through 0-to-1 launches.",
                "custcolumn3":  "2027-09-30",
                "custcolumn4":  "0",
                "custcolumn5":  "Full-time",
                "custcolumn6":  ["Hybrid", "Client-site"],
                "custcolumn7":  "+1 408 555 0312",
                "custcolumn8":  "priya.sharma.pm@protonmail.com",
                "custcolumn9":  "https://www.linkedin.com/in/priyasharma-pm",
                "custcolumn10": "150000",
            },
            "assert_hints": {
                "custcolumn1":  {"match": "contains"},
                "custcolumn2":  {"match": "semantic"},
                "custcolumn3":  {"match": "exact"},
                "custcolumn4":  {"match": "exact"},
                "custcolumn5":  {"match": "exact"},
                "custcolumn6":  {"match": "all_items_present"},
                "custcolumn7":  {"match": "contains"},
                "custcolumn8":  {"match": "exact"},
                "custcolumn9":  {"match": "contains"},
                "custcolumn10": {"match": "exact"},
            },
        },
    ],
}


RESUMES = [
    ("001_sarah_johnson.pdf",  _story_sarah_johnson),
    ("002_james_patel.pdf",    _story_james_patel),
    ("003_emily_chen.pdf",     _story_emily_chen),
    ("004_michael_torres.pdf", _story_michael_torres),
    ("005_priya_sharma.pdf",   _story_priya_sharma),
]


def main() -> None:
    tc_by_file = {tc["resume_file"]: tc for tc in GROUND_TRUTH["test_cases"]}
    print("Generating test resumes …")
    for filename, story_fn in RESUMES:
        tc = tc_by_file[filename]
        path = RESUME_DIR / filename
        _build_pdf(path, story_fn(tc["custom_fields"]))
        print(f"  ✓  {path}  ({tc['tc_id']})")

    gt_path = OUTPUT_DIR / "ground_truth.json"
    gt_path.write_text(json.dumps(GROUND_TRUTH, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    print(f"  ✓  {gt_path}")
    print(f"\nDone. Files in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()

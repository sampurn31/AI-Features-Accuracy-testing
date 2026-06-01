#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate TC007_INVALID_DEPENDENCY Resume
-----------------------------------------
This resume violates a dependency rule:
  - Work Mode Preference = "Remote"
  - According to DEPENDENCY_RULES in nested_custom_field_pipeline.py:
    • col 6 (Onsite Location) should be HIDDEN when Work Mode = Remote
  - BUT the resume text includes an Onsite Location value
  - Expected: parser should NOT extract Onsite Location (null in ground truth)
  
This tests that the parser correctly respects dependency rules and doesn't
extract values for fields that should be hidden based on parent values.
"""

import json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

def _make_styles():
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(
        name='Name',
        parent=styles['Title'],
        fontSize=24,
        textColor=HexColor('#1a365d'),
        spaceAfter=6,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    ))
    
    styles.add(ParagraphStyle(
        name='Contact',
        parent=styles['Normal'],
        fontSize=10,
        textColor=HexColor('#2d3748'),
        spaceAfter=20,
        alignment=TA_CENTER
    ))
    
    styles.add(ParagraphStyle(
        name='SectionHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=HexColor('#2b6cb0'),
        spaceBefore=16,
        spaceAfter=10,
        fontName='Helvetica-Bold'
    ))
    
    styles.add(ParagraphStyle(
        name='JobTitle',
        parent=styles['Normal'],
        fontSize=12,
        textColor=HexColor('#1a202c'),
        fontName='Helvetica-Bold',
        spaceAfter=4
    ))
    
    styles.add(ParagraphStyle(
        name='JobMeta',
        parent=styles['Normal'],
        fontSize=10,
        textColor=HexColor('#4a5568'),
        fontName='Helvetica-Oblique',
        spaceAfter=6
    ))
    
    if 'BodyText' not in styles:
        styles.add(ParagraphStyle(
            name='BodyText',
            parent=styles['Normal'],
            fontSize=10,
            textColor=HexColor('#2d3748'),
            spaceAfter=12,
            leading=14
        ))
    
    return styles


def _accent_rule(width_inches=6.5, color='#2b6cb0'):
    """Colored horizontal rule"""
    t = Table([['']], colWidths=[width_inches * inch], rowHeights=[2])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor(color)),
    ]))
    return t


def _header_block(name, contact_lines, styles):
    """Name + contact centered at top"""
    story = []
    story.append(Paragraph(name, styles['Name']))
    story.append(Paragraph(' | '.join(contact_lines), styles['Contact']))
    story.append(_accent_rule(color='#2b6cb0'))
    story.append(Spacer(1, 0.15 * inch))
    return story


def _section_heading(title, styles):
    """Section heading with rule"""
    story = []
    story.append(Paragraph(title, styles['SectionHeading']))
    story.append(_accent_rule(width_inches=6.5, color='#cbd5e0'))
    return story


def _job(title, company, dates, bullets, styles):
    """Job entry with title/company/dates/bullets"""
    story = []
    story.append(Paragraph(title, styles['JobTitle']))
    story.append(Paragraph(f"{company} | {dates}", styles['JobMeta']))
    for b in bullets:
        story.append(Paragraph(f"• {b}", styles['BodyText']))
    story.append(Spacer(1, 0.1 * inch))
    return story


def _skills_block(skills_list, styles):
    """Skills in a shaded box"""
    skills_text = ' • '.join(skills_list)
    t = Table([[Paragraph(skills_text, styles['BodyText'])]], colWidths=[6.5 * inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor('#edf2f7')),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return [t, Spacer(1, 0.1 * inch)]


def _story_mary_jones_invalid(styles):
    """
    Mary Jones - INVALID NESTED DEPENDENCY TEST CASE
    
    Nested Custom Fields:
    - custcolumn1 (Willing to Travel): "1" 
    - custcolumn2 (Work Mode Preference): "Remote"  ← This makes col6 HIDDEN
    - custcolumn3 (Technical Skill Focus): ["Backend", "Data"]
    - custcolumn4 (Travel Radius): "100 miles"  (visible, parent=1)
    - custcolumn5 (Office Setup Type): "Dedicated room"  (visible for Remote)
    - custcolumn6 (Onsite Location): null  ← SHOULD BE NULL (hidden by Remote)
    - custcolumn7 (Backend Stack): ["Django", "FastAPI"]  (visible for Backend+Data)
    - custcolumn8 (Data Tool Preference): "Apache Spark"  (visible for Data)
    - custcolumn9 (Notice Period): 30
    - custcolumn10 (Preferred Contract Rate): "$95/hour"
    
    VIOLATION: The resume text will mention working at "San Francisco office"
    but since Work Mode = Remote, Onsite Location should be hidden and not parsed.
    """
    story = []
    
    story.extend(_header_block(
        'Mary Jones',
        ['+1 650 555 0123', 'mary.jones@techmail.com', 'linkedin.com/in/maryjones'],
        styles
    ))
    
    story.extend(_section_heading('Professional Summary', styles))
    story.append(Paragraph(
        "Senior Backend Engineer with 8 years of experience building scalable data pipelines "
        "and microservices. I work remotely from home with a dedicated room setup for maximum "
        "productivity. I'm willing to travel up to 100 miles for team meetings or client visits. "
        "My expertise spans backend development and data engineering, with deep knowledge of "
        "Django and FastAPI frameworks. I prefer using Apache Spark for large-scale data processing. "
        "I have 30 days notice period and am seeking contract roles at $95/hour. "
        "Previously worked at the San Francisco office before transitioning to full remote work.",
        styles['BodyText']
    ))
    
    story.extend(_section_heading('Work Experience', styles))
    story.extend(_job(
        'Senior Backend Engineer',
        'DataFlow Systems',
        '2020 - Present',
        [
            'Architected microservices using Django and FastAPI for real-time data processing',
            'Built ETL pipelines with Apache Spark processing 10TB+ daily',
            'Led remote team of 5 engineers across 3 time zones',
            'Implemented CI/CD pipelines reducing deployment time by 60%'
        ],
        styles
    ))
    
    story.extend(_job(
        'Backend Developer',
        'CloudTech Inc',
        '2017 - 2020',
        [
            'Developed RESTful APIs using Django REST Framework',
            'Optimized database queries reducing response time by 40%',
            'Collaborated with data science team on ML model deployment',
            'Mentored 3 junior developers in backend best practices'
        ],
        styles
    ))
    
    story.extend(_section_heading('Education', styles))
    story.append(Paragraph(
        '<b>M.S. Computer Science</b> — Stanford University, 2017',
        styles['BodyText']
    ))
    story.append(Paragraph(
        '<b>B.S. Software Engineering</b> — UC Berkeley, 2015',
        styles['BodyText']
    ))
    
    story.extend(_section_heading('Technical Skills', styles))
    story.extend(_skills_block([
        'Python', 'Django', 'FastAPI', 'Apache Spark', 'PostgreSQL',
        'Redis', 'Docker', 'Kubernetes', 'AWS', 'Git'
    ], styles))
    
    story.extend(_section_heading('Additional Information', styles))
    story.append(Paragraph(
        "I maintain a professional home office with dedicated room for deep work. "
        "Available for contract positions at $95/hour with 30 days notice period. "
        "Open to travel within 100 miles radius for important meetings or workshops. "
        "Remote work preference allows me to maintain work-life balance while delivering "
        "high-quality results.",
        styles['BodyText']
    ))
    
    return story


def main():
    test_data_dir = Path("test_data")
    resumes_dir = test_data_dir / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    
    gt_file = test_data_dir / "ground_truth.json"
    
    pdf_path = resumes_dir / "007_mary_jones_invalid.pdf"
    styles = _make_styles()
    
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch
    )
    
    story = _story_mary_jones_invalid(styles)
    doc.build(story)
    print(f"✓ Generated: {pdf_path}")
    
    NEW_TEST_CASE = {
        "test_case_id": "TC007_INVALID_DEPENDENCY",
        "resume_file": "007_mary_jones_invalid.pdf",
        "description": "INVALID: Onsite Location present in text but Work Mode=Remote should hide it",
        "standard_fields": {
            "firstname": "Mary",
            "lastname": "Jones",
            "email": "mary.jones@techmail.com",
            "phone": "+1 650 555 0123"
        },
        "nested_custom_fields": {
            "custcolumn1": "1",
            "custcolumn2": "Remote",
            "custcolumn3": ["Backend", "Data"],
            "custcolumn4": "100 miles",
            "custcolumn5": "Dedicated room",
            "custcolumn6": None,
            "custcolumn7": ["Django", "FastAPI"],
            "custcolumn8": "Apache Spark",
            "custcolumn9": 30,
            "custcolumn10": "$95/hour"
        },
        "validation_notes": {
            "dependency_violation": "custcolumn6 (Onsite Location)",
            "reason": "Resume text mentions 'San Francisco office' but Work Mode Preference is 'Remote', which should hide Onsite Location field",
            "expected_behavior": "Parser should NOT extract custcolumn6 value even though text is present, because dependency rules make this field hidden"
        }
    }
    
    if gt_file.exists():
        with open(gt_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {
            "field_map": {},
            "test_cases": [],
            "nested_test_cases": []
        }
    
    if "nested_test_cases" not in data:
        data["nested_test_cases"] = []
    
    data["nested_test_cases"].append(NEW_TEST_CASE)
    
    with open(gt_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Updated: {gt_file}")
    print(f"✓ Added test case: TC007_INVALID_DEPENDENCY")
    print("\nKey points:")
    print("  • Work Mode Preference: Remote")
    print("  • Onsite Location: Should be HIDDEN (null in ground truth)")
    print("  • Resume text mentions 'San Francisco office'")
    print("  • Expected: Parser should NOT extract custcolumn6 value")
    print("\nThis tests that the parser correctly respects dependency rules!")


if __name__ == "__main__":
    main()

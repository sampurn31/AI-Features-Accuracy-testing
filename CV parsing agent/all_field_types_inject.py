#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
all_field_types_inject.py
────────────────────────────────────────────────────────────────────────────────
Standalone script – no API calls required.

Creates 6 synthetic resume PDFs, each with all 10 custom field types injected
as natural-language snippets, and saves them to:

    pipeline_output/injected_pdfs/CF_R00{1-6}_all_fields.pdf

Ground truth (values + snippets) is written to:

    pipeline_output/all_field_types_ground_truth.json

Field types covered (one field per type):
  col 1  – text            → Preferred Contact Window
  col 2  – longtext        → Career Objective Statement
  col 3  – date            → Professional Certification Expiry
  col 4  – checkbox        → Open to Relocation
  col 5  – dropdown        → Preferred Employment Type
  col 6  – multiselect     → Acceptable Work Arrangements
  col 7  – phonenumber     → Alternate Contact Phone
  col 8  – email           → Secondary Professional Email
  col 9  – social_profile  → Portfolio Website URL
  col 10 – date_time       → Interview Availability Start
"""

import json
import random
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm

OUTPUT_DIR       = Path("pipeline_output")
INJECTED_PDF_DIR = OUTPUT_DIR / "injected_pdfs"
GT_FILE          = OUTPUT_DIR / "all_field_types_ground_truth.json"

OUTPUT_DIR.mkdir(exist_ok=True)
INJECTED_PDF_DIR.mkdir(parents=True, exist_ok=True)

CUSTOM_FIELDS = [
    {
        "column_index": 1,
        "name": "Preferred Contact Window",
        "type": "text",
        "description": "Short phrase for when the candidate prefers recruiter calls.",
        "options": [],
    },
    {
        "column_index": 2,
        "name": "Career Objective Statement",
        "type": "longtext",
        "description": "1-3 sentence career objective for a profile sidebar.",
        "options": [],
    },
    {
        "column_index": 3,
        "name": "Professional Certification Expiry",
        "type": "date",
        "description": "Expiry date of the candidate's main professional credential (ISO 8601).",
        "options": [],
    },
    {
        "column_index": 4,
        "name": "Open to Relocation",
        "type": "checkbox",
        "description": "Whether the candidate would consider moving for the right role. 1=yes, 0=no.",
        "options": [],
    },
    {
        "column_index": 5,
        "name": "Preferred Employment Type",
        "type": "dropdown",
        "description": "The engagement style the candidate is targeting.",
        "options": ["Full-time", "Part-time", "Contract", "Internship", "Freelance"],
    },
    {
        "column_index": 6,
        "name": "Acceptable Work Arrangements",
        "type": "multiselect",
        "description": "Workplace presence patterns the candidate can commit to.",
        "options": ["Remote", "Hybrid", "On-site", "Weekly travel", "Client-site"],
    },
    {
        "column_index": 7,
        "name": "Alternate Contact Phone",
        "type": "phonenumber",
        "description": "Secondary phone number for recruiting contact.",
        "options": [],
    },
    {
        "column_index": 8,
        "name": "Secondary Professional Email",
        "type": "email",
        "description": "A secondary work email address.",
        "options": [],
    },
    {
        "column_index": 9,
        "name": "Portfolio Website URL",
        "type": "social_profile",
        "description": "Public portfolio or project website URL.",
        "options": [],
    },
    {
        "column_index": 10,
        "name": "Interview Availability Start",
        "type": "date_time",
        "description": "Earliest date-time the candidate can join a first interview (ISO 8601).",
        "options": [],
    },
]

PROFILES = [
    {
        "id": "CF_R001",
        "resume_text": (
            "Alice Chen\n"
            "alice.chen@gmail.com | +1-408-555-0147 | San Jose, CA\n"
            "linkedin.com/in/alicechen\n\n"
            "SUMMARY\n"
            "Senior backend engineer with 8 years of experience designing high-throughput "
            "distributed systems in Python and Go. Proven track record of reducing API latency "
            "by 40% through caching strategies and asynchronous processing.\n\n"
            "EXPERIENCE\n\n"
            "Senior Software Engineer — TechNova Inc., San Jose, CA (2019–Present)\n"
            "- Led migration of monolithic Python services to Kubernetes-based microservices.\n"
            "- Reduced average API response time from 320 ms to 190 ms via Redis caching.\n"
            "- Mentored a team of 4 junior engineers through quarterly code reviews.\n\n"
            "Software Engineer — DataStream Labs, San Francisco, CA (2016–2019)\n"
            "- Designed ETL pipeline processing 50 M daily events with Apache Kafka and Spark.\n"
            "- Built internal analytics dashboard used by 200+ product managers.\n\n"
            "EDUCATION\n"
            "B.S. Computer Science — UC Berkeley, 2016\n\n"
            "SKILLS\n"
            "Python, Go, Kubernetes, Redis, Kafka, PostgreSQL, AWS, Docker"
        ),
        "injected_fields": [
            {
                "column_index": 1,
                "field_name": "Preferred Contact Window",
                "field_type": "text",
                "value": "Weekday mornings, 9-11 AM PST",
                "snippet": "I am generally reachable for recruiter calls on weekday mornings, 9-11 AM PST.",
            },
            {
                "column_index": 2,
                "field_name": "Career Objective Statement",
                "field_type": "longtext",
                "value": (
                    "Seeking a Principal Engineer or Staff Engineer role at a product-led company "
                    "where I can architect scalable backend platforms, influence technical roadmaps, "
                    "and grow a high-performing engineering team."
                ),
                "snippet": (
                    "Seeking a Principal Engineer or Staff Engineer role at a product-led company "
                    "where I can architect scalable backend platforms, influence technical roadmaps, "
                    "and grow a high-performing engineering team."
                ),
            },
            {
                "column_index": 3,
                "field_name": "Professional Certification Expiry",
                "field_type": "date",
                "value": "2027-06-30",
                "snippet": "My AWS Solutions Architect certification is valid through June 30, 2027.",
            },
            {
                "column_index": 4,
                "field_name": "Open to Relocation",
                "field_type": "checkbox",
                "value": "1",
                "snippet": "I am open to relocation for the right opportunity, including positions outside California.",
            },
            {
                "column_index": 5,
                "field_name": "Preferred Employment Type",
                "field_type": "dropdown",
                "value": "Full-time",
                "snippet": (
                    "I am targeting full-time positions with direct employer relationships "
                    "rather than contract or agency placements."
                ),
            },
            {
                "column_index": 6,
                "field_name": "Acceptable Work Arrangements",
                "field_type": "multiselect",
                "value": ["Remote", "Hybrid"],
                "snippet": (
                    "I am comfortable working fully remote or in a hybrid arrangement "
                    "with occasional in-office days."
                ),
            },
            {
                "column_index": 7,
                "field_name": "Alternate Contact Phone",
                "field_type": "phonenumber",
                "value": "+1-650-555-0284",
                "snippet": "Alternatively, you may reach me at +1-650-555-0284 if my primary line is unavailable.",
            },
            {
                "column_index": 8,
                "field_name": "Secondary Professional Email",
                "field_type": "email",
                "value": "a.chen.engineering@protonmail.com",
                "snippet": "For professional correspondence, I can also be reached at a.chen.engineering@protonmail.com.",
            },
            {
                "column_index": 9,
                "field_name": "Portfolio Website URL",
                "field_type": "social_profile",
                "value": "https://alicechen.dev",
                "snippet": "A portfolio of open-source projects and case studies is available at https://alicechen.dev.",
            },
            {
                "column_index": 10,
                "field_name": "Interview Availability Start",
                "field_type": "date_time",
                "value": "2026-05-12T09:00:00",
                "snippet": "I am available for a first-round interview from Monday May 12, 2026 at 9:00 AM onwards.",
            },
        ],
    },

    {
        "id": "CF_R002",
        "resume_text": (
            "Marcus Williams\n"
            "marcus.williams@outlook.com | +44-7700-900142 | London, UK\n"
            "linkedin.com/in/marcuswilliams\n\n"
            "PROFESSIONAL SUMMARY\n"
            "Data scientist specialising in NLP and recommendation systems, with 6 years of "
            "experience across fintech and e-commerce. MSc in Machine Learning from UCL.\n\n"
            "EXPERIENCE\n\n"
            "Data Scientist II — Finlytix Ltd., London (2021–Present)\n"
            "- Built credit risk model achieving AUC 0.94, reducing default rate by 18%.\n"
            "- Deployed BERT-based document classifier handling 30,000 daily loan applications.\n"
            "- Collaborated with product teams to A/B test recommendation engine (CTR +12%).\n\n"
            "Data Analyst — ShopEasy UK, Manchester (2018–2021)\n"
            "- Developed customer segmentation model using k-means and RFM analysis.\n"
            "- Created automated weekly sales reports in Python/Pandas, saving 8 hrs/week.\n\n"
            "EDUCATION\n"
            "MSc Machine Learning — University College London, 2018\n"
            "BSc Mathematics — University of Manchester, 2016\n\n"
            "SKILLS\n"
            "Python, TensorFlow, PyTorch, scikit-learn, SQL, Spark, Tableau, NLP, BERT"
        ),
        "injected_fields": [
            {
                "column_index": 1,
                "field_name": "Preferred Contact Window",
                "field_type": "text",
                "value": "Tuesday and Thursday afternoons, 2-5 PM GMT",
                "snippet": "The best time to reach me is Tuesday and Thursday afternoons between 2 and 5 PM GMT.",
            },
            {
                "column_index": 2,
                "field_name": "Career Objective Statement",
                "field_type": "longtext",
                "value": (
                    "Aiming to join a research-driven team as a Senior Data Scientist where I can "
                    "apply deep learning to real-world financial problems and publish findings that "
                    "push the state of the art in responsible AI."
                ),
                "snippet": (
                    "Aiming to join a research-driven team as a Senior Data Scientist where I can "
                    "apply deep learning to real-world financial problems and publish findings that "
                    "push the state of the art in responsible AI."
                ),
            },
            {
                "column_index": 3,
                "field_name": "Professional Certification Expiry",
                "field_type": "date",
                "value": "2026-12-31",
                "snippet": "My Google Professional Machine Learning Engineer certificate expires on December 31, 2026.",
            },
            {
                "column_index": 4,
                "field_name": "Open to Relocation",
                "field_type": "checkbox",
                "value": "0",
                "snippet": "I am not currently open to relocation and wish to remain London-based for personal reasons.",
            },
            {
                "column_index": 5,
                "field_name": "Preferred Employment Type",
                "field_type": "dropdown",
                "value": "Contract",
                "snippet": (
                    "My preferred arrangement is contract work, typically six to twelve month "
                    "engagements through my personal limited company."
                ),
            },
            {
                "column_index": 6,
                "field_name": "Acceptable Work Arrangements",
                "field_type": "multiselect",
                "value": ["Remote", "Hybrid", "On-site"],
                "snippet": "I can commit to remote, hybrid, or fully on-site work depending on the team's preference.",
            },
            {
                "column_index": 7,
                "field_name": "Alternate Contact Phone",
                "field_type": "phonenumber",
                "value": "+44-7911-555023",
                "snippet": "A secondary UK number where I can be reached is +44-7911-555023.",
            },
            {
                "column_index": 8,
                "field_name": "Secondary Professional Email",
                "field_type": "email",
                "value": "m.williams.data@gmail.com",
                "snippet": "You are also welcome to contact me via m.williams.data@gmail.com for project discussions.",
            },
            {
                "column_index": 9,
                "field_name": "Portfolio Website URL",
                "field_type": "social_profile",
                "value": "https://mwilliams-ml.github.io",
                "snippet": "My project write-ups and published models can be viewed at https://mwilliams-ml.github.io.",
            },
            {
                "column_index": 10,
                "field_name": "Interview Availability Start",
                "field_type": "date_time",
                "value": "2026-05-18T14:00:00",
                "snippet": "I am free for an initial interview call from May 18, 2026 at 2:00 PM GMT.",
            },
        ],
    },

    {
        "id": "CF_R003",
        "resume_text": (
            "Priya Sharma\n"
            "priya.sharma@gmail.com | +91-98200-11234 | Bangalore, India\n"
            "linkedin.com/in/priyasharma\n\n"
            "ABOUT ME\n"
            "Product manager with 7 years of experience in SaaS and mobile products. Expert "
            "in user research, roadmap prioritisation, and cross-functional team leadership. "
            "Delivered products used by 5M+ end users across South-East Asia.\n\n"
            "EXPERIENCE\n\n"
            "Senior Product Manager — CloudOps Platforms, Bangalore (2020–Present)\n"
            "- Owned end-to-end roadmap for B2B workflow automation suite; grew ARR by $2.4 M.\n"
            "- Ran 40+ customer discovery interviews and translated insights into OKRs.\n"
            "- Coordinated three-sprint feature launches with engineering, design, and QA.\n\n"
            "Product Manager — AppMesh Technologies, Pune (2017–2020)\n"
            "- Launched v2.0 of flagship mobile app; grew MAU from 800 K to 1.9 M in 9 months.\n"
            "- Defined and tracked north-star metrics, reducing churn by 22%.\n\n"
            "EDUCATION\n"
            "MBA (Marketing & Strategy) — IIM Bangalore, 2017\n"
            "B.Tech Computer Science — BITS Pilani, 2015\n\n"
            "SKILLS\n"
            "Product strategy, user research, Jira, Figma, SQL, A/B testing, OKRs, roadmapping"
        ),
        "injected_fields": [
            {
                "column_index": 1,
                "field_name": "Preferred Contact Window",
                "field_type": "text",
                "value": "Weekday evenings after 6 PM IST",
                "snippet": "Recruiter calls work best for me on weekday evenings after 6 PM IST.",
            },
            {
                "column_index": 2,
                "field_name": "Career Objective Statement",
                "field_type": "longtext",
                "value": (
                    "Looking to step into a Director of Product role at a B2B SaaS company where "
                    "I can lead a portfolio of products, build a world-class PM team, and drive "
                    "user-centric innovation at scale."
                ),
                "snippet": (
                    "Looking to step into a Director of Product role at a B2B SaaS company where "
                    "I can lead a portfolio of products, build a world-class PM team, and drive "
                    "user-centric innovation at scale."
                ),
            },
            {
                "column_index": 3,
                "field_name": "Professional Certification Expiry",
                "field_type": "date",
                "value": "2028-03-01",
                "snippet": "My Pragmatic Institute Product Management certification renews on March 1, 2028.",
            },
            {
                "column_index": 4,
                "field_name": "Open to Relocation",
                "field_type": "checkbox",
                "value": "1",
                "snippet": "I would consider relocating to Singapore or Dubai for the right leadership role.",
            },
            {
                "column_index": 5,
                "field_name": "Preferred Employment Type",
                "field_type": "dropdown",
                "value": "Full-time",
                "snippet": "I am seeking full-time permanent employment with equity participation.",
            },
            {
                "column_index": 6,
                "field_name": "Acceptable Work Arrangements",
                "field_type": "multiselect",
                "value": ["Hybrid", "On-site"],
                "snippet": "I perform best in a hybrid or fully on-site setup with regular face time with stakeholders.",
            },
            {
                "column_index": 7,
                "field_name": "Alternate Contact Phone",
                "field_type": "phonenumber",
                "value": "+91-80-555-0388",
                "snippet": "My office landline, +91-80-555-0388, is another way to get in touch during business hours.",
            },
            {
                "column_index": 8,
                "field_name": "Secondary Professional Email",
                "field_type": "email",
                "value": "priya.sharma.pm@outlook.com",
                "snippet": "Hiring teams may also reach me through priya.sharma.pm@outlook.com for scheduling.",
            },
            {
                "column_index": 9,
                "field_name": "Portfolio Website URL",
                "field_type": "social_profile",
                "value": "https://priyabuilds.notion.site",
                "snippet": "Product case studies and strategy frameworks I have authored are at https://priyabuilds.notion.site.",
            },
            {
                "column_index": 10,
                "field_name": "Interview Availability Start",
                "field_type": "date_time",
                "value": "2026-05-20T18:30:00",
                "snippet": "I can schedule a first-round discussion from May 20, 2026 at 6:30 PM IST.",
            },
        ],
    },

    {
        "id": "CF_R004",
        "resume_text": (
            "Lukas Hoffmann\n"
            "lukas.hoffmann@web.de | +49-151-5550-8823 | Berlin, Germany\n"
            "linkedin.com/in/lukashoffmann\n\n"
            "PROFILE\n"
            "UX/UI designer with 5 years of experience crafting intuitive digital products for "
            "FinTech and HealthTech clients. Expert in design systems, accessibility standards, "
            "and rapid prototyping from concept to high-fidelity in Figma.\n\n"
            "EXPERIENCE\n\n"
            "Senior UX Designer — HealthBridge GmbH, Berlin (2022–Present)\n"
            "- Redesigned patient portal, improving task completion rate from 67% to 91%.\n"
            "- Created and maintained a design system with 120+ reusable Figma components.\n"
            "- Led usability testing sessions with 80+ participants in 3 European cities.\n\n"
            "UX/UI Designer — Bankly AG, Frankfurt (2019–2022)\n"
            "- Designed onboarding flow that reduced drop-off by 35%.\n"
            "- Produced interactive prototypes that cut development rework time by 25%.\n\n"
            "EDUCATION\n"
            "B.Des Interaction Design — University of the Arts Berlin, 2019\n\n"
            "SKILLS\n"
            "Figma, Sketch, Adobe XD, HTML/CSS, Storybook, WCAG 2.1, user research, wireframing"
        ),
        "injected_fields": [
            {
                "column_index": 1,
                "field_name": "Preferred Contact Window",
                "field_type": "text",
                "value": "Mornings between 8 and 10 AM CET",
                "snippet": "The easiest time to catch me by phone is mornings between 8 and 10 AM CET.",
            },
            {
                "column_index": 2,
                "field_name": "Career Objective Statement",
                "field_type": "longtext",
                "value": (
                    "I want to join a mission-driven design team as a Lead UX Designer where I can "
                    "shape end-to-end product experiences, champion accessibility, and mentor the "
                    "next generation of interaction designers."
                ),
                "snippet": (
                    "I want to join a mission-driven design team as a Lead UX Designer where I can "
                    "shape end-to-end product experiences, champion accessibility, and mentor the "
                    "next generation of interaction designers."
                ),
            },
            {
                "column_index": 3,
                "field_name": "Professional Certification Expiry",
                "field_type": "date",
                "value": "2027-09-14",
                "snippet": "My IDEO Human-Centered Design certificate remains valid until September 14, 2027.",
            },
            {
                "column_index": 4,
                "field_name": "Open to Relocation",
                "field_type": "checkbox",
                "value": "0",
                "snippet": "Relocation is not feasible for me at this stage; I intend to stay in the Berlin area.",
            },
            {
                "column_index": 5,
                "field_name": "Preferred Employment Type",
                "field_type": "dropdown",
                "value": "Freelance",
                "snippet": "I work as a freelance designer on a project basis and am not seeking permanent employment.",
            },
            {
                "column_index": 6,
                "field_name": "Acceptable Work Arrangements",
                "field_type": "multiselect",
                "value": ["Remote", "Client-site"],
                "snippet": "I can work fully remote or spend time at the client's office when collaboration is needed.",
            },
            {
                "column_index": 7,
                "field_name": "Alternate Contact Phone",
                "field_type": "phonenumber",
                "value": "+49-30-555-0417",
                "snippet": "My Berlin studio landline is +49-30-555-0417, available during working hours.",
            },
            {
                "column_index": 8,
                "field_name": "Secondary Professional Email",
                "field_type": "email",
                "value": "lukas.design.studio@gmail.com",
                "snippet": "Project briefs and contracts can be sent to lukas.design.studio@gmail.com.",
            },
            {
                "column_index": 9,
                "field_name": "Portfolio Website URL",
                "field_type": "social_profile",
                "value": "https://lukashoffmann.design",
                "snippet": "My full design portfolio, including case studies and prototypes, lives at https://lukashoffmann.design.",
            },
            {
                "column_index": 10,
                "field_name": "Interview Availability Start",
                "field_type": "date_time",
                "value": "2026-05-15T08:00:00",
                "snippet": "I am available for introductory calls starting May 15, 2026 at 8:00 AM CET.",
            },
        ],
    },

    {
        "id": "CF_R005",
        "resume_text": (
            "Aisha Okonkwo\n"
            "aisha.okonkwo@protonmail.com | +1-617-555-0329 | Boston, MA\n"
            "linkedin.com/in/aishaokonkwo\n\n"
            "SUMMARY\n"
            "Site Reliability Engineer with 9 years of infrastructure and platform engineering "
            "experience. Deep expertise in Kubernetes, Terraform, and observability stacks. "
            "Passionate about reducing toil through intelligent automation and SLO-driven culture.\n\n"
            "EXPERIENCE\n\n"
            "Staff SRE — PayShift Inc., Boston, MA (2020–Present)\n"
            "- Designed and maintained multi-region Kubernetes cluster supporting 99.99% uptime.\n"
            "- Reduced MTTR from 45 minutes to 8 minutes via unified Grafana/Loki dashboard.\n"
            "- Championed SLO program across 14 engineering teams, defining 60+ error budgets.\n\n"
            "Senior DevOps Engineer — CloudFirst Solutions, New York, NY (2015–2020)\n"
            "- Automated CI/CD pipelines (Jenkins to GitHub Actions), cutting deploy time by 70%.\n"
            "- Managed Terraform modules for 300+ AWS resources across 5 accounts.\n\n"
            "EDUCATION\n"
            "B.S. Computer Engineering — MIT, 2015\n\n"
            "CERTIFICATIONS\n"
            "Certified Kubernetes Administrator (CKA), HashiCorp Terraform Associate\n\n"
            "SKILLS\n"
            "Kubernetes, Terraform, AWS, GCP, Prometheus, Grafana, Loki, ArgoCD, Helm, Python, Bash"
        ),
        "injected_fields": [
            {
                "column_index": 1,
                "field_name": "Preferred Contact Window",
                "field_type": "text",
                "value": "Any weekday between 12 and 2 PM EST",
                "snippet": "Phone screening calls are most convenient for me on any weekday between 12 and 2 PM EST.",
            },
            {
                "column_index": 2,
                "field_name": "Career Objective Statement",
                "field_type": "longtext",
                "value": (
                    "Looking for a Principal SRE or Engineering Manager, Platform role where I can "
                    "scale resilient infrastructure, codify reliability practices organisation-wide, "
                    "and build the tooling that lets other engineers move fast without breaking things."
                ),
                "snippet": (
                    "Looking for a Principal SRE or Engineering Manager, Platform role where I can "
                    "scale resilient infrastructure, codify reliability practices organisation-wide, "
                    "and build the tooling that lets other engineers move fast without breaking things."
                ),
            },
            {
                "column_index": 3,
                "field_name": "Professional Certification Expiry",
                "field_type": "date",
                "value": "2026-11-30",
                "snippet": "My Certified Kubernetes Administrator (CKA) certification expires November 30, 2026.",
            },
            {
                "column_index": 4,
                "field_name": "Open to Relocation",
                "field_type": "checkbox",
                "value": "1",
                "snippet": "I am willing to relocate within the US and am open to roles in Seattle, Austin, or NYC.",
            },
            {
                "column_index": 5,
                "field_name": "Preferred Employment Type",
                "field_type": "dropdown",
                "value": "Full-time",
                "snippet": "I am looking for a full-time staff-level position with comprehensive benefits.",
            },
            {
                "column_index": 6,
                "field_name": "Acceptable Work Arrangements",
                "field_type": "multiselect",
                "value": ["Remote", "Hybrid", "Weekly travel"],
                "snippet": (
                    "I am open to remote, hybrid, or arrangements that involve weekly travel "
                    "to client sites or offices."
                ),
            },
            {
                "column_index": 7,
                "field_name": "Alternate Contact Phone",
                "field_type": "phonenumber",
                "value": "+1-617-555-0411",
                "snippet": "If you cannot reach my primary cell, please try +1-617-555-0411.",
            },
            {
                "column_index": 8,
                "field_name": "Secondary Professional Email",
                "field_type": "email",
                "value": "aisha.sre.boston@gmail.com",
                "snippet": "Technical discussions can also be directed to aisha.sre.boston@gmail.com.",
            },
            {
                "column_index": 9,
                "field_name": "Portfolio Website URL",
                "field_type": "social_profile",
                "value": "https://aishaokonkwo.io",
                "snippet": (
                    "Infrastructure projects, architecture diagrams, and SLO templates are "
                    "published at https://aishaokonkwo.io."
                ),
            },
            {
                "column_index": 10,
                "field_name": "Interview Availability Start",
                "field_type": "date_time",
                "value": "2026-05-11T12:00:00",
                "snippet": "I am available for a first-round technical screen from May 11, 2026 at 12:00 PM EST.",
            },
        ],
    },

    {
        "id": "CF_R006",
        "resume_text": (
            "Sofia Reyes\n"
            "sofia.reyes@gmail.com | +34-612-555-0783 | Madrid, Spain\n"
            "linkedin.com/in/sofiareyes\n\n"
            "PROFESSIONAL PROFILE\n"
            "Digital marketing manager with 8 years of experience driving growth through "
            "data-driven campaigns, brand strategy, and content marketing for SaaS companies. "
            "Fluent in Spanish, English, and French.\n\n"
            "EXPERIENCE\n\n"
            "Marketing Manager — GrowthLabs EU, Madrid (2021–Present)\n"
            "- Planned and executed growth campaigns generating 28,000 qualified leads/year.\n"
            "- Managed EUR 1.2 M annual budget across SEO, paid social, and email channels.\n"
            "- Grew organic traffic by 180% in 18 months through content cluster strategy.\n\n"
            "Digital Marketing Specialist — SaaSify Inc., Barcelona (2016–2021)\n"
            "- Ran lifecycle email campaigns with 42% average open rate.\n"
            "- Built automated lead scoring model that improved sales handoff quality by 30%.\n\n"
            "EDUCATION\n"
            "BA Marketing & Communications — Universidad Complutense de Madrid, 2016\n\n"
            "SKILLS\n"
            "Google Analytics, HubSpot, Salesforce, SEMrush, Mailchimp, Meta Ads, LinkedIn Ads, "
            "Content strategy, SEO, Email marketing, SQL (basic)"
        ),
        "injected_fields": [
            {
                "column_index": 1,
                "field_name": "Preferred Contact Window",
                "field_type": "text",
                "value": "Fridays between 10 AM and 12 PM CET",
                "snippet": "I prefer recruiter calls on Fridays between 10 AM and 12 PM CET when my calendar is clearest.",
            },
            {
                "column_index": 2,
                "field_name": "Career Objective Statement",
                "field_type": "longtext",
                "value": (
                    "Targeting a Head of Marketing or VP Marketing position at a European SaaS "
                    "scale-up where I can own the full-funnel strategy, build a lean but high-impact "
                    "team, and position the brand for Series B and beyond."
                ),
                "snippet": (
                    "Targeting a Head of Marketing or VP Marketing position at a European SaaS "
                    "scale-up where I can own the full-funnel strategy, build a lean but high-impact "
                    "team, and position the brand for Series B and beyond."
                ),
            },
            {
                "column_index": 3,
                "field_name": "Professional Certification Expiry",
                "field_type": "date",
                "value": "2027-02-28",
                "snippet": "My HubSpot Marketing Software certification is valid until February 28, 2027.",
            },
            {
                "column_index": 4,
                "field_name": "Open to Relocation",
                "field_type": "checkbox",
                "value": "1",
                "snippet": "I would consider relocating to London, Amsterdam, or Lisbon for a senior leadership opportunity.",
            },
            {
                "column_index": 5,
                "field_name": "Preferred Employment Type",
                "field_type": "dropdown",
                "value": "Full-time",
                "snippet": "I am exclusively seeking full-time roles with performance-linked bonus structures.",
            },
            {
                "column_index": 6,
                "field_name": "Acceptable Work Arrangements",
                "field_type": "multiselect",
                "value": ["Hybrid", "On-site", "Remote"],
                "snippet": (
                    "I am flexible and can work remotely, in a hybrid arrangement, or fully "
                    "on-site based on company culture."
                ),
            },
            {
                "column_index": 7,
                "field_name": "Alternate Contact Phone",
                "field_type": "phonenumber",
                "value": "+34-91-555-0204",
                "snippet": "My Madrid office number is +34-91-555-0204, reachable during business hours CET.",
            },
            {
                "column_index": 8,
                "field_name": "Secondary Professional Email",
                "field_type": "email",
                "value": "sofia.reyes.mktg@outlook.com",
                "snippet": "Campaign briefs and interview scheduling may also be sent to sofia.reyes.mktg@outlook.com.",
            },
            {
                "column_index": 9,
                "field_name": "Portfolio Website URL",
                "field_type": "social_profile",
                "value": "https://sofiareyes.marketing",
                "snippet": (
                    "Campaign case studies, growth frameworks, and writing samples are hosted "
                    "at https://sofiareyes.marketing."
                ),
            },
            {
                "column_index": 10,
                "field_name": "Interview Availability Start",
                "field_type": "date_time",
                "value": "2026-05-22T10:00:00",
                "snippet": "I am free for an introductory call from Friday May 22, 2026 at 10:00 AM CET.",
            },
        ],
    },
]


def text_to_pdf_bytes(text: str) -> bytes:
    """Render plain text to an A4 PDF using reportlab."""
    buf    = BytesIO()
    doc    = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    style  = styles["Normal"]
    style.fontName = "Helvetica"
    style.fontSize = 10
    style.leading  = 14

    story = []
    for line in text.split("\n"):
        clean = line.strip()
        if clean:
            story.append(
                Paragraph(
                    clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
                    style,
                )
            )
            story.append(Spacer(1, 2 * mm))
        else:
            story.append(Spacer(1, 4 * mm))

    doc.build(story)
    return buf.getvalue()


def inject_at_random_position(resume_text: str, snippet: str) -> str:
    """Insert a snippet at a random paragraph position (never in the first paragraph)."""
    if not snippet:
        return resume_text
    paragraphs = resume_text.split("\n\n")
    if len(paragraphs) <= 2:
        return resume_text + "\n\n" + snippet
    insert_at = random.randint(1, len(paragraphs))
    paragraphs.insert(insert_at, snippet)
    return "\n\n".join(paragraphs)


def inject_all_snippets(resume_text: str, injected_fields: list) -> str:
    """Shuffle and scatter all field snippets across the resume text."""
    snippets = [f["snippet"].strip() for f in injected_fields if f.get("snippet")]
    if not snippets:
        return resume_text
    random.shuffle(snippets)
    text = resume_text
    for snippet in snippets:
        text = inject_at_random_position(text, snippet)
    return text


def main() -> None:
    random.seed(42)

    ground_truth = []

    for profile in PROFILES:
        rid             = profile["id"]
        resume_text     = profile["resume_text"]
        injected_fields = profile["injected_fields"]

        modified_text = inject_all_snippets(resume_text, injected_fields)
        pdf_bytes     = text_to_pdf_bytes(modified_text)

        pdf_filename = f"{rid}_all_fields.pdf"
        pdf_path     = INJECTED_PDF_DIR / pdf_filename
        pdf_path.write_bytes(pdf_bytes)

        ground_truth.append(
            {
                "resume_id":        rid,
                "pdf_filename":     pdf_filename,
                "modified_text":    modified_text,
                "injected_fields":  injected_fields,
            }
        )

        field_types = ", ".join(f["field_type"] for f in injected_fields)
        print(f"  [{rid}] {pdf_filename}  ({len(injected_fields)} fields: {field_types})")

    GT_FILE.write_text(
        json.dumps(ground_truth, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n[Done] {len(PROFILES)} PDFs written to {INJECTED_PDF_DIR}/")
    print(f"[Done] Ground truth saved to {GT_FILE}")
    print()
    print("Field type coverage:")
    for cf in CUSTOM_FIELDS:
        print(f"  col {cf['column_index']:>2}  {cf['type']:<16}  {cf['name']}")


if __name__ == "__main__":
    print("=== all_field_types_inject.py ===")
    print(f"Output dir : {INJECTED_PDF_DIR.resolve()}\n")
    main()

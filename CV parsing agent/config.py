#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — Central configuration for all pipelines.

All credentials and environment URLs are loaded from the .env file.
Copy .env.example → .env and fill in your values before running any script.
"""

import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

BASE_URL_TRACK_A = os.getenv("TRACK_A_BASE_URL", "")
EMAIL_TRACK_A    = os.getenv("TRACK_A_EMAIL", "")
PASSWORD_TRACK_A = os.getenv("TRACK_A_PASSWORD", "")

BASE_URL_SS   = os.getenv("TRACK_B_BASE_URL", "")
EMAIL_SS      = os.getenv("TRACK_B_EMAIL", "")
PASSWORD_SS   = os.getenv("TRACK_B_PASSWORD", "")

COMPARATOR_BASE_URL = os.getenv("COMPARATOR_BASE_URL", "")
SOVREN_EMAIL        = os.getenv("SOVREN_EMAIL", "")
SOVREN_PASSWORD     = os.getenv("SOVREN_PASSWORD", "")
INHOUSE_EMAIL       = os.getenv("INHOUSE_EMAIL", "")
INHOUSE_PASSWORD    = os.getenv("INHOUSE_PASSWORD", "")

BULK_BASE_URL = os.getenv("BULK_BASE_URL", "")
BULK_EMAIL    = os.getenv("BULK_EMAIL", "")
BULK_PASSWORD = os.getenv("BULK_PASSWORD", "")

NESTED_SS_EMAIL    = os.getenv("NESTED_SS_EMAIL", "")
NESTED_SS_PASSWORD = os.getenv("NESTED_SS_PASSWORD", "")

DEEP_NESTED_SS_EMAIL    = os.getenv("DEEP_NESTED_SS_EMAIL", "")
DEEP_NESTED_SS_PASSWORD = os.getenv("DEEP_NESTED_SS_PASSWORD", "")

ML_SS_BASE_URL_TS2 = os.getenv("ML_SS_BASE_URL_TS2", "")
ML_SS_EMAIL        = os.getenv("ML_SS_EMAIL", "")
ML_SS_PASSWORD     = os.getenv("ML_SS_PASSWORD", "")

ML_HEBREW_SS_BASE_URL = os.getenv("ML_HEBREW_SS_BASE_URL", "")
ML_HEBREW_SS_EMAIL    = os.getenv("ML_HEBREW_SS_EMAIL", "")
ML_HEBREW_SS_PASSWORD = os.getenv("ML_HEBREW_SS_PASSWORD", "")

ML_JAPANESE_TRACK_A_EMAIL    = os.getenv("ML_JAPANESE_TRACK_A_EMAIL", "")
ML_JAPANESE_TRACK_A_PASSWORD = os.getenv("ML_JAPANESE_TRACK_A_PASSWORD", "")
ML_JAPANESE_SS_BASE_URL      = os.getenv("ML_JAPANESE_SS_BASE_URL", "")
ML_JAPANESE_SS_EMAIL         = os.getenv("ML_JAPANESE_SS_EMAIL", "")
ML_JAPANESE_SS_PASSWORD      = os.getenv("ML_JAPANESE_SS_PASSWORD", "")

AI_PARSER_TOKEN = os.getenv("AI_PARSER_TOKEN", "")

EEO_API_BASE_URL  = os.getenv("EEO_API_BASE_URL", "").rstrip("/")
EEO_AUTH_BASE_URL = os.getenv("EEO_AUTH_BASE_URL", "").rstrip("/")
EEO_AUTH_EMAIL    = os.getenv("EEO_AUTH_EMAIL", "")
EEO_AUTH_PASSWORD = os.getenv("EEO_AUTH_PASSWORD", "")

ENTITY_TYPE_ID = 5

import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL_A    = os.getenv("ENV_A_BASE_URL", "")
BASE_URL_B    = os.getenv("ENV_B_BASE_URL", "")
NEPTUNE_URL_A = os.getenv("ENV_A_NEPTUNE_URL", "")
NEPTUNE_URL_B = os.getenv("ENV_B_NEPTUNE_URL", "")

CREDENTIALS_A = {
    "email":    os.getenv("ENV_A_EMAIL", ""),
    "password": os.getenv("ENV_A_PASSWORD", ""),
}

CREDENTIALS_B = {
    "email":    os.getenv("ENV_B_EMAIL", ""),
    "password": os.getenv("ENV_B_PASSWORD", ""),
}

LOGIN_PATH = "/v1/login"

GROQ_API_BASE_URL = "https://api.groq.com/openai/v1"
JUDGE_MODEL       = "openai/gpt-oss-20b"

PASS_THRESHOLD    = 4.0
REVIEW_THRESHOLD  = 3.5

REQUEST_TIMEOUT_SECONDS = 30
INTER_PAIR_DELAY_SECONDS = 1.0

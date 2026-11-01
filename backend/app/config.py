import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "1234")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "TASK-PROJECT")
DATABASE_URL_OVERRIDE = os.getenv("DATABASE_URL")  # only used for testing (e.g. sqlite)

# Formula constants (from the assignment PDF)
T_OPT = 0.3318
INTERCEPT = -2.85
COEF_DD = 2.10
COEF_CP = 0.025
DD_REF_PCT = 0.50     # vertical dashed line  (X = 0.50 %)
CP_REF = 44.0         # horizontal dashed line (Y = 44 cp/mL)

"""All settings come from environment variables (set them in Render).

Locally they can live in a .env file next to README.md (never committed; see .env.example).
Real environment variables win over the file.
"""
import os
from pathlib import Path


def _load_env(path: Path = Path(__file__).resolve().parents[1] / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

SHEETS = {
    "projects": int(os.getenv("SHEET_PROJECTS", "5296291610578820")),
    "forecasts": int(os.getenv("SHEET_FORECASTS", "8457181382004612")),
    "transactions": int(os.getenv("SHEET_TRANSACTIONS", "1918591890050948")),
    "schedule": int(os.getenv("SHEET_SCHEDULE", "995388669775748")),
}
SHEET_FLAGS = int(os.getenv("SHEET_FLAGS", "0") or 0)
SHEET_PIPELINE = int(os.getenv("SHEET_PIPELINE", "0") or 0)       # your start month / split / probability per enquiry
SHEET_ENQUIRIES = int(os.getenv("SHEET_ENQUIRIES", "7290402377781124"))   # 1. Enquiries Pipeline Mastersheet (read only)     # "Flag Log": acknowledged "needs attention" items
NS_ACCOUNT = os.getenv("NS_ACCOUNT", "5142660")
NS_RESTLET_SCRIPT = os.getenv("NS_RESTLET_SCRIPT", "customscript_bhf_render_pdf")
NS_RESTLET_DEPLOY = os.getenv("NS_RESTLET_DEPLOY", "customdeploy_bhf_render_pdf")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "300"))

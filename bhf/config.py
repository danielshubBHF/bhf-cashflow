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
    # 3. BHF Systems / 2. Contracted / 0. Cashflow Database (the former TEST copies, made official 08/10/26;
    # the older sheets are in "0. Cashflow Database (old, Oct 26 - not used)")
    "projects": int(os.getenv("SHEET_PROJECTS", "5467829114720132")),
    "forecasts": int(os.getenv("SHEET_FORECASTS", "5868996240035716")),
    "transactions": int(os.getenv("SHEET_TRANSACTIONS", "2843518197518212")),
    "schedule": int(os.getenv("SHEET_SCHEDULE", "5136386488487812")),
}
SHEET_FLAGS = int(os.getenv("SHEET_FLAGS", "6273607929122692") or 0)
SHEET_PIPELINE = int(os.getenv("SHEET_PIPELINE", "1000909044928388") or 0)       # your start month / split / probability per enquiry
SHEET_ENQUIRIES = int(os.getenv("SHEET_ENQUIRIES", "7290402377781124"))   # 1. Enquiries Pipeline Mastersheet (read only)     # "Flag Log": acknowledged "needs attention" items
GO_LIVE = os.getenv("GO_LIVE", "2026-10-08")   # NetSuite jobs started before this are never flagged as "not set up on the app"
NS_ACCOUNT = os.getenv("NS_ACCOUNT", "5142660")
NS_RESTLET_SCRIPT = os.getenv("NS_RESTLET_SCRIPT", "customscript_bhf_render_pdf")
NS_RESTLET_DEPLOY = os.getenv("NS_RESTLET_DEPLOY", "customdeploy_bhf_render_pdf")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "300"))

"""Sign-in: one shared password (DASHBOARD_PASSWORD) for the team.

With no password set the dashboard is open only in DEMO mode (local preview on fixture data);
anywhere else it stays locked so a missing env var can't publish the figures.
"""
import hmac
import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def _password() -> str:
    return os.getenv("DASHBOARD_PASSWORD") or ""


def _safe_next(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") else "/"


def check(password: str) -> bool:
    pw = _password()
    return bool(pw) and hmac.compare_digest(password.encode(), pw.encode())


def require(request: Request):
    if request.session.get("user"):
        return None
    if not _password() and os.getenv("DEMO") == "1":
        return None
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


LOGO = (Path(__file__).resolve().parents[1] / "static" / "logo-datauri.txt").read_text(encoding="utf-8").strip()
PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&display=swap" rel=stylesheet>
<link rel=stylesheet href=/static/style.css><title>Sign in · Project Cashflow</title></head><body class=login>
<form method=post action=/login><img class=brandlogo src="{logo}" alt="BHF Technologies"><h1>Project Cashflow</h1>{msg}
<label>Password<input type=password name=password autofocus></label><button>Sign in</button></form></body></html>"""


@router.get("/login", response_class=HTMLResponse)
def login(request: Request, next: str = "/", failed: int = 0):
    request.session["next"] = _safe_next(next)
    if not _password():
        return HTMLResponse(PAGE.format(logo=LOGO, msg="<p>Sign-in isn't set up: DASHBOARD_PASSWORD is missing.</p>"), 503)
    return HTMLResponse(PAGE.format(logo=LOGO, msg="<p class=neg>Wrong password.</p>" if failed else ""))


@router.post("/login")
def login_pw(request: Request, password: str = Form(...)):
    if check(password):
        request.session["user"] = "BHF"
        return RedirectResponse(_safe_next(request.session.pop("next", "/")), status_code=303)
    return RedirectResponse("/login?failed=1", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

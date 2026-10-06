"""Dashboard (Render web service). Overview tab + one tab per live project."""
import datetime as dt
import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, editor, model
from .store import Store

log = logging.getLogger("web")

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="BHF Project Cashflow")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "change-me"), max_age=60 * 60 * 12)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
tpl = Jinja2Templates(directory=ROOT / "templates")
_has = lambda v: isinstance(v, (int, float, str)) and v != ""                            # blank / missing cell
tpl.env.filters["money"] = lambda v: ("−$" if v < -0.5 else "$") + f"{abs(v):,.0f}"
tpl.env.filters["k"] = lambda v: ("−" if v < 0 else "") + (f"${abs(v)/1e6:,.2f}M" if abs(v) >= 1e6 else f"${abs(v)/1e3:,.0f}k")
tpl.env.filters["pct"] = lambda v: f"{v*100:.1f}%"
tpl.env.filters["amt"] = lambda v: f"{model.num(v):,.2f}" if _has(v) else ""           # editable money
tpl.env.filters["pctin"] = lambda v: f"{model.num(v) * 100:g}" if _has(v) else ""        # 0.35 -> 35
tpl.env.filters["signed"] = lambda v: ("+" if v > 0.5 else "") + tpl.env.filters["money"](v)      # +$1,200 / −$1,200
tpl.env.filters["share"] = lambda a, b: f"{a / b * 100:.0f}%" if b else "–"                         # 412000|share(1.6e6)
tpl.env.filters["mdate"] = lambda v: (f"{int(str(v)[8:10])} {model.MONTHS[int(str(v)[5:7]) - 1]} {str(v)[2:4]}"
                                     if len(str(v or "")) >= 10 and str(v)[4] == "-" else str(v or ""))  # 2026-05-21 -> 21 May 26
PAL = ["#0E2A47", "#00A4C7", "#4bb8d4", "#7a93a6", "#128a55", "#c98a1f", "#9fb2c0", "#5566a0", "#c05a45", "#3aa0b8", "#8aa0b2"]


def pie(values) -> str:
    """CSS conic-gradient for a pie (the house dashboard's pie, server-side so it needs no JS)."""
    tot, deg, seg = sum(v for v in values if v > 0) or 1, 0.0, []
    for i, v in enumerate(x for x in values if x > 0):
        d = v / tot * 360
        seg.append(f"{PAL[i % len(PAL)]} {deg:.2f}deg {deg + d:.2f}deg")
        deg += d
    return f"conic-gradient({','.join(seg)})" if seg else "#eef2f6"


LOGO = (ROOT / "static" / "logo-datauri.txt").read_text(encoding="utf-8").strip()      # BHF logo, inlined once
tpl.env.globals.update(fy_start=model.fy_start, norm=model.norm, cost_types=editor.COST_TYPES, pal=PAL, pie=pie, logo=LOGO,
                        today=lambda: dt.date.today().isoformat())
app.include_router(auth.router)

store = Store()


def load(force=False):
    return store.load(force)


def last_synced(txns) -> str:
    """Latest 'Synced' stamp on Transactions (the sync writes dd/mm/yy HH:MM)."""
    def when(v):
        for f in ("%d/%m/%y %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(str(v).strip()[:19], f)
            except ValueError:
                pass
        return None
    stamps = [d for d in (when(t.get("Synced")) for t in txns if t.get("Synced")) if d]
    return max(stamps).strftime("%d %b %Y %H:%M") if stamps else ""


def ctx(request, **kw):
    projects, forecasts, txns, sched = load()
    pf = model.portfolio(projects, forecasts, txns, sched)
    synced = last_synced(txns)
    return {"request": request, "pf": pf, "tabs": [(v["code"], v["name"]) for v in pf["views"]],
            "user": request.session.get("user"), "demo": store.demo,
            "as_at": "demo data" if store.demo else (synced or "not synced yet"), **kw}


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    if (r := auth.require(request)):
        return r
    return tpl.TemplateResponse(request, "overview.html", ctx(request, active="overview"))


@app.get("/p/{code}", response_class=HTMLResponse)
def project(request: Request, code: str):
    if (r := auth.require(request)):
        return r
    c = ctx(request, active=code)
    v = next((v for v in c["pf"]["views"] if v["code"] == code), None)
    if not v:
        return RedirectResponse("/")
    projects, forecasts, *_ = load()
    c["v"] = v
    c["forecast_items"] = [f["Item"] for f in forecasts if f.get("Project") == code]
    c["err"] = request.query_params.get("err")
    return tpl.TemplateResponse(request, "project.html", c)


@app.post("/link")
def link(request: Request, code: str = Form(...), netsuite_id: str = Form(...), forecast: str = Form(...)):
    """Link an unassigned transaction to a forecast line (writes to Smartsheet)."""
    if (r := auth.require(request)):
        return r
    return _done(request, code, _safely(store.link, code, netsuite_id, forecast), "costs")


def _safely(fn, *args) -> str | None:
    try:
        return fn(*args)
    except Exception as e:                       # Smartsheet down / rejected: nothing in the cache changed
        log.exception("save failed")
        return f"Couldn't save to Smartsheet ({type(e).__name__}). Nothing was changed; try again."


def _done(request: Request, code: str, err: str | None, anchor: str):
    """Fetch saves get JSON (the page keeps what was typed on an error); plain form posts get a redirect."""
    if request.headers.get("x-fetch"):
        return JSONResponse({"ok": not err, "error": err}, status_code=400 if err else 200)
    return RedirectResponse(f"/p/{code}" + (f"?err={quote(err)}" if err else "") + f"#{anchor}", status_code=303)


def _known(code: str) -> bool:
    return any(p.get("Project") == code for p in load()[0])


@app.post("/p/{code}/forecast")
async def save_forecast(request: Request, code: str):
    """Add or edit a forecast line."""
    if (r := auth.require(request)):
        return r
    form = await request.form()
    values, err = editor.forecast(form)
    if not err:
        err = "Unknown project." if not _known(code) else _safely(store.save_forecast, code, form.get("id"), values)
    return _done(request, code, err, "forecast")


@app.post("/p/{code}/milestone")
async def save_milestone(request: Request, code: str):
    """Add or edit a payment milestone (customer or supplier)."""
    if (r := auth.require(request)):
        return r
    form = await request.form()
    if form.get("delete"):
        return _done(request, code, _safely(store.delete_milestone, code, form.get("id")), form.get("anchor") or "payments")
    values, err = editor.milestone(form)
    if not err:
        err = "Unknown project." if not _known(code) else _safely(
            store.save_milestone, code, form.get("id"), values, form.get("po") or "", form.get("item") or "")
    return _done(request, code, err, form.get("anchor") or "payments")


@app.get("/pdf/{netsuite_id}")
def pdf(request: Request, netsuite_id: str):
    """Open the PO / bill PDF attached to a Transactions row (Smartsheet gives a short-lived URL)."""
    if (r := auth.require(request)):
        return r
    _, _, txns, _ = load()
    t = next((x for x in txns if str(x.get("NetSuite ID")) == netsuite_id and x.get("_id")), None)
    if os.getenv("DEMO") == "1" or not t:
        return HTMLResponse("No PDF attached to that transaction.", 404)
    from .smartsheet_db import Table
    T = Table(config.SHEETS["transactions"])
    atts = T.attachments(t["_id"])
    att = next((a for a in atts if a.get("name") == t.get("PDF")), atts[0] if atts else None)
    if not att:
        return HTMLResponse("No PDF attached to that transaction.", 404)
    return RedirectResponse(T.attachment_url(att["id"]))


@app.get("/refresh")
def refresh(request: Request):
    if (r := auth.require(request)):
        return r
    load(force=True)
    return RedirectResponse(request.headers.get("referer", "/"))


@app.get("/healthz")
def health():
    return {"ok": True}

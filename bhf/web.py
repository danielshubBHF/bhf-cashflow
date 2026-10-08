"""Dashboard (Render web service). Overview tab + one tab per live project."""
import datetime as dt
import logging
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, editor, model, pl
from .store import Store

log = logging.getLogger("web")

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="BHF Project Cashflow")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "change-me"), max_age=60 * 60 * 12)
app.add_middleware(GZipMiddleware, minimum_size=1000)            # pages are mostly repeated markup: ~8x smaller


@app.middleware("http")
async def cache_static(request: Request, call_next):
    """Versioned static files (?v=…) never change: let the browser keep them instead of asking every page."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/") and "v" in request.query_params:
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.on_event("startup")
def warm():
    """Load Smartsheet and NetSuite as soon as the server starts, so the first visitor doesn't wait for it."""
    if not store.demo:
        threading.Thread(target=lambda: (store.load(), store.budget()), daemon=True).start()
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
ASSET_V = "2610w"          # bump when static/*.css or *.js change, so browsers fetch the new file
tpl.env.globals.update(asset_v=ASSET_V, fy_start=model.fy_start, norm=model.norm, cost_types=editor.COST_TYPES, pal=PAL, pie=pie, logo=LOGO,
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
    b0 = store.budget()
    if b0 and b0.get("untagged"):
        for code, items in model.untagged_flags(pf["views"] + pf["complete"], projects, b0["untagged"], b0.get("quotes")).items():
            v = next((x for x in pf["views"] + pf["complete"] if x["code"] == code), None)
            for text, key in items:
                v["flags"].append(("untagged", text, None))
                v["flag_meta"].append({"key": f"untagged:{key}", "kind": "untagged", "text": text, "amount": None, "po": None})
    pf["totals"]["flags"] = model.apply_acknowledgements(pf["views"], store.flag_log())
    synced = last_synced(txns)
    today = dt.date.today().isoformat()
    b = store.budget()
    rows = {p.get("Project"): p for p in projects}
    pls = {v["code"]: pl.project_pl(rows[v["code"]], v, today, ((b or {}).get("pl_actuals") or {}).get(v["code"]) if b else None)
           for v in pf["views"]}
    for code, x in pls.items():                    # when month-end flattening last ran
        x["last_journal"] = model.nsdate(((b or {}).get("journals") or {}).get(code))
        x["journal_behind"] = bool(x["last_journal"]) and x["last_journal"][:7] < pl.add(today[:7], -1)   # last month's not run
    fy = model.fy_of(today[:7])
    by_month = {}
    for x in pls.values():
        for r in x["rows"]:
            name = f"{model.MONTHS[int(r['month'][5:7]) - 1]} {r['month'][:4]}"
            by_month[name] = by_month.get(name, 0.0) + r["rev"]
    bv = model.budget_view(b, pf["views"], today, {k: pl.fy_remaining(x, fy, today) for k, x in pls.items()}, by_month)
    return {"request": request, "pf": pf, "bv": bv, "pls": pls, "tabs": [(v["code"], v["name"]) for v in pf["views"]], "n_complete": len(pf["complete"]),
            "user": request.session.get("user"), "demo": store.demo,
            "as_at": "demo data" if store.demo else (synced or "not synced yet"), **kw}


def pipeline_view(c) -> dict | None:
    from . import pipeline
    try:
        enq, pipe = store.pipeline()
    except Exception:
        log.exception("pipeline")
        return None
    today = dt.date.today().isoformat()
    pv = pipeline.view(enq, pipe, today, model.fy_of(today[:7]))
    if c.get("bv"):
        bv = c["bv"]
        bv["pipeline_w"] = pv["this_w"]
        bv["pipeline_n"] = sum(1 for r in pv["rows"] if r["group"] == "counted")
        bv["gap_after"] = round(bv["gap"] - pv["this_w"], 2)
        for r in bv["rows"]:                     # "Oct 2026" -> the pipeline's "2026-10"
            key = f"{r['month'][-4:]}-{model.MONTHS.index(r['month'][:3]) + 1:02d}"
            r["pipeline"] = pv["by_month"].get(key, 0.0)
    return pv


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    if (r := auth.require(request)):
        return r
    c = ctx(request, active="overview")
    c["pv"] = pipeline_view(c)
    c["err"] = request.query_params.get("err")
    return tpl.TemplateResponse(request, "overview.html", c)


@app.post("/pipeline/{row_id}")
async def save_pipeline(request: Request, row_id: str):
    """The PM's start month / stages / probability / value for one enquiry, or exclude it."""
    if (r := auth.require(request)):
        return r
    from . import pl
    form = await request.form()
    if form.get("hide") in ("0", "1"):                  # Hide / Unhide only: leave the other settings alone
        err = _safely(store.save_pipeline, row_id, str(form.get("name") or ""), {"Exclude": form.get("hide") == "1"})
        return RedirectResponse("/?" + (f"err={quote(err)}&" if err else "") + "tab=pipeline#pipeline", status_code=303)
    start, stages = str(form.get("start") or "").strip(), str(form.get("stages") or "").strip()
    prob, value = str(form.get("prob") or "").strip(), str(form.get("value") or "").strip()
    total = str(form.get("total") or "").strip()
    if total.isdigit() and 5 <= int(total) <= 120:
        stages = "/".join(map(str, pl.timing(stages, total, [int(x) for x in stages.split("/") if x.isdigit()] if total else None)))
    err = None
    if start and not pl.ym(start):
        err = "Start month must look like 2026-11."
    elif stages and pl.parse_months(stages) != [int(x) for x in re.split(r"[/,]", stages) if x.strip().isdigit()]:
        err = "Months per stage must be five whole numbers, e.g. 2/2/3/2/2."
    elif prob and not re.fullmatch(r"\d+(\.\d+)?%?", prob):
        err = "Probability must be a percentage, e.g. 50."
    elif value and not re.fullmatch(r"\$?[\d,]+(\.\d+)?", value):
        err = "Value must be a number."
    if not err:
        err = _safely(store.save_pipeline, row_id, str(form.get("name") or ""), {
            "Start": f"{pl.ym(start)}-01" if start else None, "Stages": stages or None,
            "Probability %": float(prob.rstrip("%")) if prob else None,
            "Value": float(value.replace("$", "").replace(",", "")) if value else None})
    url = "/?" + (f"err={quote(err)}&" if err else "") + "tab=pipeline#pipeline"
    return RedirectResponse(url, status_code=303)


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
    c["pl"] = c["pls"][code]
    c["forecast_items"] = [f["Item"] for f in forecasts if f.get("Project") == code]
    c["err"] = request.query_params.get("err")
    return tpl.TemplateResponse(request, "project.html", c)


def completed_rows(c) -> list[dict]:
    projects, _, txns, _ = load()
    rows = {p.get("Project"): p for p in projects}
    return [model.completed_view(v, rows[v["code"]], txns) for v in c["pf"]["complete"]]


@app.get("/completed", response_class=HTMLResponse)
def completed(request: Request):
    """Finished projects (Status = Complete): what each made, from NetSuite documents."""
    if (r := auth.require(request)):
        return r
    c = ctx(request, active="completed")
    c["rows"] = sorted(completed_rows(c), key=lambda r: r["last"] or "", reverse=True)
    c["tot"] = model.completed_totals(c["rows"])
    return tpl.TemplateResponse(request, "completed.html", c)


@app.get("/completed/{code}", response_class=HTMLResponse)
def completed_one(request: Request, code: str):
    if (r := auth.require(request)):
        return r
    c = ctx(request, active="completed")
    c["x"] = next((x for x in completed_rows(c) if x["code"] == code), None)
    if not c["x"]:
        return RedirectResponse("/completed")
    return tpl.TemplateResponse(request, "completed_one.html", c)


def load_pl(code: str) -> dict | None:
    """The project's current P&L timing (to tell a new total timeline from an unchanged one)."""
    try:
        projects, forecasts, txns, sched = load()
        p = next(x for x in projects if x.get("Project") == code)
        v = model.project_view(p, forecasts, txns, sched)
        return pl.project_pl(p, v, dt.date.today().isoformat(), None)
    except Exception:
        return None


@app.post("/p/{code}/pl")
async def save_pl(request: Request, code: str):
    """The PM's P&L timing: start month and months per stage (e.g. 2/2/3/2/2)."""
    if (r := auth.require(request)):
        return r
    form = await request.form()
    start, stages = str(form.get("start") or "").strip(), str(form.get("stages") or "").strip()
    total = str(form.get("total") or "").strip()
    err = None
    cur = (load_pl(code) or {}).get("months")
    if start and not pl.ym(start):
        err = "Start month must look like 2026-04."
    elif total and (not total.isdigit() or not 5 <= int(total) <= 120):
        err = "Total timeline must be a whole number of months, 5 or more."
    elif stages and not total and pl.parse_months(stages) != [int(x) for x in stages.replace(",", "/").split("/") if x.strip().isdigit()]:
        err = "Months per stage must be five whole numbers, e.g. 2/2/3/2/2."
    if not err:
        months = pl.timing(stages, total, cur)
        err = _safely(store.save_project, code, {"P&L Start": f"{pl.ym(start)}-01" if start else None,
                                                 "P&L Stages": "/".join(map(str, months)) if months else None})
    return _done(request, code, err, "pl")


@app.post("/p/{code}/pl/lock")
def lock_pl(request: Request, code: str):
    """Lock today's timing, revenue and cost as the baseline (or replace the baseline)."""
    if (r := auth.require(request)):
        return r
    c = ctx(request, active=code)
    x = c["pls"].get(code)
    if not x:
        return _done(request, code, "Unknown project.", "pl")
    err = _safely(store.save_project, code, {
        "P&L Locked": dt.date.today().isoformat(), "P&L Locked Start": x["start"] + "-01",
        "P&L Locked Stages": "/".join(map(str, x["months"])), "P&L Locked Revenue": x["rev_main"], "P&L Locked Cost": x["cost_total"]})
    return _done(request, code, err, "pl")


@app.get("/p/{code}/pl.csv")
def pl_csv(request: Request, code: str):
    """The project's P&L schedule as a spreadsheet (opens in Excel)."""
    if (r := auth.require(request)):
        return r
    import csv, io
    from fastapi.responses import Response
    x = ctx(request, active=code)["pls"].get(code)
    if not x:
        return RedirectResponse("/")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Month", "FY", "Stage", "Revenue (formula)", "Revenue (locked baseline)", "Revenue (NetSuite actual)",
                "Revenue difference", "Cost (formula)", "Cost (locked baseline)", "Cost (NetSuite actual)", "Cost difference"])
    names = [s["name"] for s in x["stages"]]
    for r in x["rows"]:
        w.writerow([r["label"], r["fy"], names[r["stage"]] if r["stage"] is not None else "", r["rev"], r["rev_base"], r["rev_act"],
                    "" if r["rev_diff"] is None else r["rev_diff"], r["cost"], r["cost_base"], r["cost_act"],
                    "" if r["cost_diff"] is None else r["cost_diff"]])
    for f in x["fys"]:
        w.writerow([f"{f['fy']} total", f["fy"], "", f["rev"], f["rev_base"], f["rev_act"], "", f["cost"], f["cost_base"], f["cost_act"], ""])
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{code} P&L schedule.csv"'})


@app.post("/p/{code}/flag")
async def ack_flag(request: Request, code: str):
    """Acknowledge a "needs attention" item (optional reason), or restore one (restore=1)."""
    if (r := auth.require(request)):
        return r
    form = await request.form()
    key = str(form.get("key") or "")
    if not key:
        return _done(request, code, "Which item?", "attention")
    if form.get("restore"):
        err = _safely(store.restore_flag, code, key)
    else:
        err = _safely(store.ack_flag, code, key, str(form.get("kind") or ""), str(form.get("text") or ""),
                      form.get("amount"), str(form.get("reason") or ""))
    return _done(request, code, err, "attention")


@app.post("/p/{code}/terms")
async def set_terms(request: Request, code: str):
    """From a "payment terms need checking" item: confirm the PO's milestones, or replace them with a typed split."""
    if (r := auth.require(request)):
        return r
    form = await request.form()
    po = str(form.get("po") or "")
    if not po:
        return _done(request, code, "Which PO?", "attention")
    err = _safely(store.apply_terms, code, po, str(form.get("split") or ""), bool(form.get("confirm")))
    return _done(request, code, err, "attention")


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
    same = sorted([a for a in atts if a.get("name") == t.get("PDF")], key=lambda a: str(a.get("createdAt") or ""))
    att = same[-1] if same else (atts[0] if atts else None)       # a re-rendered PDF keeps its name: newest wins
    if not att:
        return HTMLResponse("No PDF attached to that transaction.", 404)
    return RedirectResponse(T.attachment_url(att["id"]))


@app.get("/refresh")
def refresh(request: Request):
    if (r := auth.require(request)):
        return r
    if not store.demo:
        # Edits made on the projects' LIVE Smartsheet sheets come in now; the sheets are rebuilt in the background.
        from . import project_sheets
        found = project_sheets.safe_pull()
        threading.Thread(target=project_sheets.run, kwargs={"problems": found}, daemon=True).start()
    load(force=True)
    store.budget(force=True)
    return RedirectResponse(request.headers.get("referer", "/"))


@app.get("/healthz")
def health():
    return {"ok": True}

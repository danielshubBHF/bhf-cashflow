"""Readable Smartsheet views, written by the sync.

1. A "LIVE" cashflow sheet in each live project's Smartsheet folder (3. BHF Systems / 2. Contracted / {code} ...),
   laid out like the old hand-kept cashflow sheets: a summary, then money in and money out, each forecast line
   with its payments underneath. Rebuilt on every sync, so nobody edits it; the old sheets are left alone.
2. Shading on the four database sheets: project header rows navy, money-in rows green, money-out rows red.

Run:  python -m bhf.project_sheets              (both)
      python -m bhf.project_sheets --project BHF26003 --dry-run
"""
import argparse
import datetime as dt
import logging
import os

import requests

from . import config, model
from .smartsheet_db import API, Table

log = logging.getLogger("project_sheets")
CONTRACTED = int(os.getenv("SS_CONTRACTED_FOLDER", "1654885033764740"))     # 3. BHF Systems / 2. Contracted
SUFFIX = "Cashflow - LIVE"

# Smartsheet format descriptor: fontFamily, fontSize, bold, italic, underline, strikethrough, horizontalAlign,
# verticalAlign, color, backgroundColor, taskbarColor, currency, decimalCount, thousandsSeparator, numberFormat,
# textWrap, dateFormat. Colours are indexes into Smartsheet's palette (GET /serverinfo).
WHITE, NAVY, GREY_TXT, RED_TXT = 2, 39, 34, 35
GREEN_BG, GREEN_BG2, RED_BG, RED_BG2, GREY_BG, BLUE_BG = 7, 14, 4, 11, 18, 8


def fmt(bold=False, color=0, bg=0, money=False, size=None, italic=False) -> str:
    f = [""] * 17
    if size is not None:
        f[1] = str(size)
    if bold:
        f[2] = "1"
    if italic:
        f[3] = "1"
    if color:
        f[8] = str(color)
    if bg:
        f[9] = str(bg)
    if money:
        f[11], f[12], f[13], f[14] = "2", "0", "1", "2"       # AUD, no cents, thousands separator, currency
    return ",".join(f)


COLUMNS = [("Item", "TEXT_NUMBER", 330, True), ("Party", "TEXT_NUMBER", 190, False), ("Status", "TEXT_NUMBER", 120, False),
           ("Ref", "TEXT_NUMBER", 140, False), ("Date", "DATE", 100, False), ("Done", "TEXT_NUMBER", 115, False),
           ("To come", "TEXT_NUMBER", 115, False), ("Note", "TEXT_NUMBER", 260, False)]
MONEY_COLS = ("Done", "To come")
KIND = {"in": {"paid": "Received", "billed": "Invoiced, not paid", "order": "To invoice", "forecast": "Forecast"},
        "out": {"paid": "Paid", "billed": "Billed, not paid", "order": "On order", "forecast": "Forecast"}}
LINE_STATUS = dict(model.STATUS)


# ---------------------------------------------------------------- what goes on the sheet
def layout(v: dict, as_at: str) -> list[dict]:
    """Rows as {"cells": {...}, "fmt": ..., "children": [...]}: three sections, lines under them, payments under lines."""
    q, c = v["position"], v["cost"]
    summary = [
        ("Contract value", v["contract"], None, f"{money(v['contract_orig'])} original + {money(v['variations'])} variations"),
        ("Received to date", q["in_done"], None, f"{money(q['in_fy'])} this FY"),
        ("Paid to date", q["out_done"], None, f"{money(q['out_fy'])} this FY"),
        ("Cash now (received − paid)", q["now"], None, "bold"),
        ("Still to receive", None, q["in_tocome"], f"{money(q['in_overdue'])} overdue" if q["in_overdue"] >= 0.5 else ""),
        ("Still to pay", None, q["out_tocome"], f"{money(q['out_overdue'])} overdue" if q["out_overdue"] >= 0.5 else ""),
        ("Final position (cash now + to receive − to pay)", None, q["final"], "bold"),
        ("Expected cost", None, c["expected"], f"budget {money(c['budget'])}" + (f", FX {money(c['fx'])}" if abs(c["fx"]) >= 0.5 else "")),
        ("Gross margin", None, v["gm"], f"{v['gm_pct'] * 100:.1f}% of contract"),
        ("BHF labour", None, v["labour"], ""),
        ("Net margin after labour", None, v["nm"], f"{v['nm_pct'] * 100:.1f}% of contract"),
    ]
    rows = [{"cells": {"Item": f"Summary · {v['code']} {v['name']}", "Note": f"Data as at {as_at}"},
             "fmt": fmt(True, WHITE, NAVY, size=3),
             "children": [{"cells": {"Item": item, "Done": done, "To come": tocome, "Note": "" if note == "bold" else note},
                           "fmt": fmt(bold=note == "bold", money=True)} for item, done, tocome, note in summary]}]
    for side, title, bg, bg2 in (("in", "Money in · customer", GREEN_BG2, GREEN_BG), ("out", "Money out · costs", RED_BG2, RED_BG)):
        groups = v["ledger_lines"][side]
        sec = {"cells": {"Item": title, "Done": round(sum(g["done"] for g in groups), 2),
                         "To come": round(sum(g["tocome"] for g in groups), 2)},
               "fmt": fmt(True, 0, bg, money=True, size=3), "children": []}
        for g in groups:
            l = g["line"]
            note = []
            if l.get("unassigned"):
                note.append("not linked to a forecast line: link it in the app")
            if g["overdue"]:
                note.append("overdue")
            if l["direction"] == "Out" and l["overrun"] > 0.5:
                note.append(f"{money(l['overrun'])} over forecast")
            line = {"cells": {"Item": l["item"], "Party": l["party"], "Status": LINE_STATUS.get(l["status"], ""),
                              "Ref": ", ".join(l["refs"]), "Date": g["next"], "Done": g["done"], "To come": g["tocome"],
                              "Note": "; ".join(note)},
                    "fmt": fmt(True, 0, bg2, money=True), "children": []}
            for r in g["rows"]:
                doc = r.get("doc") or {}
                ref, label = doc.get("Doc #") or "", r["label"]
                if doc and ref == doc.get("Type"):                  # a bill entered without a supplier invoice number
                    ref, label = "", f"{doc.get('Type')} (no number)"
                line["children"].append({"cells": {
                    "Item": label, "Party": l["party"] if side == "in" else r["party"],   # invoices name the job, not the customer
                    "Status": KIND[side].get(r["kind"], r["kind"]), "Ref": ref, "Date": r["date"],
                    "Done": r["amount"] if r["done"] else None, "To come": None if r["done"] else r["amount"],
                    "Note": "overdue" if r["overdue"] else ""},
                    "fmt": fmt(color=RED_TXT if r["overdue"] else (GREY_TXT if r["done"] else 0), money=True)})
            sec["children"].append(line)
        rows.append(sec)
    rows.append({"cells": {"Item": "Built automatically by the cashflow sync (7am and 1pm weekdays) from NetSuite and the Cashflow "
                                   "Database. Don't edit here: changes are overwritten. Edit forecasts in the cashflow app."},
                 "fmt": fmt(color=GREY_TXT, italic=True), "children": []})
    return rows


def plain(f: str) -> str:
    """The same format without the currency parts (for the row; the amount cells keep them)."""
    parts = f.split(",")
    parts[11:15] = ["", "", "", ""]
    return ",".join(parts)


def money(x) -> str:
    x = model.num(x)
    return ("−$" if x < -0.5 else "$") + f"{abs(x):,.0f}"


# ---------------------------------------------------------------- Smartsheet plumbing
class Sheets:
    def __init__(self):
        self.h = {"Authorization": f"Bearer {os.environ['SMARTSHEET_TOKEN']}"}

    def get(self, path, **params):
        r = requests.get(f"{API}{path}", headers=self.h, params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def project_folder(self, code: str) -> dict | None:
        return next((f for f in self.get(f"/folders/{CONTRACTED}").get("folders", [])
                     if f["name"].upper().startswith(code.upper())), None)

    def find_or_create(self, folder_id: int, name: str) -> int:
        sheets = self.get(f"/folders/{folder_id}").get("sheets", [])
        hit = next((s for s in sheets if s["name"] == name), None) or next((s for s in sheets if s["name"].endswith(SUFFIX)), None)
        if hit:
            if hit["name"] != name:
                requests.put(f"{API}/sheets/{hit['id']}", headers=self.h, json={"name": name}, timeout=30).raise_for_status()
            return hit["id"]
        body = {"name": name, "columns": [{"title": t, "type": ty, "width": w, **({"primary": True} if p else {})}
                                          for t, ty, w, p in COLUMNS]}
        r = requests.post(f"{API}/folders/{folder_id}/sheets", headers=self.h, json=body, timeout=60)
        r.raise_for_status()
        log.info("created sheet %s", name)
        return r.json()["result"]["id"]


def write(sheet_id: int, rows: list[dict]):
    """Replace everything on the sheet with `rows` (sections, then their children, then grandchildren)."""
    t = Table(sheet_id)
    old = t.load()
    for i in range(0, len(old), 300):
        t.delete([r["_id"] for r in old[i:i + 300]])

    def cells(r):
        out = []
        for k, val in r["cells"].items():
            if k not in t.col or val in (None, ""):
                continue
            c = {"columnId": t.col[k], "value": round(val, 2) if isinstance(val, float) else val}
            if k in MONEY_COLS:
                c["format"] = r["fmt"]                                   # $ format only on the amount columns
            out.append(c)
        return out

    def add(batch, parent=None):
        body = [{"toBottom": True, **({"parentId": parent} if parent else {}), "format": plain(r["fmt"]), "cells": cells(r)}
                for r in batch]
        ids = []
        for i in range(0, len(body), 200):
            res = requests.post(f"{API}/sheets/{sheet_id}/rows", headers=t.h, json=body[i:i + 200], timeout=60)
            res.raise_for_status()
            ids += [x["id"] for x in res.json()["result"]]
        return ids

    level = [(None, rows)]
    while level:
        nxt = []
        for parent, batch in level:
            if batch:
                for rid, r in zip(add(batch, parent), batch):
                    if r.get("children"):
                        nxt.append((rid, r["children"]))
        level = nxt


def publish(only: str | None = None, dry: bool = False) -> list[str]:
    """Write the LIVE cashflow sheet for every live project (or one)."""
    P, F, T, S = (Table(config.SHEETS[k]).load() for k in ("projects", "forecasts", "transactions", "schedule"))
    today = dt.date.today().isoformat()
    as_at = dt.datetime.now().strftime("%d %b %Y %H:%M")
    ss, done = Sheets(), []
    for p in P:
        code = p.get("Project")
        if p.get("Status") in model.LIVE_OUT or (only and code != only):
            continue
        v = model.project_view(p, F, T, S, today)
        rows = layout(v, as_at)
        folder = ss.project_folder(code)
        if not folder:
            log.warning("%s: no folder under 2. Contracted starting with the code; skipped", code)
            continue
        name = f"3. {code} {SUFFIX}"                     # Smartsheet allows 50 characters; the folder names the job
        if dry:
            n = sum(1 + len(c["children"]) + sum(len(g.get("children", [])) for g in c["children"]) for c in rows)
            log.info("%s: would write %d rows to '%s' in folder %s", code, n, name, folder["name"])
        else:
            write(ss.find_or_create(folder["id"], name), rows)
            log.info("%s: wrote '%s'", code, name)
        done.append(code)
    return done


# ---------------------------------------------------------------- shading on the database sheets
def wanted_format(name: str, r: dict) -> str | None:
    if name == "forecasts" and not r.get("Project") and str(r.get("Item") or "").upper().startswith("BHF"):
        return fmt(True, WHITE, NAVY)                                         # project header row
    if name == "projects":
        return fmt(color=GREY_TXT) if r.get("Status") in model.LIVE_OUT else fmt(True, 0, BLUE_BG)
    d = r.get("Direction")
    if d == "In":
        return fmt(bg=GREEN_BG)
    if d == "Out":
        return fmt(bg=RED_BG)
    return None


def style(dry: bool = False) -> int:
    """Shade the database sheets' rows. Only rows whose format differs are touched, so it's cheap to repeat."""
    n = 0
    for name in ("projects", "forecasts", "transactions", "schedule"):
        sid = config.SHEETS[name]
        h = {"Authorization": f"Bearer {os.environ['SMARTSHEET_TOKEN']}"}
        res = requests.get(f"{API}/sheets/{sid}", headers=h, params={"include": "format"}, timeout=90)
        res.raise_for_status()
        s = res.json()
        title = {c["id"]: c["title"] for c in s["columns"]}
        todo = []
        for row in s.get("rows", []):
            r = {title[c["columnId"]]: c.get("value") for c in row["cells"]}
            want = wanted_format(name, r)
            if want and (row.get("format") or "") != want:
                todo.append({"id": row["id"], "format": want})
        n += len(todo)
        log.info("%s: %d rows to shade", name, len(todo))
        if not dry:
            for i in range(0, len(todo), 200):
                requests.put(f"{API}/sheets/{sid}/rows", headers=h, json=todo[i:i + 200], timeout=60).raise_for_status()
    return n


def run(dry: bool = False, only: str | None = None):
    for step in (lambda: style(dry), lambda: publish(only, dry)):
        try:
            step()
        except Exception:                        # never fail the sync over a view
            log.exception("project sheets step failed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--no-style", action="store_true")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    a = ap.parse_args()
    if a.no_style:
        publish(a.project, a.dry_run)
    else:
        run(a.dry_run, a.project)

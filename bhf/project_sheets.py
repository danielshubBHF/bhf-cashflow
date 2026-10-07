"""Readable Smartsheet views, written by the sync.

1. A "LIVE" cashflow sheet in each live project's Smartsheet folder (3. BHF Systems / 2. Contracted / {code} ...),
   laid out like the old hand-kept cashflow sheets: a summary, then money in and money out, each forecast line
   with its payments underneath. Rebuilt on every sync; the old sheets are left alone.
   Two-way: forecast lines can be edited on the sheet (item, party, type, PO #, expected date, forecast $, closed) and
   new lines added under Money in / Money out. pull() writes those into the Forecasts sheet before the rebuild. A hidden
   "Was" column holds what was written last time, so only cells changed in Smartsheet are applied (an edit made on the
   website in between is never overwritten by the sheet's stale copy).
2. Shading on the four database sheets: project header rows navy, money-in rows green, money-out rows red.

Run:  python -m bhf.project_sheets              (both)
      python -m bhf.project_sheets --project BHF26003 --dry-run
"""
import argparse
import datetime as dt
import json
import logging
import os
import re

import requests

from . import config, model
from .smartsheet_db import API, Table

log = logging.getLogger("project_sheets")
CONTRACTED = int(os.getenv("SS_CONTRACTED_FOLDER", "1654885033764740"))     # 3. BHF Systems / 2. Contracted
SUFFIX = "Cashflow"              # sheets are "3. {code} {project name} Cashflow"; ours carry a hidden "Was" column
MARK = "Was"


def sheet_name(code: str, name: str) -> str:
    """'3. BHF26001 Stacked Farm DAF UF RO 8 Cashflow', the project name trimmed (at a word) to Smartsheet's 50 characters."""
    words, keep = (name or "").split(), []
    for w in words:
        if len(f"3. {code} {' '.join(keep + [w])} {SUFFIX}") > 50:
            break
        keep.append(w)
    return " ".join(["3.", code, *keep, SUFFIX])

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


COLUMNS = [("Item", "TEXT_NUMBER", 330, True), ("Party", "TEXT_NUMBER", 190, False), ("Type", "TEXT_NUMBER", 140, False),
           ("Status", "TEXT_NUMBER", 120, False), ("Ref", "TEXT_NUMBER", 140, False), ("Date", "DATE", 100, False),
           ("Forecast", "TEXT_NUMBER", 115, False), ("Done", "TEXT_NUMBER", 115, False), ("To come", "TEXT_NUMBER", 115, False),
           ("Closed", "CHECKBOX", 60, False), ("Note", "TEXT_NUMBER", 260, False), ("Was", "TEXT_NUMBER", 60, False)]
HIDDEN = {"Was"}
MONEY_COLS = ("Forecast", "Done", "To come")
# sheet column -> Forecasts column, for the cells a PM can edit on a forecast line
EDITABLE = {"Item": "Item", "Party": "Party", "Type": "Cost Type", "Ref": "PO / Order #", "Date": "Expected Date",
            "Forecast": "Amount", "Closed": "Closed"}
RO = json.dumps({"ro": 1})
KIND = {"in": {"paid": "Received", "billed": "Invoiced, not paid", "order": "To invoice", "forecast": "Forecast"},
        "out": {"paid": "Paid", "billed": "Billed, not paid", "order": "On order", "forecast": "Forecast"}}
LINE_STATUS = dict(model.STATUS)


# ---------------------------------------------------------------- what goes on the sheet
def layout(v: dict, as_at: str, problems: list | None = None) -> list[dict]:
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
    head = f"Data as at {as_at}" + (f" · couldn't apply: {'; '.join(problems)}" if problems else "")
    rows = [{"cells": {"Item": f"Summary · {v['code']} {v['name']}", "Note": head, "Was": RO},
             "fmt": fmt(True, WHITE, NAVY, size=3), "locked": True,
             "children": [{"cells": {"Item": item, "Done": done, "To come": tocome, "Note": "" if note == "bold" else note, "Was": RO},
                           "fmt": fmt(bold=note == "bold", money=True), "locked": True} for item, done, tocome, note in summary]}]
    for side, title, bg, bg2 in (("in", "Money in · customer", GREEN_BG2, GREEN_BG), ("out", "Money out · costs", RED_BG2, RED_BG)):
        groups = v["ledger_lines"][side]
        sec = {"cells": {"Item": title, "Done": round(sum(g["done"] for g in groups), 2),
                         "To come": round(sum(g["tocome"] for g in groups), 2),
                         "Note": "add a row under this heading for a new forecast line", "Was": json.dumps({"section": side})},
               "fmt": fmt(True, 0, bg, money=True, size=3), "locked": True,
               "children": [{"cells": {"Item": f"➕ To add a {'customer payment line' if side == 'in' else 'cost'}: right-click this row › "
                                               f"Insert Row Below, then fill Item and Forecast (required), Type (pick from the list), "
                                               f"Party and Date (expected). Saved at the next sync, or Refresh in the app.", "Was": RO},
                             "fmt": fmt(color=GREY_TXT, italic=True), "locked": True}]}
        for g in groups:
            l = g["line"]
            note = []
            if l.get("unassigned"):
                note.append("not linked to a forecast line: link it in the app")
            if g["overdue"]:
                note.append("overdue")
            if l["direction"] == "Out" and l["overrun"] > 0.5:
                note.append(f"{money(l['overrun'])} over forecast")
            f = l["f"]
            edit = {"Item": l["item"], "Party": f.get("Party") or "", "Type": f.get("Cost Type") or "",
                    "Ref": str(f.get("PO / Order #") or ""), "Date": str(f.get("Expected Date") or "")[:10] or None,
                    "Forecast": model.num(f.get("Amount")) if f.get("Amount") not in (None, "") else None,
                    "Closed": bool(f.get("Closed"))}
            if l.get("unassigned"):
                edit = {"Item": l["item"], "Party": l["party"], "Ref": ", ".join(l["refs"])}
            if g["next"]:
                note.append(f"next payment {g['next']}")
            line = {"cells": {**edit, "Status": LINE_STATUS.get(l["status"], ""), "Done": g["done"], "To come": g["tocome"],
                              "Note": "; ".join(note),
                              "Was": RO if l.get("unassigned") else json.dumps({"id": l["id"], **{k: norm_cell(k, x) for k, x in edit.items()}})},
                    "fmt": fmt(True, 0, bg2, money=True), "locked": bool(l.get("unassigned")), "collapse": True, "children": []}
            for r in g["rows"]:
                doc = r.get("doc") or {}
                ref, label = doc.get("Doc #") or "", r["label"]
                if doc and ref == doc.get("Type"):                  # a bill entered without a supplier invoice number
                    ref, label = "", f"{doc.get('Type')} (no number)"
                line["children"].append({"cells": {
                    "Item": label, "Party": l["party"] if side == "in" else r["party"],   # invoices name the job, not the customer
                    "Status": KIND[side].get(r["kind"], r["kind"]), "Ref": ref, "Date": r["date"],
                    "Done": r["amount"] if r["done"] else None, "To come": None if r["done"] else r["amount"],
                    "Note": "overdue" if r["overdue"] else "", "Was": RO},
                    "fmt": fmt(color=RED_TXT if r["overdue"] else (GREY_TXT if r["done"] else 0), money=True), "locked": True})
            sec["children"].append(line)
        rows.append(sec)
    rows.append({"cells": {"Item": "Rebuilt by the cashflow sync (7am and 1pm weekdays, or Refresh in the app) from NetSuite and the "
                                   "Cashflow Database. You can edit a forecast line's item, party, type, PO #, expected date, forecast $ "
                                   "and Closed, or add a line under Money in / Money out: it's saved at the next sync. Payments come "
                                   "from NetSuite and can't be edited here. To remove a line, tick Closed or delete it in the app.",
                           "Was": RO},
                 "fmt": fmt(color=GREY_TXT, italic=True), "locked": True, "children": []})
    return rows


def norm_cell(col: str, x):
    """A cell value in a comparable form (what the sheet holds vs what was written)."""
    if col == "Closed":
        return bool(x)
    if col == "Forecast":
        if x in (None, ""):
            return None
        try:
            return round(float(re.sub(r"[^0-9.\-]", "", str(x))), 2)
        except ValueError:
            return str(x).strip()
    if col == "Date":
        return str(x)[:10] if x else None
    return re.sub(r"\s+", " ", str(x or "")).strip()


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

    def ours(self, folder_id: int) -> dict | None:
        """The app's cashflow sheet in a project folder: the one with the hidden "Was" column."""
        for s in self.get(f"/folders/{folder_id}").get("sheets", []):
            if "ashflow" in s["name"] and any(c["title"] == MARK for c in self.get(f"/sheets/{s['id']}/columns").get("data", [])):
                return s
        return None

    def dropdowns(self, sheet_id: int):
        """Type: a strict dropdown of the app's cost types (added to sheets made before it existed)."""
        from .editor import COST_TYPES
        for c in self.get(f"/sheets/{sheet_id}/columns").get("data", []):
            if c["title"] == "Type" and (c["type"] != "PICKLIST" or c.get("options") != COST_TYPES):
                requests.put(f"{API}/sheets/{sheet_id}/columns/{c['id']}", headers=self.h, timeout=30,
                             json={"type": "PICKLIST", "options": COST_TYPES, "validation": True}).raise_for_status()

    def find_or_create(self, folder_id: int, name: str) -> int:
        hit = self.ours(folder_id)
        if hit:
            if hit["name"] != name:
                requests.put(f"{API}/sheets/{hit['id']}", headers=self.h, json={"name": name}, timeout=30).raise_for_status()
            have = {c["title"] for c in self.get(f"/sheets/{hit['id']}/columns").get("data", [])}
            for i, (t, ty, w, _) in enumerate(COLUMNS):
                if t not in have:
                    requests.post(f"{API}/sheets/{hit['id']}/columns", headers=self.h, timeout=30,
                                  json=[{"title": t, "type": ty, "width": w, "index": i, "hidden": t in HIDDEN}]).raise_for_status()
            self.dropdowns(hit["id"])
            return hit["id"]
        body = {"name": name, "columns": [{"title": t, "type": ty, "width": w, **({"primary": True} if p else {}),
                                           **({"hidden": True} if t in HIDDEN else {})} for t, ty, w, p in COLUMNS]}
        r = requests.post(f"{API}/folders/{folder_id}/sheets", headers=self.h, json=body, timeout=60)
        r.raise_for_status()
        log.info("created sheet %s", name)
        self.dropdowns(r.json()["result"]["id"])
        return r.json()["result"]["id"]


def keyed(rows: list[dict]) -> list[dict]:
    """Give every row a stable key (kept in its hidden Was cell), so the next sync can update it in place:
    head, sum:{item}, sec:in / sec:out, line:{forecast row id}, u:{side}:{item}, {line key}|{item}|{ref}|{status}, foot."""
    def setk(r, k):
        was = json.loads(r["cells"].get("Was") or "{}")
        was["k"] = k
        r["cells"]["Was"] = json.dumps(was)
        return k

    for i, top in enumerate(rows):
        was = json.loads(top["cells"].get("Was") or "{}")
        tk = setk(top, f"sec:{was['section']}" if "section" in was else ("head" if i == 0 else "foot"))
        seen = {}
        for c in top.get("children", []):
            cw = json.loads(c["cells"].get("Was") or "{}")
            if tk == "head":
                ck = f"sum:{c['cells'].get('Item')}"
            elif "id" in cw:
                ck = f"line:{cw['id'] if cw['id'] is not None else c['cells'].get('Item')}"
            else:
                ck = f"u:{tk}:{c['cells'].get('Item')}"
            setk(c, ck)
            for g in c.get("children", []):
                base = f"{ck}|{g['cells'].get('Item')}|{g['cells'].get('Ref') or ''}|{g['cells'].get('Status')}"
                seen[base] = seen.get(base, 0) + 1
                setk(g, base if seen[base] == 1 else f"{base}#{seen[base]}")
    return rows


def read_sheet(sheet_id: int, h: dict) -> tuple[dict, list[dict]]:
    """({column title: id}, rows in sheet order as {id, parent, cells{title: value}, format, k})."""
    res = requests.get(f"{API}/sheets/{sheet_id}", headers=h, params={"include": "format"}, timeout=90)
    res.raise_for_status()
    sh = res.json()
    col = {c["title"]: c["id"] for c in sh["columns"]}
    title = {v: k for k, v in col.items()}
    out = []
    for r in sh.get("rows", []):
        cells = {title[c["columnId"]]: c.get("value") for c in r["cells"]}
        try:
            k = (json.loads(cells.get(MARK) or "{}") or {}).get("k")
        except (ValueError, AttributeError):
            k = None
        out.append({"id": r["id"], "parent": r.get("parentId"), "cells": cells, "format": r.get("format") or "", "k": k})
    return col, out


def same_cell(a, b) -> bool:
    if a in (None, "", False) and b in (None, "", False):
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 0.005
    return str(a) == str(b)


def write(sheet_id: int, rows: list[dict], keep_unkeyed: bool = False):
    """Bring the sheet in line with `rows`, updating rows in place (so comments, attachments and cell history
    on a row survive): changed rows are updated, missing ones added next to their neighbours, gone ones deleted.
    Rows a PM added that haven't been turned into forecast lines yet are kept when keep_unkeyed (an edit failed)."""
    rows = keyed(rows)
    h = {"Authorization": f"Bearer {os.environ['SMARTSHEET_TOKEN']}"}
    col, have = read_sheet(sheet_id, h)
    if not any(r["k"] for r in have):
        return rewrite(sheet_id, rows)                # first time (or an old-style sheet): build from scratch
    k_of = {r["id"]: r["k"] for r in have}
    by_k = {r["k"]: r for r in have if r["k"]}

    def cells_for(r, update=False):
        out = []
        for title, cid in col.items():
            val = r["cells"].get(title)
            if val in (None, "") and not update:
                continue
            c = {"columnId": cid, "value": (round(val, 2) if isinstance(val, float) else val)}
            if val in (None, ""):
                c["value"] = False if title == "Closed" else ""
            if title in MONEY_COLS:
                c["format"] = r["fmt"]
            out.append(c)
        return out

    wanted_rows, plan = set(), []                    # plan: (row, key, parent key, previous sibling key)

    def walk(rs, parent_k):
        prev = None
        for r in rs:
            k = json.loads(r["cells"]["Was"])["k"]
            plan.append((r, k, parent_k, prev))
            prev = k
            walk(r.get("children", []), k)

    walk(rows, None)
    updates, adds = [], []
    for r, k, pk, prev in plan:
        e = by_k.get(k)
        if e and k_of.get(e["parent"]) == pk:
            wanted_rows.add(e["id"])
            if (any(not same_cell(e["cells"].get(t), r["cells"].get(t)) for t in col)
                    or e["format"].strip(",") != plain(r["fmt"]).strip(",")):
                updates.append({"id": e["id"], "format": plain(r["fmt"]), "cells": cells_for(r, update=True)})
        else:
            adds.append((r, k, pk, prev))
    gone = [e["id"] for e in have if e["id"] not in wanted_rows and (e["k"] or not keep_unkeyed)]
    # children of a deleted row go with it; don't delete twice
    gone_set = set(gone)
    gone = [i for i in gone if not any(e["id"] == i and e["parent"] in gone_set for e in have)]
    for i in range(0, len(gone), 300):
        requests.delete(f"{API}/sheets/{sheet_id}/rows", headers=h, timeout=60,
                        params={"ids": ",".join(map(str, gone[i:i + 300])), "ignoreRowsNotFound": "true"}).raise_for_status()
    for i in range(0, len(updates), 200):
        requests.put(f"{API}/sheets/{sheet_id}/rows", headers=h, json=updates[i:i + 200], timeout=60).raise_for_status()
    id_of = {e["k"]: e["id"] for e in have if e["id"] in wanted_rows}
    for r, k, pk, prev in adds:                      # in sheet order, each placed after its previous sibling
        if prev and prev in id_of:
            where = {"siblingId": id_of[prev]}
        elif pk and pk in id_of:
            where = {"parentId": id_of[pk], "toTop": True}
        else:
            where = {"toTop": True}
        res = requests.post(f"{API}/sheets/{sheet_id}/rows", headers=h, timeout=60,
                            json=[{**where, "format": plain(r["fmt"]), "cells": cells_for(r),
                                   **({"locked": True} if r.get("locked") else {}),
                                   **({"expanded": False} if r.get("collapse") and r.get("children") else {})}])
        res.raise_for_status()
        id_of[k] = res.json()["result"][0]["id"]
    log.info("sheet %s: %d updated, %d added, %d removed", sheet_id, len(updates), len(adds), len(gone))


def rewrite(sheet_id: int, rows: list[dict]):
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
        body = [{"toBottom": True, **({"parentId": parent} if parent else {}), "format": plain(r["fmt"]), "cells": cells(r),
                 **({"locked": True} if r.get("locked") else {})} for r in batch]
        # (collapsing happens after the children exist: see rewrite)
        ids = []
        for i in range(0, len(body), 200):
            res = requests.post(f"{API}/sheets/{sheet_id}/rows", headers=t.h, json=body[i:i + 200], timeout=60)
            res.raise_for_status()
            ids += [x["id"] for x in res.json()["result"]]
        return ids

    level, fold = [(None, rows)], []
    while level:
        nxt = []
        for parent, batch in level:
            if batch:
                for rid, r in zip(add(batch, parent), batch):
                    if r.get("children"):
                        nxt.append((rid, r["children"]))
                        if r.get("collapse"):
                            fold.append({"id": rid, "expanded": False})
        level = nxt
    for i in range(0, len(fold), 200):
        requests.put(f"{API}/sheets/{sheet_id}/rows", headers=t.h, json=fold[i:i + 200], timeout=60).raise_for_status()


def wanted(p: dict) -> bool:
    """Live projects, and Complete ones (their folder is only found while it's still under 2. Contracted)."""
    return bool(p.get("Project")) and p.get("Status") != "Closed"


def archive_old(dry: bool = False) -> list[str]:
    """One-off tidy: move each project folder's old hand-kept cashflow sheets into its Archive subfolder."""
    ss, moved = Sheets(), []
    for f in ss.get(f"/folders/{CONTRACTED}").get("folders", []):
        if not f["name"].upper().startswith("BHF2"):
            continue
        sub = ss.get(f"/folders/{f['id']}")
        mine = ss.ours(f["id"])
        old = [s for s in sub.get("sheets", []) if "ashflow" in s["name"] and (not mine or s["id"] != mine["id"])]
        if not old or not mine:
            continue                                   # keep the old sheet until the new one exists
        arch = next((x for x in sub.get("folders", []) if x["name"].lower() == "archive"), None)
        for s in old:
            moved.append(f"{f['name']}: {s['name']} -> Archive")
            if dry:
                continue
            if not arch:
                r = requests.post(f"{API}/folders/{f['id']}/folders", headers=ss.h, json={"name": "Archive"}, timeout=30)
                r.raise_for_status()
                arch = r.json()["result"]
            requests.post(f"{API}/sheets/{s['id']}/move", headers=ss.h, timeout=30,
                          json={"destinationType": "folder", "destinationId": arch["id"]}).raise_for_status()
    for m in moved:
        log.info(m)
    return moved


def publish(only: str | None = None, dry: bool = False, problems: dict | None = None) -> list[str]:
    """Write the LIVE cashflow sheet for every live project (or one)."""
    problems = problems or {}
    P, F, T, S = (Table(config.SHEETS[k]).load() for k in ("projects", "forecasts", "transactions", "schedule"))
    today = dt.date.today().isoformat()
    as_at = dt.datetime.now().strftime("%d %b %Y %H:%M")
    ss, done = Sheets(), []
    for p in P:
        code = p.get("Project")
        if not wanted(p) or (only and code != only):
            continue
        v = model.project_view(p, F, T, S, today)
        rows = layout(v, as_at, problems.get(code))
        folder = ss.project_folder(code)
        if not folder:
            if p.get("Status") != "Complete":                 # finished jobs filed under Completed have no sheet
                log.warning("%s: no folder under 2. Contracted starting with the code; skipped", code)
            continue
        name = sheet_name(code, p.get("Name") or "")
        if dry:
            n = sum(1 + len(c["children"]) + sum(len(g.get("children", [])) for g in c["children"]) for c in rows)
            log.info("%s: would write %d rows to '%s' in folder %s", code, n, name, folder["name"])
        else:
            write(ss.find_or_create(folder["id"], name), rows, keep_unkeyed=bool(problems.get(code)))
            log.info("%s: wrote '%s'", code, name)
        done.append(code)
    return done


# ---------------------------------------------------------------- edits made on the LIVE sheets -> Forecasts
def sheet_edits(rows: list[dict]) -> tuple[list, list]:
    """From a LIVE sheet's rows (in sheet order): ([(forecast row id, {Forecasts column: value})], [new line values]).
    Only cells that differ from what the sync last wrote count as edits."""
    edits, new, side = [], [], None
    for r in rows:
        try:
            was = json.loads(r.get("Was") or "null")
        except ValueError:
            was = None
        if isinstance(was, dict) and "section" in was:
            side = was["section"]
            continue
        if isinstance(was, dict) and "id" in was:
            diff = {}
            for col, fcol in EDITABLE.items():
                now = norm_cell(col, r.get(col))
                if now != was.get(col):
                    diff[fcol] = now
            if diff:
                edits.append((was["id"], diff))
            continue
        if was is None and side and str(r.get("Item") or "").strip():
            new.append({fcol: norm_cell(col, r.get(col)) for col, fcol in EDITABLE.items()}
                       | {"Direction": "In" if side == "in" else "Out"})
    return edits, new


def clean(values: dict) -> tuple[dict, str | None]:
    """Forecasts values from sheet cells, checked the way the app's form checks them."""
    from .editor import COST_TYPES
    out = dict(values)
    if "Item" in out and not out["Item"]:
        return {}, "a line needs a name"
    if "Amount" in out and isinstance(out["Amount"], str):
        return {}, f"forecast “{out['Amount']}” isn't a number"
    if "Cost Type" in out and out["Cost Type"] and out["Cost Type"] not in COST_TYPES:
        return {}, f"type “{out['Cost Type']}” isn't one of: {', '.join(COST_TYPES)}"
    if out.get("PO / Order #"):
        out["PO / Order #"] = ", ".join(x for x in re.split(r"[,;\s]+", out["PO / Order #"]) if x)
    return {k: (None if v in ("",) else v) for k, v in out.items()}, None


def pull(only: str | None = None, dry: bool = False) -> dict:
    """Apply edits made on the LIVE sheets to the Forecasts sheet. Returns {code: [problems]} for the sheets' headers."""
    from .store import Store
    store, ss, problems = Store(), Sheets(), {}
    projects = store.load(force=True)[0]
    for p in projects:
        code = p.get("Project")
        if only and code != only:
            continue
        folder = ss.project_folder(code)
        sheet = ss.ours(folder["id"]) if folder and wanted(p) else None
        if not sheet:
            continue
        edits, new = sheet_edits(Table(sheet["id"]).load())
        for row_id, diff in edits:
            values, err = clean(diff)
            if not err and not dry:
                err = store.save_forecast(code, row_id, values)
            log.info("%s: line %s %s%s", code, row_id, values or diff, f"  [{err}]" if err else "")
            if err:
                problems.setdefault(code, []).append(err)
        for values in new:
            values, err = clean(values)
            if not err and values.get("Amount") in (None, ""):
                err = f"new line “{values.get('Item')}” needs a Forecast amount"
            values.setdefault("Cost Type", None)
            values["Cost Type"] = values.get("Cost Type") or ("Customer Milestone" if values.get("Direction") == "In" else "Other")
            if not err and not dry:
                err = store.save_forecast(code, None, values)
            log.info("%s: new line %s%s", code, values.get("Item"), f"  [{err}]" if err else "")
            if err:
                problems.setdefault(code, []).append(err)
    return problems


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


def run(dry: bool = False, only: str | None = None, problems: dict | None = None):
    for step in (lambda: style(dry), lambda: publish(only, dry, problems)):
        try:
            step()
        except Exception:                        # never fail the sync over a view
            log.exception("project sheets step failed")


def safe_pull(dry: bool = False, only: str | None = None) -> dict:
    try:
        return pull(only, dry)
    except Exception:
        log.exception("reading edits from the LIVE sheets failed")
        return {}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--no-style", action="store_true")
    ap.add_argument("--archive-old", action="store_true", help="one-off: move old cashflow sheets into Archive")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    a = ap.parse_args()
    if a.archive_old:
        archive_old(a.dry_run)
        raise SystemExit
    found = safe_pull(a.dry_run, a.project)
    if a.no_style:
        publish(a.project, a.dry_run, found)
    else:
        run(a.dry_run, a.project, found)

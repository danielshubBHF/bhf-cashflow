"""Set up new projects automatically from the job's costing.

A new cashflow starts when one thing exists (nothing else is needed), either of:
  * the job's folder in SharePoint 2.0 Projects Contracted ("BHFxxxxx ...") with the costing saved in it (in
    1.0 Working Folder / 0.3 Costing & Cashflow, or anywhere in the folder); or
  * the job's folder in Smartsheet under 3. BHF Systems / 2. Contracted (copied from the template, "BHFxxxxx ..."),
    with the costing attached to any sheet in it.
Starting from SharePoint, the Smartsheet folder is copied from the template for you.
At the next run (part of the daily 07:30 task on the filing PC; run it any time to do it now) the project gets:
  * its Projects row (code, name, Live, contract value, BHF labour, PM, SharePoint folder), or the gaps in the row the
    PM added filled in;
  * one outgoing forecast line per costing line, exactly as costed (item, supplier, extended cost; section and
    description in Notes); the $800/day BHF labour lines go to Internal Labour, not the forecast;
  * an incoming "Main contract" line for the contract value (the costing's bid price, replaced by the NetSuite sales
    order total once accounts raise it);
  * its Smartsheet folder, if it started from SharePoint or a Projects row.
NetSuite is not needed to start: the job is found by its code at a later run, once accounts create it and raise its
first document; the sync then pulls its documents into the cashflow. The project shows "New project" under Needs attention until the PM
has checked it.

Run:  python -m bhf.setup_projects            (sets up what it finds)
      python -m bhf.setup_projects --dry-run  (shows what it would do)
"""
import argparse
import datetime as dt
import logging
import os
import re
from pathlib import Path

import requests

from . import config, costing, model
from .smartsheet_db import API, Table

log = logging.getLogger("setup_projects")
ROOT = Path(os.getenv("SP_LOCAL_ROOT", r"C:\Users\DanielShub\BHF Technologies\6.0 Projects - Documents"))
CONTRACTED = ROOT / "2.0 Projects Contracted"
SS_CONTRACTED = int(os.getenv("SS_CONTRACTED_FOLDER", "1654885033764740"))
SS_TEMPLATE = os.getenv("SS_TEMPLATE_FOLDER", "BHF Project Contracted Template")
NEW_WITHIN_DAYS = 120          # only SharePoint folders / NetSuite jobs this recent count as new
SETUP_NOTE = "Set up from costing"

TYPE_RULES = [(r"install|plumbing|site work|mechanical work|civil", "Install / Site Works"),
              (r"commission|start.?up", "Commissioning"),
              (r"freight|shipping|packing|transport", "Freight & Logistics"),
              (r"travel|accom|flight", "Travel & Expenses"),
              (r"engineering|design|drafting|programm|software|documentation|scada|citect", "Engineering / Design"),
              (r"chemical|resin|media|cartridge|membrane|consumable|sand|carbon", "Consumables & Chemicals"),
              (r"consult|review|certif", "Consultants"),
              (r"contingen", "Other")]


def cost_type(text: str) -> str:
    for pat, t in TYPE_RULES:
        if re.search(pat, text, re.I):
            return t
    return "Equipment"


def party_of(line: dict) -> str:
    """The supplier: the costing's supplier column when it reads like a name, else '<Supplier> - System' sections."""
    s = (line.get("supplier") or "").strip()
    if s and len(s) <= 40 and not re.search(r"\d{3,}|included|inc\.|estimated|^\d", s, re.I):
        return s
    m = re.match(r"^([A-Z][\w&.]+(?: [A-Z][\w&.]+)?) - ", line.get("section") or "")
    return m.group(1) if m else ""


def find_folder(code: str) -> Path | None:
    hits = [p for p in CONTRACTED.iterdir() if p.is_dir() and p.name.upper().startswith(code.upper())] if CONTRACTED.exists() else []
    return hits[0] if hits else None


def find_costing(folder: Path) -> Path | None:
    """The newest costing workbook in the job folder: 1.0 Working Folder / 0.3 Costing & Cashflow first, else anywhere."""
    def costings(files):
        return [p for p in files if p.suffix.lower() in (".xlsx", ".xlsm") and not p.name.startswith("~$")
                and "cost" in p.name.lower() and "working cashflow" not in p.name.lower()]
    d = folder / "1.0 Working Folder" / "0.3 Costing & Cashflow"
    files = (costings(d.glob("*.xls*")) if d.exists() else []) or costings(folder.rglob("*.xls*"))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def sharepoint_cards(known: set) -> dict:
    """{code: folder name} for new job folders in SharePoint 2.0 Projects Contracted: not on the app yet and made in
    the last NEW_WITHIN_DAYS days (so an old folder never sets itself up)."""
    if not CONTRACTED.exists():
        return {}
    since = dt.datetime.now().timestamp() - NEW_WITHIN_DAYS * 86400
    out = {}
    for d in CONTRACTED.iterdir():
        m = re.match(r"^(BHF\d{5})\b", d.name, re.I)
        if d.is_dir() and m and m.group(1).upper() not in known and d.stat().st_ctime >= since:
            out[m.group(1).upper()] = d.name
    return out


def forecast_rows(code: str, c: dict, source: str, customer: str, contract: float) -> tuple[list, float]:
    """Forecast rows replicating the costing, and the BHF labour total."""
    rows, seen = [], {}
    stamp = dt.date.today().strftime("%d/%m/%y")
    rows.append({"Project": code, "Item": "Main contract", "Direction": "In", "Cost Type": "Customer Milestone",
                 "Party": customer, "Amount": round(contract, 2),
                 "Notes": f"{SETUP_NOTE} {source} on {stamp}: add the customer PO number and payment milestones"})
    labour = 0.0
    for l in c["lines"]:
        if l["labour"]:
            labour += l["cost"]
            continue
        name = l["item"][:80]
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name} ({seen[name]})"
        note = "; ".join(x for x in (l.get("section"), l.get("desc")) if x)
        rows.append({"Project": code, "Item": name, "Direction": "Out",
                     "Cost Type": "Other" if l["contingency"] else cost_type(f"{l['item']} {l.get('section', '')}"),
                     "Party": party_of(l), "Amount": l["cost"],
                     "Notes": (f"Costing: {note}" if note else "Costing")[:400]})
    return rows, round(labour, 2)


def copy_smartsheet_folder(name: str, h: dict) -> int | None:
    top = requests.get(f"{API}/folders/{SS_CONTRACTED}", headers=h, timeout=60).json()
    if any(f["name"].upper().startswith(name.split()[0].upper()) for f in top.get("folders", [])):
        return None                                             # already there
    tpl = next((f for f in top.get("folders", []) if f["name"] == SS_TEMPLATE), None)
    if not tpl:
        log.warning("Smartsheet template folder %r not found", SS_TEMPLATE)
        return None
    r = requests.post(f"{API}/folders/{tpl['id']}/copy", headers=h, timeout=120, params={"include": "data,attachments,forms,ruleRecipients,rules"},
                      json={"destinationType": "folder", "destinationId": SS_CONTRACTED, "newName": name[:50]})
    r.raise_for_status()
    return r.json()["result"]["id"]


def smartsheet_cards(h: dict) -> dict:
    """{code: (folder name, folder id)} for the project folders under 3. BHF Systems / 2. Contracted."""
    top = requests.get(f"{API}/folders/{SS_CONTRACTED}", headers=h, timeout=60).json()
    return {f["name"].split()[0].upper(): (f["name"], f["id"]) for f in top.get("folders", [])
            if re.match(r"^BHF\d{5}\b", f["name"], re.I)}


def smartsheet_costing(folder_id: int, h: dict) -> Path | None:
    """The newest costing workbook attached to any sheet in the project's Smartsheet folder, downloaded to a temp file."""
    import tempfile
    d = requests.get(f"{API}/folders/{folder_id}", headers=h, timeout=60).json()
    found = []
    for sh in d.get("sheets", []):
        att = requests.get(f"{API}/sheets/{sh['id']}/attachments", headers=h, timeout=60, params={"pageSize": 200}).json()
        found += [(a.get("createdAt") or "", sh["id"], a) for a in att.get("data", [])
                  if a.get("attachmentType") == "FILE" and re.search(r"cost", a["name"], re.I)
                  and a["name"].lower().endswith((".xlsx", ".xlsm"))]
    if not found:
        return None
    _, sid, a = max(found, key=lambda x: x[0])
    url = requests.get(f"{API}/sheets/{sid}/attachments/{a['id']}", headers=h, timeout=60).json()["url"]
    out = Path(tempfile.gettempdir()) / "bhf_costing" / a["name"]
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(requests.get(url, timeout=120).content)
    return out


def run(dry: bool = False) -> list[str]:
    """Set up every project that has a project card in Smartsheet (its folder under 2. Contracted, or a Live row in the
    Projects sheet with no forecast yet) and a SharePoint folder with the same code holding a costing. The NetSuite job
    is found by its code (now, or at a later run once accounts raise its first document)."""
    from .netsuite import NetSuite, amount
    ns = NetSuite()
    h = {"Authorization": f"Bearer {os.environ['SMARTSHEET_TOKEN']}"}
    P, F = Table(config.SHEETS["projects"]), Table(config.SHEETS["forecasts"])
    projects, forecasts = P.load(), F.load()
    by_code = {str(p.get("Project") or "").upper(): p for p in projects}
    has_forecast = {str(f.get("Project") or "").upper() for f in forecasts}
    cards = smartsheet_cards(h)
    todo = sharepoint_cards(set(by_code))                                                # a new SharePoint folder
    todo.update({c: n for c, (n, _) in cards.items() if c not in by_code})               # a Smartsheet folder, no row yet
    todo.update({c: f"{c} {p.get('Name') or ''}".strip() for c, p in by_code.items()   # a row added by hand, no forecast
                 if p.get("Status") == "Live" and c not in has_forecast and not p.get("Setup") and re.match(r"^BHF\d{5}$", c)})
    out, upd = [], []
    if not dry:
        P.ensure_column("Setup", "TEXT_NUMBER", 260)
    for code, card in sorted(todo.items()):
        folder = find_folder(code)
        sheet = smartsheet_costing(cards[code][1], h) if code in cards else None   # attached in its Smartsheet folder
        sheet = sheet or (find_costing(folder) if folder else None)                # else saved in SharePoint 0.3
        if not sheet:
            out.append(f"{code}: waiting for its costing (save it in the SharePoint job folder, or attach it to a sheet "
                       f"in the project's Smartsheet folder)")
            continue
        job = ns.find_job(code)
        docs = ns.project_docs(int(job["job"])) if job else []
        so = round(sum(amount(d) for d in docs if d["type"] == "SalesOrd"), 2)
        customer = next((d.get("party") for d in docs if d["type"] in ("SalesOrd", "CustInvc")), "") or ""
        customer = re.sub(r"^BHF\d{5}.*", "", customer).strip()     # invoices name the job, not the customer
        try:
            c = costing.read(str(sheet), so or None, f"{customer} {card}".strip() or None)   # the card name tells our tab apart from copied ones
        except Exception as e:
            out.append(f"{code}: costing {sheet.name} not readable ({e})")
            continue
        customer = customer or str(c["head"].get("customer") or "").split(" - ")[0].strip()
        contract = so or costing._num(c["head"].get("bid")) or costing._num(c["head"].get("calc_sell")) or c["total_sell"]
        name = (card[len(code):] if card.upper().startswith(code) else (folder.name[len(code):] if folder else "")).strip(" -:") \
            or (str(job["name"])[len(code):].strip(" -:") if job else "")
        source = f"{sheet.name} (tab {c['tab']})"
        rows, labour = forecast_rows(code, c, source, customer, contract)
        setup = (f"{SETUP_NOTE} {source} on {dt.date.today():%d/%m/%y}: check the lines, add customer milestones, set the P&L timeline"
                 + ("; contract from the sales order" if so else "; contract from the costing's bid price until a sales order is raised")
                 + ("" if job else "; NetSuite job not found yet (it links itself once accounts raise its first document)"))
        values = {"Project": code, "Name": name, "NetSuite Job ID": str(job["job"]) if job else None, "Status": "Live",
                  "Contract Value": contract or None, "Internal Labour": labour or None,
                  "PM": str(c["head"].get("engineer") or "") or None,
                  "SharePoint Folder": str(folder.relative_to(ROOT)).replace("\\", "/") if folder else None,
                  "Setup": setup}
        out.append(f"{code} {name}: {len(rows) - 1} cost lines (${sum(r['Amount'] for r in rows if r['Direction'] == 'Out'):,.0f}), "
                   f"labour ${labour:,.0f}, contract ${contract:,.0f} from {source}; NetSuite job {job['job'] if job else 'not found yet'}")
        if dry:
            continue
        row = by_code.get(code)
        if row:                                           # keep what the PM typed; fill the gaps
            upd.append((row["_id"], {k: v for k, v in values.items() if v not in (None, "") and not row.get(k)} | {"Setup": setup}))
        else:
            P.add([values])
        F.add(rows)
        if code not in cards:
            try:
                copy_smartsheet_folder(f"{code} {name}", h)
            except Exception as e:
                log.warning("%s: Smartsheet folder not copied: %s", code, e)
    # projects set up from a costing: link the NetSuite job once accounts create it, and once a sales order is raised
    # replace the costing's bid price with it (only while Contract Value / Main contract still hold the set-up figure)
    fupd = []
    for p in projects:
        code = str(p.get("Project") or "").upper()
        if p.get("Status") != "Live" or not re.match(r"^BHF\d{5}$", code) or not p.get("Setup"):
            continue
        job_id = p.get("NetSuite Job ID")
        if not job_id:
            job = ns.find_job(code)
            if not job:
                continue
            job_id = job["job"]
            out.append(f"{code}: linked to NetSuite job {job_id}")
            upd.append((p["_id"], {"NetSuite Job ID": str(job_id)}))
        if "contract from the sales order" in str(p.get("Setup")):
            continue                                                   # already taken
        so = round(sum(amount(d) for d in ns.project_docs(int(job_id)) if d["type"] == "SalesOrd"), 2)
        was = float(p.get("Contract Value") or 0)
        if so > 0 and abs(so - was) > 1:
            out.append(f"{code}: contract ${was:,.0f} -> ${so:,.0f} from the NetSuite sales orders")
            upd.append((p["_id"], {"Contract Value": so,
                                   "Setup": f"{p['Setup']}; contract from the sales order on {dt.date.today():%d/%m/%y}"}))
            fupd += [(f["_id"], {"Amount": so}) for f in forecasts if str(f.get("Project") or "").upper() == code
                     and f.get("Item") == "Main contract" and abs(float(f.get("Amount") or 0) - was) <= 1]
    if upd and not dry:
        P.update(upd)
    if fupd and not dry:
        F.update(fupd)
    for line in out or ["Set-up: nothing new (no project card without a cashflow)"]:
        log.info(line)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(ap.parse_args().dry_run)

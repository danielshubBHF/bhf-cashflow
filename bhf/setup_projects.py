"""Set up new projects automatically from NetSuite + the job's costing.

The one way a project starts:
  1. Accounts create the job in NetSuite when the customer PO arrives, and raise the first document on it
     (the sales order for the customer PO, or the first PO).
  2. The PM creates the job's SharePoint folder (from the template) in 2.0 Projects Contracted and saves the final
     costing in 1.0 Working Folder / 0.3 Costing & Cashflow.
  3. This script (part of the daily 07:30 task on the filing PC; run it any time to do it now) finds every NetSuite
     job with documents that isn't in the Projects sheet yet, whose SharePoint folder exists, and sets it up:
       * a Projects row: code, name, NetSuite job ID, Live, contract value (the sales orders in NetSuite, else the
         costing's bid price), BHF labour (the costing's $800/day lines), PM, SharePoint folder;
       * one outgoing forecast line per costing line, exactly as costed (item, supplier, extended cost), with the
         section and description in Notes; the $800/day BHF labour lines go to Internal Labour, not the forecast;
       * one incoming line "Main contract" for the contract value;
       * the project's Smartsheet folder, copied from "BHF Project Contracted Template" (the sync then writes its
         cashflow sheet there).
     The project shows "Set up from costing: check" under Needs attention until the PM acknowledges it.
  Jobs with documents but no SharePoint folder stay listed on the dashboard ("not set up on the app").

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
NEW_WITHIN_DAYS = 120          # only jobs whose first NetSuite document is this recent count as new
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
    d = folder / "1.0 Working Folder" / "0.3 Costing & Cashflow"
    files = [p for p in d.glob("*.xls*") if not p.name.startswith("~$") and "cost" in p.name.lower()
             and "working cashflow" not in p.name.lower()] if d.exists() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


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


def run(dry: bool = False) -> list[str]:
    from .netsuite import NetSuite, amount
    ns = NetSuite()
    P, F = Table(config.SHEETS["projects"]), Table(config.SHEETS["forecasts"])
    projects = P.load()
    known = [p.get("NetSuite Job ID") for p in projects if p.get("NetSuite Job ID")]
    today = dt.date.today().isoformat()
    jobs = ns.new_jobs(known, model.add_days(today, -180))
    out = []
    for j in jobs:
        full = str(j.get("name") or "")
        code = full.split()[0] if full else ""
        first = model.nsdate(j.get("first_ever") or j.get("first"))
        if not re.match(r"^BHF\d{5}$", code) or (first and model.days(first, today) > NEW_WITHIN_DAYS):
            continue                                            # an old job with new activity: left for a person
        folder = find_folder(code)
        if not folder:
            out.append(f"{code}: no SharePoint folder in 2.0 Projects Contracted yet; waiting")
            continue
        sheet = find_costing(folder)
        docs = ns.project_docs(int(j["job"]))
        so = round(sum(amount(d) for d in docs if d["type"] == "SalesOrd"), 2)
        customer = next((d.get("party") for d in docs if d["type"] in ("SalesOrd", "CustInvc")), "") or ""
        c = None
        if sheet:
            try:
                c = costing.read(str(sheet), so or None, customer)
            except Exception as e:
                out.append(f"{code}: costing {sheet.name} not readable ({e})")
        contract = so or (costing._num(c["head"].get("bid")) or costing._num(c["head"].get("calc_sell")) or c["total_sell"] if c else 0.0)
        name = full[len(code):].strip(" -:") or folder.name[len(code):].strip()
        source = f"{sheet.name} (tab {c['tab']})" if c else ""
        rows, labour = forecast_rows(code, c, source, customer, contract) if c else ([], 0.0)
        setup = (f"{SETUP_NOTE} {source} on {dt.date.today():%d/%m/%y}: check the lines, add customer milestones, set the P&L timeline"
                 if c else "No costing found in 1.0 Working Folder / 0.3 Costing & Cashflow: save it there, or add forecast lines")
        prow = {"Project": code, "Name": name, "NetSuite Job ID": str(j["job"]), "Status": "Live",
                "Contract Value": contract or None, "Internal Labour": labour or None,
                "PM": str((c or {}).get("head", {}).get("engineer") or "") or None,
                "SharePoint Folder": str(folder.relative_to(ROOT)).replace("\\", "/"), "Setup": setup}
        out.append(f"{code} {name}: {len(rows) - 1 if rows else 0} cost lines (${sum(r['Amount'] for r in rows if r['Direction'] == 'Out'):,.0f})"
                   f", labour ${labour:,.0f}, contract ${contract:,.0f}{' from ' + source if c else ' (no costing yet)'}")
        if dry:
            continue
        P.ensure_column("Setup", "TEXT_NUMBER", 260)
        P.add([prow])
        if rows:
            F.add(rows)
        try:
            copy_smartsheet_folder(f"{code} {name}", {"Authorization": f"Bearer {os.environ['SMARTSHEET_TOKEN']}"})
        except Exception as e:
            log.warning("%s: Smartsheet folder not copied: %s", code, e)
    for line in out:
        log.info(line)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(ap.parse_args().dry_run)

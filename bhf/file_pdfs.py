"""File the PDFs attached to Transactions into the project folders in SharePoint.

Runs on a PC that syncs the "6.0 Projects - Documents" library with OneDrive: it copies each unfiled
PDF into the synced folder (OneDrive uploads it) and ticks Filed. The rules are in FILING_TASK.md.

  python -m bhf.file_pdfs --dry-run                 (prints the plan, writes nothing)
  python -m bhf.file_pdfs --project BHF26005        (one project)
  python -m bhf.file_pdfs                           (everything unfiled)

SP_LOCAL_ROOT (default below) is the synced library on this PC.
"""
import argparse
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import config
from .smartsheet_db import Table
from .sync import clean_vendor, vkey

ROOT = Path(os.getenv("SP_LOCAL_ROOT", r"C:\Users\DanielShub\BHF Technologies\6.0 Projects - Documents"))
WORK, POS, OFFER = "1.0 Working Folder", "0.7 Requisitions and PO's", "0.2 Final Offer Contracts and PO"
NOT_THE_BILL = re.compile(r"proforma|(^|[^A-Z])PI[_ \-]|(^|[^A-Z])ORD|(^|[^A-Z])SO\d|QUOTE", re.I)
BAD = re.compile(r'[\\/:*?"<>|#%]')


def norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def token_in(number: str, filename: str) -> bool:
    """`number` appears in `filename` as a whole token (not inside a longer number or word)."""
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(number.upper())}(?![A-Z0-9])", filename.upper()))


def files_under(folder: Path) -> list[Path]:
    return [Path(d) / f for d, _, fs in os.walk(folder) for f in fs] if folder.is_dir() else []


def kind(t: dict) -> str:
    if t.get("Type") == "Expense" or (t.get("Type") == "Bill" and str(t.get("Doc #") or "").upper().startswith("EXP")):
        return "Expense"
    return {"PO": "PO", "Bill": "Bill", "Invoice": "Invoice", "Stock issue": "Stock"}.get(t.get("Type"), "")


def safe(s: str) -> str:
    return re.sub(r"\s+", " ", BAD.sub("-", str(s))).strip()


def doc_number(t: dict) -> str:
    """Bills with no supplier number come through as "Bill": use NetSuite's number from the PDF name."""
    d = str(t.get("Doc #") or "")
    if norm(d) in ("", "BILL"):
        m = re.search(r"_(\w+)\.pdf$", str(t.get("PDF") or ""), re.I)
        return m.group(1) if m else str(t.get("NetSuite ID") or "")
    return d


def file_name(t: dict, code: str) -> str:
    k, supplier = kind(t), clean_vendor(t.get("Party") or "")
    if k == "PO":
        return f"{safe(t['Doc #'])} - {safe(supplier)} - Purchase Order.pdf"
    if k == "Bill":
        return f"Bill {safe(doc_number(t))} - {safe(supplier)}.pdf"
    if k == "Expense":
        return f"Expense {safe(doc_number(t))} - {safe(clean_vendor(t.get('Party') or ''))}.pdf"
    if k == "Stock":
        return f"Stock issue {safe(t['Doc #'])} - BHF Internal.pdf"
    return f"{safe(t['Doc #'])} - {code} - Customer Invoice.pdf"


@dataclass
class Plan:
    row: dict
    outcome: str = ""            # ALREADY_FILED | UPLOAD | UPLOAD_NEW_FOLDER | NEEDS_YOU
    folder: Path | None = None
    name: str = ""
    note: str = ""
    matches: list = field(default_factory=list)


def supplier_folder(pos_dir: Path, t: dict, po_tree: list[Path]) -> tuple[Path | None, str]:
    """(folder, how): the PO's folder if the PO is already filed, else a name match, else a new folder."""
    po = norm(t.get("PO / Order #"))
    if po:
        hits = {f.relative_to(pos_dir).parts[0] for f in po_tree if po in norm(f.name) and len(f.relative_to(pos_dir).parts) > 1}
        if len(hits) == 1:
            return pos_dir / hits.pop(), "where its PO is filed"
    want = vkey(t.get("Party") or "")
    subs = [d for d in pos_dir.iterdir() if d.is_dir()] if pos_dir.is_dir() else []
    exact = [d for d in subs if want and (vkey(d.name) == want or vkey(d.name).startswith(want)
                                          or want.startswith(vkey(d.name)) or want in vkey(d.name))]
    if len(exact) == 1:
        return exact[0], "name match"
    if len(exact) > 1:
        return None, "several supplier folders match: " + ", ".join(d.name for d in exact)
    first = vkey(clean_vendor(t.get("Party") or "").split(" ")[0]) if t.get("Party") else ""
    loose = [d for d in subs if len(first) > 3 and vkey(d.name).startswith(first)]
    if len(loose) == 1:
        return loose[0], "loose match on first word"
    if len(loose) > 1:
        return None, "several supplier folders match: " + ", ".join(d.name for d in loose)
    return pos_dir / safe(clean_vendor(t.get("Party") or "Other")), "new folder"


def plan_one(t: dict, project_dir: Path, code: str, others: dict[str, list[Path]]) -> Plan:
    p, k = Plan(row=t), kind(t)
    pos_dir, offer_dir = project_dir / WORK / POS, project_dir / WORK / OFFER
    if not project_dir.is_dir():
        p.outcome, p.note = "NEEDS_YOU", f"project folder not found: {project_dir}"
        return p
    if k in ("PO", "Bill", "Expense", "Stock"):
        if not pos_dir.is_dir():
            p.outcome, p.note = "NEEDS_YOU", f"no '{POS}' folder"
            return p
        tree = files_under(pos_dir)
        if k == "Expense":
            folder, how = pos_dir / "Expense Claims", "expense claims folder"
        elif k == "Stock":                       # stock issued from BHF's own warehouse
            internal = [d for d in pos_dir.iterdir() if d.is_dir() and vkey(d.name).startswith("bhfinternal")]
            folder, how = (internal[0], "BHF Internal folder") if internal else (pos_dir / "BHF Internal", "new folder")
        else:
            folder, how = supplier_folder(pos_dir, t, tree)
        if folder is None:
            p.outcome, p.note = "NEEDS_YOU", how
            return p
        num = t["Doc #"] if k == "PO" else doc_number(t)
        if k == "PO":
            hits = [f for f in tree if norm(num) in norm(f.name)]
        else:                                    # bills: only inside the supplier folder, whole-token match
            hits = [f for f in files_under(folder) if token_in(num, f.name) or (len(norm(num)) >= 6 and norm(num) in norm(f.name))]
            # the bill's own number can look like a marker (Hotco's invoices are numbered "SO432371"): ignore it
            real = [f for f in hits if not NOT_THE_BILL.search(re.sub(re.escape(num), "", f.name, flags=re.I))]
            if hits and not real:
                p.note = "only a proforma / order acknowledgement is filed: filing the tax invoice too. "
            hits = real
            po = norm(t.get("PO / Order #"))
            if not hits and po and norm(t.get("Doc #")) in ("", "BILL"):
                # no supplier invoice number in NetSuite: an invoice filed under the PO number is this bill
                hits = [f for f in files_under(folder) if po in norm(f.name) and "INVOIC" in str(f).upper()
                        and "PURCHASEORDER" not in norm(f.name)]
                if hits:
                    p.note = "matched by PO number on an invoice file (bill has no supplier number). "
        if k == "PO":                            # same PO number filed under a different project?
            elsewhere = [f"{c}: {f.name}" for c, fs in others.items() if c != code for f in fs if norm(num) in norm(f.name)]
            if elsewhere:
                p.note += "possible wrong project in NetSuite - also found in " + "; ".join(elsewhere[:3]) + ". "
    else:                                        # customer invoice
        if not offer_dir.is_dir():
            p.outcome, p.note = "NEEDS_YOU", f"no '{OFFER}' folder"
            return p
        inv = [d for d in offer_dir.iterdir() if d.is_dir() and d.name.lower() == "invoices"]
        folder, how = (inv[0] if inv else offer_dir / "Invoices"), ("invoices folder" if inv else "new folder")
        hits = [f for f in files_under(offer_dir) if norm(t["Doc #"]) in norm(f.name)]
    p.folder, p.name = folder, file_name(t, code)
    if hits:
        p.outcome, p.matches = "ALREADY_FILED", [str(f.relative_to(project_dir)) for f in hits[:3]]
    elif (folder / p.name).exists():
        p.outcome, p.matches = "ALREADY_FILED", [str((folder / p.name).relative_to(project_dir))]
    else:
        p.outcome = "UPLOAD" if folder.is_dir() else "UPLOAD_NEW_FOLDER"
        p.note += how
    return p


def download(T: Table, t: dict) -> bytes:
    atts = T.attachments(t["_id"])
    att = next((a for a in atts if a.get("name") == t.get("PDF")), atts[0] if atts else None)
    if not att:
        raise FileNotFoundError("no attachment on the Transactions row")
    r = requests.get(T.attachment_url(att["id"]), timeout=120)
    r.raise_for_status()
    if r.content[:4] != b"%PDF":
        raise ValueError("attachment isn't a PDF")
    return r.content


def run(dry: bool = True, project: str | None = None, root: Path = ROOT) -> list[Plan]:
    P, T = Table(config.SHEETS["projects"]), Table(config.SHEETS["transactions"])
    projects = {p["Project"]: p for p in P.load() if p.get("SharePoint Folder")}
    rows = [t for t in T.load() if t.get("PDF") and not t.get("Filed") and t.get("Project") in projects
            and (not project or t.get("Project") == project)]
    others = {c: files_under(root / p["SharePoint Folder"] / WORK / POS) for c, p in projects.items()}
    plans = []
    for t in sorted(rows, key=lambda t: (t["Project"], kind(t), str(t.get("Doc #")))):
        p = plan_one(t, root / projects[t["Project"]]["SharePoint Folder"], t["Project"], others)
        plans.append(p)
        if dry or p.outcome == "NEEDS_YOU":
            continue
        try:
            if p.outcome.startswith("UPLOAD"):
                data = download(T, t)
                p.folder.mkdir(parents=True, exist_ok=True)
                with open(p.folder / p.name, "xb") as f:      # never overwrite an existing file
                    f.write(data)
            T.update([(t["_id"], {"Filed": True})])
        except Exception as e:
            p.outcome, p.note = "NEEDS_YOU", f"failed: {type(e).__name__}: {e}"
    report(plans, dry)
    return plans


def report(plans: list[Plan], dry: bool):
    print(("DRY RUN - nothing written\n" if dry else "") + f"{len(plans)} unfiled PDFs")
    for p in plans:
        t = p.row
        where = f"{p.folder.name}/{p.name}" if p.folder and p.outcome.startswith("UPLOAD") else "; ".join(p.matches)
        print(f"  {t['Project']} {p.outcome:17} {kind(t):7} {str(t.get('Doc #'))[:22]:22} {where}  {p.note}".rstrip())
    counts = {}
    for p in plans:
        counts[(p.row["Project"], p.outcome)] = counts.get((p.row["Project"], p.outcome), 0) + 1
    for (code, outcome), n in sorted(counts.items()):
        print(f"{code}: {outcome} {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--project")
    a = ap.parse_args()
    run(dry=a.dry_run, project=a.project)

"""Sync worker (Render cron, twice daily).

For every project in the Projects sheet that isn't Closed:
  1. Pull every NetSuite transaction coded to it (POs, bills, invoices, credits, expenses, stock).
  2. Upsert one row per transaction in Transactions. NetSuite is the source of truth.
  3. Link each transaction to a Forecast (by PO #, then by unambiguous supplier name).
     A PO auto-linked by supplier writes its PO # back onto the forecast so its bills follow.
  4. New POs/bills: fetch the PDF from NetSuite and attach it to the Transactions row
     (PDF = file name, Filed = unticked). A scheduled Claude task files them into SharePoint
     and ticks Filed; the app never writes to SharePoint. Rows still missing a PDF are retried.
  5. New POs: read the payment terms from the PDF and build the Payment Schedule.
  6. Allocate bills to schedule milestones in order.

Run:  python -m bhf.sync           (writes)
      python -m bhf.sync --dry-run (prints what would change)
"""
import argparse
import datetime as dt
import logging
import re

from . import config, terms
from .netsuite import INCOMING, ORDER_TYPES, TYPE_NAMES, NetSuite, amount, is_paid
from .smartsheet_db import Table

log = logging.getLogger("sync")
PDF_TYPES = {"PurchOrd", "VendBill", "ExpRept", "CustInvc", "InvAdjst"}   # + stock issues (a generated record)
SUFFIXES = r"\b(pty|ltd|limited|co|company|inc|llc|gmbh|sa|lda|australia|aust|\(beijing\))\b\.?"


def clean_vendor(name: str) -> str:
    n = re.sub(r"\([^)]*\)", "", name or "")
    n = re.sub(SUFFIXES, "", n, flags=re.I)
    n = re.sub(r"[,.]+", " ", n)
    n = re.sub(r"[\\/:*?\"<>|#%]", "", n)
    return re.sub(r"\s+", " ", n).strip() or "Other"


def vkey(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_vendor(s).lower())


def fetch_pdf(ns, d: dict):
    """(file name, bytes) from the NetSuite RESTlet, or None. Never fails the sync."""
    try:
        if d["type"] == "InvAdjst":
            return ns.stock_issue_pdf(int(d["id"]))
        # Bills use NetSuite's printout: the PDFs attached to bills in NetSuite turned out to be quotes, order
        # acknowledgements and proformas far more often than the supplier's tax invoice (checked 06/10/26).
        got = ns.pdf(int(d["id"]))
        return got[:2] if got else None
    except Exception as e:                      # timeout, RESTlet error: retried next sync
        log.warning("PDF for %s %s not fetched: %s", d.get("type"), d.get("tranid"), e)
        return None


def norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def iso(nsdate):
    if not nsdate:
        return None
    d, m, y = (int(x) for x in str(nsdate).split("/"))
    return dt.date(y, m, d).isoformat()


def doc_no(d: dict) -> str:
    """The document number; a bill entered without the supplier's invoice number gets 'no ref #{NetSuite ID}' so it
    can still be told apart (its supplier and date are in their own columns)."""
    t = str(d.get("tranid") or "").strip()
    name = TYPE_NAMES.get(d["type"], d["type"])
    return t if t and t.lower() != name.lower() else f"no ref #{d['id']}"


def order_no(d: dict) -> str:
    if d["type"] in ORDER_TYPES:
        return d.get("tranid") or ""
    if d["type"] in ("CustInvc", "CustCred"):
        return d.get("otherrefnum") or ""
    return d.get("from_doc") or ""


def follow_bills(docs: list[dict]):
    """A bill credit raised from a bill points at the bill; point it at the bill's PO instead, so the
    credit lands on the same forecast line (and nets off) as the bill it reverses."""
    bills = {d.get("tranid"): d for d in docs if d["type"] == "VendBill"}
    for d in docs:
        src = bills.get(d.get("from_doc")) if d["type"] == "VendCred" else None
        if src and src.get("from_doc"):
            d["from_doc"] = src["from_doc"]


def po_list(f: dict) -> list[str]:
    return [norm(x) for x in re.split(r"[,;\s]+", str(f.get("PO / Order #") or "")) if x.strip()]


def link_forecast(forecasts: list[dict], d: dict, order: str):
    """Return (forecast item or None, forecast row to append this PO # to or None)."""
    direction = "In" if d["type"] in INCOMING else "Out"
    pool = [f for f in forecasts if f.get("Direction") == direction]
    if order:
        for f in pool:
            if norm(order) in po_list(f):
                return f["Item"], None
    if d["type"] == "InvAdjst":                      # stock issued from BHF warehouse
        hits = [f for f in pool if "stock" in str(f.get("Party") or "").lower()]
        if len(hits) == 1:
            return hits[0]["Item"], None
    party = vkey(d.get("party") or "")
    if party:
        hits = [f for f in pool if f.get("Party") and not f.get("Closed")
                and (vkey(f["Party"]) in party or party in vkey(f["Party"]))]
        if len(hits) == 1:
            f = hits[0]
            return f["Item"], (f if d["type"] in ORDER_TYPES and order else None)
    return None, None


AUTO_NOTE = "Auto-added by the sync"


def auto_variation(pf: list[dict], d: dict, order: str, ordered: dict) -> dict | None:
    """A new forecast line for a variation the PM hasn't set up yet, or None.
      customer: a new invoice quoting a customer PO that no incoming line lists (once the contract PO is known)
      supplier: a new PO from a supplier whose line(s) are already fully ordered (more scope, not the planned order)
    `ordered` = {forecast item: PO value already linked to it}."""
    amt = amount(d)
    if not order or amt <= 0 or any(norm(order) in po_list(f) for f in pf):
        return None
    when = iso(d.get("trandate"))
    if d["type"] == "CustInvc":
        ins = [f for f in pf if f.get("Direction") == "In"]
        if not any(po_list(f) for f in ins):           # contract PO not recorded yet: can't tell a variation apart
            return None
        party = next((f.get("Party") for f in ins if f.get("Party")), None) or d.get("party")
        return {"Item": f"Variation - customer PO {order}", "Direction": "In", "Cost Type": "Variation", "Party": party,
                "Amount": amt, "PO / Order #": order, "Expected Date": when,
                "Notes": f"{AUTO_NOTE} from invoice {d.get('tranid')} on {when}: check the name, value and payment milestones"}
    if d["type"] == "PurchOrd":
        party = vkey(d.get("party") or "")
        lines = [f for f in pf if f.get("Direction") == "Out" and f.get("Party") and not f.get("Closed")
                 and party and (vkey(f["Party"]) in party or party in vkey(f["Party"]))]
        if not lines or any(ordered.get(f["Item"], 0.0) < 0.98 * float(f.get("Amount") or 0) for f in lines):
            return None                                 # no line for this supplier, or one still has forecast to order
        return {"Item": f"{clean_vendor(d.get('party'))} - variation {order}", "Direction": "Out", "Cost Type": "Variation",
                "Party": lines[0]["Party"], "Amount": amt, "PO / Order #": order, "Expected Date": when,
                "Notes": f"{AUTO_NOTE} from {order} on {when}: {lines[0]['Item']} was already fully ordered. "
                         f"Check it's a variation, its value and payment terms"}
    return None


def run(dry: bool = False):
    ns = NetSuite()
    P, F, T, S = (Table(config.SHEETS[k]) for k in ("projects", "forecasts", "transactions", "schedule"))
    # Closed projects are frozen; Complete ones still sync (for financial-year figures) but don't fetch PDFs.
    projects = [p for p in P.load() if p.get("Status") != "Closed" and p.get("NetSuite Job ID")]
    forecasts, txns, sched = F.load(), T.load(), S.load()
    if not dry:
        T.ensure_column("Filed", "CHECKBOX", 60)
        T.ensure_column("Part Paid", "TEXT_NUMBER", 90)       # AUD ex GST received / paid so far on an open document
        T.ensure_column("Part Paid Date", "DATE", 90)
    by_ns = {str(t.get("NetSuite ID")): t for t in txns if t.get("NetSuite ID")}
    stamp = dt.datetime.now().strftime("%d/%m/%y %H:%M")
    t_upd, t_add, f_upd, s_add, s_upd, notes = [], [], {}, [], [], []
    f_new, cv_upd = [], {}             # auto-added variation lines; Contract Value increases
    pdf_new, pdf_old = {}, []        # t_add index -> (name, bytes); (existing row id, name, bytes)

    for p in projects:
        code = p["Project"]
        wants_pdfs = p.get("Status") != "Complete"
        docs = ns.project_docs(int(float(p["NetSuite Job ID"])))
        docs.sort(key=lambda d: (d["type"] not in ORDER_TYPES, int(d["id"])))   # orders first
        follow_bills(docs)
        paid = ns.paid_dates([int(d["id"]) for d in docs if d["type"] not in ORDER_TYPES])
        part = ns.part_paid([int(d["id"]) for d in docs if d["type"] in ("CustInvc", "VendBill") and not is_paid(d)])
        pf = [f for f in forecasts if f.get("Project") == code]
        ps = [s for s in sched if s.get("Project") == code]
        ordered = {}                   # PO value already linked to each line (for the supplier-variation rule)
        for t in txns:
            if t.get("Project") == code and t.get("Type") == "PO" and t.get("Forecast"):
                ordered[t["Forecast"]] = ordered.get(t["Forecast"], 0.0) + float(t.get("Amount") or 0)

        for d in docs:
            order = order_no(d)
            existing = by_ns.get(str(d["id"]))
            fc = existing.get("Forecast") if existing and existing.get("Forecast") else None
            if not fc and not existing and (new_line := auto_variation(pf, d, order, ordered)):
                new_line["Project"] = code
                pf.append(new_line)
                forecasts.append(new_line)
                f_new.append(new_line)
                fc = new_line["Item"]
                notes.append(f"{code}: new variation line '{fc}' ${new_line['Amount']:,.2f}")
                if new_line["Direction"] == "In" and p.get("Contract Value") not in (None, ""):
                    p["Contract Value"] = round(float(p["Contract Value"]) + new_line["Amount"], 2)
                    cv_upd[p["_id"]] = {"Contract Value": p["Contract Value"]}
            if not fc:
                fc, append_to = link_forecast(pf, d, order)
                if append_to is not None:
                    cur = str(append_to.get("PO / Order #") or "").strip()
                    append_to["PO / Order #"] = f"{cur}, {order}" if cur else order
                    f_upd[append_to["_id"]] = {"PO / Order #": append_to["PO / Order #"]}
                    notes.append(f"{code}: linked {order} ({d['party']}) to forecast '{fc}'")
            ccy = d.get("ccy") or ""
            row = {
                "Doc #": doc_no(d),
                "Project": code, "Type": TYPE_NAMES.get(d["type"], d["type"]),
                "Direction": "In" if d["type"] in INCOMING else "Out", "Party": d.get("party"),
                "PO / Order #": order, "Date": iso(d.get("trandate")), "Due Date": iso(d.get("duedate")),
                "Amount": amount(d),
                "Currency Amount": "" if ccy in ("", "Australian Dollar") else f"{ccy} {float(d.get('fx') or 0):,.2f}",
                "Status": (d.get("status") or "").split(" : ")[-1],
                # Settled with no payment found (stock issues, card charges, applied credits): the document date.
                "Paid Date": iso(paid.get(int(d["id"])) or d.get("trandate")) if is_paid(d) else None,
                "Part Paid": round(amount(d) * part[int(d["id"])][0], 2) if int(d["id"]) in part else None,
                "Part Paid Date": iso(part[int(d["id"])][1]) if int(d["id"]) in part else None,
                "Forecast": fc, "NetSuite ID": str(d["id"]), "Synced": stamp,
            }
            if d["type"] == "PurchOrd" and fc and not existing:
                ordered[fc] = ordered.get(fc, 0.0) + amount(d)
            if existing:
                if d["type"] in PDF_TYPES and wants_pdfs and not existing.get("PDF"):
                    if dry:
                        notes.append(f"{code}: would attach missing PDF to {row['Doc #']}")
                    elif (got := fetch_pdf(ns, d)):
                        pdf_old.append((existing["_id"], *got))
                diff = {k: v for k, v in row.items() if k != "Synced" and (existing.get(k) or None) != (v or None)
                        and not (k == "Amount" and abs(float(existing.get(k) or 0) - float(v or 0)) < 0.005)}
                if diff:
                    t_upd.append((existing["_id"], {**diff, "Synced": stamp}))
                continue

            # ---- new transaction: PDF attached to its row, schedule from PO terms
            pdf = None
            if d["type"] in PDF_TYPES and wants_pdfs and not dry and (got := fetch_pdf(ns, d)):
                pdf = got[1]
                pdf_new[len(t_add)] = got
            t_add.append(row)
            notes.append(f"{code}: new {row['Type']} {row['Doc #']} {d.get('party')} ${row['Amount']:,.2f}"
                         + ("" if fc else "  [UNASSIGNED]"))

            if d["type"] == "PurchOrd" and wants_pdfs and not any(norm(s.get("PO / Order #")) == norm(order) for s in ps):
                t = terms.extract(pdf)
                for i, m in enumerate(t["milestones"], 1):
                    pct = float(m["percent"])
                    new = {"Milestone": m["label"], "Project": code, "PO / Order #": order,
                           "Party": d.get("party"), "Direction": "Out", "Seq": i, "Percent": pct / 100,
                           "Amount": round(row["Amount"] * pct / 100, 2),
                           "Source": "PO PDF" if pdf else "Single payment",
                           "Confirmed": bool(t.get("confident")), "Terms Text": t.get("terms_text", "")[:400]}
                    s_add.append(new)
                    ps.append(new)

        # ---- allocate bills/invoices to milestones, in order, per PO / customer order
        docs_by_order = {}
        for d in docs:
            if d["type"] not in ORDER_TYPES and order_no(d) and amount(d) > 0:
                docs_by_order.setdefault(norm(order_no(d)), []).append(d)
        for o, ds in docs_by_order.items():
            ms = sorted([s for s in ps if norm(s.get("PO / Order #")) == o], key=lambda s: float(s.get("Seq") or 0))
            used = {str(s.get("Billed Doc")) for s in ms if s.get("Billed Doc")}
            free = [s for s in ms if not s.get("Billed Doc")]
            for d in sorted(ds, key=lambda d: int(d["id"])):
                ref = d.get("tranid") or str(d["id"])
                if ref in used or not free:
                    continue
                s = free.pop(0)
                s["Billed Doc"] = ref
                if s.get("_id"):
                    s_upd.append((s["_id"], {"Billed Doc": ref}))

    for line in notes:
        log.info(line)
    log.info("transactions: %d new, %d updated | forecasts linked: %d | variation lines added: %d | milestones: %d new, %d billed",
             len(t_add), len(t_upd), len(f_upd), len(f_new), len(s_add), len(s_upd))
    if dry:
        return notes
    if f_new:                          # before the transactions, so their Forecast names exist
        from .store import group_row
        for f in f_new:
            F.add([f], parent_id=group_row(forecasts, f["Project"]))
        P.update(list(cv_upd.items()))
    T.update(t_upd)
    added = T.add(t_add)
    targets = [(added[i]["id"], name, data) for i, (name, data) in pdf_new.items()] + pdf_old
    T.update(attach_pdfs(T, targets))
    F.update(list(f_upd.items()))
    S.add(s_add)
    S.update(s_upd)
    return notes


def attach_pdfs(T: Table, targets: list[tuple]) -> list[tuple[int, dict]]:
    """Attach each PDF to its Transactions row. Returns the row updates (PDF name, Filed unticked)
    for the ones that went through; a failed upload leaves PDF blank so the next sync retries it."""
    out = []
    for row_id, name, data in targets:
        try:
            T.attach(row_id, name, data)
        except Exception as e:
            log.warning("PDF %s not attached to row %s: %s", name, row_id, e)
            continue
        out.append((row_id, {"PDF": name, "Filed": False}))
    log.info("PDFs attached: %d of %d", len(out), len(targets))
    return out


def rerender_bills(dry: bool = False, project: str | None = None) -> list[str]:
    """One-off, after NetSuite's bill print template changes: fetch each live job's bill printout again, attach it
    under the same name (the dashboard opens the newest) and untick Filed, so
    `python -m bhf.file_pdfs --replace-own` swaps the copy in SharePoint."""
    ns = NetSuite()
    P, T = Table(config.SHEETS["projects"]), Table(config.SHEETS["transactions"])
    live = {p["Project"] for p in P.load() if p.get("Status") not in ("Closed", "Complete")}
    rows = [t for t in T.load() if t.get("Type") == "Bill" and t.get("PDF") and t.get("NetSuite ID")
            and t.get("Project") in live and (not project or t.get("Project") == project)]
    done = []
    for t in rows:
        if dry:
            done.append(f"{t['Project']} {t.get('Doc #')} -> {t['PDF']}")
            continue
        try:
            got = ns.pdf(int(t["NetSuite ID"]))
            if got:
                T.attach(t["_id"], t["PDF"], got[1])
                T.update([(t["_id"], {"Filed": False})])
                done.append(f"{t['Project']} {t.get('Doc #')} -> {t['PDF']}")
        except Exception as e:
            log.warning("bill %s: %s", t.get("Doc #"), e)
    for d in done:
        log.info(d)
    log.info("bills re-rendered: %d of %d%s", len(done), len(rows), " [dry run]" if dry else "")
    return done


def refetch_bills(dry: bool = False):
    """One-off: swap NetSuite's printout for the supplier's own invoice on bills already attached. A refreshed
    row gets Filed unticked so `python -m bhf.file_pdfs --replace-own` replaces the file it filed earlier."""
    ns = NetSuite()
    P, T = Table(config.SHEETS["projects"]), Table(config.SHEETS["transactions"])
    live = {p["Project"] for p in P.load() if p.get("Status") not in ("Closed", "Complete")}
    rows = [t for t in T.load() if t.get("Type") == "Bill" and t.get("PDF") and t.get("NetSuite ID")
            and t.get("Project") in live and str(t["PDF"]).startswith("Bill_")]      # still NetSuite's printout
    swapped, none = [], 0
    for t in rows:
        got = None
        try:
            got = ns.pdf(int(t["NetSuite ID"]), attached=True)
        except Exception as e:
            log.warning("bill %s: %s", t.get("Doc #"), e)
        if not got or got[2] != "attached":
            none += 1
            continue
        if not dry:
            try:
                T.attach(t["_id"], got[0], got[1])
                T.update([(t["_id"], {"PDF": got[0], "Filed": False})])
            except Exception as e:
                log.warning("bill %s: attaching %s failed: %s", t.get("Doc #"), got[0], e)
                continue
        swapped.append(f"{t['Project']} {t.get('Doc #')} -> {got[0]}")
    for s in swapped:
        log.info(s)
    log.info("bills: %d swapped to the supplier's invoice, %d have no attached PDF (keep NetSuite's printout)%s",
             len(swapped), none, " [dry run]" if dry else "")
    return swapped


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refetch-bills", action="store_true", help="one-off: use suppliers' own invoices for bills")
    ap.add_argument("--rerender-bills", action="store_true", help="one-off: fetch bill printouts again after a template change")
    ap.add_argument("--project")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    a = ap.parse_args()
    if a.rerender_bills:
        rerender_bills(a.dry_run, a.project)
    elif a.refetch_bills:
        refetch_bills(a.dry_run)
    else:
        from . import project_sheets               # LIVE sheets in Smartsheet; never fail the sync
        found = project_sheets.safe_pull(a.dry_run)   # edits made on the LIVE sheets first, so this sync uses them
        run(a.dry_run)
        project_sheets.run(a.dry_run, problems=found)

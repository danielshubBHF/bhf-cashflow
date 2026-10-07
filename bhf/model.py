"""Turns the four Smartsheet tables into dashboard numbers. No formulas live in Smartsheet.

A forecast is a budget bucket. POs / bills / invoices roll up to it.
  committed   = POs linked to the bucket (for customer lines: the contract value once there's a customer PO)
  billed      = bills / invoices linked (credits negative)
  expected    = bucket total we now expect: max(budget, committed, billed), or max(committed, billed) if Closed
  remaining   = expected - max(committed, billed)      (forecast not yet on a PO)
  overrun     = expected - budget                      (positive = over budget)
"""
import datetime as dt
import re
from collections import defaultdict

ORDER_TYPES = {"PO", "Sales Order"}
DONE_STATUSES = {"Closed", "Fully Billed"}         # NetSuite PO statuses with nothing more to bill


def num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def month(d) -> str:
    return str(d)[:7] if d else "Undated"


def fill_months(ms: list[str]) -> list[str]:
    real = sorted(m for m in ms if m != "Undated")
    if not real:
        return ["Undated"] if "Undated" in ms else []
    y, m = map(int, real[0].split("-"))
    end, out = real[-1], []
    while True:
        cur = f"{y:04d}-{m:02d}"
        out.append(cur)
        if cur >= end:
            break
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out + (["Undated"] if "Undated" in ms else [])


CASH_KEYS = ("in_actual", "out_actual", "in_future", "out_future", "in_overdue", "out_overdue")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def month_label(m: str) -> str:
    """'2027-01' -> 'Jan 27' (harder to misread than the ISO form)."""
    return f"{MONTHS[int(m[5:7]) - 1]} {m[2:4]}" if m != "Undated" else m


def fy_of(m: str) -> str | None:
    """Australian financial year, 1 Jul - 30 Jun: '2026-10' -> 'FY27'."""
    if m == "Undated":
        return None
    y, mo = int(m[:4]), int(m[5:7])
    return f"FY{(y + 1 if mo >= 7 else y) % 100:02d}"


def build_curve(cash: dict, today: str) -> list[dict]:
    """Month rows with the running position. Past months only ever hold actuals (future amounts dated
    earlier are moved to the current month by the caller), so up to now the curve is the real bank."""
    cur = today[:7]
    months = fill_months(list(cash) + ([cur] if cash else []))
    running = actual = 0.0
    curve = []
    for m in months:
        c = {k: round(cash[m][k], 2) if m in cash else 0.0 for k in CASH_KEYS}
        opening = running
        running += c["in_actual"] + c["in_future"] - c["out_actual"] - c["out_future"]
        actual += c["in_actual"] - c["out_actual"]
        dated = m != "Undated"
        curve.append({"month": m, "label": month_label(m), "fy": fy_of(m), **c,
                      "opening": round(opening, 2), "position": round(running, 2),
                      "actual_position": round(actual, 2) if dated and m <= cur else None,
                      "is_past": dated and m < cur, "is_current": m == cur})
    return curve


def fy_totals(curve: list[dict]) -> list[dict]:
    """Per financial year: actual received / paid, still expected in / out, net and the closing position.
    Undated amounts belong to no year. 'from' says where the data starts when it's a part year."""
    out = {}
    for c in curve:
        if not c["fy"]:
            continue
        f = out.setdefault(c["fy"], {"fy": c["fy"], "received": 0.0, "paid": 0.0, "in_expected": 0.0,
                                     "out_expected": 0.0, "opening": c["opening"], "first": c["month"]})
        f["received"] += c["in_actual"]
        f["paid"] += c["out_actual"]
        f["in_expected"] += c["in_future"]
        f["out_expected"] += c["out_future"]
        f["closing"], f["last"] = c["position"], c["month"]
    for f in out.values():
        for k in ("received", "paid", "in_expected", "out_expected"):
            f[k] = round(f[k], 2)
        f["net_actual"] = round(f["received"] - f["paid"], 2)
        f["net"] = round(f["received"] + f["in_expected"] - f["paid"] - f["out_expected"], 2)
        f["partial"] = not f["first"].endswith("-07") or not f["last"].endswith("-06")
        f["from"] = f"{MONTHS[int(f['first'][5:7]) - 1]} {f['first'][:4]}" if not f["first"].endswith("-07") else None
        f["to"] = f"{MONTHS[int(f['last'][5:7]) - 1]} {f['last'][:4]}" if not f["last"].endswith("-06") else None
    return list(out.values())


def low_point(curve: list[dict]) -> dict | None:
    """Lowest projected position from the current month on (Undated last)."""
    ahead = [c for c in curve if not c["is_past"]]
    return min(ahead, key=lambda c: c["position"]) if ahead else None


def paid_so_far(t: dict) -> float:
    """Amount received / paid on a document: all of it once paid, the part payment while it's still open."""
    return num(t.get("Amount")) if t.get("Paid Date") else num(t.get("Part Paid"))


def bucket(f: dict,linked: list[dict], schedule: list[dict], today: str) -> dict:
    orders = [t for t in linked if t.get("Type") in ORDER_TYPES]
    docs = [t for t in linked if t.get("Type") not in ORDER_TYPES]
    budget = num(f.get("Amount"))
    billed = sum(num(d["Amount"]) for d in docs)
    if f.get("Cost Type") == RECOVERY and f.get("Direction") != "In":
        return recovery(f, linked, docs, budget, billed, today)
    paid = sum(paid_so_far(d) for d in docs)
    pos = {norm(o.get("PO / Order #")) for o in orders} | set(norm(x) for x in re.split(r"[,;\s]+", str(f.get("PO / Order #") or "")) if x)
    ms = sorted([s for s in schedule if norm(s.get("PO / Order #")) in pos], key=lambda s: (str(s.get("PO / Order #")), num(s.get("Seq"))))

    # A PO closed (or fully billed) in NetSuite won't be billed any further: it is worth what was billed on it.
    shut = {norm(o.get("PO / Order #")) for o in orders if str(o.get("Status") or "") in DONE_STATUSES}
    # Also done, though NetSuite still shows it open: billed to 95%+ of its value (in the PO's own currency, so FX
    # differences don't count), or a bill raised without the PO, from the same supplier, within 10% of its value.
    # It carries no open commitment; the PM is asked to close it in NetSuite.
    done_open, matched = [], set()
    no_po_bills = [d for d in docs if not norm(d.get("PO / Order #")) and d.get("Type") == "Bill"]
    for o in orders:
        ref = norm(o.get("PO / Order #"))
        if not ref or ref in shut or f.get("Direction") == "In":
            continue
        on_po = [d for d in docs if norm(d.get("PO / Order #")) == ref]
        if on_po and po_billing(o, on_po)["share"] >= 0.95:
            shut.add(ref)
            done_open.append((o, "billed"))
            continue
        val = num(o.get("Amount"))
        hit = next((d for d in no_po_bills if id(d) not in matched and party_key(d.get("Party")) == party_key(o.get("Party"))
                    and val and abs(num(d.get("Amount")) - val) <= 0.1 * val), None)
        if hit:
            matched.add(id(hit))
            shut.add(ref)
            done_open.append((o, hit))
    live = [o for o in orders if norm(o.get("PO / Order #")) not in shut]
    billed_shut = sum(num(d["Amount"]) for d in docs if norm(d.get("PO / Order #")) in shut)
    if f.get("Direction") == "In":
        face = budget if f.get("PO / Order #") else sum(num(o["Amount"]) for o in live)
    else:
        face = sum(num(o["Amount"]) for o in live)
    committed = face + billed_shut                      # PO value at the PO rate
    # A PO done though still open in NetSuite still covers its whole value (an FX gain isn't "forecast, not ordered").
    for o, why in done_open:
        on_po = sum(num(d["Amount"]) for d in docs if norm(d.get("PO / Order #")) == norm(o.get("PO / Order #")))
        committed += max(0.0, num(o.get("Amount")) - (on_po if why == "billed" else num(why.get("Amount"))))

    # Open commitment: unbilled milestones at the PO rate when we have a schedule (so FX gains/losses on
    # billed milestones are kept, not absorbed); otherwise PO value less what's been billed.
    ms_live = [m for m in ms if norm(m.get("PO / Order #")) not in shut]
    sched_orders = {norm(m.get("PO / Order #")) for m in ms_live}
    open_commit = sum(num(m.get("Amount")) for m in ms_live if not m.get("Billed Doc"))
    unsched = [o for o in live if norm(o.get("PO / Order #")) not in sched_orders]
    # Bills with no PO number (raised standalone instead of from the PO) still use up the line's open POs.
    no_po = sum(num(d["Amount"]) for d in docs if not norm(d.get("PO / Order #")) and id(d) not in matched)
    if unsched or not ms_live:
        on_po = [d for d in docs if norm(d.get("PO / Order #")) and norm(d.get("PO / Order #")) not in sched_orders | shut]
        base = sum(num(o["Amount"]) for o in unsched) if ms_live else face
        open_commit += max(0.0, base - sum(num(d["Amount"]) for d in on_po))
    open_commit = max(0.0, open_commit - no_po) if (live or ms_live) else open_commit
    top = billed + open_commit
    # Forecast not yet on a PO: what the POs (at their own rate) or the bills haven't covered.
    remaining = 0.0 if f.get("Closed") else max(0.0, budget - max(committed, billed))
    expected = top + remaining
    # Exchange-rate movement: billed milestones at the actual rate vs the PO rate.
    fx = round(top - committed, 2) if committed and any(t.get("Currency Amount") for t in linked) else 0.0
    # What a milestone's % is a share of: the PO value (supplier) or the contract line (customer).
    bases = defaultdict(float)
    for o in orders:
        bases[norm(o.get("PO / Order #"))] += num(o["Amount"])
    if f.get("Direction") == "In":
        for p in pos - {""}:
            bases[p] = bases.get(p) or budget
    # Milestones on one PO / order should add up to 100%.
    pct = defaultdict(float)
    for m in ms:
        pct[str(m.get("PO / Order #"))] += num(m.get("Percent"))
    return {
        "id": f.get("_id"), "f": f, "bases": dict(bases),
        "pct_gaps": [(o, t) for o, t in pct.items() if abs(t - 1) > 0.0005],
        "refs": list(dict.fromkeys([x.strip() for x in re.split(r"[,;]", str(f.get("PO / Order #") or "")) if x.strip()]
                                   + [str(o.get("PO / Order #")) for o in orders if o.get("PO / Order #")])),
        "item": f.get("Item"), "direction": f.get("Direction"), "type": f.get("Cost Type") or "Other",
        "party": f.get("Party") or ", ".join(sorted({t.get("Party") or "" for t in linked})) or "",
        "budget": budget, "committed": committed, "billed": billed, "paid": paid,
        "expected": expected, "remaining": remaining, "fx": fx if abs(fx) > 0.5 else 0.0,
        # expected = committed + bill_diff + remaining: the steps the "how the numbers add up" box shows
        "bill_diff": round(top - committed, 2), "foreign": [t for t in docs if t.get("Currency Amount")],
        "open_commit": open_commit, "overrun": expected - budget if budget else 0.0,
        "date": f.get("Expected Date"), "closed": bool(f.get("Closed")), "orders": sorted(pos - {""}),
        "notes": f.get("Notes") or "", "txns": sorted(linked, key=lambda t: str(t.get("Date") or "")),
        "milestones": ms, "done_open": done_open,
        "late": bool(f.get("Expected Date") and str(f["Expected Date"]) < today and not committed and not billed),
    }


RECOVERY = "Supplier recovery"


def recovery(f: dict, linked: list, docs: list, budget: float, billed: float, today: str) -> dict:
    """Money a supplier owes us (a back-charge): the forecast is what we expect back; a credit (or offset bill)
    linked to the line recovers it. It reduces the cost side: expected = -(amount); still to come is negative."""
    owed = abs(budget)
    got = max(0.0, -billed)                                    # credits are negative amounts
    left = 0.0 if f.get("Closed") else max(0.0, owed - got)
    return {"id": f.get("_id"), "f": f, "bases": {}, "pct_gaps": [], "refs": [x.strip() for x in str(f.get("PO / Order #") or "").split(",") if x.strip()],
            "item": f.get("Item"), "direction": f.get("Direction") or "Out", "type": RECOVERY, "party": f.get("Party") or "",
            "budget": -owed, "committed": 0.0, "billed": billed, "paid": sum(paid_so_far(d) for d in docs),
            "expected": -(got + left), "remaining": -left, "fx": 0.0, "bill_diff": 0.0, "foreign": [], "open_commit": 0.0,
            "overrun": 0.0, "date": f.get("Expected Date"), "closed": bool(f.get("Closed")), "orders": [],
            "notes": f.get("Notes") or "", "txns": sorted(linked, key=lambda t: str(t.get("Date") or "")), "milestones": [],
            "done_open": [], "recovery": True, "late": False}


STATUS = (("ordered", "Ordered"), ("to_place", "Still to place"), ("closed", "Closed"), ("unlinked", "Not linked to a line"))


def status(l: dict) -> str:
    """Where a line stands: ordered (has a PO or bills), still to place (nothing in NetSuite yet), closed,
    or unlinked (NetSuite documents not on any forecast line)."""
    if l.get("unassigned"):
        return "unlinked"
    if l["closed"]:
        return "closed"
    return "ordered" if l["txns"] or l["committed"] or l["billed"] else "to_place"


def block_totals(lines: list) -> dict:
    """Column totals for a block of lines, as the forecast editor shows them."""
    t = {k: round(sum(l[k] for l in lines), 2) for k in ("budget", "billed", "paid", "open_commit", "remaining", "expected")}
    t["on_order"] = round(t["billed"] + t["open_commit"], 2)
    t["vs"] = round(t["expected"] - t["budget"], 2)
    t["fx"] = round(sum(l["fx"] for l in lines), 2)
    t["stages"] = stages(t)
    return t


def stages(x: dict) -> dict:
    """The four money stages, mutually exclusive, adding up to the expected total:
    paid | billed, not paid | on order, not billed | forecast, not ordered."""
    return {"paid": round(x["paid"], 2), "unpaid": round(x["billed"] - x["paid"], 2), "on_order": round(x["open_commit"], 2),
            "to_place": round(x["remaining"], 2), "expected": round(x["expected"], 2)}


def fy_documents(txns: list) -> dict:
    """Per financial year, by document Date: customer invoices and supplier bills (credits negative)."""
    out = {}
    for t in txns:
        if t.get("Type") in ORDER_TYPES or not t.get("Date"):
            continue
        f = out.setdefault(fy_of(str(t["Date"])[:7]), {"invoiced": 0.0, "billed": 0.0})
        f["invoiced" if t.get("Direction") == "In" else "billed"] += num(t.get("Amount"))
    return {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in out.items()}


def first_date(txns: list) -> str | None:
    """Earliest date on any transaction (document or paid date): where the loaded history starts."""
    ds = [str(t.get(k))[:10] for t in txns for k in ("Date", "Paid Date") if t.get(k)]
    return min(ds) if ds else None


def unfiled(txns: list) -> int:
    """PDFs attached to Transactions rows that the SharePoint filing task hasn't ticked as Filed yet."""
    return sum(1 for t in txns if t.get("PDF") and not t.get("Filed"))


EXPENSE_GRACE = 14          # days an expense claim can sit unlinked before it's flagged
STALE_DAYS = 90             # an open PO with nothing billed this long after it was due: cancel or chase
RECEIPT_DAYS = 30           # received in NetSuite, no bill for this long: chase the supplier invoice
RECEIVED = {"Pending Bill", "Pending Billing/Partially Received"}
BILL_TYPES = {"Bill", "Bill Credit", "Card"}
_SUFFIX = re.compile(r"\b(pty|ltd|limited|co|company|inc|llc|gmbh|sa|lda|australia|aust)\b\.?", re.I)


def add_days(d: str, n: int) -> str:
    return (dt.date.fromisoformat(d[:10]) + dt.timedelta(days=n)).isoformat()


def nsdate(d) -> str | None:
    """NetSuite's d/m/yyyy -> ISO."""
    try:
        dd, mm, yy = (int(x) for x in str(d).split("/"))
        return dt.date(yy, mm, dd).isoformat()
    except (TypeError, ValueError):
        return None


QUOTE = re.compile(r"\bQ[-\s]?\d{2,6}\b", re.I)


def untagged_flags(views: list, projects: list, untagged: list, quotes: list) -> dict:
    """NetSuite POs, bills and stock issues with no project that look like they belong to one of our jobs:
      memo names the job's code; or the same supplier quote number as a PO tagged to the job; or (live jobs) the
      supplier has a cost line still waiting for its order and the amount is within 50% of it.
    Returns {code: [(text, key)]}; each document goes to one job at most."""
    by_code = {v["code"]: v for v in views}
    job_code = {str(int(float(p["NetSuite Job ID"]))): p["Project"] for p in projects if p.get("NetSuite Job ID")}
    quoted = defaultdict(set)                                    # (code, vendor key) -> quote numbers on tagged POs
    for q in quotes or []:
        c = job_code.get(str(q.get("job")))
        for m in QUOTE.findall(str(q.get("memo") or "")):
            quoted[(c, party_key(q.get("vendor")))].add(norm(m))
    out = defaultdict(list)
    for d in untagged or []:
        if d.get("type") == "PurchOrd" and d.get("status") in DONE_STATUSES:
            continue
        text = f"{d.get('memo') or ''} {d.get('linememo') or ''}".upper()
        vk, amt = party_key(d.get("vendor")), float(d.get("aud") or 0)
        code, why = None, ""
        for c in job_code.values():
            if re.search(rf"\b{c}\b", text.replace(" ", "")) or c in text.replace(" ", ""):
                code, why = c, f"its memo names {c}"
                break
        if not code:
            qs = {norm(m) for m in QUOTE.findall(text)}
            code = next((c for (c, v), refs in quoted.items() if v == vk and qs & refs), None)
            why = f"it quotes the same supplier quote ({', '.join(sorted(qs))}) as a {d.get('vendor')} PO on {code}" if code else ""
        if not code and vk and d.get("type") != "InvAdjst":
            for v in views:
                for l in v["lines_out"]:
                    if (not l.get("unassigned") and not l["closed"] and l["remaining"] > 1 and l["f"].get("Party")
                            and party_key(l["f"]["Party"]) and (party_key(l["f"]["Party"]) in vk or vk in party_key(l["f"]["Party"]))
                            and abs(amt - l["remaining"]) <= 0.5 * l["remaining"]):
                        code, why = v["code"], f"{d.get('vendor')} has “{l['item']}” ({money(l['remaining'])}) still to order here"
                        break
                if code:
                    break
        if code:
            kind = {"PurchOrd": "PO", "VendBill": "Bill", "InvAdjst": "Stock issue"}.get(d.get("type"), d.get("type"))
            fx = f" ({d.get('ccy')} {float(d.get('fx') or 0):,.0f})" if d.get("ccy") and d.get("ccy") != "Australian Dollar" else ""
            out[code].append((f"{kind} {d.get('tranid')} {d.get('vendor') or ''} {money(amt)}{fx} of {nsdate(d.get('trandate')) or ''} "
                              f"has no project in NetSuite, but {why}. Tag it to {code} in NetSuite so it counts here.",
                              str(d.get("id"))))
    return out


def days(d, today: str) -> int | None:
    try:
        return (dt.date.fromisoformat(today[:10]) - dt.date.fromisoformat(str(d)[:10])).days
    except (TypeError, ValueError):
        return None


def party_key(name) -> str:
    """Supplier name without brackets, company suffixes and punctuation, for matching across projects."""
    n = _SUFFIX.sub("", re.sub(r"\([^)]*\)", "", str(name or "")))
    return re.sub(r"[^a-z0-9]", "", n.lower())


def po_refs(f: dict) -> set:
    return {norm(x) for x in re.split(r"[,;\s]+", str(f.get("PO / Order #") or "")) if x.strip()}


def money(x: float) -> str:
    return f"${x:,.0f}"


def housekeeping(code: str, lines: list, pt: list, forecasts: list, txns: list, today: str,
                 invoiced_share: float = 0.0) -> list[tuple]:
    """Bookkeeping checks from the brief (section 3). Returns (kind, text, key) tuples.
      wrong      a PO / customer PO coded to this project that another project's forecast lists
      miscode    a line still to place here, while a PO from that supplier of about that size sits unlinked on another job
      received   PO received in NetSuite, no bill for RECEIPT_DAYS
      stale      PO still open with nothing billed STALE_DAYS after it was due
      standalone a bill not raised from the PO, so the PO still shows open in NetSuite
      closepo    a PO billed in full (or matched by a bill raised without it) that NetSuite still shows open
      nocost     a cost line past its date with nothing in NetSuite, on a job that's mostly invoiced
      custpo     a customer invoice with no customer PO number, or one no contract line lists"""
    out = []
    others = [f for f in forecasts if f.get("Project") and f.get("Project") != code]
    orders = [t for t in pt if t.get("Type") in ORDER_TYPES and t.get("Direction") != "In"]
    bills_on = defaultdict(list)
    for t in pt:
        if t.get("Type") in BILL_TYPES and norm(t.get("PO / Order #")):
            bills_on[norm(t.get("PO / Order #"))].append(t)

    # wrong project: the PO number is on another project's forecast
    for t in pt:
        ref = norm(t.get("PO / Order #"))
        if not ref or t.get("Type") not in ORDER_TYPES | {"Invoice"}:
            continue
        hit = next((f for f in others if f.get("Direction") == t.get("Direction") and ref in po_refs(f)), None)
        if hit:
            out.append(("wrong", f"{t.get('Type')} {t.get('Doc #')} {t.get('Party')} is coded to {code} in NetSuite, but "
                                 f"{hit['Project']}'s line “{hit.get('Item')}” lists {t.get('PO / Order #')}. Wrong project?",
                        t.get("NetSuite ID") or t.get("Doc #")))

    # possible miscoding: a line still to place here; the same supplier has an unlinked PO of about that size elsewhere
    items_of = defaultdict(set)
    for f in forecasts:
        items_of[f.get("Project")].add(f.get("Item"))
    away = [t for t in txns if t.get("Project") != code and t.get("Type") == "PO" and t.get("Direction") != "In"
            and items_of[t.get("Project")] and t.get("Forecast") not in items_of[t.get("Project")]]   # tracked jobs only
    for l in lines:
        if l.get("unassigned") or l["direction"] != "Out" or l["closed"] or l["remaining"] < 1 or not l["f"].get("Party"):
            continue
        k = party_key(l["f"]["Party"])
        for t in away:
            pk = party_key(t.get("Party"))
            if k and pk and (k in pk or pk in k) and abs(num(t.get("Amount")) - l["remaining"]) <= 0.25 * l["remaining"]:
                out.append(("miscode", f"“{l['item']}” ({l['f']['Party']}, {money(l['remaining'])}) isn't ordered yet, but "
                                       f"{t.get('Doc #')} from {t.get('Party')} ({money(num(t.get('Amount')))}) is on "
                                       f"{t.get('Project')} and not on its forecast. Coded to the wrong job?",
                            t.get("NetSuite ID") or t.get("Doc #")))

    # POs the model treats as done although NetSuite still shows them open: ask for them to be closed
    standalone = set()
    for l in lines:
        for o, why in l.get("done_open", []):
            ref = norm(o.get("PO / Order #"))
            standalone.add(ref)
            if not o.get("Status"):                     # NetSuite status unknown: nothing to close
                continue
            if why == "billed":
                out.append(("closepo", f"{o.get('Doc #')} {o.get('Party')} is billed in full but still {o.get('Status') or 'open'} "
                                       f"in NetSuite. Close PO in NS (it isn't counted as still to pay).", ref))
            else:
                out.append(("closepo", f"{o.get('Doc #')} {o.get('Party')} was billed without the PO (bill {why.get('Doc #')}, "
                                       f"{money(num(why.get('Amount')))}), so NetSuite still shows it open. Close PO in NS "
                                       f"(it isn't counted as still to pay).", ref))

    # standalone bills: raised without the PO, so the PO stays open in NetSuite
    for l in lines:
        live = [o for o in l["txns"] if o.get("Type") == "PO" and str(o.get("Status") or "") not in DONE_STATUSES]
        live = [o for o in live if norm(o.get("PO / Order #")) not in standalone]
        if l.get("unassigned") or not live:
            continue
        matched = {id(w) for _, w in l.get("done_open", []) if isinstance(w, dict)}
        for b in l["txns"]:
            if b.get("Type") == "Bill" and not norm(b.get("PO / Order #")) and id(b) not in matched:
                same = [o for o in live if party_key(o.get("Party")) == party_key(b.get("Party"))] or live
                standalone |= {norm(o.get("PO / Order #")) for o in same}
                pos = ", ".join(str(o.get("Doc #")) for o in same)
                out.append(("standalone", f"Bill {b.get('Doc #')} {b.get('Party')} ({money(num(b.get('Amount')))}) wasn't raised "
                                          f"from {pos}, so the PO still shows open in NetSuite. Close PO in NS, or bill from the PO next time.",
                            b.get("NetSuite ID") or b.get("Doc #")))

    # open POs: received but not billed, or stale (a PO with a standalone bill is already flagged above)
    due_of = {}
    for l in lines:
        for m in l["milestones"]:
            o = norm(m.get("PO / Order #"))
            due_of[o] = max(due_of.get(o, ""), str(m.get("Expected Date") or "")[:10])
        for o in l["orders"]:
            due_of[o] = max(due_of.get(o, ""), str(l["date"] or "")[:10])
    for o in orders:
        ref = norm(o.get("PO / Order #"))
        if not ref or ref in standalone or str(o.get("Status") or "") in DONE_STATUSES:
            continue
        bills = bills_on.get(ref, [])
        unbilled = num(o.get("Amount")) - sum(num(b.get("Amount")) for b in bills)
        if unbilled <= max(50.0, 0.01 * num(o.get("Amount"))):
            continue
        last_bill = max((str(b.get("Date") or "")[:10] for b in bills), default="")
        what = f"{o.get('Doc #')} {o.get('Party')}"
        if str(o.get("Status") or "") in RECEIVED:
            since = max(last_bill, str(o.get("Date") or "")[:10])
            if (days(since, today) or 0) > RECEIPT_DAYS:
                out.append(("received", f"{what}: received in NetSuite but {money(unbilled)} not billed. Chase the supplier invoice?", ref))
            continue
        since = max(last_bill, due_of.get(ref, ""), str(o.get("Date") or "")[:10])
        quiet = max(last_bill, str(o.get("Date") or "")[:10])         # nothing billed for 6 months, whatever the dates say
        if (days(since, today) or 0) > STALE_DAYS or (not bills and (days(quiet, today) or 0) > 2 * STALE_DAYS):
            since = since if (days(since, today) or 0) > STALE_DAYS else quiet
            done = "nothing billed" if not bills else f"{money(unbilled)} still unbilled"
            out.append(("stale", f"{what}: open with {done} since {since}. Close the PO in NetSuite, or chase?", ref))

    # a cost line due by now with nothing at all in NetSuite, on a job that's mostly invoiced: cost may be missing
    if invoiced_share >= 0.8:
        for l in lines:
            if (l["direction"] == "Out" and not l.get("unassigned") and not l.get("recovery") and not l["txns"]
                    and l["budget"] > 0 and (l["closed"] or not l["date"] or str(l["date"])[:10] < today)):
                out.append(("nocost", f"“{l['item']}” ({money(l['budget'])}) has no PO, bill or stock issue in NetSuite"
                                      + (" but is ticked Closed" if l["closed"] else f" and was due {str(l['date'])[:10]}" if l["date"]
                                         else " and has no expected date")
                                      + f", and the job is {invoiced_share * 100:.0f}% invoiced. Cost may be missing from NetSuite.",
                            l["item"]))

    # customer invoices: missing or unknown customer PO number
    mine = set()
    for l in lines:
        if l["direction"] == "In" and not l.get("unassigned"):
            mine |= po_refs(l["f"])
    mine |= {norm(t.get("PO / Order #")) for t in pt if t.get("Type") == "Sales Order"}
    for t in pt:
        if t.get("Type") != "Invoice":
            continue
        ref = norm(t.get("PO / Order #"))
        if not ref:
            out.append(("custpo", f"Invoice {t.get('Doc #')} ({money(num(t.get('Amount')))}) has no customer PO number.", t.get("Doc #")))
        elif ref not in mine:
            out.append(("custpo", f"Invoice {t.get('Doc #')} quotes customer PO {t.get('PO / Order #')}, which no contract line lists. "
                                  f"Add it to the line, or check it's this customer's PO.", t.get("Doc #")))
    return out


def shared_accounts(projects: list, views: list):
    """Live projects that share an Unearned Income or WIP account: the P&L can't split them by account."""
    by_code = {v["code"]: v for v in views}
    for col, label in (("Unearned Acct ID", "Unearned Income"), ("WIP Acct ID", "WIP")):
        groups = defaultdict(list)
        for p in projects:
            a = str(p.get(col) or "").strip()
            if a and p.get("Project") in by_code:
                try:
                    a = str(int(float(a)))
                except ValueError:
                    pass
                groups[a].append(p["Project"])
        for acct, codes in groups.items():
            if len(codes) < 2:
                continue
            for c in codes:
                v, rest = by_code[c], ", ".join(x for x in codes if x != c)
                text = f"The {label} account (ID {acct}) is shared with {rest}, so the P&L can't split them by account."
                v["flags"].append(("shared", text, None))
                v["flag_meta"].append({"key": f"shared:{label}:{acct}", "kind": "shared", "text": text, "amount": None, "po": None})


def project_view(p: dict, forecasts: list, txns: list, schedule: list, today: str | None = None) -> dict:
    today = today or dt.date.today().isoformat()
    code = p["Project"]
    pf = [f for f in forecasts if f.get("Project") == code]
    pt = [t for t in txns if t.get("Project") == code]
    ps = [s for s in schedule if s.get("Project") == code]
    items = {f["Item"] for f in pf}
    lines = [bucket(f, [t for t in pt if t.get("Forecast") == f["Item"]], ps, today) for f in pf]

    # Anything NetSuite has that isn't linked to a forecast still counts.
    loose = [t for t in pt if not t.get("Forecast") or t.get("Forecast") not in items]
    for direction in ("Out", "In"):
        lt = [t for t in loose if t.get("Direction") == direction]
        if lt:
            u = bucket({"Item": "Unassigned", "Direction": direction, "Closed": True, "Cost Type": "Other"}, lt, ps, today)
            u["unassigned"] = True
            lines.append(u)
    for l in lines:
        # A stable key for the page (drawers, panels, ledger rows): the sheet row id, else the item name.
        l["key"] = (f"u-{l['direction'].lower()}" if l.get("unassigned")
                    else str(l["id"]) if l["id"] is not None else "f-" + norm(l["item"]).lower())
        l["status"] = status(l)
        l["stages"] = stages(l)

    def tot(direction, k):
        return round(sum(l[k] for l in lines if l["direction"] == direction), 2)

    rev = {k: tot("In", k) for k in ("budget", "expected", "billed", "paid", "remaining", "open_commit")}
    cost = {k: tot("Out", k) for k in ("budget", "expected", "billed", "paid", "remaining", "open_commit", "overrun")}
    cost["fx"] = tot("Out", "fx")
    contract = num(p.get("Contract Value")) or rev["expected"]
    labour = num(p.get("Internal Labour"))
    gm = contract - cost["expected"]
    # Contract Value already includes variations: split it by the incoming forecast lines.
    ins = [l for l in lines if l["direction"] == "In" and not l.get("unassigned")]
    if ins:
        variations = round(sum(l["expected"] for l in ins if l["type"] == "Variation"), 2)
        contract_orig = round(sum(l["expected"] for l in ins if l["type"] != "Variation"), 2)
    else:
        variations, contract_orig = 0.0, contract
    contract_diff = round(contract - contract_orig - variations, 2)     # Contract Value not on any In line

    # ---- the ledger: every payment, past and to come, one row each. The cash curve is built from it.
    # Done: bills / invoices with a paid date. To come: unpaid bills / invoices (by due date), unbilled
    # milestones and open PO balances (on order / to invoice), and forecast not yet on a PO.
    # Anything still to come that was due before today is late: it can only happen from now on, so the
    # curve puts it in the current month (marked overdue) and past months hold actuals only.
    cur = today[:7]
    line_of = {id(t): l for l in lines for t in l["txns"]}
    ledger = {"in": [], "out": []}

    def row(side, l, kind, date, amt, label, party, doc=None, done=False):
        if abs(amt) < 0.005:
            return
        date = str(date)[:10] if date else None
        ledger[side].append({
            "side": side, "kind": kind, "done": done, "date": date, "amount": round(amt, 2),
            "line": l["item"] if l else "Unassigned", "label": label, "party": party or (l["party"] if l else ""),
            "key": l["key"] if l else f"u-{side}", "doc": doc,
            "overdue": (not done) and bool(date) and date < today})

    for t in pt:
        if t.get("Type") in ORDER_TYPES:
            continue
        side = "in" if t.get("Direction") == "In" else "out"
        l = line_of.get(id(t))
        ms = next((m for m in (l["milestones"] if l else []) if m.get("Billed Doc") and str(m.get("Billed Doc")) in (str(t.get("Doc #")), str(t.get("NetSuite ID")))), None)
        label = f"{ms.get('Milestone')}" if ms else f"{t.get('Type')} {t.get('Doc #')}"
        if t.get("Paid Date"):
            row(side, l, "paid", t["Paid Date"], num(t["Amount"]), label, t.get("Party"), t, done=True)
        else:                                   # open: any part payment so far is done, the rest still to come
            part = num(t.get("Part Paid"))
            if part:
                row(side, l, "paid", t.get("Part Paid Date") or t.get("Date"), part, f"{label} (part paid)", t.get("Party"), t, done=True)
            row(side, l, "billed", t.get("Due Date") or today, num(t["Amount"]) - part, label, t.get("Party"), t)
    for l in lines:
        side = "in" if l["direction"] == "In" else "out"
        open_amt = l["open_commit"]
        for m in l["milestones"]:
            if open_amt <= 0.005:
                break
            if not m.get("Billed Doc"):
                a = min(num(m.get("Amount")), open_amt)
                row(side, l, "order", m.get("Expected Date") or l["date"], a, m.get("Milestone") or "Milestone", m.get("Party"))
                open_amt -= a
        if open_amt > 0.005:
            row(side, l, "order", l["date"], open_amt, "Balance on order, not billed" if side == "out" else "Contract, not invoiced", None)
        if l["remaining"] > 0.005:
            row(side, l, "forecast", l["date"], l["remaining"], "Forecast, not ordered yet", None)
        elif l.get("recovery") and l["remaining"] < -0.005:
            row(side, l, "forecast", l["date"], l["remaining"], "Owed back by the supplier", None)
    for side in ledger:
        ledger[side].sort(key=lambda r: (not r["done"], r["date"] or "9999", r["line"]))

    cash = defaultdict(lambda: dict.fromkeys(CASH_KEYS, 0.0))
    for side, rows in ledger.items():
        for r in rows:
            if r["done"]:
                cash[month(r["date"])][f"{side}_actual"] += r["amount"]
                continue
            m = month(r["date"])
            if r["overdue"] and m < cur:
                m = cur
            cash[m][f"{side}_future"] += r["amount"]
            if r["overdue"]:
                cash[m][f"{side}_overdue"] += r["amount"]
    curve = build_curve(cash, today)

    # ---- things that need a person. Each has a stable key (so it can be acknowledged and stay acknowledged
    # while nothing material changes) and, for overruns, the amount it was raised at.
    flags, flag_meta = [], []

    def flag(kind, text, t, key, amount=None, po=None):
        flags.append((kind, text, t))
        flag_meta.append({"key": f"{kind}:{key}", "kind": kind, "text": text, "amount": amount, "po": po})

    for l in lines:
        if l.get("unassigned"):
            for t in l["txns"]:
                age = days(t.get("Date"), today)
                if t.get("Type") == "Expense" and age is not None and age <= EXPENSE_GRACE:
                    continue                    # give the PM a fortnight to link an expense claim
                flag("link", f"{t.get('Type')} {t.get('Doc #')} from {t.get('Party')} "
                             f"(${num(t.get('Amount')):,.0f}) isn't linked to a forecast"
                             + (f" ({age} days)" if t.get("Type") == "Expense" and age else ""), t, t.get("NetSuite ID") or t.get("Doc #"))
            continue
        if str(l["f"].get("Notes") or "").startswith("Auto-added by the sync"):
            flag("newvar", f"New variation line added automatically: “{l['item']}” ${l['budget']:,.0f}. "
                           f"{l['f']['Notes'].split(': ', 1)[-1].capitalize()}.", None, l["item"])
        if l["late"]:
            flag("late", f"{l['item']}: expected {l['date']} but no PO yet", None, l["item"])
        if l["overrun"] > 0.5 and l["direction"] == "Out":
            fx = l["fx"] if l["fx"] > 0 else 0.0
            why = (", all exchange-rate movement" if abs(l["overrun"] - fx) < 1 else
                   f" (${fx:,.0f} of it exchange rate)" if fx else "")
            flag("over", f"{l['item']}: ${l['overrun']:,.0f} over forecast{why}", None, l["item"], round(l["overrun"], 2))
        for o, t in l["pct_gaps"]:
            flag("terms", f"{l['item']}: {o} milestones add up to {t * 100:g}%, not 100%", None, f"{l['item']}|{o}|pct", po=o)
        for m in (l["milestones"] if l["open_commit"] > 0.5 else []):    # nothing left to bill: terms don't matter
            if not m.get("Confirmed") and not m.get("Billed Doc"):
                flag("terms", f"{m.get('PO / Order #')} {m.get('Party')}: payment terms need checking "
                              f"({m.get('Terms Text') or 'not read'})", None, m.get("PO / Order #"), po=m.get("PO / Order #"))
                break
    for t in pt:
        if (t.get("Type") == "Invoice" and not t.get("Paid Date") and t.get("Due Date")
                and str(t["Due Date"]) < today):
            owed, part = num(t.get("Amount")) - num(t.get("Part Paid")), num(t.get("Part Paid"))
            flag("overdue", f"Invoice {t.get('Doc #')} ${owed:,.0f} overdue since {t['Due Date']}"
                            + (f" (${part:,.0f} of ${num(t.get('Amount')):,.0f} received)" if part else ""), None, t.get("Doc #"))
    inv_share = rev["billed"] / contract if contract else 0.0
    for kind, text, key in housekeeping(code, lines, pt, forecasts, txns, today, inv_share):
        flag(kind, text, None, key)

    by_type = defaultdict(float)
    for l in lines:
        if l["direction"] == "Out":
            by_type[l["type"]] += l["expected"]

    return {
        "code": code, "name": p.get("Name") or "", "pm": p.get("PM") or "", "status": p.get("Status") or "",
        "contract": contract, "contract_orig": contract_orig, "variations": variations,
        "contract_diff": contract_diff if abs(contract_diff) >= 0.5 else 0.0,
        "labour": labour, "rev": rev, "cost": cost,
        "gm": gm, "gm_pct": gm / contract if contract else 0,
        "nm": gm - labour, "nm_pct": (gm - labour) / contract if contract else 0,
        "cash_position": rev["paid"] - cost["paid"],
        "lines_out": sorted([l for l in lines if l["direction"] == "Out"],
                            key=lambda l: ([k for k, _ in STATUS].index(l["status"]), -l["expected"])),
        "lines_in": [l for l in lines if l["direction"] == "In"],
        "forecast_lines": [l for l in lines if not l.get("unassigned")],      # sheet order, for the editor
        # The forecast editor: money in, then money out grouped by status (sheet order within a group).
        "blocks": [{"direction": d, "totals": block_totals([l for l in lines if l["direction"] == d]),
                    "groups": [{"key": k, "label": lab, "lines": g, "totals": block_totals(g)}
                               for k, lab in STATUS
                               if (g := [l for l in lines if l["direction"] == d and l["status"] == k])]}
                   for d in ("In", "Out")],
        "curve": curve, "fy": fy_totals(curve), "fy_now": fy_of(today[:7]), "low": low_point(curve), "flags": flags, "flag_meta": flag_meta, "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "unfiled": unfiled(pt),
        "ledger": ledger, "position": position(ledger, today), "ledger_lines": ledger_lines(lines, ledger, fy_start(today)),
        "money": {"in": stages(rev), "out": stages(cost)},
        "fy_docs": fy_documents(pt), "first_date": first_date(pt),
    }


def ledger_lines(lines: list, ledger: dict, start: str = "") -> dict:
    """The ledger grouped by forecast line, in Forecasts-sheet order (unlinked documents last), like the old
    Smartsheet cashflow sheets: a line, then its payments by date (done first)."""
    out = {}
    for side, d in (("in", "In"), ("out", "Out")):
        groups = []
        for l in sorted([l for l in lines if l["direction"] == d], key=lambda l: bool(l.get("unassigned"))):
            key = l["key"]
            rows = sorted([r for r in ledger[side] if r["key"] == key], key=lambda r: (not r["done"], r["date"] or "9999"))
            groups.append({"key": key, "line": l, "rows": rows,
                           "done": round(sum(r["amount"] for r in rows if r["done"]), 2),
                           "done_fy": round(sum(r["amount"] for r in rows if r["done"] and (r["date"] or "") >= start), 2),
                           "next": next((r["date"] for r in rows if not r["done"] and r["date"]), None),
                           "tocome": round(sum(r["amount"] for r in rows if not r["done"]), 2),
                           "overdue": any(r["overdue"] for r in rows)})
        out[side] = groups
    return out


def fy_start(today: str) -> str:
    """First day of the financial year `today` falls in: 2026-10-06 -> 2026-07-01 (FY27)."""
    y, m = int(today[:4]), int(today[5:7])
    return f"{y if m >= 7 else y - 1}-07-01"


def position(ledger: dict, today: str | None = None) -> dict:
    """The cashflow statement: received - paid = cash now; + still to receive - still to pay = final position.
    With `today`, cash now is split at the start of this financial year:
    opening (received - paid before 1 Jul) + this FY (received - paid since) = cash now."""
    s = {f"{side}_{w}": round(sum(r["amount"] for r in rows if r["done"] == (w == "done")), 2)
         for side, rows in ledger.items() for w in ("done", "tocome")}
    s["in_overdue"] = round(sum(r["amount"] for r in ledger["in"] if r["overdue"]), 2)
    s["out_overdue"] = round(sum(r["amount"] for r in ledger["out"] if r["overdue"]), 2)
    s["now"] = round(s["in_done"] - s["out_done"], 2)
    s["final"] = round(s["now"] + s["in_tocome"] - s["out_tocome"], 2)
    if today:
        start = fy_start(today)
        for side in ("in", "out"):
            s[f"{side}_before"] = round(sum(r["amount"] for r in ledger[side] if r["done"] and (r["date"] or "") < start), 2)
            s[f"{side}_fy"] = round(s[f"{side}_done"] - s[f"{side}_before"], 2)
        s["opening"] = round(s["in_before"] - s["out_before"], 2)
        s["fy_net"] = round(s["in_fy"] - s["out_fy"], 2)
    return s


LIVE_OUT = {"Closed", "Complete"}      # Closed: excluded everywhere. Complete: finished, kept for FY history only.


def merged_curve(views: list, today: str) -> list[dict]:
    merged = defaultdict(lambda: dict.fromkeys(CASH_KEYS, 0.0))
    for v in views:
        for c in v["curve"]:
            for k in CASH_KEYS:
                merged[c["month"]][k] += c[k]
    return build_curve(merged, today)


def fy_summary(views: list, today: str) -> list[dict]:
    """Financial years across projects, on two bases: cash (received / paid by paid date) and documents
    (invoices / bills by document date). Actuals only. A year the loaded history starts part-way through
    says so ('from'), derived from the earliest transaction date; the current year is to date."""
    starts = [v["first_date"] for v in views if v["first_date"]]
    if not starts:
        return []
    first, now = min(starts), fy_of(today[:7])
    fys = sorted({fy_of(first[:7]), now} | {f for v in views for f in v["fy_docs"]}
                 | {f["fy"] for v in views for f in v["fy"] if f["received"] or f["paid"]})
    fys = [f for f in fys if f and f <= now]
    out = []
    for fy in fys:
        y = 2000 + int(fy[2:])
        start, end = f"{y - 1}-07-01", f"{y}-06-30"
        rows = []
        for v in views:
            cash = next((f for f in v["fy"] if f["fy"] == fy), None)
            docs = v["fy_docs"].get(fy, {})
            r = {"code": v["code"], "name": v["name"], "status": v["status"] or "Live",
                 "received": cash["received"] if cash else 0.0, "paid": cash["paid"] if cash else 0.0,
                 "invoiced": docs.get("invoiced", 0.0), "billed": docs.get("billed", 0.0)}
            r["net"], r["gross"] = round(r["received"] - r["paid"], 2), round(r["invoiced"] - r["billed"], 2)
            if any(abs(r[k]) >= 0.5 for k in ("received", "paid", "invoiced", "billed")):
                rows.append(r)
        t = {k: round(sum(r[k] for r in rows), 2) for k in ("received", "paid", "net", "invoiced", "billed", "gross")}
        d = dt.date.fromisoformat(first)
        out.append({"fy": fy, "start": start, "end": end, "current": fy == now, "projects": rows, **t,
                    "from": f"{MONTHS[d.month - 1]} {d.year}" if start < first <= end else None,
                    "to": f"{MONTHS[int(today[5:7]) - 1]} {today[:4]}" if fy == now else None})
    return out


def portfolio(projects, forecasts, txns, schedule, today=None):
    """Live projects drive everything current (tabs, KPIs, table, chart, attention). Complete projects are
    added back for financial-year history only. Closed projects are left out entirely."""
    today = today or dt.date.today().isoformat()
    live = [p for p in projects if p.get("Status") not in LIVE_OUT]
    done = [p for p in projects if p.get("Status") == "Complete"]
    views = [project_view(p, forecasts, txns, schedule, today) for p in live]
    complete = [project_view(p, forecasts, txns, schedule, today) for p in done]
    shared_accounts(live, views)
    fy_views = views + complete
    keys = ("contract", "contract_orig", "variations", "contract_diff", "gm", "nm", "labour", "cash_position")
    totals = {k: sum(v[k] for v in views) for k in keys}
    totals["invoiced"] = sum(v["rev"]["billed"] for v in views)
    totals["received"] = sum(v["rev"]["paid"] for v in views)
    totals["cost_expected"] = sum(v["cost"]["expected"] for v in views)
    totals["cost_budget"] = sum(v["cost"]["budget"] for v in views)
    totals["cost_fx"] = sum(v["cost"]["fx"] for v in views)
    totals["cost_paid"] = sum(v["cost"]["paid"] for v in views)
    totals["gm_pct"] = totals["gm"] / totals["contract"] if totals["contract"] else 0
    totals["nm_pct"] = totals["nm"] / totals["contract"] if totals["contract"] else 0
    totals["flags"] = sum(len(v["flags"]) for v in views)
    totals["unfiled"] = sum(v["unfiled"] for v in views)
    totals["money"] = {d: {k: round(sum(v["money"][d][k] for v in views), 2) for k in ("paid", "unpaid", "on_order", "to_place", "expected")}
                       for d in ("in", "out")}
    totals["position"] = {k: round(sum(v["position"][k] for v in views), 2)
                          for k, x in (views[0]["position"].items() if views else []) if isinstance(x, (int, float))}
    now_fy = fy_of(today[:7])
    totals["fy_docs"] = {k: round(sum(v["fy_docs"].get(now_fy, {}).get(k, 0.0) for v in views), 2) for k in ("invoiced", "billed")}
    curve = merged_curve(views, today)
    curve_fy = merged_curve(fy_views, today)
    return {"views": views, "complete": complete, "totals": totals, "curve": curve, "curve_fy": curve_fy,
            "fy": fy_totals(curve_fy), "fy_now": fy_of(today[:7]), "fy_all": fy_summary(fy_views, today),
            "low": low_point(curve)}


# ---- FY budget tracking: Systems Sales (4071-4079) budget vs revenue recognised in the P&L

def fy_months(fy: str) -> list[str]:
    """NetSuite period names for a financial year: 'FY27' -> ['Jul 2026', ..., 'Jun 2027']."""
    y = 2000 + int(fy[2:])
    return [f"{MONTHS[m - 1]} {y - 1 if m >= 7 else y}" for m in (7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6)]


def budget_view(b: dict | None, views: list, today: str, scheduled: dict | None = None,
                formula_by_month: dict | None = None) -> dict | None:
    """Budget vs actual for the year, and how much of the rest is already won.

    actual        = Systems Sales revenue recognised in the P&L (NetSuite posting periods)
    closed months = months before the current one; the current month is shown separately (part month)
    secured       = contract value of live projects not yet recognised (could slip past 30 June)
    outlook       = actual to date + secured;  gap = full-year budget - outlook (needs new work)"""
    if not b:
        return None
    months = fy_months(b["fy"])
    cur = f"{MONTHS[int(today[5:7]) - 1]} {today[:4]}"
    ci = months.index(cur) if cur in months else len(months)
    rows = [{"month": m, "label": m[:3] + " " + m[-2:], "budget": round(b["budget"].get(m, 0.0), 2),
             "actual": round(b["actual"].get(m, 0.0), 2), "closed": i < ci, "current": i == ci,
             "formula": round((formula_by_month or {}).get(m, 0.0), 2)} for i, m in enumerate(months)]
    closed = [r for r in rows if r["closed"]]
    now = next((r for r in rows if r["current"]), None)
    t = {"budget_year": round(sum(r["budget"] for r in rows), 2),
         "budget_closed": round(sum(r["budget"] for r in closed), 2), "actual_closed": round(sum(r["actual"] for r in closed), 2),
         "budget_current": now["budget"] if now else 0.0, "actual_current": now["actual"] if now else 0.0}
    t["actual_ytd"] = round(t["actual_closed"] + t["actual_current"], 2)
    t["vs_closed"] = round(t["actual_closed"] - t["budget_closed"], 2)
    t["pct_closed"] = t["actual_closed"] / t["budget_closed"] if t["budget_closed"] else 0.0
    rec = b.get("recognised", {})
    projects = []
    for v in views:
        r = rec.get(v["code"], {"all": 0.0, "fy": 0.0})
        projects.append({"code": v["code"], "name": v["name"], "contract": v["contract"], "recognised": r["all"], "fy": r["fy"],
                         "before": round(r["all"] - r["fy"], 2), "remaining": round(max(0.0, v["contract"] - r["all"]), 2)})
    t["secured"] = round(sum(p["remaining"] for p in projects), 2)
    t["projects_fy"] = round(sum(r["fy"] for r in rec.values()), 2)        # live + complete projects in the app
    t["other_fy"] = round(t["actual_ytd"] - t["projects_fy"], 2)           # Systems sales not on a project here
    # Rest of the year by the P&L formula (from the current month), else every remaining contract dollar.
    if scheduled is not None:
        for p in projects:
            p["scheduled_rest"] = scheduled.get(p["code"], 0.0)
        t["scheduled_rest"] = round(sum(scheduled.values()), 2)
        t["outlook"] = round(t["actual_closed"] + t["scheduled_rest"], 2)
    else:
        t["scheduled_rest"] = None
        t["outlook"] = round(t["actual_ytd"] + t["secured"], 2)
    t["gap"] = round(t["budget_year"] - t["outlook"], 2)
    return {"fy": b["fy"], "month": cur, "at": b.get("at"), "rows": rows, "projects": projects, **t}


# ---- acknowledging "needs attention" items

def still_acknowledged(meta: dict, ack: dict) -> bool:
    """An acknowledgement holds until the item changes materially: an overrun that has grown by more than
    10% + $500 since it was acknowledged comes back."""
    if meta["kind"] == "over" and meta.get("amount") is not None:
        was = num(ack.get("Amount"))
        return meta["amount"] <= was * 1.1 + 500
    return True


def apply_acknowledgements(views: list, log: list[dict]) -> int:
    """Split each project's flags into open ones and acknowledged ones (from the Flag Log). Returns the open count."""
    by_key = {str(r.get("Flag")): r for r in log if r.get("Flag")}
    total = 0
    for v in views:
        open_, open_meta, done = [], [], []
        for f, m in zip(v["flags"], v["flag_meta"]):
            ack = by_key.get(f"{v['code']}:{m['key']}")
            if ack and still_acknowledged(m, ack):
                done.append({**m, "ack": ack})
            else:
                open_.append(f)
                open_meta.append(m)
        v["flags"], v["flag_meta"], v["flags_ack"] = open_, open_meta, done
        total += len(open_)
    return total


# ---- completed projects: final figures from NetSuite documents (no forecasts needed)
def completed_view(v: dict, p: dict, txns: list) -> dict:
    """What a finished job made, document basis: invoiced vs costs billed, what's still owed either way,
    margin, by financial year and by supplier, plus any POs still open in NetSuite to close out."""
    code = v["code"]
    pt = [t for t in txns if t.get("Project") == code]
    docs = [t for t in pt if t.get("Type") not in ORDER_TYPES]
    ins = sorted([t for t in docs if t.get("Direction") == "In"], key=lambda t: str(t.get("Date") or ""))
    outs = sorted([t for t in docs if t.get("Direction") != "In"], key=lambda t: str(t.get("Date") or ""))
    # Credits (credit notes, bill credits) are settled by being applied, so they count as received / paid.
    def got(t):                               # received / paid so far; credits are settled by being applied
        return num(t.get("Amount")) if t.get("Paid Date") or num(t.get("Amount")) < 0 else num(t.get("Part Paid"))
    s = lambda rows, paid=None: round(sum(num(t.get("Amount")) if paid is None else got(t) if paid
                                          else num(t.get("Amount")) - got(t) for t in rows), 2)
    invoiced, costs = s(ins), s(outs)
    labour = num(p.get("Internal Labour"))
    gm = round(invoiced - costs, 2)
    suppliers = defaultdict(lambda: {"amount": 0.0, "docs": 0})
    for t in outs:
        x = suppliers[t.get("Party") or "Unknown"]
        x["amount"] += num(t.get("Amount"))
        x["docs"] += 1
    by_supplier = sorted(({"party": k, "amount": round(x["amount"], 2), "docs": x["docs"]} for k, x in suppliers.items()),
                         key=lambda x: -x["amount"])
    fys = []
    for fy, d in sorted(v["fy_docs"].items(), key=lambda x: x[0] or ""):
        if fy:
            fys.append({"fy": fy, "invoiced": d.get("invoiced", 0.0), "billed": d.get("billed", 0.0),
                        "gm": round(d.get("invoiced", 0.0) - d.get("billed", 0.0), 2)})
    dates = [str(t.get("Date"))[:10] for t in docs if t.get("Date")]
    open_pos = [po_billing(o, [t for t in outs if norm(t.get("PO / Order #")) == norm(o.get("PO / Order #"))])
                for o in pt if o.get("Type") == "PO" and str(o.get("Status") or "") not in DONE_STATUSES]
    return {
        "code": code, "name": v["name"], "pm": v["pm"], "contract": num(p.get("Contract Value")),
        "first": min(dates) if dates else None, "last": max(dates) if dates else None,
        "invoiced": invoiced, "received": s(ins, True), "owed_to_us": s(ins, False),
        "costs": costs, "paid": s(outs, True), "owed_by_us": s(outs, False),
        "gm": gm, "gm_pct": gm / invoiced if invoiced else 0.0,
        "labour": labour, "nm": round(gm - labour, 2) if labour else None,
        "nm_pct": (gm - labour) / invoiced if labour and invoiced else None,
        "fys": fys, "by_supplier": by_supplier, "invoices": ins, "costs_docs": outs, "open_pos": open_pos,
        "untagged": [t for k, t, _ in v["flags"] if k == "untagged"],
    }


def fx_amount(t: dict) -> tuple[str, float] | None:
    """'US Dollar 107,240.00' -> ('US Dollar', 107240.0); None for AUD documents."""
    m = re.match(r"^(.*?)\s+(-?[\d,]+(?:\.\d+)?)$", str(t.get("Currency Amount") or "").strip())
    return (m.group(1), float(m.group(2).replace(",", ""))) if m else None


def po_billing(o: dict, bills: list) -> dict:
    """How much of a PO has been billed. In the PO's own currency when it isn't AUD (so exchange-rate
    movement doesn't look like an unbilled balance); the AUD remainder at the PO rate."""
    value = num(o.get("Amount"))
    billed_aud = round(sum(num(b.get("Amount")) for b in bills), 2)
    po_fx, share = fx_amount(o), None
    if po_fx and po_fx[1]:
        same = [fx_amount(b) for b in bills]
        if all(x and x[0] == po_fx[0] for x in same):
            share = sum(x[1] for x in same) / po_fx[1]
    if share is None:
        share = billed_aud / value if value else 0.0
    unbilled = round(max(0.0, value * (1 - share)), 2)
    return {**o, "billed": billed_aud, "share": share, "unbilled": unbilled, "bills": len(bills),
            "fully_billed": share >= 0.995}


def completed_totals(rows: list) -> dict:
    t = {k: round(sum(r[k] for r in rows), 2) for k in ("invoiced", "received", "owed_to_us", "costs", "paid", "owed_by_us", "gm")}
    t["gm_pct"] = t["gm"] / t["invoiced"] if t["invoiced"] else 0.0
    return t

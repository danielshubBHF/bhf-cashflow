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


def bucket(f: dict,linked: list[dict], schedule: list[dict], today: str) -> dict:
    orders = [t for t in linked if t.get("Type") in ORDER_TYPES]
    docs = [t for t in linked if t.get("Type") not in ORDER_TYPES]
    budget = num(f.get("Amount"))
    billed = sum(num(d["Amount"]) for d in docs)
    paid = sum(num(d["Amount"]) for d in docs if d.get("Paid Date"))
    pos = {norm(o.get("PO / Order #")) for o in orders} | set(norm(x) for x in re.split(r"[,;\s]+", str(f.get("PO / Order #") or "")) if x)
    ms = sorted([s for s in schedule if norm(s.get("PO / Order #")) in pos], key=lambda s: (str(s.get("PO / Order #")), num(s.get("Seq"))))

    # A PO closed (or fully billed) in NetSuite won't be billed any further: it is worth what was billed on it.
    shut = {norm(o.get("PO / Order #")) for o in orders if str(o.get("Status") or "") in DONE_STATUSES}
    live = [o for o in orders if norm(o.get("PO / Order #")) not in shut]
    billed_shut = sum(num(d["Amount"]) for d in docs if norm(d.get("PO / Order #")) in shut)
    if f.get("Direction") == "In":
        face = budget if f.get("PO / Order #") else sum(num(o["Amount"]) for o in live)
    else:
        face = sum(num(o["Amount"]) for o in live)
    committed = face + billed_shut                      # PO value at the PO rate

    # Open commitment: unbilled milestones at the PO rate when we have a schedule (so FX gains/losses on
    # billed milestones are kept, not absorbed); otherwise PO value less what's been billed.
    ms_live = [m for m in ms if norm(m.get("PO / Order #")) not in shut]
    sched_orders = {norm(m.get("PO / Order #")) for m in ms_live}
    open_commit = sum(num(m.get("Amount")) for m in ms_live if not m.get("Billed Doc"))
    unsched = [o for o in live if norm(o.get("PO / Order #")) not in sched_orders]
    # Bills with no PO number (raised standalone instead of from the PO) still use up the line's open POs.
    no_po = sum(num(d["Amount"]) for d in docs if not norm(d.get("PO / Order #")))
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
        "milestones": ms,
        "late": bool(f.get("Expected Date") and str(f["Expected Date"]) < today and not committed and not billed),
    }


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
        ms = next((m for m in (l["milestones"] if l else []) if m.get("Billed Doc") and m.get("Billed Doc") == t.get("Doc #")), None)
        label = f"{ms.get('Milestone')}" if ms else f"{t.get('Type')} {t.get('Doc #')}"
        if t.get("Paid Date"):
            row(side, l, "paid", t["Paid Date"], num(t["Amount"]), label, t.get("Party"), t, done=True)
        else:
            row(side, l, "billed", t.get("Due Date") or today, num(t["Amount"]), label, t.get("Party"), t)
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

    # ---- things that need a person
    flags = []
    for l in lines:
        if l.get("unassigned"):
            for t in l["txns"]:
                flags.append(("link", f"{t.get('Type')} {t.get('Doc #')} from {t.get('Party')} "
                                      f"(${num(t.get('Amount')):,.0f}) isn't linked to a forecast", t))
            continue
        if l["late"]:
            flags.append(("late", f"{l['item']}: expected {l['date']} but no PO yet", None))
        if l["overrun"] > 0.5 and l["direction"] == "Out":
            fx = l["fx"] if l["fx"] > 0 else 0.0
            why = (", all exchange-rate movement" if abs(l["overrun"] - fx) < 1 else
                   f" (${fx:,.0f} of it exchange rate)" if fx else "")
            flags.append(("over", f"{l['item']}: ${l['overrun']:,.0f} over forecast{why}", None))
        for o, t in l["pct_gaps"]:
            flags.append(("terms", f"{l['item']}: {o} milestones add up to {t * 100:g}%, not 100%", None))
        for m in (l["milestones"] if l["open_commit"] > 0.5 else []):    # nothing left to bill: terms don't matter
            if not m.get("Confirmed") and not m.get("Billed Doc"):
                flags.append(("terms", f"{m.get('PO / Order #')} {m.get('Party')}: payment terms need checking "
                                       f"({m.get('Terms Text') or 'not read'})", None))
                break
    for t in pt:
        if (t.get("Type") == "Invoice" and not t.get("Paid Date") and t.get("Due Date")
                and str(t["Due Date"]) < today):
            flags.append(("overdue", f"Invoice {t.get('Doc #')} ${num(t.get('Amount')):,.0f} overdue since {t['Due Date']}", None))

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
        "curve": curve, "fy": fy_totals(curve), "fy_now": fy_of(today[:7]), "low": low_point(curve), "flags": flags, "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "unfiled": unfiled(pt),
        "ledger": ledger, "position": position(ledger), "ledger_lines": ledger_lines(lines, ledger),
        "money": {"in": stages(rev), "out": stages(cost)},
        "fy_docs": fy_documents(pt), "first_date": first_date(pt),
    }


def ledger_lines(lines: list, ledger: dict) -> dict:
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
                           "tocome": round(sum(r["amount"] for r in rows if not r["done"]), 2),
                           "overdue": any(r["overdue"] for r in rows)})
        out[side] = groups
    return out


def position(ledger: dict) -> dict:
    """The cashflow statement: received - paid = cash now; + still to receive - still to pay = final position."""
    s = {f"{side}_{w}": round(sum(r["amount"] for r in rows if r["done"] == (w == "done")), 2)
         for side, rows in ledger.items() for w in ("done", "tocome")}
    s["in_overdue"] = round(sum(r["amount"] for r in ledger["in"] if r["overdue"]), 2)
    s["out_overdue"] = round(sum(r["amount"] for r in ledger["out"] if r["overdue"]), 2)
    s["now"] = round(s["in_done"] - s["out_done"], 2)
    s["final"] = round(s["now"] + s["in_tocome"] - s["out_tocome"], 2)
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
    totals["position"] = {k: round(sum(v["position"][k] for v in views), 2) for k in (views[0]["position"] if views else {})}
    curve = merged_curve(views, today)
    curve_fy = merged_curve(fy_views, today)
    return {"views": views, "complete": complete, "totals": totals, "curve": curve, "curve_fy": curve_fy,
            "fy": fy_totals(curve_fy), "fy_now": fy_of(today[:7]), "fy_all": fy_summary(fy_views, today),
            "low": low_point(curve)}

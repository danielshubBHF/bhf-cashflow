"""Read a BHF costing workbook (the Systems Costing Template and its RO-EDI variant) into forecast buckets.

A costing workbook holds several versions of the costing as tabs, plus reference tabs (Valves, Pumps…). A costing
tab has a header block (customer, selling price, bid / budget price, GM) and, from the row whose cells include
"Component" and "Extended Cost", line items grouped under section rows ("DAF System", "Welkin - System",
"Project Management & Documentation"…). Columns are found by their header text, so both template layouts work.

  read(path, contract=None) -> {"tab", "tabs", "head", "sections", "lines", "labour", "contingency", "total_cost",
                               "total_sell"}
The live tab is the costing tab whose bid / budget / selling price is closest to the contract value (when known),
else the first costing tab. Lines with no quantity (unselected options) are left out.
"""
import re
from collections import defaultdict

LABOUR_WORDS = re.compile(r"project management|engineering\b|procurement|commissioning|supervision|design &|"
                          r"design and|drafting|documentation|start.?up", re.I)
CONTINGENCY = re.compile(r"contingen", re.I)
HEAD_KEYS = {"Customer": "customer", "Customer contact": "contact", "Sales Person": "sales", "Project Engineer": "engineer",
             "Equiry Number": "enquiry", "Enquiry Number": "enquiry", "Project Cost": "cost", "Calculated Selling Price": "calc_sell",
             "Bid Price": "bid", "Budget Price": "bid", "Contract Price": "contract", "GM on selling (%)": "gm", "Quote ref": "quote"}


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _txt(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v not in (None, "") and not isinstance(v, (int, float)) else ""


def parse_tab(rows: list) -> dict | None:
    hdr = next((i for i, r in enumerate(rows) if r and "Component" in [_txt(x) for x in r]
                and "Extended Cost" in [_txt(x) for x in r]), None)
    if hdr is None:
        return None
    head = {}
    for r in rows[:hdr]:
        for j, v in enumerate(r or ()):
            k = HEAD_KEYS.get(_txt(v).rstrip(":"))
            if k and k not in head:
                head[k] = next((x for x in r[j + 1:] if x not in (None, "")), None)
    names = [_txt(x) for x in rows[hdr]]
    col = {}
    for j, n in enumerate(names):
        if n and n not in col:
            col[n] = j
    # Systems template: Item (name) | Component (make / supplier) | Sub Component | Description …
    # RO-EDI variant:   Component (name) | Size | Model (supplier) | Description / Comment …
    comp = col.get("Item", col["Component"])
    qty = col.get("Quantity")
    ext = col["Extended Cost"]
    sell = col.get("ExtendedSelling Price", col.get("Extended Selling Price"))
    unit = col.get("Unit Cost ExWorks")
    supp_col = col["Component"] if "Item" in col else col.get("Model")
    desc_col = col.get("Description", col.get("Description / Comment"))
    lines, section = [], ""
    for r in rows[hdr + 1:]:
        if not r:
            continue
        cells = list(r) + [None] * (max(col.values()) + 2 - len(r))
        name = _txt(cells[comp])
        if name.lower().startswith("sub total") or _txt(cells[0]).lower().startswith("sub total") or \
                any(_txt(x).lower() == "sub total" for x in cells[:ext]):
            break
        q, cost = _num(cells[qty]) if qty is not None else None, _num(cells[ext])
        if name and not any(_num(x) is not None for x in cells[comp + 1:ext + 1]):
            section = name                              # a section heading: no figures on the row
            continue
        if not name or not cost or cost <= 0 or (q is not None and q <= 0):
            continue                                    # blank, subtotal, or an option not taken
        supplier = _txt(cells[supp_col]) if supp_col is not None else ""
        desc = _txt(cells[desc_col]) if desc_col is not None else ""
        u = _num(cells[unit]) if unit is not None else None
        labour = bool(LABOUR_WORDS.search(name)) and (u == 800 or "per day" in f"{supplier} {desc}".lower()
                                                     or "/ hr" in f"{supplier} {desc}" or "week" in f"{supplier} {desc}".lower())
        lines.append({"section": section, "item": name, "supplier": supplier, "desc": desc, "qty": q, "cost": round(cost, 2),
                      "sell": round(_num(cells[sell]) or 0.0, 2) if sell is not None else 0.0,
                      "labour": labour, "contingency": bool(CONTINGENCY.search(f"{name} {section}"))})
    if not lines:
        return None
    sections = defaultdict(float)
    for l in lines:
        sections[l["section"] or "Other"] += l["cost"]
    return {"head": head, "lines": lines, "sections": {k: round(v, 2) for k, v in sections.items()},
            "labour": round(sum(l["cost"] for l in lines if l["labour"]), 2),
            "contingency": round(sum(l["cost"] for l in lines if l["contingency"] and not l["labour"]), 2),
            "total_cost": round(sum(l["cost"] for l in lines), 2), "total_sell": round(sum(l["sell"] for l in lines), 2)}


def _key(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def pick(tabs: list, contract: float | None = None, customer: str | None = None) -> dict:
    """The live costing tab: the leftmost one not copied from another customer's job; switched to another tab only
    when the leftmost is more than 2% off the contract and that tab is within 0.5% of it."""
    def foreign(t):
        c = _key(t["head"].get("customer"))
        return bool(customer and c and c != "0" and c not in _key(customer) and _key(customer)[:6] not in c)
    ok = [t for t in tabs if not foreign(t)] or tabs

    def off(t):
        h = t["head"]
        ps = [p for p in (_num(h.get("bid")), _num(h.get("calc_sell")), t["total_sell"]) if p]
        return min((abs(p - contract) / contract for p in ps), default=1.0)
    first = ok[0]
    if contract and off(first) > 0.02:
        near = [t for t in ok if off(t) <= 0.005]
        if near:
            return near[0]
    return first


def read(path: str, contract: float | None = None, customer: str | None = None) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    tabs = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(min_row=1, max_row=min(ws.max_row or 0, 400), values_only=True))
        t = parse_tab(rows)
        if t:
            t["tab"] = ws.title
            tabs.append(t)
    if not tabs:
        raise ValueError("No costing tab found (looked for a row with Component … Extended Cost).")

    def price(t):
        h = t["head"]
        return _num(h.get("bid")) or _num(h.get("calc_sell")) or t["total_sell"]
    best = pick(tabs, contract, customer)
    return {**best, "tabs": [(t["tab"], price(t), t["total_cost"]) for t in tabs]}

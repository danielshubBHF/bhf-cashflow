"""Dashboard edit forms -> clean Smartsheet values. Each cleaner returns (values, error)."""
import datetime as dt

COST_TYPES = ["Equipment", "Engineering / Design", "Freight & Logistics", "Install / Site Works", "Commissioning",
              "Consumables & Chemicals", "Consultants", "Travel & Expenses", "Customer Milestone", "Variation", "Supplier recovery", "Other"]


class Bad(ValueError):
    pass


def money(s, label="Amount"):
    s = str(s or "").replace("$", "").replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        raise Bad(f"{label} must be a number, e.g. 12500 or 12,500.00")


def day(s, label="Expected date"):
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise Bad(f"{label} must be a date, e.g. 2026-11-30 or 30/11/2026")


def text(s, limit=400):
    return " ".join(str(s or "").split())[:limit] or None


def ticked(form, name) -> bool:
    return "1" in form.getlist(name)


def forecast(form) -> tuple[dict | None, str | None]:
    try:
        item = text(form.get("item"), 200)
        if not item:
            raise Bad("Give the line an Item name")
        direction = form.get("direction")
        if direction not in ("In", "Out"):
            raise Bad("Direction must be In or Out")
        cost_type = form.get("cost_type") or None
        if cost_type and cost_type not in COST_TYPES:
            raise Bad("Pick a Cost Type from the list")
        return {
            "Item": item, "Direction": direction, "Cost Type": cost_type, "Party": text(form.get("party"), 200),
            "Amount": money(form.get("amount")), "Expected Date": day(form.get("date")),
            "PO / Order #": ", ".join(p for p in (x.strip() for x in str(form.get("po") or "").replace(";", ",").split(",")) if p) or None,
            "Closed": ticked(form, "closed"), "Notes": text(form.get("notes"), 1000),
        }, None
    except Bad as e:
        return None, str(e)


def milestone(form) -> tuple[dict | None, str | None]:
    try:
        label = text(form.get("milestone"), 200)
        if not label:
            raise Bad("Give the milestone a name, e.g. 30% advance")
        pct = money(form.get("percent"), "%")
        if pct is not None and not 0 <= pct <= 100:
            raise Bad("% must be between 0 and 100")
        return {
            "Milestone": label, "Percent": None if pct is None else round(pct / 100, 6),
            "Amount": money(form.get("amount")), "Expected Date": day(form.get("date")),
            "Confirmed": ticked(form, "confirmed"),
        }, None
    except Bad as e:
        return None, str(e)

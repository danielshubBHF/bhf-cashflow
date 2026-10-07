"""Pipeline: open Systems enquiries from "1. Enquiries Pipeline Mastersheet", projected into the P&L by the same
staged formula as live projects, so the FY budget card can show what the pipeline could add.

Per enquiry (defaults, each overridable on the Pipeline tab; overrides live in the Cashflow Database's Pipeline sheet,
never in the enquiries sheet):
  value        EstImated Value $AUD
  probability  from Likelihood: 1 PO imminent 90%, 2 awaiting approval 70%, 3 in negotiation 50%, 4 in discussion 25%,
               5 unlikely 5% (the old 0-5 Likelihood column if the new one is blank)
  start month  Estimated Order Date if set, else from Estimated Conversion: 0-3 months -> +2, 3-6 -> +5,
               6-12 -> +9, > 12 months -> next July. With neither (most enquiries today), none: the enquiry isn't
               counted until a start month is set on the Pipeline tab.
  stages       2/2/3/2/2 months (30/20/20/20/10)
"""
import re

from . import model, pl

ENQ_SHEET = 7290402377781124
NAME, VALUE, DIVISION, STREAM = "Customer - Opportunity", "EstImated Value $AUD", "BHF Division", "BHF Income Stream"
LIKELIHOOD, OLD_LIKELIHOOD, CONVERSION, ORDER_DATE = "Likelihood", "Likelihood (0-5)", "Estimated Conversion", "Estimated Order Date "
LEAD = "Responsible Lead"
PROB = {"1": 0.9, "2": 0.7, "3": 0.5, "4": 0.25, "5": 0.05}
OLD_PROB = {"5": 0.75, "4": 0.5, "3": 0.3, "2": 0.15, "1": 0.05, "0": 0.0}   # Likelihood (0-5): 5 = most likely
AHEAD = {"Converted": 1, "0-3 months": 2, "3-6 months": 5, "6-12 months": 9}
SHUT = {"Won", "Lost", "Dead"}


def money(v) -> float:
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v or "")) or 0)
    except ValueError:
        return 0.0


def is_open_systems(r: dict) -> bool:
    if "Systems" not in f"{r.get(DIVISION) or ''} {r.get(STREAM) or ''}":
        return False
    if r.get("Won/Lost/Dead") in SHUT or r.get("Proposal Outcome (Won/Lost/Dead)") in SHUT:
        return False
    if r.get("Move to Lost/Dead") or r.get("Move to Won") or r.get("Lead Status") == "Closed":
        return False
    return bool(str(r.get(NAME) or "").strip())


def default_prob(r: dict) -> float:
    lk = str(r.get(LIKELIHOOD) or "").strip()[:1]
    if lk in PROB:
        return PROB[lk]
    old = str(r.get(OLD_LIKELIHOOD) or "").strip()[:1]
    return OLD_PROB.get(old, 0.25)


def default_start(r: dict, today: str) -> str | None:
    if r.get(ORDER_DATE):
        return max(str(r[ORDER_DATE])[:7], today[:7])
    conv = str(r.get(CONVERSION) or "").strip()
    if conv.startswith(">"):                              # > 12 months: next financial year
        return f"{int(model.fy_start(today)[:4]) + 1}-07"
    ahead = AHEAD.get(conv)
    return pl.add(today[:7], ahead) if ahead else None    # unknown: not counted until a start month is set


def view(enquiries: list[dict], settings: list[dict], today: str, fy: str) -> dict:
    """Rows with each enquiry's FY revenue by the formula (this FY from the current month on, and next FY),
    unweighted and weighted, plus totals."""
    mine = {str(s.get("Row ID")): s for s in settings if s.get("Row ID")}
    nxt = f"FY{int(fy[2:]) + 1:02d}"
    rows = []
    for r in enquiries:
        if not is_open_systems(r):
            continue
        s = mine.get(str(r["_id"]), {})
        value = money(s.get("Value")) if s.get("Value") not in (None, "") else money(r.get(VALUE))
        prob = money(s.get("Probability %")) / 100 if s.get("Probability %") not in (None, "") else default_prob(r)
        start = pl.ym(s.get("Start")) or default_start(r, today)
        months = pl.parse_months(s.get("Stages"))
        sched = pl.spread(value, start, months) if value > 0 and start else {}
        this = round(sum(a for m, a in sched.items() if model.fy_of(m) == fy and m >= today[:7]), 2)
        then = round(sum(a for m, a in sched.items() if model.fy_of(m) == nxt), 2)
        rows.append({"id": r["_id"], "name": r.get(NAME), "lead": r.get(LEAD) or "", "likelihood": r.get(LIKELIHOOD) or r.get(OLD_LIKELIHOOD) or "",
                     "conversion": r.get(CONVERSION) or "", "value": value, "prob": prob, "start": start,
                     "start_label": pl.label(start) if start else "", "needs_start": not start, "months": months, "stages": "/".join(map(str, months)),
                     "include": not s.get("Exclude"), "set": bool(s), "this": this, "next": then,
                     "this_w": round(this * prob, 2), "next_w": round(then * prob, 2),
                     "their_fy": money(r.get("FY27 $") or r.get("Potential Revenue FY27")) if fy == "FY27" else None})
    rows.sort(key=lambda x: (not x["include"], x["needs_start"], -x["this_w"], -x["value"]))
    inc = [x for x in rows if x["include"]]
    tot = {k: round(sum(x[k] for x in inc), 2) for k in ("value", "this", "next", "this_w", "next_w")}
    return {"fy": fy, "next_fy": nxt, "rows": rows, "count": len(inc), "no_start": sum(1 for x in inc if x["needs_start"]),
            "no_start_value": round(sum(x["value"] for x in inc if x["needs_start"]), 2), **tot}

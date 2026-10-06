"""P&L recognition ("flattening"): how much revenue and cost each project should show in the P&L each month.

The rule (agreed with Daniel, 06/10/26):
  * Five stages with fixed shares: Deposit 30%, Design review 20%, FAT 20%, Delivery 20%,
    Commissioning & handover 10%. They are P&L categories, not the contract's payment terms.
  * The PM sets how many months each stage takes (standard 2/2/3/2/2). Each stage's share is split evenly
    over its months. The schedule starts in the month of the customer's first deposit invoice (PM can override).
  * Only incoming lines over the threshold (PL_THRESHOLD, $100,000) are flattened. Anything at or under it
    is recognised when it is invoiced (and, until then, on its expected date).
  * A variation over the threshold that lands mid-project catches up: in the month it lands it recognises
    every stage up to and including the one the project is in; the rest follows the remaining stages.
  * Cost (expected cost of every bucket + BHF labour) follows the same stages and months.
  * The PM's timing is locked at the start (baseline) and may be amended later (current). Comparing the two
    by financial year shows what has to move between years; comparing current with NetSuite's journals
    shows the corrections to post.
"""
import os
import re
from collections import defaultdict

from .model import MONTHS, fy_of, num

STAGES = [("Stage 1", "Deposit", 0.30), ("Stage 2", "Design review", 0.20), ("Stage 3", "FAT", 0.20),
          ("Stage 4", "Delivery", 0.20), ("Stage 5", "Commissioning & handover", 0.10)]
DEFAULT_MONTHS = [2, 2, 3, 2, 2]


def threshold() -> float:
    return float(os.getenv("PL_THRESHOLD", "100000"))


def parse_months(s) -> list[int]:
    """'2/2/3/2/2' -> [2, 2, 3, 2, 2]. Anything that isn't five whole numbers of at least 1 -> the standard."""
    parts = [p for p in re.split(r"[\s/,;-]+", str(s or "").strip()) if p]
    try:
        m = [int(float(p)) for p in parts]
    except ValueError:
        return list(DEFAULT_MONTHS)
    return m if len(m) == 5 and all(x >= 1 for x in m) else list(DEFAULT_MONTHS)


def ym(d) -> str | None:
    s = str(d or "").strip()
    return s[:7] if re.match(r"\d{4}-\d{2}", s) else None


def add(m: str, n: int) -> str:
    y, mo = int(m[:4]), int(m[5:7]) - 1 + n
    return f"{y + mo // 12:04d}-{mo % 12 + 1:02d}"


def plan(start: str, months: list[int]) -> list[dict]:
    """One row per month: {'month', 'stage' (0-4), 'pct'} - each stage's share split evenly over its months."""
    out, m = [], start
    for i, (n, (_, _, share)) in enumerate(zip(months, STAGES)):
        for _ in range(n):
            out.append({"month": m, "stage": i, "pct": share / n})
            m = add(m, 1)
    return out


def spread(amount: float, start: str, months: list[int]) -> dict[str, float]:
    """Each stage's share split evenly over its months, to the cent; the last month of a stage takes the
    rounding so every stage (and the whole) adds up exactly."""
    out, rows = {}, plan(start, months)
    totals = [round(amount * share, 2) for _, _, share in STAGES]
    totals[-1] = round(amount - sum(totals[:-1]), 2)
    for i, total in enumerate(totals):
        ms = [r["month"] for r in rows if r["stage"] == i]
        each = round(total / len(ms), 2)
        for m in ms[:-1]:
            out[m] = each
        out[ms[-1]] = round(total - each * (len(ms) - 1), 2)
    return out


def catch_up(amount: float, start: str, months: list[int], lands: str) -> dict[str, float]:
    """A variation landing in month `lands`: recognise every stage up to and including the current one
    straight away, then follow the remaining stages' months."""
    rows = plan(start, months)
    if lands <= start:
        return spread(amount, start, months)
    if lands > rows[-1]["month"]:
        return {lands: amount}
    stage = next(r["stage"] for r in rows if r["month"] == lands)
    full = spread(amount, start, months)
    later = {r["month"] for r in rows if r["stage"] > stage}
    out = {lands: round(amount - sum(full[m] for m in later), 2)}      # everything up to and incl. this stage
    for m in sorted(later):
        out[m] = full[m]
    return out


def label(m: str) -> str:
    return f"{MONTHS[int(m[5:7]) - 1]} {m[2:4]}"


def first_invoice(l: dict) -> str | None:
    dates = sorted(ym(t.get("Date")) for t in l["txns"] if t.get("Type") == "Invoice" and ym(t.get("Date")))
    return dates[0] if dates else None


def as_invoiced(l: dict) -> dict[str, float]:
    """At or under the threshold: in the month of each invoice; what's not invoiced yet on its expected date."""
    out = defaultdict(float)
    billed = 0.0
    for t in l["txns"]:
        if t.get("Type") in ("Invoice", "Credit Note") and ym(t.get("Date")):
            out[ym(t["Date"])] += num(t.get("Amount"))
            billed += num(t.get("Amount"))
    rest = l["expected"] - billed
    if rest > 0.5:
        out[ym(l.get("date")) or "Undated"] += rest
    return dict(out)


def project_pl(p: dict, v: dict, today: str, actual: dict | None = None) -> dict:
    """The project's P&L schedule: current (amended) and baseline (locked) revenue and cost per month,
    NetSuite's actual journals beside them, and totals per financial year."""
    lim = threshold()
    ins = [l for l in v["lines_in"] if not l.get("unassigned")]
    main = [l for l in ins if l["type"] != "Variation" and l["expected"] > lim]
    variations = [l for l in ins if l["type"] == "Variation" and l["expected"] > lim]
    small = [l for l in ins if l not in main and l not in variations]
    flattened = bool(main)
    first = min((d for d in (first_invoice(l) for l in main) if d), default=None)
    start = ym(p.get("P&L Start")) or first or ym(today)
    months = parse_months(p.get("P&L Stages"))
    labour = num(p.get("Internal Labour"))

    def build(start_, months_, rev_main, cost_total):
        rev, cost = defaultdict(float), defaultdict(float)
        if flattened:
            for m, a in spread(rev_main, start_, months_).items():
                rev[m] += a
            for l in variations:
                for m, a in catch_up(l["expected"], start_, months_, first_invoice(l) or ym(l.get("date")) or start_).items():
                    rev[m] += a
            for m, a in spread(cost_total, start_, months_).items():
                cost[m] += a
        else:                                    # small job: revenue when invoiced, cost when billed
            for l in v["lines_out"]:
                for t in l["txns"]:
                    if t.get("Type") not in ("PO", "Sales Order") and ym(t.get("Date")):
                        cost[ym(t["Date"])] += num(t.get("Amount"))
                rest = l["expected"] - l["billed"]
                if rest > 0.5:
                    cost[ym(l.get("date")) or "Undated"] += rest
        for l in small:
            for m, a in as_invoiced(l).items():
                rev[m] += a
        return rev, cost

    rev_main = round(sum(l["expected"] for l in main), 2)
    cost_total = round(v["cost"]["expected"] + labour, 2)
    rev, cost = build(start, months, rev_main, cost_total)
    locked = bool(p.get("P&L Locked"))
    if locked:
        b_start = ym(p.get("P&L Locked Start")) or start
        b_months = parse_months(p.get("P&L Locked Stages") or p.get("P&L Stages"))
        b_rev, b_cost = build(b_start, b_months, num(p.get("P&L Locked Revenue")) or rev_main,
                              num(p.get("P&L Locked Cost")) or cost_total)
    else:
        b_start, b_months, b_rev, b_cost = start, months, rev, cost
    act_rev = (actual or {}).get("rev", {})
    act_cost = (actual or {}).get("cost", {})
    stage_of = {r["month"]: r["stage"] for r in plan(start, months)} if flattened else {}
    all_months = sorted({*rev, *cost, *b_rev, *b_cost, *act_rev, *act_cost} - {"Undated"})
    rows = []
    for m in all_months:
        r = {"month": m, "label": label(m), "fy": fy_of(m), "stage": stage_of.get(m),
             "rev": round(rev.get(m, 0.0), 2), "rev_base": round(b_rev.get(m, 0.0), 2),
             "rev_act": round(act_rev.get(m, 0.0), 2), "cost": round(cost.get(m, 0.0), 2),
             "cost_base": round(b_cost.get(m, 0.0), 2), "cost_act": round(act_cost.get(m, 0.0), 2),
             "past": m < today[:7], "current": m == today[:7]}
        r["rev_diff"] = round(r["rev_act"] - r["rev"], 2) if m <= today[:7] else None
        r["cost_diff"] = round(r["cost_act"] - r["cost"], 2) if m <= today[:7] else None
        rows.append(r)
    fys = []
    for fy in sorted({r["fy"] for r in rows}):
        rr = [r for r in rows if r["fy"] == fy]
        f = {"fy": fy, **{k: round(sum(r[k] for r in rr), 2)
                          for k in ("rev", "rev_base", "rev_act", "cost", "cost_base", "cost_act")}}
        f["move_rev"] = round(f["rev"] - f["rev_base"], 2)     # amended vs locked: what moves into / out of the year
        f["move_cost"] = round(f["cost"] - f["cost_base"], 2)
        fys.append(f)
    return {"flattened": flattened, "threshold": lim, "start": start, "start_label": label(start), "months": months,
            "stages": [{"name": f"{s} {n}", "pct": share, "months": k} for (s, n, share), k in zip(STAGES, months)],
            "rev_main": rev_main, "cost_total": cost_total, "labour": labour, "locked": locked,
            "locked_on": p.get("P&L Locked"), "base_start_label": label(b_start), "base_months": b_months,
            "variations": [l["item"] for l in variations], "small": [l["item"] for l in small],
            "rows": rows, "fys": fys, "has_actual": actual is not None,
            "undated_rev": round(rev.get("Undated", 0.0), 2), "undated_cost": round(cost.get("Undated", 0.0), 2)}


def fy_remaining(pl: dict, fy: str, today: str) -> float:
    """Revenue the formula still puts into `fy` from the current month on."""
    return round(sum(r["rev"] for r in pl["rows"] if r["fy"] == fy and r["month"] >= today[:7]), 2)

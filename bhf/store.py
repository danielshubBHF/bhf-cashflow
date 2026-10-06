"""The dashboard's cached copy of the four sheets, with write-through edits.

A save writes the changed cells to Smartsheet first, then patches the cached rows in place, so the
next page render re-runs the model on cached data straight away (no reload, no waiting for a sync).
In DEMO mode the fixture is the "sheet" and edits live in memory until the server restarts.
"""
import copy
import datetime as dt
import itertools
import logging
import os
import time

from . import config
from . import model
from .model import norm

log = logging.getLogger("store")
BUDGET_SECONDS = 3600              # the FY budget and P&L actuals change slowly: ask NetSuite hourly

NAMES = ("projects", "forecasts", "transactions", "schedule")


def same(a, b) -> bool:
    if a in (None, "", False) and b in (None, "", False):
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def changes(row: dict, values: dict) -> dict:
    return {k: v for k, v in values.items() if not same(row.get(k), v)}


def group_row(forecasts: list, code: str):
    """The Forecasts group (parent) row for a project: no Project value, Item starting with the code."""
    return next((f["_id"] for f in forecasts if not f.get("Project") and str(f.get("Item") or "").startswith(code + " ")), None)


class Store:
    def __init__(self):
        self.at, self.data, self._tables = 0.0, None, {}
        self.bud, self.bud_at = None, 0.0
        self._ids = itertools.count(1)

    @property
    def demo(self) -> bool:
        return os.getenv("DEMO") == "1"

    def table(self, name):
        from .smartsheet_db import Table
        if name not in self._tables:
            self._tables[name] = Table(config.SHEETS[name])
        return self._tables[name]

    def load(self, force=False):
        if self.demo:
            if self.data is None:                       # edits persist until restart, even on Refresh
                from tests.fixture_26001 import FORECASTS, PROJECTS, SCHEDULE, TXNS
                self.data = tuple(copy.deepcopy(x) for x in (PROJECTS, FORECASTS, TXNS, SCHEDULE))
                for row in itertools.chain(*self.data):
                    row["_id"] = next(self._ids)
            return self.data
        if force or not self.data or time.time() - self.at > config.CACHE_SECONDS:
            self.data = tuple(self.table(k).load() for k in NAMES)
            self.at = time.time()
        return self.data

    # ---- writes
    def _update(self, name: str, pairs: list[tuple[dict, dict]]):
        pairs = [(r, c) for r, c in pairs if c]
        if pairs and not self.demo:
            self.table(name).update([(r["_id"], c) for r, c in pairs])
        for r, c in pairs:
            r.update(c)

    def _add(self, name: str, rows: list, row: dict, parent=None):
        if self.demo:
            row["_id"] = next(self._ids)
        else:
            row["_id"] = self.table(name).add([row], parent_id=parent)[0]["id"]
        rows.append(row)

    def save_forecast(self, code: str, row_id, values: dict) -> str | None:
        """Add (row_id empty) or edit a forecast line. Returns an error message, or None when saved."""
        _, forecasts, txns, _ = self.load()
        pf = [f for f in forecasts if f.get("Project") == code]
        row = next((f for f in pf if str(f.get("_id")) == str(row_id)), None) if row_id else None
        if row_id and row is None:
            return "That line has changed in Smartsheet since this page loaded. Refresh and try again."
        name = values["Item"].lower()
        if any(f is not row and str(f.get("Item") or "").strip().lower() == name for f in pf):
            return f"{code} already has a line called “{values['Item']}”. Use a different name."
        if row is None:
            self._add("forecasts", forecasts, {"Project": code, **values}, parent=group_row(forecasts, code))
            return None
        old = row.get("Item")
        diff = changes(row, values)
        self._update("forecasts", [(row, diff)])
        if "Item" in diff and old:              # keep linked NetSuite transactions on the renamed line
            self._update("transactions", [(t, {"Forecast": values["Item"]}) for t in txns
                                          if t.get("Project") == code and t.get("Forecast") == old])
        return None

    def link(self, code: str, netsuite_id, item: str) -> str | None:
        """Link an unassigned transaction to a forecast line. A PO's number is added to the line too,
        so its bills follow it at the next sync."""
        _, forecasts, txns, _ = self.load()
        t = next((x for x in txns if x.get("Project") == code and str(x.get("NetSuite ID")) == str(netsuite_id)), None)
        f = next((x for x in forecasts if x.get("Project") == code and x.get("Item") == item), None)
        if not t or not f:
            return "That transaction or line has changed since this page loaded. Refresh and try again."
        self._update("transactions", [(t, {"Forecast": item})])
        po, cur = t.get("PO / Order #"), str(f.get("PO / Order #") or "").strip()
        if t.get("Type") == "PO" and po and norm(po) not in {norm(x) for x in cur.split(",")}:
            self._update("forecasts", [(f, {"PO / Order #": f"{cur}, {po}" if cur else po})])
        return None

    def _milestone(self, code, row_id):
        return next((s for s in self.load()[3] if s.get("Project") == code and str(s.get("_id")) == str(row_id)), None)

    def save_milestone(self, code: str, row_id, values: dict, po: str = "", item: str = "") -> str | None:
        """Edit a payment milestone, or add one to an order (po) on a forecast line (item)."""
        _, forecasts, _, sched = self.load()
        row = self._milestone(code, row_id) if row_id else None
        if row_id and row is None:
            return "That milestone has changed in Smartsheet since this page loaded. Refresh and try again."
        po = str(row.get("PO / Order #") if row else po).strip()
        others = sum(float(s.get("Percent") or 0) for s in sched if s is not row and s.get("Project") == code
                     and norm(s.get("PO / Order #")) == norm(po))
        if others + (values.get("Percent") or 0) > 1.0005:
            return f"{po} milestones would add up to {(others + values['Percent']) * 100:g}%. The most this one can be is {max(0, 1 - others) * 100:g}%."
        if row:
            self._update("schedule", [(row, changes(row, values))])
            return None
        f = next((f for f in forecasts if f.get("Project") == code and f.get("Item") == item), None)
        if not f or not po:
            return "Pick the PO / order this milestone belongs to."
        seqs = [float(s.get("Seq") or 0) for s in sched if s.get("Project") == code and norm(s.get("PO / Order #")) == norm(po)]
        self._add("schedule", sched, {"Project": code, "PO / Order #": po, "Party": f.get("Party"),
                                      "Direction": f.get("Direction"), "Seq": int(max(seqs, default=0)) + 1,
                                      "Source": "Manual", **values})
        return None

    def delete_milestone(self, code: str, row_id) -> str | None:
        """Remove an unbilled milestone (e.g. one added by mistake)."""
        row = self._milestone(code, row_id)
        if row is None:
            return "That milestone has changed in Smartsheet since this page loaded. Refresh and try again."
        if row.get("Billed Doc"):
            return "This milestone has been billed in NetSuite, so it can't be removed."
        if not self.demo:
            self.table("schedule").delete([row["_id"]])
        self.load()[3].remove(row)
        return None

    # ---- FY budget vs actual (straight from NetSuite; not kept in Smartsheet)
    def budget(self, force=False) -> dict | None:
        today = dt.date.today().isoformat()
        fy = model.fy_of(today[:7])
        if self.demo:
            from tests.fixture_26001 import BUDGET
            return BUDGET
        if not force and self.bud and time.time() - self.bud_at < BUDGET_SECONDS:
            return self.bud
        try:
            from .netsuite import NetSuite
            ns = NetSuite()
            projects = [(p["Project"], p.get("NetSuite Job ID"), p.get("Unearned Acct ID"))
                        for p in self.load()[0] if p.get("NetSuite Job ID") and p.get("Status") != "Closed"]
            rows = [p for p in self.load()[0] if p.get("NetSuite Job ID") and p.get("Status") not in model.LIVE_OUT]
            self.bud = {"fy": fy, "budget": ns.systems_budget(fy), "actual": ns.systems_actual(model.fy_months(fy)),
                        "recognised": ns.recognised(projects, model.fy_start(today)),
                        "pl_actuals": ns.pl_actuals([(p["Project"], p.get("NetSuite Job ID"), p.get("Unearned Acct ID"),
                                                      p.get("WIP Acct ID")) for p in rows]),
                        "at": dt.datetime.now().strftime("%d %b %y %H:%M")}
            self.bud_at = time.time()
        except Exception:
            log.exception("budget from NetSuite failed")       # keep the last good copy, if any
        return self.bud

    # ---- P&L timing (Projects sheet): the PM's start month and months per stage, and the locked baseline
    PL_COLUMNS = {"P&L Start": "DATE", "P&L Stages": "TEXT_NUMBER", "P&L Locked": "DATE", "P&L Locked Start": "DATE",
                  "P&L Locked Stages": "TEXT_NUMBER", "P&L Locked Revenue": "TEXT_NUMBER", "P&L Locked Cost": "TEXT_NUMBER"}

    def save_project(self, code: str, values: dict) -> str | None:
        projects = self.load()[0]
        row = next((p for p in projects if p.get("Project") == code), None)
        if row is None:
            return "Unknown project."
        if not self.demo:
            t = self.table("projects")
            for title, kind in self.PL_COLUMNS.items():
                if title in values:
                    t.ensure_column(title, kind, 110)
        self._update("projects", [(row, changes(row, values))])
        return None

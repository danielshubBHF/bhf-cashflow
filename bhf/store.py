"""The dashboard's cached copy of the four sheets, with write-through edits.

A save writes the changed cells to Smartsheet first, then patches the cached rows in place, so the
next page render re-runs the model on cached data straight away (no reload, no waiting for a sync).

Pages never wait on Smartsheet or NetSuite once the cache is warm: when it's older than CACHE_SECONDS (or the
NetSuite figures older than an hour) a background thread refetches everything, in parallel, and swaps it in.
A refetch that overlaps a save is thrown away and repeated, so a save is never undone by an older copy.
In DEMO mode the fixture is the "sheet" and edits live in memory until the server restarts.
"""
import copy
import datetime as dt
import itertools
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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


def parse_split(text: str):
    """'30/70' or '30% deposit, 40% FAT, 30% on delivery' -> ([(30.0, ''), (70.0, '')], None), or (None, error)."""
    import re
    found = re.findall(r"(\d+(?:\.\d+)?)\s*%?\s*([^/,;+\d]*)", str(text or ""))
    parts = []
    for pct, label in found:
        label = re.sub(r"^(on|at|upon|after)\s+", "", label.strip(" -:.&"), flags=re.I).strip()
        parts.append((float(pct), label[:1].upper() + label[1:60] if label else ""))
    parts = [p for p in parts if p[0] > 0]
    if not parts:
        return None, "Type the split as percentages, e.g. 30/70 or 30% deposit, 70% on delivery."
    total = sum(p for p, _ in parts)
    if abs(total - 100) > 0.05:
        return None, f"That split adds up to {total:g}%, not 100%."
    return parts, None


class Store:
    def __init__(self):
        self.at, self.data, self._tables = 0.0, None, {}
        self.bud, self.bud_at = None, 0.0
        self._ids = itertools.count(1)
        self._writes = 0                                   # bumped by every save (see _refresh)
        self._fetching = threading.Lock()                  # one refetch at a time
        self._bud_fetching = threading.Lock()

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
        if force or not self.data:
            with self._fetching:                           # first page after a restart (or Refresh) waits
                if force or not self.data:
                    self._refresh()
        elif time.time() - self.at > config.CACHE_SECONDS:
            self._in_background(self._fetching, self._refresh)
        return self.data

    @staticmethod
    def _in_background(lock, fn):
        if lock.acquire(blocking=False):                   # already running: nothing to do
            def run():
                try:
                    fn()
                except Exception:
                    log.exception("background refresh failed")      # keep serving the last good copy
                finally:
                    lock.release()
            threading.Thread(target=run, daemon=True).start()

    def _refresh(self):
        """Fetch the four database sheets, the Flag Log and the pipeline sheets in parallel, then swap them in."""
        from .smartsheet_db import Table
        for _ in range(3):
            w0 = self._writes
            tables = {k: Table(config.SHEETS[k]) for k in NAMES}
            extra = {"flags": Table(config.SHEET_FLAGS) if config.SHEET_FLAGS else None,
                     "pipe": Table(config.SHEET_PIPELINE) if config.SHEET_PIPELINE else None,
                     "enq": Table(config.SHEET_ENQUIRIES)}
            with ThreadPoolExecutor(max_workers=7) as ex:
                got = {k: ex.submit(t.load) for k, t in {**tables, **{k: t for k, t in extra.items() if t}}.items()}
                res = {}
                for k, f in got.items():
                    try:
                        res[k] = f.result()
                    except Exception:
                        if k in NAMES:
                            raise
                        log.exception("%s sheet", k)          # the extras are optional
                        res[k] = []
            if self._writes != w0:                         # a save landed while fetching: fetch again
                continue
            self._tables.update(tables)
            self._flag_table, self._pipe_table = extra["flags"], extra["pipe"]
            self._flags, self._pipe, self._enq = res.get("flags", []), res.get("pipe", []), res.get("enq", [])
            self.data = tuple(res[k] for k in NAMES)
            self.at = self._flags_at = self._pipe_at = time.time()
            return

    # ---- writes
    def _update(self, name: str, pairs: list[tuple[dict, dict]]):
        self._writes += 1
        pairs = [(r, c) for r, c in pairs if c]
        if pairs and not self.demo:
            self.table(name).update([(r["_id"], c) for r, c in pairs])
        for r, c in pairs:
            r.update(c)

    def _add(self, name: str, rows: list, row: dict, parent=None):
        self._writes += 1
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
        if "Item" in values:
            name = str(values["Item"] or "").lower()
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
        self._writes += 1
        if not self.demo:
            self.table("schedule").delete([row["_id"]])
        self.load()[3].remove(row)
        return None

    def apply_terms(self, code: str, po: str, split: str = "", confirm: bool = False) -> str | None:
        """Set a PO's payment terms from a needs-attention item: either confirm the milestones as they are, or
        replace them with a split typed by the PM ('30/70', '30% deposit, 70% on delivery'). Billed milestones
        keep their bill; the PO value is shared out by the new percentages."""
        _, _, txns, sched = self.load()
        ms = sorted([s for s in sched if s.get("Project") == code and norm(s.get("PO / Order #")) == norm(po)],
                    key=lambda s: float(s.get("Seq") or 0))
        if confirm:
            if not ms:
                return f"{po} has no milestones to confirm."
            if abs(sum(float(m.get("Percent") or 0) for m in ms) - 1) > 0.0005:
                return f"{po} milestones don't add up to 100%. Type the actual split instead."
            self._update("schedule", [(m, changes(m, {"Confirmed": True})) for m in ms])
            return None
        parts, err = parse_split(split)
        if err:
            return err
        orders = [t for t in txns if t.get("Project") == code and t.get("Type") in model.ORDER_TYPES
                  and norm(t.get("PO / Order #")) == norm(po)]
        base = sum(float(t.get("Amount") or 0) for t in orders)
        if not base and ms:
            pct = sum(float(m.get("Percent") or 0) for m in ms)
            base = sum(float(m.get("Amount") or 0) for m in ms) / pct if pct else 0
        if not base:
            return f"Can't find the value of {po}."
        billed = [m for m in ms if m.get("Billed Doc")]
        if len(billed) > len(parts):
            return f"{len(billed)} of {po}'s milestones are already billed, so the split needs at least {len(billed)} payments."
        ms = billed + [m for m in ms if not m.get("Billed Doc")]          # billed ones keep the first payments
        amounts = [round(base * p / 100, 2) for p, _ in parts]
        amounts[-1] = round(base - sum(amounts[:-1]), 2)
        src = orders[0] if orders else (ms[0] if ms else {})
        for i, ((p, label), amt) in enumerate(zip(parts, amounts)):
            values = {"Seq": i + 1, "Percent": p / 100, "Amount": amt, "Confirmed": True, "Source": "Manual"}
            if label:
                values["Milestone"] = label
            if i < len(ms):
                self._update("schedule", [(ms[i], changes(ms[i], values))])
            else:
                self._add("schedule", sched, {"Project": code, "PO / Order #": po, "Party": src.get("Party"),
                                              "Direction": src.get("Direction") or "Out",
                                              **values, "Milestone": label or f"Payment {i + 1}"})
        extra = ms[len(parts):]
        if extra:
            self._writes += 1
            if not self.demo:
                self.table("schedule").delete([m["_id"] for m in extra])
            for m in extra:
                sched.remove(m)
        return None

    # ---- FY budget vs actual (straight from NetSuite; not kept in Smartsheet)
    def budget(self, force=False) -> dict | None:
        today = dt.date.today().isoformat()
        fy = model.fy_of(today[:7])
        if self.demo:
            from tests.fixture_26001 import BUDGET
            return BUDGET
        if force or not self.bud:
            with self._bud_fetching:
                if force or not self.bud:
                    try:
                        self._fetch_budget(today, fy)
                    except Exception:
                        log.exception("budget from NetSuite failed")       # keep the last good copy, if any
        elif time.time() - self.bud_at > BUDGET_SECONDS:
            self._in_background(self._bud_fetching, lambda: self._fetch_budget(today, fy))
        return self.bud

    def _fetch_budget(self, today: str, fy: str):
        """The NetSuite figures for the FY card and P&L tabs: four queries, run side by side."""
        from .netsuite import NetSuite
        projects = [(p["Project"], p.get("NetSuite Job ID"), p.get("Unearned Acct ID"))
                    for p in self.load()[0] if p.get("NetSuite Job ID") and p.get("Status") != "Closed"]
        rows = [p for p in self.load()[0] if p.get("NetSuite Job ID") and p.get("Status") not in model.LIVE_OUT]
        jobs = {"budget": lambda: NetSuite().systems_budget(fy),
                "actual": lambda: NetSuite().systems_actual(model.fy_months(fy)),
                "recognised": lambda: NetSuite().recognised(projects, model.fy_start(today)),
                "pl_actuals": lambda: NetSuite().pl_actuals([(p["Project"], p.get("NetSuite Job ID"), p.get("Unearned Acct ID"),
                                                              p.get("WIP Acct ID")) for p in rows])}
        with ThreadPoolExecutor(max_workers=4) as ex:
            got = {k: ex.submit(f) for k, f in jobs.items()}
            out = {k: f.result() for k, f in got.items()}
        self.bud = {"fy": fy, **out, "at": dt.datetime.now().strftime("%d %b %y %H:%M")}
        self.bud_at = time.time()

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

    # ---- pipeline: enquiries (read only) and the overrides typed on the Pipeline tab
    def pipeline(self) -> tuple[list, list]:
        if self.demo:
            return getattr(self, "_enq", []), getattr(self, "_pipe", [])
        if getattr(self, "_pipe_at", 0) != self.at:          # reload with the rest of the data
            from .smartsheet_db import Table
            try:
                self._enq = Table(config.SHEET_ENQUIRIES).load()
            except Exception:
                log.exception("enquiries sheet")
                self._enq = []
            self._pipe_table = Table(config.SHEET_PIPELINE) if config.SHEET_PIPELINE else None
            self._pipe = self._pipe_table.load() if self._pipe_table else []
            self._pipe_at = self.at
        return self._enq, self._pipe

    def save_pipeline(self, row_id, name: str, values: dict) -> str | None:
        if not self.demo and not config.SHEET_PIPELINE:
            return "The Pipeline sheet isn't set up (SHEET_PIPELINE)."
        _, pipe = self.pipeline()
        self._writes += 1
        values = {**values, "Updated": dt.date.today().isoformat()}
        row = next((s for s in pipe if str(s.get("Row ID")) == str(row_id)), None)
        if row:
            diff = changes(row, values)
            if diff and not self.demo:
                self._pipe_table.update([(row["_id"], {k: ("" if v is None else v) for k, v in diff.items()})])
            row.update(diff)
        else:
            row = {"Enquiry": name, "Row ID": str(row_id), **values}
            row["_id"] = next(self._ids) if self.demo else self._pipe_table.add([row])[0]["id"]
            pipe.append(row)
        return None

    # ---- "needs attention" acknowledgements (Flag Log sheet)
    def flag_log(self) -> list[dict]:
        if self.demo or not config.SHEET_FLAGS:
            return getattr(self, "_flags", [])
        if getattr(self, "_flags_at", 0) != self.at:          # reload with the rest of the data
            from .smartsheet_db import Table
            self._flag_table = Table(config.SHEET_FLAGS)
            self._flags = self._flag_table.load()
            self._flags_at = self.at
        return self._flags

    def ack_flag(self, code: str, key: str, kind: str, text: str, amount, reason: str) -> str | None:
        if not self.demo and not config.SHEET_FLAGS:
            return "The Flag Log sheet isn't set up (SHEET_FLAGS)."
        log = self.flag_log()
        self._writes += 1
        flag = f"{code}:{key}"
        row = {"Flag": flag, "Project": code, "Kind": kind, "Item": text[:400], "Amount": amount if amount not in (None, "") else None,
               "Reason": (reason or "").strip()[:400] or None, "Acknowledged": dt.date.today().isoformat()}
        old = [r for r in log if r.get("Flag") == flag]
        if not self.demo:
            if old:
                self._flag_table.delete([r["_id"] for r in old])
            row["_id"] = self._flag_table.add([row])[0]["id"]
        else:
            row["_id"] = next(self._ids)
        for r in old:
            log.remove(r)
        log.append(row)
        self._flags = log
        return None

    def restore_flag(self, code: str, key: str) -> str | None:
        log = self.flag_log()
        self._writes += 1
        old = [r for r in log if r.get("Flag") == f"{code}:{key}"]
        if old and not self.demo:
            self._flag_table.delete([r["_id"] for r in old])
        for r in old:
            log.remove(r)
        return None

"""Section 2: forecast editor, milestone editing, write-through cache, Forecasts sheet tidy."""
import copy, os, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from starlette.datastructures import FormData
from bhf import editor, tidy_forecasts
from bhf.model import project_view
from bhf.store import Store
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE


def form(**kw):
    items = []
    for k, v in kw.items():
        items += [(k, x) for x in v] if isinstance(v, list) else [(k, v)]
    return FormData(items)


# ---------------------------------------------------------------- form cleaning
def test_forecast_form_cleaning():
    v, err = editor.forecast(form(item="  Freight  to site ", direction="Out", cost_type="Freight & Logistics",
                                  amount="$12,500", date="30/11/2026", po="PO1; PO2 ,", closed="1", notes=""))
    assert err is None
    assert v["Item"] == "Freight to site" and v["Amount"] == 12500 and v["Expected Date"] == "2026-11-30"
    assert v["PO / Order #"] == "PO1, PO2" and v["Closed"] is True and v["Notes"] is None
    assert editor.forecast(form(item="X", direction="Out", amount=""))[0]["Amount"] is None      # blank = TBC
    assert "number" in editor.forecast(form(item="X", direction="Out", amount="12k"))[1]
    assert "date" in editor.forecast(form(item="X", direction="Out", date="next week"))[1]
    assert "Item" in editor.forecast(form(item=" ", direction="Out"))[1]
    assert "Direction" in editor.forecast(form(item="X", direction="Sideways"))[1]
    assert "Cost Type" in editor.forecast(form(item="X", direction="In", cost_type="Snacks"))[1]


def test_milestone_form_cleaning():
    v, err = editor.milestone(form(milestone="35% FAT", percent="35", amount="146,818", date="2026-08-13", confirmed="1"))
    assert err is None and v["Percent"] == 0.35 and v["Amount"] == 146818 and v["Confirmed"] is True
    assert editor.milestone(form(milestone="x", percent="120"))[1].startswith("%")
    assert editor.milestone(form(milestone="", percent="10"))[1]


# ---------------------------------------------------------------- write-through store
class FakeTable:
    def __init__(self, fail=False):
        self.updates, self.adds, self.fail = [], [], fail

    def update(self, changes):
        if self.fail:
            raise ConnectionError("Smartsheet down")
        self.updates += changes

    def add(self, rows, parent_id=None, fmt=None):
        self.adds.append((rows, parent_id))
        return [{"id": 9000 + len(self.adds)}]


def live_store(fail=False):
    """A non-demo Store holding fixture data, with fake Smartsheet tables."""
    os.environ.pop("DEMO", None)
    s = Store()
    data = [copy.deepcopy(x) for x in (PROJECTS, FORECASTS, TXNS, SCHEDULE)]
    for i, row in enumerate(r for d in data for r in d):
        row["_id"] = 100 + i
    data[1].insert(0, {"Item": "BHF26001 Stacked Farm DAF UF RO 8", "_id": 1})     # group row from the tidy
    s.data, s.at = tuple(data), time.time()
    s._tables = {k: FakeTable(fail) for k in ("projects", "forecasts", "transactions", "schedule")}
    return s


def fc(s, item):
    return next(f for f in s.data[1] if f.get("Item") == item)


def view(s):
    return project_view(PROJECTS[0], s.data[1], s.data[2], s.data[3], "2026-10-05")


def test_edit_writes_only_changed_cells_and_model_updates():
    s = live_store()
    row = fc(s, "Site install - mechanical contractor")
    vals = {k: row.get(k) for k in ("Item", "Direction", "Cost Type", "Party", "Amount", "Expected Date", "PO / Order #", "Closed", "Notes")}
    vals.update(Amount=150000.0, Closed=False)               # Closed blank -> False is not a change
    assert s.save_forecast("BHF26001", row["_id"], vals) is None
    assert s._tables["forecasts"].updates == [(row["_id"], {"Amount": 150000.0})]
    line = next(l for l in view(s)["lines_out"] if l["item"].startswith("Site install"))
    assert line["expected"] == 150000 and line["remaining"] == 150000      # re-run on cache, no reload


def test_new_line_goes_into_project_group():
    s = live_store()
    vals = {"Item": "Freight to site", "Direction": "Out", "Cost Type": "Freight & Logistics", "Amount": 12500.0}
    assert s.save_forecast("BHF26001", "", vals) is None
    rows, parent = s._tables["forecasts"].adds[0]
    assert parent == 1 and rows[0]["Project"] == "BHF26001"
    assert fc(s, "Freight to site")["_id"] == 9001
    assert any(l["item"] == "Freight to site" and l["remaining"] == 12500 for l in view(s)["lines_out"])


def test_duplicate_and_stale_rows_refused():
    s = live_store()
    assert "already has a line" in s.save_forecast("BHF26001", "", {"Item": "jar TESTING", "Direction": "Out"})
    assert "Refresh" in s.save_forecast("BHF26001", 424242, {"Item": "X", "Direction": "Out"})
    assert not s._tables["forecasts"].adds and not s._tables["forecasts"].updates


def test_rename_carries_linked_transactions():
    s = live_store()
    row = fc(s, "Jar testing")
    assert s.save_forecast("BHF26001", row["_id"], {**{k: row.get(k) for k in row if not k.startswith("_")},
                                                     "Item": "Jar testing (RLS)"}) is None
    moved = s._tables["transactions"].updates
    assert len(moved) == 2 and all(c == {"Forecast": "Jar testing (RLS)"} for _, c in moved)    # PO + bill
    line = next(l for l in view(s)["lines_out"] if l["item"] == "Jar testing (RLS)")
    assert line["billed"] == 3224 and not any(l.get("unassigned") for l in view(s)["lines_out"])


def test_smartsheet_failure_leaves_cache_untouched():
    s = live_store(fail=True)
    row = fc(s, "Jar testing")
    try:
        s.save_forecast("BHF26001", row["_id"], {"Item": "Jar testing", "Direction": "Out", "Amount": 1.0})
    except ConnectionError:
        pass
    assert row["Amount"] == 2676


def test_milestone_edit_and_add():
    s = live_store()
    m = next(x for x in s.data[3] if x["PO / Order #"] == "PO006182" and x["Seq"] == 1)
    assert s.save_milestone("BHF26001", m["_id"], {"Milestone": "20% on order", "Percent": 0.2, "Amount": 2607.0,
                                                   "Expected Date": "2026-10-30", "Confirmed": True}) is None
    assert s._tables["schedule"].updates == [(m["_id"], {"Expected Date": "2026-10-30"})]
    assert s.save_milestone("BHF26001", "", {"Milestone": "Retention", "Percent": 0.0}, po="PO-0007", item="Main contract") is None
    new = s._tables["schedule"].adds[0][0][0]
    assert new["Seq"] == 5 and new["Direction"] == "In" and new["Source"] == "Manual" and new["Party"] == "Stacked Farm"
    assert "Pick the PO" in s.save_milestone("BHF26001", "", {"Milestone": "x"}, po="", item="Main contract")


def test_milestones_capped_at_100_percent():
    s = live_store()
    m = next(x for x in s.data[3] if x["PO / Order #"] == "PO005995" and x["Seq"] == 4)
    err = s.save_milestone("BHF26001", m["_id"], {"Milestone": "10% commissioning", "Percent": 0.2})
    assert err == "PO005995 milestones would add up to 110%. The most this one can be is 10%."
    err = s.save_milestone("BHF26001", "", {"Milestone": "extra", "Percent": 0.05}, po="PO005995", item="Bondalti - main system supply")
    assert "105%" in err and "0%" in err
    assert not s._tables["schedule"].updates and not s._tables["schedule"].adds


def test_delete_unbilled_milestone_only():
    s = live_store()
    s._tables["schedule"].delete = lambda ids: s._tables["schedule"].updates.append(("deleted", ids))
    unbilled = next(x for x in s.data[3] if x["PO / Order #"] == "PO005995" and x["Seq"] == 4)
    billed = next(x for x in s.data[3] if x["PO / Order #"] == "PO005995" and x["Seq"] == 1)
    assert "billed in NetSuite" in s.delete_milestone("BHF26001", billed["_id"])
    assert s.delete_milestone("BHF26001", unbilled["_id"]) is None
    assert unbilled not in s.data[3] and s._tables["schedule"].updates == [("deleted", [unbilled["_id"]])]


def test_milestones_not_adding_to_100_are_flagged():
    s = live_store()
    m = next(x for x in s.data[3] if x["PO / Order #"] == "PO-0007" and x["Seq"] == 2)
    m["Percent"] = 0.10
    flags = [t for k, t, _ in view(s)["flags"] if "add up" in t]
    assert flags == ["Main contract: PO-0007 milestones add up to 85%, not 100%"]


def test_link_unassigned_po_adds_po_number():
    s = live_store()
    s.data[2].append({"Doc #": "PO006400", "Project": "BHF26001", "Type": "PO", "Direction": "Out", "Party": "Apex",
                      "PO / Order #": "PO006400", "Amount": 90000, "NetSuite ID": "777", "_id": 5})
    assert s.link("BHF26001", "777", "Site install - mechanical contractor") is None
    assert fc(s, "Site install - mechanical contractor")["PO / Order #"] == "PO006400"
    line = next(l for l in view(s)["lines_out"] if l["item"].startswith("Site install"))
    assert line["open_commit"] == 90000


def test_demo_store_edits_in_memory_only():
    os.environ["DEMO"] = "1"
    try:
        s = Store()
        _, f, *_ = s.load()
        row = next(x for x in f if x["Item"] == "Jar testing")
        s.save_forecast("BHF26001", row["_id"], {"Item": "Jar testing", "Direction": "Out", "Amount": 5000.0})
        assert s.load()[1] is f and row["Amount"] == 5000 and s.load(force=True)[1] is f   # Refresh keeps edits
        assert next(x for x in FORECASTS if x["Item"] == "Jar testing")["Amount"] == 2676    # fixture untouched
    finally:
        os.environ.pop("DEMO")


# ---------------------------------------------------------------- page
def test_editor_renders():
    from bhf import web
    from bhf.model import portfolio
    s = live_store()
    pf = portfolio(*s.data, today="2026-10-05")
    page = web.tpl.env.get_template("project.html").render(request=None, pf=pf, tabs=[], user="BHF", forecast_items=[],
                                                           active="BHF26001", v=pf["views"][0], err="Amount must be a number")
    assert page.count('action="/p/BHF26001/forecast"') == 15                    # 13 lines + an add form per block
    assert "Money in — customer" in page and "Money out — costs" in page and "Still to place" in page
    d = page[page.index('id="d-forecast"'):]
    assert d.index("<b>Main contract</b>") < d.index("Money out — costs") < d.index("Still to place") < d.index("<b>Site install - mechanical contractor</b>")
    i = page.index('id="fl-'); assert 'name="direction"' in page[i:] and 'data-open="d-forecast" data-line=' in page
    assert page.count("<form") == page.count("</form>")                         # no nested / broken forms
    assert 'value="543,655.14"' in page and "$13,343 over" in page              # forecast next to actual
    assert 'data-base="1564700.0"' in page and 'value="25"' in page             # customer milestone, % shown as 25
    assert "Not saved: Amount must be a number" in page


# ---------------------------------------------------------------- Forecasts sheet tidy
def test_tidy_plan_groups_and_orders():
    projects = PROJECTS + [{"Project": "BHF25015", "Name": "CCEP Fiji"}]
    rows = [dict(f, _id=i) for i, f in enumerate(FORECASTS)] + [
        {"Item": "Freight", "Project": "BHF25015", "Direction": "Out", "_id": 50},
        {"Item": "BHF26001 Stacked Farm DAF UF RO 8", "_id": 60},
        {"Item": "stray note", "_id": 70}]
    p = tidy_forecasts.plan(projects, rows)
    assert [g["code"] for g in p["groups"]] == ["BHF25015", "BHF26001"]
    g = p["groups"][1]
    assert g["parent"] == 60 and p["groups"][0]["parent"] is None and p["groups"][0]["title"] == "BHF25015 CCEP Fiji"
    assert [f["Item"] for f in g["rows"][:2]] == ["Pre-flattening design", "Main contract"]   # In first, by date
    out = [str(f.get("Expected Date") or "~") for f in g["rows"] if f["Direction"] == "Out"]
    assert out == sorted(out) and out[-1] == "~"                                       # undated lines last
    assert [f["Item"] for f in p["loose"]] == ["stray note"]


def test_statement_and_drills_render():
    from bhf import web
    from bhf.model import portfolio
    from tests.fixture_26001 import PROJECTS as P, FORECASTS as F, TXNS as T, SCHEDULE as S
    old = {"Project": "BHF25009", "Name": "Old job", "Status": "Complete", "Contract Value": 1000}
    tx = T + [{"Doc #": "INV-OLD", "Project": "BHF25009", "Type": "Invoice", "Direction": "In", "Amount": 1000,
               "Date": "2025-12-10", "Paid Date": "2026-01-15"}]
    pf = portfolio(P + [old], F, tx, S, today="2026-10-05")
    tabs = [(v["code"], v["name"]) for v in pf["views"]]
    base = {"request": None, "pf": pf, "tabs": tabs, "user": "BHF", "forecast_items": [], "as_at": "demo data"}
    page = web.tpl.env.get_template("project.html").render(**base, active="BHF26001", v=pf["views"][0])
    for want in ("Before FY27", "FY27 to date", "Total to date", "Still to come", "final position", "by document date",
                 "Incoming", "Outgoing", "35% FAT", "of which FY27", "How to read the cash chart"):
        assert want in page, want
    for d in ("d-out", "d-margin", "d-contract", "d-chart", "d-forecast"):
        assert f'data-open="{d}"' in page and f'id="{d}"' in page, d                 # every figure drills somewhere
    # Outgoing: one row per line, its payment stages hidden beneath it; each line opens its side panel
    out = page[page.index('aria-label="Outgoing"'):page.index('class="pdash"')]
    i = out.index(">Bondalti - main system supply</button>")
    assert i < out.index("35% FAT") < out.index(">Bondalti - engineering design (pre-flattening)</button>")
    for g in pf["views"][0]["ledger_lines"]["out"]:
        assert f'data-open="lp-{g["key"]}"' in out and f'id="lp-{g["key"]}"' in page and f'data-edit="{g["key"]}"' in page
    q = pf["views"][0]["position"]
    assert f"{q['opening']:,.0f}" in page and f"{q['out_fy']:,.0f}" in page               # FY split shown
    assert page.count('id="fc-') == len(pf["views"][0]["forecast_lines"]) + 2           # each form once (panels borrow it)
    from bhf.model import budget_view
    from tests.fixture_26001 import BUDGET
    ov = web.tpl.env.get_template("overview.html").render(**base, bv=budget_view(BUDGET, pf["views"], "2026-10-06"), active="overview")
    assert "FY27 Systems sales vs budget" in ov and "6,925,000" in ov and "Still to win this year" in ov
    assert "BHF25009" not in ov.split('class="odash"')[0]                          # not a tab, not in the live table
    assert "Financial years" in ov and "data from Dec 2025" in ov and "Old job" in ov   # but in the FY breakdown
    for d in ("d-o-in", "d-o-out", "d-o-now", "d-o-final", "d-o-contract", "d-fyall", "d-chart"):
        assert f'data-open="{d}"' in ov and f'id="{d}"' in ov, d


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("PASS", name)

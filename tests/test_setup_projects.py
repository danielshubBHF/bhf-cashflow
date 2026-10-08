"""Automatic project set-up from the costing: replicate the costed lines, labour to Internal Labour."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import setup_projects as sp, costing


def tab():
    rows = [[None] * 6 for _ in range(13)]
    rows[2] = ["Project Cost:", 100] + [None] * 4
    rows.append(["Component", "Size", "Model", "Description / Comment", "Quantity", "Extended Cost"])
    rows += [["Welkin - System", None, None, None, None, None],
             ["Stainless UF", None, None, "", 1, 24364.52],
             ["Plastic UF (option not taken)", None, None, "", 0, 0],
             ["BHF Supply", None, None, None, None, None],
             ["Cartridges", None, "Aquacorp", "", 1, 216],
             ["Project Management/Supervision", None, None, None, None, None],
             ["Project Management/Supervision", None, "2 weeks", "AUD 100 / hr = 800 Per Day", 10, 8000],
             ["Freight", None, "Local", "", 1, 2000],
             ["Contingency", None, None, "", 1, 10000],
             ["Sub Total", None, None, None, None, None]]
    return costing.parse_tab(rows)


def test_costing_lines_are_replicated_and_labour_split_out():
    c = tab()
    assert [l["item"] for l in c["lines"]] == ["Stainless UF", "Cartridges", "Project Management/Supervision", "Freight", "Contingency"]
    rows, labour = sp.forecast_rows("BHF26099", {**c, "tab": "T"}, "costing.xlsx (tab T)", "Graphite Energy", 50000)
    assert labour == 8000
    out = [r for r in rows if r["Direction"] == "Out"]
    assert [(r["Item"], r["Amount"]) for r in out] == [("Stainless UF", 24364.52), ("Cartridges", 216), ("Freight", 2000), ("Contingency", 10000)]
    assert out[0]["Party"] == "Welkin" and out[1]["Party"] == "Aquacorp"             # supplier from the section / column
    assert out[2]["Cost Type"] == "Freight & Logistics" and out[3]["Cost Type"] == "Other"
    main = rows[0]
    assert main["Item"] == "Main contract" and main["Amount"] == 50000 and main["Notes"].startswith(sp.SETUP_NOTE)
    assert round(sum(r["Amount"] for r in out) + labour, 2) == c["total_cost"]           # nothing lost


def test_set_up_project_is_flagged_until_checked():
    from bhf.model import project_view
    from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE
    p = {**PROJECTS[0], "Setup": "Set up from costing x.xlsx (tab T) on 08/10/26: check the lines"}
    v = project_view(p, FORECASTS, TXNS, SCHEDULE, "2026-10-08")
    assert any(m["kind"] == "setup" for m in v["flag_meta"])


def test_pick_matches_customer_by_words_and_skips_reference_tabs():
    from bhf import costing
    tab = lambda name, cust, cost, bid: {"tab": name, "head": {"customer": cust, "bid": bid}, "total_cost": cost, "total_sell": 0}
    tabs = [tab("Live", "CCEP - Fiji UF60 + ACF", 1_125_891, 1_420_000), tab("Old", "Other Co - plant", 900_000, 1_200_000),
            tab("Pumps", None, 1, 0)]
    assert costing.pick(tabs, None, "CCEP Fiji Limited")["tab"] == "Live"
    assert costing.pick(tabs[1:], None, "CCEP Fiji")["tab"] == "Old"        # only a foreign tab: still used, never Pumps


def test_netsuite_comes_later_links_job_and_takes_sales_order(monkeypatch):
    """Set up from the costing with no NetSuite job: a later run links the job and swaps the bid price for the SO."""
    from bhf import netsuite
    note = "Set up from costing c.xlsx (tab T) on 08/10/26; contract from the costing's bid price until a sales order is raised"
    tables = {"projects": [{"_id": 1, "Project": "BHF26099", "Status": "Live", "Contract Value": 400000, "Setup": note}],
              "forecasts": [{"_id": 7, "Project": "BHF26099", "Item": "Main contract", "Amount": 400000},
                            {"_id": 8, "Project": "BHF26099", "Item": "Pump", "Amount": 5000}]}
    updates = {}

    class T:
        def __init__(self, sid): self.k = "projects" if sid == "P" else "forecasts"
        def load(self): return [dict(r) for r in tables[self.k]]
        def update(self, u): updates.setdefault(self.k, []).extend(u)
        def ensure_column(self, *a): pass
        def add(self, rows): raise AssertionError("nothing new to add")

    class NS:
        def find_job(self, code): return {"job": 555, "name": "BHF26099 Test"}
        def project_docs(self, job): return [{"type": "SalesOrd", "aud": -419480}]

    monkeypatch.setenv("SMARTSHEET_TOKEN", "x")
    monkeypatch.setattr(sp.config, "SHEETS", {"projects": "P", "forecasts": "F"})
    monkeypatch.setattr(sp, "Table", T)
    monkeypatch.setattr(sp, "smartsheet_cards", lambda h: {"BHF26099": ("BHF26099 Test", 1)})
    monkeypatch.setattr(netsuite, "NetSuite", NS)
    out = sp.run()
    assert "BHF26099: linked to NetSuite job 555" in out
    p = next(u for _, u in updates["projects"] if "NetSuite Job ID" in u)
    assert p["NetSuite Job ID"] == "555"
    cv = next(u for _, u in updates["projects"] if "Contract Value" in u)
    assert cv["Contract Value"] == 419480 and cv["Setup"].endswith("contract from the sales order on " + __import__("datetime").date.today().strftime("%d/%m/%y"))
    assert updates["forecasts"] == [(7, {"Amount": 419480})]                     # Main contract only, not the cost lines


def test_new_sharepoint_folder_starts_a_project_but_old_or_known_ones_do_not(tmp_path, monkeypatch):
    import os, time
    for name in ("BHF26101 New Job", "BHF26001 Known Job", "BHF24005 Old Job", "Templates"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(sp, "CONTRACTED", tmp_path)
    old = time.time() - 400 * 86400
    real_stat = type(tmp_path).stat
    monkeypatch.setattr(type(tmp_path), "stat", lambda self, **k: os.stat_result(
        tuple(real_stat(self, **k))[:9] + (old,)) if "Old Job" in self.name else real_stat(self, **k))
    assert sp.sharepoint_cards({"BHF26001"}) == {"BHF26101": "BHF26101 New Job"}


def test_costing_found_anywhere_in_the_job_folder(tmp_path):
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "Job Costing v2.xlsx").write_bytes(b"x")
    (tmp_path / "Docs" / "Working Cashflow converted from costing.xlsx").write_bytes(b"x")
    assert sp.find_costing(tmp_path).name == "Job Costing v2.xlsx"

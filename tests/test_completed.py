"""Completed projects: final figures from NetSuite documents, and their pages."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import model
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE

TODAY = "2026-10-05"


def done():
    p = {**PROJECTS[0], "Status": "Complete"}
    pf = model.portfolio([p], FORECASTS, TXNS, SCHEDULE, TODAY)
    return pf, model.completed_view(pf["complete"][0], p, TXNS)


def test_completed_figures_tie_to_documents():
    pf, x = done()
    assert not pf["views"] and len(pf["complete"]) == 1
    ins = [t for t in TXNS if t["Direction"] == "In" and t["Type"] not in model.ORDER_TYPES]
    outs = [t for t in TXNS if t["Direction"] != "In" and t["Type"] not in model.ORDER_TYPES]
    assert x["invoiced"] == round(sum(t["Amount"] for t in ins), 2)
    assert x["costs"] == round(sum(t["Amount"] for t in outs), 2)
    assert x["gm"] == round(x["invoiced"] - x["costs"], 2)
    assert round(x["received"] + x["owed_to_us"], 2) == x["invoiced"]
    assert round(x["paid"] + x["owed_by_us"], 2) == x["costs"]
    assert round(sum(s["amount"] for s in x["by_supplier"]), 2) == x["costs"]
    assert round(sum(f["invoiced"] for f in x["fys"]), 2) == x["invoiced"]
    assert x["nm"] == round(x["gm"] - 98100, 2)                       # Internal Labour from the Projects row
    assert x["open_pos"]                                                # fixture POs carry no NetSuite status


def test_completed_pages_render():
    from bhf import web
    pf, x = done()
    common = dict(request=None, pf=pf, tabs=[], n_complete=1, user="BHF", active="completed", as_at="")
    page = web.tpl.env.get_template("completed.html").render(rows=[x], tot=model.completed_totals([x]), **common)
    assert 'href="/completed/BHF26001"' in page and "Gross margin" in page and 'href="/completed"' in page
    one = web.tpl.env.get_template("completed_one.html").render(x=x, **common)
    assert "Costs by supplier" in one and "POs still open in NetSuite" in one and "By financial year" in one
    assert one.count("<table") == 5

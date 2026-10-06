import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf.sync import follow_bills, link_forecast, order_no
from bhf.model import project_view
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE

def test_po_number_wins():
    d = {"type": "VendBill", "party": "Enkrott, SA", "from_doc": "PO005995"}
    assert link_forecast(FORECASTS, d, order_no(d))[0] == "Bondalti - main system supply"

def test_supplier_name_links_new_po_and_writes_back():
    fc = [dict(f) for f in FORECASTS]
    fc[-2]["Party"] = "Apex Mechanical"                      # PM names the install contractor
    d = {"type": "PurchOrd", "party": "Apex Mechanical Services Pty Ltd", "tranid": "PO006400"}
    item, append_to = link_forecast(fc, d, order_no(d))
    assert item == "Site install - mechanical contractor" and append_to is not None

def test_ambiguous_supplier_goes_to_unassigned():
    d = {"type": "PurchOrd", "party": "Enkrott, SA", "tranid": "PO006999"}   # 4 Bondalti forecasts
    assert link_forecast(FORECASTS, d, order_no(d))[0] is None

def _install(view):
    return next(l for l in view["lines_out"] if l["item"].startswith("Site install"))

def test_install_po_half_or_double():
    base = [dict(t) for t in TXNS]
    half = base + [{"Doc #": "PO006400", "Project": "BHF26001", "Type": "PO", "Direction": "Out", "Party": "Apex",
                    "PO / Order #": "PO006400", "Amount": 100000, "Forecast": "Site install - mechanical contractor"}]
    l = _install(project_view(PROJECTS[0], FORECASTS, half, SCHEDULE, "2026-10-05"))
    assert l["open_commit"] == 100000 and l["remaining"] == 100000 and l["expected"] == 200000
    closed = [dict(f, Closed=True) if f["Item"].startswith("Site install") else f for f in FORECASTS]
    l = _install(project_view(PROJECTS[0], closed, half, SCHEDULE, "2026-10-05"))
    assert l["expected"] == 100000 and l["remaining"] == 0           # bucket closed: saving shows
    double = base + [dict(half[-1], Amount=400000)]
    l = _install(project_view(PROJECTS[0], FORECASTS, double, SCHEDULE, "2026-10-05"))
    assert l["expected"] == 400000 and l["overrun"] == 200000         # overrun shows

if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("PASS", name)

def test_stock_issue_links_to_stock_line():
    fc = FORECASTS + [{"Item": "BHF filters (from stock)", "Project": "BHF26001", "Direction": "Out", "Party": "Stock issue"}]
    d = {"type": "InvAdjst", "party": "BHF26001 Stacked Farm DAF UF RO 8"}
    assert link_forecast(fc, d, order_no(d))[0] == "BHF filters (from stock)"

if __name__ == "__main__":
    test_stock_issue_links_to_stock_line(); print("PASS stock")

def test_bill_credit_follows_its_bill_to_the_po():
    docs = [{"type": "VendBill", "tranid": "SMTIN-AU-003", "from_doc": "PO005995", "party": "Enkrott, SA"},
            {"type": "VendCred", "tranid": "SMTIN-AU-003", "from_doc": "SMTIN-AU-003", "party": "Enkrott, SA"}]
    follow_bills(docs)
    assert order_no(docs[1]) == "PO005995"
    assert link_forecast(FORECASTS, docs[1], order_no(docs[1]))[0] == "Bondalti - main system supply"

if __name__ == "__main__":
    test_bill_credit_follows_its_bill_to_the_po(); print("PASS bill credit")

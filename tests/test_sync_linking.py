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


# ---------------------------------------------------------------- variation lines added automatically
def test_customer_invoice_with_new_po_becomes_a_variation_line():
    from bhf.sync import auto_variation
    pf = [{"Item": "Main contract", "Direction": "In", "Party": "Stacked Farm", "Amount": 1564700, "PO / Order #": "PO-0007"}]
    inv = {"type": "CustInvc", "tranid": "INV023001", "trandate": "12/10/2026", "aud": "-48000", "party": "BHF26001 Stacked Farm"}
    line = auto_variation(pf, inv, "PO-0019", {})
    assert line["Direction"] == "In" and line["Cost Type"] == "Variation" and line["Amount"] == 48000
    assert line["PO / Order #"] == "PO-0019" and line["Party"] == "Stacked Farm" and line["Notes"].startswith("Auto-added")
    assert auto_variation(pf, inv, "PO-0007", {}) is None                       # the contract's own PO
    assert auto_variation(pf, inv, "", {}) is None                              # no customer PO: can't tell
    assert auto_variation([{**pf[0], "PO / Order #": ""}], inv, "PO-0019", {}) is None   # contract PO not known yet


def test_supplier_po_becomes_a_variation_only_when_its_line_is_fully_ordered():
    from bhf.sync import auto_variation
    pf = [{"Item": "Welkin - system supply", "Direction": "Out", "Party": "Welkin", "Amount": 170000, "PO / Order #": "PO005862"}]
    po = {"type": "PurchOrd", "tranid": "PO006400", "trandate": "12/10/2026", "aud": "9000", "party": "Welkin"}
    assert auto_variation(pf, po, "PO006400", {"Welkin - system supply": 120000}) is None     # still forecast to order
    line = auto_variation(pf, po, "PO006400", {"Welkin - system supply": 170000})
    assert line["Item"] == "Welkin - variation PO006400" and line["Direction"] == "Out" and line["Amount"] == 9000
    other = {**po, "party": "RS Online"}
    assert auto_variation(pf, other, "PO006401", {"Welkin - system supply": 170000}) is None  # no line: stays Unlinked


def test_auto_added_line_is_flagged_until_acknowledged():
    from bhf.model import project_view
    from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE
    extra = {"Item": "Variation - customer PO PO-0019", "Project": "BHF26001", "Direction": "In", "Cost Type": "Variation",
             "Party": "Stacked Farm", "Amount": 48000, "PO / Order #": "PO-0019",
             "Notes": "Auto-added by the sync from invoice INV023001 on 2026-10-12: check the name, value and payment milestones"}
    v = project_view(PROJECTS[0], FORECASTS + [extra], TXNS, SCHEDULE, "2026-10-13")
    [m] = [m for m in v["flag_meta"] if m["kind"] == "newvar"]
    assert "Variation - customer PO PO-0019" in m["text"] and "$48,000" in m["text"]


def test_bill_without_supplier_reference_gets_a_distinct_number():
    from bhf.sync import doc_no
    assert doc_no({"type": "VendBill", "tranid": "WK20260307", "id": "1"}) == "WK20260307"
    assert doc_no({"type": "VendBill", "tranid": "", "id": "327062"}) == "no ref #327062"
    assert doc_no({"type": "VendBill", "tranid": "Bill", "id": "327063"}) == "no ref #327063"
    assert doc_no({"type": "PurchOrd", "tranid": "PO005570", "id": "9"}) == "PO005570"

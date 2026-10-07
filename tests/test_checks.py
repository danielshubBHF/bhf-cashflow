"""Section 3 bookkeeping checks, and setting payment terms from a needs-attention item."""
import copy, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf.model import project_view, portfolio
from bhf.store import Store, parse_split
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE

TODAY = "2026-10-05"


def kinds(v, kind):
    return [t for k, t, _ in v["flags"] if k == kind]


def txn(**kw):
    base = {"Project": "BHF26001", "Direction": "Out", "Amount": 1000, "Date": "2026-06-01"}
    return {**base, **kw}


def view(forecasts=(), txns=(), schedule=(), today=TODAY):
    return project_view(PROJECTS[0], FORECASTS + list(forecasts), TXNS + list(txns), SCHEDULE + list(schedule), today)


def test_fixture_raises_only_the_real_stale_pos():
    v = view()
    for k in ("wrong", "miscode", "received", "standalone", "custpo"):
        assert not kinds(v, k), k
    [c] = kinds(v, "closepo")                   # the fixture marks POs "Open": jar testing's is billed in full
    assert c.startswith("PO005893") and "billed in full" in c
    d2, bondalti = sorted(kinds(v, "stale"))    # both true in NetSuite too
    assert d2.startswith("PO005725 Enkrott") or bondalti.startswith("PO005725 Enkrott")
    stale = {t.split()[0]: t for t in kinds(v, "stale")}
    assert "Close the PO in NetSuite, or chase?" in stale["PO006017"]       # $27,425 unbilled since 30 Jun
    assert "nothing billed since 2026-02-13" in stale["PO005725"]           # pre-flattening engineering: no bill since Feb


def test_wrong_project_po_listed_on_another_jobs_forecast():
    other = {"Project": "BHF26003", "Item": "Pumps", "Direction": "Out", "PO / Order #": "PO009999", "Amount": 5000}
    po = txn(**{"Doc #": "PO009999", "Type": "PO", "PO / Order #": "PO009999", "Party": "Grundfos"})
    [t] = kinds(view([other], [po]), "wrong")
    assert "BHF26003" in t and "Pumps" in t


def test_miscoded_po_sitting_unlinked_on_another_tracked_job():
    line = {"Project": "BHF26001", "Item": "Dosing pumps", "Direction": "Out", "Party": "Prominent Fluid Controls", "Amount": 10000}
    elsewhere = [{"Project": "BHF26003", "Item": "Something", "Direction": "Out"},
                 txn(Project="BHF26003", **{"Doc #": "PO008888", "Type": "PO", "PO / Order #": "PO008888",
                                            "Party": "ProMinent Fluid Controls Pty Ltd", "Amount": 11500})]
    [t] = kinds(view([line, elsewhere[0]], [elsewhere[1]]), "miscode")
    assert "PO008888" in t and "BHF26003" in t
    elsewhere[1]["Amount"] = 20000                                    # outside ±25%: no flag
    assert not kinds(view([line, elsewhere[0]], [elsewhere[1]]), "miscode")
    elsewhere[1]["Amount"] = 11500                                    # a job with no forecast lines isn't tracked
    assert not kinds(view([line], [elsewhere[1]]), "miscode")


def test_received_not_billed_and_stale_po():
    rec = txn(**{"Doc #": "PO007001", "Type": "PO", "PO / Order #": "PO007001", "Party": "RS", "Status": "Pending Bill",
                 "Date": "2026-08-01", "Forecast": "Jar testing"})
    stale = txn(**{"Doc #": "PO007002", "Type": "PO", "PO / Order #": "PO007002", "Party": "MDC", "Status": "Pending Receipt",
                   "Date": "2026-05-01", "Forecast": "Jar testing"})
    v = view(txns=[rec, stale])
    assert any("PO007001" in t for t in kinds(v, "received"))
    assert any("PO007002" in t and "nothing billed" in t for t in kinds(v, "stale"))
    assert not kinds(view(txns=[rec, stale], today="2026-08-20"), "received")     # within 30 days of the PO
    assert not kinds(view(txns=[rec, stale], today="2026-07-20"), "stale")        # within 90 days


def test_po_matched_by_a_standalone_bill_is_done_and_closed():
    po = txn(**{"Doc #": "PO007003", "Type": "PO", "PO / Order #": "PO007003", "Party": "Plumblux", "Status": "Pending Bill",
                "Forecast": "Jar testing", "Amount": 1440})
    bill = txn(**{"Doc #": "114", "Type": "Bill", "PO / Order #": "", "Party": "Plumblux", "Forecast": "Jar testing",
                  "Amount": 1440, "NetSuite ID": "55"})
    v = view(txns=[po, bill])
    [t] = [x for x in kinds(v, "closepo") if "PO007003" in x]
    assert "bill 114" in t and "Close PO in NS" in t
    assert not kinds(v, "standalone")
    assert not any("PO007003" in t for t in kinds(v, "received") + kinds(v, "stale"))
    jar = next(l for l in v["lines_out"] if l["item"] == "Jar testing")
    assert not any(r["kind"] == "order" for r in v["ledger"]["out"] if r["line"] == "Jar testing")   # nothing still to pay on it


def test_po_billed_in_full_in_its_currency_is_done():
    po = txn(**{"Doc #": "PO007004", "Type": "PO", "PO / Order #": "PO007004", "Party": "Welkin", "Status": "Pending Bill",
                "Forecast": "Jar testing", "Amount": 10000, "Currency Amount": "US Dollar 6,500.00"})
    bill = txn(**{"Doc #": "WK1", "Type": "Bill", "PO / Order #": "PO007004", "Party": "Welkin", "Forecast": "Jar testing",
                  "Amount": 10400, "Currency Amount": "US Dollar 6,500.00", "NetSuite ID": "56"})
    [t] = [x for x in kinds(view(txns=[po, bill]), "closepo") if "PO007004" in x]
    assert "billed in full" in t


def test_cost_line_with_nothing_in_netsuite_on_a_mostly_invoiced_job():
    line = {"Project": "BHF26001", "Item": "Avanale UF System", "Direction": "Out", "Party": "Avanale", "Amount": 25000,
            "Expected Date": "2026-08-01", "Cost Type": "Equipment"}
    assert not kinds(view([line]), "nocost")                      # fixture job isn't 80% invoiced
    inv = txn(**{"Doc #": "INV99", "Type": "Invoice", "Direction": "In", "PO / Order #": "PO-0007", "Forecast": "Main contract",
                 "Amount": 1300000})
    [t] = kinds(view([line], [inv]), "nocost")
    assert "Avanale UF System" in t and "Cost may be missing from NetSuite" in t


def test_supplier_recovery_reduces_cost_until_credited():
    from bhf.model import project_view
    rec = {"Project": "BHF26001", "Item": "Welkin back-charge (Mifelec enclosure)", "Direction": "Out", "Party": "Welkin",
           "Amount": 3597.40, "Cost Type": "Supplier recovery", "Expected Date": "2026-11-30"}
    base = view()
    v = view([rec])
    assert round(base["position"]["out_tocome"] - v["position"]["out_tocome"], 2) == 3597.40
    credit = txn(**{"Doc #": "VC1", "Type": "Bill Credit", "Party": "Welkin", "Forecast": rec["Item"], "Amount": -3597.40,
                    "Paid Date": "2026-11-20", "NetSuite ID": "57"})
    v2 = view([rec], [credit])
    l = next(x for x in v2["lines_out"] if x["item"] == rec["Item"])
    assert l["remaining"] == 0 and round(v2["position"]["out_tocome"], 2) == round(base["position"]["out_tocome"], 2)


def test_customer_invoice_without_or_with_unknown_po():
    inv = txn(**{"Doc #": "INV9", "Type": "Invoice", "Direction": "In", "PO / Order #": "", "Forecast": "Main contract"})
    inv2 = txn(**{"Doc #": "INV10", "Type": "Invoice", "Direction": "In", "PO / Order #": "XYZ-1", "Forecast": "Main contract"})
    t = kinds(view(txns=[inv, inv2]), "custpo")
    assert any("INV9" in x and "no customer PO" in x for x in t) and any("INV10" in x and "XYZ-1" in x for x in t)


def test_unlinked_expense_waits_a_fortnight():
    e = txn(**{"Doc #": "EXP1", "Type": "Expense", "Party": "Neil Morrow", "Date": "2026-09-28", "NetSuite ID": "77"})
    assert not any("EXP1" in t for t in kinds(view(txns=[e]), "link"))
    e["Date"] = "2026-09-01"
    assert any("EXP1" in t and "34 days" in t for t in kinds(view(txns=[e]), "link"))


def test_shared_unearned_account_flags_both_projects():
    a = {**PROJECTS[0], "Unearned Acct ID": "1149"}
    b = {**PROJECTS[0], "Project": "BHF26003", "Unearned Acct ID": "1149.0"}
    pf = portfolio([a, b], FORECASTS, TXNS, SCHEDULE, TODAY)
    for v in pf["views"]:
        assert any("shared" == k for k, _, _ in v["flags"])
    assert pf["totals"]["flags"] == sum(len(v["flags"]) for v in pf["views"])


# ---------------------------------------------------------------- payment terms from the attention list
def test_parse_split():
    assert parse_split("30/70")[0] == [(30.0, ""), (70.0, "")]
    assert parse_split("30% deposit, 40% on FAT, 30% delivery")[0] == [(30.0, "Deposit"), (40.0, "FAT"), (30.0, "Delivery")]
    assert "100%" in parse_split("30/60")[1]
    assert parse_split("net 30")[1]


def test_apply_terms_replaces_milestones_and_clears_the_flag():
    os.environ["DEMO"] = "1"
    try:
        s = Store()
        d = s.load()
        po = "PO006153"                              # Enkrott variation PO, no schedule yet
        assert s.apply_terms("BHF26001", po, "50% deposit / 50% delivery") is None
        ms = sorted([x for x in d[3] if x.get("PO / Order #") == po], key=lambda x: x["Seq"])
        assert [x["Percent"] for x in ms] == [.5, .5] and all(x["Confirmed"] for x in ms)
        value = sum(t["Amount"] for t in d[2] if t.get("Type") == "PO" and t.get("PO / Order #") == po)
        assert round(sum(x["Amount"] for x in ms), 2) == round(value, 2)
        assert [x["Milestone"] for x in ms] == ["Deposit", "Delivery"]
        v2 = project_view(d[0][0], d[1], d[2], d[3], TODAY)
        assert not any(x["po"] == po and x["kind"] == "terms" for x in v2["flag_meta"])
        assert "100%" in s.apply_terms("BHF26001", po, "50/40")
    finally:
        os.environ.pop("DEMO")


def test_confirm_terms_as_they_are():
    os.environ["DEMO"] = "1"
    try:
        s = Store()
        d = s.load()
        po = "PO005995"
        for x in d[3]:
            if x.get("PO / Order #") == po:
                x["Confirmed"] = False
        v = project_view(d[0][0], d[1], d[2], d[3], TODAY)
        assert any(m["po"] == po and m["kind"] == "terms" for m in v["flag_meta"])
        before = [(x["Percent"], x["Amount"]) for x in d[3] if x.get("PO / Order #") == po]
        assert s.apply_terms("BHF26001", po, confirm=True) is None
        assert [(x["Percent"], x["Amount"]) for x in d[3] if x.get("PO / Order #") == po] == before
        assert all(x["Confirmed"] for x in d[3] if x.get("PO / Order #") == po)
        v = project_view(d[0][0], d[1], d[2], d[3], TODAY)
        assert not any(m["po"] == po and m["kind"] == "terms" for m in v["flag_meta"])
    finally:
        os.environ.pop("DEMO")

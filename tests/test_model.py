import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf.model import project_view
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE

v = project_view(PROJECTS[0], FORECASTS, TXNS, SCHEDULE, today="2026-10-05")

def test_ties_to_netsuite():
    assert round(v["rev"]["billed"], 2) == 832350.00          # NS invoiced
    assert round(v["cost"]["billed"], 2) == 190507.41         # NS bills
    assert round(v["cost"]["paid"], 2) == 190507.41

def test_bucket_logic():
    jar = next(l for l in v["lines_out"] if l["item"] == "Jar testing")
    assert jar["expected"] == 3224 and round(jar["overrun"]) == 548    # PO above forecast -> overrun
    install = next(l for l in v["lines_out"] if l["item"].startswith("Site install"))
    assert install["remaining"] == 200000                              # no PO yet -> full forecast

def test_fx_gain_is_under_not_to_place():
    uf = next(l for l in v["lines_out"] if l["item"] == "UF membranes")        # USD PO, first bill at a better rate
    assert uf["remaining"] == 0 and round(uf["overrun"], 2) == -24.73 and uf["fx"] == -24.73

def test_fx_overrun_is_labelled():
    flags = [t for k, t, _ in v["flags"] if k == "over"]
    assert "Bondalti - main system supply: $13,343 over forecast, all exchange-rate movement" in flags
    assert "Jar testing: $548 over forecast" in flags                         # a real overrun, no FX

def test_closed_po_stops_counting_unbilled_balance():
    from tests.fixture_26001 import FORECASTS as F
    tx = [dict(t, Status="Closed") if t["Doc #"] == "PO006017" else t for t in TXNS]    # D2 review: $9,295 billed of $36,720
    line = next(l for l in project_view(PROJECTS[0], F, tx, SCHEDULE, "2026-10-05")["lines_out"]
                if l["item"] == "D2 Process - 3rd party design review")
    assert round(line["open_commit"], 2) == 0 and round(line["expected"], 2) == 36720    # forecast still stands...
    closed = [dict(f, Closed=True) if f["Item"] == "D2 Process - 3rd party design review" else f for f in F]
    line = next(l for l in project_view(PROJECTS[0], closed, tx, SCHEDULE, "2026-10-05")["lines_out"]
                if l["item"] == "D2 Process - 3rd party design review")
    assert round(line["expected"], 2) == 9294.55 and round(line["overrun"], 2) == -27425.45  # ...until the line is closed

def test_standalone_bill_uses_up_open_po():
    # Pearl Filtration (26005): PO005884 $1,144 still "Pending Bill", paid by a bill raised without the PO
    f = [{"Item": "Pearl housing", "Project": "BHF26001", "Direction": "Out", "Party": "Pearl", "Amount": 1144, "PO / Order #": "PO005884"}]
    tx = [{"Doc #": "PO005884", "Project": "BHF26001", "Type": "PO", "Direction": "Out", "PO / Order #": "PO005884", "Amount": 1144,
           "Status": "Pending Bill", "Forecast": "Pearl housing"},
          {"Doc #": "00050425", "Project": "BHF26001", "Type": "Bill", "Direction": "Out", "PO / Order #": "", "Amount": 1144,
           "Paid Date": "2026-05-09", "Forecast": "Pearl housing"}]
    for sched in ([], [{"Project": "BHF26001", "PO / Order #": "PO005884", "Seq": 1, "Percent": 1, "Amount": 1144, "Confirmed": False}]):
        l = project_view(PROJECTS[0], f, tx, sched, "2026-10-05")["lines_out"][0]
        assert l["open_commit"] == 0 and l["expected"] == 1144, (sched, l["open_commit"], l["expected"])   # not $2,288

def test_terms_flag_only_while_money_is_still_to_come():
    s = [dict(m, Confirmed=False) if m["PO / Order #"] in ("PO006182", "PO006180") else m for m in SCHEDULE]
    flags = [t for k, t, _ in project_view(PROJECTS[0], FORECASTS, TXNS, s, "2026-10-05")["flags"] if k == "terms"]
    assert any("PO006182" in t for t in flags) and any("PO006180" in t for t in flags)     # both still owe money
    tx = TXNS + [{"Doc #": "AQ-1", "Project": "BHF26001", "Type": "Bill", "Direction": "Out", "PO / Order #": "PO006182",
                  "Amount": 13035, "Forecast": "RO membranes"}]
    s = [dict(m, **{"Billed Doc": "AQ-1"}) if m["PO / Order #"] == "PO006182" else m for m in s]
    flags = [t for k, t, _ in project_view(PROJECTS[0], FORECASTS, tx, s, "2026-10-05")["flags"] if k == "terms"]
    assert not any("PO006182" in t for t in flags)                                          # fully billed: no noise

def test_cash_now_splits_at_financial_year_start():
    # 26001 on 2026-10-05 (FY27 from 1 Jul 26): all receipts and most payments were before 1 July
    p = v["position"]
    assert round(p["in_before"], 2) == 832350.00 and p["in_fy"] == 0
    assert round(p["out_before"] + p["out_fy"], 2) == round(p["out_done"], 2)
    assert round(p["out_fy"], 2) == round(9294.55 + 3224 + 1549.14, 2)    # D2 (27 Jul), Scinor 20% (21 Aug), jar testing (2 Oct)
    assert round(p["opening"] + p["fy_net"], 2) == round(p["now"], 2)
    g = next(g for g in v["ledger_lines"]["out"] if g["line"]["item"] == "Jar testing")
    assert g["done_fy"] == 3224
    from bhf.model import fy_start
    assert fy_start("2026-10-06") == "2026-07-01" and fy_start("2027-03-01") == "2026-07-01" and fy_start("2026-06-30") == "2025-07-01"

def test_breakdown_adds_up_on_every_line():
    for l in v["lines_out"] + v["lines_in"]:
        if not l.get("unassigned"):
            assert abs(l["committed"] + l["bill_diff"] + l["remaining"] - l["expected"]) < 0.01, l["item"]

def test_cash_curve_balances():
    total_out = sum(c["out_actual"] + c["out_future"] for c in v["curve"])
    assert abs(total_out - v["cost"]["expected"]) < 1
    total_in = sum(c["in_actual"] + c["in_future"] for c in v["curve"])
    assert abs(total_in - v["rev"]["expected"]) < 1

def test_contract_split_original_and_variations():
    assert v["contract"] == 1614700 and v["contract_orig"] == 1614700 and v["variations"] == 0   # both In lines are milestones
    assert v["contract_diff"] == 0
    f = FORECASTS + [{"Item": "VO1 extra skid", "Project": "BHF26001", "Direction": "In", "Cost Type": "Variation",
                      "Party": "Stacked Farm", "Amount": 70000, "Expected Date": "2027-01-15"}]
    p = dict(PROJECTS[0], **{"Contract Value": 1684700})
    w = project_view(p, f, TXNS, SCHEDULE, today="2026-10-05")
    assert w["contract"] == 1684700 and w["contract_orig"] == 1614700 and w["variations"] == 70000 and w["contract_diff"] == 0
    w = project_view(PROJECTS[0], f, TXNS, SCHEDULE, today="2026-10-05")     # sheet not updated for the VO yet
    assert w["contract"] == 1614700 and w["variations"] == 70000 and w["contract_diff"] == -70000      # shown, not invented
    from bhf.model import portfolio
    t = portfolio([p], f, TXNS, SCHEDULE, today="2026-10-05")["totals"]
    assert t["contract_orig"] == 1614700 and t["variations"] == 70000 and abs(t["nm"] - (1684700 - w["cost"]["expected"] - 98100)) < 1

def test_overdue_moves_to_current_month():
    late = {"Doc #": "B-LATE", "Project": "BHF26001", "Type": "Bill", "Direction": "Out", "Party": "D2 Process",
            "PO / Order #": "PO006017", "Amount": 5000, "Date": "2026-07-01", "Due Date": "2026-08-15",
            "Paid Date": None, "Forecast": "D2 Process - 3rd party design review"}
    w = project_view(PROJECTS[0], FORECASTS, TXNS + [late], SCHEDULE, today="2026-10-05")
    by = {c["month"]: c for c in w["curve"]}
    assert by["2026-08"]["out_future"] == 0 and by["2026-08"]["out_overdue"] == 0        # past month: actuals only
    assert by["2026-10"]["out_overdue"] == 5000 and by["2026-10"]["out_future"] >= 5000 and by["2026-10"]["is_current"]
    for c in w["curve"]:
        if c["is_past"]:
            assert c["in_future"] == c["out_future"] == 0, c["month"]
        if not c["is_current"]:
            assert c["in_overdue"] == c["out_overdue"] == 0, c["month"]
    assert abs(sum(c["out_actual"] + c["out_future"] for c in w["curve"]) - w["cost"]["expected"]) < 1

def test_actual_position_and_projection():
    cur = next(c for c in v["curve"] if c["is_current"])
    assert cur["month"] == "2026-10" and cur["label"] == "Oct 26"
    assert abs(cur["actual_position"] - v["cash_position"]) < 0.01                       # received less paid, to date
    for c in v["curve"]:
        if c["is_past"]:
            assert c["actual_position"] == c["position"], c["month"]                     # history is the real bank
        elif not c["is_current"]:
            assert c["actual_position"] is None
    for a, b in zip(v["curve"], v["curve"][1:]):
        assert b["opening"] == a["position"]
    end = v["curve"][-1]["position"]
    assert abs(end - (v["rev"]["expected"] - v["cost"]["expected"])) < 1                   # sum in - sum out
    assert v["curve"][-1]["month"] == "Undated" and v["low"]["position"] == min(c["position"] for c in v["curve"] if not c["is_past"])

def test_fy_totals():
    from bhf.model import fy_of, month_label
    assert fy_of("2026-06") == "FY26" and fy_of("2026-07") == "FY27" and fy_of("Undated") is None
    assert month_label("2027-01") == "Jan 27"
    fy = {f["fy"]: f for f in v["fy"]}
    assert list(fy) == ["FY26", "FY27"] and v["fy_now"] == "FY27"
    assert fy["FY26"]["from"] == "Feb 2026" and fy["FY26"]["partial"]                      # data starts Feb 2026
    assert fy["FY26"]["received"] == 832350 and fy["FY26"]["in_expected"] == 0          # a past year: actuals only
    assert fy["FY27"]["opening"] == fy["FY26"]["closing"]                                # carries the position over
    assert abs(sum(f["received"] + f["in_expected"] for f in fy.values())
               + sum(c["in_future"] for c in v["curve"] if not c["fy"]) - v["rev"]["expected"]) < 1

def test_editor_blocks_group_by_status():
    blocks = {b["direction"]: b for b in v["blocks"]}
    assert [g["key"] for g in blocks["In"]["groups"]] == ["ordered"]
    out = {g["key"]: [l["item"] for l in g["lines"]] for g in blocks["Out"]["groups"]}
    assert out["to_place"] == ["Site install - mechanical contractor", "Freight - Portugal to Melbourne"]   # no PO, nothing billed
    assert out["ordered"][0] == "Bondalti - main system supply" and "closed" not in out                   # sheet order
    t = blocks["Out"]["totals"]
    assert abs(t["expected"] - v["cost"]["expected"]) < 0.01 and abs(t["paid"] - v["cost"]["paid"]) < 0.01
    assert abs(t["vs"] - (t["expected"] - t["budget"])) < 0.01 and abs(t["on_order"] - (t["billed"] + t["open_commit"])) < 0.01
    closed = [dict(f, Closed=True) if f["Item"] == "Jar testing" else f for f in FORECASTS]
    w = project_view(PROJECTS[0], closed, TXNS + [{"Doc #": "X1", "Project": "BHF26001", "Type": "Bill", "Direction": "Out",
                                                   "Amount": 100, "Party": "Someone"}], SCHEDULE, today="2026-10-05")
    out = {g["key"]: [l["item"] for l in g["lines"]] for g in w["blocks"][1]["groups"]}
    assert out["closed"] == ["Jar testing"] and out["unlinked"] == ["Unassigned"] and "Jar testing" not in out["ordered"]
    assert [l["status"] for l in w["lines_out"]][-1] == "unlinked"

def test_money_stages_add_up():
    for l in v["lines_out"] + v["lines_in"]:
        s = l["stages"]
        assert abs(s["paid"] + s["unpaid"] + s["on_order"] + s["to_place"] - l["expected"]) < 0.01, l["item"]
    for b in v["blocks"]:
        for t in [b["totals"]] + [g["totals"] for g in b["groups"]]:
            s = t["stages"]
            assert abs(s["paid"] + s["unpaid"] + s["on_order"] + s["to_place"] - t["expected"]) < 0.01
    m = v["money"]["out"]
    assert m["paid"] == round(v["cost"]["paid"], 2) and abs(m["expected"] - v["cost"]["expected"]) < 0.01
    bond = next(l for l in v["lines_out"] if l["item"] == "Bondalti - main system supply")["stages"]
    assert bond["paid"] == 176439.72 and bond["unpaid"] == 0 and bond["to_place"] == 0     # 3 milestones still on order

def test_ledger_is_the_cashflow_statement():
    led, pos = v["ledger"], v["position"]
    assert abs(sum(r["amount"] for r in led["in"]) - v["rev"]["expected"]) < 0.01
    assert abs(sum(r["amount"] for r in led["out"]) - v["cost"]["expected"]) < 0.01
    assert pos["in_done"] == v["rev"]["paid"] and abs(pos["out_done"] - v["cost"]["paid"]) < 0.01
    assert abs(pos["now"] - v["cash_position"]) < 0.01 and abs(pos["final"] - v["curve"][-1]["position"]) < 0.01
    fat = [r for r in led["out"] if r["line"] == "Bondalti - main system supply"]
    assert [r["label"] for r in fat] == ["30% advance", "35% FAT", "25% before shipment", "10% commissioning"]
    assert fat[0]["done"] and fat[0]["amount"] == 176439.72 and fat[1]["kind"] == "order"          # FX-correct actual
    assert [r["date"] for r in led["out"] if not r["done"] and r["date"]] == sorted(r["date"] for r in led["out"] if not r["done"] and r["date"])
    late = {"Doc #": "B-LATE", "Project": "BHF26001", "Type": "Bill", "Direction": "Out", "Party": "D2 Process",
            "PO / Order #": "PO006017", "Amount": 5000, "Date": "2026-07-01", "Due Date": "2026-08-15",
            "Forecast": "D2 Process - 3rd party design review"}
    w = project_view(PROJECTS[0], FORECASTS, TXNS + [late], SCHEDULE, today="2026-10-05")
    r = next(r for r in w["ledger"]["out"] if r["doc"] and r["doc"]["Doc #"] == "B-LATE")
    assert r["overdue"] and r["kind"] == "billed" and w["position"]["out_overdue"] == 5000
    assert abs(w["position"]["final"] - w["curve"][-1]["position"]) < 0.01

def test_complete_projects_count_only_for_financial_years():
    from bhf.model import portfolio
    old = {"Project": "BHF25009", "Name": "Old job", "Status": "Complete", "Contract Value": 100000, "Internal Labour": 5000}
    gone = {"Project": "BHF24001", "Name": "Closed job", "Status": "Closed", "Contract Value": 50000}
    f = FORECASTS + [{"Item": "Contract", "Project": "BHF25009", "Direction": "In", "Amount": 100000, "Cost Type": "Customer Milestone"},
                     {"Item": "Contract", "Project": "BHF24001", "Direction": "In", "Amount": 50000}]
    tx = TXNS + [{"Doc #": "INV-OLD", "Project": "BHF25009", "Type": "Invoice", "Direction": "In", "Amount": 100000,
                  "Date": "2025-12-10", "Paid Date": "2026-01-15", "Forecast": "Contract"},
                 {"Doc #": "INV-GONE", "Project": "BHF24001", "Type": "Invoice", "Direction": "In", "Amount": 50000,
                  "Date": "2025-08-01", "Paid Date": "2025-09-01", "Forecast": "Contract"}]
    base = portfolio(PROJECTS, FORECASTS, TXNS, SCHEDULE, today="2026-10-05")
    pf = portfolio(PROJECTS + [old, gone], f, tx, SCHEDULE, today="2026-10-05")
    assert [x["code"] for x in pf["views"]] == ["BHF26001"] and [x["code"] for x in pf["complete"]] == ["BHF25009"]
    assert pf["totals"]["contract"] == base["totals"]["contract"] and pf["curve"] == base["curve"]       # live view untouched
    fy = {x["fy"]: x for x in pf["fy_all"]}
    assert fy["FY26"]["received"] == 832350 + 100000 and fy["FY26"]["invoiced"] == 832350 + 100000      # complete included
    assert fy["FY26"]["from"] == "Dec 2025" and {r["code"] for r in fy["FY26"]["projects"]} == {"BHF26001", "BHF25009"}
    assert "FY25" not in fy                                                                               # closed: nowhere
    assert sum(c["in_actual"] for c in pf["curve_fy"]) == sum(c["in_actual"] for c in base["curve"]) + 100000
    assert fy["FY27"]["current"] and fy["FY27"]["to"] == "Oct 2026" and fy["FY27"]["from"] is None
    assert {x["fy"]: x for x in base["fy_all"]}["FY26"]["from"] == "Jan 2026"                            # derived, not fixed

def test_ledger_grouped_by_line_in_sheet_order():
    g = v["ledger_lines"]
    assert [x["line"]["item"] for x in g["out"]] == [f["Item"] for f in FORECASTS if f["Direction"] == "Out"]
    assert [x["line"]["item"] for x in g["in"]] == ["Main contract", "Pre-flattening design"]
    for side in ("in", "out"):
        for x in g[side]:
            assert abs(sum(r["amount"] for r in x["rows"]) - x["line"]["expected"]) < 0.01, x["line"]["item"]
            assert abs(x["done"] + x["tocome"] - x["line"]["expected"]) < 0.01
            assert all(r["key"] == x["key"] for r in x["rows"])
    bond = next(x for x in g["out"] if x["line"]["item"] == "Bondalti - main system supply")
    assert [r["label"] for r in bond["rows"]] == ["30% advance", "35% FAT", "25% before shipment", "10% commissioning"]
    tx = TXNS + [{"Doc #": "X1", "Project": "BHF26001", "Type": "Bill", "Direction": "Out", "Amount": 100, "Party": "Someone"}]
    w = project_view(PROJECTS[0], FORECASTS, tx, SCHEDULE, today="2026-10-05")["ledger_lines"]["out"]
    assert w[-1]["key"] == "u-out" and w[-1]["rows"][0]["amount"] == 100                      # unlinked last

if __name__ == "__main__":
    for f in (test_ties_to_netsuite, test_bucket_logic, test_fx_gain_is_under_not_to_place, test_fx_overrun_is_labelled,
              test_closed_po_stops_counting_unbilled_balance, test_standalone_bill_uses_up_open_po, test_terms_flag_only_while_money_is_still_to_come, test_cash_now_splits_at_financial_year_start, test_breakdown_adds_up_on_every_line, test_cash_curve_balances,
              test_contract_split_original_and_variations, test_overdue_moves_to_current_month, test_actual_position_and_projection,
              test_fy_totals, test_editor_blocks_group_by_status,
              test_money_stages_add_up, test_ledger_is_the_cashflow_statement, test_complete_projects_count_only_for_financial_years,
              test_ledger_grouped_by_line_in_sheet_order):
        f(); print("PASS", f.__name__)
    print(f"Contract {v['contract']:,.0f} | cost expected {v['cost']['expected']:,.0f} | GM {v['gm']:,.0f} ({v['gm_pct']:.1%}) | NM {v['nm']:,.0f}")
    for f_ in v["flags"]: print(" flag:", f_[0], f_[1])

"""P&L recognition ("flattening") rules agreed with Daniel on 06/10/26, worked on BHF26001."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import pl
from bhf.model import project_view
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE, BUDGET

TODAY = "2026-10-06"
v = project_view(PROJECTS[0], FORECASTS, TXNS, SCHEDULE, TODAY)


def test_standard_stages_and_even_split():
    assert [round(s, 2) for _, _, s in pl.STAGES] == [0.3, 0.2, 0.2, 0.2, 0.1] and pl.parse_months("") == [2, 2, 3, 2, 2]
    s = pl.spread(1000, "2026-04", [2, 2, 3, 2, 2])
    assert list(s) == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02"]
    assert s["2026-04"] == 150 and s["2026-06"] == 100 and s["2026-08"] == 66.67 and s["2026-10"] == 66.66 and s["2027-02"] == 50
    assert round(sum(s.values()), 6) == 1000
    assert pl.parse_months("1/1/2/1/1") == [1, 1, 2, 1, 1] and pl.parse_months("2/2") == [2, 2, 3, 2, 2]


def test_variation_catches_up_to_the_current_stage():
    start, m = "2026-04", [2, 2, 3, 2, 2]
    assert pl.catch_up(100, start, m, "2026-05") == {**{"2026-05": 30.0}, **{k: v for k, v in pl.spread(100, start, m).items() if k >= "2026-06"}}
    assert pl.catch_up(100, start, m, "2026-07")["2026-07"] == 50          # lands in stage 2 design review
    assert round(pl.catch_up(100, start, m, "2026-09")["2026-09"], 6) == 70  # stage 3 FAT
    assert round(pl.catch_up(100, start, m, "2026-12")["2026-12"], 6) == 90  # stage 4 delivery
    assert pl.catch_up(100, start, m, "2027-05") == {"2027-05": 100}         # after the end: all at once
    for lands in ("2026-04", "2026-07", "2026-10", "2027-01"):
        assert round(sum(pl.catch_up(100, start, m, lands).values()), 6) == 100


def test_bhf26001_schedule():
    x = pl.project_pl(PROJECTS[0], v, TODAY, BUDGET["pl_actuals"]["BHF26001"])
    assert x["flattened"] and x["start"] == "2026-04"                  # first deposit invoice INV021864, 11 Apr 26
    assert x["rev_main"] == 1564700 and x["small"] == ["Pre-flattening design"]   # $50k stays unflattened
    rev = {r["month"]: r["rev"] for r in x["rows"]}
    assert rev["2026-04"] == round(1564700 * 0.15, 2)                  # deposit: 30% over Apr-May
    assert rev["2026-01"] == 25000 and rev["2026-03"] == 25000          # pre-flattening design, when invoiced
    assert rev["2026-07"] == round(1564700 * 0.10, 2) and rev["2027-02"] == round(1564700 * 0.05, 2)
    fy = {f["fy"]: f for f in x["fys"]}
    assert fy["FY26"]["rev"] == round(1564700 * 0.40 + 50000, 2)        # Apr-Jun: deposit 30% + half of design review
    assert fy["FY27"]["rev"] == round(1564700 * 0.60, 2)
    assert round(fy["FY26"]["rev"] + fy["FY27"]["rev"], 2) == 1614700
    assert x["cost_total"] == round(v["cost"]["expected"] + 98100, 2)
    assert round(sum(r["cost"] for r in x["rows"]), 2) == x["cost_total"]
    jul = next(r for r in x["rows"] if r["month"] == "2026-07")
    assert jul["rev_act"] == 121103 and jul["rev_diff"] == round(121103 - 156470, 2)   # NetSuite behind the formula
    assert next(r for r in x["rows"] if r["month"] == "2026-11")["rev_diff"] is None    # future: no difference yet
    assert abs(pl.fy_remaining(x, "FY27", TODAY) - (1564700 * 0.60 - 1564700 * 0.10 - 1564700 * 0.2 / 3 * 2)) < 0.02   # Oct-Feb


def test_locked_baseline_vs_amended_shows_what_moves_between_years():
    p = dict(PROJECTS[0], **{"P&L Locked": "2026-04-30", "P&L Locked Start": "2026-04-01", "P&L Locked Stages": "2/2/3/2/2",
                             "P&L Locked Revenue": 1564700, "P&L Locked Cost": 1000000, "P&L Stages": "2/3/3/2/2"})
    x = pl.project_pl(p, v, TODAY)
    fy = {f["fy"]: f for f in x["fys"]}
    # design review stretched to 3 months: nothing moves out of FY26 (still Apr-Jun = deposit + half design review
    # becomes deposit + a third), so FY26 drops and FY27 picks it up
    moved = round(1564700 * 0.2 / 2 - 1564700 * 0.2 / 3, 2)
    assert fy["FY26"]["move_rev"] == -moved and fy["FY27"]["move_rev"] == moved
    assert x["locked"] and x["base_months"] == [2, 2, 3, 2, 2]


def test_small_project_is_recognised_when_invoiced():
    p = dict(PROJECTS[0], **{"Contract Value": 90000})
    f = [dict(l, Amount=90000) if l["Item"] == "Main contract" else l for l in FORECASTS if l["Item"] != "Pre-flattening design"]
    tx = [dict(t, Amount=45000) if t["Doc #"] == "INV021864" else t for t in TXNS if t["PO / Order #"] != "PO-3596"]
    sch = [dict(m, Amount=m["Percent"] * 90000) if m["PO / Order #"] == "PO-0007" else m for m in SCHEDULE]
    x = pl.project_pl(p, project_view(p, f, tx, sch, TODAY), TODAY)
    assert not x["flattened"]
    rev = {r["month"]: r["rev"] for r in x["rows"]}
    assert rev["2026-04"] == 45000 and round(sum(rev.values()), 2) == 90000          # invoice month, rest on its date


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("PASS", name)


def test_total_timeline_splits_in_standard_proportions():
    from bhf import pl
    assert pl.split_total(11) == [2, 2, 3, 2, 2]
    assert pl.split_total(14) == [3, 3, 4, 2, 2]
    assert pl.split_total(5) == [1, 1, 1, 1, 1]
    for n in range(5, 61):
        m = pl.split_total(n)
        assert sum(m) == n and min(m) >= 1
    # a new total wins over the old stages; stages edited to match the total are kept
    assert pl.timing("2/2/3/2/2", "14", [2, 2, 3, 2, 2]) == [3, 3, 4, 2, 2]
    assert pl.timing("4/2/4/2/2", "14", [2, 2, 3, 2, 2]) == [4, 2, 4, 2, 2]
    assert pl.timing("2/2/3/2/2", "11", [2, 2, 3, 2, 2]) == [2, 2, 3, 2, 2]
    assert pl.timing("", "", None) is None

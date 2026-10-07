"""Pipeline: open Systems enquiries projected by the P&L formula, with the PM's overrides."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import pipeline as pp

TODAY = "2026-10-07"


def enq(i, **kw):
    return {"_id": i, "Customer - Opportunity": f"Customer {i}", "BHF Division": "Systems", "EstImated Value $AUD": "$1,000,000", **kw}


def test_only_open_systems_enquiries_count():
    rows = [enq(1), enq(2, **{"BHF Division": "Consumables"}), enq(3, **{"Won/Lost/Dead": "Lost"}),
            enq(4, **{"Move to Won": True}), enq(5, **{"Lead Status": "Closed"}), enq(6, **{"BHF Division": "", "BHF Income Stream": "Systems"})]
    v = pp.view(rows, [], TODAY, "FY27")
    assert sorted(r["id"] for r in v["rows"]) == [1, 6]


def test_defaults_probability_and_start():
    assert pp.default_prob(enq(1, Likelihood="1 - PO Imminent")) == 0.9
    assert pp.default_prob(enq(1, Likelihood="4 - In Discussion")) == 0.25
    assert pp.default_start(enq(1, **{"Estimated Conversion": "0-3 months"}), TODAY) == "2026-12"
    assert pp.default_start(enq(1, **{"Estimated Conversion": "> 12 months"}), TODAY) == "2027-07"
    assert pp.default_start(enq(1, **{"Estimated Order Date ": "2027-02-14"}), TODAY) == "2027-02"
    assert pp.default_start(enq(1), TODAY) is None                                   # no timing: not counted yet
    assert pp.default_prob(enq(1, **{"Likelihood (0-5)": 4.0})) == 0.5
    v = pp.view([enq(1)], [], TODAY, "FY27")
    assert v["rows"][0]["needs_start"] and v["this"] == 0 and v["no_start"] == 1 and v["no_start_value"] == 1000000


def test_formula_split_and_weighting():
    # $1M starting Dec 26 over 2/2/3/2/2: Dec-Jun (7 months) is stages 1-3 = 70% in FY27, the rest (30%) in FY28
    v = pp.view([enq(1, Likelihood="3 - In Negotiation", **{"Estimated Conversion": "0-3 months"})], [], TODAY, "FY27")
    r = v["rows"][0]
    assert r["start"] == "2026-12" and r["this"] == 700000 and r["next"] == 300000
    assert r["this_w"] == 350000 and v["this_w"] == 350000


def test_overrides_and_exclude():
    s = [{"Row ID": "1", "Start": "2027-03-01", "Stages": "1/1/1/1/1", "Probability %": 80, "Value": 500000}]
    r = pp.view([enq(1)], s, TODAY, "FY27")["rows"][0]
    assert r["start"] == "2027-03" and r["months"] == [1, 1, 1, 1, 1] and r["prob"] == 0.8
    assert r["this"] == 450000 and r["this_w"] == 360000 and r["next"] == 50000      # Mar-Jun = stages 1-4 = 90%
    v = pp.view([enq(1), enq(2)], [{"Row ID": "2", "Exclude": True}], TODAY, "FY27")
    assert v["count"] == 1 and [r["include"] for r in v["rows"]] == [True, False]

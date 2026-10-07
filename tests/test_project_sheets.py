"""Readable Smartsheet views: the LIVE cashflow sheet layout and the database shading rules."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import model, project_sheets as ps
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE


def rows():
    v = model.project_view(PROJECTS[0], FORECASTS, TXNS, SCHEDULE, "2026-10-05")
    return v, ps.layout(v, "05 Oct 2026 07:00")


def test_layout_sections_add_up_to_the_app():
    v, r = rows()
    assert [x["cells"]["Item"].split(" ·")[0] for x in r[:3]] == ["Summary", "Money in", "Money out"]
    summary = {c["cells"]["Item"]: c["cells"] for c in r[0]["children"]}
    q = v["position"]
    assert summary["Cash now (received − paid)"]["Done"] == q["now"]
    assert summary["Final position (cash now + to receive − to pay)"]["To come"] == q["final"]
    for sec, side in ((r[1], "in"), (r[2], "out")):
        assert round(sum(l["cells"]["Done"] for l in sec["children"]), 2) == round(sec["cells"]["Done"], 2) == q[f"{side}_done"]
        assert round(sec["cells"]["To come"], 2) == q[f"{side}_tocome"]
        for line in sec["children"]:                       # each line's payments add up to the line
            got = sum((p["cells"]["Done"] or 0) + (p["cells"]["To come"] or 0) for p in line["children"])
            assert abs(got - line["cells"]["Done"] - line["cells"]["To come"]) < 0.02


def test_formats_are_valid_descriptors():
    _, r = rows()
    for f in [r[0]["fmt"], r[1]["fmt"], ps.plain(r[1]["fmt"]), ps.fmt(money=True)]:
        assert len(f.split(",")) == 17
    assert ps.plain(ps.fmt(money=True)).split(",")[11:15] == ["", "", "", ""]


def test_shading_rules():
    assert ps.wanted_format("forecasts", {"Item": "BHF26001 Stacked Farm"}) == ps.fmt(True, ps.WHITE, ps.NAVY)
    assert ps.wanted_format("transactions", {"Direction": "In"}) == ps.fmt(bg=ps.GREEN_BG)
    assert ps.wanted_format("schedule", {"Direction": "Out"}) == ps.fmt(bg=ps.RED_BG)
    assert ps.wanted_format("projects", {"Status": "Complete"}) == ps.fmt(color=ps.GREY_TXT)
    assert ps.wanted_format("transactions", {}) is None

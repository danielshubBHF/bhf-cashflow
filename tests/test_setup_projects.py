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

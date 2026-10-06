"""Tidy the Forecasts sheet so it works as a backup editor.

  * one bold group row per project ("BHF26001 Stacked Farm ..."), projects in code order
  * each project's lines indented under it: incoming first, then by expected date, then name
  * sensible column widths

Group rows have no Project value, so the app and the sync ignore them. Safe to re-run; new lines
added from the dashboard land in their project's group automatically.

  python -m bhf.tidy_forecasts           (prints the plan, writes nothing)
  python -m bhf.tidy_forecasts --apply   (writes it)
"""
import argparse

from . import config
from .smartsheet_db import Table
from .store import group_row

WIDTHS = {"Item": 300, "Project": 85, "Direction": 75, "Cost Type": 160, "Party": 170, "Amount": 110,
          "Expected Date": 110, "PO / Order #": 190, "Closed": 65, "Notes": 360}
BOLD = ",,1,,,,,,,,,,,,,,"                       # Smartsheet format string: bold


def order(f: dict):
    return (f.get("Direction") != "In", str(f.get("Expected Date") or "9999"), str(f.get("Item") or "").lower())


def plan(projects: list, forecasts: list) -> dict:
    names = {p.get("Project"): p.get("Name") or "" for p in projects}
    codes = sorted({f["Project"] for f in forecasts if f.get("Project")})
    groups = []
    for code in codes:
        groups.append({"code": code, "title": f"{code} {names.get(code, '')}".strip(),
                       "parent": group_row(forecasts, code),
                       "rows": sorted((f for f in forecasts if f.get("Project") == code), key=order)})
    loose = [f for f in forecasts if not f.get("Project") and not any(f["_id"] == g["parent"] for g in groups)]
    return {"groups": groups, "loose": loose}


def run(apply: bool = False):
    P, F = Table(config.SHEETS["projects"]), Table(config.SHEETS["forecasts"])
    p = plan(P.load(), F.load())
    for g in p["groups"]:
        print(f"{g['title']}: {'group row exists' if g['parent'] else 'add group row'}, {len(g['rows'])} lines")
        for f in g["rows"]:
            print(f"    {f.get('Direction') or '':3} {f.get('Expected Date') or '':10}  {f.get('Item')}")
    for f in p["loose"]:
        print(f"  left alone (no Project): {f.get('Item')}")
    if not apply:
        print("\nDry run: nothing written. Re-run with --apply to tidy the sheet.")
        return p
    for g in p["groups"]:
        if not g["parent"]:
            g["parent"] = F.add([{"Item": g["title"]}], fmt=BOLD)[0]["id"]
        F.move([f["_id"] for f in g["rows"]], g["parent"])
    F.move_top([g["parent"] for g in p["groups"]])          # groups in project-code order
    for title, w in WIDTHS.items():
        if title in F.col:
            F.set_width(title, w)
    print("Forecasts sheet tidied.")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    run(ap.parse_args().apply)

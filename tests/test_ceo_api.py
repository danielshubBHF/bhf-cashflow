"""/api/ceo-summary: bearer-token JSON for the CEO dashboard, same figures as the Overview page."""
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from bhf import web

TOKEN = "test-ceo-token"
FIELDS = {"as_at", "netsuite_at", "fy", "live_projects", "items_need_attention", "projects_flagged", "contracts",
          "gross_margin", "gross_margin_pct", "net_cash_now", "final_position", "fy_budget", "fy_actual_closed",
          "still_to_win", "pipeline_weighted"}


def _env(**kw):
    for k in ("CEO_API_TOKEN", "DASHBOARD_PASSWORD", "DEMO"):
        os.environ.pop(k, None)
    os.environ.update(kw)


def _get(headers=None):
    return TestClient(web.app).get("/api/ceo-summary", headers=headers or {})


def test_no_header_is_401():
    _env(DEMO="1", CEO_API_TOKEN=TOKEN)
    try:
        r = _get()
        assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
    finally:
        _env()


def test_wrong_token_is_401():
    _env(DEMO="1", CEO_API_TOKEN=TOKEN)
    try:
        assert _get({"Authorization": "Bearer nope"}).status_code == 401
        assert _get({"Authorization": f"Basic {TOKEN}"}).status_code == 401        # must be a Bearer token
        assert _get({"Authorization": "Bearer "}).status_code == 401
    finally:
        _env()


def test_unset_token_always_401():
    for empty in ({}, {"CEO_API_TOKEN": ""}):
        _env(DEMO="1", **empty)
        try:
            assert _get({"Authorization": f"Bearer {TOKEN}"}).status_code == 401
            assert _get({"Authorization": "Bearer "}).status_code == 401
        finally:
            _env()


def test_right_token_matches_overview():
    _env(DEMO="1", CEO_API_TOKEN=TOKEN)
    try:
        r = _get({"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        j = r.json()
        assert set(j) == FIELDS
        req = type("R", (), {"session": {}})()                    # what the Overview route computes, in this test
        c = web.ctx(req, active="overview")
        pv = web.pipeline_view(c)
        t = c["pf"]["totals"]
        assert j["live_projects"] == len(c["pf"]["views"]) and j["live_projects"] > 0
        assert j["items_need_attention"] == t["flags"]
        assert j["net_cash_now"] == round(t["position"]["now"], 2)
        assert j["final_position"] == round(t["position"]["final"], 2)
        assert j["contracts"] == round(t["contract"], 2) and j["gross_margin"] == round(t["gm"], 2)
        assert j["fy"] == c["bv"]["fy"] and j["fy_budget"] == round(c["bv"]["budget_year"], 2)
        assert j["still_to_win"] == round(c["bv"]["gap"], 2) and j["pipeline_weighted"] == round(pv["this_w"], 2)
        assert j["projects_flagged"] == sum(1 for v in c["pf"]["views"] if v["flags"]) <= j["live_projects"]
    finally:
        _env()


def test_token_does_not_open_the_pages():
    _env(DASHBOARD_PASSWORD="pw", CEO_API_TOKEN=TOKEN)            # as on Render: login required
    try:
        r = TestClient(web.app).get("/", headers={"Authorization": f"Bearer {TOKEN}"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
    finally:
        _env()


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("PASS", name)

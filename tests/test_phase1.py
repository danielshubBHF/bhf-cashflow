"""Section 1: password-only sign-in, PDFs attached to Transactions rows, 'not yet filed' count."""
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.pop("ANTHROPIC_API_KEY", None)                      # terms reading stays offline
import logging; logging.getLogger("pypdf").setLevel(logging.ERROR)
from bhf import auth, sync
from bhf.model import project_view, unfiled
from tests.fixture_26001 import PROJECTS, FORECASTS, TXNS, SCHEDULE


# ---------------------------------------------------------------- sign-in
class Req:
    def __init__(self, user=None):
        self.session = {"user": user} if user else {}
        self.url = type("U", (), {"path": "/p/BHF26001"})()


def _env(**kw):
    for k in ("DASHBOARD_PASSWORD", "DEMO"):
        os.environ.pop(k, None)
    os.environ.update(kw)


def test_password_only_login():
    _env(DASHBOARD_PASSWORD="s3cret")
    assert auth.check("s3cret") and not auth.check("wrong") and not auth.check("")
    assert auth.require(Req()).headers["location"] == "/login?next=/p/BHF26001"
    assert auth.require(Req(user="BHF")) is None
    assert not hasattr(auth, "_msal") and not hasattr(auth, "callback")      # Entra path gone


def test_no_password_is_locked_except_demo():
    _env()
    assert auth.require(Req()) is not None and not auth.check("")         # Render without the var: locked
    _env(DEMO="1")
    assert auth.require(Req()) is None                                     # local preview stays open
    _env()


def test_login_redirect_stays_on_site():
    assert auth._safe_next("/p/BHF26001") == "/p/BHF26001"
    assert auth._safe_next("//evil.example") == "/" and auth._safe_next("https://evil.example") == "/"


# ---------------------------------------------------------------- sync: PDFs to Transactions rows
class FakeTable:
    sheets = {}

    def __init__(self, sheet_id):
        self.rows = FakeTable.sheets.setdefault(sheet_id, [])
        self.col = {}
        self.attached = FakeTable.sheets.setdefault(("att", sheet_id), [])
        self.fail_attach = FakeTable.sheets.get(("fail", sheet_id), False)

    def load(self):
        base = ["Doc #", "Project", "Type", "Direction", "Party", "PO / Order #", "Date", "Due Date", "Amount",
                "Currency Amount", "Status", "Paid Date", "Forecast", "PDF", "NetSuite ID", "Synced",
                "Item", "Milestone", "Seq", "Billed Doc", "Status", "NetSuite Job ID"]
        self.col = {c: i for i, c in enumerate(dict.fromkeys(base + [k for r in self.rows for k in r]))}
        return [dict(r) for r in self.rows]

    def ensure_column(self, title, type_="TEXT_NUMBER", width=80):
        self.col.setdefault(title, len(self.col))
        FakeTable.sheets.setdefault("ensured", []).append(title)

    def add(self, rows):
        out = []
        for r in rows:
            rid = 1000 + len(self.rows)
            self.rows.append({**r, "_id": rid})
            out.append({"id": rid})
        return out

    def update(self, changes):
        for rid, v in changes:
            next(r for r in self.rows if r["_id"] == rid).update(v)

    def attach(self, row_id, name, data):
        if self.fail_attach:
            raise RuntimeError("Smartsheet 500")
        self.attached.append((row_id, name, data))
        return 1


DOCS = [
    {"id": 1, "type": "PurchOrd", "tranid": "PO006400", "party": "Apex Mechanical", "aud": 100000, "trandate": "01/10/2026"},
    {"id": 2, "type": "VendBill", "tranid": "B-1", "party": "Apex Mechanical", "aud": 30000, "from_doc": "PO006400",
     "trandate": "03/10/2026"},
    {"id": 3, "type": "CustInvc", "tranid": "INV1", "party": "Stacked Farm", "aud": -5000, "otherrefnum": "PO-0007",
     "trandate": "03/10/2026"},
]


class FakeNS:
    pdf_ok = {1}                    # bill 2's RESTlet call fails the first time
    calls = []

    def project_docs(self, job_id):
        return [dict(d) for d in DOCS]

    def paid_dates(self, ids):
        return {}

    def part_paid(self, ids):
        return {}

    def bill_pdf(self, txn_id):
        got = self.pdf(txn_id)
        return got[:2] if got else None

    def pdf(self, txn_id, attached=False):
        FakeNS.calls.append(txn_id)
        if txn_id == 99:
            raise TimeoutError("RESTlet timeout")
        return (f"PO_{txn_id}.pdf", b"%PDF-1.4 fake") if txn_id in FakeNS.pdf_ok else None


def _setup():
    from bhf import config
    FakeTable.sheets = {}
    FakeTable.sheets[config.SHEETS["projects"]] = [{"Project": "BHF26001", "NetSuite Job ID": "13217", "Status": "Live", "_id": 1}]
    FakeTable.sheets[config.SHEETS["forecasts"]] = [dict(f, _id=i) for i, f in enumerate(FORECASTS)]
    FakeNS.pdf_ok, FakeNS.calls = {1}, []
    sync.NetSuite, sync.Table = FakeNS, FakeTable
    return config.SHEETS["transactions"]


def test_new_po_pdf_attached_and_unfiled():
    tid = _setup()
    sync.run()
    rows = {r["Doc #"]: r for r in FakeTable.sheets[tid]}
    att = FakeTable.sheets[("att", tid)]
    assert [(a[0], a[1]) for a in att] == [(rows["PO006400"]["_id"], "PO_1.pdf")]
    assert rows["PO006400"]["PDF"] == "PO_1.pdf" and rows["PO006400"]["Filed"] is False
    assert not rows["B-1"].get("PDF")                       # RESTlet gave nothing: left for retry
    assert 3 in FakeNS.calls                                # customer invoices are fetched too
    assert "Filed" in FakeTable.sheets["ensured"]   # column provisioned on first run


def test_missing_pdf_retried_next_sync_not_duplicated():
    tid = _setup()
    sync.run()
    FakeNS.pdf_ok, FakeNS.calls = {1, 2}, []
    sync.run()
    rows = {r["Doc #"]: r for r in FakeTable.sheets[tid]}
    assert len(FakeTable.sheets[tid]) == 3                  # no duplicate rows
    assert sorted(FakeNS.calls) == [2, 3]                   # only the bill + invoice that were missing, not PO 1
    assert rows["B-1"]["PDF"] == "PO_2.pdf" and rows["B-1"]["Filed"] is False
    assert len(FakeTable.sheets[("att", tid)]) == 2


def test_failed_attach_leaves_pdf_blank():
    tid = _setup()
    FakeTable.sheets[("fail", tid)] = True
    sync.run()
    assert all(not r.get("PDF") for r in FakeTable.sheets[tid])


def test_restlet_error_does_not_stop_sync():
    tid = _setup()
    DOCS.append({"id": 99, "type": "PurchOrd", "tranid": "PO006401", "party": "X", "aud": 10, "trandate": "01/10/2026"})
    try:
        sync.run()
    finally:
        DOCS.pop()
    assert len(FakeTable.sheets[tid]) == 4


def test_complete_project_syncs_without_pdfs():
    from bhf import config
    tid = _setup()
    FakeTable.sheets[config.SHEETS["projects"]][0]["Status"] = "Complete"
    sync.run()
    assert len(FakeTable.sheets[tid]) == 3 and FakeNS.calls == []       # actuals in, no PDF fetches
    assert not FakeTable.sheets.get(config.SHEETS["schedule"])           # and no payment schedules built


def test_dry_run_fetches_no_pdfs():
    _setup()
    sync.run(dry=True)
    assert FakeNS.calls == []


# ---------------------------------------------------------------- dashboard note
def test_unfiled_count():
    tx = [dict(t) for t in TXNS]
    tx[3].update(PDF="PO_PO005995.pdf", Filed=False)
    tx[4].update(PDF="PO_PO005725.pdf", Filed=True)
    tx[5].update(PDF="PO_PO006153.pdf")                     # Filed blank counts as not filed
    assert unfiled(tx) == 2
    assert project_view(PROJECTS[0], FORECASTS, tx, SCHEDULE, "2026-10-05")["unfiled"] == 2


def test_note_renders_on_project_and_overview():
    from bhf import web
    tx = [dict(t) for t in TXNS]
    tx[3].update(PDF="PO_PO005995.pdf", Filed=False, **{"NetSuite ID": "555"})
    from bhf.model import portfolio
    pf = portfolio(PROJECTS, FORECASTS, tx, SCHEDULE, "2026-10-05")
    base = {"request": None, "pf": pf, "tabs": [], "user": "BHF", "forecast_items": []}
    page = web.tpl.env.get_template("project.html").render(**base, active="BHF26001", v=pf["views"][0])
    assert "1 PDF not yet filed in SharePoint" in page and 'href="/pdf/555"' in page
    assert "1 PDF not yet filed" in web.tpl.env.get_template("overview.html").render(**base, active="overview")


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("PASS", name)

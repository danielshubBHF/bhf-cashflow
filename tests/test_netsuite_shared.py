"""Projects with no Unearned / WIP account of their own (BHF26006 Frucor): their flattening journals go through the
general accounts (2167 General - Unearned Income, id 1160; 1157 WIP - General, id 1159) and are matched by memo.
Fixture: journal 807, 20 Oct 2026, "BHF26006 - Flattening of COGS/Income": Cr 4071 29,401.54, Dr 5071 17,640.92."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import config, netsuite

MEMO = "BHF26006 - Flattening of COGS/Income"


class FakeNS(netsuite.NetSuite):
    """The real query-building code over canned SuiteQL replies; every query is kept for inspection."""
    def __init__(self, replies):
        self.replies, self.sent = replies, []

    def query(self, q):
        self.sent.append(q)
        for marker, rows in self.replies:
            if all(m in q for m in marker):
                return rows
        return []


def memo_queries(ns):
    return [q for q in ns.sent if "UPPER(t.memo) LIKE" in q]


def test_memo_owner_matches_the_code_at_the_start_only():
    keys = {"BHF26006", "BHF2600"}
    assert netsuite.memo_owner(MEMO, keys) == "BHF26006"
    assert netsuite.memo_owner("bhf26006 flattening", keys) == "BHF26006"
    assert netsuite.memo_owner("BHF260061 - something else", {"BHF26006"}) is None
    assert netsuite.memo_owner("Torresans 20% Completion FY25", keys) is None
    assert netsuite.memo_owner("Flattening BHF26006", keys) is None
    assert netsuite.code_key(" bhf26006'") == "BHF26006"


def test_recognised_counts_the_general_unearned_journal_for_a_project_with_no_account():
    ns = FakeNS([
        (("UPPER(t.memo) LIKE",), [{"memo": MEMO, "all_": "29401.54", "fy": "29401.54"},
                                    {"memo": "BHF260061 - not this one", "all_": "999", "fy": "999"}]),
        (("u.account AS key",), [{"key": "1149", "all_": "1180292", "fy": "484412"}]),
        (("tl.entity AS key",), [{"key": "13039", "all_": "-481.73", "fy": "-481.73"}]),
    ])
    rec = ns.recognised([("BHF26001", "13217", "1149"), ("BHF26006", "13739", None), ("BHF25012", "13039", None)], "2026-07-01")
    assert rec["BHF26006"] == {"all": 29401.54, "fy": 29401.54}
    assert rec["BHF26001"] == {"all": 1180292.0, "fy": 484412.0}
    assert rec["BHF25012"] == {"all": -481.73, "fy": -481.73}                  # its credit note; no journal claimed
    q = memo_queries(ns)[0]
    assert "x.account IN (1160)" in q and "LIKE '407%'" in q
    assert "LIKE 'BHF26006%'" in q and "LIKE 'BHF25012%'" in q and "BHF26001%" not in q   # only projects with no account
    assert "y.account IN (1149)" in q                                  # a journal on BHF26001's own account isn't counted twice


def test_no_memo_query_when_every_project_has_its_own_account():
    ns = FakeNS([])
    ns.recognised([("BHF26001", "13217", "1149")], "2026-07-01")
    assert memo_queries(ns) == []


def test_general_account_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "NS_GENERAL_UNEARNED", "4242")
    ns = FakeNS([])
    ns.recognised([("BHF26006", "13739", None)], "2026-07-01")
    assert "x.account IN (4242)" in memo_queries(ns)[0]


def test_pl_actuals_takes_revenue_and_cost_from_the_general_accounts():
    ns = FakeNS([
        (("UPPER(t.memo) LIKE", "LIKE '407%'"), [{"memo": MEMO, "p": "Oct 2026", "amt": "-29401.54"}]),
        (("UPPER(t.memo) LIKE", "LIKE '5%'"), [{"memo": MEMO, "p": "Oct 2026", "amt": "17640.92"}]),
    ])
    out = ns.pl_actuals([("BHF26001", "13217", "1149", "1148"), ("BHF26006", "13739", None, None)])
    assert out["BHF26006"] == {"rev": {"2026-10": 29401.54}, "cost": {"2026-10": 17640.92}}
    assert out["BHF26001"] == {"rev": {}, "cost": {}}
    rev, cost = memo_queries(ns)
    assert "x.account IN (1160)" in rev and "y.account IN (1149)" in rev
    assert "x.account IN (1159)" in cost and "y.account IN (1148)" in cost


def test_last_journal_date_for_a_project_on_the_general_accounts():
    ns = FakeNS([
        (("UPPER(t.memo) LIKE",), [{"memo": MEMO, "last": "20/10/2026"}]),
        (("tal.account AS acct",), [{"acct": "1149", "last": "12/10/2026"}]),
    ])
    out = ns.last_journals({"BHF26001": ("1149", "1148"), "BHF26006": (None, None)})
    assert out == {"BHF26001": "12/10/2026", "BHF26006": "20/10/2026"}
    q = memo_queries(ns)[0]
    assert "x.account IN (1160,1159)" in q and "BHF26001%" not in q

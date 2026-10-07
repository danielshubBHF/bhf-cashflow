"""Background refresh: pages never wait, and a refetch never undoes a save made while it ran."""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import config, smartsheet_db, store as store_mod


class FakeTable:
    calls = 0
    hook = None

    def __init__(self, sheet_id):
        self.sheet_id = sheet_id

    def load(self):
        FakeTable.calls += 1
        if FakeTable.hook:
            FakeTable.hook()
        return [{"_id": 1, "Sheet": self.sheet_id, "Version": FakeTable.calls}]


def make(monkeypatch):
    monkeypatch.setattr(smartsheet_db, "Table", FakeTable)
    monkeypatch.delenv("DEMO", raising=False)
    FakeTable.calls, FakeTable.hook = 0, None
    return store_mod.Store()


def test_stale_cache_answers_at_once_and_refreshes_behind(monkeypatch):
    s = make(monkeypatch)
    first = s.load()
    assert first and FakeTable.calls >= 4
    s.at -= config.CACHE_SECONDS + 1
    t = time.time()
    assert s.load() is first                       # the old copy, straight away
    assert time.time() - t < 0.05
    for _ in range(50):
        if s.data is not first:
            break
        time.sleep(0.02)
    assert s.data is not first                     # swapped in by the background thread


def test_a_save_during_a_refetch_makes_it_fetch_again(monkeypatch):
    s = make(monkeypatch)
    s.load()
    n = FakeTable.calls
    hits = []

    def save_once():
        if not hits:
            hits.append(1)
            s._writes += 1                         # a save lands mid-fetch
    FakeTable.hook = save_once
    s._refresh()
    per_fetch = n                                  # same number of sheets each time
    assert FakeTable.calls == n + 2 * per_fetch    # the overlapped fetch was thrown away and repeated

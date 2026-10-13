"""Every live-price cache row says when it was fetched and which session its bar belongs to.

Price-risk detection refuses an intraday quote that cannot prove both (review 2026-10-13): the
cache key's TTL bounds the blob, not the data written into it, and a quote cached in the
morning still matches the previous close in the afternoon.
"""
from datetime import datetime, timezone

import pandas as pd

from src.api.routes import _fetch_live_bulk


class _FakeStock:
    def __init__(self, symbol, currency="USD"):
        self.symbol, self.currency = symbol, currency


def _df(prices: dict) -> pd.DataFrame:
    idx = pd.DatetimeIndex(["2026-10-12", "2026-10-13"])
    return pd.concat({s: pd.DataFrame({"Close": [p, c], "Volume": [1, 2]}, index=idx)
                      for s, (p, c) in prices.items()}, axis=1)


def test_bulk_rows_carry_fetch_time_and_bar_session(monkeypatch):
    import src.api.routes as routes_mod
    monkeypatch.setattr(routes_mod.yf, "download",
                        lambda symbols, **kw: _df({"AAPL": (100.0, 101.0), "MSFT": (300.0, 305.0)}))
    before = datetime.now(timezone.utc)
    rows = _fetch_live_bulk([_FakeStock("AAPL"), _FakeStock("MSFT")])
    assert rows and all(r["session_date"] == "2026-10-13" for r in rows)
    for r in rows:
        fetched = datetime.fromisoformat(r["fetched_at"])
        assert fetched.tzinfo is not None
        # Stamped BEFORE the request: it may understate freshness, never overstate it.
        assert before <= fetched <= datetime.now(timezone.utc)


def test_fallback_rows_get_a_fetch_time_but_no_guessed_session(monkeypatch):
    import src.api.routes as routes_mod
    monkeypatch.setattr(routes_mod.yf, "download",
                        lambda symbols, **kw: _df({"AAPL": (100.0, 101.0)}))
    monkeypatch.setattr(routes_mod, "_fetch_live_one",
                        lambda sym, ccy: {"symbol": sym, "price": 42.0, "prev_close": 41.0})
    rows = {r["symbol"]: r for r in _fetch_live_bulk([_FakeStock("AAPL"), _FakeStock("GAP")])}
    assert "fetched_at" in rows["GAP"] and "session_date" not in rows["GAP"]

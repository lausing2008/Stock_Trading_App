from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pandas as pd

from src.services import valuation_outcome_prices as P
from test_valuation_universe import Cache

NOW = datetime(2026, 10, 30, 5, tzinfo=timezone.utc)


def test_only_adjusted_completed_closes_are_used_and_original_cache_time_is_retained(monkeypatch):
    frame = pd.DataFrame({"Adj Close": [90, 100, 999], "Close": [91, 101, 1000]},
                         index=pd.to_datetime(["2026-10-28", "2026-10-29", "2026-10-30"], utc=True))
    calls = []
    monkeypatch.setattr(P.yf, "Ticker", lambda symbol: SimpleNamespace(history=lambda **kwargs: calls.append(kwargs) or frame))
    cache = Cache()
    first = P.collect(cache, ["ADBE"], now=NOW)
    assert first["series"]["ADBE"]["bars"] == [{"date": "2026-10-28", "adj_close": 90}, {"date": "2026-10-29", "adj_close": 100}]
    second = P.collect(cache, ["ADBE"], now=NOW)
    assert second["series"] == first["series"] and len(calls) == 1
    assert calls[0]["auto_adjust"] is False


def test_raw_closes_do_not_replace_missing_adjustments(monkeypatch):
    frame = pd.DataFrame({"Close": [100]}, index=pd.to_datetime(["2026-10-29"], utc=True))
    monkeypatch.setattr(P.yf, "Ticker", lambda symbol: SimpleNamespace(history=lambda **kwargs: frame))
    value = P.collect(Cache(), ["MISSING"], now=NOW)
    assert value["series"]["MISSING"]["state"] == "UNAVAILABLE"
    assert value["series"]["MISSING"]["bars"] == []


def test_existing_fresh_fundamental_history_avoids_duplicate_yahoo_requests(monkeypatch):
    cache = Cache()
    cache.items[f"{P.INPUT_CACHE_PREFIX}:ADBE"] = json.dumps({
        "fetched_at": NOW.isoformat(), "bars": [{"date": "2026-10-29", "adj_close": 100}]})
    def forbidden(symbol): raise AssertionError("Already-cached adjusted history should be reused")
    monkeypatch.setattr(P.yf, "Ticker", forbidden)
    value = P.collect(cache, ["ADBE"], now=NOW)
    assert value["series"]["ADBE"]["state"] == "OK"

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from src.services import valuation_inputs as V


def test_completed_session_handles_weekends_holidays_and_early_closes():
    assert V.completed_session(datetime(2026, 10, 25, 17, tzinfo=timezone.utc)).isoformat() == "2026-10-23"
    # Thanksgiving Friday closes at 13:00 NY; use a 30-minute finalization buffer.
    assert V.completed_session(datetime(2026, 11, 27, 18, 20, tzinfo=timezone.utc)).isoformat() == "2026-11-25"
    assert V.completed_session(datetime(2026, 11, 27, 18, 35, tzinfo=timezone.utc)).isoformat() == "2026-11-27"
    with pytest.raises(ValueError):
        V.completed_session(datetime(2028, 1, 4, 20, tzinfo=timezone.utc))


def test_discovery_is_not_limited_to_tracked_stocks_and_excludes_foreign_exchanges():
    chosen, count = V.select_symbols([
        {"symbol": "NEW", "exchange": "NYQ", "quoteType": "EQUITY"},
        {"symbol": "SPY", "exchange": "NYQ", "quoteType": "ETF"},
        {"symbol": "FOREIGN", "exchange": "LSE", "quoteType": "EQUITY"},
        {"symbol": "NEW", "exchange": "NYQ", "quoteType": "EQUITY"},
    ], {"OLD"})
    assert chosen == ["NEW", "OLD"]
    assert count == 2


def test_failed_provider_refresh_keeps_dated_old_input(monkeypatch):
    import json
    cache = MagicMock()
    old = {"symbol": "OLD", "bars": [{"date": "2026-10-23"}], "info": {"marketCap": 1e10},
           "fetched_at": "2026-10-23T20:35:00+00:00", "errors": []}
    cache.get.side_effect = lambda key: json.dumps(old) if key.endswith(":OLD") else None
    monkeypatch.setattr(V.yf, "screen", lambda *args, **kwargs: {"quotes": []})
    def fail(*args):
        raise ValueError("provider down")
    monkeypatch.setattr(V, "_fetch", fail)
    result = V.collect_inputs(cache, {"OLD"}, now=datetime(2026, 10, 28, 14, tzinfo=timezone.utc))
    assert result["rows"][0]["fetched_at"] == old["fetched_at"]
    assert "refresh_failed_using_old_inputs" in result["rows"][0]["errors"]
    assert result["price_session"] == "2026-10-27"
    cache.setex.assert_not_called()


def test_uw_live_shapes_normalize_positive_capex_and_keep_unknown_metrics_unknown():
    import json
    from pathlib import Path
    from src.services.valuation_uw import normalize
    fixture = json.loads((Path(__file__).parent / "fixtures/valuation_uw_otis.json").read_text())
    now = datetime(2026, 10, 29, 7, tzinfo=timezone.utc)
    row = normalize("OTIS", fixture["profile"], fixture["financials"], [
        {"date": "2026-10-28", "market_time": "r", "close": "73", "volume": "1000000"},
        {"date": "2026-10-29", "market_time": "r", "close": "74", "volume": "1000000"},
        {"date": "2026-10-28", "market_time": "po", "close": "75", "volume": "100"},
    ], fixture["opinions"], now.date().replace(day=28), now)
    assert row["provider"] == "Unusual Whales"
    assert row["quote_type"] == "EQUITY"
    assert row["annuals"][0]["capital_expenditure"] == -152000000
    assert row["annuals"][0]["change_working_capital"] is None
    assert len(row["bars"]) == 1 and row["bars"][0]["adj_close"] is None
    assert row["info"]["trailingEps"] is not None
    assert "forwardEps" not in row["info"] and "pegRatio" not in row["info"]
    assert row["analyst_actions"][0]["firm"] == "Wells Fargo"


def test_yahoo_failure_uses_uw_without_losing_a_new_discovery(monkeypatch):
    from src.services import valuation_uw as UW
    cache = MagicMock(); cache.get.return_value = None
    monkeypatch.setattr(V.yf, "screen", lambda *a, **k: {"quotes": [{"symbol": "NEW", "exchange": "NYQ"}]})
    def fail(*a): raise ValueError("Yahoo invalid crumb")
    monkeypatch.setattr(V, "_fetch", fail)
    monkeypatch.setattr(UW, "fetch", lambda symbol, day, now: {"symbol": symbol, "provider": "Unusual Whales", "errors": [], "bars": []})
    result = V.collect_inputs(cache, set(), now=datetime(2026, 10, 28, 14, tzinfo=timezone.utc))
    assert result["rows"][0]["provider"] == "Unusual Whales"
    assert result["rows"][0]["tracked"] is False

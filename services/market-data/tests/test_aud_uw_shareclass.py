"""AUD-UW-SHARECLASS (2026-11-11): Unusual Whales spells share classes with a dot.

/api/darkpool/BRK-A returned HTTP 422 about once a minute; BRK.A answers (checked live).
"""
from unittest.mock import patch

from src.services import unusual_whales as UW


def test_share_classes_use_a_dot_and_everything_else_is_unchanged():
    assert UW.uw_ticker("BRK-A") == "BRK.A"
    assert UW.uw_ticker("BRK-B") == "BRK.B"
    assert UW.uw_ticker("BF-B") == "BF.B"
    for same in ("AAPL", "0700.HK", "BRK.A", "SPY", "ABC-WS", "", "A-"):
        assert UW.uw_ticker(same) == same


class _Redis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.store[k] = v


def test_the_dark_pool_request_uses_the_provider_spelling_and_keeps_the_app_symbol():
    seen = {}

    def fake_get(path, params=None, endpoint=None):
        seen["path"] = path
        return [{"price": "500000", "size": 10, "market_center": "L", "executed_at": "2026-11-10T15:00:00Z",
                 "tracking_id": 1, "canceled": False}]
    with patch.object(UW, "is_available", lambda: True), patch.object(UW, "_get_redis", lambda: _Redis()), \
         patch.object(UW, "_get", fake_get):
        fetch = UW.fetch_dark_pool_prints("BRK-A")
    assert seen["path"] == "/api/darkpool/BRK.A"
    assert fetch.status == "ok" and fetch.rows and fetch.rows[0].symbol == "BRK-A"


def test_the_flow_alert_request_uses_the_provider_spelling():
    seen = {}

    def fake_get(path, params=None, endpoint=None):
        seen["ticker"] = (params or {}).get("ticker_symbol")
        return []
    with patch.object(UW, "is_available", lambda: True), patch.object(UW, "_get_redis", lambda: _Redis()), \
         patch.object(UW, "_get", fake_get):
        UW.get_flow_alerts("BRK-A")
    assert seen["ticker"] == "BRK.A"

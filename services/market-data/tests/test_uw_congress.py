"""Tests for T323-DARKPOOL's shared/common/uw_congress.py — Unusual Whales' real congressional-
trade feed, factored into shared/common/ (not services/market-data/src/services/unusual_whales.py,
where the rest of the UW client lives) because its one real consumer,
services/event-intelligence/src/services/congress.py, runs in a separate container that never
mounts market-data's own src/ tree.

conftest.py blanket-stubs common.uw_congress as a MagicMock (matching common.ai_keys' own
stubbing) so unusual_whales.py's `from common.uw_congress import ...` re-export line imports
cleanly without needing this module's own real dependencies. That means the REAL implementation
needs to be loaded directly here — matching test_risk_snapshots.py's/test_correlation_
preentry.py's established "load the real file via importlib, bypassing the blanket stub"
technique, just without their heavier DB-engine setup (this module's only real dependencies are
common.ai_keys/common.redis_client, both already plain MagicMock stubs at the point this loads,
which is exactly what a unit test wants — mock the key/redis lookups, exercise the real parsing).
"""
import sys
import importlib.util
import pathlib
from unittest.mock import patch

# conftest.py (auto-loaded by pytest for this whole tests/ dir) has already stubbed
# common.uw_congress as a blanket MagicMock by the time this file's imports run — remove that
# stub and load the REAL file in its place before this test file does anything else.
if "common.uw_congress" in sys.modules:
    del sys.modules["common.uw_congress"]

_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "uw_congress.py"
_spec = importlib.util.spec_from_file_location("common.uw_congress", _path)
uwc = importlib.util.module_from_spec(_spec)
sys.modules["common.uw_congress"] = uwc
_spec.loader.exec_module(uwc)


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


# ── is_available() ───────────────────────────────────────────────────────────────────────

def test_is_available_false_when_key_missing():
    with patch.object(uwc, "is_unusual_whales_enabled", return_value=True), \
         patch.object(uwc, "get_unusual_whales_key", return_value=""):
        assert uwc.is_available() is False


def test_is_available_true_only_when_both_conditions_hold():
    with patch.object(uwc, "is_unusual_whales_enabled", return_value=True), \
         patch.object(uwc, "get_unusual_whales_key", return_value="real-token"):
        assert uwc.is_available() is True


# ── _normalize_congress_txn_type() — must match event-intelligence's own vocabulary ────────

def test_normalize_txn_type_purchase_variants():
    assert uwc._normalize_congress_txn_type("Purchase") == "purchase"
    assert uwc._normalize_congress_txn_type("buy") == "purchase"


def test_normalize_txn_type_sale_variants():
    assert uwc._normalize_congress_txn_type("Sale (Full)") == "sale"
    assert uwc._normalize_congress_txn_type("sell") == "sale"


def test_normalize_txn_type_exchange():
    assert uwc._normalize_congress_txn_type("Exchange") == "exchange"


def test_normalize_txn_type_unknown_for_none_or_unrecognized():
    assert uwc._normalize_congress_txn_type(None) == "unknown"
    assert uwc._normalize_congress_txn_type("") == "unknown"
    assert uwc._normalize_congress_txn_type("some other thing") == "some other thing"


# ── get_congress_trades() ────────────────────────────────────────────────────────────────

def test_congress_trades_returns_empty_list_when_not_available():
    with patch.object(uwc, "is_available", return_value=False):
        assert uwc.get_congress_trades(since="2026-09-01") == []


def test_congress_trades_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    import json
    from dataclasses import asdict
    cached_row = uwc.CongressTradeRow(
        politician_name="Jane Smith", party="D", chamber="House", ticker="AAPL",
        transaction_type="purchase", amount_min=15001.0, amount_max=50000.0,
        trade_date="2026-08-28", disclosure_date="2026-09-01",
    )
    fake_redis.store["stockai:uw:congress:2026-09-01"] = json.dumps([asdict(cached_row)])
    with patch.object(uwc, "is_available", return_value=True), \
         patch.object(uwc, "get_redis", return_value=fake_redis):
        with patch("httpx.Client") as mock_client:
            result = uwc.get_congress_trades(since="2026-09-01")
    assert result == [cached_row]
    mock_client.assert_not_called()


def test_congress_trades_parses_a_real_response():
    fake_redis = _FakeRedis()

    class _FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [{
                "politician_name": "Jane Smith", "party": "D", "chamber": "House",
                "ticker": "aapl", "transaction_type": "Purchase",
                "amount_min": "15001", "amount_max": "50000",
                "transaction_date": "2026-08-28", "filing_date": "2026-09-01",
            }]}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch.object(uwc, "is_available", return_value=True), \
         patch.object(uwc, "get_redis", return_value=fake_redis), \
         patch.object(uwc, "get_unusual_whales_key", return_value="real-token"), \
         patch("httpx.Client", return_value=_FakeClient()):
        result = uwc.get_congress_trades(since="2026-09-01")
    assert len(result) == 1
    r = result[0]
    assert r.politician_name == "Jane Smith"
    assert r.ticker == "AAPL"  # uppercased
    assert r.transaction_type == "purchase"  # normalized
    assert r.amount_min == 15001.0
    assert r.amount_max == 50000.0
    assert r.trade_date == "2026-08-28"
    assert r.disclosure_date == "2026-09-01"


def test_congress_trades_falls_back_to_reporter_or_name_field_for_politician_name():
    """The skill.md itself does not document this endpoint's exact response shape (confirmed
    directly — see uw_congress.py's own module docstring) — probing several plausible key names
    is a deliberate defensive choice, not an assumption of one true shape."""
    class _FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [{"ticker": "MSFT", "reporter": "John Doe", "transaction_type": "sale"}]}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch.object(uwc, "is_available", return_value=True), \
         patch.object(uwc, "get_redis", return_value=_FakeRedis()), \
         patch.object(uwc, "get_unusual_whales_key", return_value="real-token"), \
         patch("httpx.Client", return_value=_FakeClient()):
        result = uwc.get_congress_trades(since="2026-09-01")
    assert result[0].politician_name == "John Doe"


def test_congress_trades_skips_rows_with_no_ticker():
    class _FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [{"ticker": "", "politician_name": "Nobody"}]}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch.object(uwc, "is_available", return_value=True), \
         patch.object(uwc, "get_redis", return_value=_FakeRedis()), \
         patch.object(uwc, "get_unusual_whales_key", return_value="real-token"), \
         patch("httpx.Client", return_value=_FakeClient()):
        result = uwc.get_congress_trades(since="2026-09-01")
    assert result == []


def test_congress_trades_returns_empty_list_on_auth_error():
    class _FakeResp:
        status_code = 401
    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch.object(uwc, "is_available", return_value=True), \
         patch.object(uwc, "get_redis", return_value=_FakeRedis()), \
         patch.object(uwc, "get_unusual_whales_key", return_value="real-token"), \
         patch("httpx.Client", return_value=_FakeClient()):
        assert uwc.get_congress_trades(since="2026-09-01") == []


def test_congress_trades_returns_empty_list_on_a_fetch_exception():
    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            raise RuntimeError("boom")

    with patch.object(uwc, "is_available", return_value=True), \
         patch.object(uwc, "get_redis", return_value=_FakeRedis()), \
         patch.object(uwc, "get_unusual_whales_key", return_value="real-token"), \
         patch("httpx.Client", return_value=_FakeClient()):
        assert uwc.get_congress_trades(since="2026-09-01") == []


def test_congress_trades_one_malformed_row_does_not_drop_the_rest():
    class _FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [
                {"ticker": None},  # will raise inside the try block below (no valid ticker)
                {"ticker": "AAPL", "politician_name": "Jane Smith", "transaction_type": "purchase"},
            ]}
    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get(self, *a, **kw):
            return _FakeResp()

    with patch.object(uwc, "is_available", return_value=True), \
         patch.object(uwc, "get_redis", return_value=_FakeRedis()), \
         patch.object(uwc, "get_unusual_whales_key", return_value="real-token"), \
         patch("httpx.Client", return_value=_FakeClient()):
        result = uwc.get_congress_trades(since="2026-09-01")
    assert len(result) == 1
    assert result[0].ticker == "AAPL"

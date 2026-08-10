"""Tests for T230-DATA-STREAMING-QUOTES's alpaca_quote_stream.py.

_quote_channel / _publish_quote / _parse_quote_message have zero DB/sqlalchemy dependency at
call time, so they're imported and tested directly against the module under conftest.py's
normal stubbing (websockets is additionally stubbed here since it isn't installed in this local
dev environment — a real, pinned requirements.txt dependency absent locally, same class of gap
already documented for jose/redis/requests_oauthlib elsewhere in this repo's history).

_active_us_symbols() does `from sqlalchemy import select` / `from db import SessionLocal, Stock`
at module level — conftest.py stubs both as MagicMock, so this specific function's real source
is instead extracted via exec() and run against a real in-memory SQLite session + the real
shared/db/models.py, matching test_correlation_preentry.py's/test_broker_position_sync.py's
established technique for exactly this constraint. The module itself is never imported while
sqlalchemy is stubbed, so the two testing strategies (direct import + source extraction) don't
conflict with each other.
"""
import json
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("websockets", MagicMock())

from src.services import alpaca_quote_stream as m  # noqa: E402 — direct import for the pure fns


class TestQuoteChannel:
    def test_builds_the_expected_redis_channel_name(self):
        assert m._quote_channel("AAPL") == "stockai:quotes:AAPL"

    def test_channel_is_symbol_specific(self):
        assert m._quote_channel("AAPL") != m._quote_channel("MSFT")


class TestPublishQuote:
    def test_publishes_the_expected_json_payload(self):
        redis_client = MagicMock()
        m._publish_quote(redis_client, "AAPL", 231.45, "2026-08-01T12:00:00Z")
        redis_client.publish.assert_called_once()
        channel, payload = redis_client.publish.call_args[0]
        assert channel == "stockai:quotes:AAPL"
        assert json.loads(payload) == {"symbol": "AAPL", "price": 231.45, "ts": "2026-08-01T12:00:00Z"}

    def test_fails_open_when_redis_publish_raises(self):
        """A Redis outage must never propagate up into the connection-handling loop — this is
        best-effort fan-out, not a required write."""
        redis_client = MagicMock()
        redis_client.publish.side_effect = ConnectionError("redis down")
        m._publish_quote(redis_client, "AAPL", 231.45, "2026-08-01T12:00:00Z")  # must not raise


class TestParseQuoteMessage:
    def test_parses_a_trade_message(self):
        msg = {"T": "t", "S": "AAPL", "p": 231.45, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) == ("AAPL", 231.45, "2026-08-01T12:00:00Z")

    def test_parses_a_quote_message_as_the_bid_ask_midpoint(self):
        msg = {"T": "q", "S": "AAPL", "bp": 231.00, "ap": 231.50, "t": "2026-08-01T12:00:00Z"}
        result = m._parse_quote_message(msg)
        assert result == ("AAPL", 231.25, "2026-08-01T12:00:00Z")

    def test_returns_none_for_a_quote_message_with_a_zero_bid(self):
        """A zero/negative bid or ask is a malformed/incomplete quote, not a real midpoint —
        must not silently publish a bogus price."""
        msg = {"T": "q", "S": "AAPL", "bp": 0, "ap": 231.50, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_a_negative_ask(self):
        msg = {"T": "q", "S": "AAPL", "bp": 231.00, "ap": -1, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_a_quote_message_missing_ask(self):
        msg = {"T": "q", "S": "AAPL", "bp": 231.00, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_a_trade_message_missing_price(self):
        msg = {"T": "t", "S": "AAPL", "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_an_unrecognized_message_type(self):
        """Alpaca's stream also emits status/subscription-ack/error messages (T="success",
        T="error", etc.) interleaved with real ticks — these must be silently ignored, not
        mistaken for a price update."""
        msg = {"T": "success", "msg": "connected"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_when_symbol_is_missing(self):
        msg = {"T": "t", "p": 231.45, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_when_timestamp_is_missing(self):
        msg = {"T": "t", "S": "AAPL", "p": 231.45}
        assert m._parse_quote_message(msg) is None


class TestMaxSymbolsCap:
    def test_max_symbols_per_connection_matches_alpacas_documented_cap(self):
        # Regression guard: this constant is what caps the subscribe list sent to Alpaca —
        # a silent change here would either waste headroom or exceed Alpaca's real limit.
        assert m._MAX_SYMBOLS_PER_CONNECTION == 500


# ── _active_us_symbols() — extracted from real source, run against a real DB ──────────────

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util  # noqa: E402
import pathlib  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_quotes", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_quotes"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_ENGINE, tables=[_models.Stock.__table__])

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange

_SOURCE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "alpaca_quote_stream.py"
_SOURCE = _SOURCE_PATH.read_text()


def _extract_active_us_symbols(session_local):
    start = _SOURCE.index("def _active_us_symbols(")
    end = _SOURCE.index("\n\n\ndef _quote_channel(", start)
    func_source = _SOURCE[start:end]
    namespace = {"select": select, "Stock": Stock, "SessionLocal": session_local}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_active_us_symbols"]


class _SessionCtx:
    """Wraps an already-open test session so `with SessionLocal() as session:` (the real
    calling convention inside _active_us_symbols) works without actually closing the shared
    session on exit — the test fixture owns closing it, not the function under test."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def db_session():
    session = Session(_ENGINE)
    try:
        yield session
    finally:
        session.rollback()
        session.execute(Stock.__table__.delete())
        session.commit()
        session.close()


def _mk_stock(session, symbol, *, market=Market.US, active=True, delisted=False, id_=None):
    row = Stock(
        id=id_,
        symbol=symbol,
        name=symbol,
        market=market,
        exchange=Exchange.NASDAQ if market == Market.US else Exchange.HKEX,
        active=active,
        delisted=delisted,
    )
    session.add(row)
    session.commit()
    return row


class TestActiveUsSymbols:
    def test_returns_active_non_delisted_us_symbols(self, db_session):
        fn = _extract_active_us_symbols(lambda: _SessionCtx(db_session))
        _mk_stock(db_session, "AAPL", id_=1)
        _mk_stock(db_session, "MSFT", id_=2)
        assert set(fn()) == {"AAPL", "MSFT"}

    def test_excludes_inactive_stocks(self, db_session):
        fn = _extract_active_us_symbols(lambda: _SessionCtx(db_session))
        _mk_stock(db_session, "AAPL", id_=1, active=True)
        _mk_stock(db_session, "DEAD", id_=2, active=False)
        assert fn() == ["AAPL"]

    def test_excludes_delisted_stocks_even_though_still_marked_active(self, db_session):
        """BUG-DELISTED-GENERATION-BLIND: Stock.active.is_(True) alone does NOT exclude a
        confirmed delisting (a delisted stock stays active=True forever) — the query must also
        filter Stock.delisted.is_(False). This is the exact regression an earlier, incorrect
        docstring claim ("active already excludes delisted") would have silently reintroduced
        if the filter itself had been left out."""
        fn = _extract_active_us_symbols(lambda: _SessionCtx(db_session))
        _mk_stock(db_session, "AAPL", id_=1, active=True, delisted=False)
        _mk_stock(db_session, "ZOMBIE", id_=2, active=True, delisted=True)
        assert fn() == ["AAPL"]

    def test_excludes_hk_stocks(self, db_session):
        fn = _extract_active_us_symbols(lambda: _SessionCtx(db_session))
        _mk_stock(db_session, "AAPL", id_=1, market=Market.US)
        _mk_stock(db_session, "0700.HK", id_=2, market=Market.HK)
        assert fn() == ["AAPL"]

    def test_returns_empty_list_when_no_stocks_qualify(self, db_session):
        fn = _extract_active_us_symbols(lambda: _SessionCtx(db_session))
        assert fn() == []

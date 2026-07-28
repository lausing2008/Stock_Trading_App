"""Tests for AUD-SURVIVORSHIP-DELISTDETECT's ingestion.py half — _record_delisting_signal()/
_clear_delisting_signal() and their wiring into ingest_symbol()'s adapter loop.

Closes a real, confirmed dead-column bug: ml-prediction's training-universe query already does
`WHERE active OR delisted` (services/ml-prediction/src/api/routes.py), but nothing anywhere
ever set Stock.delisted=True — confirmed live, it was always False for every row in production.
YFTickerMissingError (raised by yfinance when Yahoo's own API reports "no data found, symbol
may be delisted" — a structurally distinct exception from YFRateLimitError, which is NOT a
subclass) is the real signal; 2 consecutive confirmations (not 1) guard against a single
transient glitch, since a stock's daily ingestion could in principle hit a genuine one-off
issue even with YFTickerMissingError excluded from the adapter's own retry policy.

sqlalchemy/db are stubbed wholesale by conftest.py — matches test_gate_harness_extended.py's/
test_broker_position_sync.py's established stub-pop-and-restore technique to load the real
Stock model against an in-memory SQLite engine, with _record_delisting_signal()/
_clear_delisting_signal() extracted from the real ingestion.py source via exec() so these
tests exercise the actual logic, not a hand-copied reimplementation.
"""
import sys
from unittest.mock import MagicMock

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util  # noqa: E402
import pathlib  # noqa: E402

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_delist", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_delist"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_ENGINE, tables=[_models.Stock.__table__])
_SessionLocal = sessionmaker(bind=_ENGINE)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange

_ingestion_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "ingestion.py"
_source = _ingestion_path.read_text()


def _extract(func_name, end_marker):
    start = _source.index(f"def {func_name}(")
    end = _source.index(end_marker, start) + len(end_marker)
    return _source[start:end]


_record_src = _extract("_record_delisting_signal", '        log.warning("ingest.delisting_signal_failed", symbol=symbol, error=str(exc))')
_clear_src = _extract("_clear_delisting_signal", '        log.warning("ingest.delisting_signal_clear_failed", symbol=symbol, error=str(exc))')


def _make_namespace(fake_redis, fake_session_local):
    log = MagicMock()
    ns = {
        "get_redis": lambda: fake_redis,
        "SessionLocal": fake_session_local,
        "select": select,
        "Stock": Stock,
        "log": log,
        "_DELISTING_CONFIRM_THRESHOLD": 2,
        "_DELISTING_REDIS_KEY": "stockai:delisting_signal:{symbol}",
        "_DELISTING_REDIS_TTL": 30 * 86400,
    }
    exec(_record_src, ns)
    exec(_clear_src, ns)
    return ns


def _seed_stock(symbol, delisted=False):
    with _SessionLocal() as s:
        existing = s.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
        if existing:
            s.delete(existing)
            s.commit()
        s.add(Stock(symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ, name=symbol, delisted=delisted))
        s.commit()


def _get_stock(symbol):
    with _SessionLocal() as s:
        return s.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}
        self.expired_keys: list[str] = []
        self.deleted_keys: list[str] = []

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, ttl):
        self.expired_keys.append(key)

    def delete(self, key):
        self.deleted_keys.append(key)
        self.store.pop(key, None)


class TestRecordDelistingSignal:
    def test_first_occurrence_does_not_set_delisted(self):
        _seed_stock("FAKE1")
        redis = _FakeRedis()
        ns = _make_namespace(redis, _SessionLocal)
        ns["_record_delisting_signal"]("FAKE1")
        assert _get_stock("FAKE1").delisted is False

    def test_second_consecutive_occurrence_sets_delisted(self):
        _seed_stock("FAKE2")
        redis = _FakeRedis()
        ns = _make_namespace(redis, _SessionLocal)
        ns["_record_delisting_signal"]("FAKE2")
        ns["_record_delisting_signal"]("FAKE2")
        assert _get_stock("FAKE2").delisted is True

    def test_confirmation_clears_the_redis_counter(self):
        """Once confirmed, the counter should be cleared — an already-delisted stock doesn't
        need to keep incrementing forever."""
        _seed_stock("FAKE3")
        redis = _FakeRedis()
        ns = _make_namespace(redis, _SessionLocal)
        ns["_record_delisting_signal"]("FAKE3")
        ns["_record_delisting_signal"]("FAKE3")
        assert "stockai:delisting_signal:FAKE3" in redis.deleted_keys

    def test_unknown_symbol_does_not_raise(self):
        redis = _FakeRedis()
        ns = _make_namespace(redis, _SessionLocal)
        ns["_record_delisting_signal"]("NONEXISTENT_XYZ")
        ns["_record_delisting_signal"]("NONEXISTENT_XYZ")  # must not raise even at threshold

    def test_redis_failure_does_not_raise(self):
        _seed_stock("FAKE4")

        class _BrokenRedis:
            def incr(self, key):
                raise ConnectionError("redis down")

        ns = _make_namespace(_BrokenRedis(), _SessionLocal)
        ns["_record_delisting_signal"]("FAKE4")  # must not raise
        assert _get_stock("FAKE4").delisted is False


class TestClearDelistingSignal:
    def test_clears_the_redis_key(self):
        redis = _FakeRedis()
        redis.store["stockai:delisting_signal:FAKE5"] = 1
        ns = _make_namespace(redis, _SessionLocal)
        ns["_clear_delisting_signal"]("FAKE5")
        assert "stockai:delisting_signal:FAKE5" in redis.deleted_keys

    def test_redis_failure_does_not_raise(self):
        class _BrokenRedis:
            def delete(self, key):
                raise ConnectionError("redis down")

        ns = _make_namespace(_BrokenRedis(), _SessionLocal)
        ns["_clear_delisting_signal"]("FAKE6")  # must not raise

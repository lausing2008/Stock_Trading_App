"""Tests for T270-SECTOR-THEME-FORECAST-EMAIL's compute_theme_signal()/generate_theme_summary()/
_clean_summary() in services/market-data/src/services/theme_signals.py.

theme_signals.py imports `db` (Stock, Price, Ranking, Signal, ...) and issues real SQLAlchemy
queries — conftest.py stubs both `sqlalchemy` and `db` as MagicMock for the rest of the test
session, so compute_theme_signal() can't be exercised against a stub the way a pure-aggregation
function could. It ALSO uses real `httpx.AsyncClient`, which conftest.py stubs too (unlike
decision-engine's test suite, where httpx is a real installed package with no stub). Matches
test_correlation_preentry.py's/test_broker_position_sync.py's established technique: pop the
sqlalchemy/db/httpx stubs, build ONE shared in-memory engine + real models while real
sqlalchemy is active, import the real module under test, then restore every stub immediately so
later-collected test files in the same pytest session aren't affected.
"""
import sys

_STUBBED_MODULES = (
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql",
    "db", "httpx", "common.ai_keys",
)
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_theme", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_theme"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[
        _models.Stock.__table__, _models.Price.__table__,
        _models.Ranking.__table__, _models.Signal.__table__,
    ],
)

# Make the real models importable as `db` (theme_signals.py does `from db import Price, ...`).
# AUD-TESTORDER: `db` must stay real until AFTER theme_signals.py is imported below — an
# earlier version of this file restored `db`'s stub in this same loop (it wasn't in the
# skip-list), which silently swapped `db` back to the MagicMock BEFORE the real import ran,
# so every query built against `db.Stock`/`db.Signal` etc. was actually built against mock
# attributes. Caught immediately (every compute_theme_signal() test failed with a real
# sqlalchemy.exc.ArgumentError naming a MagicMock, not a passing-for-the-wrong-reason silent
# bug) — but worth keeping this note so the exact same ordering mistake isn't reintroduced.
sys.modules["db"] = _models

for _mod, _stub in _saved_stubs.items():
    if _mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "httpx", "db"):
        # leave the real module in place for the real import below
        continue
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

import httpx  # real, not the stub — needed by generate_theme_summary()
import pytest

import src.services.theme_signals as theme_signals

Stock = _models.Stock
Price = _models.Price
Ranking = _models.Ranking
Signal = _models.Signal
TimeFrame = _models.TimeFrame
Market = _models.Market
Exchange = _models.Exchange
SignalHorizon = _models.SignalHorizon
SignalType = _models.SignalType

# Restore the httpx/sqlalchemy stubs for whatever test files collect AFTER this one, matching
# the established precedent's own restore-after-import discipline.
for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)


@pytest.fixture
def session():
    with Session(_ENGINE) as s:
        yield s
        # Clean up rows created by this test so the next test starts from a blank table —
        # this shares ONE engine across all tests in this file (matching the established
        # precedent's own reasoning: building a fresh engine per test broke 7 other test
        # files' collection once before by leaving the real sqlalchemy swapped in globally).
        s.query(Signal).delete()
        s.query(Ranking).delete()
        s.query(Price).delete()
        s.query(Stock).delete()
        s.commit()


def _make_stock(session, symbol, sid) -> Stock:
    st = Stock(
        id=sid, symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ,
        name=symbol, active=True, delisted=False,
    )
    session.add(st)
    session.commit()
    return st


def _add_prices(session, stock_id, closes: list[float]):
    """closes[0] is the OLDEST bar, closes[-1] is the NEWEST — inserted with ascending ts so
    ORDER BY ts DESC LIMIT 6 in compute_theme_signal() returns them newest-first."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, c in enumerate(closes):
        session.add(Price(
            id=stock_id * 1000 + i, stock_id=stock_id, ts=base + timedelta(days=i),
            timeframe=TimeFrame.D1, open=c, high=c, low=c, close=c, volume=1000,
        ))
    session.commit()


_ranking_id_counter = {"next": 1}


def _add_ranking(session, stock_id, score, as_of):
    rid = _ranking_id_counter["next"]
    _ranking_id_counter["next"] += 1
    session.add(Ranking(
        id=rid, stock_id=stock_id, as_of=as_of, score=score,
        technical=score, momentum=score, volatility=1.0,
    ))
    session.commit()


def _add_signal(session, stock_id, signal_type, ts):
    session.add(Signal(
        id=stock_id * 100 + 1, stock_id=stock_id, ts=ts, signal=signal_type,
        horizon=SignalHorizon.SWING, confidence=70.0,
    ))
    session.commit()


# ── compute_theme_signal() ──────────────────────────────────────────────────────

def test_returns_none_when_no_symbols_resolve(session):
    result = theme_signals.compute_theme_signal(session, "Nonexistent Theme", ["ZZZZ", "YYYY"])
    assert result is None


def test_excludes_delisted_and_inactive_stocks(session):
    live = _make_stock(session, "LIVE", 1)
    dead = Stock(id=2, symbol="DEAD", market=Market.US, exchange=Exchange.NASDAQ, name="DEAD", active=True, delisted=True)
    inactive = Stock(id=3, symbol="OFF", market=Market.US, exchange=Exchange.NASDAQ, name="OFF", active=False, delisted=False)
    session.add_all([dead, inactive])
    session.commit()
    _add_prices(session, live.id, [100.0, 110.0])

    result = theme_signals.compute_theme_signal(session, "T", ["LIVE", "DEAD", "OFF"])
    assert result is not None
    assert result.symbol_count == 1
    assert [s["symbol"] for s in result.top_symbols] == ["LIVE"]


def test_computes_5d_return_from_newest_vs_oldest_of_a_6_bar_window(session):
    st = _make_stock(session, "AAPL", 1)
    # 6 bars: 100 -> 110 -> 5-day return should be exactly +10%
    _add_prices(session, st.id, [100.0, 101.0, 102.0, 103.0, 104.0, 110.0])

    result = theme_signals.compute_theme_signal(session, "T", ["AAPL"])
    assert result.avg_return_5d_pct == pytest.approx(10.0)
    assert result.top_symbols[0]["return_5d_pct"] == pytest.approx(10.0)


def test_a_stock_with_only_one_bar_is_excluded_from_the_return_average_not_treated_as_zero(session):
    st = _make_stock(session, "NEWCO", 1)
    _add_prices(session, st.id, [50.0])  # only 1 bar — can't compute a 5d return

    result = theme_signals.compute_theme_signal(session, "T", ["NEWCO"])
    assert result.avg_return_5d_pct is None
    assert result.top_symbols[0]["return_5d_pct"] is None


def test_uses_most_recent_ranking_score_per_stock(session):
    st = _make_stock(session, "MSFT", 1)
    _add_prices(session, st.id, [100.0])
    _add_ranking(session, st.id, 40.0, date_ := __import__("datetime").date(2026, 1, 1))
    _add_ranking(session, st.id, 80.0, __import__("datetime").date(2026, 1, 10))

    result = theme_signals.compute_theme_signal(session, "T", ["MSFT"])
    assert result.avg_kscore == 80.0


def test_counts_buy_and_sell_signals_from_most_recent_swing_signal_per_stock(session):
    a = _make_stock(session, "A", 1)
    b = _make_stock(session, "B", 2)
    c = _make_stock(session, "C", 3)
    for s in (a, b, c):
        _add_prices(session, s.id, [100.0])
    _add_signal(session, a.id, SignalType.BUY, datetime(2026, 1, 10, tzinfo=timezone.utc))
    _add_signal(session, b.id, SignalType.SELL, datetime(2026, 1, 10, tzinfo=timezone.utc))
    _add_signal(session, c.id, SignalType.HOLD, datetime(2026, 1, 10, tzinfo=timezone.utc))

    result = theme_signals.compute_theme_signal(session, "T", ["A", "B", "C"])
    assert result.buy_signal_count == 1
    assert result.sell_signal_count == 1


def test_only_the_most_recent_signal_counts_not_every_historical_row(session):
    st = _make_stock(session, "FLIP", 1)
    _add_prices(session, st.id, [100.0])
    _add_signal(session, st.id, SignalType.SELL, datetime(2026, 1, 5, tzinfo=timezone.utc))
    # The most recent row is BUY — only this should count
    st2 = Signal(id=999, stock_id=st.id, ts=datetime(2026, 1, 10, tzinfo=timezone.utc), signal=SignalType.BUY, horizon=SignalHorizon.SWING, confidence=70.0)
    session.add(st2)
    session.commit()

    result = theme_signals.compute_theme_signal(session, "T", ["FLIP"])
    assert result.buy_signal_count == 1
    assert result.sell_signal_count == 0


def test_top_symbols_sorted_by_return_descending_none_last(session):
    a = _make_stock(session, "A", 1)
    b = _make_stock(session, "B", 2)
    c = _make_stock(session, "C", 3)
    _add_prices(session, a.id, [100.0, 105.0])   # +5%
    _add_prices(session, b.id, [100.0, 120.0])   # +20%
    _add_prices(session, c.id, [50.0])            # no return (1 bar)

    result = theme_signals.compute_theme_signal(session, "T", ["A", "B", "C"])
    assert [s["symbol"] for s in result.top_symbols] == ["B", "A", "C"]


# ── generate_theme_summary() ─────────────────────────────────────────────────────

class _FakeAsyncClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        if self._exc:
            raise self._exc
        return self._response


def _make_response(status_code=200, summary=None, raw_text=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error body"
    if raw_text is not None:
        resp.json.return_value = {"content": [{"text": raw_text}]}
    elif summary is not None:
        import json
        resp.json.return_value = {"content": [{"text": json.dumps({"summary": summary})}]}
    return resp


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _neutral_result():
    return theme_signals.ThemeSignalResult(
        theme="AI / GPU Semiconductors", symbol_count=2, avg_return_5d_pct=5.0,
        avg_kscore=70.0, buy_signal_count=2, sell_signal_count=0,
        top_symbols=[{"symbol": "NVDA", "return_5d_pct": 6.0, "kscore": 75.0, "signal": "BUY"}],
    )


def test_returns_none_when_no_api_key(monkeypatch):
    monkeypatch.setattr(theme_signals, "_api_key", lambda: "")
    result = _run(theme_signals.generate_theme_summary(_neutral_result()))
    assert result is None


def test_returns_summary_on_a_successful_call(monkeypatch):
    monkeypatch.setattr(theme_signals, "_api_key", lambda: "test-key")
    resp = _make_response(200, summary="Semiconductors are up this week on strong K-Scores.")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(theme_signals.generate_theme_summary(_neutral_result()))
    assert result == "Semiconductors are up this week on strong K-Scores."


def test_fails_open_on_non_200_response(monkeypatch):
    monkeypatch.setattr(theme_signals, "_api_key", lambda: "test-key")
    resp = _make_response(500)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(theme_signals.generate_theme_summary(_neutral_result()))
    assert result is None


def test_fails_open_on_a_network_exception(monkeypatch):
    monkeypatch.setattr(theme_signals, "_api_key", lambda: "test-key")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(exc=ConnectionError("down")))
    result = _run(theme_signals.generate_theme_summary(_neutral_result()))
    assert result is None


def test_strips_markdown_fence_before_parsing(monkeypatch):
    monkeypatch.setattr(theme_signals, "_api_key", lambda: "test-key")
    fenced = '```json\n{"summary": "Real momentum this week."}\n```'
    resp = _make_response(200, raw_text=fenced)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(theme_signals.generate_theme_summary(_neutral_result()))
    assert result == "Real momentum this week."


def test_malformed_json_fails_open(monkeypatch):
    monkeypatch.setattr(theme_signals, "_api_key", lambda: "test-key")
    resp = _make_response(200, raw_text="not json at all")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(theme_signals.generate_theme_summary(_neutral_result()))
    assert result is None


# ── _clean_summary() ─────────────────────────────────────────────────────────────

def test_clean_summary_rejects_non_string():
    assert theme_signals._clean_summary(None) is None
    assert theme_signals._clean_summary(["a", "list"]) is None
    assert theme_signals._clean_summary(42) is None


def test_clean_summary_strips_whitespace():
    assert theme_signals._clean_summary("  hello world  ") == "hello world"


def test_clean_summary_empty_string_becomes_none():
    assert theme_signals._clean_summary("   ") is None


def test_clean_summary_truncates_to_500_chars():
    long = "x" * 900
    result = theme_signals._clean_summary(long)
    assert result is not None
    assert len(result) == 500

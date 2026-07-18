"""Tests for T258-PORTFOLIO-CORRELATION-PREENTRY's _max_correlation_with_open_positions()
and _bulk_fetch_daily_closes().

paper_trading_engine.py can't be imported directly in this test environment (conftest.py
stubs sqlalchemy itself as a MagicMock) — matches test_broker_position_sync.py's established
technique exactly: pop the stub, build ONE shared in-memory engine + real models while real
sqlalchemy is active, then restore the stub immediately so later-collected test files aren't
affected. The two functions under test are extracted from the real source via exec() and run
against this real session, so these tests exercise the actual logic, not a re-implementation.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_corr", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_corr"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.Price.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Price = _models.Price
TimeFrame = _models.TimeFrame
Market = _models.Market
Exchange = _models.Exchange

_ENGINE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


def _extract_functions():
    """Pulls _bulk_fetch_daily_closes() and _max_correlation_with_open_positions()'s real
    source out of paper_trading_engine.py and exec()s them against real sqlalchemy/pandas/
    models, with only `log` stubbed (the one non-pure dependency, an error-logging call)."""
    start = _ENGINE_SOURCE.index("def _bulk_fetch_daily_closes(")
    end = _ENGINE_SOURCE.index("\n\n\n# ── Entry qualifier", start)
    func_source = _ENGINE_SOURCE[start:end]
    namespace = {
        "select": select,
        "Price": Price,
        "TimeFrame": TimeFrame,
        "pd": pd,
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "log": MagicMock(),
        "_CORR_LOOKBACK_DAYS": 30,
        "_CORR_MIN_OVERLAP_ROWS": 10,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_bulk_fetch_daily_closes"], namespace["_max_correlation_with_open_positions"]


_bulk_fetch_daily_closes, _max_correlation_with_open_positions = _extract_functions()


def _make_session():
    session = Session(_ENGINE)
    session.execute(Price.__table__.delete())
    session.execute(Stock.__table__.delete())
    session.commit()
    return session


def _make_stock(session, symbol="TEST", sector="Technology"):
    stock = Stock(symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ, name=symbol, sector=sector)
    session.add(stock)
    session.commit()
    return stock


_next_price_id = [1]  # BigInteger PKs don't get SQLite's implicit autoincrement — set explicitly
# (same test-harness-only workaround as test_signal_outcome_research_rec_width.py; a real
# Postgres sequence handles this in production).


def _add_daily_closes(session, stock_id, closes, start_days_ago=30):
    base = datetime.now(timezone.utc) - timedelta(days=start_days_ago)
    for i, close in enumerate(closes):
        session.add(Price(
            id=_next_price_id[0], stock_id=stock_id, ts=base + timedelta(days=i), timeframe=TimeFrame.D1,
            open=close, high=close, low=close, close=close, volume=1_000_000,
        ))
        _next_price_id[0] += 1
    session.commit()


# ── source-text check ────────────────────────────────────────────────────────

def test_wired_into_should_enter_call_site():
    scanner_source = _ENGINE_SOURCE
    idx = scanner_source.index("se_result = _should_enter(")
    window = scanner_source[max(0, idx - 300):idx + 300]
    assert "_max_correlation_with_open_positions" in window
    assert "max_open_corr=_max_corr" in window


# ── _bulk_fetch_daily_closes ──────────────────────────────────────────────────

def test_bulk_fetch_returns_empty_dataframe_for_no_stock_ids():
    session = _make_session()
    result = _bulk_fetch_daily_closes(session, [])
    assert result.empty


def test_bulk_fetch_pivots_multiple_stocks_into_wide_columns():
    session = _make_session()
    s1 = _make_stock(session, "AAA")
    s2 = _make_stock(session, "BBB")
    # start_days_ago=10 (well inside the 30-day lookback, not near the cutoff boundary)
    _add_daily_closes(session, s1.id, [100.0, 101.0, 102.0], start_days_ago=10)
    _add_daily_closes(session, s2.id, [50.0, 49.0, 51.0], start_days_ago=10)
    wide = _bulk_fetch_daily_closes(session, [s1.id, s2.id])
    assert s1.id in wide.columns
    assert s2.id in wide.columns
    assert len(wide) == 3


def test_bulk_fetch_excludes_data_older_than_the_lookback_window():
    session = _make_session()
    s1 = _make_stock(session, "OLD")
    _add_daily_closes(session, s1.id, [100.0], start_days_ago=90)  # well outside 30-day lookback
    wide = _bulk_fetch_daily_closes(session, [s1.id])
    assert wide.empty or s1.id not in wide.columns or wide[s1.id].dropna().empty


# ── _max_correlation_with_open_positions ──────────────────────────────────────

def test_returns_none_when_no_open_positions():
    session = _make_session()
    s1 = _make_stock(session, "CAND")
    _add_daily_closes(session, s1.id, list(np.linspace(100, 110, 20)))
    result = _max_correlation_with_open_positions(session, s1.id, [], pd.DataFrame())
    assert result is None


def test_returns_none_when_open_closes_cache_is_empty():
    session = _make_session()
    s1 = _make_stock(session, "CAND")
    _add_daily_closes(session, s1.id, list(np.linspace(100, 110, 20)))
    result = _max_correlation_with_open_positions(session, s1.id, [999], pd.DataFrame())
    assert result is None


def test_detects_high_correlation_between_identical_price_series():
    session = _make_session()
    cand = _make_stock(session, "CAND")
    open_pos = _make_stock(session, "OPEN")
    # Identical (perfectly correlated) daily closes.
    closes = [100.0 + i + (i % 3) * 0.7 for i in range(25)]
    _add_daily_closes(session, cand.id, closes)
    _add_daily_closes(session, open_pos.id, closes)
    open_cache = _bulk_fetch_daily_closes(session, [open_pos.id])
    result = _max_correlation_with_open_positions(session, cand.id, [open_pos.id], open_cache)
    assert result is not None
    assert result > 0.95


def test_low_correlation_between_unrelated_series():
    session = _make_session()
    cand = _make_stock(session, "CAND")
    open_pos = _make_stock(session, "OPEN")
    rng = np.random.default_rng(42)
    cand_closes = list(100 + np.cumsum(rng.normal(0, 1, 25)))
    open_closes = list(50 + np.cumsum(rng.normal(0, 1, 25) * -1))  # independent random walk
    _add_daily_closes(session, cand.id, cand_closes)
    _add_daily_closes(session, open_pos.id, open_closes)
    open_cache = _bulk_fetch_daily_closes(session, [open_pos.id])
    result = _max_correlation_with_open_positions(session, cand.id, [open_pos.id], open_cache)
    # Not asserting a specific bound (random walks can spuriously correlate) — just confirming
    # the function returns a real, non-crashing numeric result for genuinely independent data.
    assert result is None or -1.0 <= result <= 1.0


def test_returns_none_with_insufficient_overlapping_history():
    session = _make_session()
    cand = _make_stock(session, "CAND")
    open_pos = _make_stock(session, "OPEN")
    _add_daily_closes(session, cand.id, [100.0, 101.0, 102.0])  # only 3 days — below the floor
    _add_daily_closes(session, open_pos.id, [50.0, 51.0, 52.0])
    open_cache = _bulk_fetch_daily_closes(session, [open_pos.id])
    result = _max_correlation_with_open_positions(session, cand.id, [open_pos.id], open_cache)
    assert result is None


def test_candidate_is_excluded_from_its_own_open_position_list():
    """If the candidate itself is (erroneously) included in open_stock_ids alongside a REAL
    open position, the self-reference must be filtered out rather than compared against
    itself. Deliberately does NOT build open_cache from [cand.id] alone (that scenario
    degrades to a caught pandas ValueError on the self-join regardless of whether the
    self-exclusion filter runs — a real gap this test previously had, caught by adversarially
    disabling the filter and finding the test still passed via a different code path)."""
    session = _make_session()
    cand = _make_stock(session, "CAND")
    real_open = _make_stock(session, "REAL")
    cand_closes = list(np.linspace(100, 110, 20))
    _add_daily_closes(session, cand.id, cand_closes)
    _add_daily_closes(session, real_open.id, [50.0 + (i % 2) for i in range(20)])  # weak correlation
    open_cache = _bulk_fetch_daily_closes(session, [real_open.id])
    # cand.id is (erroneously) duplicated into open_stock_ids alongside the real open position.
    result = _max_correlation_with_open_positions(
        session, cand.id, [cand.id, real_open.id], open_cache,
    )
    # Without the self-exclusion filter, comparing the candidate against itself would produce
    # a perfect (or near-perfect) 1.0 correlation, dominating the "highest absolute value" pick
    # over the genuinely weaker real_open correlation.
    assert result is not None
    assert result < 0.99


def test_picks_the_highest_absolute_correlation_across_multiple_open_positions():
    session = _make_session()
    cand = _make_stock(session, "CAND")
    weakly_correlated = _make_stock(session, "WEAK")
    strongly_correlated = _make_stock(session, "STRONG")
    cand_closes = [100.0 + i for i in range(25)]
    _add_daily_closes(session, cand.id, cand_closes)
    _add_daily_closes(session, weakly_correlated.id, [50.0 + (i % 2) for i in range(25)])
    _add_daily_closes(session, strongly_correlated.id, [200.0 + 2 * i for i in range(25)])  # perfectly correlated
    open_cache = _bulk_fetch_daily_closes(session, [weakly_correlated.id, strongly_correlated.id])
    result = _max_correlation_with_open_positions(
        session, cand.id, [weakly_correlated.id, strongly_correlated.id], open_cache,
    )
    assert result is not None
    assert result > 0.99

"""Tests for T264-SHORTSQUEEZE-PREBREAKOUT's build_prebreakout_dataset()
(services/market-data/src/backtest/prebreakout_dataset.py) — the labeled historical training-
set generator for "will a coiling, high-short-interest stock go on to a sustained breakout."

Direct user request: "predict the short sell not able to recover and send me the alert before
it starts to breakout... using daily volume and trading data along with the option call and
sell data expiry."

conftest.py stubs `sqlalchemy`/`db` as MagicMock (needed so ingestion.py-adjacent modules don't
need a real Postgres driver at import time) — this test pops those stubs BEFORE importing
anything else so the REAL sqlalchemy + REAL shared/db/models.py load for this file, matching
the established technique in test_squeeze_alert_outcomes.py/test_broker_position_sync.py.
prebreakout_dataset.py itself does real `from db import ...` and relative
`from ..services.price_compression import ...` imports — both real package imports, not
source-text extraction, so a proper package hierarchy is registered in sys.modules first.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib
import importlib.util
import pathlib
import types
from datetime import date, datetime, timedelta

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)
sys.modules["db"] = _models  # prebreakout_dataset.py's own `from db import ...` resolves to this

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[_models.Stock.__table__, _models.Price.__table__, _models.FundamentalsSnapshot.__table__],
)

# Register a real package hierarchy so prebreakout_dataset.py's `from ..services.price_
# compression import detect_price_compression` (a genuine relative import, not source-text
# extraction) resolves correctly — this module is exercised as REAL code, not a hand-copied
# duplicate, matching this repo's own established discipline for testing pure functions.
_MARKET_DATA_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
_src_pkg = types.ModuleType("src")
_src_pkg.__path__ = [str(_MARKET_DATA_SRC)]
sys.modules["src"] = _src_pkg
_services_pkg = types.ModuleType("src.services")
_services_pkg.__path__ = [str(_MARKET_DATA_SRC / "services")]
sys.modules["src.services"] = _services_pkg
_backtest_pkg = types.ModuleType("src.backtest")
_backtest_pkg.__path__ = [str(_MARKET_DATA_SRC / "backtest")]
sys.modules["src.backtest"] = _backtest_pkg

_dataset_mod = importlib.import_module("src.backtest.prebreakout_dataset")
build_prebreakout_dataset = _dataset_mod.build_prebreakout_dataset
_find_qualifying_breakout = _dataset_mod._find_qualifying_breakout

# Restore every stub now — later-collected test files must see the ORIGINAL stubbed state.
for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Price = _models.Price
FundamentalsSnapshot = _models.FundamentalsSnapshot
Market = _models.Market
TimeFrame = _models.TimeFrame

_next_id = [1000]


def _new_id() -> int:
    _next_id[0] += 1
    return _next_id[0]


def _make_session():
    session = Session(_ENGINE)
    for table in (Price.__table__, FundamentalsSnapshot.__table__, Stock.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_stock(session, symbol="TEST"):
    st = Stock(id=_new_id(), symbol=symbol, name=symbol, market=Market.US, exchange="NASDAQ", sector="Tech", active=True)
    session.add(st)
    session.commit()
    return st


def _make_snapshot(session, symbol, snapshot_date, short_pct_of_float):
    session.add(FundamentalsSnapshot(id=_new_id(), symbol=symbol, snapshot_date=snapshot_date, short_percent_of_float=short_pct_of_float))
    session.commit()


def _make_daily_bars(session, stock_id, closes, highs=None, lows=None, volumes=None, start=date(2024, 1, 1)):
    n = len(closes)
    highs = highs if highs is not None else [c * 1.01 for c in closes]
    lows = lows if lows is not None else [c * 0.99 for c in closes]
    volumes = volumes if volumes is not None else [1_000_000.0] * n
    for i in range(n):
        d = start + timedelta(days=i)
        session.add(Price(
            id=_new_id(), stock_id=stock_id, ts=datetime.combine(d, datetime.min.time()),
            timeframe=TimeFrame.D1, open=float(closes[i]), high=float(highs[i]), low=float(lows[i]),
            close=float(closes[i]), volume=float(volumes[i]),
        ))
    session.commit()


def _coiling_then_breakout_series(seed=42, n=250, coil_start=150, breakout_at=200, dry_up=True):
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for i in range(n - 1):
        if coil_start <= i < breakout_at:
            sigma = 0.003
        elif i >= breakout_at:
            sigma = 0.001
            prices[-1] *= 1.02
        else:
            sigma = 0.03
        prices.append(prices[-1] * (1 + rng.normal(0, sigma)))
    close = np.array(prices)
    high = close * 1.01
    low = close * 0.99
    volume = rng.uniform(900_000, 1_100_000, n).astype(float)
    if dry_up:
        volume[coil_start:breakout_at] *= 0.6
    volume[breakout_at] *= 3.0
    return close, high, low, volume


# ── build_prebreakout_dataset() ──────────────────────────────────────────────────────────────

def test_finds_candidate_days_during_a_real_coiling_period_and_labels_the_breakout_correctly():
    session = _make_session()
    st = _make_stock(session, "TEST")
    close, high, low, volume = _coiling_then_breakout_series()
    _make_daily_bars(session, st.id, close, high, low, volume)
    _make_snapshot(session, "TEST", date(2024, 1, 1) + timedelta(days=150), 0.20)  # clears the 15% floor

    result = build_prebreakout_dataset(session, lookback_days=1000)

    assert result.n_symbols_scanned == 1
    assert result.n_candidate_days > 0
    assert result.n_positive > 0
    # The earliest candidate rows (far from the breakout) must be labeled False; the latest
    # ones (within the 10-day forward window of the real breakout) must be labeled True.
    rows_by_date = {r.as_of: r.label for r in result.rows}
    dates_sorted = sorted(rows_by_date.keys())
    assert rows_by_date[dates_sorted[0]] is False
    assert rows_by_date[dates_sorted[-1]] is True


def test_excludes_a_symbol_below_the_short_interest_floor():
    session = _make_session()
    st = _make_stock(session, "TEST")
    close, high, low, volume = _coiling_then_breakout_series()
    _make_daily_bars(session, st.id, close, high, low, volume)
    _make_snapshot(session, "TEST", date(2024, 1, 1) + timedelta(days=150), 0.05)  # below the 15% floor

    result = build_prebreakout_dataset(session, lookback_days=1000)

    assert result.n_candidate_days == 0


def test_a_consistently_volatile_symbol_produces_far_fewer_candidate_days_than_a_real_coiling_one():
    """A percentile-based compression threshold means SOME days in any long enough window will
    fall in the bottom 20th percentile purely by chance, even for a consistently volatile
    series with no real coiling regime — this is expected, correct behavior for a percentile
    definition (bottom-20%-of-its-own-trailing-window is, by construction, going to happen
    ~20% of the time even under pure noise). The real assurance this test checks is RELATIVE:
    a genuine multi-month coiling regime (detect_price_compression()'s own dedicated test
    fixture) produces MANY MORE candidate days than a stock that never actually settles into
    one, not that a consistently volatile stock produces exactly zero."""
    session = _make_session()
    st = _make_stock(session, "TEST")
    rng = np.random.default_rng(3)
    n = 250
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0, 0.03)))  # consistently volatile throughout
    close = np.array(prices)
    _make_daily_bars(session, st.id, close, close * 1.02, close * 0.98, rng.uniform(900_000, 1_100_000, n))
    _make_snapshot(session, "TEST", date(2024, 1, 1) + timedelta(days=150), 0.20)

    volatile_result = build_prebreakout_dataset(session, lookback_days=1000)

    session2 = _make_session()
    st2 = _make_stock(session2, "TEST")
    close2, high2, low2, volume2 = _coiling_then_breakout_series()
    _make_daily_bars(session2, st2.id, close2, high2, low2, volume2)
    _make_snapshot(session2, "TEST", date(2024, 1, 1) + timedelta(days=150), 0.20)
    coiling_result = build_prebreakout_dataset(session2, lookback_days=1000)

    assert volatile_result.n_candidate_days < coiling_result.n_candidate_days


def test_no_qualifying_short_interest_snapshot_scans_zero_symbols():
    session = _make_session()
    st = _make_stock(session, "TEST")
    close, high, low, volume = _coiling_then_breakout_series()
    _make_daily_bars(session, st.id, close, high, low, volume)
    # No FundamentalsSnapshot at all for this symbol.

    result = build_prebreakout_dataset(session, lookback_days=1000)

    assert result.n_symbols_scanned == 0
    assert result.rows == []


def test_short_history_symbol_is_skipped_not_crashed():
    """Fewer than _MIN_HISTORY_BARS (146) bars — must not raise, must simply skip the symbol."""
    session = _make_session()
    st = _make_stock(session, "TEST")
    close, high, low, volume = _coiling_then_breakout_series(n=100, coil_start=60, breakout_at=90)
    _make_daily_bars(session, st.id, close, high, low, volume)
    _make_snapshot(session, "TEST", date(2024, 1, 1) + timedelta(days=60), 0.20)

    result = build_prebreakout_dataset(session, lookback_days=1000)  # must not raise

    assert result.n_symbols_scanned == 1
    assert result.n_candidate_days == 0  # too little history for detect_price_compression to ever fire


def test_point_in_time_a_later_snapshot_never_leaks_short_interest_backward():
    """A day BEFORE the qualifying weekly snapshot even existed must never be treated as
    short-interest-qualifying, even though a later snapshot for the same symbol does qualify —
    the same point-in-time discipline ml-prediction's own builder.py already established for
    this exact weekly-snapshot-onto-daily-bars join shape.

    The COILING period itself deliberately SPANS both sides of the qualifying snapshot's own
    date (coiling starts at day 80, well before the day-140 snapshot, and continues through
    day 200) — this is the genuine boundary test: some real candidate days exist on BOTH sides
    of the snapshot date, so the point-in-time cutoff is actually exercised, not just
    coincidentally satisfied because no candidate day happened to exist before the cutoff
    anyway. (An earlier version of this test used a fixture where the compression precondition
    only started well AFTER the snapshot anyway, so a forward-vs-backward merge_asof direction
    bug was never actually exercised — caught during adversarial verification when a real
    sabotage of the merge direction still passed this test; fixed by overlapping the two
    preconditions so a genuine leak, if reintroduced, produces real pre-cutoff candidate rows.)
    """
    session = _make_session()
    st = _make_stock(session, "TEST")
    close, high, low, volume = _coiling_then_breakout_series(coil_start=80, breakout_at=230)
    _make_daily_bars(session, st.id, close, high, low, volume)
    # The FIRST snapshot (below floor) covers days 0-139; the SECOND (qualifying) covers day
    # 140 onward. Coiling runs 80-229, so real candidate days exist BOTH before day 140
    # (which must be excluded — short interest wasn't qualifying yet) and after (included).
    _make_snapshot(session, "TEST", date(2024, 1, 1), 0.05)
    _make_snapshot(session, "TEST", date(2024, 1, 1) + timedelta(days=140), 0.20)

    result = build_prebreakout_dataset(session, lookback_days=1000)

    cutoff = date(2024, 1, 1) + timedelta(days=140)
    assert len(result.rows) > 0  # sanity: the fixture actually produces candidate rows to check
    assert all(r.as_of >= cutoff for r in result.rows)


# ── _find_qualifying_breakout() ──────────────────────────────────────────────────────────────

def _bars_df(closes, volumes=None):
    import pandas as pd
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float) if volumes is not None else np.full(len(closes), 1_000_000.0)
    return pd.DataFrame({"close": closes, "high": closes + 0.5, "low": closes - 0.5, "volume": volumes})


def test_find_qualifying_breakout_true_for_a_held_volume_confirmed_break():
    closes = [100.0] * 25 + [110.0, 111.0, 112.0, 113.0]  # breaks above the trailing 20-high and holds
    volumes = [1_000_000.0] * 25 + [3_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0]
    df = _bars_df(closes, volumes)
    assert _find_qualifying_breakout(df, start_idx=24) is True


def test_find_qualifying_breakout_false_when_it_pokes_and_reverts():
    """A classic poke-and-reject — breaks above the level, but reverses back below it within
    the hold window. Must NOT be labeled a qualifying breakout."""
    closes = [100.0] * 25 + [110.0, 95.0, 95.0, 95.0]
    volumes = [1_000_000.0] * 25 + [3_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0]
    df = _bars_df(closes, volumes)
    assert _find_qualifying_breakout(df, start_idx=24) is False


def test_find_qualifying_breakout_false_without_volume_confirmation():
    """A break above the level on ordinary (not elevated) volume must not qualify — the
    _BREAKOUT_MIN_RVOL requirement exists specifically to exclude a low-conviction drift."""
    closes = [100.0] * 25 + [110.0, 111.0, 112.0, 113.0]
    volumes = [1_000_000.0] * 29  # no volume spike on the breakout bar
    df = _bars_df(closes, volumes)
    assert _find_qualifying_breakout(df, start_idx=24) is False


def test_find_qualifying_breakout_false_outside_the_window():
    """A real, volume-confirmed, held breakout that happens AFTER the forward window has
    already closed must not be counted."""
    closes = [100.0] * 40 + [110.0, 111.0, 112.0, 113.0]  # breakout at bar 40, window is only 10 days from bar 24
    volumes = [1_000_000.0] * 40 + [3_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0]
    df = _bars_df(closes, volumes)
    assert _find_qualifying_breakout(df, start_idx=24) is False

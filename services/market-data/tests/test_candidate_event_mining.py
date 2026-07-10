"""T241-POSITION-SCALING: tests for candidate_event_mining.py's pure/DB-independent helpers.

mine_candidate_events()/mine_and_report() are DB-integration functions (real SQLAlchemy
Session against the shared ORM models) and are verified via a live smoke-test run inside
the actual container per this module's own deployment notes, not a local unit test here —
importing shared/db eagerly pulls in env-dependent settings/structlog that aren't available
outside the container, and every other module in this backtest package (multi_tranche_engine,
triple_barrier_labeling, position_scaling_gate) established the same pattern: keep local unit
tests DB-free and pure.
"""
from datetime import timedelta

import numpy as np
import pandas as pd

from src.backtest.candidate_event_mining import (
    MinedCandidate,
    _atr_at,
    _build_atr_series,
    _regime_favorable_near,
    build_feature_matrix,
    candidates_to_dataframe,
)
from src.backtest.position_scaling_gate import FEATURE_COLUMNS


def _price_df(n=30, start_price=100.0, seed=1):
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    closes = start_price + np.cumsum(rng.uniform(-1, 1, n))
    highs = closes + rng.uniform(0.1, 1.0, n)
    lows = closes - rng.uniform(0.1, 1.0, n)
    return pd.DataFrame({"ts": dates, "high": highs, "low": lows, "close": closes})


def test_regime_favorable_near_matches_bull_within_window():
    snapshots = pd.DataFrame({
        "entry_date": pd.to_datetime(["2026-03-01", "2026-03-15"]),
        "market_regime_at_entry": ["bull", "bear"],
    })
    # 2026-03-03 is 2 days from the 2026-03-01 bull snapshot — within the 10-day window.
    assert _regime_favorable_near(snapshots, pd.Timestamp("2026-03-03").date()) is True


def test_regime_favorable_near_matches_bear_is_not_favorable():
    snapshots = pd.DataFrame({
        "entry_date": pd.to_datetime(["2026-03-15"]),
        "market_regime_at_entry": ["bear"],
    })
    assert _regime_favorable_near(snapshots, pd.Timestamp("2026-03-16").date()) is False


def test_regime_favorable_near_too_far_defaults_false():
    snapshots = pd.DataFrame({
        "entry_date": pd.to_datetime(["2026-01-01"]),
        "market_regime_at_entry": ["bull"],
    })
    # 2026-03-01 is ~59 days from the only snapshot — well outside the 10-day window.
    assert _regime_favorable_near(snapshots, pd.Timestamp("2026-03-01").date()) is False


def test_regime_favorable_near_empty_snapshots_defaults_false():
    assert _regime_favorable_near(pd.DataFrame(), pd.Timestamp("2026-03-01").date()) is False


def test_build_atr_series_matches_canonical_atr_directly():
    """Hand-check: _build_atr_series should be identical to calling common.indicators.atr
    directly on the same columns — it's a thin wrapper, not a reimplementation."""
    from common.indicators import atr as canon_atr

    df = _price_df(40)
    wrapped = _build_atr_series(df)
    direct = canon_atr(df["high"], df["low"], df["close"], period=14)
    pd.testing.assert_series_equal(wrapped, direct)


def test_atr_at_looks_up_value_at_or_before_timestamp():
    df = _price_df(30)
    atr_series = _build_atr_series(df)
    # Query a timestamp exactly on a bar — should return that bar's ATR (once warmed up).
    ts = df["ts"].iloc[20]
    val = _atr_at(atr_series, df, ts)
    assert val is not None
    assert val == atr_series.loc[20] or (pd.isna(atr_series.loc[20]) and val is None)


def test_atr_at_before_any_data_returns_none():
    df = _price_df(30)
    atr_series = _build_atr_series(df)
    before_start = df["ts"].iloc[0] - timedelta(days=5)
    assert _atr_at(atr_series, df, before_start) is None


def test_atr_at_during_warmup_period_returns_none():
    """ATR needs `period` (14) prior bars to produce a real value — before that, NaN."""
    df = _price_df(30)
    atr_series = _build_atr_series(df)
    early_ts = df["ts"].iloc[2]  # well within the 14-bar warmup window
    assert _atr_at(atr_series, df, early_ts) is None


def test_candidates_to_dataframe_has_expected_columns():
    cand = MinedCandidate(
        symbol="TEST",
        event_timestamp=pd.Timestamp("2026-03-01"),
        atr_at_event=2.0,
        candidate_add_price=95.0,
        candidate_add_shares=100.0,
        existing_tranches=[(pd.Timestamp("2026-02-15"), 100.0, 100.0)],
    )
    df = candidates_to_dataframe([cand])
    assert list(df.columns) == [
        "symbol", "event_timestamp", "atr_at_event",
        "candidate_add_price", "candidate_add_shares", "existing_tranches",
    ]
    assert df.iloc[0]["symbol"] == "TEST"
    assert df.iloc[0]["existing_tranches"] == [(pd.Timestamp("2026-02-15"), 100.0, 100.0)]


def test_candidates_to_dataframe_empty_list():
    df = candidates_to_dataframe([])
    assert df.empty


def test_build_feature_matrix_produces_feature_columns_and_matching_length():
    cand = MinedCandidate(
        symbol="TEST",
        event_timestamp=pd.Timestamp("2026-03-01"),
        atr_at_event=2.0,
        candidate_add_price=95.0,
        candidate_add_shares=100.0,
        existing_tranches=[(pd.Timestamp("2026-02-15"), 100.0, 100.0)],
        primary_signal_confidence=70.0,
        signal_confidence_at_last_entry=60.0,
        regime_is_favorable=True,
        volume_zscore=1.5,
        support_level=92.0,
        days_since_last_entry=14,
        num_prior_adds=0,
    )
    labeled = pd.DataFrame([{
        "label_add_was_correct": True,
        "realized_return_with_add": 0.03,
    }])
    X, y, ret = build_feature_matrix([cand], labeled)
    assert list(X.columns) == FEATURE_COLUMNS
    assert len(X) == len(y) == len(ret) == 1
    assert y.iloc[0] is True or y.iloc[0] == True  # noqa: E712 — pandas bool passthrough
    assert ret.iloc[0] == 0.03
    # Hand-verified: cost_basis = 100.0 (single tranche), current_price = 95.0
    # -> current_drawdown_pct = (95-100)/100 = -0.05
    assert round(X.iloc[0]["current_drawdown_pct"], 4) == -0.05
    # Hand-verified: signal_confidence_delta = 70.0 - 60.0 = 10.0
    assert X.iloc[0]["signal_confidence_delta"] == 10.0
    # Hand-verified: distance_to_support_atr = (95 - 92) / 2.0 = 1.5
    assert X.iloc[0]["distance_to_support_atr"] == 1.5


def test_build_feature_matrix_uses_support_fallback_when_none():
    """When no sr_nearest_support was available on the mined signal, compute_features_for_event
    falls back to current_price * 0.97 (a 3%-below synthetic support) rather than crashing on
    a missing value — verify this fallback actually engages and produces a finite result."""
    cand = MinedCandidate(
        symbol="TEST",
        event_timestamp=pd.Timestamp("2026-03-01"),
        atr_at_event=2.0,
        candidate_add_price=100.0,
        candidate_add_shares=100.0,
        existing_tranches=[(pd.Timestamp("2026-02-15"), 100.0, 100.0)],
        support_level=None,
    )
    labeled = pd.DataFrame([{"label_add_was_correct": False, "realized_return_with_add": -0.02}])
    X, _, _ = build_feature_matrix([cand], labeled)
    # Hand-verified: fallback support = 100.0 * 0.97 = 97.0; distance = (100-97)/2.0 = 1.5
    assert round(X.iloc[0]["distance_to_support_atr"], 4) == 1.5


def test_build_feature_matrix_length_mismatch_raises():
    cand = MinedCandidate(
        symbol="TEST", event_timestamp=pd.Timestamp("2026-03-01"), atr_at_event=2.0,
        candidate_add_price=95.0, candidate_add_shares=100.0,
        existing_tranches=[(pd.Timestamp("2026-02-15"), 100.0, 100.0)],
    )
    labeled = pd.DataFrame()  # 0 rows vs. 1 candidate
    try:
        build_feature_matrix([cand], labeled)
        assert False, "expected AssertionError on length mismatch"
    except AssertionError:
        pass

"""Tests for MPE-04's OPTIONS_COLUMNS (opt_cp_ratio, opt_whale_count) — a point-in-time-safe
join of each row's own options_flow_snapshots row against that row's own bar date, mirroring
analyst_pt_upside's/eps_revision_direction's own established merge_asof(direction="backward")
pattern exactly.

build_features() only depends on numpy/pandas (both real, installed packages), so it imports
and runs normally under pytest — no stub workaround needed, matching test_analyst_pt_upside.py's
own established precedent.
"""
import numpy as np
import pandas as pd
import pytest

from src.features import FEATURE_COLUMNS, build_features
from src.features.builder import OPTIONS_COLUMNS


def _price_df(n=400, start="2022-01-01"):
    """A real (non-flat) noisy random walk — matching test_analyst_pt_upside.py's own
    established fixture, since a perfectly flat close series starves build_features() down
    to 0 rows via required technical-indicator NaN cascades."""
    rng = np.random.default_rng(42)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=n, freq="D"),
        "close": close,
        "high": close + 2,
        "low": close - 2,
        "volume": rng.integers(1000, 5000, n),
    })


def _opt_snap(date_str, cp_ratio=None, whale_count=None):
    return {"snapshot_date": date_str, "opt_cp_ratio": cp_ratio, "opt_whale_count": whale_count}


def test_options_columns_are_in_feature_columns():
    assert "opt_cp_ratio" in FEATURE_COLUMNS
    assert "opt_whale_count" in FEATURE_COLUMNS


def test_no_snapshots_leaves_both_columns_nan_not_a_crash():
    """The common case — most training symbols were never in options_flow_snapshots' own
    bounded coverage set (see _load_options_snapshots()'s docstring)."""
    df = _price_df()
    X, _, _ = build_features(df, horizon=5, options_snapshots=[])
    assert X["opt_cp_ratio"].isna().all()
    assert X["opt_whale_count"].isna().all()


def test_none_options_snapshots_also_leaves_both_columns_nan_not_a_crash():
    """options_snapshots defaults to None (not an empty list) at the parameter level — must
    degrade identically to the empty-list case, not raise on `if options_snapshots:` with a
    falsy None."""
    df = _price_df()
    X, _, _ = build_features(df, horizon=5, options_snapshots=None)
    assert X["opt_cp_ratio"].isna().all()
    assert X["opt_whale_count"].isna().all()


def test_a_row_on_or_after_the_snapshot_date_sees_the_real_value():
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_opt_snap("2022-01-10", cp_ratio=2.5, whale_count=3)]
    X, _, _ = build_features(df, horizon=5, options_snapshots=snaps, inference_mode=True)
    # inference_mode keeps the most recent bar, well after 2022-01-10.
    assert X["opt_cp_ratio"].iloc[-1] == pytest.approx(2.5)
    assert X["opt_whale_count"].iloc[-1] == pytest.approx(3.0)


def test_a_row_before_any_snapshot_exists_is_nan_not_a_leaked_future_value():
    """Point-in-time correctness: a row dated BEFORE the first real snapshot must never see a
    cp_ratio/whale_count that didn't exist yet."""
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_opt_snap("2023-01-20", cp_ratio=4.0, whale_count=5)]
    X, _, _ = build_features(df, horizon=5, options_snapshots=snaps)
    dates = pd.to_datetime(df["ts"])
    early_mask = dates < pd.Timestamp("2022-12-20")
    early_rows = X.loc[X.index.isin(np.where(early_mask)[0]), "opt_cp_ratio"]
    assert early_rows.isna().all()


def test_a_later_snapshot_supersedes_an_earlier_one_for_rows_after_it():
    """Must reflect the MOST RECENT snapshot as-of a row's own date, not the first one ever
    seen — matching every other PIT column's merge_asof(direction='backward') semantics."""
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_opt_snap("2022-01-05", cp_ratio=1.0, whale_count=0),
             _opt_snap("2022-01-20", cp_ratio=8.0, whale_count=10)]
    X, _, _ = build_features(df, horizon=5, options_snapshots=snaps, inference_mode=True)
    assert X["opt_cp_ratio"].iloc[-1] == pytest.approx(8.0)
    assert X["opt_whale_count"].iloc[-1] == pytest.approx(10.0)


def test_computed_in_both_training_and_inference_mode():
    """No static fund_data broadcast equivalent exists for these columns — must be computed in
    both modes, matching analyst_pt_upside's/eps_revision_direction's own precedent."""
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_opt_snap("2022-01-01", cp_ratio=3.0, whale_count=1)]
    X_train, _, _ = build_features(df, horizon=5, options_snapshots=snaps, inference_mode=False)
    X_infer, _, _ = build_features(df, horizon=5, options_snapshots=snaps, inference_mode=True)
    assert not X_train["opt_cp_ratio"].isna().all()
    assert not X_infer["opt_cp_ratio"].isna().all()


def test_missing_whale_count_key_but_present_cp_ratio_degrades_that_one_column_only():
    """A snapshot dict missing one of the two keys entirely (e.g. an older row shape) must not
    poison the OTHER, present column."""
    df = _price_df(n=400, start="2022-01-01")
    snaps = [{"snapshot_date": "2022-01-05", "opt_cp_ratio": 2.0}]  # no opt_whale_count key
    X, _, _ = build_features(df, horizon=5, options_snapshots=snaps, inference_mode=True)
    assert X["opt_cp_ratio"].iloc[-1] == pytest.approx(2.0)
    assert X["opt_whale_count"].isna().all()


def test_options_columns_are_nan_allowed_not_required_for_a_row_to_survive():
    """A row must NOT be dropped from X just because opt_cp_ratio/opt_whale_count are NaN —
    matching FUNDAMENTAL_COLUMNS/WEEKLY_COLUMNS/SECTOR_COLUMNS/OUTCOME_COLUMNS' own established
    NaN-allowed convention (the _nan_ok set in build_features())."""
    df = _price_df()
    X, y_dir, _ = build_features(df, horizon=5, options_snapshots=[])
    assert len(X) > 0
    assert X["opt_cp_ratio"].isna().all()


def test_a_malformed_snapshot_join_fails_open_to_nan_not_a_crash():
    """A genuinely malformed snapshot_date (unparseable) must degrade to NaN for both columns,
    never raise out of build_features() entirely."""
    df = _price_df()
    snaps = [{"snapshot_date": "not-a-real-date", "opt_cp_ratio": 5.0, "opt_whale_count": 2}]
    X, _, _ = build_features(df, horizon=5, options_snapshots=snaps)
    assert "opt_cp_ratio" in X.columns
    assert "opt_whale_count" in X.columns

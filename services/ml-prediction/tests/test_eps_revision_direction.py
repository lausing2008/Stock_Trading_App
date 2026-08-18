"""Tests for T237-ML2b: eps_revision_direction, reintroduced point-in-time-correctly into
build_features() after its original T237-ML2 removal for broadcast lookahead bias.

build_features() only depends on numpy/pandas (both real, installed packages), so it imports
and runs normally under pytest — no stub workaround needed, matching test_features.py's own
established precedent.
"""
import numpy as np
import pandas as pd

from src.features import FEATURE_COLUMNS, build_features


def _price_df(n=400, start="2022-01-01"):
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=n, freq="D"),
        "close": 100 + rng.normal(0, 1, n).cumsum(),
        "high": 102 + rng.normal(0, 1, n).cumsum(),
        "low": 98 + rng.normal(0, 1, n).cumsum(),
        "volume": rng.integers(1000, 5000, n),
    })


def _snap(date_str, rec_mean):
    return {"snapshot_date": date_str, "recommendation_mean": rec_mean}


def test_eps_revision_direction_is_in_feature_columns():
    assert "eps_revision_direction" in FEATURE_COLUMNS


def test_no_snapshots_leaves_the_column_nan_not_a_crash():
    df = _price_df()
    X, _, _ = build_features(df, horizon=5, fund_snapshots=[])
    assert X["eps_revision_direction"].isna().all()


def test_upgrade_trend_produces_positive_one():
    """recommendation_mean trending DOWN (more bullish) over the window must yield +1 —
    lower recommendation_mean = more bullish, so oldest-minus-newest is positive."""
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_snap(f"2023-01-{i+1:02d}", 3.0 - i * 0.2) for i in range(8)]  # 3.0 -> 1.6
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    # The most recent row (inference_mode keeps the latest bar) should see the full upgrade.
    assert X["eps_revision_direction"].iloc[-1] == 1.0


def test_downgrade_trend_produces_negative_one():
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_snap(f"2023-01-{i+1:02d}", 1.6 + i * 0.2) for i in range(8)]  # 1.6 -> 3.0
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    assert X["eps_revision_direction"].iloc[-1] == -1.0


def test_flat_trend_produces_zero():
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_snap(f"2023-01-{i+1:02d}", 2.5) for i in range(8)]  # unchanged
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    assert X["eps_revision_direction"].iloc[-1] == 0.0


def test_a_small_delta_within_the_015_band_is_flat_not_upgrading():
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_snap("2023-01-01", 2.60), _snap("2023-01-08", 2.50)]  # delta = 0.10, < 0.15
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    assert X["eps_revision_direction"].iloc[-1] == 0.0


def test_only_one_snapshot_is_insufficient_stays_nan():
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_snap("2023-01-01", 2.0)]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    assert pd.isna(X["eps_revision_direction"].iloc[-1])


def test_a_training_row_never_sees_a_snapshot_after_its_own_date():
    """The core point-in-time-correctness property this whole reimplementation exists for:
    an EARLY training row must not reflect a LATER upgrade/downgrade that hadn't happened yet
    as of that row's own date — the exact lookahead bias the original T237-ML2 removal found."""
    df = _price_df(n=400, start="2022-01-01")
    # Flat through early Dec, then a sharp downgrade trend starting January — a row dated
    # BEFORE the downgrade snapshots exist must not already reflect it.
    snaps = (
        [_snap(f"2022-12-{i+1:02d}", 1.5) for i in range(10)]
        + [_snap(f"2023-01-{i+1:02d}", 1.5 + i * 0.3) for i in range(8)]  # 1.5 -> 3.6, a real downgrade
    )
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps)
    dates = pd.to_datetime(df["ts"])
    early_mask = dates < pd.Timestamp("2022-12-20")  # well before any downgrade snapshot exists
    early_rows = X.loc[X.index.isin(np.where(early_mask)[0]), "eps_revision_direction"]
    # None of these early rows can show the downgrade (-1) that hadn't happened yet — either
    # NaN (no snapshot yet) or 0 (flat, correctly reflecting only the pre-downgrade snapshots).
    assert not (early_rows == -1.0).any(), (
        "an early training row incorrectly reflects a downgrade that happened LATER — "
        "this is the exact lookahead bias T237-ML2 was created to fix"
    )


def test_a_later_training_row_correctly_sees_the_downgrade_that_already_happened():
    """The mirror of the lookahead-safety test above — a row dated AFTER the downgrade
    snapshots exist SHOULD reflect it, confirming the fix doesn't just always return NaN/0.

    A 400-bar series starting 2022-01-01 (matching this file's own established fixture size
    for enough warmup history) yields usable rows only through ~2023-01-29 after horizon-shift
    trimming — the downgrade snapshots here are scheduled to complete well before that, with
    the "late" assertion window still comfortably inside the real usable row range."""
    df = _price_df(n=400, start="2022-01-01")
    snaps = (
        [_snap(f"2022-12-{i+1:02d}", 1.5) for i in range(10)]
        + [_snap(f"2023-01-{i+1:02d}", 1.5 + i * 0.3) for i in range(8)]  # 1.5 -> 3.6 by Jan 8
    )
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps)
    dates = pd.to_datetime(df["ts"])
    late_mask = dates >= pd.Timestamp("2023-01-15")
    late_rows = X.loc[X.index.isin(np.where(late_mask)[0]), "eps_revision_direction"]
    assert (late_rows == -1.0).any(), "a row well after the downgrade completed should see it"


def test_window_is_capped_at_8_not_the_full_history():
    """The formula is 'oldest of the last 8 snapshots' — a 9th, older snapshot must NOT be
    included in the delta, matching signals.py's own live LIMIT 8 semantics exactly."""
    df = _price_df(n=400, start="2022-01-01")
    # 9 snapshots: the OLDEST (rec=5.0, a huge value) must be excluded from an 8-window delta.
    snaps = [_snap("2022-12-01", 5.0)] + [_snap(f"2023-01-{i+1:02d}", 2.0) for i in range(8)]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    # If the 9th (oldest, rec=5.0) snapshot were wrongly included, delta would be huge/positive
    # (5.0 - 2.0 = 3.0 -> upgrading). With the correct 8-window, all 8 values are 2.0 -> flat.
    assert X["eps_revision_direction"].iloc[-1] == 0.0


def test_inference_mode_computes_the_feature_too_not_just_training():
    """The gap this session's own fix closed: the ORIGINAL removed implementation only ever
    computed this feature via a live-only DB query at inference time with no training
    equivalent at all; this reimplementation must work in BOTH modes, using the SAME
    fund_snapshots-based computation — not silently NaN at inference time."""
    df = _price_df(n=400, start="2022-01-01")
    snaps = [_snap(f"2023-01-{i+1:02d}", 3.0 - i * 0.3) for i in range(8)]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    assert X["eps_revision_direction"].iloc[-1] == 1.0

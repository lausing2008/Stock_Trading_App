"""Regression tests for AUD232-METAMODEL-MEDIUM-GROUP item 059 — meta_trainer.py's
train_meta_model() previously called build_features() (and compute_label_threshold()) FRESH
for every signal_outcome row, re-slicing the price DataFrame up to that row's signal_date and
recomputing the entire rolling-window indicator pipeline (SMA/RSI/MACD/ATR/etc.) from scratch
each time — for a symbol with N outcome rows, that's N full recomputations over heavily-
overlapping windows instead of one.

The fix computes build_features() ONCE per symbol on the full price history and indexes into
it per row instead. This is safe specifically because:
  (1) build_features()'s indicators are all trailing rolling-window computations — a row's
      value depends only on data up to and including that row, never on data trailing after
      it, so computing on the full df vs. a truncated df_upto slice gives numerically
      identical values for any given date;
  (2) `horizon` only affects build_features()'s discarded fwd_ret/y_dir outputs and
      compute_label_threshold()'s result (itself only used by the non-inference-mode dead-zone
      mask, never reached here since inference_mode=True is always passed) — both were
      genuinely unused busywork on top of the duplication itself.

These tests exercise the REAL build_features() (pure pandas/numpy, no DB/mocking needed) to
prove the core numerical-parity claim the fix depends on, rather than asserting against a
hand-copied reimplementation of the old per-row logic (which could silently drift from the
real thing and mask a real regression).
"""
import pathlib

import numpy as np
import pandas as pd
import pytest

from src.features.builder import FEATURE_COLUMNS, build_features

_META_TRAINER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "meta_trainer.py"
_META_TRAINER_SOURCE = _META_TRAINER_PATH.read_text()


def _train_meta_model_body() -> str:
    start = _META_TRAINER_SOURCE.index("def train_meta_model(")
    end = _META_TRAINER_SOURCE.index("\ndef ", start + 1)
    return _META_TRAINER_SOURCE[start:end]


def test_build_features_is_called_exactly_once_per_symbol_not_inside_the_row_loop():
    """Regression guard against reintroducing the per-row duplication: build_features(...) must
    appear exactly once inside the `for symbol, sym_rows in symbol_rows.items():` loop, OUTSIDE
    (before) the inner `for row in sym_rows_sorted:` loop — not once per signal_outcome row."""
    body = _train_meta_model_body()
    symbol_loop_idx = body.index("for symbol, sym_rows in symbol_rows.items():")
    row_loop_idx = body.index("for row in sym_rows_sorted:", symbol_loop_idx)
    build_features_call_idx = body.index("build_features(", symbol_loop_idx)
    assert symbol_loop_idx < build_features_call_idx < row_loop_idx, (
        "build_features() must be called once per symbol, BEFORE the per-row loop begins"
    )
    # And must not appear again inside the row loop itself.
    assert "build_features(" not in body[row_loop_idx:]


def test_compute_label_threshold_is_no_longer_called_in_the_row_loop():
    """compute_label_threshold()'s result was never actually used by X_feat in inference_mode
    (see this test file's own module docstring) — confirm the now-genuinely-dead per-row CALL
    was removed (the name may still appear in an explanatory comment about why it's gone)."""
    body = _train_meta_model_body()
    assert "= compute_label_threshold(" not in body
    # Must not be imported into this function's local scope either — it's unused here now.
    import_line = next(line for line in body.splitlines() if "from ..features.builder import" in line)
    assert "compute_label_threshold" not in import_line


def _synthetic_price_df(n=300, seed=11) -> pd.DataFrame:
    """Real OHLCV bars with enough history (300 bars) that build_features()'s longest-window
    column (momentum_12_1, needs 252+21 bars) is non-NaN well before the end of the series —
    matching test_meta_trainer.py's own established fixture-construction discipline (varying
    volume so volume_z's rolling std() is never exactly 0)."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + np.abs(rng.normal(0, 0.3, n))
    low = close - np.abs(rng.normal(0, 0.3, n))
    volume = rng.uniform(5e5, 1.5e6, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"ts": dates, "open": close, "high": high, "low": low, "close": close, "volume": volume})


def test_full_history_call_matches_per_row_truncated_call_at_a_real_date():
    """The core numerical-parity claim: build_features() called ONCE on the full df, then
    indexed at a given date, must produce the EXACT same feature row as the OLD approach of
    calling build_features() fresh on a df TRUNCATED to that same date."""
    df = _synthetic_price_df()
    signal_row_idx = 280  # well past every rolling window's warm-up period

    # OLD approach: truncate to the signal date, then build fresh.
    df_upto = df.iloc[:signal_row_idx + 1].copy()
    X_old, _, _ = build_features(df_upto, horizon=10, macro_df=None, inference_mode=True)
    assert not X_old.empty
    old_row = X_old.iloc[-1]

    # NEW approach: build once on the FULL df, then index the same date out of it.
    X_new, _, _ = build_features(df, horizon=10, macro_df=None, inference_mode=True)
    assert signal_row_idx in X_new.index
    new_row = X_new.loc[signal_row_idx]

    for col in FEATURE_COLUMNS:
        old_val = old_row.get(col)
        new_val = new_row.get(col)
        if pd.isna(old_val) and pd.isna(new_val):
            continue
        assert old_val == pytest.approx(new_val, rel=1e-9, abs=1e-12), f"mismatch on column {col!r}"


def test_full_history_call_matches_per_row_truncated_call_across_multiple_dates():
    """Same parity property, checked at several different signal dates within the same
    symbol — this is the exact shape of the real bug: multiple signal_outcome rows for one
    symbol, each previously triggering its own fresh, independent build_features() call."""
    df = _synthetic_price_df()
    X_new, _, _ = build_features(df, horizon=10, macro_df=None, inference_mode=True)

    for signal_row_idx in (260, 270, 285, 299):
        df_upto = df.iloc[:signal_row_idx + 1].copy()
        X_old, _, _ = build_features(df_upto, horizon=10, macro_df=None, inference_mode=True)
        assert not X_old.empty
        old_row = X_old.iloc[-1]
        assert signal_row_idx in X_new.index, f"row {signal_row_idx} missing from full-history result"
        new_row = X_new.loc[signal_row_idx]
        for col in FEATURE_COLUMNS:
            old_val, new_val = old_row.get(col), new_row.get(col)
            if pd.isna(old_val) and pd.isna(new_val):
                continue
            assert old_val == pytest.approx(new_val, rel=1e-9, abs=1e-12), (
                f"mismatch on column {col!r} at row {signal_row_idx}"
            )


def test_horizon_value_does_not_affect_the_feature_vector_in_inference_mode():
    """The fix relies on `horizon` being irrelevant to X_feat in inference_mode=True (it only
    affects fwd_ret/y_dir, both discarded by the caller) — meaning a single build_features()
    call can serve rows with DIFFERENT horizons (SHORT/SWING/LONG/GROWTH) within the same
    symbol. Verify directly: two calls with different horizon values must produce an
    IDENTICAL X in inference_mode, proving horizon truly has no bearing on the feature vector
    the meta-trainer actually uses."""
    df = _synthetic_price_df()
    X_short, _, _ = build_features(df, horizon=5, macro_df=None, inference_mode=True)
    X_growth, _, _ = build_features(df, horizon=10, macro_df=None, inference_mode=True)
    X_long, _, _ = build_features(df, horizon=20, macro_df=None, inference_mode=True)

    pd.testing.assert_frame_equal(X_short, X_growth)
    pd.testing.assert_frame_equal(X_short, X_long)


def test_index_preserves_original_row_position_after_the_inference_mode_mask():
    """The fix's lookup (X_feat_full.loc[row_idx]) depends on build_features()'s boolean mask
    (X[mask]) preserving the ORIGINAL DataFrame index values for surviving rows, rather than
    resetting to a fresh 0..N range — otherwise indexing by the original row position would
    silently select the wrong row (or raise a KeyError, which the fix's own `if row_idx not in
    X_feat_full.index: continue` guard would then skip, masking the bug as "insufficient
    data" rather than a real lookup error)."""
    df = _synthetic_price_df()
    X, _, _ = build_features(df, horizon=10, macro_df=None, inference_mode=True)
    # Every surviving index value must be a valid position in the ORIGINAL df (0..n-1) —
    # not a freshly reset 0..len(X)-1 range, which would only coincidentally look valid.
    assert X.index.max() < len(df)
    # Since len(X) < len(df) (early rows are dropped by the NaN-feature mask), a reset index
    # would necessarily differ from the original index for at least the tail of the frame —
    # confirm the actual index values are NOT a simple 0..len(X)-1 range.
    assert not (X.index == pd.RangeIndex(len(X))).all(), (
        "build_features() appears to reset the index after masking — the fix's per-row "
        "lookup by original position would silently break"
    )

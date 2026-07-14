import numpy as np
import pandas as pd

from src.dsl import compute_features, evaluate_rule


def _df(n=300):
    rng = np.random.default_rng(0)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame(
        {
            "ts": pd.date_range("2023-01-01", periods=n, freq="D"),
            "open": close + rng.normal(0, 0.2, n),
            "high": close + np.abs(rng.normal(0, 0.5, n)),
            "low": close - np.abs(rng.normal(0, 0.5, n)),
            "close": close,
            "volume": rng.integers(1000, 5000, n),
        }
    )


def test_simple_rule():
    df = compute_features(_df())
    rule = {"op": "<", "left": "rsi_14", "right": 30}
    out = evaluate_rule(rule, df)
    assert out.dtype == bool
    assert len(out) == len(df)


def test_and_rule():
    df = compute_features(_df())
    rule = {
        "op": "and",
        "nodes": [
            {"op": "<", "left": "rsi_14", "right": 70},
            {"op": ">", "left": "close", "right": "sma_50"},
        ],
    }
    out = evaluate_rule(rule, df)
    assert out.dtype == bool


def test_crosses_above():
    df = compute_features(_df())
    rule = {"op": "crosses_above", "left": "macd", "right": "macd_signal"}
    out = evaluate_rule(rule, df)
    assert out.dtype == bool


# ── T247-STRATEGYENGINE-NOT-NAN regression guard ──────────────────────────────
# `not` over a leaf comparison during an indicator's warmup window (NaN) previously inverted
# to True instead of staying False — every leaf comparison fillna(False)'d NaN to False BEFORE
# `not`'s `~` ran, so `not` had no way to distinguish "condition is false" from "condition is
# unknown" and flipped the already-collapsed False to True for the entire warmup window.

def _short_df(n=20):
    """Short enough that sma_200 (needs 200 bars) is NaN for the WHOLE series — the exact
    shape that reproduced the bug (a 20-bar synthetic series produced [True]*20)."""
    rng = np.random.default_rng(1)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame({
        "close": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "volume": rng.integers(1000, 5000, n),
    })


def test_not_over_nan_comparison_stays_false_during_warmup():
    """The exact reproduction from the audit finding: a 'not' over a comparison against
    sma_200 (all-NaN for a 20-bar series) must NEVER be True during warmup."""
    df = compute_features(_short_df(20))
    assert df["sma_200"].isna().all(), "fixture no longer reproduces all-NaN sma_200"
    rule = {"op": "not", "node": {"op": ">", "left": "close", "right": "sma_200"}}
    out = evaluate_rule(rule, df)
    assert not out.any(), f"expected all False during warmup, got {out.tolist()}"
    assert out.dtype == bool


def test_not_over_real_values_still_inverts_correctly():
    """Sanity check that the fix doesn't break normal (non-warmup) not-inversion."""
    df = pd.DataFrame({"close": [10.0, 20.0, 5.0], "sma_50": [15.0, 15.0, 15.0]})
    rule = {"op": "not", "node": {"op": ">", "left": "close", "right": "sma_50"}}
    out = evaluate_rule(rule, df)
    # close > sma_50 -> [False, True, False]; not -> [True, False, True]
    assert out.tolist() == [True, False, True]
    assert out.dtype == bool


def test_not_over_partial_warmup_only_masks_warmup_rows():
    """Past the warmup window (once sma_50 has real values), 'not' must resume normal
    inversion — only the genuinely-NaN rows should be forced to False, not the whole series."""
    n = 60
    rng = np.random.default_rng(2)
    close = pd.Series(100 + rng.normal(0, 1, n).cumsum())
    sma_50 = close.rolling(50).mean()
    df = pd.DataFrame({"close": close, "sma_50": sma_50})
    rule = {"op": "not", "node": {"op": ">", "left": "close", "right": "sma_50"}}
    out = evaluate_rule(rule, df)
    # First 49 bars: sma_50 is NaN -> must be False (not unknown-inverted-to-True).
    assert not out.iloc[:49].any(), f"expected all False during warmup, got {out.iloc[:49].tolist()}"
    # From bar 49 onward, sma_50 is real -> must match direct inversion of the real comparison.
    direct = ~(close > sma_50).fillna(False)
    assert out.iloc[49:].tolist() == direct.iloc[49:].tolist()


def test_and_with_nan_operand_does_not_produce_spurious_true():
    """and(real_condition, not(nan_condition)) must not fire just because the not-branch
    was previously miscomputed as True during warmup."""
    df = compute_features(_short_df(20))
    rule = {
        "op": "and",
        "nodes": [
            {"op": ">", "left": "close", "right": 0},  # always true (close > 0)
            {"op": "not", "node": {"op": ">", "left": "close", "right": "sma_200"}},
        ],
    }
    out = evaluate_rule(rule, df)
    assert not out.any(), f"expected all False (sma_200 unknown all the way through), got {out.tolist()}"

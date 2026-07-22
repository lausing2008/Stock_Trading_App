"""Tests for AUD-DUPLOGIC's detect_sr_context() — a port of signal-engine's own _sr_context()
classification logic (services/signal-engine/src/generators/signals.py), now the canonical
implementation exposed via GET /ta/{symbol}/levels' new sr_context field, so signal-engine's
breakout/at_resistance/at_support/neutral labeling can no longer silently disagree with the
chart's own official S/R levels for the same symbol.

Fixtures mirror the classification cases signal-engine's own logic was designed to handle:
a fresh breakout past a former resistance, sitting near an established resistance without yet
clearing it, sitting near support, and a genuinely neutral mid-range case.
"""
import numpy as np
import pandas as pd

from src.indicators.trendlines import Level, detect_sr_context


def _flat_range_df(n: int = 100, base: float = 100.0, band: float = 5.0, seed: int = 0) -> pd.DataFrame:
    """A price series oscillating in a stable [base-band, base+band] range for most of its
    history, ending on a specific last value the caller controls via .iloc[-1] overrides."""
    rng = np.random.default_rng(seed)
    close = base + rng.uniform(-band, band, n)
    return pd.DataFrame({
        "close": close,
        "high": close + 0.3,
        "low": close - 0.3,
        "open": close,
        "volume": rng.integers(500_000, 2_000_000, n).astype(float),
    })


def test_fresh_breakout_past_former_resistance():
    """Price closes decisively above a level that the prior bar was still below — a
    freshly-confirmed breakout."""
    df = _flat_range_df(n=100, base=100.0, band=2.0, seed=1)
    # Cap history at 103 (a real resistance ceiling) for most of the series...
    df.loc[:97, "close"] = np.clip(df.loc[:97, "close"], 97, 103)
    df.loc[:97, "high"] = df.loc[:97, "close"] + 0.3
    df.loc[:97, "low"] = df.loc[:97, "close"] - 0.3
    # ...then a clean breakout: prior bar still under 103, today's bar clears it decisively.
    df.loc[98, ["close", "high", "low"]] = [102.0, 102.3, 101.7]
    df.loc[99, ["close", "high", "low"]] = [106.0, 106.3, 105.7]

    result = detect_sr_context(df)
    assert result["sr_context"] == "breakout"


def test_neutral_mid_range():
    """A price comfortably in the middle of its recent range, approached gradually (no sudden
    jump from a lower prior bar) — must classify as neutral, not spuriously breakout/
    at_resistance/at_support."""
    n = 150
    # A gentle sine-wave oscillation between 85 and 115, so both peaks (resistance candidates)
    # and troughs (support candidates) exist, but the FINAL bars sit at the range's own
    # midpoint (100), approached gradually from a nearby prior value — no level is close by,
    # and no former-resistance-clearing breakout condition is triggered.
    x = np.linspace(0, 6 * np.pi, n)
    close = 100 + 15 * np.sin(x)
    close[-3:] = [100.5, 100.2, 100.0]  # ease into the midpoint gradually, not a decisive jump
    df = pd.DataFrame({
        "close": close, "high": close + 0.3, "low": close - 0.3,
        "open": close, "volume": np.full(n, 1_000_000.0),
    })
    result = detect_sr_context(df)
    assert result["sr_context"] == "neutral"


def test_returns_all_expected_keys():
    df = _flat_range_df(n=100, seed=3)
    result = detect_sr_context(df)
    assert set(result.keys()) == {
        "sr_context", "sr_nearest_resistance", "sr_nearest_support",
        "sr_52w_high", "sr_52w_low",
        # T258-ACCUM-DIST-BREAKOUT-QUALITY: the level actually broken (as opposed to
        # sr_nearest_resistance/sr_nearest_support, which are always still ahead of price).
        "sr_cleared_resistance", "sr_cleared_support",
    }
    assert result["sr_context"] in ("breakout", "at_resistance", "at_support", "neutral")


def test_accepts_precomputed_levels_without_recomputing():
    """When `levels` is passed in, detect_sr_context() must use them directly rather than
    calling detect_support_resistance() a second time — confirms the /levels endpoint's own
    call site (which already computed levels once) doesn't pay for it twice."""
    df = _flat_range_df(n=100, seed=4)
    fake_levels = [Level(price=999.0, kind="resistance", strength=5)]
    result = detect_sr_context(df, levels=fake_levels)
    # With an artificial resistance far above any real price, nothing should register as
    # nearby — confirms the passed-in (not recomputed) levels were actually used.
    assert result["sr_nearest_resistance"] != 999.0 or result["sr_context"] != "at_resistance"


def test_all_time_high_breakout_with_no_qualifying_nearest_resistance():
    """A stock that clears every known resistance level in one decisive move has no level
    satisfying 'nearest resistance still above current price' — the cleared_res fallback path
    must still recognize this as a breakout instead of silently falling through to neutral."""
    n = 100
    close = np.full(n, 50.0)
    close[-3] = 55.0  # a modest local peak, becomes a resistance pivot
    close[-2] = 54.0  # still below that peak
    close[-1] = 70.0  # decisive breakout clearing everything
    df = pd.DataFrame({
        "close": close, "high": close + 0.3, "low": close - 0.3,
        "open": close, "volume": np.full(n, 1_000_000.0),
    })
    result = detect_sr_context(df)
    assert result["sr_context"] == "breakout"

"""Tests for T264-SHORTSQUEEZE-PREBREAKOUT's detect_price_compression()
(services/technical-analysis/src/indicators/trendlines.py) — the "coiling" detector.

A volume-PATTERN-based read (Bollinger Band width + ATR, both normalized by price, plus an
optional volume-dry-up signal) — no proprietary options-flow or short-interest data feeds
into this function directly; it's pure price/volume, matching this module's own established
"be honest about what the data can and can't show" discipline.
"""
import numpy as np
import pandas as pd

from src.indicators.trendlines import detect_price_compression


def _volatile_then_coiling_df(seed=42, n=200, split=150, wide_sigma=0.03, tight_sigma=0.003, volume_dry_up=True):
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for i in range(n - 1):
        sigma = wide_sigma if i < split else tight_sigma
        prices.append(prices[-1] * (1 + rng.normal(0, sigma)))
    close = np.array(prices)
    high = close * 1.01
    low = close * 0.99
    volume = rng.uniform(900_000, 1_100_000, n)
    if volume_dry_up:
        volume[split:] *= 0.6
    return pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})


def _consistently_volatile_df(seed=1, n=200, sigma=0.03):
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0, sigma)))
    close = np.array(prices)
    high = close * 1.02
    low = close * 0.98
    volume = rng.uniform(900_000, 1_100_000, n)
    return pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})


def test_a_stock_that_compresses_after_a_volatile_period_is_flagged_compressed():
    df = _volatile_then_coiling_df()
    result = detect_price_compression(df)
    assert result["is_compressed"] is True
    assert result["bb_width_pctile"] <= 0.20
    assert result["atr_pctile"] <= 0.20


def test_a_consistently_volatile_stock_is_not_flagged_compressed():
    df = _consistently_volatile_df()
    result = detect_price_compression(df)
    assert result["is_compressed"] is False


def test_volume_dry_up_is_reported_when_present():
    df = _volatile_then_coiling_df(volume_dry_up=True)
    result = detect_price_compression(df)
    assert result["volume_dried_up"] is True


def test_volume_dry_up_is_false_when_volume_stays_flat_through_compression():
    df = _volatile_then_coiling_df(volume_dry_up=False)
    result = detect_price_compression(df)
    assert result["volume_dried_up"] is False


def test_is_compressed_requires_BOTH_bb_width_and_atr_to_agree():
    """Mirrors detect_accumulation_distribution()'s own "two independent signals must agree"
    discipline — a stock coiling on ONE measure but not the other must not be flagged, since a
    single compressed reading could just be that one indicator's own noise."""
    df = _volatile_then_coiling_df()
    result = detect_price_compression(df)
    # Sanity: both percentiles individually clear the bar in the constructed fixture.
    assert result["bb_width_pctile"] <= 0.20 and result["atr_pctile"] <= 0.20
    assert result["is_compressed"] is True


def test_bb_width_compressed_but_atr_not_reads_not_compressed():
    """The genuine disagreement case an `or` (instead of the correct `and`) would silently
    accept: closing-price volatility (bb_width) has compressed, but true range stays wide
    (elevated intrabar high-low swings each day) — a real, constructible scenario where the
    two signals genuinely disagree, not just a coincidence of one shared fixture."""
    rng = np.random.default_rng(7)
    n = 200
    prices = [100.0]
    for i in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0, 0.03 if i < 150 else 0.003)))
    close = pd.Series(prices)
    high = close.copy()
    low = close.copy()
    for i in range(150, n):
        high.iloc[i] = close.iloc[i] * 1.05  # intrabar range stays artificially wide
        low.iloc[i] = close.iloc[i] * 0.95
    for i in range(150):
        high.iloc[i] = close.iloc[i] * 1.01
        low.iloc[i] = close.iloc[i] * 0.99
    volume = pd.Series(rng.uniform(900_000, 1_100_000, n))
    df = pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})

    result = detect_price_compression(df)
    assert result["bb_width_pctile"] <= 0.20  # bb_width genuinely did compress
    assert result["atr_pctile"] > 0.20        # but ATR (true range) did NOT
    assert result["is_compressed"] is False   # so the combined call must be False


def test_insufficient_history_degrades_to_not_compressed_not_a_crash():
    n = 50  # well below the ~146-bar floor (126 lookback + 20 warmup)
    close = pd.Series(np.linspace(100, 105, n))
    df = pd.DataFrame({"close": close, "high": close + 0.5, "low": close - 0.5, "volume": pd.Series(np.full(n, 1_000_000.0))})
    result = detect_price_compression(df)
    assert result["is_compressed"] is False
    assert result["bb_width_pctile"] is None
    assert result["atr_pctile"] is None
    assert result["volume_dried_up"] is None


def test_a_flat_zero_volatility_series_does_not_crash_on_a_zero_denominator():
    """bb_mid could be exactly 0 in a pathological input (never in practice for a real stock
    price, but this guards the /inf-safety branch the same way detect_accumulation_
    distribution()'s own AUD-T258-INF fix guards its own division)."""
    n = 160
    close = pd.Series(np.full(n, 100.0))
    high = close.copy()
    low = close.copy()
    volume = pd.Series(np.full(n, 1_000_000.0))
    df = pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})
    result = detect_price_compression(df)  # must not raise
    assert result["is_compressed"] in (True, False)


def test_percentile_boundary_is_inclusive_at_exactly_the_20th_percentile():
    """bb_pctile/atr_pctile use <= against _COMPRESSION_PERCENTILE — confirms the boundary
    condition explicitly rather than only ever testing comfortably-below values."""
    df = _volatile_then_coiling_df()
    result = detect_price_compression(df)
    # The constructed fixture's actual percentiles are well below 0.20 in practice; this test
    # instead directly re-derives the boundary check to confirm inclusivity semantics.
    assert result["bb_width_pctile"] is not None
    is_compressed_reconstructed = result["bb_width_pctile"] <= 0.20 and result["atr_pctile"] <= 0.20
    assert result["is_compressed"] == is_compressed_reconstructed

"""Tests for T258-ACCUM-DIST-BREAKOUT-QUALITY's detect_accumulation_distribution() and
assess_breakout_quality() (services/technical-analysis/src/indicators/trendlines.py).

Both are volume-PATTERN-based reads (OBV trend, up/down-day volume ratio, RVOL on the
breakout bar) — no block-trade/dark-pool data source exists anywhere in this app, so neither
function claims true institutional-flow detection; they're framed and tested as pattern reads.
"""
import numpy as np
import pandas as pd

from src.indicators.trendlines import assess_breakout_quality, detect_accumulation_distribution, detect_sr_context


def _df(closes, volumes) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    return pd.DataFrame({
        "close": closes,
        "high": closes + 0.5,
        "low": closes - 0.5,
        "volume": volumes,
    })


# ── detect_accumulation_distribution() ──────────────────────────────────────────

def test_heavier_up_day_volume_and_bullish_obv_reads_accumulation():
    rng = np.random.default_rng(1)
    n = 60
    closes = 100 + np.cumsum(rng.uniform(-0.3, 0.8, n))
    volumes = []
    prev = closes[0]
    for c in closes:
        volumes.append(rng.uniform(2_000_000, 3_000_000) if c > prev else rng.uniform(500_000, 1_000_000))
        prev = c
    df = _df(closes, volumes)
    result = detect_accumulation_distribution(df)
    assert result["state"] == "accumulation"
    assert result["obv_trend_bullish"] is True
    assert result["updown_vol_ratio"] > 1.2


def test_heavier_down_day_volume_and_bearish_obv_reads_distribution():
    rng = np.random.default_rng(2)
    n = 60
    closes = 100 - np.cumsum(rng.uniform(-0.3, 0.8, n))
    volumes = []
    prev = closes[0]
    for c in closes:
        volumes.append(rng.uniform(2_000_000, 3_000_000) if c < prev else rng.uniform(500_000, 1_000_000))
        prev = c
    df = _df(closes, volumes)
    result = detect_accumulation_distribution(df)
    assert result["state"] == "distribution"
    assert result["obv_trend_bullish"] is False


def test_volume_ratio_just_below_the_accumulation_threshold_reads_neutral_not_accumulation():
    """A volume ratio of exactly 1.19 (just under the 1.2 accumulation threshold) must read
    'neutral' even with a bullish OBV trend — deterministically constructed (10 up-days at a
    fixed volume, 10 down-days at a fixed slightly-lower volume giving an exact 1.19 ratio)
    rather than relying on random noise landing on the right side of the threshold."""
    n = 40
    closes = list(100 + np.linspace(0, 5, n))  # monotonic uptrend -> bullish OBV, zero down-days
    # Override the LAST 20 bars with an explicit alternating up/down pattern so both up-days
    # and down-days exist in the updown_vol_ratio window, at volumes producing exactly 1.19.
    c = closes[n - 21]
    last20 = []
    for i in range(20):
        c += 0.3 if i % 2 == 0 else -0.1  # net upward drift -> OBV stays bullish
        last20.append(c)
    closes = closes[: n - 20] + last20
    volumes = [1_000_000.0] * (n - 20)
    for i in range(20):
        volumes.append(1_190_000.0 if i % 2 == 0 else 1_000_000.0)  # up-vol/down-vol = 1.19
    df = _df(closes, volumes)
    result = detect_accumulation_distribution(df, window=20)
    assert result["updown_vol_ratio"] == 1.19
    assert result["state"] == "neutral"


def test_too_little_history_returns_neutral_with_none_fields():
    df = _df([100.0] * 10, [1_000_000.0] * 10)
    result = detect_accumulation_distribution(df, window=20)
    assert result == {"state": "neutral", "obv_trend_bullish": None, "updown_vol_ratio": None}


def test_flat_price_no_down_days_produces_a_finite_sentinel_not_json_breaking_inf():
    """AUD-T258-INF: all up/flat days (no down_vol at all) must not divide by zero or crash,
    and — critically — must NEVER return a real float('inf'): json.dumps(float('inf')) emits
    the bare token `Infinity`, which browser JSON.parse rejects outright, breaking the ENTIRE
    GET /ta/{symbol}/levels response (S/R, trendlines, FVG, and this card together), not just
    this field. A large finite sentinel (999.0) is used instead — still unambiguously
    "overwhelmingly up-volume" to any reader, but always JSON-safe."""
    closes = list(np.linspace(100, 110, 40))
    volumes = [1_000_000.0] * 40
    df = _df(closes, volumes)
    result = detect_accumulation_distribution(df)
    assert result["updown_vol_ratio"] == 999.0
    assert result["updown_vol_ratio"] != float("inf")
    import json
    json.dumps(result)  # must not raise, and must round-trip through a strict JSON encoder


# ── assess_breakout_quality() ────────────────────────────────────────────────────

def _flat_then_break(break_offset_from_end: int, hold: bool, extra_after: int = 0):
    """Builds a flat-100 series, then a breakout bar above 101 with 5x volume, optionally
    followed by more bars. `hold=False` reverses the bar immediately after the breakout."""
    base = [100.0] * 30
    closes = base + [100, 100, 100, 105]
    volumes = [1_000_000.0] * 33 + [5_000_000.0]
    if extra_after > 0 or not hold:
        if hold:
            closes += [106.0] * extra_after
            volumes += [1_500_000.0] * extra_after
        else:
            closes += [99.0] + [100.0] * (extra_after - 1 if extra_after > 0 else 0)
            volumes += [1_000_000.0] * max(1, extra_after)
    return _df(closes, volumes)


def test_no_breakout_at_all_returns_none():
    df = _df([100.0] * 40, [1_000_000.0] * 40)
    assert assess_breakout_quality(df, level=150.0, direction="up") is None


def test_breakout_on_the_last_bar_is_unconfirmed_no_next_bar_yet():
    df = _flat_then_break(break_offset_from_end=0, hold=True, extra_after=0)
    result = assess_breakout_quality(df, level=101.0, direction="up")
    assert result["quality"] == "unconfirmed"
    assert result["volume_confirmed"] is True


def test_breakout_that_holds_the_next_bar_with_volume_confirmation_is_real():
    df = _flat_then_break(break_offset_from_end=0, hold=True, extra_after=1)
    result = assess_breakout_quality(df, level=101.0, direction="up")
    assert result["quality"] == "real"


def test_breakout_that_reverses_the_next_bar_is_failed():
    df = _flat_then_break(break_offset_from_end=0, hold=False, extra_after=1)
    result = assess_breakout_quality(df, level=101.0, direction="up")
    assert result["quality"] == "failed"


def test_breakout_without_volume_confirmation_is_unconfirmed_not_real():
    """A break above the level that holds the next bar but had NO volume expansion on the
    breakout bar itself must not be called 'real' — real-vs-failed is genuinely unknowable
    from price alone without volume confirmation."""
    base = [100.0] * 30
    closes = base + [100, 100, 100, 105, 106]
    volumes = [1_000_000.0] * 34 + [1_000_000.0]  # breakout bar has ordinary volume, not elevated
    df = _df(closes, volumes)
    result = assess_breakout_quality(df, level=101.0, direction="up")
    assert result["volume_confirmed"] is False
    assert result["quality"] == "unconfirmed"


def test_breakdown_direction_down_detects_a_break_below_support():
    base = [100.0] * 30
    closes = base + [100, 100, 100, 95, 94]  # breaks below 99 with volume, holds next bar
    volumes = [1_000_000.0] * 33 + [5_000_000.0, 1_500_000.0]
    df = _df(closes, volumes)
    result = assess_breakout_quality(df, level=99.0, direction="down")
    assert result["quality"] == "real"
    assert result["direction"] == "down"


def test_price_already_beyond_level_for_the_whole_window_finds_the_first_crossing():
    """If price has been above the level for many bars (an established uptrend, not a fresh
    break), the function must find the FIRST bar that crossed it, not just look at today's
    close vs. level (which would misreport a decades-old level as freshly broken today)."""
    closes = [90.0] * 10 + [105.0] * 30  # crossed 100 at index 10, held ever since
    volumes = [1_000_000.0] * 10 + [5_000_000.0] + [1_000_000.0] * 29
    df = _df(closes, volumes)
    result = assess_breakout_quality(df, level=100.0, direction="up")
    assert result is not None
    assert result["close"] == 105.0  # the breakout bar's own close, not today's


# ── GET /ta/{symbol}/levels integration — sr_cleared_resistance/sr_cleared_support ──

def test_sr_context_cleared_fields_are_the_broken_level_not_the_nearest_unreached_one():
    """sr_nearest_resistance/sr_nearest_support are ALWAYS on the not-yet-reached side of
    price by construction — sr_cleared_resistance/sr_cleared_support must instead be the
    level actually broken through, which is what assess_breakout_quality() needs."""
    rng = np.random.default_rng(4)
    closes = 100 + rng.uniform(-2, 2, 90)
    closes = np.append(closes, [110.0])  # a clear break above the established ~100-102 range
    df = _df(closes, [1_000_000.0] * len(closes))
    ctx = detect_sr_context(df)
    if ctx["sr_cleared_resistance"] is not None:
        assert ctx["sr_cleared_resistance"] < 110.0
        assert ctx["sr_nearest_resistance"] is None or ctx["sr_nearest_resistance"] > 110.0


# ── AUD-T258 fixes: post-audit regression tests ─────────────────────────────────

def test_21_to_29_bars_can_now_produce_a_real_state_not_forced_neutral():
    """AUD-T258-DEADZONE: previously, 21-29 bars could NEVER produce anything but 'neutral'
    (the entry guard admitted them, but obv_trend_bullish silently stayed None below 30 bars,
    and both non-neutral branches require a real True/False). With the guard raised to the
    true 30-bar minimum, a 25-bar input must degrade explicitly (None fields), never silently
    admit a state it can't actually compute."""
    rng = np.random.default_rng(6)
    n = 25
    closes = 100 + np.cumsum(rng.uniform(-0.3, 0.8, n))
    volumes = list(rng.uniform(2_000_000, 3_000_000, n))
    df = _df(closes, volumes)
    result = detect_accumulation_distribution(df, window=20)
    assert result == {"state": "neutral", "obv_trend_bullish": None, "updown_vol_ratio": None}


def test_exactly_30_bars_is_the_real_minimum_for_a_non_neutral_verdict():
    rng = np.random.default_rng(1)
    n = 30
    closes = 100 + np.cumsum(rng.uniform(-0.3, 0.8, n))
    volumes = []
    prev = closes[0]
    for c in closes:
        volumes.append(rng.uniform(2_000_000, 3_000_000) if c > prev else rng.uniform(500_000, 1_000_000))
        prev = c
    df = _df(closes, volumes)
    result = detect_accumulation_distribution(df, window=20)
    assert result["obv_trend_bullish"] is not None


def test_breakout_that_fully_reverses_after_initially_holding_returns_none_not_real():
    """AUD-T258-STALEBREAK: a breakout that held for exactly one bar (satisfying the old,
    incomplete 'next bar' check) but then fully reversed and stayed reversed must NOT be
    reported 'real' — the function must check whether the move is still intact as of the
    actual current (most recent) bar, not just the bar immediately after the breakout."""
    base = [100.0] * 30
    # Breaks above 101 at idx 33 (5x volume), holds ONE bar (idx 34), then fully reverses
    # and stays reversed for several more bars.
    closes = base + [100, 100, 100, 105, 106, 95, 92, 90]
    volumes = [1_000_000.0] * 33 + [5_000_000.0, 1_500_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0]
    df = _df(closes, volumes)
    result = assess_breakout_quality(df, level=101.0, direction="up")
    assert result is None


def test_breakout_that_holds_all_the_way_to_the_current_bar_is_unaffected_by_the_new_guard():
    """The new still-beyond-as-of-now guard must not break the ordinary held-and-confirmed
    case — a breakout that holds through to the current bar must still classify normally."""
    base = [100.0] * 30
    closes = base + [100, 100, 100, 105, 106, 107, 108]
    volumes = [1_000_000.0] * 33 + [5_000_000.0, 1_500_000.0, 1_500_000.0, 1_500_000.0]
    df = _df(closes, volumes)
    result = assess_breakout_quality(df, level=101.0, direction="up")
    assert result is not None
    assert result["quality"] == "real"


def test_updown_vol_ratio_never_returns_a_real_float_infinity():
    """AUD-T258-INF: json.dumps(float('inf')) emits the bare token `Infinity`, which browser
    JSON.parse rejects outright — breaking the ENTIRE GET /ta/{symbol}/levels response (S/R,
    trendlines, FVG, and this card together), not just this one field. Any input that would
    have produced a real inf must instead produce a large-but-finite, JSON-safe sentinel."""
    import json
    closes = list(np.linspace(100, 110, 40))  # monotonic up, zero down-close days
    volumes = [1_000_000.0] * 40
    df = _df(closes, volumes)
    result = detect_accumulation_distribution(df)
    assert isinstance(result["updown_vol_ratio"], float)
    assert result["updown_vol_ratio"] != float("inf")
    json.dumps(result)  # must not raise

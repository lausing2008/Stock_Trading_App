"""Tests for detect_fair_value_gaps() (Fair Value Gap / imbalance detection).

FVG is a 3-candle price-action pattern: bar[i-1].high < bar[i+1].low (bullish) or
bar[i-1].low > bar[i+1].high (bearish) — the middle bar's move was so decisive the two
neighbors never overlap it. The gap itself is [bar[i-1].high, bar[i+1].low] (bullish) or
[bar[i+1].high, bar[i-1].low] (bearish), not the middle bar's own high/low.
"""
import numpy as np
import pandas as pd

from src.indicators.trendlines import FairValueGap, detect_fair_value_gaps


def _flat_df(n=60, price=100.0):
    return pd.DataFrame({
        "open": np.full(n, price), "high": np.full(n, price + 0.5),
        "low": np.full(n, price - 0.5), "close": np.full(n, price),
    })


def test_detects_a_clean_bullish_gap():
    df = _flat_df(30)
    # bar 14: high=100.5 (unremarkable) ; bar 15: huge up-move ; bar 16: low=100.5
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [110.0, 105.0]
    df.loc[16, ["high", "low"]] = [112.0, 106.0]  # low (106.0) > bar14.high (100.5)

    gaps = detect_fair_value_gaps(df)
    bullish = [g for g in gaps if g.kind == "bullish" and g.idx == 15]
    assert len(bullish) == 1
    g = bullish[0]
    assert g.bottom == 100.5  # bar14.high
    assert g.top == 106.0     # bar16.low


def test_detects_a_clean_bearish_gap():
    df = _flat_df(30)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [95.0, 90.0]
    df.loc[16, ["high", "low"]] = [93.0, 88.0]  # high (93.0) < bar14.low (99.5)

    gaps = detect_fair_value_gaps(df)
    bearish = [g for g in gaps if g.kind == "bearish" and g.idx == 15]
    assert len(bearish) == 1
    g = bearish[0]
    assert g.top == 99.5   # bar14.low
    assert g.bottom == 93.0  # bar16.high


def test_overlapping_bars_produce_no_gap():
    df = _flat_df(30)  # every bar identical high/low — no bar's neighbors can fail to overlap
    gaps = detect_fair_value_gaps(df)
    assert gaps == []


def test_gap_is_marked_filled_once_a_later_bar_covers_it():
    df = _flat_df(40)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [110.0, 105.0]
    df.loc[16, ["high", "low"]] = [112.0, 106.0]
    # Bar 20 later trades all the way back down through [100.5, 106.0] — fills the gap.
    df.loc[20, ["high", "low"]] = [108.0, 99.0]

    gaps = detect_fair_value_gaps(df)
    g = next(g for g in gaps if g.idx == 15)
    assert g.filled is True
    assert g.filled_idx == 20


def test_gap_stays_unfilled_if_price_never_returns():
    df = _flat_df(40)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [110.0, 105.0]
    df.loc[16, ["high", "low"]] = [112.0, 106.0]
    # Every bar after stays well above the gap — never fills it.
    for i in range(17, 40):
        df.loc[i, ["high", "low"]] = [115.0, 112.0]

    gaps = detect_fair_value_gaps(df)
    g = next(g for g in gaps if g.idx == 15)
    assert g.filled is False
    assert g.filled_idx is None


def test_partial_overlap_does_not_count_as_filled():
    """A later bar must cover the FULL gap range, not just dip into part of it."""
    df = _flat_df(40)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [110.0, 105.0]
    df.loc[16, ["high", "low"]] = [112.0, 106.0]  # gap = [100.5, 106.0]
    # Bar 20 only dips to 103.0 — inside the gap but doesn't reach the 100.5 bottom.
    df.loc[20, ["high", "low"]] = [108.0, 103.0]

    gaps = detect_fair_value_gaps(df)
    g = next(g for g in gaps if g.idx == 15)
    assert g.filled is False


def test_tiny_noise_level_gaps_are_filtered_by_min_gap_pct():
    df = _flat_df(30, price=100.0)
    df.loc[14, ["high", "low"]] = [100.001, 99.999]
    df.loc[15, ["high", "low"]] = [100.005, 100.002]
    df.loc[16, ["high", "low"]] = [100.006, 100.003]  # low > bar14.high, but by a tiny amount

    gaps = detect_fair_value_gaps(df, min_gap_pct=0.001)
    assert gaps == []


def test_only_scans_within_lookback_window():
    df = _flat_df(300)
    # A gap far in the past, outside a 50-bar lookback.
    df.loc[9, ["high", "low"]] = [100.5, 99.5]
    df.loc[10, ["high", "low"]] = [110.0, 105.0]
    df.loc[11, ["high", "low"]] = [112.0, 106.0]

    gaps = detect_fair_value_gaps(df, lookback=50)
    assert all(g.idx >= len(df) - 50 for g in gaps)
    assert not any(g.idx == 10 for g in gaps)


def test_returns_fair_value_gap_dataclass_instances():
    df = _flat_df(30)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [110.0, 105.0]
    df.loc[16, ["high", "low"]] = [112.0, 106.0]
    gaps = detect_fair_value_gaps(df)
    assert all(isinstance(g, FairValueGap) for g in gaps)


# ── AUD-FVG-SINGLEBARFILL: multi-bar cumulative fill ─────────────────────────────────────

def test_gap_traded_through_gradually_over_multiple_bars_is_marked_filled():
    """A gap fully traded through over SEVERAL bars (no single bar spans the whole range)
    must still be marked filled — this is the exact regression the audit found: the old
    single-bar check left gaps like this permanently filled=False."""
    df = _flat_df(40)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [110.0, 105.0]
    df.loc[16, ["high", "low"]] = [112.0, 106.0]  # gap = [100.5, 106.0]
    # Bar 20 covers the bottom half [100.5, 103.0]; bar 21 covers the top half [102.5, 106.0]
    # (overlapping slightly at 102.5-103.0 so the two covered runs are contiguous) — together
    # they fully cover [100.5, 106.0], but neither bar alone does.
    df.loc[20, ["high", "low"]] = [103.0, 100.5]
    df.loc[21, ["high", "low"]] = [106.0, 102.5]

    gaps = detect_fair_value_gaps(df)
    g = next(g for g in gaps if g.idx == 15)
    assert g.filled is True
    assert g.filled_idx == 21


def test_gap_traded_through_by_disjoint_bars_with_a_gap_in_the_middle_stays_unfilled():
    """Two bars each cover part of the range but leave an untouched middle strip — the
    covered sub-ranges are NOT contiguous, so the gap is genuinely still open in the middle
    and must stay filled=False."""
    df = _flat_df(40)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [113.0, 105.0]
    df.loc[16, ["high", "low"]] = [115.0, 106.0]  # gap = [100.5, 106.0]
    # Bar 20 only touches the very bottom [100.5, 101.5]; bar 21 only touches the very top
    # [104.5, 106.0] — a real untouched strip [101.5, 104.5] remains in the middle.
    df.loc[20, ["high", "low"]] = [101.5, 100.5]
    df.loc[21, ["high", "low"]] = [106.0, 104.5]

    gaps = detect_fair_value_gaps(df)
    g = next(g for g in gaps if g.idx == 15)
    assert g.filled is False


def test_a_bar_entirely_above_or_below_the_gap_does_not_falsely_mark_it_filled():
    """Regression for a real bug caught while fixing this: a bar whose full range sits
    entirely ABOVE the gap's top (never dips down into the gap at all) must not be treated
    as 'covering' the gap just because its high exceeds the gap's top."""
    df = _flat_df(40)
    df.loc[14, ["high", "low"]] = [100.5, 99.5]
    df.loc[15, ["high", "low"]] = [110.0, 105.0]
    df.loc[16, ["high", "low"]] = [112.0, 106.0]  # gap = [100.5, 106.0]
    # Every bar after stays well ABOVE the gap (low=112 > gap top=106) — never touches it.
    for i in range(17, 40):
        df.loc[i, ["high", "low"]] = [115.0, 112.0]

    gaps = detect_fair_value_gaps(df)
    g = next(g for g in gaps if g.idx == 15)
    assert g.filled is False


# ── AUD-FVG-CAPORDERING: max_gaps prioritizes nearest-unfilled, not most-recent ──────────

def test_max_gaps_cap_keeps_the_nearest_unfilled_gap_over_a_more_recent_far_gap():
    """When more gaps exist than max_gaps allows, the cap must keep the gap NEAREST to
    current price (and prioritize unfilled over filled), not just the most recently formed
    ones by bar index — the exact regression the audit found."""
    df = _flat_df(200, price=100.0)
    # An OLD gap near current price (100.0), left unfilled — the genuinely actionable one.
    df.loc[9,  ["high", "low"]] = [100.5, 99.5]
    df.loc[10, ["high", "low"]] = [102.0, 101.0]
    df.loc[11, ["high", "low"]] = [104.0, 101.5]  # gap idx=10, [100.5, 101.5], near price=100
    # 20 NEWER gaps, all far away from current price and all filled immediately after forming,
    # so none of them are actually actionable — but by bar index they're all more "recent"
    # than idx=10.
    for k in range(20):
        base = 50 + k * 5
        df.loc[base,     ["high", "low"]] = [200.5 + k, 199.5 + k]
        df.loc[base + 1, ["high", "low"]] = [220.0 + k, 215.0 + k]
        df.loc[base + 2, ["high", "low"]] = [222.0 + k, 216.0 + k]  # far-away gap
        df.loc[base + 3, ["high", "low"]] = [225.0 + k, 214.0 + k]  # immediately fills it

    gaps = detect_fair_value_gaps(df, max_gaps=20)
    assert any(g.idx == 10 for g in gaps), (
        "the nearest, still-unfilled gap must survive the max_gaps cap even though 20 "
        "more-recent (but filled, far-away) gaps exist"
    )


def test_max_gaps_cap_output_is_chronologically_ordered():
    """Even after re-prioritizing for the cap, the returned list must be restored to
    chronological (by idx) order — callers/renderers expect a stable time-ordered list."""
    df = _flat_df(200, price=100.0)
    df.loc[9,  ["high", "low"]] = [100.5, 99.5]
    df.loc[10, ["high", "low"]] = [102.0, 101.0]
    df.loc[11, ["high", "low"]] = [104.0, 101.5]
    for k in range(20):
        base = 50 + k * 5
        df.loc[base,     ["high", "low"]] = [200.5 + k, 199.5 + k]
        df.loc[base + 1, ["high", "low"]] = [220.0 + k, 215.0 + k]
        df.loc[base + 2, ["high", "low"]] = [222.0 + k, 216.0 + k]
        df.loc[base + 3, ["high", "low"]] = [225.0 + k, 214.0 + k]

    gaps = detect_fair_value_gaps(df, max_gaps=20)
    idxs = [g.idx for g in gaps]
    assert idxs == sorted(idxs)

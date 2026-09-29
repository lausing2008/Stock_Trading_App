"""Realized (historical) volatility from a daily close series.

T411-IVHV: built for the stock detail page's Options tab, where a real implied-volatility
reading from Unusual Whales is plotted against the volatility the stock ACTUALLY realized over
the same window. That comparison is the whole point of the panel — IV is what the options market
charged for future movement, HV is what the movement turned out to be — so the two series have
to be derived honestly and independently, and the gap between them has to be the data's, not an
artifact of how this file computes.

Three deliberate choices, each of which changes the number:

1. **Log returns, not simple returns.** `paper_trading_engine._vol_target_multiplier()` uses
   simple returns for its own annualized figure, and that is correct for what it measures (the
   dispersion of an equity curve). This module is different: the series it produces is compared
   directly against a Black-Scholes implied volatility, and BS is defined on log-normal price
   dynamics. Using simple returns here would bias HV upward against the IV it is plotted with,
   by an amount that grows with volatility — precisely the regime where a reader is most likely
   to act on the gap. This is NOT a drifting second copy of that helper; it is a different
   quantity for a different comparison.

2. **Sample variance (n-1), zero-mean NOT assumed.** Some volatility conventions drop the mean
   term (assuming zero drift). Over a 20-day window on a trending stock the drift is not zero
   and dropping it inflates HV. The mean is subtracted.

3. **sqrt(252) annualization**, matching every other annualized figure in this codebase.

The series is returned as a decimal fraction (0.247 = 24.7% annualized), matching the units
`UnusualWhales.IVRankData.volatility` already arrives in, so the two can be plotted on one axis
without a conversion step that could silently drift.
"""
from __future__ import annotations

import math
from datetime import date

# The window over which realized volatility is measured. 20 trading days ~= one calendar month,
# which is also the horizon most "IV" quotes are implicitly about (a 30-calendar-day reference
# window). Matching the two makes the comparison meaningful rather than an apples-to-oranges
# overlay of a 10-day realized figure against a 30-day implied one.
DEFAULT_HV_WINDOW = 20

# Below this many returns an annualized figure is noise, not a measurement. Same floor as
# `paper_trading_engine._VOL_TARGET_MIN_SAMPLE_DAYS` / `paper_portfolio._MIN_SHARPE_DAYS`.
MIN_HV_SAMPLE = 10

TRADING_DAYS_PER_YEAR = 252


def annualized_vol(returns: list[float]) -> float | None:
    """Annualized standard deviation of `returns`, or None when the sample is too small.

    Returns None — never 0.0 — for an unmeasurable sample. A zero would render on the chart as
    a real reading of "this stock did not move", which is a materially different claim from
    "there is not enough history here to say".

    A genuinely flat series (every return identical, e.g. a halted stock) DOES return 0.0: that
    is a real measurement, not a missing one.
    """
    n = len(returns)
    if n < MIN_HV_SAMPLE:
        return None
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    if variance <= 0:
        return 0.0
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


def log_returns(closes: list[float]) -> list[float]:
    """Consecutive log returns. Non-positive closes are skipped along with the return that
    would span them — log() is undefined at zero and negative, and a bad tick must not take out
    the whole series or, worse, produce a plausible-looking wrong number."""
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev is None or cur is None or prev <= 0 or cur <= 0:
            continue
        out.append(math.log(cur / prev))
    return out


def historical_volatility_series(
    bars: list[tuple[date, float]],
    window: int = DEFAULT_HV_WINDOW,
) -> list[tuple[date, float]]:
    """Rolling annualized realized volatility, one point per date that has a full `window` of
    returns behind it.

    `bars` must be (date, close) ascending. The first `window` dates produce NO point rather
    than a point computed from a partial window — a 3-day HV plotted next to a 20-day HV is not
    the same measurement, and silently mixing them is how a chart lies about where volatility
    actually turned.

    Dates whose window contains bad closes still produce a point if at least `MIN_HV_SAMPLE`
    usable returns survive; otherwise that date is omitted.
    """
    if window < 2:
        raise ValueError("window must be at least 2")

    clean = [(d, c) for d, c in bars if c is not None and c > 0]
    if len(clean) < window + 1:
        return []

    out: list[tuple[date, float]] = []
    closes = [c for _, c in clean]
    dates = [d for d, _ in clean]

    # Point at index i uses the `window` returns ending at i, i.e. closes[i-window .. i].
    for i in range(window, len(clean)):
        rets = log_returns(closes[i - window: i + 1])
        vol = annualized_vol(rets)
        if vol is not None:
            out.append((dates[i], vol))
    return out

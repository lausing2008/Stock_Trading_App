"""Black-Scholes call pricing and greeks for the Call Finder (design:
docs/features/2026-11-10-options-tab-call-finder-design.md).

Pure functions, no I/O. Used only to (a) estimate greeks the provider did not send (it sends
greeks only for contracts that traded that session) and (b) re-price a contract under a stated
scenario. Assumptions, printed by the caller beside every figure: European exercise, no
dividends, a flat risk-free rate, and the contract's own IV held constant. A real fall in IV
(for example after earnings) makes a real outcome worse than the model shows.
"""
from __future__ import annotations

import math

RISK_FREE_RATE = 0.04   # stated, flat; a sensitivity, not a forecast


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _valid(spot, strike, years, iv) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (spot, strike, years, iv)) and spot > 0 and strike > 0 and iv > 0


def call_price(spot: float, strike: float, years: float, iv: float, rate: float = RISK_FREE_RATE) -> float | None:
    """Model value of a call; intrinsic value at or after expiry. None on invalid input."""
    if not _valid(spot, strike, max(years, 1e-9), iv):
        return None
    if years <= 0:
        return max(spot - strike, 0.0)
    sd = iv * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / sd
    return spot * _n(d1) - strike * math.exp(-rate * years) * _n(d1 - sd)


def call_greeks(spot: float, strike: float, years: float, iv: float, rate: float = RISK_FREE_RATE) -> dict | None:
    """{delta, gamma, theta_per_day, vega_per_point} for a call; None on invalid input or at expiry.

    theta is per calendar day in price units; vega is the price change for a 1-point (0.01) IV move.
    """
    if not _valid(spot, strike, years, iv) or years <= 0:
        return None
    sd = iv * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / sd
    d2 = d1 - sd
    theta_year = -(spot * _pdf(d1) * iv) / (2 * math.sqrt(years)) - rate * strike * math.exp(-rate * years) * _n(d2)
    return {"delta": _n(d1), "gamma": _pdf(d1) / (spot * sd),
            "theta_per_day": theta_year / 365.0, "vega_per_point": spot * _pdf(d1) * math.sqrt(years) / 100.0}


def prob_above(spot: float, level: float, years: float, iv: float, rate: float = RISK_FREE_RATE) -> float | None:
    """Model (risk-neutral) probability the price ends above `level` at `years`.

    This is the market's pricing under the model, not a forecast of what will happen.
    """
    if not _valid(spot, level, years, iv) or years <= 0:
        return None
    sd = iv * math.sqrt(years)
    return _n((math.log(spot / level) + (rate - 0.5 * iv * iv) * years) / sd)

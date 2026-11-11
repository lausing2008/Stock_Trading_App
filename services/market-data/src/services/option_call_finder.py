"""Call Finder: a near-term call and a LEAPS call for a stated view, with reasons.

Design: docs/features/2026-11-10-options-tab-call-finder-design.md. Pure: rows and inputs in,
picks out; no database, network or clock.

WHAT "BEST" MEANS HERE. Option prices are fair on average, so no contract is most profitable
unconditionally, and this project's own replay (October 31) found no selection rule that picked
better contracts. The finder therefore ranks against the reader's view, a target price by a date:

  * STRIKE: the eligible contract with the highest return if the stock makes HALF of the target
    move by the end of the holding period. Maximising return at the full target always picks the
    cheapest far out-of-the-money call; averaging symmetric scenarios always picks the deepest
    in-the-money one (fair pricing showing through). The half-move rule has a real optimum inside
    the chain. The full-target maximum is returned beside it as the aggressive alternative.
  * EXPIRY: a time rule, not a return, because any scenario that assumes the move arrives on
    time prefers the shortest expiry.

Every figure uses the same stated model (option_pricing): the contract's own IV held constant,
entry at the ask, exit at the model mid less half the contract's current spread.
"""
from __future__ import annotations

import math
from datetime import date

from . import option_pricing as bs

METHOD_VERSION = "call_finder_v1"

NEAR = {"name": "near_term", "min_dte_floor": 30, "max_dte": 90, "hold_multiple": 2,
        "max_spread_pct": 10.0, "fallback_spread_pct": 20.0, "min_oi": 100,
        "min_delta": 0.40, "max_delta": 0.85}
LEAPS = {"name": "leaps", "min_dte": 365, "fallback_min_dte": 270,
         "max_spread_pct": 15.0, "fallback_spread_pct": 20.0, "min_oi": 50,
         "min_delta": 0.60, "max_delta": 0.90}
LEAPS_HOLD_DAYS = 180
_SAME_WEEK_DAYS = 7
_MAX_IV_PCT = 300.0


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _dte(expiry: str, today: date) -> int | None:
    try:
        return (date.fromisoformat(str(expiry)[:10]) - today).days
    except (TypeError, ValueError):
        return None


def _call_oi(rows: list[dict], expiry: str) -> int:
    return sum(int(r["oi"]) for r in rows if r.get("expiry") == expiry and _num(r.get("oi")) is not None)


def pick_near_expiry(rows: list[dict], today: date, hold_days: int) -> tuple[str | None, str]:
    """The nearest expiry with at least max(2 x hold, 30) days and at most 90; between two such
    expiries within a week of each other, the one with more call open interest."""
    floor = max(NEAR["hold_multiple"] * hold_days, NEAR["min_dte_floor"])
    expiries = sorted({r["expiry"] for r in rows if r.get("expiry")})
    ok = [e for e in expiries if (d := _dte(e, today)) is not None and floor <= d <= NEAR["max_dte"]]
    if not ok:
        return None, f"no expiry between {floor} and {NEAR['max_dte']} days"
    first = ok[0]
    close = [e for e in ok if _dte(e, today) - _dte(first, today) <= _SAME_WEEK_DAYS]
    best = max(close, key=lambda e: (_call_oi(rows, e), -_dte(e, today)))
    why = (f"nearest expiry with at least {floor} days (2x your {hold_days}-day hold, minimum 30)"
           + (f"; chosen over a date within a week for its larger call open interest" if best != first else ""))
    return best, why


def pick_leaps_expiry(rows: list[dict], today: date) -> tuple[str | None, str, bool]:
    """(expiry, reason, is_full_leaps). Among expiries 365+ days out, the one with the most call
    open interest, ties to the later date; else the longest expiry of 270+ days, labelled."""
    expiries = sorted({r["expiry"] for r in rows if r.get("expiry")})
    full = [e for e in expiries if (d := _dte(e, today)) is not None and d >= LEAPS["min_dte"]]
    if full:
        best = max(full, key=lambda e: (_call_oi(rows, e), _dte(e, today)))
        return best, "the 365+ day expiry with the most call open interest (where long-dated liquidity sits)", True
    near_full = [e for e in expiries if (d := _dte(e, today)) is not None and d >= LEAPS["fallback_min_dte"]]
    if near_full:
        return near_full[-1], "no expiry 365+ days out; the longest available (270+ days), not a full LEAPS", False
    return None, "no expiry 270+ days out", False


def _spread_pct(bid: float, ask: float) -> float:
    return (ask - bid) / ((ask + bid) / 2) * 100


def _greeks(row: dict, spot: float, years: float, iv: float) -> tuple[float | None, float | None, bool]:
    """(delta, gamma, estimated). Provider values when present, else the model's, flagged."""
    d, g = _num(row.get("delta")), _num(row.get("gamma"))
    if d is not None and g is not None:
        return d, g, False
    est = bs.call_greeks(spot, row["strike"], years, iv)
    if est is None:
        return d, g, d is None or g is None
    return (d if d is not None else est["delta"]), (g if g is not None else est["gamma"]), True


def eligible(rows: list[dict], expiry: str, spot: float, today: date, rules: dict) -> tuple[list[dict], dict, bool]:
    """(candidates, rejection counts, used_wide_quote_fallback) for one expiry."""
    dte = _dte(expiry, today)
    years = dte / 365.0
    out, rejected, wide = [], {}, []

    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1

    for r in rows:
        if r.get("expiry") != expiry:
            continue
        terms = r.get("contract_terms") or {}
        if terms.get("automatic_payoff_supported") is not True:
            reject("unsupported_contract_terms"); continue
        bid, ask, strike = _num(r.get("bid")), _num(r.get("ask")), _num(r.get("strike"))
        if strike is None or strike <= 0:
            reject("bad_strike"); continue
        if bid is None or ask is None or bid <= 0 or ask <= bid:
            reject("no_two_sided_quote"); continue
        oi = _num(r.get("oi"))
        if oi is None or oi < rules["min_oi"]:
            reject("open_interest_unknown_or_low"); continue
        iv_pct = _num(r.get("iv"))
        if iv_pct is None or not (0 < iv_pct <= _MAX_IV_PCT):
            reject("iv_unknown_or_invalid"); continue
        iv = iv_pct / 100.0
        delta, gamma, estimated = _greeks(r, spot, years, iv)
        if delta is None or not (rules["min_delta"] <= delta <= rules["max_delta"]):
            reject("delta_outside_range"); continue
        spread = _spread_pct(bid, ask)
        if spread > rules["fallback_spread_pct"]:
            reject("spread_too_wide"); continue
        c = {"option_symbol": r.get("option_symbol"), "expiry": expiry, "dte": dte, "strike": strike,
             "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread_pct": spread, "oi": int(oi),
             "iv_pct": iv_pct, "iv": iv, "delta": delta, "gamma": gamma, "greeks_estimated": estimated}
        (out if spread <= rules["max_spread_pct"] else wide).append(c)
    if out:
        rejected["spread_above_preferred"] = len(wide)
        return out, rejected, False
    return wide, rejected, bool(wide)


def scenario_return(c: dict, spot_at_exit: float, days_held: int) -> float | None:
    """Return on the premium paid: buy at the ask, exit at the model mid less half the spread."""
    years_left = max(c["dte"] - days_held, 0) / 365.0
    value = bs.call_price(spot_at_exit, c["strike"], years_left, c["iv"])
    if value is None:
        return None
    exit_value = value * (1 - c["spread_pct"] / 200.0)
    return exit_value / c["ask"] - 1


def _scenarios(c: dict, spot: float, target: float, hold: int, sigma_move: float) -> dict:
    late = min(2 * hold, max(c["dte"] - 1, 0))
    return {
        "target": scenario_return(c, target, hold),
        "half_move": scenario_return(c, spot + (target - spot) / 2, hold),
        "flat": scenario_return(c, spot, hold),
        "down_one_sigma": scenario_return(c, spot * (1 - sigma_move), hold),
        "target_twice_as_late": scenario_return(c, target, late),
        "late_days": late,
    }


def _reasons(c: dict, spot: float, target: float, hold: int, rank_in_oi: tuple[int, int],
             hv20: float | None, iv_rank: float | None) -> dict:
    g = bs.call_greeks(spot, c["strike"], c["dte"] / 365.0, c["iv"]) or {}
    theta = g.get("theta_per_day")
    vega = g.get("vega_per_point")
    moneyness = (spot - c["strike"]) / spot * 100
    intrinsic = max(spot - c["strike"], 0.0)
    time_value_share = (c["ask"] - intrinsic) / c["ask"] * 100
    delta_after = (bs.call_greeks(target, c["strike"], max(c["dte"] - hold, 1) / 365.0, c["iv"]) or {}).get("delta")
    round_trip = (c["ask"] - c["bid"]) * 100
    iv_vs_hv = (f"{c['iv_pct']:.0f}% IV against {hv20 * 100:.0f}% realised (20-day)"
                if hv20 else f"{c['iv_pct']:.0f}% IV; realised volatility unavailable")
    rank_text = f", IV rank {iv_rank:.0f}/100 over the past year" if iv_rank is not None else ""
    return {
        "expiration": (f"{c['dte']} days to run against a {hold}-day hold; time decay about "
                       f"${abs(theta) * 100:.2f}/day per contract ({abs(theta) / c['ask'] * 100:.2f}% of the premium)"
                       if theta is not None else f"{c['dte']} days to run against a {hold}-day hold"),
        "strike": (f"{'in' if moneyness > 0 else 'out of'} the money by {abs(moneyness):.1f}%; "
                   f"{time_value_share:.0f}% of the premium is time value"),
        "delta": (f"gains about ${c['delta'] * 100:.0f} per $1 the stock rises (like {c['delta'] * 100:.0f} shares) "
                  f"for ${c['ask'] * 100:.0f}, against ${spot * 100:,.0f} for 100 shares"
                  + (" (estimated: the provider sent no greeks for this contract)" if c["greeks_estimated"] else "")),
        "gamma": (f"{c['gamma']:.3f}: delta would be about {delta_after:.2f} after the target move"
                  if delta_after is not None and c["gamma"] is not None else "gamma unavailable"),
        "open_interest": (f"{c['oi']:,} contracts, #{rank_in_oi[0]} of {rank_in_oi[1]} calls in this expiry; "
                          f"spread {c['spread_pct']:.1f}% (about ${round_trip:.0f} per contract round trip at these quotes)"),
        "iv": (iv_vs_hv + rank_text
               + (f"; a 5-point IV drop would cost about ${vega * 5 * 100:.0f} per contract" if vega is not None else "")),
    }


def _pick(cands: list[dict], spot: float, target: float, hold: int, sigma_move: float, key: str) -> dict | None:
    best, best_val = None, None
    for c in cands:
        val = scenario_return(c, spot + (target - spot) / 2 if key == "half" else target, hold)
        if val is None:
            continue
        tie = (val, c["oi"], -c["spread_pct"], -c["strike"])
        if best_val is None or tie > best_val:
            best, best_val = c, tie
    return best


def _describe(c: dict, spot: float, target: float, hold: int, sigma_move: float, cands: list[dict],
              hv20, iv_rank) -> dict:
    ranked = sorted((x["oi"] for x in cands), reverse=True)
    breakeven = c["strike"] + c["ask"]
    years = c["dte"] / 365.0
    return {
        "option_symbol": c["option_symbol"], "expiry": c["expiry"], "dte": c["dte"], "strike": c["strike"],
        "bid": c["bid"], "ask": c["ask"], "mid": round(c["mid"], 4), "spread_pct": round(c["spread_pct"], 2),
        "cost_per_contract": round(c["ask"] * 100, 2), "breakeven": round(breakeven, 4),
        "breakeven_move_pct": round((breakeven / spot - 1) * 100, 2),
        "delta": round(c["delta"], 4), "gamma": round(c["gamma"], 5) if c["gamma"] is not None else None,
        "greeks_estimated": c["greeks_estimated"], "oi": c["oi"], "iv_pct": c["iv_pct"],
        "scenarios": _scenarios(c, spot, target, hold, sigma_move),
        "model_prob_above_breakeven": bs.prob_above(spot, breakeven, years, c["iv"]),
        "reasons": _reasons(c, spot, target, hold, (ranked.index(c["oi"]) + 1, len(ranked)), hv20, iv_rank),
    }


def find(kind: str, rows: list[dict], *, spot: float, today: date, hold_days: int,
         target: float | None = None, hv20: float | None = None, iv_rank: float | None = None,
         sma200: float | None = None, events: list[tuple[str, str]] | None = None,
         chain_age_days: int | None = None) -> dict:
    """One pick ('near' or 'leaps') from one session's call rows."""
    rules = NEAR if kind == "near" else LEAPS
    hold = hold_days if kind == "near" else LEAPS_HOLD_DAYS
    if not _num(spot) or spot <= 0:
        return {"kind": kind, "available": False, "reason": "no verified stock price for the chain's session"}
    if kind == "near":
        expiry, expiry_reason = pick_near_expiry(rows, today, hold)
        full_leaps = None
    else:
        expiry, expiry_reason, full_leaps = pick_leaps_expiry(rows, today)
    if expiry is None:
        return {"kind": kind, "available": False, "reason": expiry_reason}
    cands, rejected, wide = eligible(rows, expiry, spot, today, rules)
    if not cands:
        top = max(rejected, key=rejected.get) if rejected else None
        return {"kind": kind, "available": False, "expiry": expiry, "rejected": rejected,
                "reason": f"no contract in {expiry} passed the checks" + (f"; most removed by {top}" if top else "")}
    atm = min(cands, key=lambda c: abs(c["strike"] - spot))
    sigma_move = atm["iv"] * math.sqrt(hold / 365.0)
    target_basis = "your target"
    if not _num(target) or target <= spot:
        target = spot * (1 + sigma_move)
        target_basis = (f"one implied standard deviation up over {hold} days "
                        f"({sigma_move * 100:.1f}%, from the at-the-money IV): a neutral yardstick, not a forecast")
    pick = _pick(cands, spot, target, hold, sigma_move, "half")
    aggressive = _pick(cands, spot, target, hold, sigma_move, "full")
    warnings = []
    for label, when in events or []:
        d = _dte(when, today)
        if d is not None and 0 <= d <= hold:
            warnings.append(f"{label} on {when} falls inside the holding period (date not issuer-confirmed); "
                            "IV usually falls after earnings, which this model does not include")
    if iv_rank is not None and iv_rank > 60:
        warnings.append(f"IV rank {iv_rank:.0f}: calls are expensive against this stock's own year; "
                        "a call spread would cut the IV exposure")
    if kind == "leaps" and _num(sma200) and spot < sma200:
        warnings.append("the stock is below its 200-day average; this project's LEAPS backtest found a "
                        "0.70-delta call needs a trend (TQQQ -73.78% over 336 days)")
    if kind == "leaps" and full_leaps is False:
        warnings.append("no expiry 365+ days out; this is the longest available, not a full LEAPS")
    if wide:
        warnings.append("no contract met the preferred spread; the wider-quote fallback (up to 20%) was used")
    if pick["greeks_estimated"]:
        warnings.append("delta and gamma are estimated from the contract's IV (the provider sent none)")
    if chain_age_days is not None and chain_age_days > 3:
        warnings.append(f"the chain is from a session {chain_age_days} days ago")
    return {
        "kind": kind, "available": True, "method_version": METHOD_VERSION,
        "expiry": expiry, "expiry_reason": expiry_reason, "hold_days": hold,
        "target": round(target, 4), "target_basis": target_basis, "spot": spot,
        "rule": "highest return if the stock makes half of the target move by the end of the hold",
        "pick": _describe(pick, spot, target, hold, sigma_move, cands, hv20, iv_rank),
        "aggressive_alternative": (None if aggressive is None or aggressive["option_symbol"] == pick["option_symbol"]
                                   else _describe(aggressive, spot, target, hold, sigma_move, cands, hv20, iv_rank)),
        "eligible_count": len(cands), "rejected": rejected, "wide_quote_fallback": wide,
        "warnings": warnings,
    }

"""T402-OPTIONS-STRATEGY-MATRIX — all four option legs, the common combinations, and an
honest recommendation among them.

WHY THIS EXISTS. The Options Game Plan showed exactly two plays: a protective put (BUY a put)
and a covered call (SELL a call). That is half the grid. The other two legs answer questions a
holder actually asks — "how do I get leveraged upside with defined risk" (BUY a call) and "how
do I get paid to wait for my entry" (SELL a put) — and the interesting structures are all
COMBINATIONS of legs, not single legs.

EVERYTHING HERE IS PURE. No DB, no HTTP, no clock beyond an injected `today`. The caller passes
an already-fetched chain, exactly as compute_options_game_plan() already does, so every payoff
below is testable against a synthetic chain with no network.

WHAT THIS IS NOT. Not a prediction, and not advice. Every number is the arithmetic of a REAL,
currently-listed contract against the user's OWN stop/target — the same honesty convention the
rest of this app's options surface already follows (max-pain, GEX and squeeze alerts all
explicitly disclaim prediction). The recommendation ranks structures by how well they match a
stated objective and the current IV regime; it cannot know whether the trade will win.

THE ONE REAL EDGE ENCODED HERE is the volatility direction, because it is the part most people
get backwards: option premium is the product, and IV is its price. When IV is HIGH relative to
this symbol's own trailing range, selling premium (cash-secured put, covered call, credit
spreads) is being paid above the usual rate, and buying it is paying above the usual rate. When
IV is LOW the reverse holds. That single consideration flips which side of the same directional
view you should take, and it is why a "bullish" view does not automatically mean "buy a call".
"""
from __future__ import annotations

from datetime import date, datetime, timezone

# Above this IV percentile, SELLING premium is favoured; below the low mark, BUYING is.
# Deliberately wide and neutral in the middle — a 50th-percentile IV is not information, and
# pretending otherwise would manufacture a recommendation out of noise.
_IV_RANK_RICH = 60.0
_IV_RANK_CHEAP = 30.0

_CONTRACT_MULTIPLIER = 100


def _mid(contract: dict | None) -> float | None:
    """Mid of bid/ask, falling back to last traded price.

    Mid rather than bid or ask because these figures are shown as "what this would cost/pay",
    not as a fill you are guaranteed. The real fill lands somewhere in the spread, and a wide
    spread is itself reported (see `spread_pct`) so the reader can see how much that matters.
    """
    if not contract:
        return None
    bid, ask = contract.get("bid") or 0.0, contract.get("ask") or 0.0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    last = contract.get("last_price") or 0.0
    return float(last) if last > 0 else None


def _spread_pct(contract: dict | None) -> float | None:
    """Bid-ask spread as a % of mid. A 30%-wide spread means the 'cost' below is a polite
    fiction — you will not fill at mid — so it is surfaced rather than buried."""
    if not contract:
        return None
    bid, ask = contract.get("bid") or 0.0, contract.get("ask") or 0.0
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    return round((ask - bid) / mid * 100, 1) if mid > 0 else None


def _nearest(rows: list[dict], target: float | None) -> dict | None:
    if not rows or target is None:
        return None
    return min(rows, key=lambda r: abs(r["strike"] - target))


def _leg(contract: dict | None, action: str, expiry: str | None, dte: int | None) -> dict | None:
    """One leg of a structure, in the shape the UI and the calculator both consume."""
    price = _mid(contract)
    if contract is None or price is None or price <= 0:
        return None
    return {
        "action": action,                      # "buy" | "sell"
        "right": contract.get("right", "?"),   # "call" | "put"
        "strike": contract["strike"],
        "price_per_share": round(price, 2),
        "cost_per_contract": round(price * _CONTRACT_MULTIPLIER, 2),
        "expiry": expiry,
        "days_to_expiry": dte,
        "iv": contract.get("iv"),
        "oi": contract.get("oi"),
        "spread_pct": _spread_pct(contract),
    }


def _dte(expiry: str | None, today: date) -> int | None:
    if not expiry:
        return None
    try:
        return (datetime.strptime(expiry, "%Y-%m-%d").date() - today).days
    except ValueError:
        return None


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def build_strategy_matrix(
    *,
    current_price: float,
    stop_loss: float | None,
    take_profit: float | None,
    signal: str | None,
    put_rows: list[dict],
    put_expiry: str | None,
    call_rows: list[dict],
    call_expiry: str | None,
    shares: float | None = None,
    iv_rank: float | None = None,
    today: date | None = None,
) -> dict:
    """All four single legs plus the combinations that can be built from the SAME two chains.

    Strike selection is anchored to the user's own plan wherever one exists — the protective put
    and bear spread sit at the stop, the covered call and bull spread cap at the target — and
    falls back to at-the-money only where the plan says nothing. Nothing here invents a
    stop or target.
    """
    today = today or datetime.now(timezone.utc).date()
    put_dte, call_dte = _dte(put_expiry, today), _dte(call_expiry, today)

    # Tag each row with its right so a leg is self-describing once detached from its chain.
    put_rows = [{**r, "right": "put"} for r in (put_rows or [])]
    call_rows = [{**r, "right": "call"} for r in (call_rows or [])]

    atm_call = _nearest(call_rows, current_price)
    atm_put = _nearest(put_rows, current_price)
    target_call = _nearest(call_rows, take_profit) if take_profit else None
    stop_put = _nearest(put_rows, stop_loss) if stop_loss else None

    singles: dict = {}
    combos: dict = {}

    # ── 1. BUY CALL — long call ────────────────────────────────────────────────────────
    # Defined risk, unlimited upside, and the whole premium is at risk if the stock simply
    # does not move. That last part is the one people underestimate: being RIGHT about
    # direction but slow still loses the entire debit.
    leg = _leg(atm_call, "buy", call_expiry, call_dte)
    if leg:
        debit = leg["price_per_share"]
        singles["long_call"] = {
            "name": "Long Call", "direction": "bullish", "net": "debit",
            "legs": [leg],
            "net_per_share": round(debit, 2),
            "net_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
            "max_loss_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
            "max_profit_per_contract": None,          # unbounded
            "breakeven": round(leg["strike"] + debit, 2),
            "breakeven_move_pct": _pct(leg["strike"] + debit - current_price, current_price),
            "requires_shares": False,
            "collateral_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
            "what_it_does": "Pays for the right to buy at the strike. Full premium is lost if the stock is below the strike at expiry.",
            "use_when": "You expect a move up big enough and soon enough to clear the breakeven, and you want a hard cap on what you can lose.",
        }

    # ── 2. SELL CALL — covered call ────────────────────────────────────────────────────
    # Income, but it is NOT free: you have sold your upside above the strike. This is the
    # existing covered-call leg, restated in the shared shape.
    leg = _leg(target_call or atm_call, "sell", call_expiry, call_dte)
    if leg:
        credit = leg["price_per_share"]
        singles["covered_call"] = {
            "name": "Covered Call", "direction": "neutral-to-mildly-bullish", "net": "credit",
            "legs": [leg],
            "net_per_share": round(credit, 2),
            "net_per_contract": round(credit * _CONTRACT_MULTIPLIER, 2),
            "max_loss_per_contract": round((current_price - credit) * _CONTRACT_MULTIPLIER, 2),
            "max_profit_per_contract": round((leg["strike"] - current_price + credit) * _CONTRACT_MULTIPLIER, 2),
            "breakeven": round(current_price - credit, 2),
            "breakeven_move_pct": _pct(-credit, current_price),
            "requires_shares": True,
            "collateral_per_contract": round(current_price * _CONTRACT_MULTIPLIER, 2),
            "what_it_does": f"Collects premium now in exchange for capping your exit near ${leg['strike']:.2f}.",
            "use_when": "You already own the shares, would be content selling at the strike, and want to be paid for the wait.",
        }

    # ── 3. BUY PUT — protective put ────────────────────────────────────────────────────
    leg = _leg(stop_put or atm_put, "buy", put_expiry, put_dte)
    if leg:
        debit = leg["price_per_share"]
        singles["protective_put"] = {
            "name": "Protective Put", "direction": "hedge", "net": "debit",
            "legs": [leg],
            "net_per_share": round(debit, 2),
            "net_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
            "max_loss_per_contract": round((current_price - leg["strike"] + debit) * _CONTRACT_MULTIPLIER, 2),
            "max_profit_per_contract": None,          # you still own the upside
            "breakeven": round(current_price + debit, 2),
            "breakeven_move_pct": _pct(debit, current_price),
            "requires_shares": True,
            "collateral_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
            "effective_floor": round(leg["strike"] - debit, 2),
            "what_it_does": f"Insurance. Puts a floor near ${leg['strike'] - debit:.2f} no matter how far the stock falls.",
            "use_when": "You own the shares, want to keep the upside, and are willing to pay a known premium to bound the downside.",
        }

    # ── 4. SELL PUT — cash-secured put ─────────────────────────────────────────────────
    # The leg most often missing from a "game plan", and the one that answers "I want in, but
    # lower". You are paid to promise to buy at the strike. The risk is NOT the premium — it
    # is owning the stock at the strike after it has fallen through it.
    leg = _leg(stop_put or atm_put, "sell", put_expiry, put_dte)
    if leg:
        credit = leg["price_per_share"]
        singles["cash_secured_put"] = {
            "name": "Cash-Secured Put", "direction": "neutral-to-bullish", "net": "credit",
            "legs": [leg],
            "net_per_share": round(credit, 2),
            "net_per_contract": round(credit * _CONTRACT_MULTIPLIER, 2),
            "max_loss_per_contract": round((leg["strike"] - credit) * _CONTRACT_MULTIPLIER, 2),
            "max_profit_per_contract": round(credit * _CONTRACT_MULTIPLIER, 2),
            "breakeven": round(leg["strike"] - credit, 2),
            "breakeven_move_pct": _pct(leg["strike"] - credit - current_price, current_price),
            "requires_shares": False,
            "collateral_per_contract": round(leg["strike"] * _CONTRACT_MULTIPLIER, 2),
            "effective_entry": round(leg["strike"] - credit, 2),
            "what_it_does": f"Paid now to promise to buy at ${leg['strike']:.2f}; if assigned your effective cost is ${leg['strike'] - credit:.2f}.",
            "use_when": "You want the shares but at a lower price, and are genuinely willing to own them if it drops.",
        }

    # ── 5. COLLAR — long put + short call ──────────────────────────────────────────────
    # The natural pairing of the two legs this page already had. The short call PAYS for the
    # protective put, which is why a collar is the usual answer to "hedging is too expensive".
    pl, cl = _leg(stop_put, "buy", put_expiry, put_dte), _leg(target_call, "sell", call_expiry, call_dte)
    if pl and cl:
        net = pl["price_per_share"] - cl["price_per_share"]   # >0 = net cost
        combos["collar"] = {
            "name": "Collar", "direction": "hedge", "net": "debit" if net > 0 else "credit",
            "legs": [pl, cl],
            "net_per_share": round(net, 2),
            "net_per_contract": round(net * _CONTRACT_MULTIPLIER, 2),
            "max_loss_per_contract": round((current_price - pl["strike"] + net) * _CONTRACT_MULTIPLIER, 2),
            "max_profit_per_contract": round((cl["strike"] - current_price - net) * _CONTRACT_MULTIPLIER, 2),
            "breakeven": round(current_price + net, 2),
            "breakeven_move_pct": _pct(net, current_price),
            "requires_shares": True,
            "collateral_per_contract": round(current_price * _CONTRACT_MULTIPLIER, 2),
            "what_it_does": (f"Floors you near ${pl['strike']:.2f} and caps you near ${cl['strike']:.2f}, "
                             f"for a net {'cost' if net > 0 else 'credit'} of ${abs(net):.2f}/share."),
            "use_when": "You own the shares and want the downside bounded without paying full price for the put — you are selling your upside to fund it.",
        }

    # ── 6. BULL CALL SPREAD — long ATM call + short call at the target ─────────────────
    # Cheaper than the outright call, because you sell away the part of the upside your OWN
    # target says you do not expect to reach.
    lo, hi = _leg(atm_call, "buy", call_expiry, call_dte), _leg(target_call, "sell", call_expiry, call_dte)
    if lo and hi and hi["strike"] > lo["strike"]:
        debit = lo["price_per_share"] - hi["price_per_share"]
        width = hi["strike"] - lo["strike"]
        if debit > 0:
            combos["bull_call_spread"] = {
                "name": "Bull Call Spread", "direction": "bullish", "net": "debit",
                "legs": [lo, hi],
                "net_per_share": round(debit, 2),
                "net_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
                "max_loss_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
                "max_profit_per_contract": round((width - debit) * _CONTRACT_MULTIPLIER, 2),
                "breakeven": round(lo["strike"] + debit, 2),
                "breakeven_move_pct": _pct(lo["strike"] + debit - current_price, current_price),
                "requires_shares": False,
                "collateral_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
                "reward_risk": round((width - debit) / debit, 2) if debit > 0 else None,
                "what_it_does": f"Bullish to ${hi['strike']:.2f} for {_pct(debit, lo['price_per_share'])}% of the outright call's cost.",
                "use_when": "You are bullish but your own target is the cap anyway — so paying for upside beyond it is waste.",
            }

    # ── 7. BEAR PUT SPREAD — long put at the stop + short put below it ─────────────────
    # A cheaper hedge than the outright put, at the cost of a floor UNDER the floor: below the
    # short strike you are unprotected again.
    if stop_put and put_rows:
        lower_target = stop_put["strike"] - max(current_price * 0.05, 0.01)
        lower = _nearest([r for r in put_rows if r["strike"] < stop_put["strike"]], lower_target)
        hp, lp = _leg(stop_put, "buy", put_expiry, put_dte), _leg(lower, "sell", put_expiry, put_dte)
        if hp and lp and lp["strike"] < hp["strike"]:
            debit = hp["price_per_share"] - lp["price_per_share"]
            width = hp["strike"] - lp["strike"]
            if debit > 0:
                combos["bear_put_spread"] = {
                    "name": "Bear Put Spread", "direction": "bearish-hedge", "net": "debit",
                    "legs": [hp, lp],
                    "net_per_share": round(debit, 2),
                    "net_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
                    "max_loss_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
                    "max_profit_per_contract": round((width - debit) * _CONTRACT_MULTIPLIER, 2),
                    "breakeven": round(hp["strike"] - debit, 2),
                    "breakeven_move_pct": _pct(hp["strike"] - debit - current_price, current_price),
                    "requires_shares": False,
                    "collateral_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
                    "reward_risk": round((width - debit) / debit, 2) if debit > 0 else None,
                    "what_it_does": f"Protection between ${hp['strike']:.2f} and ${lp['strike']:.2f} only — below ${lp['strike']:.2f} you are exposed again.",
                    "use_when": "You want downside cover cheaply and believe a fall would be contained, not a collapse.",
                }

    return {
        "singles": singles,
        "combos": combos,
        "recommendation": _recommend(
            singles=singles, combos=combos, signal=signal, iv_rank=iv_rank,
            holds_shares=bool(shares and shares > 0),
        ),
        "iv_rank": iv_rank,
        "iv_regime": _iv_regime(iv_rank),
    }


def _iv_regime(iv_rank: float | None) -> str:
    if iv_rank is None:
        return "unknown"
    if iv_rank >= _IV_RANK_RICH:
        return "rich"
    if iv_rank <= _IV_RANK_CHEAP:
        return "cheap"
    return "normal"


def _recommend(*, singles: dict, combos: dict, signal: str | None,
               iv_rank: float | None, holds_shares: bool) -> dict:
    """Pick a primary structure, and say WHY in terms the reader can check.

    Two inputs drive it, in this order:

      1. What you already own. A covered call or collar is unavailable to someone holding no
         shares, and a cash-secured put is the wrong answer for someone who already has a full
         position. This is a hard constraint, not a preference.
      2. Where IV sits in this symbol's own range. High IV favours SELLING premium, low IV
         favours BUYING it — the same directional view points at a different structure.

    The signal is deliberately the WEAKEST input. This app's own 2026-09 audits found the
    displayed confidence does not reliably order outcomes, so it selects among structures that
    already fit the constraints rather than overriding them. When nothing fits, this returns
    None with a reason instead of inventing a pick.
    """
    regime = _iv_regime(iv_rank)
    sig = (signal or "").upper()
    bullish, bearish = sig in {"BUY", "STRONG_BUY"}, sig in {"SELL", "STRONG_SELL"}
    available = {**singles, **combos}
    if not available:
        return {"primary": None, "reason": "No listed contracts priced well enough to build a structure."}

    def pick(key: str, why: str) -> dict | None:
        return {"primary": key, "name": available[key]["name"], "reason": why} if key in available else None

    iv_note = {
        "rich": "IV is high in this symbol's own 1-year range, so premium is expensive — that favours SELLING it rather than buying.",
        "cheap": "IV is low in this symbol's own 1-year range, so premium is cheap — that favours BUYING it rather than selling.",
        "normal": "IV is mid-range, so volatility is not itself an argument either way; this is a directional and position choice.",
        "unknown": "IV rank was unavailable for this symbol, so this ranking rests on your position and direction only — not on whether premium is currently rich or cheap.",
    }[regime]

    order: list[tuple[str, str]] = []
    if holds_shares:
        if bearish or regime == "cheap":
            order = [("protective_put", "You own the shares and the case for protection is strongest here: you keep all the upside and pay a known, bounded premium for the floor."),
                     ("collar", "Bounds the downside while the short call pays for most of the put."),
                     ("bear_put_spread", "A cheaper partial hedge when a fall is likely to be contained.")]
        elif regime == "rich":
            order = [("covered_call", "You own the shares and premium is expensive — selling a call against them is being paid above the usual rate for upside you have already said you would exit at."),
                     ("collar", "Same income, with the put buying back your downside."),
                     ("protective_put", "Pure protection, but you are paying the same rich premium you could be collecting.")]
        else:
            order = [("collar", "You own the shares and IV gives no edge either way, so the structure that costs least to hold is the one that funds its own hedge."),
                     ("covered_call", "Income if you are content capping at your target."),
                     ("protective_put", "Protection if keeping the upside matters more than the premium.")]
    else:
        if regime == "rich":
            order = [("cash_secured_put", "You hold no shares and premium is expensive — being PAID to wait for a lower entry beats paying up for a call. You must genuinely want the shares at the strike."),
                     ("bull_call_spread", "If you want defined-risk upside anyway, the spread sells back some of that expensive premium."),
                     ("long_call", "Cleanest bullish expression, but you are buying premium at its most expensive.")]
        elif bullish and regime == "cheap":
            order = [("long_call", "Bullish with premium cheap in this symbol's own range: the outright call keeps the full upside and caps the loss at the debit."),
                     ("bull_call_spread", "Cheaper still, if your own target is the realistic cap."),
                     ("cash_secured_put", "Bullish but paid to wait, if you would rather own the shares lower.")]
        elif bullish:
            order = [("bull_call_spread", "Bullish, with IV giving no edge — the spread caps the upside at your own target, which you were not counting on exceeding anyway, and costs a fraction of the outright call."),
                     ("long_call", "If you want the uncapped upside and accept the higher debit."),
                     ("cash_secured_put", "If you would rather be paid to wait for a lower entry.")]
        else:
            order = [("cash_secured_put", "No shares and no bullish signal: the only structure that pays you while you wait, and commits you only at a price you chose."),
                     ("bull_call_spread", "Defined-risk upside if the view turns bullish."),
                     ("long_call", "Highest risk of total premium loss without a directional edge — listed last for that reason.")]

    for key, why in order:
        got = pick(key, why)
        if got:
            alts = [{"key": k, "name": available[k]["name"], "reason": w}
                    for k, w in order if k != key and k in available]
            return {**got, "iv_regime": regime, "iv_note": iv_note, "alternatives": alts,
                    "constraint": ("You hold shares, so covered calls and collars are available."
                                   if holds_shares else
                                   "You hold no shares, so covered calls, collars and protective puts are not available — they all require the stock.")}

    key = next(iter(available))
    return {"primary": key, "name": available[key]["name"],
            "reason": "The only structure that could be priced from the currently-listed chain.",
            "iv_regime": regime, "iv_note": iv_note, "alternatives": []}

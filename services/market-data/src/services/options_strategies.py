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

import math

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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

    SR-06 (2026-10-02): A CROSSED QUOTE USED TO PRICE NORMALLY. `bid > 0 and ask > 0` admits
    bid 12 / ask 2, whose "mid" of 7 is not a price of anything — a crossed or locked book
    means the two sides are not describing the same market, and a structure priced from one
    leg's crossed quote and another's clean quote is comparing two different moments. Such a
    quote is now refused rather than averaged.

    The last-price fallback is kept, because an illiquid leg with no live two-sided quote is
    ordinary, but it is a different KIND of number: a trade that already happened, at an
    unknown time, rather than a current market. `_leg` records which one was used so the
    reader is not shown a stale print as if it were a quote.
    """
    if not contract:
        return None
    bid, ask = contract.get("bid") or 0.0, contract.get("ask") or 0.0
    if not _finite_positive(bid) or not _finite_positive(ask):
        bid = ask = 0.0
    if bid > 0 and ask > 0:
        if bid > ask:
            return None  # crossed book — not a price
        return (bid + ask) / 2.0
    last = contract.get("last_price") or 0.0
    return float(last) if _finite_positive(last) else None


def _finite_positive(value) -> bool:
    """A price must be a real, finite, positive number. NaN and inf both survive `> 0`
    comparisons in ways that produce payoff arithmetic nobody can read."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def _price_source(contract: dict | None) -> str:
    """Which kind of number `_mid` returned — a live two-sided quote, or a stale print."""
    if not contract:
        return "none"
    bid, ask = contract.get("bid") or 0.0, contract.get("ask") or 0.0
    if _finite_positive(bid) and _finite_positive(ask) and bid <= ask:
        return "quote_mid"
    return "last_trade" if _finite_positive(contract.get("last_price")) else "none"


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
    # SR-06: a leg with no readable strike or expiry cannot be part of a payoff statement —
    # the width of a vertical and the bounds of a collar are both functions of exactly these.
    if not _finite_positive(contract.get("strike")) or not expiry:
        return None
    # SF-05 (2026-10-02): A NONEMPTY EXPIRY IS NOT A VALID ONE.
    # The check above accepted any truthy string, so an already-expired contract became a
    # priced leg. Witness: on October 2 an October 1 call was still the matrix's PRIMARY
    # recommendation, with days_to_expiry = -1. The ordinary route happens to filter past
    # expiries upstream, so this is a defensive gap for alternate callers, replay and stale
    # caches rather than proof the normal endpoint serves expired options — but a payoff
    # computed for a contract that no longer exists is not a plan under any caller.
    #
    # DTE ZERO IS DELIBERATELY STILL ALLOWED, and that is not an oversight: an expiry today is
    # tradeable until its last trading time, which is a session/venue question this pure
    # module cannot answer. Treating 0 as expired would silently drop same-day structures;
    # treating it as fully tradeable is the existing assumption and is left to the caller.
    if dte is not None and dte < 0:
        return None
    # SF-05 RESIDUAL (2026-10-02): "UNKNOWN IS NOT EXPIRED" DOES NOT MAKE UNKNOWN ELIGIBLE.
    #
    # The guard above rejects a negative DTE but accepted None with any nonempty expiry, and
    # `_dte()` returns None for an unparseable date — so `expiry='not-a-date'` produced a
    # priced leg with `days_to_expiry=None`, and the matrix recommended it. My own test
    # asserting "unknown DTE still builds" could not tell an OMITTED CALCULATION apart from
    # an UNPARSEABLE CONTRACT IDENTITY, and those are different facts.
    #
    # The expiry string is validated here, at the construction boundary, rather than trusting
    # the caller's `dte`: a contract whose identity cannot be read is not a contract this
    # module can price, whoever called it and whatever they computed.
    try:
        datetime.strptime(expiry, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return {
        "action": action,                      # "buy" | "sell"
        "right": contract.get("right", "?"),   # "call" | "put"
        "strike": contract["strike"],
        # SR-06: says whether the number beside it is a live two-sided quote or a trade that
        # already happened. Presenting the second as the first is how an "executable estimate"
        # becomes fiction.
        "price_source": _price_source(contract),
        "price_per_share": round(price, 2),
        "cost_per_contract": round(price * _CONTRACT_MULTIPLIER, 2),
        "expiry": expiry,
        "days_to_expiry": dte,
        "iv": contract.get("iv"),
        "oi": contract.get("oi"),
        "spread_pct": _spread_pct(contract),
    }


# SR-06: fees are not modelled per-contract anywhere in this module, so "after costs" is
# expressed as a minimum edge the structure must clear before it is worth offering at all. A
# debit within a cent of the width is arithmetically positive and economically pointless.
_MIN_VERTICAL_EDGE_PER_SHARE = 0.05


def _vertical_is_viable(debit: float, width: float, long_leg: dict, short_leg: dict) -> bool:
    """Is this debit vertical an economically valid plan, not merely positive arithmetic?

    A debit vertical's maximum payoff at expiry is `width - debit`. Four ways that fails:

      * a non-positive debit (it is not a debit spread at all);
      * a debit at or above the width, whose best case is a loss;
      * a debit so close to the width that the remaining edge cannot survive costs;
      * legs that do not share an expiry, which makes `width - debit` the payoff of a
        structure nobody holds — the same defect SR-07 describes for the collar.
    """
    if not _finite_positive(debit) or not _finite_positive(width):
        return False
    if long_leg.get("expiry") != short_leg.get("expiry"):
        return False
    return (width - debit) >= _MIN_VERTICAL_EDGE_PER_SHARE


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
    # AUD-T409-UTCDATEBOUNDARY: ET, not a naive UTC truncation — see the same finding in
    # options_income_engine._today_et(). A naive UTC date reads one calendar day ahead of
    # the real US trading day for ~4-5 hours every evening, which would understate every
    # days_to_expiry shown on the game plan and calculator by one during that window.
    today = today or datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
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
    # SR-06/SR-07: structures that COULD be built from the chain but must not be offered with
    # a payoff summary, each with the reason. Omitting them silently would leave the reader to
    # conclude the chain had nothing, which is a different and wrong answer.
    unavailable: dict = {}

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
            "directional_exposure": "bullish",
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
            "directional_exposure": "income_long",
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
            "directional_exposure": "hedge_long",
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
            "directional_exposure": "income_bullish",
            "collateral_per_contract": round(leg["strike"] * _CONTRACT_MULTIPLIER, 2),
            "effective_entry": round(leg["strike"] - credit, 2),
            "what_it_does": f"Paid now to promise to buy at ${leg['strike']:.2f}; if assigned your effective cost is ${leg['strike'] - credit:.2f}.",
            "use_when": "You want the shares but at a lower price, and are genuinely willing to own them if it drops.",
        }

    # ── 5. COLLAR — long put + short call ──────────────────────────────────────────────
    # The natural pairing of the two legs this page already had. The short call PAYS for the
    # protective put, which is why a collar is the usual answer to "hedging is too expensive".
    pl, cl = _leg(stop_put, "buy", put_expiry, put_dte), _leg(target_call, "sell", call_expiry, call_dte)
    # SR-07 (2026-10-02): A SINGLE-EXPIRY PAYOFF DIAGRAM DOES NOT DESCRIBE A STAGGERED COLLAR.
    # The caller selects protective puts at 25-60 DTE and calls at 14-45 DTE, so the two legs
    # routinely expire on different dates — and the block below reported one fixed max profit,
    # max loss and breakeven regardless. The witness: stock 100, put 95 expiring Nov 20, call
    # 110 expiring Oct 16, reported max profit $900 per 100 shares. If the short call expires
    # worthless at 100 and the stock then reaches 120 by the put's expiry, the position earns
    # $1,900 — there is no longer a call capping that upside. Every other path needs
    # assignment and remaining-leg analysis that a common-expiry diagram cannot express.
    #
    # Matched expiry is therefore a precondition for offering the structure with bounds. An
    # unmatched pair is not silently dropped: it is recorded as unavailable with the reason,
    # so the page can say why rather than simply omitting a structure the reader expected.
    if pl and cl and pl["expiry"] != cl["expiry"]:
        unavailable["collar"] = {
            "name": "Collar",
            "reason": (f"The protective put expires {pl['expiry']} and the call "
                       f"{cl['expiry']}. A collar's floor, cap and breakeven are only defined "
                       f"when both legs expire together; with different dates the position "
                       f"changes shape when the first leg expires, and a single payoff summary "
                       f"would misstate it."),
            "put_expiry": pl["expiry"], "call_expiry": cl["expiry"],
        }
    elif pl and cl:
        net = pl["price_per_share"] - cl["price_per_share"]   # >0 = net cost
        combos["collar"] = {
            "name": "Collar", "direction": "hedge", "net": "debit" if net > 0 else "credit",
            "legs": [pl, cl],
            # SR-07: the bounds below are only meaningful because both legs share this date.
            "expiry": pl["expiry"],
            "net_per_share": round(net, 2),
            "net_per_contract": round(net * _CONTRACT_MULTIPLIER, 2),
            "max_loss_per_contract": round((current_price - pl["strike"] + net) * _CONTRACT_MULTIPLIER, 2),
            "max_profit_per_contract": round((cl["strike"] - current_price - net) * _CONTRACT_MULTIPLIER, 2),
            "breakeven": round(current_price + net, 2),
            "breakeven_move_pct": _pct(net, current_price),
            "requires_shares": True,
            "directional_exposure": "hedge_long",
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
        # SR-06 (2026-10-02): `debit > 0` IS NOT A VALIDITY TEST.
        # A debit vertical's maximum payoff at expiry is the strike width LESS the debit, so a
        # debit at or above the width is a structure whose best case is a loss. The witness:
        # underlying 100, strikes 100/105, leg mids 12 and 2 -> debit 10 against width 5, and
        # the module computed max profit -$500 while still offering it as the primary
        # recommendation for a bullish request. The arithmetic was right; nothing asked
        # whether the result made sense.
        if _vertical_is_viable(debit, width, lo, hi):
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
                "directional_exposure": "bullish",
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
            # SR-06: same test as the bull call spread — see its own note.
            if _vertical_is_viable(debit, width, hp, lp):
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
                    "directional_exposure": "bearish",
                    "collateral_per_contract": round(debit * _CONTRACT_MULTIPLIER, 2),
                    "reward_risk": round((width - debit) / debit, 2) if debit > 0 else None,
                    "what_it_does": f"Protection between ${hp['strike']:.2f} and ${lp['strike']:.2f} only — below ${lp['strike']:.2f} you are exposed again.",
                    "use_when": "You want downside cover cheaply and believe a fall would be contained, not a collapse.",
                }

    return {
        # Construction has no live quote, account, deliverable, market-state or portfolio
        # evidence. It therefore cannot call the shared promotion gate with invented facts.
        "trade_eligibility": {
            "status": "research_only", "quantity": None, "risk_budget": None,
            "blockers": [
                "identity_verified", "deliverable_verified", "quotes_fresh",
                "two_sided_quotes", "spread_acceptable", "size_sufficient",
                "market_open", "event_coverage_verified", "account_permissions_verified",
                "capital_sufficient", "portfolio_risk_checked",
            ],
        },
        "singles": singles,
        "combos": combos,
        "unavailable": unavailable,
        "recommendation": _recommend(
            singles=singles, combos=combos, signal=signal, iv_rank=iv_rank,
            # SF-03 (2026-10-02): ONE SHARE DOES NOT COVER A CALL.
            # `bool(shares and shares > 0)` made a single share qualify the account for a
            # covered call, and the recommendation then said "You hold shares, so covered
            # calls and collars are available" — while every payoff beside it was quoted per
            # standard 100-share contract, and the frontend separately told the reader 100
            # shares were required. The advice and the warning disagreed on the same screen.
            holds_shares=_coverable_contracts(shares) >= 1,
            coverable_contracts=_coverable_contracts(shares),
            holds_any_shares=bool(shares and float(shares) > 0),
        ),
        "iv_rank": iv_rank,
        "iv_regime": _iv_regime(iv_rank),
    }


def _constraint_text(coverable: int, holds_any: bool) -> str:
    """SF-03: say what the holding can actually do, including when it is close but short.

    THREE STATES, not two. "You hold no shares" is wrong for someone holding 99 — they hold
    shares, just not enough, and being told they hold none sends them to check the wrong
    thing. `coverable == 0` alone cannot tell those apart, which is why `holds_any` is passed
    separately rather than inferred from it.
    """
    if coverable >= 1:
        return (f"You hold enough stock to cover {coverable} standard "
                f"contract{'s' if coverable != 1 else ''}, so covered calls and collars are "
                f"available.")
    if holds_any:
        return (f"You hold some stock, but not the {_CONTRACT_MULTIPLIER} shares a standard "
                f"contract delivers — covered calls and collars need at least that much "
                f"before they are available to you.")
    return ("You hold no shares, so covered calls, collars and protective puts are not "
            "available — they all require the stock.")


def _coverable_contracts(shares: float | None) -> int:
    """How many standard contracts this holding can actually cover.

    SF-03: deliverable units, not a truthiness test. A standard equity option delivers
    `_CONTRACT_MULTIPLIER` shares, so coverage is a floor division and 99 shares cover zero
    calls — not "some". Fractional holdings floor the same way: a broker will not deliver
    0.4 of a share against an assignment.

    DELIBERATELY NOT MODELLED HERE, and named so nobody assumes otherwise: shares already
    encumbered by existing short calls, adjusted deliverables after a corporate action, and
    settlement state. Those need an account snapshot this pure module does not have, and
    this function must not be mistaken for an account-level eligibility check.
    """
    try:
        n = float(shares or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(n) or n <= 0:
        return 0
    return int(n // _CONTRACT_MULTIPLIER)


def _iv_regime(iv_rank: float | None) -> str:
    if iv_rank is None:
        return "unknown"
    if iv_rank >= _IV_RANK_RICH:
        return "rich"
    if iv_rank <= _IV_RANK_CHEAP:
        return "cheap"
    return "normal"


#: WHICH WAY EACH STRUCTURE LEANS, as a property of the structure rather than a list of names
#: kept somewhere else. Declared at construction beside `requires_shares`, so a structure added
#: later inherits the compatibility rule without anyone remembering to update a table.
#:
#:   bullish        gains when the underlying rises (long call, bull call spread)
#:   bearish        gains when the underlying falls (bear put spread)
#:   income_bullish SELLS premium while carrying LONG exposure. A cash-secured put is the case
#:                  that matters: it reads as "get paid to wait", but the obligation is to BUY
#:                  at the strike, so its risk is a falling stock. It is not a bearish trade and
#:                  must never be offered as one.
#:   income_long    income against stock already held (covered call) — long exposure, capped
#:   hedge_long     reduces the risk of stock already held (protective put, collar)
BULLISH_EXPOSURES = frozenset({"bullish", "income_bullish", "income_long"})
BEARISH_EXPOSURES = frozenset({"bearish"})
HEDGE_EXPOSURES = frozenset({"hedge_long"})


def _exposure_is_compatible(entry: dict, *, bullish: bool, bearish: bool,
                            holds_shares: bool) -> bool:
    """Is this structure's exposure compatible with the stated view?

    O01 (2026-10-08, reproduced by the reviewer). With `signal=SELL`, no shares and IV rank 80,
    `_recommend` returned `cash_secured_put` as the PRIMARY — a structure whose risk is the
    stock falling, offered to someone who had just been told the stock would fall. The ranking
    tested the IV regime before it tested direction, and the final branch treated BEARISH as
    merely "not bullish", lumping it with no-view.

    Compatibility is a CONSTRAINT, like holding enough shares, so it is applied before ranking
    and before any fallback — not expressed as a preference order that a later branch can
    overrule. A bearish view may take a bearish structure, or a hedge of stock actually held,
    and nothing else. Ranking still decides among what survives.
    """
    exposure = entry.get("directional_exposure")
    if exposure is None:
        # A structure that does not declare its exposure cannot be shown to be compatible.
        # Unknown is not permission.
        return False
    if bearish:
        return exposure in BEARISH_EXPOSURES or (exposure in HEDGE_EXPOSURES and holds_shares)
    if bullish:
        return exposure not in BEARISH_EXPOSURES
    # NO STATED VIEW. Everything stays available — the absence of a direction is not evidence
    # for one — but see `_recommend`, which refuses to present a directional structure as a
    # primary recommendation on no view at all.
    return True


def _recommend(*, singles: dict, combos: dict, signal: str | None,
               iv_rank: float | None, holds_shares: bool,
               coverable_contracts: int | None = None,
               holds_any_shares: bool = False) -> dict:
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
        return {"primary": None,
                "reason": "No listed contracts priced well enough to build a structure.",
                "coverable_contracts": coverable_contracts,
                "constraint": _constraint_text(coverable_contracts or 0, holds_any_shares),
                "rejected_unsound": [], "ineligible_for_holding": []}

    # SR-06 (2026-10-02): A STRUCTURE WHOSE BEST CASE IS A LOSS MUST NOT BE RECOMMENDED.
    # This function picked the first structure that fit the constraints and never looked at
    # what it was worth, so the bull call spread with max profit -$500 was offered as the
    # primary plan for a bullish request. Construction-time validation (`_vertical_is_viable`)
    # is the real fix; this is the backstop, and it covers every structure rather than the two
    # the construction test knows about.
    def _payoff_is_sane(entry: dict) -> bool:
        mp = entry.get("max_profit_per_contract")
        return not (isinstance(mp, (int, float)) and mp <= 0)

    _unsound = sorted(k for k, v in available.items() if not _payoff_is_sane(v))
    available = {k: v for k, v in available.items() if _payoff_is_sane(v)}

    # SF-03 RESIDUAL (2026-10-02): ELIGIBILITY MUST CONSTRAIN EVERY PATH, NOT THE ORDERING.
    #
    # The first fix changed the PREFERENCE ORDER for an account without coverage but left
    # ineligible structures in `available`, so the final `next(iter(available))` fallback
    # handed them back anyway. Reviewer's executed witness: spot 100, one share, rich IV, no
    # put chain, the ATM call crossed at 12/2 and the target call valid at 2/2.2. The long
    # call cannot be built, no preferred alternative survives, and the fallback returned
    # `covered_call` — for a holding of one share — with none of the coverage metadata the
    # ordinary path carries.
    #
    # Pricing and eligibility are different questions. "The only structure we could price" is
    # not "a structure this account can hold", and the fallback was answering the first while
    # the reader hears the second. Filtering here, before ranking and before any fallback, is
    # what makes that impossible rather than merely unlikely.
    #
    # Keyed off each structure's OWN `requires_shares` flag rather than a hardcoded list of
    # names, so a structure added later inherits the rule without anyone remembering to.
    def _is_eligible(entry: dict) -> bool:
        return not (entry.get("requires_shares") and (coverable_contracts or 0) < 1)

    _ineligible = sorted(k for k, v in available.items() if not _is_eligible(v))
    available = {k: v for k, v in available.items() if _is_eligible(v)}

    # O01 (2026-10-08): DIRECTIONAL COMPATIBILITY IS A CONSTRAINT, NOT A PREFERENCE.
    # Reviewer's reproduced witness: signal=SELL, no shares, IV rank 80, with valid bearish AND
    # bullish spreads available — primary came back `cash_secured_put`, a structure whose risk
    # is the stock falling, recommended to someone told the stock would fall. The ranking tested
    # the IV regime first and the final branch treated BEARISH as merely "not bullish".
    #
    # Filtered HERE, beside the holdings constraint and before every ranking branch and
    # fallback, which is what makes the contradiction impossible rather than merely unlikely —
    # the same lesson as SF-03 above, where fixing the order left the fallback handing back what
    # the order had demoted.
    _incompatible = sorted(
        k for k, v in available.items()
        if not _exposure_is_compatible(v, bullish=bullish, bearish=bearish,
                                       holds_shares=holds_shares))
    available = {k: v for k, v in available.items()
                 if _exposure_is_compatible(v, bullish=bullish, bearish=bearish,
                                            holds_shares=holds_shares)}

    # Carried on EVERY return path below, including the empty ones — the reviewer found the
    # fallback omitting exactly this, which left the reader with a recommendation and no
    # statement of what their holding could actually support.
    _coverage = {"coverable_contracts": coverable_contracts,
                 "constraint": _constraint_text(coverable_contracts or 0, holds_any_shares),
                 "rejected_unsound": _unsound,
                 # Priced, but not something this holding can carry. Reported rather than
                 # silently dropped: "we found nothing" and "we found something you cannot
                 # use" are different answers and lead to different next steps.
                 "ineligible_for_holding": _ineligible,
                 # Priced and holdable, but pointing the wrong way for the stated view. Named
                 # rather than silently dropped: "nothing fits your view" is a different answer
                 # from "nothing could be priced", and leads somewhere different.
                 "incompatible_with_direction": _incompatible}

    if not available:
        if _incompatible and bearish:
            return {"primary": None,
                    "reason": ("Nothing priced here expresses a downward view that this account "
                               "could hold. The structures available all carry long exposure, "
                               "and offering one of those against a bearish view would "
                               "contradict it — so there is no recommendation, which is the "
                               "honest answer rather than a fallback."),
                    **_coverage}
        if _incompatible:
            return {"primary": None,
                    "reason": ("The structures the chain could price point the wrong way for "
                               "the stated view, so there is no plan here that matches it."),
                    **_coverage}
        if _ineligible:
            return {"primary": None,
                    "reason": ("The only structures the current chain could price require stock "
                               "you do not hold enough of, so there is no plan here you could "
                               "actually put on."),
                    **_coverage}
        return {"primary": None,
                "reason": ("Every structure the current chain could price has a maximum payoff "
                           "of zero or less — there is no valid plan here, which is itself the "
                           "answer."),
                **_coverage}

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
    elif bearish:
        # NO SHARES AND A DOWNWARD VIEW. Only bearish structures survived the compatibility
        # filter, so this orders among those rather than reaching for an income trade. IV still
        # matters for HOW to express it, never for whether to invert the view.
        order = [("bear_put_spread",
                  "You hold no shares and the view is down: the debit put spread expresses that "
                  "directly, with the loss capped at what you pay and the gain capped at your "
                  "own downside target."
                  + (" Premium is rich, so the short leg sells back some of that expense."
                     if regime == "rich" else ""))]
    else:
        # DIRECTION FIRST, THEN IV. The IV regime used to be tested before direction here, which
        # is how a SELL signal reached a cash-secured put.
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

    # O01: WITH NO STATED VIEW, NOTHING HERE IS A VIEW. The game-plan route calls this with
    # `signal=None`, and with rich IV the ranking then leads with a cash-secured put — a
    # structure carrying long exposure. That is a reasonable answer to "what can this account
    # sell premium with"; it is not an answer to "which way is this going", and must not be
    # read as one. Said on the record rather than left to the reader to infer.
    _view = {"stated_direction": ("bullish" if bullish else "bearish" if bearish else None),
             # The label the reader sees. "Structure comparison" says what this IS; a primary
             # recommendation without it reads as an opportunity, and being priceable is not
             # being suitable.
             "primary_label": (None if (bullish or bearish)
                               else "Structure comparison — direction not assessed"),
             "direction_basis": (
                 None if (bullish or bearish) else
                 "No directional view was supplied, so this ranks on your holdings and on where "
                 "IV sits — not on where the price is going. A structure being priceable is not "
                 "the same as an opportunity being suitable; read this as what this account "
                 "could structure, not as a case for a direction.")}

    for key, why in order:
        got = pick(key, why)
        if got:
            alts = [{"key": k, "name": available[k]["name"], "reason": w}
                    for k, w in order if k != key and k in available]
            return {**got, "iv_regime": regime, "iv_note": iv_note, "alternatives": alts,
                    **_view, **_coverage}

    # SF-03 RESIDUAL: `available` is now eligibility-filtered above, so this fallback can only
    # return something the holding can actually support. It still carries the coverage
    # metadata, which it previously dropped. O01 adds the direction filter upstream, so it also
    # cannot return something that contradicts a stated view.
    key = next(iter(available))
    return {"primary": key, "name": available[key]["name"],
            "reason": "The only structure that could be priced from the currently-listed chain.",
            "iv_regime": regime, "iv_note": iv_note, "alternatives": [],
            **_view, **_coverage}

"""Position sizing — Kelly base × multipliers.

T232-DL-DUALSCORER (corrected 2026-07-04): this docstring previously claimed to mirror
paper_trading_engine._scan_for_entries()'s sizing formula "exactly" — that has never been
true and isn't the current design intent either. Concretely: the confidence-multiplier
breakpoints here are deliberately rescaled (see the T232-DE2 comment below) to sit above
this service's own higher confidence floor, which is a different scale than
_scan_for_entries()'s bands; HMM bear-pressure dampening exists only in
paper_trading_engine.py, never here; and the earnings multiplier doesn't compound into the
max-position-pct cap the way it does in paper_trading_engine.py. Treat this as a related but
INDEPENDENT sizing model, not a mirror — see docs/AUDIT_REPORT_TIER232_2026-07-02.md Part 10
for the full itemized diff before assuming a value here matches the real trading engine.

AUD232-034/035/036/038/039/040/041 (audit pass 2026-07-10): confirmed 6 MORE divergences
beyond the ones already listed above — this is a preview/scoring-only module (paper_trading
never consumes its sizing plan, only its go/no-go verdict + score), so none of these are bugs
to reconcile, just further evidence this module's numbers should never be read as "what the
real engine would size":
  1. (AUD232-034) stop_dist here is floored at 1% of price (T237-DE1, see compute_position()
     below); paper_trading_engine.py has no equivalent floor and relies entirely on downstream
     caps instead.
  2. (AUD232-035/036) combined_market_mult() below uses min() across regime/breadth/vix;
     paper_trading_engine.py instead multiplies ALL 6 of its per-trade multipliers together
     AND applies its own 25% floor that has no equivalent here.
  3. (AUD232-038) the confidence-multiplier tier tables (see the T232-DE2 comment below) differ
     from paper_trading_engine.py's own bands in both breakpoints and whether a floor-rescale
     is applied.
  4. max_position_pct's interaction with a min_position_value skip-check exists in
     paper_trading_engine.py but has no equivalent gate here.
  5. (AUD232-039) no HMM bear-pressure dampening parameter exists in compute_position()'s
     signature at all — paper_trading_engine.py applies min(regime_size_mult, 0.70) whenever
     live_regime["hmm_bear_pressure"] is set; there is no third/fourth market_mult term here for
     it. A permanent, accepted gap — see combined_market_mult()'s own docstring for why adding
     a 4th term to that min() wouldn't even be the right fix if this WAS ever wired to real sizing.
  6. (AUD232-041) this module has no K-score/ranking-engine input anywhere in its formula, so it
     cannot replicate any DE-score-based sizing decision — confirmed to be a non-issue in
     practice since paper_trading_engine.py's own score_size_mult formula also has no direct
     K-score multiplier (K-score is a pre-entry gate, not a size multiplier, on both sides).
"""
from __future__ import annotations

from .models import Multipliers, PositionPlan

# Regime → size multiplier
_REGIME_MULT = {
    "bull":     1.00,
    "neutral":  1.00,
    "choppy":   0.75,
    "risk_off": 0.50,
    "bear":     0.00,
}

# Research recommendation → size multiplier
_RESEARCH_MULT = {
    "STRONG BUY": 1.20,
    "BUY":        1.00,
    "WATCH":      0.80,
    "AVOID":      0.60,
    "SELL":       0.60,
}


def combined_market_mult(regime_mult: float, breadth_mult: float, vix_mult: float) -> float:
    """T232-DE1: regime, breadth, and vix multipliers are NOT independent — all three
    ultimately describe "how dangerous is the broad market right now" (regime_mult is itself
    partly derived from VIX), so multiplying them together double/triple-counts the same
    signal. Composed via min() instead — take the single most conservative market-wide signal.

    AUD232-053: extracted so routes.py's micro-position skip-check (which needs the same
    combined value from an already-computed Multipliers object) can call this instead of
    re-deriving the identical min() expression inline — a small duplication, but one that
    could silently diverge if this formula ever changed in only one of the two places.
    """
    return min(regime_mult, breadth_mult, vix_mult)


def compute_position(
    equity: float,
    live_price: float,
    game_plan: dict,
    confidence: float,
    research_rec: str | None,
    research_score_val: float | None,
    regime_state: str,
    cross_style_buys: int,
    days_to_earnings: int | None,
    cfg: dict,
    breadth_size_mult: float = 1.0,
    vix_size_mult: float = 1.0,
) -> tuple[PositionPlan, Multipliers]:
    """Return (PositionPlan, Multipliers)."""

    stop_price  = game_plan.get("stop",       live_price * 0.880)
    take_profit = game_plan.get("take_profit", live_price * 1.350)
    target_1    = game_plan.get("target_1",   live_price + (take_profit - live_price) * 0.5)
    target_2    = take_profit

    # T237-DE1: an absolute 1-cent floor meant an invalid game plan (stop_price >= live_price —
    # no real downside protection at all) produced a fake, tiny stop_dist, which fed into
    # dollar_risk = stop_dist * shares as a misleadingly small "risk" figure shown directly to
    # the user (decide response text, research page Dollar Risk field) — the real risk on such
    # a plan is undefined/unbounded, not a few cents. Floor at a percentage of price instead so
    # a degenerate stop still produces a dollar_risk in the right order of magnitude rather
    # than an implausibly reassuring near-zero value. The equity-based max_pos_value cap below
    # already prevents this from translating into an oversized real position either way.
    stop_dist   = max(live_price - stop_price, live_price * 0.01)
    rr          = (take_profit - live_price) / stop_dist

    # ── Multipliers ────────────────────────────────────────────────────────────

    regime_mult = _REGIME_MULT.get(regime_state, 1.0)

    # DE-SIZER1: STRONG BUY/BUY previously had no "score gate failed" branch — unlike WATCH
    # (which falls to 0.60 below when it misses its own >=60 gate), a STRONG BUY at score 70
    # (below its 75 gate) or a BUY at score 50 (below its 65 gate) fell through every elif and
    # landed on the generic `else: research_mult = 1.00` — identical to having NO research
    # coverage at all, silently un-penalizing exactly the weak-recommendation case this gating
    # logic exists to catch. Matches WATCH's existing pattern: recommendation present but
    # doesn't clear its confidence bar → 0.60, not a silent no-op back to neutral.
    rec_upper = (research_rec or "").upper().replace("_", " ")
    if rec_upper == "STRONG BUY" and (research_score_val or 0) >= 75:
        research_mult = 1.20
    elif rec_upper == "BUY" and (research_score_val or 0) >= 65:
        research_mult = 1.00
    elif rec_upper == "WATCH" and (research_score_val or 0) >= 60:
        research_mult = 0.80
    elif rec_upper in ("STRONG BUY", "BUY", "WATCH", "AVOID", "SELL"):
        research_mult = 0.60
    else:
        research_mult = 1.00

    # Confidence sizing (PT-D2): 0–100 confidence from signal engine.
    # T232-DE2: the hard-reject floor in hard_rejects.py is min_confidence(62) * 0.90 = 55.8,
    # so every trade that reaches this function already has confidence >= 55.8 — the old
    # `>= 50` branch always fired (every position silently 25% oversized with zero variation
    # by conviction) and the 30-49 / <30 tiers below the floor were unreachable dead code.
    # Rescaled to sit entirely above the floor so the tiers are actually reachable.
    if confidence >= 80:
        confidence_mult = 1.25
    elif confidence >= 62:
        confidence_mult = 1.00
    else:
        confidence_mult = 0.85

    # Cross-horizon consensus (40-B)
    if cross_style_buys >= 2:
        consensus_mult = 1.15
    elif cross_style_buys == 1:
        consensus_mult = 1.07
    else:
        consensus_mult = 1.00

    # Earnings proximity reduction
    earnings_mult = 1.0
    if days_to_earnings is not None:
        dte = int(days_to_earnings)
        if 6 <= dte <= 10:
            earnings_mult = 0.50
        elif 11 <= dte <= 20:
            earnings_mult = 0.75

    mults = Multipliers(
        regime=regime_mult,
        research=research_mult,
        confidence=confidence_mult,
        consensus=consensus_mult,
        earnings=earnings_mult,
        breadth=breadth_size_mult,
        vix=vix_size_mult,
    )

    # ── Share calculation ──────────────────────────────────────────────────────

    # T232-DE1: at VIX=30, 0.50 (regime) x 0.667 (vix gradient) = 0.335 combined, when the
    # intent is a single "how bad is it" dampening of 0.50 — see combined_market_mult()'s
    # docstring for the full reasoning. Idiosyncratic per-trade signals (research/confidence/
    # consensus/earnings) are genuinely independent judgments about THIS trade and remain
    # multiplied together.
    market_mult = combined_market_mult(regime_mult, breadth_size_mult, vix_size_mult)
    risk_per_trade = cfg.get("risk_per_trade_pct", 0.01)
    risk_dollar = (
        equity * risk_per_trade
        * market_mult
        * earnings_mult * confidence_mult * research_mult * consensus_mult
    )
    shares = risk_dollar / stop_dist

    # Max dollar loss cap (PA-C1)
    max_loss_pct = cfg.get("max_loss_per_trade_pct", 0.02)
    if max_loss_pct and equity > 0:
        max_loss = equity * max_loss_pct
        if stop_dist * shares > max_loss:
            shares = max_loss / stop_dist

    # Max position size cap (earnings_mult already applied via risk_dollar above)
    max_pos_pct = cfg.get("max_position_pct", 0.10)
    max_pos_value = equity * max_pos_pct
    position_value = shares * live_price
    if position_value > max_pos_value:
        shares = max_pos_value / live_price

    shares = round(max(shares, 0), 4)
    position_value = round(shares * live_price, 2)
    size_pct = round(position_value / equity, 4) if equity > 0 else 0.0
    dollar_risk = round(stop_dist * shares, 2)

    plan = PositionPlan(
        shares=shares,
        size_pct=size_pct,
        dollar_risk=dollar_risk,
        entry_price=round(live_price, 4),
        stop_price=round(stop_price, 4),
        target_1=round(target_1, 4),
        target_2=round(target_2, 4),
        rr_ratio=round(rr, 2),
    )
    return plan, mults

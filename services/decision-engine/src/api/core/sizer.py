"""Position sizing — Kelly base × four independent multipliers.

Mirrors the sizing formula in paper_trading_engine._scan_for_entries() exactly so
paper trading and future live execution produce identical position sizes.
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
) -> tuple[PositionPlan, Multipliers]:
    """Return (PositionPlan, Multipliers)."""

    stop_price  = game_plan.get("stop",       live_price * 0.880)
    take_profit = game_plan.get("take_profit", live_price * 1.350)
    target_1    = game_plan.get("target_1",   live_price + (take_profit - live_price) * 0.5)
    target_2    = take_profit

    stop_dist   = max(live_price - stop_price, 0.01)
    rr          = (take_profit - live_price) / stop_dist

    # ── Multipliers ────────────────────────────────────────────────────────────

    regime_mult = _REGIME_MULT.get(regime_state, 1.0)

    rec_upper = (research_rec or "").upper().replace("_", " ")
    if rec_upper == "STRONG BUY" and (research_score_val or 0) >= 75:
        research_mult = 1.20
    elif rec_upper == "BUY" and (research_score_val or 0) >= 65:
        research_mult = 1.00
    elif rec_upper == "WATCH" and (research_score_val or 0) >= 60:
        research_mult = 0.80
    elif rec_upper in ("WATCH", "AVOID", "SELL"):
        research_mult = 0.60
    else:
        research_mult = 1.00

    # Confidence sizing (PT-D2): 0–100 confidence from signal engine
    if confidence >= 50:
        confidence_mult = 1.25
    elif confidence >= 30:
        confidence_mult = 1.00
    else:
        confidence_mult = 0.75

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
    )

    # ── Share calculation ──────────────────────────────────────────────────────

    risk_per_trade = cfg.get("risk_per_trade_pct", 0.01)
    risk_dollar = (
        equity * risk_per_trade
        * earnings_mult * regime_mult * confidence_mult * research_mult * consensus_mult
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

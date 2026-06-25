"""Hard-reject checks — fire before scoring and return BLOCKED immediately."""
from __future__ import annotations


def check_hard_rejects(
    signal_direction: str,
    confidence: float,
    live_price: float,
    stop_price: float,
    take_profit: float,
    regime_state: str,
    days_to_earnings: int | None,
    open_positions: int,
    max_positions: int,
    daily_pnl_pct: float,
    cfg: dict,
    research_rec: str | None = None,
    game_plan: dict | None = None,
) -> str | None:
    """Return a human-readable reject reason, or None if all checks pass."""

    if signal_direction.upper() != "BUY":
        return f"Signal direction is {signal_direction} — only BUY signals evaluated for entry"

    if cfg.get("research_gating_enabled") and research_rec in ("AVOID", "SELL"):
        return f"Research recommendation is {research_rec} — gated until outlook improves"

    if regime_state == "bear":
        return "Bear regime — all long entries blocked"

    if open_positions >= max_positions:
        return f"Portfolio full ({open_positions}/{max_positions} positions)"

    max_daily_loss = cfg.get("max_daily_loss_pct", 0.04)
    if daily_pnl_pct <= -abs(max_daily_loss):
        return f"Daily loss limit hit ({daily_pnl_pct*100:.1f}% ≤ -{max_daily_loss*100:.0f}%)"

    min_conf     = cfg.get("min_confidence", 62.0)
    hard_floor   = min_conf * 0.90
    if confidence < hard_floor:
        return f"Confidence {confidence:.1f}% below hard floor {hard_floor:.1f}%"

    stop_dist    = live_price - stop_price
    min_stop_dist = max(live_price * 0.005, 0.05)
    if stop_dist <= 0:
        return f"Stop ${stop_price:.2f} is above price ${live_price:.2f} — invalid setup"
    if stop_dist < min_stop_dist:
        return (
            f"Stop ${stop_price:.2f} too close to price ${live_price:.2f} "
            f"(distance ${stop_dist:.4f} < min ${min_stop_dist:.4f})"
        )

    rr = (take_profit - live_price) / stop_dist
    min_rr = cfg.get("min_rr_ratio", 2.0)
    if rr < min_rr:
        return f"R:R {rr:.2f}:1 below minimum {min_rr:.1f}:1"

    if days_to_earnings is not None and days_to_earnings <= 5:
        return f"Earnings in {days_to_earnings} days — binary event risk"

    # Extended-move guard: stock is >6% above the breakout level the signal was
    # calibrated to. A human trader waits for a pullback rather than chasing.
    if game_plan:
        breakout = game_plan.get("breakout")
        if breakout and float(breakout) > 0:
            ext_pct = (live_price / float(breakout) - 1) * 100
            threshold = cfg.get("max_breakout_extension_pct", 6.0)
            if ext_pct > threshold:
                return (
                    f"Stock {ext_pct:.1f}% above breakout ${breakout:.2f} — "
                    f"extended move, wait for pullback (threshold {threshold:.0f}%)"
                )

    return None

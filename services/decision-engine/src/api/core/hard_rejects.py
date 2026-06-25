"""Hard-reject checks — fire before scoring and return BLOCKED immediately."""
from __future__ import annotations

from datetime import datetime, timezone


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
    market: str = "US",
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

    # T186: Sector concentration — block if too many open positions already in this sector.
    # open_sector_counts and candidate_sector are passed by paper_trading_engine per candidate.
    _open_sector_counts = cfg.get("open_sector_counts")
    _candidate_sector = cfg.get("candidate_sector")
    if _open_sector_counts is not None and _candidate_sector is not None:
        _sector_key = _candidate_sector or "unclassified"
        _sector_existing = int(_open_sector_counts.get(_sector_key, 0))
        _max_sector = int(cfg.get("max_sector_positions", 3))
        if _sector_existing >= _max_sector:
            return (
                f"Sector concentration: {_sector_existing}/{_max_sector} open positions already "
                f"in '{_sector_key}' — diversification gate"
            )

    # T187: Consecutive loss cooldown — too many straight losses means the market is not
    # behaving as expected; entries suspended until next winning trade.
    _consec_losses = int(cfg.get("consec_losses", 0))
    _max_consec = int(cfg.get("max_consecutive_losses", 3))
    if _consec_losses > 0 and _consec_losses >= _max_consec:
        return (
            f"Consecutive loss cooldown: {_consec_losses} straight losses — "
            f"entries suspended until next winning trade"
        )

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

    # T185: Time-of-day gate — human traders avoid the first 30 min (price discovery, wide spreads)
    # and last 15 min (closing auction games) of the market session.
    try:
        from zoneinfo import ZoneInfo as _ZI
        _tz = _ZI("America/New_York") if market.upper() != "HK" else _ZI("Asia/Hong_Kong")
        _local = datetime.now(timezone.utc).astimezone(_tz)
        _mins = _local.hour * 60 + _local.minute
        if 570 <= _mins < 600:
            return (
                f"Time-of-day gate: first 30 min of market open — "
                f"price discovery in progress ({_local.strftime('%H:%M')} local)"
            )
        if 945 <= _mins < 960:
            return (
                f"Time-of-day gate: last 15 min before close — "
                f"avoid closing auction risk ({_local.strftime('%H:%M')} local)"
            )
    except Exception:
        pass  # tz lookup failure → allow entry

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

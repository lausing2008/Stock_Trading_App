"""Hard-reject checks — fire before scoring and return BLOCKED immediately."""
from __future__ import annotations

from datetime import date, datetime, timezone

# QW-4: NYSE holidays — market-closed guard would block weekends but not holidays.
# Update annually or replace with a market-calendar library.
_NYSE_HOLIDAYS: frozenset[date] = frozenset({
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})


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
    reasons: dict | None = None,
) -> str | None:
    """Return a human-readable reject reason, or None if all checks pass."""

    if signal_direction.upper() != "BUY":
        return f"Signal direction is {signal_direction} — only BUY signals evaluated for entry"

    # T193: Market-closed guard — block entries when the exchange is not open for regular trading.
    # Complements T185 (session edge). Catches weekends, pre-market, after-hours, and HK lunch.
    try:
        from zoneinfo import ZoneInfo as _ZI
        _tz = _ZI("America/New_York") if market.upper() != "HK" else _ZI("Asia/Hong_Kong")
        _local = datetime.now(timezone.utc).astimezone(_tz)
        _wd = _local.weekday()  # 0=Mon … 4=Fri, 5=Sat, 6=Sun
        if _wd >= 5:
            return f"Market closed: weekend ({_local.strftime('%A %H:%M')} local)"
        if market.upper() != "HK" and _local.date() in _NYSE_HOLIDAYS:
            return f"Market closed: NYSE holiday ({_local.strftime('%Y-%m-%d')})"
        _mins = _local.hour * 60 + _local.minute
        if market.upper() == "HK":
            # HK: morning 9:30–12:00, afternoon 13:00–16:00
            if not (570 <= _mins < 720 or 780 <= _mins < 960):
                return (
                    f"Market closed: HK exchange not in trading session "
                    f"({_local.strftime('%H:%M')} HKT)"
                )
        else:
            # US: 9:30–16:00 ET
            if not (570 <= _mins < 960):
                return (
                    f"Market closed: US exchange not in trading session "
                    f"({_local.strftime('%H:%M')} ET)"
                )
    except Exception:
        pass  # tz lookup failure → allow entry (fail-open)

    if cfg.get("research_gating_enabled") and research_rec in ("AVOID", "SELL"):
        return f"Research recommendation is {research_rec} — gated until outlook improves"

    if regime_state == "bear":
        return "Bear regime — all long entries blocked"

    if open_positions >= max_positions:
        return f"Portfolio full ({open_positions}/{max_positions} positions)"

    max_daily_loss = cfg.get("max_daily_loss_pct", 0.04)
    if daily_pnl_pct <= -abs(max_daily_loss):
        return f"Daily loss limit hit ({daily_pnl_pct*100:.1f}% ≤ -{max_daily_loss*100:.0f}%)"

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
    # T190: In choppy/risk_off regimes human traders demand better setups — require higher R:R.
    if regime_state in ("choppy", "risk_off"):
        min_rr = max(min_rr, cfg.get("regime_min_rr_ratio", 3.0))
    if rr < min_rr:
        return f"R:R {rr:.2f}:1 below minimum {min_rr:.1f}:1"

    if days_to_earnings is not None and days_to_earnings <= 5:
        return f"Earnings in {days_to_earnings} days — binary event risk"

    # T234-DE-MISSING-HARD-REJECTS: ported from paper_trading_engine.py's _should_enter()
    # fallback (the "primary" DE gate was missing these two unconditional hard rejects that
    # the fallback path enforces, making the normally-active gate looser than the outage-only
    # fallback — backwards from intended). Both use signal.reasons, same as the fallback.
    _reasons = reasons or {}

    # T171: Premarket gap filter — reject if price has already gapped up significantly
    # from its signal-time close. reasons["last_price"] is the close at signal-compute time.
    _signal_close = _reasons.get("last_price")
    if _signal_close and float(_signal_close) > 0:
        _gap = live_price / float(_signal_close) - 1
        _max_gap = cfg.get("max_entry_gap_pct", 0.04)
        if _gap > _max_gap:
            return (
                f"Gap-up {_gap:.1%} above signal close ${_signal_close:.2f} "
                f"exceeds limit {_max_gap:.0%} — entry price degraded"
            )

    # T220-D: Economic calendar blackout — reject BUY entries within 2h of major macro events.
    # Checks reasons["macro_blackout"] first (fast path — set by signal-engine), then queries
    # DB directly, matching the fallback's fail-open-on-error behavior.
    _macro_evt = _reasons.get("macro_blackout")
    if _macro_evt is None:
        try:
            from db import SessionLocal
            from sqlalchemy import text
            from datetime import timedelta
            _now = datetime.now(timezone.utc)
            _window_end = _now + timedelta(hours=2)
            with SessionLocal() as _evsess:
                _ev_row = _evsess.execute(text(
                    "SELECT title FROM economic_events "
                    "WHERE event_date >= :now AND event_date <= :end "
                    "AND importance IN ('high', 'critical') "
                    "LIMIT 1"
                ), {"now": _now.isoformat(), "end": _window_end.isoformat()}).fetchone()
                if _ev_row:
                    _macro_evt = _ev_row.title
        except Exception:
            pass  # DB query failure → allow entry (fail-open), matching the fallback
    if _macro_evt:
        return f"Macro blackout: {_macro_evt} within 2h — avoid binary-event risk"

    # T232-DL-DUALSCORER: the caller (paper_trading_engine._call_decision_engine) sends
    # open_sector_counts/candidate_sector inside config_overrides, but this function never read
    # them — DE had zero sector-concentration protection despite the caller believing it was
    # providing that data. Only the COUNT-based cap can be reconciled here (the real engine's
    # dollar-exposure cap, max_sector_pct, needs live per-position prices this endpoint never
    # receives) — mirrors paper_trading_engine's max_sector_positions check exactly.
    candidate_sector = cfg.get("candidate_sector")
    open_sector_counts = cfg.get("open_sector_counts")
    if candidate_sector and isinstance(open_sector_counts, dict):
        max_sector_positions = int(cfg.get("max_sector_positions", 3))
        sector_count = int(open_sector_counts.get(candidate_sector, 0))
        if sector_count >= max_sector_positions:
            return (
                f"Sector position-count cap reached: {candidate_sector} has "
                f"{sector_count}/{max_sector_positions} open positions"
            )

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

"""Hard-reject checks — fire before scoring and return BLOCKED immediately."""
from __future__ import annotations

import json
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
    equity: float | None = None,
    initial_capital: float | None = None,
    research_rec: str | None = None,
    game_plan: dict | None = None,
    market: str = "US",
    reasons: dict | None = None,
    symbol: str | None = None,
    style: str | None = None,
    sig_ts=None,
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

    # T232-DL-DUALSCORER-DEBT / T201: _scan_for_entries' equity-floor circuit breaker —
    # suspends ALL new entries once the portfolio's equity has dropped below equity_floor_pct
    # (default 80%) of its starting capital. A portfolio-wide gate, not per-symbol scoring,
    # so it belongs alongside the bear-regime check above rather than the per-symbol checks
    # below. `equity` was already sent to decision-engine (used by sizer.py's illustrative
    # position-sizing preview) but `initial_capital` was never sent at all — both are needed
    # for the ratio, so this required a genuine new DecisionRequest field, not a free port.
    _equity_floor_pct = float(cfg.get("equity_floor_pct", 0.80))
    if (
        _equity_floor_pct > 0
        and equity is not None
        and initial_capital is not None
        and initial_capital > 0
        and (equity / initial_capital) < _equity_floor_pct
    ):
        return (
            f"Account equity {equity / initial_capital * 100:.1f}% of starting capital, "
            f"below the {_equity_floor_pct * 100:.0f}% floor — all new entries suspended (T201)"
        )

    # T232-DL-DUALSCORER-DEBT: Index-trend HARD REJECT (T221), ported from
    # paper_trading_engine.py's _scan_for_entries() (index_return_pct < index_trend_gate_pct,
    # default -1.5%). Genuinely distinct from the regime_state=="bear" check just above —
    # regime is a SUSTAINED multi-day classification; this is a single-day macro-shock catch
    # (FOMC surprise, CPI print, HSI circuit-breaker open) where any long entry immediately
    # fights the tide, even in an otherwise-bull/choppy regime that hasn't reclassified yet.
    # Placed here (market-wide, before any per-symbol gate) rather than alongside the
    # reasons-derived gates below, since — unlike every other gate in this file — this one is
    # purely a function of (market, index_return), with zero per-symbol/per-portfolio state.
    # UNLIKE min_kscore/min_ta_score/HK-flow/low-volume, this value was never already flowing
    # to decision-engine anywhere (not in sig.reasons, not in /stocks/regime) — a genuine
    # write-side change on paper_trading_engine.py's side, not a free port; both
    # index_return_pct and index_trend_gate_pct are only present when the caller supplies a
    # real measured value (see paper_trading_engine.py's config_overrides), matching this
    # file's established optional-parameter fail-open convention exactly.
    if cfg.get("index_trend_gate_pct") is not None:
        _idx_ret_val = cfg.get("index_return_pct")
        if _idx_ret_val is not None and float(_idx_ret_val) < float(cfg["index_trend_gate_pct"]):
            return (
                f"Index down {abs(float(_idx_ret_val))*100:.1f}% today, exceeds "
                f"{abs(float(cfg['index_trend_gate_pct']))*100:.1f}% threshold "
                f"— macro shock, no new entries (T221)"
            )

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

    # T234-CONFIG-DECIDE-DEFAULT-MISMATCH: this 62.0 fallback is a disconnected literal with
    # no relation to the real per-style/market value (SWING=50/HK=65, LONG=40, etc. — see
    # paper_trading_engine.py's resolve_entry_gate_params()). It's effectively unreachable in
    # production now — routes.py's _decide() always resolves and fills in the real value into
    # cfg via aget_entry_gate_params() before this function is ever called — kept only as a
    # safety net for a direct caller (e.g. a test) that constructs cfg without that key at all.
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

    # T232-DL-DUALSCORER-DEBT / T226-A: paper_trading_engine.py's _scan_for_entries() blocks
    # ALL new entries outright in a risk_off regime (data-backed: 9/30 real closed paper
    # trades entered during risk_off had a 0% win rate) — decision-engine only had the SOFT
    # R:R-stiffening check above (T190), never a hard block, so /decide/{symbol} could still
    # approve an entry a risk_off regime should categorically block whenever a candidate's
    # R:R happened to clear the raised bar. A time-boxed override (regime_risk_off_override_
    # until) mirrors the fallback engine's own POST /paper-portfolio/risk-off-override
    # mechanism exactly — an ISO-format expiry timestamp in cfg, checked fresh on every call
    # so it self-expires without any cron job clearing it.
    if regime_state == "risk_off" and cfg.get("regime_risk_off_gate", True):
        _override_until = cfg.get("regime_risk_off_override_until")
        _override_active = False
        if _override_until:
            try:
                _override_active = datetime.utcnow() < datetime.fromisoformat(_override_until)
            except (ValueError, TypeError):
                _override_active = False
        if not _override_active:
            return "Risk-off regime — no new entries until regime improves (T226-A)"

    if days_to_earnings is not None and days_to_earnings <= 5:
        return f"Earnings in {days_to_earnings} days — binary event risk"

    # T234-CONFIG-UNJUSTIFIED-THRESHOLDS: T222-C signal-staleness HARD REJECT, ported from
    # paper_trading_engine.py's _scan_for_entries() (max_signal_age_hours, default 72h/3 days).
    # This is genuinely NOT the same threshold as this file's own Layer-3e-equivalent soft
    # freshness scoring elsewhere in decision-engine (scorer.py's 4h/18h SA-24 bands, which
    # already correctly mirror _should_enter()'s own soft scoring) — T222-C is a separate,
    # earlier, HARD cutoff in the pipeline that decision-engine had no equivalent of at all,
    # making /decide/{symbol} silently accept an arbitrarily-stale signal that
    # paper_trading_engine would have filtered out entirely before ever reaching a scorer.
    if sig_ts is not None:
        try:
            if isinstance(sig_ts, str):
                _ts_aware = datetime.fromisoformat(sig_ts.replace("Z", "+00:00"))
            else:
                _ts_aware = sig_ts.replace(tzinfo=timezone.utc) if sig_ts.tzinfo is None else sig_ts
            _sig_age_h = (datetime.now(timezone.utc) - _ts_aware).total_seconds() / 3600
            _max_age_h = float(cfg.get("max_signal_age_hours", 72))
            if _sig_age_h > _max_age_h:
                return f"Signal is {_sig_age_h:.1f}h old, exceeds max age {_max_age_h:.0f}h — stale, discard thesis"
        except Exception:
            pass  # malformed ts → fail-open, matching every other gate in this function

    # T232-DL-DUALSCORER-DEBT: K-Score floor HARD REJECT, ported from
    # paper_trading_engine.py's _scan_for_entries() (min_kscore, per-style default 48-52).
    # Genuinely distinct from this file's own AUD232-042 soft K-Score SCORE layer elsewhere in
    # this function (scorer.py's ±1 for kscore >=/< 55) — that layer nudges the score but never
    # blocks outright. min_kscore is a separate, EARLIER hard pre-filter in _scan_for_entries
    # that discards a candidate entirely before it's ever scored — decision-engine had no
    # equivalent, so /decide/{symbol} could approve a candidate _scan_for_entries would have
    # discarded before ever calling a scorer, for any caller that doesn't replicate that
    # pre-filter itself (e.g. decide.tsx). cfg["min_kscore"] is only present when the caller
    # also sent a real kscore (see paper_trading_engine.py's config_overrides) — absent for any
    # older caller not yet passing it, matching this function's established optional-parameter
    # fail-open convention elsewhere (symbol/style/sig_ts above).
    if cfg.get("min_kscore") is not None:
        _kscore_val = cfg.get("kscore")
        if _kscore_val is not None and float(_kscore_val) < float(cfg["min_kscore"]):
            return (
                f"K-Score {float(_kscore_val):.0f} below minimum {float(cfg['min_kscore']):.0f} "
                f"— fundamental/momentum quality gate not met"
            )

    # T232-DL-DUALSCORER-DEBT: TA-score floor HARD REJECT, ported from
    # paper_trading_engine.py's _scan_for_entries() (T224-C/T225-A min_ta_score — 0.0/disabled
    # by _DEFAULT_CONFIG's own absence of the key, 0.50 for SWING via _STYLE_OVERRIDES, 0.65 for
    # HK via _HK_MARKET_OVERRIDES). Same shape as the min_kscore gate immediately above: an
    # EARLIER hard pre-filter in _scan_for_entries that discards a candidate before it's ever
    # scored, with no equivalent in decision-engine at all before this — /decide/{symbol} could
    # approve a candidate _scan_for_entries would have discarded, for any caller that doesn't
    # replicate that pre-filter itself (e.g. decide.tsx). cfg["min_ta_score"] is only present
    # when the caller also sent a real ta_score (see paper_trading_engine.py's
    # config_overrides), matching this function's established optional-parameter fail-open
    # convention. A min_ta_score of 0.0 (the gate's own disabled state upstream) never rejects,
    # since ta_score can't be below 0.0 — matches _scan_for_entries' own `_min_ta > 0` no-op check.
    if cfg.get("min_ta_score") is not None:
        _ta_val = cfg.get("ta_score")
        if _ta_val is not None and float(_ta_val) < float(cfg["min_ta_score"]):
            return (
                f"TA score {float(_ta_val):.2f} below minimum {float(cfg['min_ta_score']):.2f} "
                f"— technical-analysis quality gate not met"
            )

    # T232-DL-DUALSCORER-DEBT: Declining-confidence HARD REJECT (T202), ported from
    # paper_trading_engine.py's _scan_for_entries() (max_confidence_decline, default -8.0).
    # Genuinely distinct from this file's own SA-26 soft ±1 confidence-trajectory SCORE layer
    # in scorer.py (nudges the score at the same ±8 boundary but never blocks outright) —
    # max_confidence_decline is a separate, EARLIER hard pre-filter in _scan_for_entries that
    # discards a candidate whose confidence has fallen since its prior signal before it's ever
    # scored — decision-engine had no equivalent, so /decide/{symbol} could approve a degrading
    # setup _scan_for_entries would have discarded, for any caller that doesn't replicate that
    # pre-filter itself (e.g. decide.tsx). cfg["max_confidence_decline"] is only present when
    # the caller also sent a real confidence_delta (see paper_trading_engine.py's
    # config_overrides), matching this function's established optional-parameter fail-open
    # convention. UNLIKE min_kscore/min_ta_score (positive floors, value < min blocks), this
    # threshold is NEGATIVE and the gate blocks when the delta falls BELOW it — do not treat
    # the comparison direction like a positive floor.
    if cfg.get("max_confidence_decline") is not None:
        _conf_delta_val = cfg.get("confidence_delta")
        if _conf_delta_val is not None and float(_conf_delta_val) < float(cfg["max_confidence_decline"]):
            return (
                f"Confidence declined {float(_conf_delta_val):.1f} pts since prior signal, "
                f"exceeds max decline {float(cfg['max_confidence_decline']):.1f} pts "
                f"— setup degrading, wait for stabilisation"
            )

    # T234-DE-MISSING-HARD-REJECTS: ported from paper_trading_engine.py's _should_enter()
    # fallback (the "primary" DE gate was missing these two unconditional hard rejects that
    # the fallback path enforces, making the normally-active gate looser than the outage-only
    # fallback — backwards from intended). Both use signal.reasons, same as the fallback.
    _reasons = reasons or {}

    # T232-DL-DUALSCORER-DEBT: HK Stock-Connect mainland-flow HARD REJECT (T224-A), ported
    # from paper_trading_engine.py's _scan_for_entries() (HK entries require positive 5-day
    # southbound flow; flow_5d_net_hkd <= 0 means mainland money is net-selling the stock —
    # bearish pressure). Unlike min_kscore/min_ta_score/max_confidence_decline, this required
    # ZERO write-side threading — sig.reasons (which already carries flow_5d_net_hkd when
    # present) is already sent to decision-engine wholesale as the request's "reasons" field,
    # so _reasons (built just above, for T171/T220-D) already has this value in scope. Fail-
    # open if the field is absent (not all stocks are Stock Connect eligible), matching the
    # fallback's own behavior exactly. HK-only, using this function's own `market` parameter
    # (not cfg.get("market") — market is already threaded here directly, unlike cfg-only gates).
    if market.upper() == "HK":
        _flow5d = _reasons.get("flow_5d_net_hkd")
        if _flow5d is not None and float(_flow5d) <= 0:
            return (
                f"HK mainland outflow: 5d net flow {float(_flow5d):,.0f} HKD <= 0 "
                f"— Stock Connect southbound selling pressure (T224-A)"
            )

    # T232-DL-DUALSCORER-DEBT: Low-volume HARD REJECT (T200, min_volume_z default -1.5),
    # ported from paper_trading_engine.py's _scan_for_entries(). Genuinely distinct from this
    # file's own pre-existing SOFT volume-z SCORE layer in scorer.py (Layer 3a: +1 above
    # z=1.0, -1 below z=-0.5, 0 in between) — that layer nudges the score but never blocks
    # outright, and its -0.5 mild-penalty band is a materially looser bar than this gate's
    # -1.5 hard floor. T200 is a separate, earlier hard pre-filter in _scan_for_entries that
    # discards a thin-market candidate entirely before it's ever scored — decision-engine had
    # no equivalent, so /decide/{symbol} could approve an entry against abnormally low volume
    # (higher slippage/exit risk) the fallback gate would reject. Same zero-write-side-
    # threading shape as the HK flow gate above — sig.reasons already carries volume_z when
    # present (T232-DL5: a missing value must fail OPEN, not be treated as 0/average, which
    # would silently pass the gate for a genuine data gap — matches the fallback exactly).
    _vol_z_raw = _reasons.get("volume_z")
    if _vol_z_raw is not None:
        _vol_z = float(_vol_z_raw)
        _min_vol_z = float(cfg.get("min_volume_z", -1.5))
        if _vol_z < _min_vol_z:
            return (
                f"Volume z-score {_vol_z:.2f} below minimum {_min_vol_z:.2f} "
                f"— thin market, elevated slippage/exit risk (T200)"
            )

    # T232-DL-DUALSCORER-DEBT: Price-drift HARD REJECT (T196, max_price_drift_pct default 3.0,
    # i.e. 3%), ported from paper_trading_engine.py's _scan_for_entries(). Genuinely distinct
    # from the T171 gap-filter gate just below (which fires on ANY positive gap over a looser
    # 4% bar off reasons["last_price"]) — T196 uses a tighter 3% bar off a freshly re-derived
    # daily-close reference price (sig_ref_price, threaded through config_overrides), not the
    # frozen reasons["last_price"] snapshot captured at signal-generation time — those two
    # values were verified to diverge in a real (not hypothetical) way whenever a candidate is
    # evaluated in a LATER refresh cycle than the one that generated its signal, so this gate
    # deliberately does NOT reuse reasons["last_price"] the way T171 does. cfg["sig_ref_price"]
    # is only present when the caller sent a real reference price (see
    # paper_trading_engine.py's config_overrides), matching this function's established
    # optional-parameter fail-open convention (symbol/style/sig_ts, min_kscore, etc.).
    if cfg.get("max_price_drift_pct") is not None:
        _ref_price = cfg.get("sig_ref_price")
        if _ref_price is not None and float(_ref_price) > 0:
            _drift_pct = (live_price / float(_ref_price) - 1) * 100
            _max_drift = float(cfg["max_price_drift_pct"])
            if _drift_pct > _max_drift:
                return (
                    f"Price drifted {_drift_pct:.1f}% above signal reference ${float(_ref_price):.2f} "
                    f"exceeds max drift {_max_drift:.0f}% — chasing blocked (T196)"
                )

    # T232-DL-DUALSCORER-DEBT: Multi-timeframe confluence HARD REJECT (T215, extended to
    # SWING by T222-B), ported from paper_trading_engine.py's _scan_for_entries(). A GROWTH/
    # LONG/SWING BUY whose SHORT-horizon signal has already flipped to SELL means near-term
    # momentum is working against the trade — the fallback discards these before ever scoring
    # them. cfg["short_signal"] is only present when the caller sent a real SHORT-horizon
    # signal (see paper_trading_engine.py's config_overrides) and cfg["confluence_check_enabled"]
    # defaults True to match _scan_for_entries' own default — a portfolio that has explicitly
    # disabled the check must not have DE silently re-enforce it.
    if (
        cfg.get("confluence_check_enabled", True)
        and style in ("GROWTH", "LONG", "SWING")
        and cfg.get("short_signal") == "SELL"
    ):
        return (
            "SHORT-horizon signal is SELL — near-term momentum contradicts this "
            f"{style} BUY (T215/T222-B)"
        )

    # T232-DL-DUALSCORER-DEBT / T221-E: Portfolio heat brake, ported from paper_trading_engine.py's
    # _scan_for_entries(). Too many stops hit recently in THIS portfolio means adverse market
    # conditions — the fallback pauses ALL new entries portfolio-wide rather than scoring
    # individual candidates. cfg["recent_stop_count"] is only present when the caller sent a
    # real portfolio's own recent-stop count (see paper_trading_engine.py's config_overrides);
    # heat_brake_max_stops <= 0 disables the gate entirely, matching _scan_for_entries' own
    # `if _heat_max > 0:` opt-out.
    if cfg.get("recent_stop_count") is not None:
        _heat_max = cfg.get("heat_brake_max_stops", 3)
        if _heat_max > 0 and int(cfg["recent_stop_count"]) >= _heat_max:
            return (
                f"Heat brake — {int(cfg['recent_stop_count'])} stops hit recently, "
                f"exceeds {_heat_max} threshold — entries paused until market conditions improve (T221-E)"
            )

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

    # T232-DL-DUALSCORER-DEBT: Conviction gate hard-block — ported from
    # paper_trading_engine.py's _scan_for_entries(). Reads the SAME conv_gate:{symbol}:{style}
    # Redis key the alert system already writes (1-day TTL) — if the alert system's own
    # 7-layer conviction check already evaluated this BUY and failed it, decision-engine must
    # agree rather than approve an entry the alert system itself would not have notified on.
    # No gate key = gate not yet run (fail-open, allow entry — matches _should_enter()'s own
    # fail-open-on-missing-data behavior elsewhere in this function). Deliberately reads Redis
    # directly (decision-engine already depends on `redis` for llm_scorer.py/risk_agent.py,
    # and shares the same redis_url as every other service) rather than requiring the caller
    # to pre-compute and forward this — the whole point is that /decide/{symbol} must be
    # self-sufficient for callers other than paper_trading_engine (e.g. decide.tsx).
    if symbol and style:
        try:
            from common.redis_client import get_redis as _get_pool_redis
            _gate_redis = _get_pool_redis()
            _cgval = _gate_redis.get(f"conv_gate:{symbol}:{style}")
            if _cgval:
                _cgdata = json.loads(_cgval)
                if _cgdata.get("signal") == "BUY" and _cgdata.get("sent") is False:
                    _failed_layers = _cgdata.get("failed", [])
                    return (
                        f"Conviction gate failed: {', '.join(_failed_layers[:2]) or 'multiple layers'} "
                        f"— alert system would not have notified on this BUY"
                    )
        except Exception:
            pass  # Redis unavailable or parse error → allow entry (fail-open)

    return None

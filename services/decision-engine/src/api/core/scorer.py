"""5-layer scoring model — extracted faithfully from paper_trading_engine._should_enter()."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import ScoreItem


_REGIME_SCORE = {
    "bull":     1,
    "neutral":  0,
    "choppy":  -1,
    "risk_off": -2,
    "bear":    -99,  # should never reach scoring if bear (hard-rejected first)
}

_RESEARCH_SCORE = {
    "STRONG BUY": 2,
    "BUY":        1,
    "WATCH":      0,
    "AVOID":     -1,
    "SELL":      -2,
}


def compute_score(
    live_price: float,
    game_plan: dict,
    signal_data: dict,
    research_rec: str | None,
    research_score_val: float | None,
    regime_state: str,
    cfg: dict,
    is_pre_choppy: bool = False,
    is_pre_risk_off: bool = False,
) -> tuple[int, list[ScoreItem]]:
    """Return (total_score, breakdown_list)."""
    reasons  = signal_data.get("reasons") or {}
    breakdown: list[ScoreItem] = []
    score = 0

    entry2     = game_plan.get("entry2",     live_price * 0.940)
    breakout   = game_plan.get("breakout",   live_price * 1.035)
    stop       = game_plan.get("stop",       live_price * 0.880)
    take_profit = game_plan.get("take_profit", live_price * 1.35)

    stop_dist = live_price - stop
    rr = (take_profit - live_price) / max(stop_dist, 0.0001)

    # ── Layer 1: Price zone ───────────────────────────────────────────────────
    if live_price < entry2:
        pts = 2
        note = f"Price ${live_price:.2f} below entry2 ${entry2:.2f} — deep pullback, excellent R:R"
    elif live_price <= breakout:
        pts = 2
        note = f"Price ${live_price:.2f} in optimal zone (${entry2:.2f}–${breakout:.2f})"
    elif live_price <= breakout * 1.03:
        pts = 1
        note = f"Price just above breakout (${breakout:.2f}) — momentum confirmed, slight chase"
    else:
        pts = -3
        pct_ext = (live_price / breakout - 1) * 100
        note = f"Price ${live_price:.2f} extended {pct_ext:.1f}% above breakout — chasing risk"
    score += pts
    breakdown.append(ScoreItem(layer="price_zone", pts=pts, note=note))

    # ── Layer 2: R:R quality ──────────────────────────────────────────────────
    if rr >= 3.5:
        pts, note = 2, f"Excellent R:R {rr:.1f}:1"
    elif rr >= 2.5:
        pts, note = 1, f"Good R:R {rr:.1f}:1"
    else:
        pts, note = 0, f"Acceptable R:R {rr:.1f}:1"
    score += pts
    breakdown.append(ScoreItem(layer="rr_quality", pts=pts, note=note))

    # ── Layer 3a: Volume z-score ──────────────────────────────────────────────
    volume_z = reasons.get("volume_z")
    if volume_z is not None:
        vz = float(volume_z)
        if vz > 1.0:
            pts, note = 1, f"Above-average volume (z={vz:.1f}) — conviction confirmation"
        elif vz < -0.5:
            pts, note = -1, f"Below-average volume (z={vz:.1f}) — breakout less reliable"
        else:
            pts, note = 0, f"Average volume (z={vz:.1f})"
        score += pts
        breakdown.append(ScoreItem(layer="volume", pts=pts, note=note))

    # ── Layer 3b: Earnings proximity softener ─────────────────────────────────
    dte = reasons.get("days_to_earnings")
    if dte is not None:
        dte_int = int(dte)
        if 6 <= dte_int <= 10:
            pts, note = -1, f"Earnings in {dte_int} days — size conservatively"
            score += pts
            breakdown.append(ScoreItem(layer="earnings", pts=pts, note=note))

    # ── Layer 3c: Fused ML+TA probability ────────────────────────────────────
    bull_prob = float(signal_data.get("bullish_probability") or 0.0)
    if bull_prob >= 0.70:
        pts, note = 1, f"Strong conviction {bull_prob*100:.0f}% fused probability"
    elif bull_prob < 0.58:
        pts, note = -1, f"Weak conviction {bull_prob*100:.0f}% fused probability"
    else:
        pts, note = 0, f"Moderate conviction {bull_prob*100:.0f}% fused probability"
    score += pts
    breakdown.append(ScoreItem(layer="ml_signal", pts=pts, note=note))

    # ── Layer 3d: Confidence trajectory (SA-26) ───────────────────────────────
    conf_delta = signal_data.get("confidence_delta")
    if conf_delta is not None:
        cd = float(conf_delta)
        if cd > 8:
            pts, note = 1, f"Signal accelerating (+{cd:.0f} confidence trend)"
        elif cd < -8:
            pts, note = -1, f"Signal decelerating ({cd:.0f} confidence trend)"
        else:
            pts = 0
            note = f"Confidence stable (delta={cd:.0f})"
        score += pts
        breakdown.append(ScoreItem(layer="conf_delta", pts=pts, note=note))

    # ── Layer 3e: Signal freshness (SA-24) ────────────────────────────────────
    sig_ts = signal_data.get("ts")
    if sig_ts is not None:
        try:
            if isinstance(sig_ts, str):
                ts_aware = datetime.fromisoformat(sig_ts.replace("Z", "+00:00"))
            else:
                ts_aware = sig_ts.replace(tzinfo=timezone.utc) if sig_ts.tzinfo is None else sig_ts
            age_h = (datetime.now(timezone.utc) - ts_aware).total_seconds() / 3600
            if age_h < 4:
                pts, note = 1, f"Fresh signal ({age_h:.1f}h) — entry in prime window"
            elif age_h > 18:
                pts, note = -1, f"Stale signal ({age_h:.1f}h) — conditions may have shifted"
            else:
                pts, note = 0, f"Signal age {age_h:.1f}h — acceptable"
            score += pts
            breakdown.append(ScoreItem(layer="freshness", pts=pts, note=note))
        except Exception:
            pass

    # ── Layer 3f: Catalyst intelligence ──────────────────────────────────────
    catalyst_score = reasons.get("catalyst_score")
    if catalyst_score is not None:
        cs = float(catalyst_score)
        if cs >= 60:
            pts, note = 1, f"Strong catalyst signal (score={cs:.0f}) — insider buying or congress accumulation"
        elif cs <= -30:
            pts, note = -1, f"Negative catalyst signal (score={cs:.0f}) — insider selling or adverse events"
        else:
            pts, note = 0, f"Neutral catalyst signal (score={cs:.0f})"
        score += pts
        breakdown.append(ScoreItem(layer="catalyst", pts=pts, note=note))

    # ── Layer 3g: Pre-regime early-warning (F11) ──────────────────────────────
    if is_pre_risk_off:
        pts, note = -1, "Pre-risk-off: VIX rising into warning zone — conditions deteriorating"
        score += pts
        breakdown.append(ScoreItem(layer="pre_regime", pts=pts, note=note))
    elif is_pre_choppy:
        pts, note = -1, "Pre-choppy: SPY hugging EMA50 — trend weakening, raise bar"
        score += pts
        breakdown.append(ScoreItem(layer="pre_regime", pts=pts, note=note))

    # ── Layer 4: Research alignment ───────────────────────────────────────────
    if research_rec:
        rec_upper = research_rec.upper().replace("_", " ")
        pts = _RESEARCH_SCORE.get(rec_upper, 0)
        rscore = f" (score {research_score_val:.0f})" if research_score_val else ""
        note = f"Research: {rec_upper}{rscore}"
        score += pts
        breakdown.append(ScoreItem(layer="research", pts=pts, note=note))

    # ── Layer 5: Market regime ────────────────────────────────────────────────
    pts = _REGIME_SCORE.get(regime_state, 0)
    note = f"Regime: {regime_state}"
    score += pts
    breakdown.append(ScoreItem(layer="regime", pts=pts, note=note))

    return score, breakdown


def min_score_for_regime(regime_state: str, cfg: dict) -> int:
    """Regime-adjusted minimum entry score."""
    base = cfg.get("min_entry_score", 4)
    if regime_state == "bear":
        return 999
    if regime_state == "risk_off":
        return max(base, cfg.get("regime_risk_off_min_score", 5))
    if regime_state == "choppy":
        return max(base, cfg.get("regime_choppy_min_score", 4))
    return base

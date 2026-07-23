"""7-layer scoring model — extracted from paper_trading_engine._should_enter(), not a byte-
for-byte mirror. Two intentional, currently-undeferred divergences remain (AUD232-027/028,
docs/AUDIT_REPORT_TIER242_2026-07-10.md):

1. No RL policy adjustment layer (AL-1 in _should_enter()) — that layer depends on
   rl_agent.py, a market-data-local module with its own trained Q-function state; porting
   it here would mean either a cross-service HTTP call back to market-data on every score
   (added latency/coupling) or duplicating the RL model-loading logic in a second service.
   Deferred rather than rushed.
2. compute_score()'s decision rule is always the additive-score threshold — it cannot
   replicate _should_enter()'s PT-3 calibrated-logistic-regression bypass (entry_weights.json,
   >=100 closed trades), which also depends on market-data-local file state this service has
   no access to.

Both are real, confirmed gaps — not silently unnoticed — see the audit doc for the full
9-item comparison (T232-DL-DUALSCORER-DEBT tracks the broader architectural debt)."""
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
    recent_win_rate: float | None = None,
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
    # Was signal_data.get("confidence_delta") — but signal-engine only ever writes this into
    # reasons["confidence_delta"] (signal-engine/src/api/routes.py, _bulk_persist(), stayed in
    # routes.py after the T233-ARCH-INSERVICE-SPLITS 2026-07-22 file split — line number no
    # longer stable across a growing file, hence naming the function instead), never top-level. Every
    # other reasons-sourced field in this function (volume_z, days_to_earnings, catalyst_score)
    # already correctly reads from `reasons`; this one was an isolated miss, making layer 3d
    # permanently dead code — accelerating/decelerating signals never got their ±1 adjustment.
    conf_delta = reasons.get("confidence_delta")
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
    # AUD232-006: previously read a single combined reasons["catalyst_score"], which
    # event-intelligence clamps to [0, 100] before signal-engine ever stores it — the
    # `cs <= -30` branch below was unreachable dead code, silently dropping every
    # bearish-catalyst penalty (heavy insider/congress selling got zero points instead
    # of the -1 the fallback _should_enter() applies for the same signal). Fixed to read
    # the two separate, genuinely-signed fields (insider_score, congress_score) signal-engine
    # already writes into reasons, matching _should_enter()'s two-layer scoring exactly.
    _insider_score  = reasons.get("insider_score")
    _congress_score = reasons.get("congress_score")
    if _insider_score is not None:
        ins = float(_insider_score)
        if ins >= 60:
            pts, note = 1, f"Strong insider buying (score={ins:.0f}) — real-money conviction"
        elif ins < -30:
            pts, note = -1, f"Significant insider selling (score={ins:.0f}) — management caution"
        else:
            pts, note = 0, f"Neutral insider signal (score={ins:.0f})"
        score += pts
        breakdown.append(ScoreItem(layer="catalyst_insider", pts=pts, note=note))
    if _congress_score is not None:
        cong = float(_congress_score)
        if cong > 50:
            pts, note = 1, f"Congress net buying (score={cong:.0f}) — informed capital inflow"
        else:
            pts, note = 0, f"Neutral congress signal (score={cong:.0f})"
        score += pts
        breakdown.append(ScoreItem(layer="catalyst_congress", pts=pts, note=note))

    # ── Layer 3g: Pre-regime early-warning (F11) ──────────────────────────────
    if is_pre_risk_off:
        pts, note = -1, "Pre-risk-off: VIX rising into warning zone — conditions deteriorating"
        score += pts
        breakdown.append(ScoreItem(layer="pre_regime", pts=pts, note=note))
    elif is_pre_choppy:
        pts, note = -1, "Pre-choppy: SPY hugging EMA50 — trend weakening, raise bar"
        score += pts
        breakdown.append(ScoreItem(layer="pre_regime", pts=pts, note=note))

    # T234-DE-SCORER-DOUBLECOUNT-ENTRYZONE: Layer 3h ("entry_drift") used to live here,
    # scoring live_price against entry2/breakout again — the same static signal-time
    # reference points Layer 1 ("price_zone") above already scores. Since breakout is a
    # fixed ratio of entry2 (both computed once at signal time and never updated as
    # live_price moves), the two layers were not independent scoring axes: they moved in
    # lockstep, so a single directional price move got weighted twice in the aggregate
    # score. Removed rather than "made independent" — Layer 1's 4-bucket price_zone check
    # already captures this signal; a genuinely different freshness/volatility-adjusted
    # measure can be added later as its own layer if warranted, rather than restoring a
    # second view of the same static comparison.

    # ── Layer 4: Research alignment ───────────────────────────────────────────
    if research_rec:
        rec_upper = research_rec.upper().replace("_", " ")
        pts = _RESEARCH_SCORE.get(rec_upper, 0)
        # T247-DECISIONENGINE-RESEARCHSCORE-FALSY: `if research_score_val` treated a genuine
        # overall_score of 0 (the worst possible score, distinct from no score existing) as
        # falsy, silently dropping the score from the display note exactly when it matters
        # most. Use `is not None` so only a genuinely missing score omits the "(score N)" suffix.
        rscore = f" (score {research_score_val:.0f})" if research_score_val is not None else ""
        note = f"Research: {rec_upper}{rscore}"
        score += pts
        breakdown.append(ScoreItem(layer="research", pts=pts, note=note))

    # ── Layer 5: Market regime ────────────────────────────────────────────────
    pts = _REGIME_SCORE.get(regime_state, 0)
    note = f"Regime: {regime_state}"
    score += pts
    breakdown.append(ScoreItem(layer="regime", pts=pts, note=note))

    # ── Layer 6: K-Score conviction ────────────────────────────────────────────
    # AUD232-042: DE previously had zero K-Score/ranking-engine reference anywhere — a
    # LONG-horizon stock with kscore=25 (well below the real conviction gate's 55 floor,
    # scheduler.py's check_signal_alerts()) could still score highly and enter via DE purely
    # on price_zone + rr_quality + ml_signal + regime, since DE had no fundamental/momentum
    # input to reflect that weakness. kscore arrives via cfg (threaded through config_overrides
    # by the caller, matching the existing recent_win_rate/consec_losses pattern) since DE
    # itself has no ranking-engine client — it's supplied by whichever caller already computed
    # it (paper_trading_engine.py's kscore_f). Uses the same >=55 conviction threshold as the
    # real gate; a low-but-still-gated K-Score (the pre-entry min_kscore gate already filters
    # out anything below ~48-52 depending on style) gets a mild penalty rather than a 0, since
    # it already passed that floor.
    kscore = cfg.get("kscore")
    if kscore is not None:
        kscore = float(kscore)
        if kscore >= 55:
            pts, note = 1, f"K-Score {kscore:.0f} — conviction positive"
        else:
            pts, note = -1, f"K-Score {kscore:.0f} below 55 — weak fundamental/momentum case"
        score += pts
        breakdown.append(ScoreItem(layer="kscore", pts=pts, note=note))

    # ── Layer 7: Cross-horizon consensus ──────────────────────────────────────
    # AUD232-007: this scorer had NO layer reading cross_style_buys at all — a 2-point
    # swing in the fallback _should_enter() (+1 for >=2 other horizons also BUY, -1 for
    # zero consensus in bear/choppy) was completely invisible here, even though
    # cross_style_buys is already available in reasons (DE's own routes.py already reads
    # it for sizer.py/llm_scorer.py, just never forwarded it into this scoring function).
    cross_style_buys = int(reasons.get("cross_style_buys", 0))
    if cross_style_buys >= 2:
        pts, note = 1, f"Cross-horizon: {cross_style_buys}+ styles BUY — strong multi-timeframe alignment"
        score += pts
        breakdown.append(ScoreItem(layer="consensus", pts=pts, note=note))
    elif cross_style_buys == 0 and regime_state in ("bear", "choppy"):
        pts, note = -1, "No cross-horizon support in bear/choppy regime — conviction penalty"
        score += pts
        breakdown.append(ScoreItem(layer="consensus", pts=pts, note=note))

    return score, breakdown


def min_score_for_regime(regime_state: str, cfg: dict) -> int:
    """Regime-adjusted minimum entry score.

    Also raises the floor by 1 when recent win rate is below 30% — a human
    trader who has lost 7 of the last 10 trades gets more selective, not less.
    """
    base = cfg.get("min_entry_score", 4)
    if regime_state == "bear":
        return 999
    if regime_state == "risk_off":
        base = max(base, cfg.get("regime_risk_off_min_score", 5))
    elif regime_state == "choppy":
        base = max(base, cfg.get("regime_choppy_min_score", 4))
    recent_win_rate = cfg.get("recent_win_rate")
    if recent_win_rate is not None and float(recent_win_rate) < 0.30:
        base += 1
    return base

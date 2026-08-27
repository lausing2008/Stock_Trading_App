"""Decision Engine API routes."""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from common.jwt_auth import get_current_username

from .core.aggregator import (
    abuild_game_plan,
    aget_entry_gate_params,
    aget_entry_weights,
    extract_live_price,
    fetch_all,
)
from .core.hard_rejects import check_hard_rejects
from .core.models import (
    BatchDecisionRequest,
    DecisionRequest,
    DecisionResult,
    Factors,
    Multipliers,
    PositionPlan,
    RiskFlag,
    ScoreItem,
    ScoreReplayRequest,
    ScoreReplayResponse,
    ScoreReplayResult,
)
from .core.regime import aget_regime, get_regime
from .core.scorer import compute_score, min_score_for_regime
from .core.sizer import combined_market_mult, compute_position
from .llm_scorer import score_with_llm
from .risk_agent import check_risks

router = APIRouter()
log = structlog.get_logger()

# ── Config defaults ────────────────────────────────────────────────────────────

_DEFAULT_CFG: dict[str, Any] = {
    "min_entry_score":        4,
    "min_confidence":         62.0,
    "min_rr_ratio":           2.0,
    "risk_per_trade_pct":     0.01,
    "max_position_pct":       0.10,
    "max_loss_per_trade_pct": 0.02,
    "max_daily_loss_pct":     0.04,
    "research_gating_enabled": True,
    "regime_risk_off_min_score": 5,
    "regime_choppy_min_score":   4,
}


def _merge_cfg(overrides: dict) -> dict:
    return {**_DEFAULT_CFG, **overrides}


# ── Core decision logic ────────────────────────────────────────────────────────

async def _decide(symbol: str, req: DecisionRequest) -> DecisionResult:
    t0 = _time.monotonic()
    cfg = _merge_cfg(req.config_overrides)
    # T232-DE4: req.max_daily_loss_pct was accepted on the request model but never merged into
    # cfg, so a caller requesting a tighter (or looser) daily-loss gate was silently ignored —
    # the gate always used the 0.04 default. Only apply the explicit request value when the
    # caller didn't already set it via config_overrides (overrides take precedence).
    if "max_daily_loss_pct" not in req.config_overrides:
        cfg["max_daily_loss_pct"] = req.max_daily_loss_pct
    style = req.style.upper()

    # 1. Fan-out: fetch signal + research + yfinance price fallback in parallel
    signal_data, research_data, yf_price = await fetch_all(symbol, style)

    # 2. Resolve live price (signal reasons → yfinance fallback → caller-supplied)
    live_price = req.live_price
    if live_price is None:
        live_price = extract_live_price(signal_data, yf_price)
    if live_price is None or live_price <= 0:
        raise HTTPException(422, f"Cannot resolve live price for {symbol} — yfinance returned no data")

    # 3. Resolve game plan
    if req.game_plan:
        # BUG-DECIDE-GAMEPLAN-STYLEFLOAT: the real caller (paper_trading_engine.py's
        # _build_game_plan_for_style()) returns a dict that legitimately includes a "style"
        # key (a string like "GROWTH") alongside the numeric entry1/entry2/breakout/stop/
        # take_profit/current_price fields — this function only ever reads the numeric keys
        # (scorer.py/sizer.py/hard_rejects.py all use game_plan.get("stop"/"take_profit"/etc.)
        # with a numeric default; "style" is never read anywhere in this service). A blanket
        # float(v) over every key crashed on the FIRST real non-numeric value with a raw,
        # unhandled ValueError — confirmed live in production: 3 real BUY candidates
        # (AXON, DIVO, NET) hit this over a 24h window, each silently falling back to
        # _should_enter() (the DE-outage fallback gate) instead of getting decision-engine's
        # real, primary scoring, with no visibility beyond a "decision_engine.bad_status"
        # warning log on the CALLING side. Convert only values that are actually numeric;
        # pass anything else through unchanged rather than crashing on it.
        game_plan = {}
        for k, v in req.game_plan.items():
            try:
                game_plan[k] = float(v)
            except (TypeError, ValueError):
                game_plan[k] = v
    else:
        # T247-DECISIONENGINE-STYLEPARAMS-BLOCKING: must use the async variant — a cache miss
        # inside build_game_plan()'s _get_style_params() call does a blocking httpx.get(),
        # which would otherwise stall this shared event loop for every concurrent request.
        game_plan = await abuild_game_plan(live_price, style, signal_data)

    stop_price  = game_plan.get("stop",       live_price * 0.880)
    take_profit = game_plan.get("take_profit", live_price * 1.350)

    # 4. Extract signal fields
    sig_direction  = (signal_data or {}).get("signal", "HOLD")
    confidence     = float((signal_data or {}).get("confidence") or 0.0)
    reasons        = (signal_data or {}).get("reasons") or {}
    dte            = reasons.get("days_to_earnings")
    dte_int        = int(dte) if dte is not None else None
    cross_buys     = int(reasons.get("cross_style_buys", 0))

    # Compute signal age for Factors display
    sig_age_h: float | None = None
    sig_ts = (signal_data or {}).get("ts")
    if sig_ts is not None:
        try:
            if isinstance(sig_ts, str):
                ts_aware = datetime.fromisoformat(sig_ts.replace("Z", "+00:00"))
                if ts_aware.tzinfo is None:
                    ts_aware = ts_aware.replace(tzinfo=timezone.utc)
            else:
                ts_aware = sig_ts.replace(tzinfo=timezone.utc) if sig_ts.tzinfo is None else sig_ts
            sig_age_h = (datetime.now(timezone.utc) - ts_aware).total_seconds() / 3600
        except Exception as exc:
            log.warning("decision.sig_ts_parse_failed", ts=sig_ts, error=str(exc))

    # 5. Resolve research fields
    research_rec   = None
    research_score = None
    if research_data:
        # T247-DECISIONENGINE-DEAD-AIVERDICT-FALLBACK: removed the `or
        # research_data.get("ai_verdict", {})...` fallback — research_data here always comes
        # from GET /research/{symbol}/summary (research-engine's get_research_summary(),
        # which returns only {recommendation, overall_score, confidence, generated_at}, never
        # an ai_verdict key, and recommendation is always one of STRONG BUY/BUY/WATCH/AVOID/
        # SELL/INSUFFICIENT DATA — never None/empty). The fallback branch was permanently
        # unreachable dead code.
        research_rec   = research_data.get("recommendation")
        research_score = research_data.get("overall_score")
        if research_score is not None:
            research_score = float(research_score)

    # 6. Market regime — auto-detect HK from symbol suffix (F7)
    market = req.market
    if symbol.endswith(".HK") and market == "US":
        market = "HK"
    # T247-DECISIONENGINE-REGIME-BLOCKING: must use the async variant here — this function
    # runs on the shared event loop and is fanned out via asyncio.gather() by /decide/batch;
    # the sync get_regime() would block that loop with a synchronous httpx.get() on any
    # cache miss, stalling every other concurrent request for up to 10s.
    regime = await aget_regime(market)
    regime_state = regime.get("state", "neutral")
    breadth_size_mult = float(regime.get("breadth_size_mult", 1.0))
    vix_size_mult     = float(regime.get("vix_size_mult", 1.0))
    is_pre_choppy = bool(regime.get("is_pre_choppy", False))
    is_pre_risk_off = bool(regime.get("is_pre_risk_off", False))

    # T234-CONFIG-DECIDE-DEFAULT-MISMATCH: _DEFAULT_CFG's min_confidence/min_kscore/
    # min_entry_score/min_ta_score/min_rr_ratio are disconnected literals with no relation to
    # what a real portfolio of this style/market would actually use (_scan_for_entries' own
    # _DEFAULT_CONFIG + _STYLE_OVERRIDES + HK-override merge in paper_trading_engine.py) —
    # a caller that goes through _call_decision_engine() (the real trading path) always sends
    # these explicitly via config_overrides, so this never mattered there, but a standalone
    # caller (decide.tsx's GET /decide/{symbol}/explain, which sends no config_overrides at
    # all) silently got _DEFAULT_CFG's own guessed values instead of the real ones. Only fills
    # in keys the caller didn't already explicitly override — config_overrides still always wins.
    _real_gate_defaults = await aget_entry_gate_params(style, market)
    for _k, _v in _real_gate_defaults.items():
        if _k not in req.config_overrides and _v is not None:
            cfg[_k] = _v

    # 7. Hard rejects — special-case: no signal data means symbol is unknown
    if signal_data is None:
        latency = int((_time.monotonic() - t0) * 1000)
        no_signal_reason = (
            "No stored signal for this symbol — open the stock detail page first to generate one."
        )
        log.info("decision.blocked", symbol=symbol, style=style, reason=no_signal_reason)
        return DecisionResult(
            symbol=symbol, style=style,
            verdict="BLOCKED", score=-99, min_score=min_score_for_regime(regime_state, cfg),
            factors=Factors(regime=regime_state), multipliers=Multipliers(),
            score_breakdown=[], blocked_reason=no_signal_reason,
            latency_ms=latency, timestamp=datetime.now(timezone.utc).isoformat(),
        )

    reject_reason = check_hard_rejects(
        signal_direction=sig_direction,
        confidence=confidence,
        live_price=live_price,
        stop_price=stop_price,
        take_profit=take_profit,
        regime_state=regime_state,
        days_to_earnings=dte_int,
        open_positions=req.open_positions,
        max_positions=req.max_positions,
        daily_pnl_pct=req.daily_pnl_pct,
        equity=req.equity,
        initial_capital=req.initial_capital,
        cfg=cfg,
        research_rec=research_rec,
        game_plan=game_plan,
        market=market,
        reasons=reasons,
        symbol=symbol,
        style=style,
        sig_ts=sig_ts,
    )

    # Explicit None checks so 0.0 values are preserved (truthy-or chain would coerce 0.0 → None)
    _sd = signal_data or {}
    _bp = _sd.get("bullish_probability") if _sd.get("bullish_probability") is not None else reasons.get("ml_probability")
    _cd = reasons.get("confidence_delta") if reasons.get("confidence_delta") is not None else _sd.get("confidence_delta")
    factors = Factors(
        signal_direction=sig_direction,
        signal_confidence=round(confidence, 2),
        ml_bull_prob=float(_bp) if _bp is not None else None,
        research_recommendation=research_rec,
        research_score=research_score,
        regime=regime_state,
        volume_z=float(reasons["volume_z"]) if reasons.get("volume_z") is not None else None,
        days_to_earnings=dte_int,
        signal_age_h=round(sig_age_h, 2) if sig_age_h is not None else None,
        conf_delta=float(_cd) if _cd is not None else None,
        cross_style_buys=cross_buys,
    )

    if reject_reason:
        latency = int((_time.monotonic() - t0) * 1000)
        log.info("decision.blocked", symbol=symbol, style=style, reason=reject_reason)
        return DecisionResult(
            symbol=symbol, style=style,
            verdict="BLOCKED", score=-99, min_score=min_score_for_regime(regime_state, cfg),
            factors=factors, multipliers=Multipliers(),
            score_breakdown=[], blocked_reason=reject_reason,
            latency_ms=latency, timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # 8. Score
    recent_win_rate = cfg.get("recent_win_rate")
    score, breakdown = compute_score(
        live_price=live_price,
        game_plan=game_plan,
        signal_data=signal_data or {},
        research_rec=research_rec,
        research_score_val=research_score,
        regime_state=regime_state,
        cfg=cfg,
        is_pre_choppy=is_pre_choppy,
        is_pre_risk_off=is_pre_risk_off,
        recent_win_rate=float(recent_win_rate) if recent_win_rate is not None else None,
    )
    min_score = min_score_for_regime(regime_state, cfg)

    # 9. Size
    position, multipliers = compute_position(
        equity=req.equity,
        live_price=live_price,
        game_plan=game_plan,
        confidence=confidence,
        research_rec=research_rec,
        research_score_val=research_score,
        regime_state=regime_state,
        cross_style_buys=cross_buys,
        days_to_earnings=dte_int,
        cfg=cfg,
        breadth_size_mult=breadth_size_mult,
        vix_size_mult=vix_size_mult,
    )

    # 10. T203: Optional LLM scoring layer (after hard rejects, before final verdict)
    llm_verdict_str: str | None = None
    llm_reasoning: str | None = None
    llm_verdict_overridden_by_sizing = False
    if cfg.get("llm_scoring_enabled", False):
        llm_adj, llm_reasoning = await score_with_llm(
            symbol=symbol, style=style,
            sig_direction=sig_direction, confidence=confidence,
            ml_prob=float(_bp) if _bp is not None else None,
            game_plan=game_plan, regime_state=regime_state, regime=regime,
            research_rec=research_rec, research_score=research_score,
            cross_style_buys=cross_buys,
            score=score, min_score=min_score,
            score_breakdown=breakdown,
            sig_ts=sig_ts, cfg=cfg,
        )
        if llm_adj != 0:
            score += llm_adj
            breakdown.append(ScoreItem(
                layer="llm_reasoning",
                pts=llm_adj,
                note=f"Claude: {llm_reasoning[:60] if llm_reasoning else 'no note'}",
            ))
        llm_verdict_str = ("BUY" if llm_adj > 0 else "SKIP" if llm_adj < 0 else "HOLD")

    # 10b. T258-WHATCOULDGOWRONG-AGENT: optional adversarial risk enumeration (advisory only —
    # never affects score/verdict, matching the design's own explicit "no unvalidated
    # probability_of_failure gating anything" stance).
    risks: list[dict] | None = None
    if cfg.get("risk_check_enabled", False):
        _vz = reasons.get("volume_z")
        risks = await check_risks(
            symbol=symbol, style=style,
            sig_direction=sig_direction, confidence=confidence,
            game_plan=game_plan, regime_state=regime_state, regime=regime,
            is_pre_choppy=is_pre_choppy, is_pre_risk_off=is_pre_risk_off,
            research_rec=research_rec, research_score=research_score,
            days_to_earnings=dte_int, volume_z=float(_vz) if _vz is not None else None,
            reasons=reasons, sig_ts=sig_ts, cfg=cfg,
        )

    # 11. Verdict
    # T232-DL-DUALSCORER-DEBT item #23: paper_trading_engine.py's _should_enter() abandons the
    # plain additive score>=min_entry_score comparison entirely once a portfolio has >=100
    # closed trades (PT-3) — it fits a calibrated logistic-regression win-probability model
    # instead. decision-engine had no equivalent, so /decide/{symbol} always used the plain
    # threshold even for a portfolio whose fallback gate had already moved on to the calibrated
    # model — a real divergence for exactly the portfolios most worth trusting (100+ real closed
    # trades). Mirrors _should_enter()'s own formula/threshold verbatim; only ever engages when
    # the SAME >=100-trade gate _should_enter() itself checks is satisfied, so a young portfolio
    # (or a market-data outage — the fetch fails open to {}) sees byte-identical behavior to
    # before this change.
    _entry_weights = await aget_entry_weights()
    if _entry_weights.get("intercept") is not None and _entry_weights.get("n_trades", 0) >= 100:
        import math as _math
        _stop_dist = live_price - stop_price
        _rr = (take_profit - live_price) / max(_stop_dist, 0.0001)
        _ks_for_cal = float(cfg.get("kscore")) if cfg.get("kscore") is not None else 50.0
        _logit = (
            _entry_weights["intercept"]
            + _entry_weights["w_rr"]         * min(_rr, 8.0)
            + _entry_weights["w_confidence"] * confidence
            + _entry_weights["w_score"]      * float(score)
            + _entry_weights["w_kscore"]     * _ks_for_cal
        )
        _cal_prob = 1.0 / (1.0 + _math.exp(-_logit))
        _cal_threshold = _entry_weights.get("threshold", 0.52)
        if _cal_prob >= _cal_threshold:
            verdict = "BUY"
        elif score >= min_score - 2:
            verdict = "HOLD"
        else:
            verdict = "SKIP"
        breakdown.append(ScoreItem(
            layer="calibrated_entry",
            pts=0,
            note=f"Calibrated win-prob {_cal_prob*100:.0f}% (threshold {_cal_threshold*100:.0f}%, n_trades={_entry_weights.get('n_trades')})",
        ))
    elif score >= min_score:
        verdict = "BUY"
    elif score >= min_score - 2:
        verdict = "HOLD"
    else:
        verdict = "SKIP"

    # T232-DE1: a candidate that clears the score bar but whose combined sizing multiplier is
    # too small (e.g. stacked regime/breadth/VIX/confidence dampening during a volatile period)
    # produces an economically-meaningless micro-position that still occupies a max_positions
    # slot and pays slippage/commission that can exceed its own expected value. Skip outright
    # rather than opening dust — better candidates with a normal-sized position should get the
    # slot instead.
    _MIN_COMBINED_MULT = 0.30
    _micro_position_reason: str | None = None
    if verdict == "BUY":
        # T232-DE1: at VIX=30 + risk_off + confidence=0.85, straight multiplication of
        # regime/breadth/vix gave 0.283 (incorrectly below the 0.30 floor, skipping a trade the
        # real sizer would size normally at 0.425). AUD232-053: now calls sizer.py's
        # combined_market_mult() directly instead of re-deriving the identical min() expression
        # inline — the two could otherwise silently diverge if the formula changed in only one
        # of the two places.
        _market_mult = combined_market_mult(multipliers.regime, multipliers.breadth, multipliers.vix)
        _combined_mult = (
            _market_mult * multipliers.research * multipliers.confidence
            * multipliers.consensus * multipliers.earnings
        )
        if _combined_mult < _MIN_COMBINED_MULT:
            verdict = "SKIP"
            # T247-DECISIONENGINE-LLMVERDICT-ORDERING: llm_verdict_str was computed earlier
            # from the LLM's own standalone view and is NOT re-derived here — this flag makes
            # the resulting verdict/llm_verdict disagreement (e.g. llm_verdict="BUY",
            # verdict="SKIP") an explicit, intentional signal instead of a silent
            # inconsistency.
            if llm_verdict_str == "BUY":
                llm_verdict_overridden_by_sizing = True
            _micro_position_reason = (
                f"Combined sizing multiplier {_combined_mult:.3f} below floor {_MIN_COMBINED_MULT} "
                f"— would open a dust position, skipping instead"
            )
            log.info(
                "decision.skipped_micro_position",
                symbol=symbol, combined_mult=round(_combined_mult, 3),
                floor=_MIN_COMBINED_MULT,
                note="sizing multipliers stacked below floor — skipping rather than opening a dust position",
            )

    latency = int((_time.monotonic() - t0) * 1000)

    log.info(
        "decision.evaluated",
        symbol=symbol, style=style, verdict=verdict,
        score=score, min_score=min_score,
        regime=regime_state, latency_ms=latency,
        llm_verdict=llm_verdict_str,
        llm_verdict_overridden_by_sizing=llm_verdict_overridden_by_sizing,
    )

    return DecisionResult(
        symbol=symbol, style=style,
        verdict=verdict, score=score, min_score=min_score,
        position=position if verdict == "BUY" else None,
        factors=factors, multipliers=multipliers,
        score_breakdown=breakdown,
        blocked_reason=_micro_position_reason,
        latency_ms=latency,
        timestamp=datetime.now(timezone.utc).isoformat(),
        llm_verdict=llm_verdict_str,
        llm_verdict_overridden_by_sizing=llm_verdict_overridden_by_sizing,
        llm_reasoning=llm_reasoning,
        risks=[RiskFlag(**r) for r in risks] if risks else None,
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/decide/batch", response_model=list[DecisionResult])
async def decide_batch(
    req: BatchDecisionRequest,
    _: str = Depends(get_current_username),
):
    """Evaluate multiple symbols using shared portfolio context.

    Results are sorted by score descending so the highest-conviction candidates
    appear first. Useful for watchlist scanning before market open.
    """
    import asyncio as _asyncio

    single_req = DecisionRequest(
        style=req.style,
        portfolio_id=req.portfolio_id,
        equity=req.equity,
        open_positions=req.open_positions,
        max_positions=req.max_positions,
        daily_pnl_pct=req.daily_pnl_pct,
        max_daily_loss_pct=req.max_daily_loss_pct,
        market=req.market,
        config_overrides=req.config_overrides,
    )

    tasks = [_decide(sym.upper(), single_req) for sym in req.symbols]
    results = await _asyncio.gather(*tasks, return_exceptions=True)

    output = []
    for sym, res in zip(req.symbols, results):
        if isinstance(res, Exception):
            log.warning("decision.batch_symbol_failed", symbol=sym, error=str(res))
            continue
        output.append(res)

    return sorted(output, key=lambda r: r.score, reverse=True)


@router.post("/decide/{symbol}", response_model=DecisionResult)
async def decide(
    symbol: str,
    req: DecisionRequest,
    _: str = Depends(get_current_username),
):
    """Evaluate whether to enter a position in {symbol} right now.

    Aggregates signal engine, ML probability, research recommendation, and market
    regime into a single verdict (BUY / HOLD / SKIP / BLOCKED) with an illustrative
    position sizing preview and per-layer score breakdown.

    T234-DE-SIZER-DISCARDED: the `position` field is ILLUSTRATIVE ONLY. The live
    (paper) trading path never calls this endpoint for sizing — paper_trading_engine.py's
    _call_decision_engine() reads only verdict/score/blocked_reason from this response
    and computes real share counts independently via its own formula. sizer.py's
    multiplier bands are also deliberately different in places (see sizer.py's module
    docstring) — do not assume `position` matches what the trading engine would
    actually do for this symbol right now.
    """
    symbol = symbol.upper()
    return await _decide(symbol, req)


@router.get("/decide/{symbol}/explain")
async def explain(
    symbol: str,
    style: str = "SWING",
    _: str = Depends(get_current_username),
):
    """Human-readable explanation of the current decision for a symbol."""
    symbol = symbol.upper()
    req = DecisionRequest(style=style)
    result = await _decide(symbol, req)

    lines = [
        f"Decision for {symbol} ({style}): **{result.verdict}**",
        f"Score: {result.score} / min {result.min_score}",
        "",
        "Score breakdown:",
    ]
    for item in result.score_breakdown:
        lines.append(f"  [{item.pts:+d}] {item.layer}: {item.note}")

    if result.blocked_reason:
        lines.append(f"\nBlocked: {result.blocked_reason}")

    if result.position:
        p = result.position
        lines += [
            "",
            f"Position: {p.shares} shares @ ${p.entry_price:.2f}",
            f"Stop: ${p.stop_price:.2f} | Target 1: ${p.target_1:.2f} | Target 2: ${p.target_2:.2f}",
            f"R:R: {p.rr_ratio:.1f}:1 | Dollar risk: ${p.dollar_risk:.0f}",
        ]

    return {"symbol": symbol, "style": style, "explanation": "\n".join(lines), "result": result}


@router.get("/decide/regime")
def regime_status(market: str = "US", _: str = Depends(get_current_username)):
    """Return current market regime for US or HK."""
    return get_regime(market.upper())


@router.post("/decide/score-replay", response_model=ScoreReplayResponse)
def score_replay(req: ScoreReplayRequest, _: str = Depends(get_current_username)) -> ScoreReplayResponse:
    """T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group A scorer sweep: batch-scores N already-
    resolved historical BUY signals against ONE candidate cfg, calling the REAL compute_score()
    / min_score_for_regime() directly — never a re-implementation of the scoring formula in a
    second service. Called by market-data's own walk-forward sweep, which owns all the DATA
    reconstruction (game_plan/confidence_delta point-in-time replay, matching
    gate_harness.py's already-proven replay_should_enter() approach) — this endpoint's only job
    is to run the caller-assembled inputs through the actual live scoring code.

    Deliberately scored WITHOUT is_pre_choppy/is_pre_risk_off/recent_win_rate (matching
    replay_should_enter()'s own documented live_regime omission — no historical regime-
    persistence table exists to reconstruct those from) and WITHOUT signal_data["ts"] (Layer 3e
    freshness reads the real wall-clock with no as_of injection — see ScoreReplayInput's own
    field-level comment for why omitting it entirely, not sending a stale value, is the correct
    fix). Both are real, disclosed simplifications, not silent gaps — this endpoint only ever
    exercises the score layers that are genuinely point-in-time reconstructible today.

    Also applies the item #3 max_breakout_extension_pct HARD reject (hard_rejects.py's own
    hardcoded 6.0% breakout-extension check) as a pre-score gate — deliberately NOT calling the
    full check_hard_rejects() here, since most of that function's OTHER checks (time-of-day,
    market-hours) read the real wall-clock with no as_of injection, the exact problem this
    endpoint's own freshness-layer omission above already works around. This one specific check
    is pure (only live_price/game_plan/cfg), so it's inlined directly rather than dragging in
    the rest of that function's wall-clock-dependent machinery.
    """
    results: list[ScoreReplayResult] = []
    for item in req.inputs:
        breakout = (item.game_plan or {}).get("breakout")
        if breakout and float(breakout) > 0:
            ext_pct = (item.live_price / float(breakout) - 1) * 100
            threshold = req.cfg.get("max_breakout_extension_pct", 6.0)
            if ext_pct > threshold:
                results.append(ScoreReplayResult(
                    signal_id=item.signal_id,
                    score=0,
                    min_score=0,
                    entered=False,
                    pct_return=item.pct_return,
                ))
                continue

        signal_data = {
            "reasons": item.reasons,
            "bullish_probability": item.bullish_probability,
            # ts deliberately omitted — see ScoreReplayInput's own comment.
        }
        score, _breakdown = compute_score(
            live_price=item.live_price,
            game_plan=item.game_plan,
            signal_data=signal_data,
            research_rec=item.research_rec,
            research_score_val=item.research_score_val,
            regime_state=item.regime_state,
            cfg={**req.cfg, "kscore": item.kscore},
            is_pre_choppy=False,
            is_pre_risk_off=False,
            recent_win_rate=None,
        )
        min_score = min_score_for_regime(item.regime_state, req.cfg)
        results.append(ScoreReplayResult(
            signal_id=item.signal_id,
            score=score,
            min_score=min_score,
            entered=score >= min_score,
            pct_return=item.pct_return,
        ))
    return ScoreReplayResponse(results=results)

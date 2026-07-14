"""Decision Engine API routes."""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from common.jwt_auth import get_current_username

from .core.aggregator import build_game_plan, extract_live_price, fetch_all
from .core.hard_rejects import check_hard_rejects
from .core.models import (
    BatchDecisionRequest,
    DecisionRequest,
    DecisionResult,
    Factors,
    Multipliers,
    PositionPlan,
    ScoreItem,
)
from .core.regime import aget_regime, get_regime
from .core.scorer import compute_score, min_score_for_regime
from .core.sizer import combined_market_mult, compute_position
from .llm_scorer import score_with_llm

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
        game_plan = {k: float(v) for k, v in req.game_plan.items()}
    else:
        game_plan = build_game_plan(live_price, style, signal_data)

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
        research_rec   = research_data.get("recommendation") or research_data.get("ai_verdict", {}).get("final_recommendation")
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
        cfg=cfg,
        research_rec=research_rec,
        game_plan=game_plan,
        market=market,
        reasons=reasons,
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

    # 11. Verdict
    if score >= min_score:
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
        llm_reasoning=llm_reasoning,
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

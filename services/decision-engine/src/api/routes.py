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
from .core.regime import get_regime
from .core.scorer import compute_score, min_score_for_regime
from .core.sizer import compute_position

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

    # 5. Resolve research fields
    research_rec   = None
    research_score = None
    if research_data:
        research_rec   = research_data.get("recommendation") or research_data.get("ai_verdict", {}).get("final_recommendation")
        research_score = research_data.get("overall_score")
        if research_score is not None:
            research_score = float(research_score)

    # 6. Market regime
    regime = get_regime(req.market)
    regime_state = regime.get("state", "neutral")

    # 7. Hard rejects
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
    )

    factors = Factors(
        signal_direction=sig_direction,
        signal_confidence=round(confidence, 2),
        ml_bull_prob=float(reasons.get("ml_probability", 0) or (signal_data or {}).get("bullish_probability") or 0) or None,
        research_recommendation=research_rec,
        research_score=research_score,
        regime=regime_state,
        volume_z=float(reasons["volume_z"]) if reasons.get("volume_z") is not None else None,
        days_to_earnings=dte_int,
        conf_delta=float(reasons.get("confidence_delta") or (signal_data or {}).get("confidence_delta") or 0) or None,
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
    score, breakdown = compute_score(
        live_price=live_price,
        game_plan=game_plan,
        signal_data=signal_data or {},
        research_rec=research_rec,
        research_score_val=research_score,
        regime_state=regime_state,
        cfg=cfg,
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
    )

    # 10. Verdict
    if score >= min_score:
        verdict = "BUY"
    elif score >= min_score - 2:
        verdict = "HOLD"
    else:
        verdict = "SKIP"

    latency = int((_time.monotonic() - t0) * 1000)

    log.info(
        "decision.evaluated",
        symbol=symbol, style=style, verdict=verdict,
        score=score, min_score=min_score,
        regime=regime_state, latency_ms=latency,
    )

    return DecisionResult(
        symbol=symbol, style=style,
        verdict=verdict, score=score, min_score=min_score,
        position=position if verdict == "BUY" else None,
        factors=factors, multipliers=multipliers,
        score_breakdown=breakdown,
        blocked_reason=None,
        latency_ms=latency,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/decide/{symbol}", response_model=DecisionResult)
async def decide(
    symbol: str,
    req: DecisionRequest,
    _: str = Depends(get_current_username),
):
    """Evaluate whether to enter a position in {symbol} right now.

    Aggregates signal engine, ML probability, research recommendation, and market
    regime into a single verdict (BUY / HOLD / SKIP / BLOCKED) with full position
    sizing and per-layer score breakdown.
    """
    symbol = symbol.upper()
    return await _decide(symbol, req)


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
        sign = "+" if item.pts > 0 else ""
        lines.append(f"  [{sign}{item.pts:+d}] {item.layer}: {item.note}")

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


@router.get("/regime")
def regime_status(market: str = "US", _: str = Depends(get_current_username)):
    """Return current market regime for US or HK."""
    return get_regime(market.upper())

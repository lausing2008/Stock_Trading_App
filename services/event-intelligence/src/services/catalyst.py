"""Catalyst, Risk, and AI Composite Scoring Engine."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_session, SessionLocal, CatalystScore, Stock

from .earnings import get_days_to_earnings, get_beat_rate
from .insider import compute_insider_score
from .congress import compute_congress_score, days_since_last_congress_buy
from .institutional import compute_institutional_score
from .economic import days_to_next_fomc
from .insider import get_insider_for_symbol

log = structlog.get_logger()


def _compute_earnings_score(stock_id: int) -> tuple[float, int | None]:
    """Return (earnings_score 0-100, days_out)."""
    days_out = get_days_to_earnings(stock_id)
    beat_rate = get_beat_rate(stock_id) or 0.5

    score = 0.0
    if days_out is not None:
        if days_out <= 3:    score += 50
        elif days_out <= 7:  score += 35
        elif days_out <= 14: score += 20
        elif days_out <= 30: score += 10

    if beat_rate > 0.80:   score += 25
    elif beat_rate > 0.65: score += 15
    elif beat_rate > 0.50: score += 5

    return min(100.0, score), days_out


def _compute_economic_score() -> float:
    """Score based on proximity to high-impact economic events."""
    fomc_days = days_to_next_fomc()
    if fomc_days is None:
        return 0.0
    if fomc_days <= 2:   return 80.0
    if fomc_days <= 7:   return 40.0
    if fomc_days <= 14:  return 20.0
    return 5.0


def _compute_risk_score(
    stock_id: int,
    earnings_days_out: int | None,
    insider_score: float,
    atr_pct: float = 0.0,
) -> float:
    """0-100 risk score (higher = more risky)."""
    risk = 0.0

    # Earnings proximity risk
    if earnings_days_out is not None:
        if earnings_days_out <= 1:  risk += 35
        elif earnings_days_out <= 3: risk += 25
        elif earnings_days_out <= 7: risk += 15

    # Volatility risk (ATR % passed from signal)
    if atr_pct > 0.06:   risk += 20
    elif atr_pct > 0.04: risk += 12
    elif atr_pct > 0.02: risk += 5

    # Insider selling risk
    if insider_score < -30: risk += 25
    elif insider_score < -10: risk += 12

    # Congress selling risk
    congress = compute_congress_score(stock_id)
    if congress < 0:
        risk += 15

    # FOMC risk
    fomc_days = days_to_next_fomc()
    if fomc_days is not None and fomc_days <= 2:
        risk += 20

    return min(100.0, risk)


def _compute_composite(
    technical_score: float,
    earnings_score: float,
    catalyst_score: float,
    insider_score: float,
    congress_score: float,
    institutional_score: float,
    risk_score: float,
) -> float:
    """AI Composite Score (0-100)."""
    raw = (
        0.25 * technical_score
        + 0.20 * catalyst_score
        + 0.20 * earnings_score
        + 0.15 * max(insider_score, 0)
        + 0.10 * congress_score
        + 0.10 * institutional_score
    )
    risk_dampen = 1.0 - 0.05 * (risk_score / 100.0)
    return min(100.0, max(0.0, raw * risk_dampen))


def compute_and_store(stock_id: int, technical_score: float = 50.0, atr_pct: float = 0.0) -> dict:
    """Compute all scores for a stock and upsert to catalyst_scores table."""
    earnings_score, days_out = _compute_earnings_score(stock_id)
    insider_score = compute_insider_score(stock_id)
    congress_score = compute_congress_score(stock_id)
    institutional_score = compute_institutional_score(stock_id)
    economic_score = _compute_economic_score()
    risk_score = _compute_risk_score(stock_id, days_out, insider_score, atr_pct)

    catalyst_score = (
        0.35 * max(insider_score, 0)
        + 0.30 * earnings_score
        + 0.25 * congress_score
        + 0.10 * economic_score
    )
    catalyst_score = min(100.0, max(0.0, catalyst_score))

    composite_score = _compute_composite(
        technical_score, earnings_score, catalyst_score,
        insider_score, congress_score, institutional_score, risk_score,
    )

    last_congress = days_since_last_congress_buy(stock_id)
    last_insider = _days_since_last_insider_buy(stock_id)

    with SessionLocal() as s:
        stmt = (
            pg_insert(CatalystScore)
            .values(
                stock_id=stock_id,
                catalyst_score=round(catalyst_score, 1),
                earnings_score=round(earnings_score, 1),
                insider_score=round(insider_score, 1),
                congress_score=round(congress_score, 1),
                institutional_score=round(institutional_score, 1),
                economic_score=round(economic_score, 1),
                risk_score=round(risk_score, 1),
                composite_score=round(composite_score, 1),
                earnings_days_out=days_out,
                last_insider_days=last_insider,
                last_congress_days=last_congress,
                computed_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                constraint="uq_catalyst_stock",
                set_=dict(
                    catalyst_score=round(catalyst_score, 1),
                    earnings_score=round(earnings_score, 1),
                    insider_score=round(insider_score, 1),
                    congress_score=round(congress_score, 1),
                    institutional_score=round(institutional_score, 1),
                    economic_score=round(economic_score, 1),
                    risk_score=round(risk_score, 1),
                    composite_score=round(composite_score, 1),
                    earnings_days_out=days_out,
                    last_insider_days=last_insider,
                    last_congress_days=last_congress,
                    computed_at=datetime.now(timezone.utc),
                ),
            )
        )
        s.execute(stmt)
        s.commit()

    return {
        "stock_id": stock_id,
        "catalyst_score": round(catalyst_score, 1),
        "earnings_score": round(earnings_score, 1),
        "insider_score": round(insider_score, 1),
        "congress_score": round(congress_score, 1),
        "institutional_score": round(institutional_score, 1),
        "economic_score": round(economic_score, 1),
        "risk_score": round(risk_score, 1),
        "composite_score": round(composite_score, 1),
        "earnings_days_out": days_out,
        "last_insider_days": last_insider,
        "last_congress_days": last_congress,
    }


def _days_since_last_insider_buy(stock_id: int) -> int | None:
    from datetime import date
    txns = get_insider_for_symbol(stock_id, 365)
    buys = [t for t in txns if t["transaction_type"] == "purchase"]
    if not buys:
        return None
    latest = max(date.fromisoformat(t["transaction_date"]) for t in buys)
    return (date.today() - latest).days


def get_catalyst(stock_id: int) -> dict | None:
    with SessionLocal() as s:
        row = s.execute(
            select(CatalystScore).where(CatalystScore.stock_id == stock_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        return _score_to_dict(row)


def get_catalyst_leaderboard(limit: int = 20) -> list[dict]:
    with SessionLocal() as s:
        rows = s.execute(
            select(CatalystScore, Stock.symbol, Stock.name)
            .join(Stock, CatalystScore.stock_id == Stock.id)
            .where(CatalystScore.catalyst_score.isnot(None))
            .order_by(CatalystScore.catalyst_score.desc())
            .limit(limit)
        ).all()
        return [
            {**_score_to_dict(cs), "symbol": sym, "company": name}
            for cs, sym, name in rows
        ]


def get_risk_leaderboard(limit: int = 20) -> list[dict]:
    with SessionLocal() as s:
        rows = s.execute(
            select(CatalystScore, Stock.symbol, Stock.name)
            .join(Stock, CatalystScore.stock_id == Stock.id)
            .where(CatalystScore.risk_score.isnot(None))
            .order_by(CatalystScore.risk_score.desc())
            .limit(limit)
        ).all()
        return [
            {**_score_to_dict(cs), "symbol": sym, "company": name}
            for cs, sym, name in rows
        ]


def get_composite_leaderboard(limit: int = 20) -> list[dict]:
    with SessionLocal() as s:
        rows = s.execute(
            select(CatalystScore, Stock.symbol, Stock.name)
            .join(Stock, CatalystScore.stock_id == Stock.id)
            .where(CatalystScore.composite_score.isnot(None))
            .order_by(CatalystScore.composite_score.desc())
            .limit(limit)
        ).all()
        return [
            {**_score_to_dict(cs), "symbol": sym, "company": name}
            for cs, sym, name in rows
        ]


async def recompute_all(technical_scores: dict[int, float] | None = None) -> dict:
    """Recompute catalyst scores for all tracked stocks."""
    with SessionLocal() as s:
        stocks = s.execute(select(Stock.id, Stock.symbol)).all()

    ts = technical_scores or {}
    computed = 0
    for stock_id, symbol in stocks:
        try:
            compute_and_store(stock_id, technical_score=ts.get(stock_id, 50.0))
            computed += 1
        except Exception as exc:
            log.warning("catalyst.compute_fail", symbol=symbol, error=str(exc))

    return {"computed": computed, "total": len(stocks)}


def _score_to_dict(cs: CatalystScore) -> dict:
    return {
        "stock_id": cs.stock_id,
        "catalyst_score": cs.catalyst_score,
        "earnings_score": cs.earnings_score,
        "insider_score": cs.insider_score,
        "congress_score": cs.congress_score,
        "institutional_score": cs.institutional_score,
        "economic_score": cs.economic_score,
        "risk_score": cs.risk_score,
        "composite_score": cs.composite_score,
        "earnings_days_out": cs.earnings_days_out,
        "last_insider_days": cs.last_insider_days,
        "last_congress_days": cs.last_congress_days,
        "computed_at": cs.computed_at.isoformat() if cs.computed_at else None,
    }

from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import Signal, SignalHorizon, SignalType, Stock, get_session

from ..generators import generate_signal

log = get_logger("signals")

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
def all_latest_signals(session: Session = Depends(get_session)):
    """Return the most recently persisted signal for every active stock."""
    latest_subq = (
        select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
        .group_by(Signal.stock_id)
        .subquery()
    )
    rows = session.execute(
        select(Stock.symbol, Signal.signal, Signal.horizon, Signal.confidence, Signal.bullish_probability, Signal.ts)
        .join(Signal, Stock.id == Signal.stock_id)
        .join(latest_subq, (Signal.stock_id == latest_subq.c.stock_id) & (Signal.ts == latest_subq.c.max_ts))
        .where(Stock.active.is_(True))
    ).all()
    return [
        {
            "symbol": row.symbol,
            "signal": row.signal.value,
            "horizon": row.horizon.value,
            "confidence": row.confidence,
            "bullish_probability": row.bullish_probability,
            "ts": row.ts.isoformat() if row.ts else None,
        }
        for row in rows
    ]


@router.get("/{symbol}")
def signal_for(symbol: str, persist: bool = False, session: Session = Depends(get_session)):
    try:
        ai = generate_signal(symbol)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    if persist:
        stock = session.query(Stock).filter(Stock.symbol == symbol).one_or_none()
        if stock:
            session.add(
                Signal(
                    stock_id=stock.id,
                    signal=SignalType(ai.signal),
                    horizon=SignalHorizon(ai.horizon),
                    confidence=ai.confidence,
                    bullish_probability=ai.bullish_probability,
                    reasons=ai.reasons,
                )
            )
            session.commit()
    return {"symbol": symbol, **asdict(ai)}


@router.post("/refresh")
def refresh_signals(
    tasks: BackgroundTasks,
    market: str | None = None,
    session: Session = Depends(get_session),
):
    """Recompute and persist signals for all active stocks, optionally filtered by market."""
    q = select(Stock.symbol).where(Stock.active.is_(True))
    if market:
        q = q.where(Stock.market == market.upper())
    symbols = list(session.execute(q).scalars())
    tasks.add_task(_bulk_persist, symbols)
    return {"status": "scheduled", "count": len(symbols)}


def _bulk_persist(symbols: list[str]) -> None:
    from db import SessionLocal
    for symbol in symbols:
        try:
            ai = generate_signal(symbol)
            with SessionLocal() as s:
                stock = s.query(Stock).filter(Stock.symbol == symbol).one_or_none()
                if stock:
                    s.add(Signal(
                        stock_id=stock.id,
                        signal=SignalType(ai.signal),
                        horizon=SignalHorizon(ai.horizon),
                        confidence=ai.confidence,
                        bullish_probability=ai.bullish_probability,
                        reasons=ai.reasons,
                    ))
                    s.commit()
        except Exception as exc:
            log.warning("signals.refresh.skip", symbol=symbol, error=str(exc))

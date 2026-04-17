from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db import Signal, SignalHorizon, SignalType, Stock, get_session

from ..generators import generate_signal

router = APIRouter(prefix="/signals", tags=["signals"])


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

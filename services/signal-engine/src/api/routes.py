from dataclasses import asdict
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import Price, Signal, SignalHorizon, SignalType, Stock, TimeFrame, get_session

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


@router.post("/reset")
def reset_signals(tasks: BackgroundTasks, session: Session = Depends(get_session)):
    """Wipe all persisted signals then re-persist fresh ones for every active stock."""
    deleted = session.query(Signal).delete()
    session.commit()
    symbols = list(session.execute(select(Stock.symbol).where(Stock.active.is_(True))).scalars())
    tasks.add_task(_bulk_persist, symbols)
    log.info("signals.reset", deleted=deleted, repersisting=len(symbols))
    return {"status": "reset", "deleted": deleted, "repersisting": len(symbols)}


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


@router.get("/accuracy")
def signal_accuracy(
    lookback_days: int = Query(90, ge=2, le=365),
    symbol: str | None = None,
    session: Session = Depends(get_session),
):
    """Historical accuracy of BUY/SELL signals vs actual price outcomes.

    For each persisted BUY or SELL signal within the lookback window, compares
    the close price on the signal date to the most recent available close price.
    A BUY is 'correct' if price rose; a SELL is 'correct' if it fell.
    Signals need at least 1 day of price history after the signal date to be evaluated.
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    outcome_cutoff = datetime.utcnow() - timedelta(days=1)

    q = (
        select(Signal, Stock.symbol, Stock.name)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal.in_([SignalType.BUY, SignalType.SELL]))
        .order_by(Signal.ts.desc())
    )
    if symbol:
        q = q.where(Stock.symbol == symbol.upper())

    rows = session.execute(q).all()

    results = []
    for sig, sym, name in rows:
        signal_date = sig.ts.date()

        # Entry: the most recent close on or before the signal date.
        # Handles weekend/holiday signals where no bar exists on the signal day.
        entry_row = session.execute(
            select(Price.close)
            .where(Price.stock_id == sig.stock_id, Price.timeframe == TimeFrame.D1)
            .where(Price.ts <= signal_date)
            .order_by(Price.ts.desc())
            .limit(1)
        ).scalar_one_or_none()

        # Exit: most recent close after the signal date — measures cumulative
        # return from signal issuance to the latest available price.
        exit_row = session.execute(
            select(Price.close, Price.ts)
            .where(Price.stock_id == sig.stock_id, Price.timeframe == TimeFrame.D1)
            .where(Price.ts > signal_date)
            .order_by(Price.ts.desc())
            .limit(1)
        ).first()

        if entry_row is None or exit_row is None:
            continue

        entry_close = float(entry_row)
        exit_close  = float(exit_row[0])
        pct_change  = (exit_close - entry_close) / entry_close * 100
        signal_type = sig.signal.value
        correct     = (signal_type == "BUY" and pct_change > 0) or (signal_type == "SELL" and pct_change < 0)

        results.append({
            "symbol": sym,
            "name": name,
            "signal": signal_type,
            "confidence": round(sig.confidence, 1),
            "bullish_probability": round(sig.bullish_probability, 4) if sig.bullish_probability else None,
            "signal_date": signal_date.isoformat(),
            "entry_price": round(entry_close, 4),
            "exit_price": round(exit_close, 4),
            "pct_change": round(pct_change, 2),
            "correct": correct,
            "days_held": (exit_row[1].date() - signal_date).days,
        })

    buy_r  = [r for r in results if r["signal"] == "BUY"]
    sell_r = [r for r in results if r["signal"] == "SELL"]

    def _accuracy(items: list) -> float | None:
        return round(sum(1 for i in items if i["correct"]) / len(items) * 100, 1) if items else None

    def _avg_return(items: list) -> float | None:
        return round(sum(i["pct_change"] for i in items) / len(items), 2) if items else None

    def _profit_factor(items: list) -> float | None:
        wins  = sum(i["pct_change"] for i in items if i["correct"])
        losses = abs(sum(i["pct_change"] for i in items if not i["correct"]))
        return round(wins / losses, 2) if losses > 0 else None

    return {
        "lookback_days": lookback_days,
        "total_signals": len(results),
        "buy_count": len(buy_r),
        "sell_count": len(sell_r),
        "buy_accuracy": _accuracy(buy_r),
        "sell_accuracy": _accuracy(sell_r),
        "overall_accuracy": _accuracy(results),
        "avg_buy_return_pct": _avg_return(buy_r),
        "avg_sell_return_pct": _avg_return(sell_r),
        "profit_factor": _profit_factor(results),
        "signals": results,
    }


@router.get("/{symbol}")
def signal_for(symbol: str, persist: bool = False, session: Session = Depends(get_session)):
    """Generate (and optionally persist) a fresh signal for the given symbol."""
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

"""Earnings Intelligence — yfinance earnings history + upcoming calendar."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_session, SessionLocal, EarningsEvent, Stock

log = structlog.get_logger()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yf_earnings")


def _fetch_earnings_for_symbol(symbol: str, stock_id: int) -> int:
    """Fetch earnings history + calendar from yfinance and upsert to DB. Returns rows upserted."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        upserted = 0

        # Historical earnings (EPS beats)
        try:
            hist = ticker.earnings_history
            if hist is not None and not hist.empty:
                with SessionLocal() as s:
                    for idx, row in hist.iterrows():
                        try:
                            report_date = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                            eps_est = row.get("epsEstimate") if pd.notna(row.get("epsEstimate")) else None
                            eps_act = row.get("epsActual") if pd.notna(row.get("epsActual")) else None
                            surprise = None
                            if eps_est and eps_act and eps_est != 0:
                                surprise = round((eps_act - eps_est) / abs(eps_est) * 100, 2)
                            # Infer quarter from month
                            fq = (report_date.month - 1) // 3 + 1
                            fy = report_date.year
                            strength = _compute_strength(eps_est, eps_act, surprise)
                            stmt = (
                                pg_insert(EarningsEvent)
                                .values(
                                    stock_id=stock_id,
                                    report_date=report_date,
                                    period=f"Q{fq} {fy}",
                                    fiscal_year=fy,
                                    fiscal_quarter=fq,
                                    eps_estimate=eps_est,
                                    eps_actual=eps_act,
                                    surprise_pct=surprise,
                                    earnings_strength_score=strength,
                                )
                                .on_conflict_do_update(
                                    constraint="uq_earnings_stock_period",
                                    set_=dict(
                                        eps_estimate=eps_est,
                                        eps_actual=eps_act,
                                        surprise_pct=surprise,
                                        earnings_strength_score=strength,
                                        report_date=report_date,
                                    ),
                                )
                            )
                            result = s.execute(stmt)
                            upserted += result.rowcount
                        except Exception:
                            continue
                    s.commit()
        except Exception as exc:
            log.debug("earnings.history_skip", symbol=symbol, error=str(exc))

        # Upcoming earnings date (calendar)
        try:
            cal = ticker.calendar
            if cal is not None:
                earnings_dt = cal.get("Earnings Date")
                if earnings_dt is not None:
                    if hasattr(earnings_dt, "__iter__") and not isinstance(earnings_dt, str):
                        earnings_dt = list(earnings_dt)[0]
                    if hasattr(earnings_dt, "date"):
                        upcoming = earnings_dt.date()
                    else:
                        upcoming = date.fromisoformat(str(earnings_dt)[:10])
                    eps_est = cal.get("EPS Estimate")
                    rev_est = cal.get("Revenue Estimate")
                    fq = (upcoming.month - 1) // 3 + 1
                    fy = upcoming.year
                    with SessionLocal() as s:
                        stmt = (
                            pg_insert(EarningsEvent)
                            .values(
                                stock_id=stock_id,
                                report_date=upcoming,
                                period=f"Q{fq} {fy}",
                                fiscal_year=fy,
                                fiscal_quarter=fq,
                                eps_estimate=float(eps_est) if eps_est and pd.notna(eps_est) else None,
                                revenue_estimate=float(rev_est) if rev_est and pd.notna(rev_est) else None,
                            )
                            .on_conflict_do_update(
                                constraint="uq_earnings_stock_period",
                                set_=dict(
                                    report_date=upcoming,
                                    eps_estimate=float(eps_est) if eps_est and pd.notna(eps_est) else None,
                                    revenue_estimate=float(rev_est) if rev_est and pd.notna(rev_est) else None,
                                ),
                            )
                        )
                        s.execute(stmt)
                        s.commit()
                        upserted += 1
        except Exception as exc:
            log.debug("earnings.calendar_skip", symbol=symbol, error=str(exc))

        return upserted
    except Exception as exc:
        log.warning("earnings.symbol_fail", symbol=symbol, error=str(exc))
        return 0


def _compute_strength(eps_est: float | None, eps_act: float | None, surprise_pct: float | None) -> float | None:
    """0-100 earnings strength score based on beat size."""
    if eps_act is None:
        return None
    score = 50.0
    if surprise_pct is not None:
        if surprise_pct > 20:    score += 30
        elif surprise_pct > 10:  score += 20
        elif surprise_pct > 5:   score += 10
        elif surprise_pct < -10: score -= 20
        elif surprise_pct < -5:  score -= 10
    if eps_act and eps_act > 0:
        score += 10
    return max(0.0, min(100.0, score))


async def sync_all_earnings() -> dict:
    """Sync earnings for all tracked stocks. Runs yfinance calls in thread pool."""
    with SessionLocal() as s:
        stocks = s.execute(select(Stock.id, Stock.symbol)).all()

    loop = asyncio.get_running_loop()
    total = 0
    for stock_id, symbol in stocks:
        n = await loop.run_in_executor(_executor, _fetch_earnings_for_symbol, symbol, stock_id)
        total += n
        await asyncio.sleep(0.2)  # gentle rate limiting

    return {"symbols_processed": len(stocks), "rows_upserted": total}


def get_earnings_for_symbol(stock_id: int, days_back: int = 365) -> list[dict]:
    since = date.today() - timedelta(days=days_back)
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent)
            .where(EarningsEvent.stock_id == stock_id, EarningsEvent.report_date >= since)
            .order_by(EarningsEvent.report_date.desc())
        ).scalars().all()
        return [_row_to_dict(e) for e in rows]


def get_upcoming_earnings(days: int = 14) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days)
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent, Stock.symbol, Stock.name)
            .join(Stock, EarningsEvent.stock_id == Stock.id)
            .where(EarningsEvent.report_date >= today, EarningsEvent.report_date <= cutoff)
            .order_by(EarningsEvent.report_date)
        ).all()
        return [
            {
                **_row_to_dict(e),
                "symbol": symbol,
                "company": name,
            }
            for e, symbol, name in rows
        ]


def get_days_to_earnings(stock_id: int) -> int | None:
    today = date.today()
    with SessionLocal() as s:
        row = s.execute(
            select(EarningsEvent.report_date)
            .where(EarningsEvent.stock_id == stock_id, EarningsEvent.report_date >= today)
            .order_by(EarningsEvent.report_date)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return (row - today).days


def get_beat_rate(stock_id: int, lookback: int = 8) -> float | None:
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent.surprise_pct)
            .where(EarningsEvent.stock_id == stock_id, EarningsEvent.surprise_pct.isnot(None))
            .order_by(EarningsEvent.report_date.desc())
            .limit(lookback)
        ).scalars().all()
        if not rows:
            return None
        beats = sum(1 for x in rows if x > 0)
        return round(beats / len(rows), 2)


def _row_to_dict(e: EarningsEvent) -> dict:
    return {
        "id": e.id,
        "stock_id": e.stock_id,
        "report_date": e.report_date.isoformat(),
        "period": e.period,
        "fiscal_year": e.fiscal_year,
        "fiscal_quarter": e.fiscal_quarter,
        "eps_estimate": e.eps_estimate,
        "eps_actual": e.eps_actual,
        "revenue_estimate": e.revenue_estimate,
        "revenue_actual": e.revenue_actual,
        "surprise_pct": e.surprise_pct,
        "revenue_surprise_pct": e.revenue_surprise_pct,
        "earnings_strength_score": e.earnings_strength_score,
        "post_earnings_return_1d": e.post_earnings_return_1d,
    }

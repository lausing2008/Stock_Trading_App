"""Persist the same UW chain read served by the route, with its evidence limitations.

No second yfinance aggregation. Chain turnover is not traded tape premium; unknown premium
and whale fields remain NULL. Snapshot source session is separate from computation time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from db import OptionsFlowSnapshot
from sqlalchemy.dialects.postgresql import insert as pg_insert

import structlog

log = structlog.get_logger()


@dataclass
class OptionsFlowResult:
    cp_ratio: float
    cp_ratio_uncapped: float
    call_volume: int
    put_volume: int
    call_premium: float | None
    put_premium: float | None
    whale_count: int | None
    top_whale_premium: float | None
    sentiment: str
    evidence: dict | None = None


def _fetch_flow_response(symbol: str) -> dict:
    from db import SessionLocal
    from ..api.routes import get_options_flow
    with SessionLocal() as session:
        return get_options_flow(symbol, session=session)


def compute_options_flow(symbol: str) -> OptionsFlowResult | None:
    try:
        data = _fetch_flow_response(symbol)
        if not data.get("available"):
            return None
        calls, puts = data["call_volume"], data["put_volume"]
        return OptionsFlowResult(
            cp_ratio=data["cp_ratio"], cp_ratio_uncapped=round(calls / max(puts, 1), 2),
            call_volume=calls, put_volume=puts,
            call_premium=None, put_premium=None, whale_count=None, top_whale_premium=None,
            sentiment=data.get("activity_composition", "unknown"),
            evidence={key: data.get(key) for key in (
                "chain_as_of", "coverage_status", "expiries_used", "expiries_attempted",
                "directional_intent", "independence_group", "source")},
        )
    except Exception as exc:
        log.warning("options_flow_snapshot.compute_failed", symbol=symbol, error=str(exc))
        return None


def upsert_options_flow_snapshot(
    session, stock_id: int, result: OptionsFlowResult, as_of: date | None = None
) -> None:
    """Upsert one OptionsFlowSnapshot row. Idempotent via ON CONFLICT DO UPDATE on
    (stock_id, as_of) — safe to re-run for the same day without creating duplicate rows.
    Does NOT commit — the caller (the EOD batch job) commits once after the whole batch,
    matching this repo's own convention of one commit per batch rather than per-row.
    """
    # AUD-T409-UTCDATEBOUNDARY: naive UTC truncation reads one calendar day ahead of the real
    # US trading day for ~4-5 hours every evening — as_of is the (stock_id, as_of) upsert key,
    # so a late/retried/manually-triggered run in that window would mis-date this snapshot.
    from zoneinfo import ZoneInfo
    as_of = as_of or datetime.now(ZoneInfo("America/New_York")).date()
    values = dict(
        stock_id=stock_id,
        as_of=as_of,
        cp_ratio=result.cp_ratio,
        cp_ratio_uncapped=result.cp_ratio_uncapped,
        call_volume=result.call_volume,
        put_volume=result.put_volume,
        call_premium=result.call_premium,
        put_premium=result.put_premium,
        whale_count=result.whale_count,
        top_whale_premium=result.top_whale_premium,
        sentiment=result.sentiment,
        evidence=result.evidence,
    )
    stmt = pg_insert(OptionsFlowSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id", "as_of"],
        set_={k: v for k, v in values.items() if k not in ("stock_id", "as_of")},
    )
    session.execute(stmt)


def get_latest_options_flow(session, stock_id: int) -> OptionsFlowSnapshot | None:
    """Most recent OptionsFlowSnapshot row for a stock, or None if never computed."""
    from sqlalchemy import select

    return session.execute(
        select(OptionsFlowSnapshot)
        .where(OptionsFlowSnapshot.stock_id == stock_id)
        .order_by(OptionsFlowSnapshot.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()

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


def expected_flow_session(job_date: date) -> date:
    """The settled session a snapshot taken on `job_date` (ET) should describe (2026-10-22).

    A session's settled chain reaches the archive the next day (captured after settlement), so
    a 17:00 ET run on day D can at best describe the trading session before D. Older is stale.
    """
    from datetime import timedelta
    from common.market_calendar import session_bounds
    d = job_date - timedelta(days=1)
    for _ in range(10):
        if session_bounds("US", d) is not None:
            return d
        d -= timedelta(days=1)
    raise ValueError(f"no US session within 10 days before {job_date}")


def last_job_day(brief_date: date) -> date:
    """The most recent weekday before `brief_date`: the EOD job's cron is Mon-Fri, holidays included."""
    from datetime import timedelta
    d = brief_date - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def compute_options_flow(symbol: str) -> OptionsFlowResult | None:
    return compute_options_flow_with_reason(symbol)[0]


def compute_options_flow_with_reason(symbol: str) -> tuple[OptionsFlowResult | None, str | None]:
    """(result, None) or (None, why): the job records the reason for every symbol it skips."""
    try:
        data = _fetch_flow_response(symbol)
        if not data.get("available"):
            return None, str(data.get("reason") or "unavailable")
        calls, puts = data["call_volume"], data["put_volume"]
        if data.get("cp_ratio") is None:
            # Audit R04: one side's volume was entirely unmeasured, so no ratio exists. Persisting
            # one from a zero subtotal would store "all calls" for a chain whose puts nobody saw.
            return None, "volume_side_unmeasured"
        return OptionsFlowResult(
            cp_ratio=data["cp_ratio"], cp_ratio_uncapped=round(calls / max(puts, 1), 2),
            call_volume=calls, put_volume=puts,
            call_premium=None, put_premium=None, whale_count=None, top_whale_premium=None,
            sentiment=data.get("activity_composition", "unknown"),
            evidence={key: data.get(key) for key in (
                "chain_as_of", "coverage_status", "expiries_used", "expiries_attempted",
                "directional_intent", "independence_group", "source",
                # Phase B (2026-10-22): `sentiment` above now comes from EXACT volumes. Rows
                # without these keys were labelled from the rounded/capped ratio, so boundary
                # rows (e.g. 1296/1000, or puts == 0) differ across the cut-over.
                "composition_rule", "cp_ratio_basis", "call_share", "put_share",
                "sample_adequacy", "archive_coverage_status",
                # R04: a snapshot over an observed subtotal says so.
                "volume_coverage", "composition_scope")},
        ), None
    except Exception as exc:
        log.warning("options_flow_snapshot.compute_failed", symbol=symbol, error=str(exc))
        return None, "compute_failed: " + str(exc)[:120]


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

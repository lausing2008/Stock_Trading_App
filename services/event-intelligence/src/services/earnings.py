"""Earnings Intelligence — yfinance earnings history + upcoming calendar."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.config import get_settings
from db import get_session, SessionLocal, EarningsEvent, Stock

log = structlog.get_logger()
_settings = get_settings()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yf_earnings")

# T249-EARNINGS-LLM-IMPACT: unlike macro_reaction.py's blocking httpx.get()/feedparser calls
# (which needed a dedicated executor — AUD-EI-MACRO-REACTION-BLOCKING), generate_earnings_
# impact() below uses httpx.AsyncClient, which is natively async and does not block the
# shared event loop — no ThreadPoolExecutor needed here.
_REDIS_EARNINGS_LLM_ENABLED = "stockai:admin:feature:earnings_llm_impact_enabled"

_IMPACT_SYSTEM = """You are an equity analyst producing a brief post-earnings impact read for a
retail trading app. You will receive a company's ticker, sector, EPS actual vs. estimate,
revenue actual vs. estimate, surprise percentages, and a computed earnings strength score
(0-100). Respond ONLY with valid JSON (no markdown, no explanation outside JSON) in this exact
format:
{"one_paragraph":"<2-3 sentences>","sectors_helped":["Technology"],"sectors_hurt":["Utilities"]}
one_paragraph must be 2-3 plain-English sentences a retail trader can act on, max 400 chars —
cover what the beat/miss means and any read-through risk (e.g. a weak print from a bellwether
can pressure its whole sector/peers, a strong print can lift them).
sectors_helped and sectors_hurt: 0-4 GICS-style sector names each (e.g. "Technology",
"Financials", "Energy", "Utilities", "Consumer Discretionary", "Healthcare", "Industrials",
"Materials", "Real Estate", "Communication Services", "Consumer Staples") that this specific
report plausibly helps or hurts — almost always includes the company's OWN sector, plus any
closely-related peer sector if there's a real read-through (e.g. a major chipmaker's earnings
affecting the broader semiconductor/tech supply chain). Use empty lists if you have no concrete
basis — never pad these lists to look complete."""


def _api_key() -> str:
    """AUD-DUPLOGIC: delegates to common.ai_keys.get_admin_ai_key(), matching every other
    Claude call site in this codebase (macro_reaction.py, decision-engine's llm_scorer.py/
    risk_agent.py, market-data's news.py, research-engine)."""
    from common.ai_keys import get_admin_ai_key
    return get_admin_ai_key("claude")


def _clean_sector_list(raw: object) -> list[str]:
    """Same validation as macro_reaction.py's own _clean_sector_list() — a bad/malformed
    sector list must never take down the reaction_text/impact_text it's paired with."""
    if not isinstance(raw, list):
        return []
    cleaned = [str(s).strip() for s in raw if isinstance(s, str) and str(s).strip()]
    return cleaned[:6]


async def generate_earnings_impact(
    symbol: str, sector: str | None, eps_actual: float, eps_estimate: float | None,
    surprise_pct: float | None, revenue_actual: float | None, revenue_estimate: float | None,
    revenue_surprise_pct: float | None, strength_score: float | None,
) -> dict | None:
    """LLM-generated earnings impact read — mirrors macro_reaction.py's generate_reaction()
    exactly (same model, same fail-open contract, same sector-impact structure), applied to a
    just-reported earnings print instead of a macro release. Fail-open: returns None on any
    error, matching every other LLM call site in this codebase — a missing reaction just means
    no impact text is available that cycle, never a broken page or a blocked write of the
    already-computed eps_actual/surprise_pct fields.
    """
    api_key = _api_key()
    if not api_key:
        log.info("earnings_impact.no_api_key", symbol=symbol)
        return None

    prompt = (
        f"Ticker: {symbol}\n"
        f"Sector: {sector or 'unknown'}\n"
        f"EPS actual: {eps_actual}\n"
        f"EPS estimate: {eps_estimate if eps_estimate is not None else 'unavailable'}\n"
        f"EPS surprise: {f'{surprise_pct:+.1f}%' if surprise_pct is not None else 'unavailable'}\n"
        f"Revenue actual: {revenue_actual if revenue_actual is not None else 'unavailable'}\n"
        f"Revenue estimate: {revenue_estimate if revenue_estimate is not None else 'unavailable'}\n"
        f"Revenue surprise: {f'{revenue_surprise_pct:+.1f}%' if revenue_surprise_pct is not None else 'unavailable'}\n"
        f"Earnings strength score (0-100): {strength_score if strength_score is not None else 'unavailable'}\n"
    )
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "temperature": 0.2,
        "system": _IMPACT_SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if r.status_code != 200:
            log.warning("earnings_impact.api_error", symbol=symbol, status=r.status_code, body=r.text[:200])
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
        impact_text = (data.get("one_paragraph") or "")[:500] or None
        if impact_text is None:
            return None
        return {
            "impact_text": impact_text,
            "sectors_helped": _clean_sector_list(data.get("sectors_helped")),
            "sectors_hurt": _clean_sector_list(data.get("sectors_hurt")),
        }
    except Exception as exc:
        log.warning("earnings_impact.call_failed", symbol=symbol, error=str(exc))
        return None


async def check_earnings_impact_poll() -> dict:
    """Detection half of the earnings LLM impact feature (delivery is market-data's
    check_earnings_reactions(), same detect-in-event-intelligence/deliver-in-market-data split
    already established for check_release_day_fast_poll()/check_macro_reaction_alerts()).

    Unlike macro's release-day-armed poll (which knows exactly which minute a release is due),
    earnings land unpredictably per company throughout the trading day/after-hours — so this
    simply scans for EarningsEvent rows where eps_actual has landed (via sync_all_earnings()'s
    existing daily sync) but impact_text hasn't been generated yet. Gated behind the
    earnings_llm_impact_enabled admin flag (default OFF, matching every other opt-in
    Claude-calling feature added since the CLAUDE-API-COST-AUDIT) — checked FIRST, before any
    DB query, so a disabled flag costs nothing.
    """
    try:
        from common.redis_client import get_redis
        if get_redis().get(_REDIS_EARNINGS_LLM_ENABLED) != "1":
            return {"checked": 0, "generated": 0, "skipped": "feature_disabled"}
    except Exception:
        return {"checked": 0, "generated": 0, "skipped": "feature_disabled"}

    cutoff = date.today() - timedelta(days=2)
    generated = 0
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent, Stock.symbol, Stock.sector)
            .join(Stock, EarningsEvent.stock_id == Stock.id)
            .where(
                EarningsEvent.report_date >= cutoff,
                EarningsEvent.eps_actual.isnot(None),
                EarningsEvent.impact_text.is_(None),
            )
        ).all()
        checked = len(rows)
        for ev, sym, sector in rows:
            try:
                impact = await generate_earnings_impact(
                    sym, sector, ev.eps_actual, ev.eps_estimate, ev.surprise_pct,
                    ev.revenue_actual, ev.revenue_estimate, ev.revenue_surprise_pct,
                    ev.earnings_strength_score,
                )
                if impact is None:
                    continue
                ev.impact_text = impact["impact_text"]
                ev.sectors_helped = json.dumps(impact["sectors_helped"])
                ev.sectors_hurt = json.dumps(impact["sectors_hurt"])
                ev.impact_generated_at = datetime.now(timezone.utc)
                s.commit()
                generated += 1
                log.info("earnings_impact.generated", symbol=sym)
            except Exception as exc:
                log.warning("earnings_impact.poll_error", symbol=sym, error=str(exc))

    return {"checked": checked, "generated": generated, "skipped": None}


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
                            if eps_est is not None and eps_act is not None and eps_est != 0:
                                surprise = round((eps_act - eps_est) / abs(eps_est) * 100, 2)
                            # Infer quarter from month
                            fq = (report_date.month - 1) // 3 + 1
                            fy = report_date.year
                            strength = _compute_strength(eps_est, eps_act, surprise)
                            # DQ-EARNINGS-FETCHED-AT-FROZEN: fetched_at has server_default=func.now(),
                            # which only fires on a fresh INSERT — once every (stock_id, period) row
                            # already exists from the initial backfill, every subsequent daily sync run
                            # is 100% UPDATEs via the conflict path, so fetched_at could never advance
                            # again no matter how many times the sync ran cleanly. This silently broke
                            # the earnings_events DQ staleness check (AUD232-DQ-MISSING-CHECKS) — it
                            # alerted "stale" every day despite a genuinely healthy daily sync, because
                            # the column it watches was structurally frozen. Set it explicitly on every
                            # upsert so it reflects "last time this row was actually touched," not
                            # "first time this row ever existed."
                            _now = datetime.now(timezone.utc)
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
                                    fetched_at=_now,
                                )
                                .on_conflict_do_update(
                                    constraint="uq_earnings_stock_period",
                                    set_=dict(
                                        eps_estimate=eps_est,
                                        eps_actual=eps_act,
                                        surprise_pct=surprise,
                                        earnings_strength_score=strength,
                                        report_date=report_date,
                                        fetched_at=_now,
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
                        # See DQ-EARNINGS-FETCHED-AT-FROZEN comment above — same reasoning applies
                        # to the upcoming-earnings-calendar upsert path.
                        _now = datetime.now(timezone.utc)
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
                                fetched_at=_now,
                            )
                            .on_conflict_do_update(
                                constraint="uq_earnings_stock_period",
                                set_=dict(
                                    report_date=upcoming,
                                    eps_estimate=float(eps_est) if eps_est and pd.notna(eps_est) else None,
                                    revenue_estimate=float(rev_est) if rev_est and pd.notna(rev_est) else None,
                                    fetched_at=_now,
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
    today = date.today()
    return {
        "id": e.id,
        "stock_id": e.stock_id,
        "earnings_date": e.report_date.isoformat(),  # matches TypeScript EarningsEvent.earnings_date
        "estimated_eps": e.eps_estimate,              # matches TypeScript EarningsEvent.estimated_eps
        "actual_eps": e.eps_actual,                   # matches TypeScript EarningsEvent.actual_eps
        "estimated_revenue": e.revenue_estimate,      # matches TypeScript EarningsEvent.estimated_revenue
        "actual_revenue": e.revenue_actual,           # matches TypeScript EarningsEvent.actual_revenue
        "surprise_pct": e.surprise_pct,
        "beat_rate": None,    # per-row historical beat rate requires extra query; shows '—' in UI
        "avg_beat_pct": None,
        "is_upcoming": e.report_date >= today,        # matches TypeScript EarningsEvent.is_upcoming
        "period": e.period,
        "fiscal_year": e.fiscal_year,
        "fiscal_quarter": e.fiscal_quarter,
        "earnings_strength_score": e.earnings_strength_score,
    }

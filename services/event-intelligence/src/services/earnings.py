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


def _match_report_dates_to_history(
    hist_rows: list[dict], announce_rows: list[dict]
) -> dict[str, date]:
    """AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: ticker.earnings_history's own
    index is the fiscal PERIOD-END date (labeled "quarter" by yfinance itself), NOT the real
    announcement date — confirmed live: AAPL's history shows an index entry of 2025-09-30 for
    the report whose real announcement (per ticker.earnings_dates, a separate property) landed
    2025-10-30, a full month later. The old code stored this period-end date directly into
    report_date, which every consumer in this codebase (is_upcoming, day-of-earnings matching,
    get_days_to_earnings, dedup keys) treats as the real announcement date — silently wrong for
    every historical row, not just future ones.

    earnings_history has no announcement-date field of its own, but earnings_dates does (its
    own index) — the two are joined here by matching on the reported EPS value (rounded to 2dp,
    which both sources report to), since that's the only value genuinely shared between them
    and confirmed to match exactly for the same real event. Returns {period_end_iso:
    real_announcement_date} for every period-end date a match was found for; a period-end with
    no matching announce row (a data gap on one side) is simply absent from the returned dict —
    the caller falls back to the period-end date itself rather than fabricating one.
    """
    by_eps: dict[float, date] = {}
    for row in announce_rows:
        eps_act = row.get("eps_actual")
        announce_date = row.get("announce_date")
        if eps_act is None or announce_date is None:
            continue
        by_eps.setdefault(round(eps_act, 2), announce_date)

    matched: dict[str, date] = {}
    for row in hist_rows:
        period_end = row.get("period_end")
        eps_act = row.get("eps_actual")
        if period_end is None or eps_act is None:
            continue
        real_date = by_eps.get(round(eps_act, 2))
        if real_date is not None:
            matched[period_end.isoformat()] = real_date
    return matched


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
                # AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: fetch earnings_dates
                # too so real announcement dates (see _match_report_dates_to_history's own
                # docstring) can be joined in — best-effort, a failure here just means every
                # row falls back to the period-end date (the pre-fix behavior), not a hard stop.
                real_dates_by_period_end: dict[str, date] = {}
                try:
                    ed = ticker.earnings_dates
                    if ed is not None and not ed.empty:
                        hist_rows_for_match = []
                        for idx, row in hist.iterrows():
                            pe = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                            ea = row.get("epsActual")
                            hist_rows_for_match.append({
                                "period_end": pe,
                                "eps_actual": float(ea) if pd.notna(ea) else None,
                            })
                        announce_rows = []
                        for idx, row in ed.iterrows():
                            ad = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                            ea = row.get("Reported EPS")
                            announce_rows.append({
                                "announce_date": ad,
                                "eps_actual": float(ea) if pd.notna(ea) else None,
                            })
                        real_dates_by_period_end = _match_report_dates_to_history(hist_rows_for_match, announce_rows)
                except Exception as exc:
                    log.debug("earnings.earnings_dates_join_skip", symbol=symbol, error=str(exc))

                # AUD-EARNINGS-REVENUEACTUAL: revenue_actual/revenue_surprise_pct are real
                # columns on EarningsEvent, read by generate_earnings_impact()'s LLM prompt and
                # returned to the frontend via _row_to_dict()'s actual_revenue field — but until
                # this fix, NOTHING in this file ever wrote either one. ticker.earnings_history
                # (the loop below) only carries EPS fields; ticker.calendar's "Revenue Estimate"
                # (written elsewhere in this function) is a forward-looking, pre-report figure
                # that can never carry an actual by construction. Real historical revenue lives
                # in ticker.quarterly_financials's own "Total Revenue" row, indexed by the SAME
                # period-end dates ticker.earnings_history uses (confirmed directly against a
                # real yfinance response before writing this) — one extra fetch, best-effort,
                # same fail-open convention as the earnings_dates join right above.
                revenue_actual_by_period_end: dict[str, float] = {}
                try:
                    qf = ticker.quarterly_financials
                    if qf is not None and not qf.empty and "Total Revenue" in qf.index:
                        for col, val in qf.loc["Total Revenue"].items():
                            if pd.notna(val):
                                pe = col.date() if hasattr(col, "date") else date.fromisoformat(str(col)[:10])
                                revenue_actual_by_period_end[pe.isoformat()] = float(val)
                except Exception as exc:
                    log.debug("earnings.revenue_actual_join_skip", symbol=symbol, error=str(exc))

                with SessionLocal() as s:
                    for idx, row in hist.iterrows():
                        try:
                            period_end = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                            eps_est = row.get("epsEstimate") if pd.notna(row.get("epsEstimate")) else None
                            eps_act = row.get("epsActual") if pd.notna(row.get("epsActual")) else None
                            surprise = _compute_surprise_pct(eps_est, eps_act)
                            rev_act = revenue_actual_by_period_end.get(period_end.isoformat())
                            # AUD264: prefer the real, joined announcement date; fall back to the
                            # period-end date (the pre-fix value) only when no match was found —
                            # still better than nothing, and matches this function's own existing
                            # fail-open convention throughout.
                            report_date = real_dates_by_period_end.get(period_end.isoformat(), period_end)
                            # Fiscal quarter/year are still a best-effort calendar-month label
                            # (non-calendar-fiscal-year companies, e.g. AAPL's Sep year-end,
                            # aren't exactly representable this way) — but they're read-only
                            # display fields with zero downstream logic depending on their exact
                            # value (see the AUD264 fix note on the uniqueness constraint below).
                            fq = (period_end.month - 1) // 3 + 1
                            fy = period_end.year
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
                            # AUD264: the calendar path (below) may have already written a
                            # PENDING row (eps_actual IS NULL) for this exact event under its
                            # own earlier, projected report_date — which can genuinely differ
                            # from the real, joined announcement date resolved above. A bare
                            # ON CONFLICT (stock_id, report_date) would miss that row entirely
                            # (different dates) and insert a second, duplicate row for the same
                            # event. Find and update any such pending row in place first, same
                            # reasoning as the calendar path's own existing_pending handling.
                            existing_pending = s.execute(
                                select(EarningsEvent).where(
                                    EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.eps_actual.is_(None),
                                )
                            ).scalars().first()
                            if existing_pending is not None:
                                # revenue_estimate was already set by the earlier calendar-path
                                # write that created this pending row (see the calendar block
                                # below) — reuse it here to compute the surprise, matching the
                                # EPS surprise computation above exactly. A row that was never
                                # seen by the calendar path first (rare — a report syncing before
                                # its own pending calendar entry ever ran) has revenue_estimate
                                # still None, so rev_surprise correctly stays None too, same as
                                # the pre-existing eps `surprise is None` fallback above.
                                rev_surprise = _compute_surprise_pct(existing_pending.revenue_estimate, rev_act)
                                existing_pending.report_date = report_date
                                existing_pending.period = f"Q{fq} {fy}"
                                existing_pending.fiscal_year = fy
                                existing_pending.fiscal_quarter = fq
                                existing_pending.eps_estimate = eps_est
                                existing_pending.eps_actual = eps_act
                                existing_pending.surprise_pct = surprise
                                existing_pending.revenue_actual = rev_act
                                existing_pending.revenue_surprise_pct = rev_surprise
                                existing_pending.earnings_strength_score = strength
                                existing_pending.fetched_at = _now
                                s.flush()
                                upserted += 1
                            else:
                                # No prior pending row (and therefore no revenue_estimate ever
                                # captured for this event) — revenue_surprise_pct correctly
                                # stays unset, matching eps `surprise`'s own identical
                                # both-sides-required convention immediately above.
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
                                        revenue_actual=rev_act,
                                        earnings_strength_score=strength,
                                        fetched_at=_now,
                                    )
                                    .on_conflict_do_update(
                                        constraint="uq_earnings_stock_report_date",
                                        set_=dict(
                                            eps_estimate=eps_est,
                                            eps_actual=eps_act,
                                            surprise_pct=surprise,
                                            revenue_actual=rev_act,
                                            earnings_strength_score=strength,
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
                    # AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: fiscal_year/
                    # fiscal_quarter are still only a best-effort calendar-month label (see the
                    # historical-path comment above for why) — the real fix here is switching
                    # the uniqueness key from (fiscal_year, fiscal_quarter) to (stock_id,
                    # report_date), which is what actually stops two genuine reports in the same
                    # calendar quarter from silently overwriting each other.
                    fq = (upcoming.month - 1) // 3 + 1
                    fy = upcoming.year
                    with SessionLocal() as s:
                        # AUD264: report_date is now the uniqueness key (not fiscal_year/
                        # fiscal_quarter), but yfinance's own PROJECTED earnings date for an
                        # unreported quarter routinely shifts by a few days as the real date is
                        # confirmed closer to the event — daily re-syncs (sync_earnings runs
                        # every day at 06:30 UTC) would otherwise insert a brand-new row each
                        # time the estimate moves instead of updating the one real pending
                        # event. There can only ever be one legitimate "next unreported" row per
                        # stock (eps_actual IS NULL, the same predicate already used elsewhere
                        # in this codebase to mean "not yet reported") — find and update it in
                        # place if it exists under a DIFFERENT date than today's estimate,
                        # rather than letting the insert path create a duplicate.
                        existing_pending = s.execute(
                            select(EarningsEvent).where(
                                EarningsEvent.stock_id == stock_id,
                                EarningsEvent.eps_actual.is_(None),
                            )
                        ).scalars().first()
                        # See DQ-EARNINGS-FETCHED-AT-FROZEN comment above — same reasoning applies
                        # to the upcoming-earnings-calendar upsert path.
                        _now = datetime.now(timezone.utc)
                        _est = float(eps_est) if eps_est and pd.notna(eps_est) else None
                        _rev_est = float(rev_est) if rev_est and pd.notna(rev_est) else None
                        if existing_pending is not None:
                            existing_pending.report_date = upcoming
                            existing_pending.period = f"Q{fq} {fy}"
                            existing_pending.fiscal_year = fy
                            existing_pending.fiscal_quarter = fq
                            existing_pending.eps_estimate = _est
                            existing_pending.revenue_estimate = _rev_est
                            existing_pending.fetched_at = _now
                        else:
                            stmt = (
                                pg_insert(EarningsEvent)
                                .values(
                                    stock_id=stock_id,
                                    report_date=upcoming,
                                    period=f"Q{fq} {fy}",
                                    fiscal_year=fy,
                                    fiscal_quarter=fq,
                                    eps_estimate=_est,
                                    revenue_estimate=_rev_est,
                                    fetched_at=_now,
                                )
                                .on_conflict_do_update(
                                    constraint="uq_earnings_stock_report_date",
                                    set_=dict(
                                        eps_estimate=_est,
                                        revenue_estimate=_rev_est,
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


def _backfill_report_dates_for_symbol(symbol: str, stock_id: int) -> int:
    """AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH one-time backfill: rows already
    persisted BEFORE this fix shipped have report_date set to the fiscal PERIOD-END date, not
    the real announcement date (see _match_report_dates_to_history()'s own docstring). The
    normal sync path (_fetch_earnings_for_symbol) does NOT self-heal these — its
    existing_pending lookup only matches eps_actual IS NULL rows, so re-running the ordinary
    daily sync against an already-reported stale row would INSERT A DUPLICATE under the
    correct date rather than fix the original. This function instead finds each already-
    reported row (eps_actual IS NOT NULL) for the symbol and updates report_date/period/
    fiscal_year/fiscal_quarter IN PLACE, matched by eps_actual — the exact same reliable join
    key _match_report_dates_to_history() already uses. Returns the count of rows corrected.

    Safe to run repeatedly: a row whose report_date already matches the real announcement
    date needs no update (skipped), and a row with no matching announce-side data is left
    untouched (matching _fetch_earnings_for_symbol's own fail-open fallback).
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.earnings_history
        if hist is None or hist.empty:
            return 0
        ed = ticker.earnings_dates
        if ed is None or ed.empty:
            return 0

        hist_rows = []
        for idx, row in hist.iterrows():
            pe = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            ea = row.get("epsActual")
            hist_rows.append({"period_end": pe, "eps_actual": float(ea) if pd.notna(ea) else None})

        announce_rows = []
        for idx, row in ed.iterrows():
            ad = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            ea = row.get("Reported EPS")
            announce_rows.append({"announce_date": ad, "eps_actual": float(ea) if pd.notna(ea) else None})

        real_dates_by_period_end = _match_report_dates_to_history(hist_rows, announce_rows)
        # Also index the real dates by eps_actual directly, so a stored row can be matched
        # even if its OWN report_date no longer matches any period_end in the current
        # earnings_history window (yfinance's history window rolls forward over time).
        real_date_by_eps: dict[float, date] = {}
        for row in hist_rows:
            ea = row.get("eps_actual")
            pe = row.get("period_end")
            if ea is None or pe is None:
                continue
            matched = real_dates_by_period_end.get(pe.isoformat())
            if matched is not None:
                real_date_by_eps.setdefault(round(ea, 2), matched)

        corrected = 0
        with SessionLocal() as s:
            rows = s.execute(
                select(EarningsEvent).where(
                    EarningsEvent.stock_id == stock_id,
                    EarningsEvent.eps_actual.is_not(None),
                )
            ).scalars().all()
            for ev in rows:
                real_date = real_date_by_eps.get(round(ev.eps_actual, 2))
                if real_date is None or real_date == ev.report_date:
                    continue
                # AUD264-BACKFILL-PENDING-ROW-COLLISION: if the normal daily sync has already
                # run since this fix shipped, it may have ALREADY inserted (or updated a
                # pending row to) the correct real_date for this exact event via its own
                # existing_pending logic — the (stock_id, report_date) uniqueness constraint
                # would otherwise reject this UPDATE outright. Confirmed live: AAPL's calendar
                # path had already written a pending row (eps_actual NULL) at the real
                # announcement date before this backfill ran. Since the ALREADY-REPORTED row
                # (ev, with real eps_actual/surprise_pct/strength) carries the data worth
                # keeping, delete the redundant pending duplicate and move ev onto its date.
                conflicting = s.execute(
                    select(EarningsEvent).where(
                        EarningsEvent.stock_id == stock_id,
                        EarningsEvent.report_date == real_date,
                        EarningsEvent.id != ev.id,
                    )
                ).scalars().first()
                if conflicting is not None:
                    if conflicting.eps_actual is not None:
                        # A genuinely different, already-reported row already sits at this
                        # date — do not silently clobber real, independent data; skip this
                        # one for manual review rather than guessing which is correct.
                        continue
                    s.delete(conflicting)
                    s.flush()
                ev.report_date = real_date
                ev.fiscal_year = real_date.year
                ev.fiscal_quarter = (real_date.month - 1) // 3 + 1
                ev.period = f"Q{ev.fiscal_quarter} {ev.fiscal_year}"
                corrected += 1
            s.commit()
        return corrected
    except Exception as exc:
        log.warning("earnings.backfill_report_dates_failed", symbol=symbol, error=str(exc))
        return 0


async def backfill_report_dates() -> dict:
    """One-time, safe-to-re-run backfill correcting every already-stored, already-reported
    earnings_events row's report_date from the pre-fix period-end date to the real
    announcement date. See _backfill_report_dates_for_symbol()'s own docstring for why this
    is a separate function from the normal daily sync, not something that self-heals."""
    with SessionLocal() as s:
        stocks = s.execute(select(Stock.id, Stock.symbol)).all()

    loop = asyncio.get_running_loop()
    total = 0
    for stock_id, symbol in stocks:
        n = await loop.run_in_executor(_executor, _backfill_report_dates_for_symbol, symbol, stock_id)
        total += n
        await asyncio.sleep(0.2)  # gentle rate limiting, matching sync_all_earnings()

    return {"symbols_processed": len(stocks), "rows_corrected": total}


def _compute_surprise_pct(estimate: float | None, actual: float | None) -> float | None:
    """% beat/miss of `actual` over `estimate`. Shared by both eps_surprise (unchanged formula,
    extracted here under AUD-EARNINGS-REVENUEACTUAL) and revenue_surprise_pct (new) — same
    both-sides-required, divide-by-abs(estimate) convention either way."""
    if estimate is None or actual is None or estimate == 0:
        return None
    return round((actual - estimate) / abs(estimate) * 100, 2)


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


async def sync_todays_earnings() -> dict:
    """AUD-EARNINGS-INTRADAY-SYNC-GAP: sync_all_earnings() only runs once/day at 06:30 UTC —
    before the US market session — so a company reporting during market hours or after the
    close (the overwhelming majority of real earnings releases) never gets its eps_actual/
    surprise_pct picked up until THE NEXT MORNING's 06:30 sync. check_earnings_reactions()
    (market-data, runs every minute) and check_earnings_impact_poll() (this service, every
    5 min) both correctly fire the instant eps_actual lands — the gap was purely upstream,
    that nothing ever re-synced a stock's row same-day once it actually reported. Confirmed
    live 2026-08-03: PLTR posted real, materially strong Q2 results (EPS $0.41 beat $0.35,
    guidance raised) at ~20:05 UTC, but its earnings_events row still showed eps_actual=NULL
    hours later because nothing had re-checked it since that morning's sync.

    Deliberately NOT a second full-universe rescan (that's what the 06:30 UTC job is for,
    and running it again intraday would repeat the same ~178-symbol yfinance sweep every
    cycle for no reason) — scoped to just the stocks that ACTUALLY need a fresh check:
    report_date in {yesterday, today} (yesterday covers an after-market print Yahoo still
    files against the prior calendar day) with eps_actual still NULL. On a quiet day with
    nobody reporting, or once all of today's reporters have already resolved, this is a
    single cheap indexed query with zero yfinance calls at all.
    """
    cutoff_start = date.today() - timedelta(days=1)
    cutoff_end = date.today()
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent.stock_id, Stock.symbol)
            .join(Stock, EarningsEvent.stock_id == Stock.id)
            .where(
                EarningsEvent.report_date >= cutoff_start,
                EarningsEvent.report_date <= cutoff_end,
                EarningsEvent.eps_actual.is_(None),
            )
        ).all()

    if not rows:
        return {"symbols_checked": 0, "rows_upserted": 0}

    loop = asyncio.get_running_loop()
    total = 0
    for stock_id, symbol in rows:
        n = await loop.run_in_executor(_executor, _fetch_earnings_for_symbol, symbol, stock_id)
        total += n
        await asyncio.sleep(0.2)  # gentle rate limiting, matching sync_all_earnings()

    return {"symbols_checked": len(rows), "rows_upserted": total}


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

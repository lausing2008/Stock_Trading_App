"""T257-OVERNIGHT-FLOW-BRIEF Phase 2: end-of-day options-flow snapshot persistence.

GET /{symbol}/options-flow (services/market-data/src/api/routes.py's get_options_flow()) is
live-only with a 15-minute Redis cache — nothing persists it, so the pre-market brief's own
design ("yesterday's late-day flow was call-heavy on X/Y/Z") had no data to report from. This
module computes the SAME aggregate the live endpoint does (cp_ratio, call/put volume, sentiment,
whale detection) directly from a fresh yfinance option-chain fetch, plus two fields the live
endpoint does NOT already aggregate (call_premium/put_premium — the live endpoint only tracks
per-contract premium inside its own top-10 "unusual activity" list, never a running total across
the whole chain), and persists one row per stock per day.

Deliberately does NOT reuse get_options_flow()'s own code directly (that function lives in
routes.py, is FastAPI-route-shaped, and returns its own Redis-cached dict rather than exposing a
reusable "give me the raw aggregate" function) — this module independently re-derives the same
cp_ratio/sentiment math from the same yfinance data, matching that function's exact thresholds
(cp_ratio capped at 10.0, sufficient_put_vol >= 100, the same 5-tier sentiment ladder) so the two
stay in agreement. If either's math changes, check whether the other needs the same change too —
same "two independent ports, not one shared implementation" caveat volume_area.py's own docstring
already documents for its own TS/Python pairing.

Deliberately scoped to a BOUNDED symbol set (see scheduler.py's _bounded_options_flow_symbols())
rather than the whole universe — yfinance's options-chain endpoint is the most rate-limit-
fragile call this app makes.
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
    call_premium: float
    put_premium: float
    whale_count: int
    top_whale_premium: float
    sentiment: str


def compute_options_flow(symbol: str) -> OptionsFlowResult | None:
    """Fetch the nearest 4 option-chain expiries for `symbol` and aggregate call/put volume,
    premium, and whale activity. Returns None if the symbol has no listed options, no volume,
    or the fetch fails for any reason (fail-open — a single symbol's failure must never abort
    the whole EOD batch).

    Mirrors get_options_flow()'s exact math (routes.py) so the two never silently disagree:
    cp_ratio capped at 10.0, sentiment requires >=100 put contracts before declaring bullish/
    bearish (a near-zero put volume usually means illiquid options, not extreme sentiment), and
    the same 5-tier sentiment ladder (strongly_bullish/bullish/neutral/slightly_bearish/bearish).
    """
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        # AUD265-GAMMA-ASSUMES-SORTED-EXPIRIES: sorted() makes "nearest 4 expiries" structural
        # rather than dependent on yfinance's own (undocumented) ordering of t.options.
        expiries = sorted(t.options)
        if not expiries:
            return None

        total_call_vol = 0
        total_put_vol = 0
        total_call_premium = 0.0
        total_put_premium = 0.0
        whale_count = 0
        top_whale_premium = 0.0

        for exp in expiries[:4]:
            try:
                chain = t.option_chain(exp)
            except Exception:
                continue

            calls = chain.calls.fillna(0)
            puts = chain.puts.fillna(0)

            total_call_vol += int(calls["volume"].sum())
            total_put_vol += int(puts["volume"].sum())

            for df, is_call in [(calls, True), (puts, False)]:
                for _, row in df.iterrows():
                    vol = float(row["volume"])
                    if vol <= 0:
                        continue
                    premium = vol * float(row.get("lastPrice", 0)) * 100
                    if is_call:
                        total_call_premium += premium
                    else:
                        total_put_premium += premium
                    if premium > 500_000:
                        whale_count += 1
                        top_whale_premium = max(top_whale_premium, premium)

        if total_call_vol == 0 and total_put_vol == 0:
            return None

        # AUD265-CPRATIO-CENSORED-BREAKS-RANKING: cp_ratio is capped at 10.0 for sentiment
        # classification (the ladder's own tier boundaries were chosen against this capped
        # scale) — but the real, uncapped ratio is preserved separately so ranking/display can
        # still distinguish a 10x-lopsided flow from a 500x one; both would otherwise collapse
        # to the identical stored value.
        cp_ratio_uncapped = round(total_call_vol / max(total_put_vol, 1), 2)
        cp_ratio = min(cp_ratio_uncapped, 10.0)
        sufficient_put_vol = total_put_vol >= 100

        if cp_ratio >= 2.0 and sufficient_put_vol:
            sentiment = "strongly_bullish"
        elif cp_ratio >= 1.3 and sufficient_put_vol:
            sentiment = "bullish"
        elif cp_ratio <= 0.5 and sufficient_put_vol:
            sentiment = "bearish"
        elif cp_ratio <= 0.8 and sufficient_put_vol:
            sentiment = "slightly_bearish"
        else:
            sentiment = "neutral"

        return OptionsFlowResult(
            cp_ratio=cp_ratio,
            cp_ratio_uncapped=cp_ratio_uncapped,
            call_volume=total_call_vol,
            put_volume=total_put_vol,
            call_premium=round(total_call_premium, 2),
            put_premium=round(total_put_premium, 2),
            whale_count=whale_count,
            top_whale_premium=round(top_whale_premium, 2),
            sentiment=sentiment,
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
    as_of = as_of or datetime.now(timezone.utc).date()
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

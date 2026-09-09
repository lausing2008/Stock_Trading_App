"""Ingestion service — incremental loads, validation, Parquet + Postgres sinks."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.config import get_settings
from common.logging import get_logger
from common.redis_client import get_redis

from db import Price, SessionLocal, Stock, TimeFrame

from ..adapters import get_adapter, get_adapters

log = get_logger("ingestion")

_settings = get_settings()

# BUG-POLYGONBUDGET (2026-08-07): Polygon's own free-tier plan caps at 5 requests/minute
# (services/market-data/src/adapters/polygon_adapter.py's own docstring: "Polygon.io
# free-tier adapter"). With ~121 active US symbols eligible for the Polygon-first
# incremental path (see the adapter-selection comment below), every ingest cycle sent the
# large majority of them into a Polygon call that was mathematically certain to 429 before
# ever reaching yfinance — confirmed live: 24,949 of 25,825 Polygon calls (97%) rate-limited
# in a single 24h window, each one adding a wasted round-trip ahead of the yfinance fallback
# that was going to be needed anyway. RateLimitError is already excluded from Polygon's own
# retry policy (fails fast on the first 429, no wasted backoff) — this doesn't change that;
# it stops SENDING the doomed request in the first place once the real per-minute budget for
# THIS cycle is already spent.
#
# NOTE (AUD-ING-POLYGONBUDGET-SKIPSPRIMARY, 2026-09-09): the original wording of this comment
# ended "...going straight to yfinance instead", and that is no longer what happens — nor what
# should. Polygon is now LAST in _PRIORITY (see registry.py), so exhausting this budget must
# DROP POLYGON from the candidate list, not pick a specific replacement. Naming yfinance here
# is what let this guard silently skip Unusual Whales, the new primary, for ~126 of 131 US
# symbols every cycle. The counter increments on every US incremental ingest regardless of
# whether Polygon is ever reached, which is why the blast radius was near-total rather than
# limited to Polygon calls.
_POLYGON_BUDGET_PER_MINUTE = 5
_POLYGON_BUDGET_KEY_PREFIX = "stockai:polygon_budget:"


def _polygon_budget_available() -> bool:
    """True if we're still under Polygon's free-tier ~5-req/min budget for the current
    minute. Fails OPEN (returns True) on any Redis error — worst case we send one wasted
    Polygon call, same as before this fix; we never want a Redis hiccup to silently disable
    Polygon entirely."""
    try:
        redis_client = get_redis()
        minute_key = _POLYGON_BUDGET_KEY_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        count = redis_client.incr(minute_key)
        if count == 1:
            redis_client.expire(minute_key, 90)  # a little past 60s so a slow cycle can't undercount
        return count <= _POLYGON_BUDGET_PER_MINUTE
    except Exception:
        return True


class IngestionError(Exception):
    pass


# AUD-SURVIVORSHIP-DELISTDETECT: closes the gap where ml-prediction's training-universe
# query already does `WHERE active OR delisted` (see services/ml-prediction/src/api/routes.py)
# but nothing anywhere ever sets delisted=True — confirmed a dead, always-False column in
# production. YFTickerMissingError (raised by yfinance_adapter.py when Yahoo's own API reports
# "no data found, symbol may be delisted") is a real, distinct signal — but a SINGLE
# occurrence isn't trusted alone, since this app's daily ingestion cycle could in principle hit
# a genuine one-off data-provider glitch even though YFTickerMissingError itself is excluded
# from yfinance_adapter.py's own retry policy. Requiring 2 occurrences on separate ingestion
# RUNS (not 2 retries within one call — those are already collapsed into a single raise by the
# retry-exclusion above) before acting is a cheap, conservative confirmation margin.
_DELISTING_CONFIRM_THRESHOLD = 2

# AUD-ING7-DELISTNEVERFIRES: how stale the NEWEST bar a fetch returns may be before that fetch
# counts as evidence of delisting rather than evidence of life. See the call site for why a
# successful fetch alone is not proof a symbol is alive.
#
# 7 days deliberately, matching the stale_symbols_d1 DQ gauge (AUD-DQ2-PERSYMBOLSTALENESS) so
# the two mechanisms cannot disagree about what "stale" means. Comfortably wider than any
# holiday weekend (max ~4 calendar days closed), and combined with _DELISTING_CONFIRM_THRESHOLD
# = 2 a symbol must look dead across two separate ingest cycles before it is flagged — so a
# single transient bad fetch cannot delist a live stock.
_DELISTING_STALE_BAR_DAYS = 7
_DELISTING_REDIS_KEY = "stockai:delisting_signal:{symbol}"
_DELISTING_REDIS_TTL = 30 * 86400  # 30 days — a stale single occurrence should eventually decay


def _record_delisting_signal(symbol: str) -> None:
    """Increment this symbol's consecutive-delisting-signal counter; on reaching the
    confirmation threshold, set Stock.delisted=True. Fails open (a Redis hiccup must never
    block ingestion) — logs a warning and returns without raising.
    """
    try:
        r = get_redis()
        key = _DELISTING_REDIS_KEY.format(symbol=symbol)
        count = r.incr(key)
        r.expire(key, _DELISTING_REDIS_TTL)
        log.warning("ingest.delisting_signal", symbol=symbol, count=count)
        if count >= _DELISTING_CONFIRM_THRESHOLD:
            with SessionLocal() as session:
                stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
                if stock and not stock.delisted:
                    stock.delisted = True
                    session.commit()
                    log.warning("ingest.delisted_confirmed", symbol=symbol, count=count)
            r.delete(key)
    except Exception as exc:
        log.warning("ingest.delisting_signal_failed", symbol=symbol, error=str(exc))


def _clear_delisting_signal(symbol: str) -> None:
    """A successful fetch means this symbol is NOT delisted (a real data source just returned
    real data for it) — clear any accumulated signal so a past transient glitch doesn't
    linger toward the threshold indefinitely. Fails open, matching _record_delisting_signal().
    """
    try:
        get_redis().delete(_DELISTING_REDIS_KEY.format(symbol=symbol))
    except Exception as exc:
        log.warning("ingest.delisting_signal_clear_failed", symbol=symbol, error=str(exc))


def validate_ohlcv(df: pd.DataFrame, symbol: str, allow_zero_volume: bool = False) -> pd.DataFrame:
    """Reject bars with bad invariants (low>high, negative prices, etc).

    allow_zero_volume: yfinance's prepost=True intraday bars (T230-CHARTING-PREMARKET) commonly
    report volume=0 for real pre/post-market trades — a known yfinance quirk, not a sign of an
    invalid bar the way volume=0 would be on a regular-session bar. Without this, every single
    extended-hours bar was silently dropped, defeating the feature entirely (discovered via a
    real ingest showing 342/576 fetched bars dropped, all zero-volume, all outside 9:30-16:00 ET).
    AUD-ING6-HKZEROVOLUME: the original wording here said "Regular-session and daily bars keep
    the strict volume>0 check — real trading always has nonzero volume there." That is a
    US-LIQUID-EQUITY assumption and it is false for thinly-traded HK small caps, where a
    zero-volume day is a legitimate no-trade session. It silently deleted 62% of 1671.HK's
    history and 56% of 0117.HK's. US daily bars still keep the strict check (a zero-volume
    regular-session bar on a liquid US listing really is a bad bar); HK daily/weekly bars now
    allow zero volume. See the caller's own comment for the measurements.

    Note this ONLY relaxes the volume gate — the OHLC-ordering and positivity invariants above
    apply to every market and timeframe regardless.
    """
    if df.empty or not {"high", "low", "open", "close", "volume"}.issubset(df.columns):
        return df
    before = len(df)
    df = df.copy()
    df = df[(df["high"] >= df["low"]) & (df["high"] >= df["open"]) & (df["high"] >= df["close"])]
    df = df[(df["low"] <= df["open"]) & (df["low"] <= df["close"])]
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    # AUD-ING6-HKZEROVOLUME: `allow_zero_volume` relaxes the floor from >0 to >=0 — it does NOT
    # skip the volume check entirely. Skipping it would let a NEGATIVE volume through, which is
    # corrupt data in any market and was never the thing being permitted. (Caught by a test
    # while making this change: the naive `if not allow_zero_volume` form kept a volume of -5.)
    df = df[df["volume"] >= 0] if allow_zero_volume else df[df["volume"] > 0]
    dropped = before - len(df)
    if dropped:
        log.warning("ohlcv.drop_invalid", symbol=symbol, dropped=dropped)
    return df


_PREMARKET_OPEN_ET = dtime(4, 0)
_MARKET_OPEN_ET = dtime(9, 30)
_MARKET_CLOSE_ET = dtime(16, 0)
_POSTMARKET_CLOSE_ET = dtime(20, 0)


# T230-CHARTING-PREMARKET: classify each intraday bar's timestamp as PRE/REGULAR/POST.
# US only — HK has no pre/post-market session concept (a pure cash-open market), and
# ingest_symbol always routes HK through yfinance with prepost=True harmlessly returning
# only regular-session bars for HK tickers (yfinance simply has no extended-hours data to add).
# ts here is UTC-naive (see adapters/base.py's _to_canonical intraday branch), so it's
# converted to US Eastern before comparing against the regular-session clock boundaries.
def _classify_session(ts: datetime, market: str) -> str:
    if market != "US":
        return "REGULAR"
    et = ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York"))
    t = et.time()
    if t < _PREMARKET_OPEN_ET or t >= _POSTMARKET_CLOSE_ET:
        return "REGULAR"  # outside yfinance's extended-hours window entirely — treat as regular
    if _MARKET_OPEN_ET <= t < _MARKET_CLOSE_ET:
        return "REGULAR"
    return "PRE" if t < _MARKET_OPEN_ET else "POST"


def _last_bar_ts(session, stock_id: int, timeframe: TimeFrame) -> datetime | None:
    stmt = (
        select(Price.ts)
        .where(Price.stock_id == stock_id, Price.timeframe == timeframe)
        .order_by(Price.ts.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def ingest_symbol(
    symbol: str,
    market: str = "US",
    timeframe: str = "1d",
    lookback_days: int = 365 * 3,
    provider: str | None = None,
    force: bool = False,
) -> dict:
    """Idempotent incremental ingest — loads only bars newer than DB head.

    If force=True, deletes all existing price rows for the symbol+timeframe first,
    then re-fetches the full lookback_days window from scratch.
    """
    tf = TimeFrame(timeframe)

    with SessionLocal() as session:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol)
        ).scalar_one_or_none()
        if stock is None:
            raise IngestionError(f"Unknown symbol: {symbol} (seed universe first)")

        if force:
            session.execute(
                delete(Price).where(Price.stock_id == stock.id, Price.timeframe == tf)
            )
            # F-2: do NOT commit here — keep DELETE and INSERT in one transaction so no
            # reader sees a gap window between the two operations.
            log.info("ingest.force_delete", symbol=symbol, tf=timeframe)

        head = None if force else _last_bar_ts(session, stock.id, tf)
        if head:
            if timeframe == "1d":
                # Look back 7 extra days so split-adjusted prices overwrite stale bars.
                start = head.date() - timedelta(days=7) + timedelta(days=1)
            else:
                # For intraday, re-fetch from the same calendar date as the last bar
                # so we pick up new bars that arrived after the last stored bar.
                # on_conflict_do_update handles duplicates safely.
                start = head.date()
        else:
            start = date.today() - timedelta(days=lookback_days)
        # yfinance only serves intraday bars within the last 60 days
        if timeframe in ("1m", "5m", "15m", "1h"):
            start = max(start, date.today() - timedelta(days=59))
        end = date.today() + timedelta(days=1)

        if start >= end:
            return {"symbol": symbol, "inserted": 0, "skipped": "up_to_date"}

        # Adapter selection strategy:
        #   - Explicit provider requested → use that provider
        #   - HK stocks → always yfinance (Polygon doesn't support HK, and UW has no HK coverage)
        #   - Batch context (force or no existing bars) → yfinance (preserve paid quota for incremental)
        #   - US incremental → the full _PRIORITY list, with Polygon DROPPED when its free-tier
        #     per-minute budget for this cycle is already spent (BUG-POLYGONBUDGET).
        #
        # AUD-ING-POLYGONBUDGET-SKIPSPRIMARY (2026-09-09): this last branch used to read
        # `elif not _polygon_budget_available(): adapters = [get_adapter("yfinance")]`, which was
        # correct ONLY while Polygon was FIRST in _PRIORITY — "don't send a request we know will
        # 429, go straight to the fallback". After AUD-ING-POLYGONDELAYED reordered _PRIORITY to
        # put unusual_whales first and Polygon LAST, that same line became actively harmful: it
        # hardcoded yfinance and therefore SKIPPED THE NEW PRIMARY ENTIRELY.
        #
        # The blast radius was near-total, because the budget counter increments on EVERY US
        # incremental ingest whether or not Polygon is ever reached. With _POLYGON_BUDGET_PER_MINUTE
        # = 5 and ~131 active US symbols, only the FIRST 5 SYMBOLS PER MINUTE saw Unusual Whales;
        # the other ~126 were forced onto yfinance-only — the unauthenticated, rate-limiting source
        # the reorder existed to stop depending on. Verified live: 9 consecutive calls in one
        # minute returned [True]*5 + [False]*4.
        #
        # THE FIX IS TO MAKE THE GATE DO WHAT ITS NAME SAYS: exclude POLYGON, not select yfinance.
        # Everything else in _PRIORITY keeps its normal order, so exhausting Polygon's tiny free
        # budget can never again decide which OTHER provider answers.
        #
        # GENERALISABLE: a guard written as "fall back to X" silently encodes the priority order
        # that was current when it was written. When that order changes, the guard does not — it
        # keeps naming a provider that is no longer the right answer. Prefer "exclude Y" over
        # "use X" so the guard stays correct under reordering.
        if provider:
            adapters = [get_adapter(provider, market)]
        elif symbol.endswith(".HK") or market == "HK":
            adapters = [get_adapter("yfinance")]
        elif force or head is None:
            adapters = [get_adapter("yfinance")]
        else:
            adapters = get_adapters(market, timeframe)
            if not _polygon_budget_available():
                _without_polygon = [a for a in adapters if a.name != "polygon"]
                # Never return an EMPTY list: if Polygon were somehow the only candidate, dropping
                # it would raise IngestionError with no adapter tried at all. Keep it in that case
                # — one doomed request beats no request.
                if _without_polygon:
                    adapters = _without_polygon

        # T230-CHARTING-PREMARKET: only the US-intraday prepost=True path can legitimately
        # produce real zero-volume bars (yfinance's extended-hours quirk) — daily/weekly bars
        # and HK (no extended-hours session) keep the strict volume>0 invariant check.
        #
        # AUD-ING6-HKZEROVOLUME: ...EXCEPT that assumption ("real trading always has nonzero
        # volume") is a US-liquid-equity assumption, and it is FALSE for thinly-traded HK small
        # caps, where a zero-volume day is a legitimate no-trade session carrying a real price —
        # not an invalid bar.
        #
        # Measured against the real 3-year fetch through the real validator:
        #     1671.HK  fetched 735  kept 277  DROPPED 458  (448 of them zero-volume)
        #     0117.HK  fetched 735  kept 320  DROPPED 415  (398 of them zero-volume)
        # Zero-volume share by liquidity: 0700.HK/0005.HK/9988.HK 0.6-1.2%, 0117.HK 26.9%,
        # 1671.HK 48.6%. The rule is correct for liquid names and badly wrong for illiquid ones.
        #
        # Consequences of dropping them: every rolling feature for these symbols is computed
        # across a series missing half its bars, so a "200-day SMA" actually spans ~400 calendar
        # days — and both symbols are in the ML training universe and emit live signals. It was
        # also SELF-CONCEALING: the weekly force=True refresh re-fetches all 735 bars and
        # re-drops the same 458 every week, so the gap could never heal and the only symptom was
        # an ohlcv.drop_invalid log line.
        #
        # HK daily/weekly bars now allow zero volume. US daily stays strict: a zero-volume
        # regular-session bar on a US listing really is a bad bar, and loosening it there would
        # discard a genuine signal (measured: liquid US names lose ~1% either way, so there is
        # nothing to gain and a real invariant to lose). The OHLC-ordering and positivity checks
        # are unaffected in both markets — this only relaxes the volume gate.
        # AUD-ING6-MARKETINFER: derive the effective market the SAME way adapter selection does
        # (`symbol.endswith(".HK") or market == "HK"`, a few lines above) rather than trusting
        # the `market` parameter alone. They disagreed: ingest_universe() calls ingest_symbol()
        # WITHOUT a market, so it always defaulted to "US" — an HK symbol routed to the correct
        # HK adapter by suffix while simultaneously being held to the strict US `volume > 0`
        # rule, silently dropping every zero-volume bar of an illiquid HK name. That is the
        # exact defect AUD-ING6-HKZEROVOLUME fixed for the explicit-market path, reachable again
        # through any caller that omits the argument.
        _effective_market = "HK" if (symbol.endswith(".HK") or market == "HK") else market
        allow_zero_volume = (
            (_effective_market == "US" and timeframe not in ("1d", "1w"))
            or _effective_market == "HK"
        )

        last_err: Exception | None = None
        df: pd.DataFrame | None = None
        # AUD-SURVIVORSHIP-DELISTDETECT: only the DAILY-bar ingestion cycle drives the
        # delisting signal — intraday ingestion runs far more often (would reach the
        # confirmation threshold in hours, not days, on a stock that's merely rate-limited)
        # and HK/US-daily is the cadence this app's own scheduler already treats as the
        # canonical "is this stock still alive" check (see _snapshot_fundamentals()'s own
        # `WHERE delisted = false` filter, which assumes a daily-granularity signal).
        _track_delisting = (timeframe == "1d")
        for adapter in adapters:
            try:
                ohlcv = adapter.fetch_ohlcv(symbol, start, end, timeframe)
                candidate = validate_ohlcv(ohlcv.df, symbol, allow_zero_volume=allow_zero_volume)
                if not candidate.empty:
                    df = candidate
                    if _track_delisting and adapter.name == "yfinance":
                        # AUD-ING7-DELISTNEVERFIRES: a successful fetch is NOT proof the symbol
                        # is alive, and treating it as such made the whole delisting mechanism
                        # unreachable.
                        #
                        # The detector only ever incremented on YFTickerMissingError. But
                        # yfinance's behaviour depends on HOW you ask:
                        #     history(start=..., end=...)  -> 1 stale bar,          SUCCESS
                        #     history(period="1mo")        -> YFPricesMissingError, RAISES
                        # ingest_symbol() always builds an explicit start/end (incremental from
                        # head - 7 days), and for a delisted ticker that window still straddles
                        # its FINAL REAL BAR. So yfinance returns that one stale bar, this
                        # branch counted it as success, _clear_delisting_signal() reset the
                        # counter, and the confirmation threshold was unreachable BY
                        # CONSTRUCTION. Verified live: a real ingest_symbol("SKHYV") logged
                        # inserted=1 and left no signal key, with zero delisting keys in Redis
                        # and zero delisted_confirmed events in 7 days of logs — the mechanism
                        # had never fired once since it was written.
                        #
                        # So judge LIVENESS by the data, not by the absence of an exception: if
                        # the newest bar returned is older than the staleness window, this is a
                        # dead ticker that merely happens to still answer, and it should ACCRUE
                        # a signal rather than clear one.
                        #
                        # CRITICAL: this must not blanket-delist everything that looks stale in
                        # the DB. SSNLF and SKHYV are indistinguishable there (both 1 bar, both
                        # active) but have OPPOSITE causes — SKHYV genuinely raises
                        # YFPricesMissingError on a relative-period fetch, while SSNLF returns
                        # 23 real bars and is simply under-ingested. Keying off the freshness of
                        # the bars actually RETURNED gets this right: SSNLF's fetch brings back
                        # current bars and correctly clears, SKHYV's brings back only a
                        # months-old bar and correctly accrues.
                        _newest = pd.to_datetime(candidate["ts"]).max()
                        _age_days = (pd.Timestamp.utcnow().tz_localize(None) - _newest).days
                        if _age_days > _DELISTING_STALE_BAR_DAYS:
                            log.warning("ingest.stale_newest_bar", symbol=symbol,
                                        newest_bar=str(_newest)[:10], age_days=_age_days,
                                        threshold_days=_DELISTING_STALE_BAR_DAYS)
                            _record_delisting_signal(symbol)
                        else:
                            _clear_delisting_signal(symbol)
                    break
                log.warning("ingest.adapter_empty", adapter=adapter.name, symbol=symbol)
            except yf.exceptions.YFTickerMissingError as exc:
                log.warning("ingest.adapter_failed", adapter=adapter.name, symbol=symbol, error=str(exc))
                last_err = exc
                if _track_delisting:
                    _record_delisting_signal(symbol)
            except Exception as exc:
                log.warning("ingest.adapter_failed", adapter=adapter.name, symbol=symbol, error=str(exc))
                last_err = exc
        if df is None:
            if last_err:
                raise IngestionError(f"All adapters failed for {symbol}: {last_err}")
            return {"symbol": symbol, "inserted": 0, "skipped": "no_bars"}

        # Parquet write (partitioned by symbol)
        _write_parquet(df, symbol, timeframe)

        rows = [
            {
                "stock_id": stock.id,
                "ts": (_ts := r.ts.to_pydatetime() if hasattr(r.ts, "to_pydatetime") else r.ts),
                "timeframe": tf,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": float(r.volume),
                "adj_close": float(r.adj_close) if pd.notna(r.adj_close) else None,
                "session": _classify_session(_ts, market) if timeframe not in ("1d", "1w") else "REGULAR",
            }
            for r in df.itertuples(index=False)
        ]

        # BUG-INGEST-CARDINALITYVIOLATION: `ON CONFLICT DO UPDATE` cannot resolve two rows in
        # the SAME insert statement mapping to the same conflict target (stock_id, ts,
        # timeframe) — Postgres raises psycopg2.errors.CardinalityViolation, aborting the whole
        # batch. Confirmed live in production (repeated real GDX 1d-bar ingestions, 20 identical
        # rows for the same calendar date in one batch) even though every deliberate,
        # deterministic reproduction attempt against the real yfinance API for the exact same
        # symbol/window/timeframe came back clean — this points at a rare, non-deterministic
        # duplication somewhere upstream (yfinance/curl_cffi under this app's real concurrent
        # ThreadPoolExecutor load) rather than a bug in this app's own request construction. The
        # root cause may be outside this app's control, but the batch-level defense is not: no
        # single ingest call has any legitimate reason to write the SAME (stock_id, ts,
        # timeframe) key twice, so deduplicate immediately before the conflict target ever
        # matters. Keeps the LAST occurrence per key (dict insertion order) — if the same bar
        # genuinely appears twice with a revised price (e.g. a late correction), the later value
        # is the more current one to keep, matching how a legitimate re-ingest of the same date
        # is expected to behave (the newer fetch's value should win).
        _dedup: dict[tuple, dict] = {}
        for row in rows:
            _dedup[(row["stock_id"], row["ts"], row["timeframe"])] = row
        if len(_dedup) < len(rows):
            log.warning("ingest.duplicate_rows_deduped", symbol=symbol, timeframe=timeframe,
                        before=len(rows), after=len(_dedup))
        rows = list(_dedup.values())

        stmt = pg_insert(Price).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_id", "ts", "timeframe"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "adj_close": stmt.excluded.adj_close,
                "session": stmt.excluded.session,
            },
        )
        # AUD-ING-POLYGONDELAYED: capture the DB head BEFORE the upsert so the result can say
        # whether this call actually ADVANCED the series. `result.rowcount` on an
        # ON CONFLICT DO UPDATE counts rows SENT (inserted + updated), so a call that re-upserts
        # 5 identical existing bars and adds nothing new reports `inserted=5` and logs a clean
        # `ingest.done` — which is precisely how 4 US symbols sat frozen at 2026-09-04 for four
        # days with no error and no alert anywhere. "Success" must mean the head moved.
        _head_before = head
        result = session.execute(stmt)
        session.commit()

        _head_after = _last_bar_ts(session, stock.id, tf)
        _advanced = bool(
            _head_after is not None
            and (_head_before is None or _head_after > _head_before)
        )
        _newest_written = max((r["ts"] for r in rows), default=None)

        log.info("ingest.done", symbol=symbol, inserted=result.rowcount, tf=timeframe,
                 rows_sent=len(rows), advanced=_advanced,
                 head_before=str(_head_before)[:19] if _head_before else None,
                 head_after=str(_head_after)[:19] if _head_after else None,
                 newest_bar=str(_newest_written)[:19] if _newest_written else None,
                 adapter=adapter.name)
        return {
            "symbol": symbol,
            "inserted": result.rowcount,
            "tf": timeframe,
            # `inserted` is kept for backward compatibility with existing callers, but it does
            # NOT mean "new bars" — read `advanced` for that.
            "advanced": _advanced,
            "head_after": _head_after,
            "adapter": adapter.name,
        }


def _write_parquet(df: pd.DataFrame, symbol: str, timeframe: str) -> None:
    out = Path(_settings.parquet_dir) / f"timeframe={timeframe}" / f"symbol={symbol}"
    out.mkdir(parents=True, exist_ok=True)
    fname = out / f"{df['ts'].min().strftime('%Y%m%d')}_{df['ts'].max().strftime('%Y%m%d')}.parquet"
    df.to_parquet(fname, index=False)


def _bust_live_price_cache() -> None:
    try:
        from common.redis_client import get_redis as _get_pool_redis
        r = _get_pool_redis()
        r.delete("stockai:live_prices")
    except Exception:
        pass


def ingest_universe(symbols: list[str], timeframe: str = "1d", max_workers: int = 6, force: bool = False) -> list[dict]:
    """Fetch all symbols in parallel (I/O-bound — safe to thread)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch(sym: str) -> dict:
        try:
            return ingest_symbol(sym, timeframe=timeframe, force=force)
        except Exception as exc:
            log.error("ingest.symbol_failed", symbol=sym, error=str(exc))
            return {"symbol": sym, "error": str(exc)}

    results: list[dict] = [{}] * len(symbols)
    index = {sym: i for i, sym in enumerate(symbols)}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            results[index[sym]] = fut.result()
    _bust_live_price_cache()
    return results

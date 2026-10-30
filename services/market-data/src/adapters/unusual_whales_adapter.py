"""AUD-ING-POLYGONDELAYED: a real OHLCV adapter backed by Unusual Whales' /ohlc endpoint.

WHY THIS EXISTS. The US daily ingest silently stalled for 4 symbols (UBER, CWEN, ARMK, BIP) at
2026-09-04 for four days, with no error anywhere. Root cause: the configured Polygon key is on a
`DELAYED` plan whose newest daily bar IS 2026-09-04. For a symbol whose DB head had already
reached 09-04, the incremental window (head - 7d .. today) asked Polygon for a range it could
only answer with `resultsCount: 0` — an empty 200, not an error — and Polygon sits FIRST in
`_PRIORITY`. Symbols whose head was further back still overlapped Polygon's coverage, got real
bars, and stayed healthy. That is why it hit 4 of 131 rather than all of them.

It was self-concealing in the worst way: `ingest_symbol()` returned `{'inserted': 5}` and logged
`ingest.done`, because `inserted` counts rows SENT to an upsert, not rows CHANGED. Zero new bars,
a clean success log. Same class as the 2026-09-07 series: not a crash, a silent wrong answer.

WHY UW AND NOT JUST YFINANCE. yfinance remains the fallback and is still the sole HK source, but
it is an unauthenticated scrape with no quota guarantee — during this very investigation it
returned HTTP 429 on every retry for all 4 symbols, so the urgent backfill could not run through
it. UW is authenticated, paid, and metered at 120k requests/day. At 131 US symbols x 5 refreshes
= 655 requests/day it costs 0.55% of that budget.

THREE THINGS ABOUT THIS ENDPOINT THAT WILL BITE A FUTURE READER:

1. **It returns THREE rows per calendar date**, tagged `market_time`: `pr` (pre-market), `r`
   (regular session) and `po` (post-market). Ingesting the payload as-is writes 3 bars per day.
   Only `r` is the real daily bar, and `_MARKET_TIME_REGULAR` is the filter. Verified: AAPL
   returns 756 rows over 252 distinct dates.

2. **`limit` silently returns an EMPTY list when too large.** `limit=5000` -> `{"data":[]}`,
   HTTP 200, no error. Same silent-empty shape as the Polygon bug this adapter exists to fix, so
   `_MAX_LIMIT = 500` is enforced here rather than trusted to the caller.

3. **The sibling `/api/screener/stocks` batch endpoint cannot substitute for this one.** It
   accepts a comma-separated ticker list (3 requests would cover all 131 US symbols) but silently
   caps at 50 rows AND returns `open: None` — and `validate_ohlcv()` requires `open` and enforces
   `low <= open <= high`. It is a fine freshness sweep; it is not a bar source. Do not "optimise"
   this adapter into it. Saving 640 requests out of 120,000 is not worth fabricating an `open`.

DELIBERATELY US-ONLY. UW has no HK coverage; `supports()` returns False for HK so the registry
keeps routing HK to yfinance, unchanged.
"""
from __future__ import annotations

from datetime import date

import httpx
import pandas as pd
import structlog

from .base import DataAdapter, OHLCV
from .registry import register_adapter

log = structlog.get_logger()

# AUD-UW-ADAPTER-UNINSTRUMENTED (2026-10-02): THIS ADAPTER WAS INVISIBLE TO EVERY UW METRIC.
#
# It builds its own `httpx.Client`, so it never passed through `services/unusual_whales.py`'s
# `_record_call_status()` / `_incr_rate_limit_counter()` / `uw_calls` counters. Two consequences
# measured the day this was found:
#
#   * The UW usage dashboard read 75,811 calls for 2026-10-02 and listed no `ohlc` endpoint at
#     all, while this adapter was making an estimated ~14,768 — true usage ~90.6k of a 120k
#     budget, with 20% of it unaccounted.
#   * The UW rate-limit gauge read 5-16/hour while this adapter's own 429s ran ~232/hour, a
#     ~33x undercount. The gauge that exists to show UW throttling was blind to its largest
#     source.
#
# So every call and every failure is now recorded under the SAME key shapes the service module
# uses, and 429s feed the existing gauge. Reporting is fail-open: a Redis hiccup must never
# turn a metric into a failed ingest.
_CALL_COUNTER_PREFIX = "stockai:metric:uw_calls"
_FAILURE_COUNTER_PREFIX = "stockai:metric:uw_adapter_failures"
_COUNTER_TTL_S = 49 * 3600

# AUD-UW-BURST: a throttled UW used to cost a failed call on EVERY symbol.
#
# `ingest_universe` walks ~142 US symbols in a tight loop every five minutes, and the registry
# puts this adapter first. Once UW starts refusing, each of those 142 symbols still paid a full
# round trip to be told 429 before falling through to yfinance — 142 wasted calls per burst,
# against the very quota that was exhausted, and each one also delaying the fallback.
#
# A short cooldown turns that into ONE wasted call per window: the first 429 arms it, and
# `supports()` then reports False so the registry routes straight to yfinance until it expires.
# Deliberately short — this is a step-aside, not a circuit breaker with a long memory, and UW
# must get a chance to come back on its own.
_COOLDOWN_KEY = "stockai:uw_adapter:cooldown"
_COOLDOWN_SECONDS = 120

# AUD-UW-COOLDOWN-YFSPILL (2026-10-30): every 429 on Oct 28-29 (115) was on THIS endpoint, at
# ~24-29k calls/day against a 120k quota, each within seconds of a 5-minute run starting — a
# per-minute burst, not the daily budget. This path never read UW's per-minute headers, so how
# close a run comes to the limit was unknown. One hash per hour, field = minute (UTC), value = the
# LOWEST remaining UW reported in that minute; "limit" = counter + remaining as last reported.
# Read-compare-write races between ingest threads can only keep a slightly higher minimum.
_MINUTE_HEADROOM_PREFIX = "stockai:metric:uw_bar_minute_remaining"

_BASE = "https://api.unusualwhales.com"
# See note 2 above — a larger value silently returns an empty list.
_MAX_LIMIT = 500
# See note 1 above — 'pr'/'po' are pre/post-market rows for the SAME date.
_MARKET_TIME_REGULAR = "r"

# UW's candle_size values, mapped from this app's timeframe vocabulary.
_TF_CANDLE = {"1d": "1d", "1h": "1h", "15m": "15m", "5m": "5m", "1m": "1m"}


class UnusualWhalesAdapter(DataAdapter):
    name = "unusual_whales"
    supported_markets = ("US",)

    def supports_static(self, market: str, timeframe: str) -> bool:
        """Routing capability, ignoring the temporary AUD-UW-BURST cooldown (bar authority uses it)."""
        return market == "US" and timeframe in _TF_CANDLE

    def supports(self, market: str, timeframe: str) -> bool:
        if not self.supports_static(market, timeframe):
            return False
        # AUD-UW-BURST: step aside while cooling down so the registry picks yfinance without
        # paying a round trip first. Fail-open on any Redis problem — an unavailable metric
        # store must not remove a working data source.
        try:
            from common.redis_client import get_redis
            if get_redis().get(_COOLDOWN_KEY):
                return False
        except Exception:
            pass
        return True

    @staticmethod
    def _note(kind: str, name: str) -> None:
        """Record one call or one failure. Never raises."""
        try:
            from datetime import datetime, timezone
            from common.redis_client import get_redis
            now = datetime.now(timezone.utc)
            if kind == "call":
                key = f"{_CALL_COUNTER_PREFIX}:{name}:{now:%Y%m%d}"
            else:
                key = f"{_FAILURE_COUNTER_PREFIX}:{name}:{now:%Y%m%d%H}"
            r = get_redis()
            if r.incr(key) == 1:
                r.expire(key, _COUNTER_TTL_S)
        except Exception:
            pass

    @staticmethod
    def _note_minute_headroom(headers) -> tuple[int | None, int | None]:
        """Record the per-minute headroom UW reported. Never raises; returns (used, remaining)."""
        try:
            used = headers.get("x-uw-minute-req-counter")
            remaining = headers.get("x-uw-req-per-minute-remaining")
            used = int(used) if used is not None else None
            remaining = int(remaining) if remaining is not None else None
            if remaining is None:
                return used, None
            from datetime import datetime, timezone
            from common.redis_client import get_redis
            now = datetime.now(timezone.utc)
            key = f"{_MINUTE_HEADROOM_PREFIX}:{now:%Y%m%d%H}"
            r = get_redis()
            cur = r.hget(key, f"{now:%M}")
            if cur is None or remaining < int(cur):
                r.hset(key, f"{now:%M}", remaining)
            if used is not None:
                r.hset(key, "limit", used + remaining)
            r.expire(key, _COUNTER_TTL_S)
            return used, remaining
        except Exception:
            return None, None

    @staticmethod
    def _arm_cooldown() -> None:
        try:
            from common.redis_client import get_redis
            get_redis().setex(_COOLDOWN_KEY, _COOLDOWN_SECONDS, "1")
        except Exception:
            pass

    @staticmethod
    def _key() -> str | None:
        """Read the key the same way every other UW caller does, so an admin toggle applies
        here too — a configured key alone does not mean the feature is enabled."""
        from common.ai_keys import get_unusual_whales_key, is_unusual_whales_enabled
        if not is_unusual_whales_enabled():
            return None
        return get_unusual_whales_key()

    def fetch_ohlcv(
        self, symbol: str, start: date, end: date, timeframe: str = "1d"
    ) -> OHLCV:
        key = self._key()
        if not key:
            self._note("fail", "no_key")
            raise RuntimeError("Unusual Whales key not configured or feature disabled")
        candle = _TF_CANDLE.get(timeframe)
        if candle is None:
            self._note("fail", "unsupported_timeframe")
            raise ValueError(f"unsupported timeframe for Unusual Whales: {timeframe}")

        log.info("unusual_whales.fetch", symbol=symbol, tf=timeframe)
        # AUD-UW-ADAPTER-UNINSTRUMENTED: counted under the same endpoint-shaped key the service
        # module uses, so the usage dashboard finally sees the bar path. The path template is
        # written out rather than interpolated per symbol — one counter per endpoint, not one
        # per ticker, matching how every other UW endpoint is tallied.
        self._note("call", "/api/stock/{symbol}/ohlc/{candle}")
        try:
            with httpx.Client(timeout=30) as client:
                r = client.get(
                    f"{_BASE}/api/stock/{symbol}/ohlc/{candle}",
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                    params={"limit": _MAX_LIMIT},
                )
                _headers = getattr(r, "headers", None) or {}
                _used, _remaining = self._note_minute_headroom(_headers)
                if r.status_code == 429:
                    # Feed the EXISTING gauge, which this path never reached before, and step
                    # aside so the next 141 symbols in this burst do not each pay for the same
                    # refusal.
                    self._note("fail", "rate_limited")
                    self._arm_cooldown()
                    log.warning("unusual_whales.bar_rate_limited", symbol=symbol, tf=timeframe,
                                minute_used=_used, minute_remaining=_remaining,
                                minute_reset_ms=_headers.get("x-uw-req-per-minute-reset"))
                    try:
                        from ..services.unusual_whales import _incr_rate_limit_counter
                        _incr_rate_limit_counter()
                    except Exception:
                        pass
                    raise RuntimeError("Unusual Whales rate limit exceeded")
                if r.status_code >= 400:
                    # Classified by STATUS, because "the other 10,342 failures" could not be
                    # explained from logs that no longer existed. A counter survives a rebuild.
                    self._note("fail", f"http_{r.status_code}")
                r.raise_for_status()
                rows = (r.json() or {}).get("data") or []
        except httpx.TimeoutException:
            self._note("fail", "timeout")
            raise
        except httpx.HTTPStatusError:
            raise  # already classified by status above
        except RuntimeError:
            raise  # rate-limit, already counted
        except Exception as exc:
            self._note("fail", f"other_{type(exc).__name__}")
            raise

        # AUD-UW-INTRADAY-TZ (2026-10-14): EVERYTHING AFTER THE RESPONSE IS METERED TOO.
        # The window filter below used to raise `TypeError: Invalid comparison between
        # dtype=datetime64[ns, UTC] and Timestamp` on EVERY intraday request, outside the
        # request's own try block — so it was counted nowhere, and every 5m/15m/1h fetch fell
        # back to yfinance silently. A processing failure is now recorded once, by type, and
        # re-raised so the registry's fallback behaves exactly as before.
        try:
            return OHLCV(symbol, timeframe, self._process(rows, start, end, timeframe))
        except Exception as exc:
            self._note("fail", f"processing_{type(exc).__name__}")
            raise

    def _process(self, rows: list, start, end, timeframe: str) -> pd.DataFrame:
        """Regular-session rows → canonical frame, filtered to the caller's window.

        THE TWO PAYLOAD SHAPES (captured from production 2026-10-14, GDX):
          * `1d`: `date` = the MARKET date ("2026-10-14"); `start_time`/`end_time` are None.
          * intraday: `start_time`/`end_time` in UTC ("2026-10-14T19:00:00Z"); `date` is None.

        WINDOW SEMANTICS. `start`/`end` are DATES (ingestion passes `date` objects), so both are
        inclusive whole days:
          * daily bars are kept when their market date is in [start, end];
          * intraday bars are kept when their US/Eastern trading date is in [start, end] — the
            whole end date, not just up to its midnight. Merely attaching UTC to the old naive
            bounds would have compared against end-date 00:00 and dropped that day's bars.
        A `datetime` bound (none today, accepted for safety) is an INSTANT: naive is UTC.
        """
        empty = pd.DataFrame(columns=["ts"])
        if not rows:
            return empty
        # Note 1: keep ONLY the regular session, else every date lands three times.
        rows = [x for x in rows if x.get("market_time") == _MARKET_TIME_REGULAR]
        if not rows:
            return empty
        df = pd.DataFrame(rows)
        for col in ("open", "high", "low", "close", "volume"):
            # UW returns prices as STRINGS ("316.22"); volume comes back as an int.
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["adj_close"] = df["close"]

        daily = timeframe == "1d"
        if daily:
            if "date" not in df or df["date"].isna().all():
                raise ValueError("daily candles carry no `date`")
            df["ts"] = pd.to_datetime(df["date"], errors="raise")      # naive market date
            day = df["ts"].dt.date
        else:
            if "start_time" not in df or df["start_time"].isna().all():
                raise ValueError("intraday candles carry no `start_time`")
            df["ts"] = pd.to_datetime(df["start_time"], utc=True, errors="raise")
            day = df["ts"].dt.tz_convert("America/New_York").dt.date
        df = df[_within(df["ts"], day, start, end)]
        df = df.sort_values("ts").reset_index(drop=True)
        # Canonical: daily → naive market date; intraday → tz-aware UTC, which _to_canonical
        # stores as a naive UTC timestamp with its time kept.
        return self._to_canonical(df)


def _within(ts: pd.Series, day: pd.Series, start, end) -> pd.Series:
    """Inclusive window. Dates compare by (market) date; datetimes compare as UTC instants."""
    from datetime import datetime as _dt

    def bound(b, is_start):
        if isinstance(b, _dt):
            t = pd.Timestamp(b)
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            return ("instant", t)
        return ("date", pd.Timestamp(b).date() if not isinstance(b, date) else b)

    keep = pd.Series(True, index=ts.index)
    aware = getattr(ts.dt, "tz", None) is not None
    for b, is_start in ((start, True), (end, False)):
        if b is None:
            continue
        kind, v = bound(b, is_start)
        if kind == "date":
            keep &= (day >= v) if is_start else (day <= v)
        else:
            t = ts if aware else ts.dt.tz_localize("UTC")
            keep &= (t >= v) if is_start else (t <= v)
    return keep


register_adapter(UnusualWhalesAdapter())

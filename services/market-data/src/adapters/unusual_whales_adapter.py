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

    def supports(self, market: str, timeframe: str) -> bool:
        if not (market == "US" and timeframe in _TF_CANDLE):
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
                if r.status_code == 429:
                    # Feed the EXISTING gauge, which this path never reached before, and step
                    # aside so the next 141 symbols in this burst do not each pay for the same
                    # refusal.
                    self._note("fail", "rate_limited")
                    self._arm_cooldown()
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

        if not rows:
            return OHLCV(symbol, timeframe, pd.DataFrame(columns=["ts"]))

        # Note 1: keep ONLY the regular session, else every date lands three times.
        rows = [x for x in rows if x.get("market_time") == _MARKET_TIME_REGULAR]
        if not rows:
            return OHLCV(symbol, timeframe, pd.DataFrame(columns=["ts"]))

        df = pd.DataFrame(rows)
        df = df.rename(columns={"date": "ts", "start_time": "ts"})
        for col in ("open", "high", "low", "close", "volume"):
            # UW returns prices as STRINGS ("316.22"); volume comes back as an int.
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["adj_close"] = df["close"]
        df["ts"] = pd.to_datetime(df["ts"])

        # The endpoint has no start/end params — it returns the most recent `limit` candles, so
        # the caller's window is applied here. Inclusive of both ends, matching how the
        # yfinance/Polygon adapters' windows behave for daily bars.
        df = df[(df["ts"] >= pd.Timestamp(start)) & (df["ts"] <= pd.Timestamp(end))]

        df = df.sort_values("ts").reset_index(drop=True)
        return OHLCV(symbol, timeframe, self._to_canonical(df))


register_adapter(UnusualWhalesAdapter())

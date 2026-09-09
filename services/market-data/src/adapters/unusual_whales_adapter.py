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
        return market == "US" and timeframe in _TF_CANDLE

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
            raise RuntimeError("Unusual Whales key not configured or feature disabled")
        candle = _TF_CANDLE.get(timeframe)
        if candle is None:
            raise ValueError(f"unsupported timeframe for Unusual Whales: {timeframe}")

        log.info("unusual_whales.fetch", symbol=symbol, tf=timeframe)
        with httpx.Client(timeout=30) as client:
            r = client.get(
                f"{_BASE}/api/stock/{symbol}/ohlc/{candle}",
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                params={"limit": _MAX_LIMIT},
            )
            if r.status_code == 429:
                raise RuntimeError("Unusual Whales rate limit exceeded")
            r.raise_for_status()
            rows = (r.json() or {}).get("data") or []

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

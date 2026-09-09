"""Alpha Vantage free-tier adapter (US equities)."""
from __future__ import annotations

from datetime import date
from io import StringIO

import httpx
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from common.config import get_settings
from common.logging import get_logger

from .base import DataAdapter, OHLCV
from .registry import get_runtime_key, register_adapter

log = get_logger("alpha_vantage_adapter")

_TF_FN = {
    "1d": "TIME_SERIES_DAILY_ADJUSTED",
    "1w": "TIME_SERIES_WEEKLY_ADJUSTED",
}


class AlphaVantageAdapter(DataAdapter):
    name = "alpha_vantage"
    supported_markets = ("US",)
    _BASE = "https://www.alphavantage.co/query"

    def __init__(self) -> None:
        self._key = get_settings().alpha_vantage_api_key

    def _active_key(self) -> str:
        return get_runtime_key("alpha_vantage") or self._key or ""

    def supports(self, market: str, timeframe: str) -> bool:
        return market == "US" and timeframe in _TF_FN and bool(self._active_key())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def fetch_ohlcv(self, symbol: str, start: date, end: date, timeframe: str = "1d") -> OHLCV:
        key = self._active_key()
        if not key:
            raise RuntimeError("ALPHA_VANTAGE_API_KEY not configured")
        fn = _TF_FN.get(timeframe, "TIME_SERIES_DAILY_ADJUSTED")
        params = {
            "function": fn,
            "symbol": symbol,
            "outputsize": "full",
            "datatype": "csv",
            "apikey": key,
        }
        log.info("alpha_vantage.fetch", symbol=symbol, fn=fn)
        with httpx.Client(timeout=30) as client:
            r = client.get(self._BASE, params=params)
            # AUD-AVKEY-INEXCEPTION (2026-09-09): Alpha Vantage has NO header auth — the key must
            # travel as the `apikey` QUERY PARAM — so the sibling fix used for Polygon
            # (Authorization: Bearer, AUD-POLYGONKEY-INURL) is not available here.
            #
            # The leak path is NOT the request logger: `configure_logging()` (called for every
            # service via common.service.create_app) already pins httpx to WARNING, so no request
            # line is ever emitted. It is `raise_for_status()`, whose HTTPStatusError message
            # EMBEDS THE FULL URL — and callers log that message at ERROR
            # (`ingest.adapter_failed` / `ingest.symbol_failed`), which no log level suppresses.
            # Measured on the Polygon twin: 58 plaintext key occurrences in one container's logs,
            # every one of them from an exception message, none from a request line.
            #
            # So re-raise with a URL-free message. The status code and symbol are preserved
            # (that is all a caller needs to retry or fail over); only the URL is dropped.
            # `from None` suppresses the __cause__ chain, since the original exception's own
            # repr would otherwise carry the URL straight into the traceback anyway.
            if r.status_code >= 400:
                raise RuntimeError(
                    f"Alpha Vantage HTTP {r.status_code} for {symbol} ({fn}) "
                    f"— URL withheld (carries the API key)"
                ) from None
            df = pd.read_csv(StringIO(r.text))

        df = df.rename(
            columns={
                "timestamp": "ts",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "adjusted_close": "adj_close",
                "volume": "volume",
            }
        )
        df = df[(pd.to_datetime(df["ts"]).dt.date >= start) & (pd.to_datetime(df["ts"]).dt.date <= end)]
        return OHLCV(symbol, timeframe, self._to_canonical(df))


register_adapter(AlphaVantageAdapter())

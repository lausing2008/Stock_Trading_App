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
from .registry import register_adapter

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

    def supports(self, market: str, timeframe: str) -> bool:
        return market == "US" and timeframe in _TF_FN and bool(self._key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def fetch_ohlcv(self, symbol: str, start: date, end: date, timeframe: str = "1d") -> OHLCV:
        if not self._key:
            raise RuntimeError("ALPHA_VANTAGE_API_KEY not configured")
        fn = _TF_FN.get(timeframe, "TIME_SERIES_DAILY_ADJUSTED")
        params = {
            "function": fn,
            "symbol": symbol,
            "outputsize": "full",
            "datatype": "csv",
            "apikey": self._key,
        }
        log.info("alpha_vantage.fetch", symbol=symbol, fn=fn)
        with httpx.Client(timeout=30) as client:
            r = client.get(self._BASE, params=params)
            r.raise_for_status()
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

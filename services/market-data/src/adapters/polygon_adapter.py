"""Polygon.io free-tier adapter."""
from __future__ import annotations

from datetime import date

import httpx
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from common.config import get_settings
from common.logging import get_logger

from .base import DataAdapter, OHLCV
from .registry import register_adapter

log = get_logger("polygon_adapter")

_TF_MULT = {
    "1m": (1, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
    "1w": (1, "week"),
}


class PolygonAdapter(DataAdapter):
    name = "polygon"
    supported_markets = ("US",)
    _BASE = "https://api.polygon.io"

    def __init__(self) -> None:
        self._key = get_settings().polygon_api_key

    def supports(self, market: str, timeframe: str) -> bool:
        return market == "US" and timeframe in _TF_MULT and bool(self._key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def fetch_ohlcv(self, symbol: str, start: date, end: date, timeframe: str = "1d") -> OHLCV:
        if not self._key:
            raise RuntimeError("POLYGON_API_KEY not configured")
        mult, span = _TF_MULT[timeframe]
        url = (
            f"{self._BASE}/v2/aggs/ticker/{symbol}/range/{mult}/{span}/"
            f"{start.isoformat()}/{end.isoformat()}"
        )
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": self._key}
        log.info("polygon.fetch", symbol=symbol, tf=timeframe)
        with httpx.Client(timeout=30) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            data = r.json().get("results", []) or []
        if not data:
            return OHLCV(symbol, timeframe, pd.DataFrame(columns=["ts"]))
        df = pd.DataFrame(data).rename(
            columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
        )
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None)
        df["adj_close"] = df["close"]
        return OHLCV(symbol, timeframe, self._to_canonical(df))


register_adapter(PolygonAdapter())

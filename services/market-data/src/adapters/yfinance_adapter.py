"""yfinance adapter — US + HK (ticker.HK) coverage."""
from __future__ import annotations

from datetime import date

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from common.logging import get_logger

from .base import DataAdapter, OHLCV
from .registry import register_adapter

_TIMEFRAME_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "60m",
    "1d": "1d",
    "1w": "1wk",
}

log = get_logger("yfinance_adapter")


class YFinanceAdapter(DataAdapter):
    name = "yfinance"
    supported_markets = ("US", "HK")

    def supports(self, market: str, timeframe: str) -> bool:
        return market in self.supported_markets and timeframe in _TIMEFRAME_MAP

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def fetch_ohlcv(
        self,
        symbol: str,
        start: date,
        end: date,
        timeframe: str = "1d",
    ) -> OHLCV:
        interval = _TIMEFRAME_MAP.get(timeframe, "1d")
        log.info("yfinance.fetch", symbol=symbol, start=str(start), end=str(end), tf=timeframe)
        df = yf.download(
            tickers=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if df is None or df.empty:
            return OHLCV(symbol, timeframe, pd.DataFrame(columns=["ts"]))

        df = df.reset_index().rename(
            columns={
                "Date": "ts",
                "Datetime": "ts",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adj_close",
                "Volume": "volume",
            }
        )
        # yfinance sometimes returns a MultiIndex column frame for single tickers
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        return OHLCV(symbol, timeframe, self._to_canonical(df))


register_adapter(YFinanceAdapter())

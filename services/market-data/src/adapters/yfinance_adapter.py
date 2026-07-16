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
        ticker = yf.Ticker(symbol)

        # Daily bars: auto_adjust=True so Close is already split+dividend-adjusted.
        # Intraday bars: auto_adjust=False (yfinance does not reliably adjust intraday).
        use_adjusted = (timeframe == "1d")
        # T230-CHARTING-PREMARKET: prepost=True on intraday fetches includes pre/post-market
        # bars in the same dataframe (yfinance's normal behavior, no separate call needed).
        # Daily bars never carry a prepost concept — leave those requests untouched.
        is_intraday = timeframe != "1d" and timeframe != "1w"
        df = ticker.history(
            start=start.isoformat(),
            end=end.isoformat(),
            interval=interval,
            auto_adjust=use_adjusted,
            prepost=is_intraday,
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
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        return OHLCV(symbol, timeframe, self._to_canonical(df))


register_adapter(YFinanceAdapter())

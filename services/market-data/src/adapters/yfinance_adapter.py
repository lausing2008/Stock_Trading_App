"""yfinance adapter — US + HK (ticker.HK) coverage."""
from __future__ import annotations

from datetime import date

import pandas as pd
import yfinance as yf
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

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
        # AUD-SURVIVORSHIP-DELISTDETECT: YFTickerMissingError (raised as either of its two
        # observed subclasses in practice, YFTzMissingError or YFPricesMissingError — both
        # confirmed live against real delisted tickers) means yfinance/Yahoo itself reported
        # "no data found, symbol may be delisted" — a condition retrying will never resolve
        # (unlike a transient rate-limit/network error, which raises the SEPARATE, non-subclass
        # YFRateLimitError instead, and where retrying legitimately helps). Retrying this 3x
        # would just waste time and delay the delisting signal for no benefit — excluded from
        # the retry policy so it raises immediately on first occurrence.
        retry=retry_if_not_exception_type(yf.exceptions.YFTickerMissingError),
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
        # AUD-SURVIVORSHIP-DELISTDETECT: raise_errors=True makes yfinance raise a
        # YFTickerMissingError subclass instead of silently swallowing it into an empty
        # DataFrame — confirmed live against 5 real delisted tickers (Lehman Brothers, Sears,
        # Bed Bath & Beyond, etc.), this is Yahoo's OWN API telling us "no data found, symbol
        # may be delisted" (a distinct error code from a rate-limit/timeout, which raises the
        # structurally SEPARATE YFRateLimitError instead — confirmed NOT a subclass of
        # YFTickerMissingError) — not a guess inferred from emptiness. A real, currently-listed
        # stock queried with a `start` before its own IPO can ALSO raise this (verified: AAPL
        # queried from 1900 raises YFPricesMissingError too) — but this app's real call shape
        # (start = last known bar - 7d, or a 3-year lookback) never legitimately produces that
        # false-positive shape for an actually-still-listed stock (verified live: a recent IPO,
        # ARM, queried with a 3-year lookback returns its real post-IPO history with no error,
        # it does not raise). Let it propagate uncaught here; ingestion.py's adapter loop is
        # where it's actually handled.
        df = ticker.history(
            start=start.isoformat(),
            end=end.isoformat(),
            interval=interval,
            auto_adjust=use_adjusted,
            prepost=is_intraday,
            raise_errors=True,
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

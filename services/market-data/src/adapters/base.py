"""Provider-agnostic data adapter contract.

Each adapter returns a canonical DataFrame with columns:
    ts | open | high | low | close | volume | adj_close

This isolates the rest of the platform from any single vendor's quirks
and lets us swap free→paid providers via the registry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import pandas as pd


OHLCV_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "adj_close"]


@dataclass(frozen=True)
class OHLCV:
    symbol: str
    timeframe: str
    df: pd.DataFrame  # canonical columns


class DataAdapter(ABC):
    """Base class all provider adapters implement."""

    name: str = "base"
    supported_markets: tuple[str, ...] = ()

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        start: date,
        end: date,
        timeframe: str = "1d",
    ) -> OHLCV:
        """Return canonical OHLCV for the requested window."""

    @abstractmethod
    def supports(self, market: str, timeframe: str) -> bool:
        """Return True if this adapter can serve the market+timeframe."""

    @staticmethod
    def _to_canonical(df: pd.DataFrame) -> pd.DataFrame:
        """Reduce to canonical column set, dropping rows with missing OHLC."""
        missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
        for col in missing:
            df[col] = None
        df = df[OHLCV_COLUMNS].copy()
        df = df.dropna(subset=["open", "high", "low", "close"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
        return df.reset_index(drop=True)

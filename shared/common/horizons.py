"""The one place that says which signal horizons exist and where each one lives.

Intraday Stage C (docs/features/2026-10-29-intraday-stage-c-contract-design.md §1-§2).

The four DAILY horizons are the values of the shared PostgreSQL enum `signalhorizon` and the
only values daily code may accept. INTRADAY is a separate, same-session horizon: it is stored in
market-data's own `intraday_*` tables and is deliberately NOT a `signalhorizon` value, so the
database itself rejects it at every daily write. It is also not the daily SHORT horizon (SHORT's
intraday ATR refresh is a daily-model feature, unrelated to this).

Daily code must validate through `parse_daily_horizon`, never by iterating `SignalHorizon` or
falling back to SWING/all-horizons on an unknown value.

Pure: stdlib only, no I/O. Importable by any service and by a probe.
"""
from __future__ import annotations

from dataclasses import dataclass

DAILY_HORIZONS: tuple[str, ...] = ("SHORT", "SWING", "LONG", "GROWTH")


@dataclass(frozen=True)
class HorizonSpec:
    id: str
    label: str
    kind: str                      # 'intraday'
    storage: str                   # the table family that holds it; never signals/signal_outcomes
    markets: tuple[str, ...]
    trigger_timeframe: str
    context_timeframe: str
    forecast_windows_min: tuple[int, ...]
    primary_forecast_min: int
    max_hold_min: int


INTRADAY = HorizonSpec(
    id="INTRADAY", label="Intraday · same session", kind="intraday",
    storage="intraday_setups", markets=("US",),
    trigger_timeframe="5m", context_timeframe="15m",
    forecast_windows_min=(30, 60, 120), primary_forecast_min=60,
    max_hold_min=120,
)

#: A constant, not an environment flag: production Intraday generation needs a code change and a
#: review. Stage C ships no generator at all; this is what a future one must check.
INTRADAY_PRODUCTION_ENABLED = False


class UnsupportedHorizon(ValueError):
    """A value daily code must not accept. `hint` says where an INTRADAY caller should go."""

    def __init__(self, horizon):
        self.horizon = horizon
        self.hint = ("INTRADAY is a separate same-session horizon served by /stocks/intraday/*, "
                     "not by daily signal endpoints") if str(horizon).upper() == INTRADAY.id else None
        super().__init__(f"unsupported_horizon:{horizon!r}")


class UnsupportedMarket(ValueError):
    def __init__(self, market):
        self.market = market
        super().__init__(f"unsupported_market:{market!r}")


def parse_daily_horizon(value) -> str:
    """The canonical daily horizon for `value`, or UnsupportedHorizon. Case-insensitive for
    strings; an enum member is read through its `.value`. Never a fallback."""
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise UnsupportedHorizon(value)
    h = raw.strip().upper()
    if h not in DAILY_HORIZONS:
        raise UnsupportedHorizon(value)
    return h


def is_daily_horizon(value) -> bool:
    try:
        parse_daily_horizon(value)
        return True
    except UnsupportedHorizon:
        return False


def require_intraday_market(market) -> str:
    """'US', or UnsupportedMarket. Intraday v1 has no HK support (its lunch break needs a
    spans-aware bar preparation that does not exist)."""
    m = (market or "").strip().upper() if isinstance(market, str) else ""
    if m not in INTRADAY.markets:
        raise UnsupportedMarket(market)
    return m

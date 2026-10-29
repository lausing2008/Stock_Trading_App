"""Research fill model v1 and forecast outcomes (design §6-§7). Pure; completed 5m bars only.

Every fill here is a research assumption (`execution_grade=False`): a market order fills at the
open of the first bar that opens strictly after the order was active; resting bracket orders are
adjudicated on completed OHLC from the entry bar onward with the stop winning a double touch;
scheduled exits may use an open equal to their scheduled time because they were ready earlier.
No closing fill is ever invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .contract import (ContractViolation, Params, SessionTimes, bar_close, cancellation_vs_entry,
                       first_grid_open_after, first_grid_open_at_or_after, readable_at, utc)

FILL_BASIS = "next_bar_open_research"


@dataclass(frozen=True)
class Bar:
    open_at: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class TradeResult:
    state: str                    # entered->exited | unresolved_exit_at_close | expired | cancelled |
                                  # rejected_invalid_bracket_at_fill | chronology_ambiguous
    reason: str | None = None
    entry_fill_at: datetime | None = None
    entry_fill_price: float | None = None
    exit_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None            # stop | target | max_hold | liquidation
    exit_intrabar_time: str | None = None     # 'unknown' for bracket fills inside a bar
    r_multiple: float | None = None
    net_return: float | None = None
    mae: float | None = None
    mfe: float | None = None
    mark: float | None = None                 # unresolved only: last observed price, NOT realized
    mark_at: datetime | None = None
    evidence_eligible_at: datetime | None = None   # for a data-driven cancellation
    unobserved_intervals: int = 0
    fill_basis: str = FILL_BASIS
    execution_grade: bool = False
    notes: list = field(default_factory=list)


def _index(bars):
    out = {}
    for b in bars:
        t = utc(b.open_at)
        if t in out:
            raise ContractViolation(f"duplicate bar at {t}")
        out[t] = b
    return out


def _cost(price, params: Params):
    return price * params.cost_bps / 1e4 + params.fee_per_share


def simulate_long(*, entry_reference: float, stop: float, target: float, actionable_at: datetime,
                  entry_expires_at: datetime, times: SessionTimes, bars: list[Bar], params: Params = Params(),
                  cancellation_effective: datetime | None = None, cancellation_known: bool = True,
                  cancellation_reason: str = "invalidated_price") -> TradeResult:
    """Simulate one bullish setup. `cancellation_effective` is the effective time (contract.
    effective_time) of the earliest pre-entry cancellation, if any; `cancellation_known=False`
    means a cancellation exists whose chronology is unknown."""
    if not stop < entry_reference < target:
        raise ContractViolation("bullish bracket must satisfy stop < entry < target")
    by_open = _index(bars)
    slip = params.slippage_bps / 1e4

    # Entry: a market order active from actionable_at; the first grid open strictly after it and
    # strictly before expiry. A missing bar there is unobservable, never skipped to a later one.
    entry_open = first_grid_open_after(actionable_at, entry_expires_at, times)
    if entry_open is None:
        return TradeResult("expired")
    if not cancellation_known:
        return TradeResult("chronology_ambiguous", reason=cancellation_reason)
    if cancellation_effective is not None:
        verdict = cancellation_vs_entry(cancellation_effective, entry_open)
        if verdict != "entered":
            return TradeResult(verdict, reason=cancellation_reason)
    entry_bar = by_open.get(entry_open)
    if entry_bar is None:
        return TradeResult("cancelled", reason="data_unavailable",
                           evidence_eligible_at=readable_at(entry_open, "5m", times))
    fill = entry_bar.open * (1 + slip)
    if fill <= stop or fill >= target:
        return TradeResult("rejected_invalid_bracket_at_fill", entry_fill_at=entry_open, entry_fill_price=fill)
    risk = fill - stop

    time_exit = first_grid_open_at_or_after(entry_open + timedelta(minutes=params.max_hold_min), times)
    scheduled = min(t for t in (time_exit, times.liquidation_at) if t is not None)
    scheduled_reason = "max_hold" if time_exit is not None and time_exit < times.liquidation_at else "liquidation"

    res = TradeResult("entered", entry_fill_at=entry_open, entry_fill_price=fill)
    lo, hi = fill, fill
    t = entry_open
    last_seen = None
    while t < times.close:
        b = by_open.get(t)
        if t >= scheduled:
            # Scheduled order, ready since entry: fills at this open, before anything inside it.
            # A missing bar is no executable observation: the order stays working and fills at
            # the next observed open before the close, or not at all.
            if b is None:
                res.unobserved_intervals += 1
                t += timedelta(minutes=5)
                continue
            reason = "liquidation" if t >= times.liquidation_at else scheduled_reason
            return _exit(res, t, b.open * (1 - slip), reason, None, risk, fill, lo, hi, params)
        if b is None:
            res.unobserved_intervals += 1          # a stop may have fired unseen: flagged, not assumed
            t += timedelta(minutes=5)
            continue
        last_seen = b
        if t != entry_open and b.open <= stop:
            return _exit(res, t, b.open * (1 - slip), "stop", None, risk, fill, min(lo, b.open), hi, params)
        if b.low <= stop:                       # double touch: the stop wins
            return _exit(res, t, stop * (1 - slip), "stop", "unknown", risk, fill, min(lo, b.low), max(hi, b.high), params)
        if b.high > target:                     # a limit fills only if traded through
            return _exit(res, t, target, "target", "unknown", risk, fill, lo, max(hi, b.high), params)
        lo, hi = min(lo, b.low), max(hi, b.high)
        t += timedelta(minutes=5)
    # No executable observation for the liquidation order before the close: never invent a fill.
    res.state = "unresolved_exit_at_close"
    if last_seen is not None:
        res.mark = last_seen.close
        res.mark_at = bar_close(last_seen.open_at, "5m", times)
    res.mae, res.mfe = (lo - fill) / fill, (hi - fill) / fill
    return res


def _exit(res, at, price, reason, intrabar, risk, fill, lo, hi, params):
    res.state = "exited"
    res.exit_at, res.exit_price, res.exit_reason, res.exit_intrabar_time = at, price, reason, intrabar
    net = price - fill - _cost(fill, params) - _cost(price, params)
    res.r_multiple = net / risk
    res.net_return = net / fill
    res.mae, res.mfe = (lo - fill) / fill, (hi - fill) / fill
    return res


@dataclass(frozen=True)
class Forecast:
    window_min: int
    status: str                   # resolved | truncated_session_end | missing_terminal_bar
    actual_min: int | None
    end_bar_open: datetime | None
    ret: float | None
    entry_basis: str = "trigger_close_descriptive"


def forecast_outcomes(*, trigger_bar_open: datetime, trigger_close: float, times: SessionTimes,
                      bars: list[Bar], params: Params = Params()) -> list[Forecast]:
    """Descriptive returns from the trigger bar's close; never combined with trade returns."""
    by_open = _index(bars)
    start = bar_close(trigger_bar_open, "5m", times)
    out = []
    for w in params.forecast_windows_min:
        end = start + timedelta(minutes=w)
        if end > times.close:
            # Score at the last completed bar of the session; store its real length.
            last = max((t for t in by_open if start <= t < times.close), default=None)
            if last is None:
                out.append(Forecast(w, "missing_terminal_bar", None, None, None))
                continue
            closes = bar_close(last, "5m", times)
            out.append(Forecast(w, "truncated_session_end", int((closes - start).total_seconds() // 60),
                                last, by_open[last].close / trigger_close - 1))
            continue
        end_open = end - timedelta(minutes=5)
        b = by_open.get(end_open)
        if b is None:
            out.append(Forecast(w, "missing_terminal_bar", None, end_open, None))
        else:
            out.append(Forecast(w, "resolved", w, end_open, b.close / trigger_close - 1))
    return out

"""T241-POSITION-SCALING Phase 1: multi-tranche position simulation for backtesting.

Extends the backtesting capability so a single logical position can consist of multiple
tranches (entries), each with its own entry price, timestamp, and size, with a running
weighted-average cost basis recalculated after each tranche and exit barriers evaluated
against that blended basis rather than the original entry price alone.

Scope (Phase 1 only — see Improvements/Position_Scaling/implementation_prompt.md):
  - Pure position-state simulation: given a price path and a sequence of tranche/exit
    decisions, compute the resulting cost basis, P&L, and barrier-hit outcomes correctly.
  - Does NOT decide WHEN to add a tranche — that is the position-scaling gate's job
    (Phase 2+, not built yet). This module answers "if a tranche were added here, what
    would the position's state become and when would it exit," nothing more.
  - Does NOT replace gate_harness.py's replay_should_enter() (fresh-entry gate replay,
    already shipped) or engine.py's single-asset DSL backtester (strategy-engine, a
    different product surface for user-authored rules) — this is net-new, additive.
  - Adds ONLY on a pullback (price below the position's current weighted-average cost
    basis) per the user's explicit direction (2026-07-09): the existing scale-in
    mechanism in paper_trading_engine.py (adds on a position already up >=5%) is
    untouched and out of scope here — this module models the opposite, new direction:
    conviction-based averaging into a justified pullback, not adding to a winner.

Slippage: each tranche fills at a slightly worse price than the raw bar close, and later/
smaller tranches are modeled with slightly worse slippage than the first (a human adding
into a falling, less liquid moment typically gets a worse fill than the original, planned
entry) — see _slippage_for_tranche().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Tranche:
    """One entry (the original entry, or a later add) into a logical position."""
    seq: int                    # 0 = original entry, 1 = first add, 2 = second add, ...
    timestamp: datetime
    fill_price: float           # actual filled price, AFTER slippage
    raw_price: float            # the bar close/signal price BEFORE slippage
    shares: float
    reason: str = ""            # human-readable note, e.g. "original entry" / "pullback add #1"


@dataclass
class BarrierConfig:
    """Exit barriers, expressed as multiples of ATR-at-original-entry so they adapt per
    stock rather than using one fixed percentage for every symbol."""
    profit_atr_multiple: float = 2.0
    stop_atr_multiple: float = 1.0
    max_holding_days: int = 20


@dataclass
class MultiTranchePosition:
    """A single logical position that may consist of 1+ tranches.

    Cost basis is a running weighted average, recalculated after every tranche —
    matching the exact accounting already used (and already correct, per
    T234-PT-SCALEIN-COST-BASIS-BUG) by paper_trading_engine.py's existing scale-in path,
    so this simulation stays consistent with the real system's own math.
    """
    symbol: str
    atr_at_entry: float
    barrier_cfg: BarrierConfig
    tranches: list[Tranche] = field(default_factory=list)

    # Set once the position is closed (barrier hit or max_holding_days reached).
    exit_price: float | None = None
    exit_timestamp: datetime | None = None
    exit_reason: str | None = None  # "profit_target" | "stop_loss" | "time_limit"

    @property
    def total_shares(self) -> float:
        return round(sum(t.shares for t in self.tranches), 6)

    @property
    def weighted_avg_cost(self) -> float:
        """Running weighted-average cost basis across all tranches filled so far.

        Matches paper_trading_engine.py:3211-3214's existing scale-in blending formula
        exactly: sum(shares_i * fill_price_i) / total_shares.
        """
        total = self.total_shares
        if total <= 0:
            return 0.0
        return round(sum(t.shares * t.fill_price for t in self.tranches) / total, 6)

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def num_tranches(self) -> int:
        return len(self.tranches)

    def current_barriers(self) -> tuple[float, float]:
        """Return (upper/profit-target price, lower/stop-loss price) against the CURRENT
        weighted-average cost basis — this is the path-dependent piece: a position that
        added a tranche on day 3 has a different cost basis, and therefore different
        barrier prices, than if it had never added.
        """
        basis = self.weighted_avg_cost
        upper = basis + self.barrier_cfg.profit_atr_multiple * self.atr_at_entry
        lower = basis - self.barrier_cfg.stop_atr_multiple * self.atr_at_entry
        return upper, lower

    def unrealized_pnl(self, current_price: float) -> float:
        return round((current_price - self.weighted_avg_cost) * self.total_shares, 4)

    def unrealized_pnl_pct(self, current_price: float) -> float:
        basis = self.weighted_avg_cost
        if basis <= 0:
            return 0.0
        return round((current_price - basis) / basis, 6)


def _slippage_for_tranche(seq: int, base_slippage_pct: float = 0.001) -> float:
    """Later tranches fill worse than the first.

    A human (or algorithm) adding into a falling, less-liquid moment does not get the
    same fill quality as a planned, patient original entry — model this as slippage
    increasing modestly with tranche sequence number rather than treating every add as
    if it filled as cleanly as the first entry.
    """
    return base_slippage_pct * (1.0 + 0.5 * seq)


def open_position(
    symbol: str,
    timestamp: datetime,
    raw_price: float,
    shares: float,
    atr_at_entry: float,
    barrier_cfg: BarrierConfig,
    slippage_pct: float = 0.001,
) -> MultiTranchePosition:
    """Create a new position with its original (tranche 0) entry."""
    fill_price = round(raw_price * (1 + slippage_pct), 4)
    pos = MultiTranchePosition(symbol=symbol, atr_at_entry=atr_at_entry, barrier_cfg=barrier_cfg)
    pos.tranches.append(Tranche(
        seq=0, timestamp=timestamp, fill_price=fill_price, raw_price=raw_price,
        shares=shares, reason="original entry",
    ))
    return pos


def add_tranche(
    pos: MultiTranchePosition,
    timestamp: datetime,
    raw_price: float,
    shares: float,
    reason: str = "",
    base_slippage_pct: float = 0.001,
) -> None:
    """Add a new tranche to an existing open position, mutating it in place.

    Recomputes the weighted-average cost basis implicitly (weighted_avg_cost is a
    property computed from self.tranches, never stored/cached, so there is no separate
    "recalculate" step to forget to call — this is deliberate: a cached, separately-
    updated cost basis is exactly the kind of two-writers bug this module exists to avoid).
    """
    if not pos.is_open:
        raise ValueError(f"Cannot add a tranche to an already-closed position ({pos.symbol})")
    seq = pos.num_tranches
    slippage = _slippage_for_tranche(seq, base_slippage_pct)
    fill_price = round(raw_price * (1 + slippage), 4)
    pos.tranches.append(Tranche(
        seq=seq, timestamp=timestamp, fill_price=fill_price, raw_price=raw_price,
        shares=shares, reason=reason or f"add #{seq}",
    ))


def check_barriers(pos: MultiTranchePosition, bar_high: float, bar_low: float, bar_close: float,
                    bar_timestamp: datetime) -> str | None:
    """Check whether this bar triggers profit-target, stop-loss, or (caller-checked)
    time-limit against the position's CURRENT (path-dependent) cost basis.

    Returns the exit_reason string if a barrier was hit this bar, else None. Does not
    mutate pos — caller is responsible for calling close_position() with the result,
    keeping this function a pure check (easier to unit test in isolation).
    """
    if not pos.is_open:
        return None
    upper, lower = pos.current_barriers()
    if bar_high >= upper:
        return "profit_target"
    if bar_low <= lower:
        return "stop_loss"
    return None


def close_position(pos: MultiTranchePosition, exit_price: float, exit_timestamp: datetime,
                    exit_reason: str) -> None:
    """Mark a position closed. exit_price should be the barrier price for profit_target/
    stop_loss (matching real fill assumptions), or the bar close for time_limit exits.
    """
    pos.exit_price = exit_price
    pos.exit_timestamp = exit_timestamp
    pos.exit_reason = exit_reason


def realized_pnl(pos: MultiTranchePosition) -> float | None:
    if pos.exit_price is None:
        return None
    return round((pos.exit_price - pos.weighted_avg_cost) * pos.total_shares, 4)


def realized_pnl_pct(pos: MultiTranchePosition) -> float | None:
    if pos.exit_price is None:
        return None
    basis = pos.weighted_avg_cost
    if basis <= 0:
        return None
    return round((pos.exit_price - basis) / basis, 6)

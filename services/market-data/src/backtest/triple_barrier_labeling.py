"""T241-POSITION-SCALING Phase 2: triple-barrier labeling for position-scaling gate training.

For every historical point where a pullback-add COULD have happened (not just where one
did), this determines what the CORRECT action would have been in hindsight — add, or don't
add — by simulating three exit barriers (profit target, stop-loss, max holding time) against
the multi-tranche engine from Phase 1, and seeing which one triggers first.

This is the ground-truth generator the position-scaling gate (Phase 3, not built yet) trains
against. Adapted from the reference triple_barrier_labeling.py in
Improvements/Position_Scaling/AI_Investment_Position_Scaling_Architecture.pdf Appendix A, with
these adaptations to this codebase (see the module docstring in multi_tranche_engine.py for the
full context):

  - Barriers are simulated via Phase 1's MultiTranchePosition/check_barriers/close_position
    rather than a standalone walk-forward loop, so the SAME cost-basis math backs both the
    labeling pipeline and (eventually) live position scaling — one implementation, not two
    that could silently drift apart.
  - "Was adding correct?" is evaluated by comparing two parallel simulations from the same
    candidate-add point forward: (a) WITH the candidate add applied, (b) WITHOUT it (holding
    the pre-existing tranches only). The add is labeled correct only if it produced a better
    realized outcome than not adding — not merely "did the position eventually profit,"
    which would also reward adds that helped less than doing nothing.
  - Reference: Lopez de Prado, "Advances in Financial Machine Learning", ch. 3 (triple-barrier
    method), extended here to the multi-tranche/scaling case per the architecture doc's
    section 5.1.

No look-ahead: every labeling call takes a price_path already sliced to start at or after the
candidate event's timestamp — see label_single_event()'s docstring. Do not pass a price_path
that includes bars before the event; there is no internal guard against this because the
caller (build_labeled_dataset()) is responsible for the slice, matching gate_harness.py's
existing _fetch_matched_signals() discipline of pre-filtering before this module ever sees data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

import pandas as pd

from .multi_tranche_engine import (
    BarrierConfig,
    MultiTranchePosition,
    add_tranche,
    check_barriers,
    close_position,
    open_position,
    realized_pnl_pct,
)


class BarrierOutcome(Enum):
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    TIME_LIMIT = "time_limit"


# T241-P2-THRESHOLD: a time-limit exit is only labeled "correct" if the realized return
# exceeded this threshold — avoids rewarding flat, capital-tying trades that merely survived
# to the time limit without actually going anywhere. Stop-losses are always labeled incorrect
# (by construction: the position lost money, whether or not adding was "conceptually right").
# Tune per asset class if HK vs US names show materially different flat-trade rates.
_TIME_LIMIT_CORRECT_THRESHOLD = 0.005


@dataclass
class LabeledEvent:
    event_timestamp: pd.Timestamp
    symbol: str
    entry_price: float          # pre-existing position's cost basis at the candidate-add point
    atr_at_event: float
    outcome_with_add: BarrierOutcome
    outcome_without_add: BarrierOutcome
    exit_price_with_add: float
    exit_price_without_add: float
    exit_timestamp: pd.Timestamp
    holding_days: int
    realized_return_with_add: float
    realized_return_without_add: float
    # The actual training label: was adding size at this event net positive versus holding?
    label_add_was_correct: bool


def _simulate_from_snapshot(
    symbol: str,
    existing_tranches: list[tuple[pd.Timestamp, float, float]],  # (timestamp, fill_price, shares)
    price_path: pd.DataFrame,  # columns: ['timestamp', 'high', 'low', 'close'], from event forward
    atr_at_event: float,
    config: BarrierConfig,
    candidate_add: tuple[pd.Timestamp, float, float] | None,  # (timestamp, raw_price, shares) or None
) -> tuple[BarrierOutcome, float, pd.Timestamp, int, float]:
    """Rebuild a MultiTranchePosition from existing_tranches (+ optionally one candidate add),
    then walk price_path forward until a barrier triggers. Returns
    (outcome, exit_price, exit_timestamp, holding_days, realized_return).

    Rebuilding from raw tranche data each call (rather than deep-copying a live position
    object) keeps the "with add" and "without add" simulations fully independent — no shared
    mutable state between the two branches that could let one accidentally affect the other.
    """
    t0_ts, t0_price, t0_shares = existing_tranches[0]
    pos = open_position(symbol, t0_ts, raw_price=t0_price, shares=t0_shares,
                        atr_at_entry=atr_at_event, barrier_cfg=config, slippage_pct=0.0)
    for ts, price, shares in existing_tranches[1:]:
        add_tranche(pos, ts, raw_price=price, shares=shares, base_slippage_pct=0.0)
    if candidate_add is not None:
        add_ts, add_price, add_shares = candidate_add
        add_tranche(pos, add_ts, raw_price=add_price, shares=add_shares, base_slippage_pct=0.0)

    max_date = price_path["timestamp"].iloc[0] + timedelta(days=config.max_holding_days)
    window = price_path[price_path["timestamp"] <= max_date]

    outcome = None
    exit_price = None
    exit_ts = None
    for _, row in window.iterrows():
        reason = check_barriers(pos, bar_high=row["high"], bar_low=row["low"],
                                 bar_close=row["close"], bar_timestamp=row["timestamp"])
        if reason == "profit_target":
            outcome = BarrierOutcome.PROFIT_TARGET
            upper, _ = pos.current_barriers()
            exit_price = upper
            exit_ts = row["timestamp"]
            break
        if reason == "stop_loss":
            outcome = BarrierOutcome.STOP_LOSS
            _, lower = pos.current_barriers()
            exit_price = lower
            exit_ts = row["timestamp"]
            break

    if outcome is None:
        # Time barrier hit — exit at the last available close in the window.
        outcome = BarrierOutcome.TIME_LIMIT
        exit_price = float(window["close"].iloc[-1])
        exit_ts = window["timestamp"].iloc[-1]

    close_position(pos, exit_price=exit_price, exit_timestamp=exit_ts, exit_reason=outcome.value)
    holding_days = (exit_ts - window["timestamp"].iloc[0]).days
    ret = realized_pnl_pct(pos) or 0.0
    return outcome, exit_price, exit_ts, holding_days, ret


def label_single_event(
    symbol: str,
    existing_tranches: list[tuple[pd.Timestamp, float, float]],
    price_path: pd.DataFrame,
    atr_at_event: float,
    candidate_add_price: float,
    candidate_add_shares: float,
    config: BarrierConfig,
) -> LabeledEvent:
    """Label one historical candidate-add event: was adding size here, versus holding the
    existing position unchanged, the better choice in hindsight?

    price_path must already be sliced to start at (or just after) the event — see the
    no-look-ahead note in the module docstring.
    """
    event_ts = price_path["timestamp"].iloc[0]

    outcome_with, exit_price_with, exit_ts_with, days_with, ret_with = _simulate_from_snapshot(
        symbol, existing_tranches, price_path, atr_at_event, config,
        candidate_add=(event_ts, candidate_add_price, candidate_add_shares),
    )
    outcome_without, exit_price_without, exit_ts_without, days_without, ret_without = _simulate_from_snapshot(
        symbol, existing_tranches, price_path, atr_at_event, config,
        candidate_add=None,
    )

    # T241-P2-LABEL: "correct" means the add produced a genuinely better realized outcome
    # than not adding — not merely "the with-add position was profitable," which would also
    # reward adds that helped less than simply holding would have. A profit-target exit is
    # unambiguously good on its own terms, but still only counts as a correct ADD decision if
    # it beat the without-add counterfactual; a stop-loss is always wrong; a time-limit exit
    # needs the realized return to clear a minimal threshold, matching Appendix A's rationale
    # for not rewarding flat, capital-tying trades.
    if outcome_with == BarrierOutcome.STOP_LOSS:
        label = False
    elif outcome_with == BarrierOutcome.PROFIT_TARGET:
        label = ret_with > ret_without
    else:  # TIME_LIMIT
        label = ret_with > _TIME_LIMIT_CORRECT_THRESHOLD and ret_with > ret_without

    return LabeledEvent(
        event_timestamp=event_ts,
        symbol=symbol,
        entry_price=existing_tranches[-1][1],
        atr_at_event=atr_at_event,
        outcome_with_add=outcome_with,
        outcome_without_add=outcome_without,
        exit_price_with_add=exit_price_with,
        exit_price_without_add=exit_price_without,
        exit_timestamp=exit_ts_with,
        holding_days=days_with,
        realized_return_with_add=ret_with,
        realized_return_without_add=ret_without,
        label_add_was_correct=label,
    )


def build_labeled_dataset(
    candidate_events: pd.DataFrame,
    # columns: symbol, event_timestamp, atr_at_event, candidate_add_price, candidate_add_shares,
    # existing_tranches (list[tuple[timestamp, price, shares]] per row)
    price_history: dict[str, pd.DataFrame],  # symbol -> OHLC dataframe, columns ['timestamp','high','low','close']
    config: BarrierConfig,
) -> pd.DataFrame:
    """Batch version: label every historical candidate-add event in the input set.

    This is the function a training pipeline (Phase 3) calls to produce (X, y) pairs — y comes
    from label_add_was_correct, X comes from the position-scaling gate's feature schema
    (not built yet — Phase 3).
    """
    labeled: list[LabeledEvent] = []
    for _, event in candidate_events.iterrows():
        symbol_prices = price_history.get(event["symbol"])
        if symbol_prices is None:
            continue
        path = symbol_prices[symbol_prices["timestamp"] >= event["event_timestamp"]]
        if path.empty:
            continue
        result = label_single_event(
            symbol=event["symbol"],
            existing_tranches=event["existing_tranches"],
            price_path=path,
            atr_at_event=event["atr_at_event"],
            candidate_add_price=event["candidate_add_price"],
            candidate_add_shares=event["candidate_add_shares"],
            config=config,
        )
        labeled.append(result)

    return pd.DataFrame([vars(r) for r in labeled])


def label_balance_report(labeled: pd.DataFrame) -> dict:
    """Sanity-check report: label balance (% add-correct vs not) and outcome distribution.

    Per the design doc's Phase 2 acceptance criteria — flag if any outcome bucket is wildly
    overrepresented before trusting the dataset for training.
    """
    if labeled.empty:
        return {"n_events": 0}
    n = len(labeled)
    return {
        "n_events": n,
        "pct_add_correct": round(float(labeled["label_add_was_correct"].mean()) * 100, 2),
        "outcome_with_add_counts": labeled["outcome_with_add"].value_counts().to_dict(),
        "outcome_without_add_counts": labeled["outcome_without_add"].value_counts().to_dict(),
        "mean_realized_return_with_add": round(float(labeled["realized_return_with_add"].mean()), 4),
        "mean_realized_return_without_add": round(float(labeled["realized_return_without_add"].mean()), 4),
    }

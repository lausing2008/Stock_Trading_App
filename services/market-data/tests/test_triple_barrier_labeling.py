"""T241-POSITION-SCALING Phase 2: triple-barrier labeling tests.

Per Improvements/Position_Scaling/implementation_prompt.md Phase 2 acceptance criteria:
"at least 3 spot-checked examples where you manually agree with the label given the price path."
Each test below states the hand-reasoned expected label BEFORE asserting it, so the reasoning
is checkable independent of the code under test.
"""
import pandas as pd

from src.backtest.multi_tranche_engine import BarrierConfig
from src.backtest.triple_barrier_labeling import (
    BarrierOutcome,
    build_labeled_dataset,
    label_balance_report,
    label_single_event,
)

_T0 = pd.Timestamp("2026-01-01")


def _path(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """rows: list of (date_str, high, low, close)."""
    return pd.DataFrame([
        {"timestamp": pd.Timestamp(d), "high": h, "low": lo, "close": c}
        for d, h, lo, c in rows
    ])


def test_spot_check_1_add_clears_profit_target_add_was_correct():
    """Hand reasoning: existing tranche at 100. Candidate add at 90 (a real pullback).
    Blended basis after the add = (10*100 + 10*90)/20 = 95. Barriers: profit=95+2*2=99,
    stop=95-1*2=93. WITHOUT the add, basis stays 100: profit=104, stop=98.

    Day 1 (the event bar itself, low=90.5/high=91) must stay inside BOTH barrier sets —
    checked by hand: 90.5 > 93's-lower-bound? No — 90.5 < 93, which would falsely stop out
    the with-add position on bar 0. Corrected: day 1 low=94 (between with-add stop 93 and
    without-add stop 98 — i.e. this bar alone would stop out the WITHOUT-add position at
    98 while leaving the with-add position, whose lower/safer blended basis gives it more
    room, still open). Day 2 then rallies to 100, clearing the with-add profit target (99).
    Expected: label_add_was_correct = True (with-add reaches a real profit exit; without-add
    would have been stopped out on day 1 at a loss).
    """
    cfg = BarrierConfig(profit_atr_multiple=2.0, stop_atr_multiple=1.0, max_holding_days=20)
    path = _path([
        ("2026-01-01", 96, 94, 95),    # low=94: below without-add stop (98) but above with-add stop (93)
        ("2026-01-02", 100, 95, 99),   # with-add basis 95: high=100 clears with-add profit target (99)
    ])
    result = label_single_event(
        symbol="TEST",
        existing_tranches=[(_T0, 100.0, 10.0)],
        price_path=path,
        atr_at_event=2.0,
        candidate_add_price=90.0,
        candidate_add_shares=10.0,
        config=cfg,
    )
    assert result.outcome_without_add == BarrierOutcome.STOP_LOSS  # stopped out on day 1 at 98
    assert result.outcome_with_add == BarrierOutcome.PROFIT_TARGET  # survives day 1, wins on day 2
    assert result.realized_return_with_add > result.realized_return_without_add
    assert result.label_add_was_correct is True


def test_spot_check_2_add_gets_stopped_out_add_was_incorrect():
    """Hand reasoning: existing tranche at 100. Candidate add at 90. Blended basis = 95,
    stop = 95 - 1*2 = 93. Price keeps falling to 92 on day 2 — hits the stop. A stop-loss
    exit is ALWAYS labeled incorrect by construction (the position lost money), regardless
    of what the without-add counterfactual did. Expected: label_add_was_correct = False.
    """
    cfg = BarrierConfig(profit_atr_multiple=2.0, stop_atr_multiple=1.0, max_holding_days=20)
    path = _path([
        ("2026-01-01", 91, 89, 90),
        ("2026-01-02", 93, 91, 92),  # low=91 breaches stop=93
    ])
    result = label_single_event(
        symbol="TEST",
        existing_tranches=[(_T0, 100.0, 10.0)],
        price_path=path,
        atr_at_event=2.0,
        candidate_add_price=90.0,
        candidate_add_shares=10.0,
        config=cfg,
    )
    assert result.outcome_with_add == BarrierOutcome.STOP_LOSS
    assert result.label_add_was_correct is False


def test_spot_check_3_flat_time_limit_exit_below_threshold_is_incorrect():
    """Hand reasoning: existing tranche at 100 (10sh). Candidate add at 98 (10sh) — a very
    shallow, barely-a-pullback add. Blended with-add basis = (10*100+10*98)/20 = 99.0.
    Barriers (ATR=2, profit_atr=2, stop_atr=1): profit=99+4=103, stop=99-2=97. Price drifts
    in a narrow 98.0-98.7 band for 5 days — never reaches either barrier (all highs stay
    below 103, all lows stay above 97) — a time-limit exit at the last close (98.4).
    Realized return = (98.4-99.0)/99.0 = -0.00606 (a small LOSS, magnitude just over the
    0.5% "worth it" threshold but negative, not flat-positive) — either way this must not be
    labeled correct, since the threshold check requires ret_with > +0.005, and a negative
    return fails that regardless of magnitude. Expected: label_add_was_correct = False.
    """
    cfg = BarrierConfig(profit_atr_multiple=2.0, stop_atr_multiple=1.0, max_holding_days=5)
    path = _path([
        ("2026-01-01", 98.5, 97.8, 98.2),
        ("2026-01-02", 98.6, 98.0, 98.3),
        ("2026-01-03", 98.7, 98.1, 98.4),
        ("2026-01-04", 98.6, 98.0, 98.3),
        ("2026-01-05", 98.7, 98.1, 98.4),
    ])
    result = label_single_event(
        symbol="TEST",
        existing_tranches=[(_T0, 100.0, 10.0)],
        price_path=path,
        atr_at_event=2.0,
        candidate_add_price=98.0,
        candidate_add_shares=10.0,
        config=cfg,
    )
    assert result.outcome_with_add == BarrierOutcome.TIME_LIMIT
    assert result.realized_return_with_add < 0  # a small loss, not flat-positive
    assert result.label_add_was_correct is False


def test_build_labeled_dataset_batch_and_balance_report():
    cfg = BarrierConfig(profit_atr_multiple=2.0, stop_atr_multiple=1.0, max_holding_days=20)
    events = pd.DataFrame([
        {
            "symbol": "AAA", "event_timestamp": _T0, "atr_at_event": 2.0,
            "candidate_add_price": 90.0, "candidate_add_shares": 10.0,
            "existing_tranches": [(_T0, 100.0, 10.0)],
        },
        {
            "symbol": "AAA", "event_timestamp": pd.Timestamp("2026-01-03"), "atr_at_event": 2.0,
            "candidate_add_price": 90.0, "candidate_add_shares": 10.0,
            "existing_tranches": [(_T0, 100.0, 10.0)],
        },
    ])
    price_history = {
        "AAA": _path([
            ("2026-01-01", 91, 89, 90),
            ("2026-01-02", 100, 95, 99),
            ("2026-01-03", 100, 95, 99),
            ("2026-01-04", 100, 95, 99),
        ]),
    }
    labeled = build_labeled_dataset(events, price_history, cfg)
    assert len(labeled) == 2

    report = label_balance_report(labeled)
    assert report["n_events"] == 2
    assert "pct_add_correct" in report
    assert "outcome_with_add_counts" in report


def test_build_labeled_dataset_skips_symbol_with_no_price_history():
    cfg = BarrierConfig()
    events = pd.DataFrame([
        {
            "symbol": "MISSING", "event_timestamp": _T0, "atr_at_event": 2.0,
            "candidate_add_price": 90.0, "candidate_add_shares": 10.0,
            "existing_tranches": [(_T0, 100.0, 10.0)],
        },
    ])
    labeled = build_labeled_dataset(events, price_history={}, config=cfg)
    assert labeled.empty

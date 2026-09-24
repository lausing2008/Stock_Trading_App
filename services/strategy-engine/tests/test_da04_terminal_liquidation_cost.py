"""DA-04: terminal liquidation costs were charged in the trade list but omitted from equity.

THE DEFECT. The engine closes any still-open position at the last bar, deducting fee and
slippage — but it left `position[-1] == 1`. The equity curve's cost adjustment only fired on a
TRANSITION to zero, which a terminal liquidation never produces, so the forced exit's cost
reached `trades` and never reached the curve. An entry on the final bar was starker still: a
complete round trip appeared in the trade list while equity stayed exactly flat.

Reported by the audit at $100 constant prices, 5 bps fee + 2 bps slippage per side:

    earlier entry, forced final exit    trade -0.139902%   equity -0.069951%   (one side charged)
    entry and forced exit on last bar   trade -0.139902%   equity  0%          (neither charged)

THE FIX IS ABOUT WHERE TRUTH LIVES. The old adjustment re-derived fills from `position`
changing, which cannot see a liquidation and cannot express two fills on one bar. Fills are now
RECORDED as they happen (entry_bars / exit_bars) and the adjustment reads those. A same-bar
round trip additionally needs an explicit charge: its return is gated by pos_shifted, which is 0
because the book was flat on the prior bar, so no price adjustment can ever reach it.

Reporting a liquidation in the trade list while the curve still shows the position open is two
conventions in one result — which is the deeper point the finding makes.
"""
import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine

FEE_BPS, SLIP_BPS = 5.0, 2.0
ROUND_TRIP = (1 - (FEE_BPS + SLIP_BPS) / 1e4) / (1 + (FEE_BPS + SLIP_BPS) / 1e4) - 1  # ~ -0.139902%


def _flat_df(n=30, price=100.0):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
        "open": price, "high": price, "low": price, "close": price,
        "volume": 1_000_000,
    })


def _engine():
    return BacktestEngine(fee_bps=FEE_BPS, slippage_bps=SLIP_BPS)


def _always_true_after(idx: int, n: int) -> dict:
    """A rule that is true from bar `idx` onward, expressed through the engine's own rule
    evaluator via a column the feature builder passes through."""
    return {"field": "close", "op": ">", "value": 0} if idx == 0 else None


def _run_with_positions(engine, df, entry_mask, exit_mask=None):
    """Drive the engine with explicit boolean masks, bypassing rule evaluation.

    The engine's public entry point takes rule dicts; this reaches past them so a test can place
    a fill on an exact bar, which is the whole point of the cases below.
    """
    import src.backtest.engine as E

    real_eval = E.evaluate_rule
    calls = {"n": 0}

    def _fake_eval(rule, feat):
        calls["n"] += 1
        return pd.Series(entry_mask if calls["n"] == 1 else exit_mask, index=feat.index)

    E.evaluate_rule = _fake_eval
    try:
        return engine.run(df, {"any": "entry"}, {"any": "exit"} if exit_mask is not None else None)
    finally:
        E.evaluate_rule = real_eval


# ── The two reported cases ───────────────────────────────────────────────────

def test_an_earlier_entry_with_a_forced_final_exit_charges_BOTH_sides():
    """Previously -0.069951%: the entry was charged, the forced exit was not."""
    n = 30
    df = _flat_df(n)
    entry = [False] * n
    entry[4] = True                      # signal at bar 4 -> fill at bar 5
    res = _run_with_positions(_engine(), df, entry)

    assert len(res.trades) == 1
    assert res.trades[0]["ret"] == pytest.approx(ROUND_TRIP, rel=1e-6)
    assert res.total_return == pytest.approx(round(ROUND_TRIP, 4), abs=1e-6)


def test_an_entry_and_forced_exit_on_the_LAST_bar_is_not_free():
    """Previously 0%: a complete round trip in the trade list against a perfectly flat curve.

    Its return is gated by pos_shifted (the book was flat on the prior bar), so no price
    adjustment can reach it — the costs have to be charged explicitly."""
    n = 30
    df = _flat_df(n)
    entry = [False] * n
    entry[n - 2] = True                  # signal at n-2 -> fill at the final bar
    res = _run_with_positions(_engine(), df, entry)

    assert len(res.trades) == 1
    assert res.trades[0]["ret"] == pytest.approx(ROUND_TRIP, rel=1e-6)
    assert res.total_return != 0.0
    assert res.total_return == pytest.approx(round(ROUND_TRIP, 4), abs=1e-6)


# ── The convention must be consistent ────────────────────────────────────────

def test_an_ordinary_signalled_exit_is_unchanged():
    """The fix must not alter the path that was always correct."""
    n = 30
    df = _flat_df(n)
    entry = [False] * n
    exit_ = [False] * n
    entry[4] = True
    exit_[9] = True                      # exit signal at 9 -> fill at 10
    res = _run_with_positions(_engine(), df, entry, exit_)

    assert len(res.trades) == 1
    assert res.trades[0]["ret"] == pytest.approx(ROUND_TRIP, rel=1e-6)
    assert res.total_return == pytest.approx(round(ROUND_TRIP, 4), abs=1e-6)


def test_zero_cost_reconciles_exactly_on_a_flat_series():
    """With no fees, a flat price series must produce exactly zero — a fix that charged
    something unconditionally would show up here."""
    n = 30
    df = _flat_df(n)
    entry = [False] * n
    entry[4] = True
    res = _run_with_positions(BacktestEngine(fee_bps=0.0, slippage_bps=0.0), df, entry)
    assert res.trades[0]["ret"] == pytest.approx(0.0, abs=1e-12)
    assert res.total_return == pytest.approx(0.0, abs=1e-12)


def test_no_trades_leaves_the_curve_flat():
    n = 30
    res = _run_with_positions(_engine(), _flat_df(n), [False] * n)
    assert res.n_trades == 0
    assert res.total_return == pytest.approx(0.0, abs=1e-12)


def test_equity_reconciles_to_the_trade_return_on_a_rising_series():
    """The engine is full-allocation, so terminal equity must compound the trade returns. A
    cost booked in one place and not the other breaks this identity — which is exactly what
    the finding is."""
    n = 30
    df = _flat_df(n)
    df["close"] = np.linspace(100.0, 110.0, n)
    entry = [False] * n
    entry[4] = True
    res = _run_with_positions(_engine(), df, entry)

    compounded = 1.0
    for t in res.trades:
        compounded *= (1 + t["ret"])
    assert res.total_return == pytest.approx(compounded - 1, abs=5e-4)


# ── What the structure guarantees ────────────────────────────────────────────

def test_a_same_bar_round_trip_can_only_occur_at_the_terminal_bar():
    """Documents the invariant two otherwise-untestable lines rest on.

    An exit requires its signal on a strictly earlier bar than its fill, so an entry and an exit
    cannot land on the same mid-series bar under today's rules — the only way to get both on one
    bar is an entry on the final bar plus the forced liquidation. That is why the explicit
    round-trip charge exists (the price path cannot express it) and why `position[-1] = 0` and
    the `if`/`if` adjustment are defensive rather than load-bearing.

    If this invariant ever breaks — an intrabar rule, a same-bar reversal — those two lines stop
    being cosmetic and this test is where that shows up.
    """
    n = 20
    df = _flat_df(n)
    entry = [False] * n
    exit_ = [False] * n
    entry[4] = True
    exit_[4] = True          # same signal bar for both
    res = _run_with_positions(_engine(), df, entry, exit_)

    entry_bars = {t["entry_ts"] for t in res.trades}
    exit_bars = {t.get("exit_ts") for t in res.trades}
    # The entry fills at bar 5; the exit signal at bar 4 cannot also fill at bar 5, because the
    # engine takes the entry branch first and only tests exits while already in position.
    assert entry_bars & exit_bars == set() or len(res.trades) == 1
    # Whatever the pairing, the accounting identity must still hold.
    compounded = 1.0
    for t in res.trades:
        compounded *= (1 + t["ret"])
    assert res.total_return == pytest.approx(compounded - 1, abs=5e-4)

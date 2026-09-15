"""T388-ENGINE-COSTS — fee/slippage and look-ahead tests the engine never had.

FROM THE 2025-08-22 BACKTESTING FRAMEWORK AUDIT, verified still true on 2026-09-15:

    Missing Tests:
    - No tests for fee/slippage calculation correctness
    - No tests for look-ahead bias prevention

Grepping the whole test directory for "fee", "slippage", "look-ahead", "lookahead", "1-bar" or
"next-bar" returned **zero** matching files. Those two properties are the engine's most
load-bearing correctness guarantees — a look-ahead bug makes every backtest optimistic and
undetectable, and a fee/slippage error silently changes every reported return — and neither
had a single test.

(The audit's other "missing test" claims had gone STALE: `test_dsl.py` and
`test_atr_consolidation.py` were added after it was written, so NaN handling and DSL evaluation
ARE covered. Only these two gaps were real.)

THESE ARE BEHAVIOURAL TESTS, not source-text assertions. Each drives the real `BacktestEngine`
with synthetic prices chosen so the correct answer is computable BY HAND, then asserts the
engine reproduces it. A source-text test would pass against a re-implementation that had the
arithmetic backwards; this repo has been bitten by exactly that (a function pinned only by a
COPY of itself is not pinned).
"""
import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine


def _bars(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    """OHLCV frame with a real DatetimeIndex column, flat OHLC around each close."""
    ts = pd.date_range(start, periods=len(closes), freq="D")
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "ts": ts, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
        "volume": [1_000_000] * len(closes),
    })


# A rule that is TRUE on a given bar index — lets a test place a signal exactly.
def _rule_true_when_close_above(x: float) -> dict:
    return {"op": ">", "left": "close", "right": x}


def _rule_never() -> dict:
    return {"op": ">", "left": "close", "right": 1e12}


# ── Fee / slippage arithmetic ───────────────────────────────────────────────────────────

def test_entry_pays_up_and_exit_receives_less():
    """THE DIRECTION OF THE COST. A sign error here would make trading FREE — or profitable by
    construction — and every reported return would be wrong in the flattering direction."""
    eng = BacktestEngine(fee_bps=5.0, slippage_bps=2.0)
    # 40 flat bars at 100 so indicators warm up, then a step to trigger entry, then exit.
    closes = [100.0] * 40 + [110.0] * 5 + [100.0] * 5
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), {"op": "<", "left": "close", "right": 105})
    assert res.n_trades >= 1
    t = res.trades[0]
    # cost = slippage + fee = 2bps + 5bps = 7bps
    assert t["entry"] == pytest.approx(110.0 * 1.0007, rel=1e-9), "entry must pay ABOVE the close"
    assert t["exit"] == pytest.approx(100.0 * 0.9993, rel=1e-9), "exit must receive BELOW the close"


def test_a_round_trip_at_a_flat_price_loses_exactly_the_cost():
    """The cleanest possible check: buy and sell at the SAME price, so the entire P&L is the
    round-trip cost. 7bps each way compounds to 1 - 0.9993/1.0007."""
    eng = BacktestEngine(fee_bps=5.0, slippage_bps=2.0)
    closes = [100.0] * 40 + [110.0, 110.0] + [110.0] * 5
    # enter above 105, exit immediately on the next bar via an always-true exit
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_true_when_close_above(105))
    t = res.trades[0]
    expected = (110.0 * 0.9993) / (110.0 * 1.0007) - 1
    assert t["ret"] == pytest.approx(expected, rel=1e-9)
    assert t["ret"] < 0, "a flat round trip must LOSE money — it pays the spread twice"


def test_zero_cost_config_is_frictionless():
    """Pins that the cost is actually driven by the constructor args, not hardcoded."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    closes = [100.0] * 40 + [110.0] * 5
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_never())
    t = res.trades[0]
    assert t["entry"] == pytest.approx(110.0, rel=1e-12), "no cost => fill AT the close"


@pytest.mark.parametrize("fee,slip", [(5.0, 2.0), (10.0, 5.0), (0.0, 30.0), (25.0, 0.0)])
def test_cost_scales_with_the_configured_bps(fee, slip):
    """A higher configured cost must produce a strictly worse round trip — guards against the
    bps conversion (x/1e4) being wrong by an order of magnitude."""
    closes = [100.0] * 40 + [110.0, 110.0] + [110.0] * 3
    res = BacktestEngine(fee_bps=fee, slippage_bps=slip).run(
        _bars(closes), _rule_true_when_close_above(105), _rule_true_when_close_above(105))
    mult = (fee + slip) / 1e4
    assert res.trades[0]["entry"] == pytest.approx(110.0 * (1 + mult), rel=1e-9)


def test_bps_are_not_confused_with_percent():
    """5 bps is 0.05%, NOT 5%. An off-by-100 here would make every backtest catastrophically
    wrong while still 'working'."""
    eng = BacktestEngine(fee_bps=5.0, slippage_bps=2.0)
    closes = [100.0] * 40 + [110.0] * 5
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_never())
    entry = res.trades[0]["entry"]
    # 7bps of 110 is 0.077. MY FIRST BOUND HERE WAS WRONG (110.01, which is 0.9bps) and the
    # engine correctly failed it — the arithmetic in the assertion was the bug, not the code.
    assert entry - 110.0 == pytest.approx(0.077, abs=1e-6)
    # The real guard: 7 BPS, not 7 PERCENT. An off-by-100 would put the fill at 117.7.
    assert entry < 110.1, "a percent/bps confusion would land near 117.70"


def test_the_equity_curve_also_pays_the_cost():
    """The trade list and the equity curve are computed SEPARATELY (adj_close), so a fix to one
    can silently miss the other. A flat-price round trip must show equity BELOW 1.0."""
    eng = BacktestEngine(fee_bps=5.0, slippage_bps=2.0)
    closes = [100.0] * 40 + [110.0, 110.0, 110.0] + [110.0] * 3
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_true_when_close_above(105))
    assert res.total_return < 0, "equity must reflect fee drag, not raw closes"


# ── Look-ahead prevention ───────────────────────────────────────────────────────────────

def test_a_signal_fills_on_the_NEXT_bar_not_its_own():
    """THE CORE GUARANTEE. The signal is read at bar i-1 and filled at bar i. Filling on the
    signal bar itself would let the backtest trade on information it could not have had, which
    makes every result optimistic and is invisible in the output."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    # bar 40 is the first close above 105; the fill must therefore happen at bar 41's close.
    closes = [100.0] * 40 + [110.0, 120.0] + [120.0] * 3
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_never())
    assert res.trades[0]["entry"] == pytest.approx(120.0, rel=1e-12), (
        "filled at 110 => same-bar look-ahead; 120 is the next bar's close"
    )


def test_a_perfect_foresight_rule_cannot_capture_the_spike_bar():
    """A rule that fires exactly on a one-bar spike must NOT earn the spike — the fill lands on
    the bar after, by which time the price has already collapsed."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    closes = [100.0] * 40 + [500.0] + [100.0] * 5     # one-bar spike at index 40
    res = eng.run(_bars(closes), _rule_true_when_close_above(400), _rule_never())
    assert res.trades[0]["entry"] == pytest.approx(100.0, rel=1e-12), (
        "entering at 500 would mean trading on the spike bar's own close"
    )
    assert res.total_return <= 0.0, "foresight on a spike must not be profitable"


def test_the_equity_curve_excludes_the_fill_bars_own_return():
    """`position.shift(1)` means the first return counted is fill-close -> next-close. Counting
    the fill bar's own return would double-count the move that triggered the signal."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    closes = [100.0] * 40 + [110.0, 110.0, 121.0] + [121.0] * 2
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_never())
    # entry fills at bar 41 (110). The +10% move to 121 at bar 42 IS captured.
    assert res.total_return == pytest.approx(0.10, rel=1e-6)


def test_an_exit_signal_also_fills_on_the_next_bar():
    """The lag must apply symmetrically — an exit that filled on its own signal bar would let
    the backtest dodge a drop it could not have seen."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    closes = [100.0] * 40 + [110.0, 110.0, 90.0, 80.0] + [80.0] * 2
    res = eng.run(_bars(closes), _rule_true_when_close_above(105),
                  {"op": "<", "left": "close", "right": 100})
    t = res.trades[0]
    # exit signal first true at bar 42 (close 90) -> fill at bar 43 (close 80)
    assert t["exit"] == pytest.approx(80.0, rel=1e-12), (
        "exiting at 90 would mean acting on the exit bar's own close"
    )


def test_no_trade_is_opened_on_the_very_first_bar():
    """The loop starts at i=1 precisely because bar 0 has no prior bar to read a signal from.
    An entry at index 0 would be a signal from nowhere."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    closes = [110.0] * 45                       # rule is true on EVERY bar, including bar 0
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_never())
    assert res.trades[0]["entry_ts"] != str(_bars(closes)["ts"].iloc[0])


# ── Position lifecycle ──────────────────────────────────────────────────────────────────

def test_an_open_position_is_closed_at_the_last_bar():
    """An unclosed position would leave `ret` missing on the final trade and understate n_trades
    against the equity curve."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    closes = [100.0] * 40 + [110.0] * 5
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_never())
    assert "exit" in res.trades[-1] and "ret" in res.trades[-1]


def test_a_second_entry_does_not_open_while_already_in_position():
    """Single-asset, long-only: the engine must not stack positions."""
    eng = BacktestEngine(fee_bps=0.0, slippage_bps=0.0)
    closes = [100.0] * 40 + [110.0] * 10       # entry rule stays true throughout
    res = eng.run(_bars(closes), _rule_true_when_close_above(105), _rule_never())
    assert res.n_trades == 1


def test_no_signal_means_no_trades_and_flat_equity():
    eng = BacktestEngine(fee_bps=5.0, slippage_bps=2.0)
    res = eng.run(_bars([100.0] * 45), _rule_never(), _rule_never())
    assert res.n_trades == 0
    assert res.total_return == pytest.approx(0.0, abs=1e-12), "no trades must cost nothing"

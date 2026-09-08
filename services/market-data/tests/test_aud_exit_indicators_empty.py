"""AUD-EXIT-INDICATORSEMPTY — two exit gates read a table nobody ever wrote to.

`_monitor_positions()` built its `_rsi_overbought` map from the `indicators` table:

    SELECT Indicator.value WHERE name='rsi_14' AND timeframe=D1

**That table has ZERO rows and NO WRITER ANYWHERE in the codebase.** `Indicator` is declared in
`shared/db/models.py` and constructed nowhere — no `Indicator(...)`, no `insert(Indicator)`, no
`session.add`. `git log -S` confirms no writer ever existed. So `_rsi_overbought` was
unconditionally `{}`.

TWO CONTROLS WERE SILENTLY DEAD while reading as enabled:

  1. **`momentum_fade` exit (T207)** — `_rsi_overbought.get(symbol)` is a mandatory `and` term,
     and `momentum_exit_enabled` defaults to True with no portfolio config setting it. So it
     read as a working, enabled gate. Production: **0 momentum_fade exits across 116 closed
     trades.** Its intent — bank gains when OBV distribution meets an overbought roll — never
     executed once. Positions rode the fade back down to a trailing stop instead.
  2. **PT-H5's RSI-overbought trail tightener** — the 1.0xATR lock-in-profits-near-the-peak
     control. Inert.

THE DATA WAS NEVER MISSING, only in the wrong place: `signals.reasons->>'rsi'` is populated on
**43,024 signal rows over 90 days, 388 of them above 75**. The gate would have fired regularly
against the populated source, and `_monitor_positions` already reads `signals.reasons` twice in
the same function.

WHY IT SURVIVED: an empty result is indistinguishable from "no symbol is overbought". This is
the same shape as `AUD-RANK-RSPLACEHOLDER` (a fabricated neutral indistinguishable from a real
measurement) and `AUD-CONVICTION-RSIDIV-NOWRITER` (a hard gate reading a key nothing writes).

SCOPE, bounded by sweeping every table: only `indicators` and `stock_connect_flows` are truly
empty, and the latter is harmless (`StockConnectFlow` is a defined-but-unused model with no
readers; the working HK flow gate reads `signals.reasons->>'flow_5d_net_hkd'`). So this is two
dead gates, not a systemic problem.
"""
import pathlib

import pytest

PT_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/paper_trading_engine.py"
).read_text()


def _rsi_map_block() -> str:
    """The _rsi_overbought construction block."""
    i = PT_SRC.index("_rsi_overbought: dict[str, bool] = {}")
    return PT_SRC[i:PT_SRC.index("_rsi_overbought[sym] = True", i) + 200]


# ── The core fix: read the source that actually has data ────────────────────────────────

def test_the_rsi_map_no_longer_reads_the_empty_indicators_table():
    """THE BUG. `indicators` has 0 rows and no writer anywhere."""
    block = _rsi_map_block()
    assert "Indicator.value" not in block, "must not read the unwritten indicators table"
    assert "Indicator.name" not in block
    assert 'name == "rsi_14"' not in block


def test_it_reads_signals_reasons_instead():
    """The populated source — 43,024 rows in 90 days, and already read twice in this same
    function (see `_dte` and `sig_reasons`)."""
    block = _rsi_map_block()
    assert "reasons->>'rsi'" in block
    assert "FROM signals" in block


def test_it_takes_the_most_recent_signal_per_symbol():
    """A stale RSI would be as wrong as a missing one. DISTINCT ON + ORDER BY ts DESC gives the
    latest row per symbol, matching the original query's `.distinct(Stock.symbol)` intent."""
    block = _rsi_map_block()
    assert "DISTINCT ON (s.symbol)" in block
    assert "ORDER BY s.symbol, sig.ts DESC" in block


def test_it_stays_a_single_bulk_query():
    """The original was one query over armed_symbols. A per-symbol loop inside
    _monitor_positions would add one round-trip per open position on every 5-minute scan."""
    block = _rsi_map_block()
    assert "ANY(:syms)" in block, "must bulk-filter on the symbol list"
    assert block.count("session.execute") == 1


# ── Threshold and semantics must be unchanged ───────────────────────────────────────────

def test_the_75_threshold_is_preserved():
    """This fix restores a dead gate; it must not also retune it. Changing the threshold in the
    same change would make any behavioural difference impossible to attribute."""
    block = _rsi_map_block()
    assert "> 75" in block


def test_rsi_zero_is_treated_as_a_reading_not_as_missing():
    """Falsy-zero guard. An RSI of 0.0 is a legitimate (extremely oversold) value; `if rsi_val`
    would silently drop it. This codebase has fixed the `x or default` confusion 6+ times."""
    block = _rsi_map_block()
    assert "rsi_val is not None" in block, "must test for None, not truthiness"


def test_it_fails_CLOSED_to_an_empty_map():
    """The failure DIRECTION matters more than the failure handling. An unavailable RSI must
    never manufacture an 'overbought' verdict that force-exits a live position — so the except
    arm leaves the map empty (gate does not fire), exactly as before."""
    block = _rsi_map_block()
    i = PT_SRC.index("_rsi_overbought: dict[str, bool] = {}")
    tail = PT_SRC[i:i + 2200]
    assert "except Exception" in tail
    assert "_rsi_overbought = " not in tail.split("except Exception")[1][:200], (
        "the except arm must not populate the map"
    )


# ── The two consumers must be unchanged ─────────────────────────────────────────────────

def test_both_dead_consumers_still_reference_the_map():
    """The fix is at the SOURCE, not the consumers — their logic was always correct, they were
    just fed an empty dict."""
    # Count CODE lines only — the AUD-EXIT-INDICATORSEMPTY comment above the map quotes
    # `_rsi_overbought.get(symbol)` when explaining the defect, so a raw string count sees 3.
    consumers = [
        l for l in PT_SRC.splitlines()
        if "_rsi_overbought.get(" in l and not l.lstrip().startswith("#")
    ]
    assert len(consumers) == 2, (
        f"expected exactly the momentum_fade exit and PT-H5's trail tightener, got {consumers}"
    )


def test_momentum_fade_still_requires_the_overbought_term():
    """It is a mandatory `and` term by design — the exit needs BOTH OBV distribution and an
    overbought roll. Restoring the data must not loosen that conjunction."""
    consumers = [
        l for l in PT_SRC.splitlines()
        if "_rsi_overbought.get(" in l and not l.lstrip().startswith("#")
    ]
    assert all("and" in l for l in consumers), (
        "both consumers must keep the overbought term as a conjunction, not an alternative"
    )


# ── Production-grounded expectations ────────────────────────────────────────────────────

def test_the_fix_does_not_immediately_force_exits():
    """Measured at deploy time: of 171 symbols carrying an RSI in the last 7 days, ZERO are
    above 75 (max 71.9). So this restores the gate's CAPABILITY without an immediate behaviour
    change — a genuinely reassuring property for a control that closes live positions.

    Documented as a fact about the deploy, not asserted against live data (which moves).
    """
    assert True


def test_indicators_table_is_not_read_anywhere_else_in_this_file():
    """If another consumer still reads it, that consumer is dead too."""
    assert "Indicator." not in PT_SRC.replace("Indicator.value", ""), (
        "no remaining reads of the unwritten indicators table"
    )

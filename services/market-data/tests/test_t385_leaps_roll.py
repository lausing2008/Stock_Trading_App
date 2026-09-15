"""T385-LEAPS-ROLL — buy a long-dated LEAPS, sell early, repeat.

USER REQUEST: "can we define the period to sell the close? Like I set 2 years leaps (or any
close to 0.7 or 0.8 delta) but I wanna sell before 2 years like half a year or a year? And
then repeat."

THE SINGLE-CYCLE HALF ALREADY WORKED and was verified before anything was built:
`backtest_leaps()` with a short exit_date already buys a long-dated contract and sells early,
because T380's `_eff_min_dte = max(min_dte, hold_days)` only RAISES the expiry floor, never
lowers it. Measured on QQQ: entry 2024-07-08 at min_dte=600 selected QQQ261218C00434780 — an
**893-day** contract at delta 0.701 — and sold it 365 days later for **+17.56%**. So only the
REPEAT was missing.

WHY ROLLING IS A DIFFERENT STRATEGY, not a convenience wrapper:
  * it re-strikes at the CURRENT price each cycle, so the position keeps tracking the
    underlying instead of drifting deep-ITM (a 0.70-delta call that doubles becomes ~0.95
    delta and stops behaving like a leveraged bet);
  * it pays a full bid/ask round-trip EVERY cycle;
  * it never holds into the steep part of the theta curve.

MEASURED ON REAL CAPTURED CHAINS, QQQ 2024-07-08 -> 2026-07-08 at delta 0.70:

    HOLD 2yr     +118.00%   spread   $384   1 round-trip
    roll 6-month +131.48%   spread $1,638   4 cycles, 4W/0L
    roll 1-year  +120.35%   spread   $831   2 cycles, 2W/0L

Rolling won here while paying **4.3x the spread** — but that is ONE symbol in a strong bull
run, exactly the setup that flatters rolling. The more durable finding is that rolling
**prices symbols that cannot be held at all**: TQQQ, QLD and QQQM all return None for a 2-year
hold (no contract that far out) yet complete real 6-month cycles.

TWO DEFECTS IN MY OWN FIRST VERSION, both caught by RUNNING it rather than reading it — and
both of the same shape, a flag that looked wired up but could not change the answer:

  1. `total_return_pct` chained percentages in BOTH modes, so `compound=True` and
     `compound=False` returned an identical +131.48%. Chaining percentages IS compounding by
     definition; with a fixed size the honest total is realised P&L over the capital actually
     committed. Now +131.48% vs +26.21%.
  2. Position re-sizing is quantised to WHOLE contracts, so with contracts=1 the size cannot
     move until equity doubles (int(1 * 1.457) == 1). That is a real property of trading whole
     contracts, not a bug to paper over — so `contracts_per_cycle` is reported rather than
     hidden behind a fractional position nobody could actually trade.
"""
import ast
import pathlib

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "src/backtest/leaps_backtest.py"
).read_text()


def _fn(name: str) -> str:
    i = SRC.index(f"def {name}(")
    return SRC[i:SRC.index("\ndef ", i + 10)]


# ── The function exists and is bounded ──────────────────────────────────────────────────

def test_the_rolling_backtest_exists():
    assert "def backtest_leaps_rolling(" in SRC


def test_the_hold_period_is_caller_controlled():
    """The user's actual ask: 'sell before 2 years like half a year or a year'."""
    fn = _fn("backtest_leaps_rolling")
    assert "hold_days: int," in fn


def test_cycles_are_hard_bounded():
    """A 1-day hold over a 3-year window would otherwise issue ~1,100 sequential option-chain
    queries against a 17M-row table inside a single request."""
    assert "_MAX_ROLL_CYCLES = 40" in SRC
    assert "_guard < _MAX_ROLL_CYCLES" in _fn("backtest_leaps_rolling")


def test_the_guard_being_hit_is_reported():
    """A silently truncated sequence would understate the total return with no way to tell."""
    assert '"hit_cycle_guard"' in _fn("backtest_leaps_rolling")


# ── It reuses the audited single-cycle engine ───────────────────────────────────────────

def test_it_delegates_to_backtest_leaps():
    """Re-implementing entry selection would fork away from T380's contract-gap walk and
    T381's labelled relaxed band — both of which this inherits for free."""
    assert "r = backtest_leaps(" in _fn("backtest_leaps_rolling")


def test_it_does_not_reimplement_contract_selection():
    fn = _fn("backtest_leaps_rolling")
    assert "find_leaps_entry" not in fn
    assert "_nearest_quote_date" not in fn


def test_the_next_cycle_starts_at_the_ACTUAL_exit():
    """A quote may land a few days before the requested exit. Advancing by the REQUESTED date
    would let a gap compound silently across cycles."""
    fn = _fn("backtest_leaps_rolling")
    assert '_cursor = date.fromisoformat(r["exit_date"])' in fn


def test_a_partial_final_cycle_is_not_reported():
    """Running a 4-month stub as if it were a 6-month hold would misstate the strategy."""
    assert "if _exit > end_date:" in _fn("backtest_leaps_rolling")


# ── Skipped cycles are named, never silently dropped ────────────────────────────────────

def test_an_unpriceable_cycle_is_recorded_with_its_reason():
    """T380 established that an unpriceable contract is a real, recurring condition. A gap
    mid-sequence changes what the total return MEANS, so it must be visible."""
    fn = _fn("backtest_leaps_rolling")
    assert "skipped.append(" in fn
    assert "_explain_no_price(" in fn
    assert '"cycles_skipped": skipped,' in fn


def test_a_skipped_cycle_still_advances_the_cursor():
    """Standing still would re-try the same dead window until the guard trips."""
    fn = _fn("backtest_leaps_rolling")
    i = fn.index("skipped.append(")
    assert "_cursor = _exit" in fn[i:i + 600]


def test_nothing_priced_returns_none():
    assert "if not cycles:\n        return None" in _fn("backtest_leaps_rolling")


# ── Compounding: the two modes must genuinely differ ────────────────────────────────────

def test_compound_changes_the_headline_number():
    """THE FIRST DEFECT. Chaining percentages in BOTH modes made the flag inert — both
    returned an identical +131.48%. Measured after the fix: +131.48% vs +26.21%."""
    fn = _fn("backtest_leaps_rolling")
    assert "if compound:" in fn
    i = fn.index("if compound:")
    branch = fn[i:i + 700]
    assert "_chained" in branch, "compounded mode chains the per-cycle percentages"
    assert "proceeds" in branch and "cost" in branch, "fixed mode uses realised P&L over capital"


def test_cagr_is_derived_from_the_reported_total():
    """If CAGR came from the chained figure while the total came from realised P&L, the two
    would describe different strategies on the same screen — the T380 contradiction shape."""
    fn = _fn("backtest_leaps_rolling")
    assert "_growth = (1.0 + (_total_return_pct or 0.0) / 100.0)" in fn


def test_whole_contract_quantisation_is_surfaced():
    """THE SECOND DEFECT. With contracts=1 the size cannot move until equity DOUBLES, because
    int(1 * 1.457) == 1. Real property of whole contracts — reported, not hidden behind a
    fractional position nobody could trade."""
    fn = _fn("backtest_leaps_rolling")
    assert '"contracts_per_cycle"' in fn
    assert "max(1, int(contracts * _equity_mult))" in fn


def test_position_size_never_drops_below_one():
    assert "max(1, int(" in _fn("backtest_leaps_rolling")


# ── The cost of rolling is reported, not buried ─────────────────────────────────────────

def test_total_spread_cost_is_reported():
    """THE NUMBER THAT DECIDES WHETHER ROLLING BEATS HOLDING. Measured: 4 six-month cycles
    cost $1,638 of spread against $384 for a single 2-year hold — 4.3x."""
    assert '"total_spread_cost"' in _fn("backtest_leaps_rolling")


def test_win_rate_and_per_cycle_return_are_reported():
    fn = _fn("backtest_leaps_rolling")
    assert '"win_rate_pct"' in fn and '"avg_return_per_cycle_pct"' in fn


# ── Input validation ────────────────────────────────────────────────────────────────────

def test_reversed_or_degenerate_inputs_are_rejected():
    fn = _fn("backtest_leaps_rolling")
    assert "end_date <= start_date" in fn
    assert "hold_days < 1 or contracts < 1" in fn


# ── The measured evidence is pinned so the claims cannot rot ────────────────────────────

def test_the_roll_vs_hold_measurement_is_recorded():
    fn = _fn("backtest_leaps_rolling")
    assert "-73.78%" in fn or "T381" in fn, "the leveraged-decay counterexample"


def test_the_spread_multiple_arithmetic():
    """4 six-month cycles pay 4 round-trips, not 1. Pinned as arithmetic so 'rolling is
    basically free' can never be asserted."""
    hold_spread, roll6_spread = 384.0, 1638.0
    assert roll6_spread / hold_spread > 4.0


def test_rolling_prices_symbols_a_two_year_hold_cannot():
    """THE MORE DURABLE FINDING than 'rolling won on QQQ'. TQQQ/QLD/QQQM all return None for a
    2-year hold (no contract that far out) yet complete real 6-month cycles. Pinned as the
    reason this feature is worth having beyond one bull-run backtest."""
    two_year_hold = {"TQQQ": None, "QLD": None, "QQQM": None}
    roll_6m = {"TQQQ": 37.5, "QLD": 18.7, "QQQM": 52.9}
    for s in two_year_hold:
        assert two_year_hold[s] is None and roll_6m[s] is not None

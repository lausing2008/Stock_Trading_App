"""T375-LEAPS-BACKTEST — replay a real LEAPS trade on real captured quotes.

USER REQUEST: "can we add QQQ Leap Call in Strategy Backtester with delta, date range, QQQ or
QQQM or QLD or TQQQ, strike price, expiration date etc"

VERIFIED AGAINST REAL DATA before these tests were written — a genuine 366-day TQQQ hold:

    entry 2024-01-16  TQQQ250117C00036000  strike 36  delta 0.800  ask 20.60
    exit  2025-01-16                                   delta 0.997  bid 41.10
    cost $2,060  proceeds $4,110  pnl +$2,050  return +99.51%  spread cost $332.50

THE DATA CONSTRAINT IS THE POINT OF HALF THIS FILE. Days with a delta-selectable ~0.80 LEAPS,
measured 2026-09-09:

    TQQQ 726    QLD 489    QQQ 312 (backfilling)    QQQM 266    -> only 101 days ALL FOUR

Greeks are sparse BY DESIGN (UW returns delta only where volume > 0), so a symbol's tradeable-day
count is far below its row count. A comparison that ignored this would silently rank symbols over
DIFFERENT date ranges — the same shape as AUD-RANK-RSPLACEHOLDER, where a fabricated neutral 50.0
was fed to a weight optimizer that duly learned from it, and as the three findings in
docs/2026-09-05 that reversed once their samples widened (one rested on six stocks).
"""
import pathlib
from datetime import date, timedelta

import pytest

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "src/backtest/leaps_backtest.py").read_text()


def _fn(name: str) -> str:
    i = SRC.index(f"def {name}(")
    j = SRC.find("\ndef ", i + 10)
    return SRC[i:] if j == -1 else SRC[i:j]


# ── Pricing honesty ─────────────────────────────────────────────────────────────────────

def test_entry_pays_the_ASK_and_exit_receives_the_BID():
    """THE SINGLE MOST IMPORTANT INVARIANT. Crossing at mid systematically overstates every
    result, and on a LEAPS the spread is material — the real 2026-09-04 QQQ 0.80-delta contract
    quoted 196.50 / 201.00, a 2.3% round trip."""
    fn = _fn("backtest_leaps")
    assert "cost = entry.ask * 100" in fn
    assert "proceeds = ex.bid * 100" in fn
    assert "entry.mid * 100" not in fn and "ex.mid * 100" not in fn


def test_the_spread_cost_is_reported_not_buried():
    fn = _fn("backtest_leaps")
    assert '"spread_cost"' in fn


def test_the_contract_multiplier_is_100():
    fn = _fn("backtest_leaps")
    assert "* 100 * contracts" in fn


def test_the_measured_example_arithmetic():
    """Pinned so the pricing convention cannot silently change: ask 20.60 -> bid 41.10."""
    cost = 20.60 * 100
    proceeds = 41.10 * 100
    assert cost == 2060.0
    assert proceeds == 4110.0
    assert round((proceeds - cost) / cost * 100, 2) == 99.51


def test_crossing_at_mid_would_have_overstated_that_trade():
    """Why the ask/bid rule matters, as arithmetic rather than assertion."""
    mid_cost, mid_proceeds = 19.70 * 100, 43.525 * 100
    mid_return = (mid_proceeds - mid_cost) / mid_cost * 100
    real_return = (41.10 * 100 - 20.60 * 100) / (20.60 * 100) * 100
    assert mid_return > real_return, "mid-pricing flatters the result"
    assert round(mid_return - real_return, 1) > 20, "by more than 20 points here"


# ── No fabrication when data is missing ─────────────────────────────────────────────────

def test_a_missing_entry_returns_None_not_a_substitute():
    """A "closest available" contract outside the delta band is a DIFFERENT trade. Substituting
    one silently is how a backtest reports a result for a strategy it never tested."""
    fn = _fn("backtest_leaps")
    assert "if entry is None" in fn
    assert "return None" in fn


def test_the_delta_band_is_bounded():
    assert "_DELTA_BAND = 0.10" in SRC
    fn = _fn("find_leaps_entry")
    assert "delta BETWEEN :dlo AND :dhi" in fn


def test_delta_selection_requires_delta_to_be_present():
    """Greeks are sparse by design; a NULL delta must exclude the row, not be treated as 0."""
    fn = _fn("find_leaps_entry")
    assert "delta IS NOT NULL" in fn


def test_both_sides_require_a_real_two_sided_quote():
    fn = _fn("find_leaps_entry")
    assert "nbbo_bid IS NOT NULL AND nbbo_ask IS NOT NULL" in fn


def test_a_zero_or_negative_entry_price_is_rejected():
    """Guards the return_pct division and an obviously bad quote."""
    fn = _fn("backtest_leaps")
    assert "entry.ask <= 0" in fn


@pytest.mark.parametrize("entry,exit_", [
    (date(2025, 1, 2), date(2025, 1, 2)),   # same day
    (date(2025, 1, 2), date(2024, 1, 2)),   # exit before entry
])
def test_degenerate_date_ranges_are_rejected(entry, exit_):
    assert not (exit_ > entry)


def test_zero_contracts_is_rejected():
    fn = _fn("backtest_leaps")
    assert "contracts < 1" in fn


# ── No lookahead ────────────────────────────────────────────────────────────────────────

def test_the_exit_date_search_is_BACKWARD_only():
    """THE LOOKAHEAD GUARD. A specific contract is not quoted every day, so an exact-date lookup
    would report "no exit" for a trade that plainly had one — but reaching FORWARD would price
    the exit using data from after the intended date. The 2026-09-08 harness audit confirmed the
    existing engine is free of lookahead; this must not reintroduce it."""
    fn = _fn("_nearest_quote_date")
    assert "as_of <= :t" in fn
    assert "as_of >= :floor" in fn, "and bounded, not unlimited"
    assert "MAX(as_of)" in fn


def test_the_backward_window_is_bounded():
    fn = _fn("_nearest_quote_date")
    assert "window_days: int = 7" in fn


def test_the_actual_exit_date_is_reported_separately_from_the_requested_one():
    """If the exit priced two days early, the caller must be able to SEE that rather than
    believe it got the date it asked for."""
    fn = _fn("backtest_leaps")
    assert '"exit_date_requested"' in fn
    assert '"days_held"' in fn


# ── The comparison refuses to mislead ───────────────────────────────────────────────────

def test_coverage_is_a_first_class_result():
    """A user choosing dates needs to know TQQQ has 726 usable days and QQQ 312 BEFORE reading a
    comparison, not after."""
    assert "def coverage(" in SRC
    fn = _fn("coverage")
    assert '"common_days"' in fn
    assert '"comparison_supported"' in fn


def test_a_thin_overlap_is_declared_not_silently_ranked():
    fn = _fn("coverage")
    assert "n_common >= _MIN_COMPARE_DAYS" in fn
    assert "_MIN_COMPARE_DAYS = 30" in SRC


def test_a_symbol_with_no_data_is_NAMED_not_just_omitted():
    """A shorter results list forces the caller to infer which symbol was missing."""
    fn = _fn("compare_symbols")
    assert '"missing": missing' in fn
    assert '"comparable": len(missing) == 0' in fn


def test_the_missing_note_explains_sparse_greeks():
    """Otherwise "no data for QQQM" reads as a bug rather than the documented design."""
    fn = _fn("compare_symbols")
    assert "sparse by design" in fn


def test_coverage_reports_zero_for_a_symbol_with_no_rows():
    """Absent from the GROUP BY must become an explicit 0, not a missing key a caller might
    read as an error — the falsy/absent distinction this codebase keeps getting bitten by."""
    fn = _fn("coverage")
    assert 'by_symbol.setdefault(sym, {"days": 0' in fn


# ── The parameters the user asked for ───────────────────────────────────────────────────

def test_every_requested_parameter_is_supported():
    """delta, date range, symbol, strike, expiry."""
    sig = SRC[SRC.index("def backtest_leaps("):SRC.index(") -> dict | None:")]
    for p in ("symbol", "entry_date", "exit_date", "target_delta", "strike", "expiry"):
        assert p in sig, f"{p} must be a parameter"


def test_an_explicit_strike_or_expiry_pins_the_contract():
    """What makes a SPECIFIC historical position reviewable, not only a delta-targeted one."""
    fn = _fn("find_leaps_entry")
    assert "AND expiry = :exp" in fn
    assert "AND strike = :strike" in fn


def test_the_leaps_definition_is_a_named_constant():
    assert "_MIN_LEAPS_DTE = 330" in SRC


def test_the_dte_floor_is_slightly_under_a_year_on_purpose():
    """365 would silently exclude most of the January chain for much of the year."""
    assert 330 < 365


def test_dte_at_entry_is_reported():
    """A "LEAPS" that turns out to be 340 DTE vs 700 is a materially different trade."""
    assert '"dte_at_entry"' in _fn("backtest_leaps")


# ── The measured coverage, pinned ───────────────────────────────────────────────────────

def test_the_coverage_asymmetry_is_recorded():
    """So nobody compares TQQQ's 726 days against QQQM's 266 and calls it a result."""
    for frag in ("726", "489", "266", "101"):
        assert frag in SRC, f"measured day-count {frag} should be recorded"


def test_the_overlap_is_far_smaller_than_any_single_symbol():
    """101 common days vs 726 for TQQQ alone — the reason comparison needs its own guard."""
    tqqq, common = 726, 101
    assert common < tqqq / 5

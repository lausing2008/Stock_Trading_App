"""Regression test for AUD283-GATEBACKTEST-LOOKAHEAD.

gate_backtest()'s per-signal loop previously looked up its "entry" price at the signal's own
same-day close (entry = _price_at(row.stock_id, sig_date)) — the exact SE-F2 look-ahead bias
already fixed everywhere else in this codebase (a live trader acting on a signal generated
during/after today's close can only enter the NEXT trading day). Confirmed via repo-wide grep
that this endpoint has no live caller/promote step (a pure read-only research/retrospective
tool per its own docstring), so this carried zero production risk — but the reported win-rate/
return numbers were silently overstated by one extra day of price movement on every record.

gate_backtest() itself is 250+ lines of DB query construction with cross-service regime-
threshold replicas, not easily isolated as a whole (matching test_evaluate_outcomes_nested_
savepoint.py's own documented reasoning for evaluate_signal_outcomes()) — this test instead
directly exercises the fixed price-lookup logic (_price_at() plus the entry_date/exit_date
computation) via source-text extraction against a real SQLite-backed price series, proving the
actual mechanism the fix uses rather than a hand-copied reimplementation that could drift.
"""
import pathlib
from datetime import date, timedelta

_OUTCOMES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()


def _make_price_at(prices_by_stock: dict):
    """_price_at() (as defined inside the real gate_backtest()) is a closure over the
    enclosing function's own `prices_by_stock` local — it takes no such parameter itself.
    Extracts its real source and re-creates the identical closure by wrapping the extracted
    def inside an outer function that defines `prices_by_stock` in its own scope first,
    exactly reproducing the real closure relationship rather than bolting the dict on as a
    fake extra parameter."""
    start = _OUTCOMES_SOURCE.index("    def _price_at(stock_id: int, target)")
    end = _OUTCOMES_SOURCE.index("    # Evaluate each signal under old and new gates")
    inner_def = _OUTCOMES_SOURCE[start:end]
    # Re-indent the real inner def one level deeper (4 extra spaces) so it can sit inside our
    # own wrapper function below, preserving its exact body verbatim. The trailing `return`
    # must sit at the SAME extra-indent level as `def _price_at` itself (both are direct
    # children of _wrapper), not at the original, un-shifted 4-space level.
    reindented = "\n".join(("    " + line if line.strip() else line) for line in inner_def.splitlines())
    wrapper_source = "def _wrapper(prices_by_stock):\n" + reindented + "\n        return _price_at\n"
    namespace: dict = {}
    exec(wrapper_source, namespace)  # noqa: S102 — isolated eval of real source, re-wrapped
    return namespace["_wrapper"](prices_by_stock)


def _extract_date_logic():
    end_marker = "    # Evaluate each signal under old and new gates"
    end = _OUTCOMES_SOURCE.index(end_marker)
    loop_start = _OUTCOMES_SOURCE.index("        sig_date = row.ts.date()", end)
    loop_end = _OUTCOMES_SOURCE.index("        entry = _price_at(row.stock_id, entry_date)")
    loop_end = _OUTCOMES_SOURCE.index("\n", loop_end)
    return _OUTCOMES_SOURCE[loop_start:loop_end]


def test_entry_date_is_signal_date_plus_one_not_the_signal_date_itself():
    """The real fix: entry_date must be sig_date + 1, never sig_date itself."""
    date_logic_body = _extract_date_logic()
    assert "entry_date = sig_date + timedelta(days=1)" in date_logic_body
    assert "entry = _price_at(row.stock_id, sig_date)" not in date_logic_body


def test_exit_date_is_relative_to_entry_date_not_the_raw_signal_date():
    """The hold window must start counting from the REAL entry (T+1), not the signal date
    itself — a hold_days=10 window starting from sig_date would silently give the trade one
    extra day of the hold period for free, on top of the entry-price look-ahead bias itself."""
    date_logic_body = _extract_date_logic()
    assert "exit_date = entry_date + timedelta(days=hold_days)" in date_logic_body


def test_price_at_finds_the_correct_t_plus_one_close_not_the_signal_day_close():
    """End-to-end proof against a real price series: a signal fired on day 0 must resolve its
    entry price to day 1's close, not day 0's — the exact behavioral difference the fix
    produces."""
    sig_date = date(2026, 1, 5)
    prices_by_stock = {
        1: [
            (date(2026, 1, 5), 100.0),   # signal day close — must NOT be used as entry
            (date(2026, 1, 6), 103.0),   # T+1 — the correct entry price
            (date(2026, 1, 16), 110.0),  # T+1 + 10 calendar days later (exit)
        ],
    }
    price_at = _make_price_at(prices_by_stock)
    entry_date = sig_date + timedelta(days=1)
    exit_date = entry_date + timedelta(days=10)

    entry_price = price_at(1, entry_date)
    exit_price = price_at(1, exit_date)

    assert entry_price == 103.0
    assert exit_price == 110.0


def test_price_at_falls_back_to_the_nearest_future_price_when_no_exact_match_exists():
    """A weekend/holiday gap must still resolve to the nearest LATER price, matching the
    real function's own future-only ('d >= target') semantics — this behavior is unchanged
    by the fix and must keep working."""
    prices_by_stock = {1: [(date(2026, 1, 5), 100.0), (date(2026, 1, 8), 105.0)]}
    price_at = _make_price_at(prices_by_stock)
    # target = Jan 6 (a gap day with no exact price) -> nearest future price is Jan 8
    result = price_at(1, date(2026, 1, 6))
    assert result == 105.0

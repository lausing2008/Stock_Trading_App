"""Source-text regression tests for the 2026-07-31 signal-testing-framework review fix in
promotion_gate.py: BUG233-BACKTESTHARNESS-EMPTYVALIDATION's window-derivation fix must be
applied consistently in this module too, not just in gate_harness.py itself.

promotion_gate.py imports gate_harness.py (which imports paper_trading_engine.py, which pulls
in the full Docker-only dependency chain), so it can't be imported directly in this local test
environment — matches this repo's established source-text-extraction convention for modules
with this exact constraint (see test_tune_strategy_scheduling.py, test_scheduler_static_names.py
for the same pattern applied elsewhere).
"""
import pathlib

_PG_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "promotion_gate.py"
_PG_SOURCE = _PG_PATH.read_text()


def test_imports_resolvable_window_end_from_gate_harness():
    """The whole point of this fix: promotion_gate.py must import and use the SAME
    _resolvable_window_end() gate_harness.py itself now uses, not re-derive its own,
    independently-driftable copy of the window math."""
    assert "_resolvable_window_end" in _PG_SOURCE
    import_block = _PG_SOURCE[_PG_SOURCE.index("from .gate_harness import"):_PG_SOURCE.index(")\n", _PG_SOURCE.index("from .gate_harness import"))]
    assert "_resolvable_window_end" in import_block


def test_evaluate_and_record_recomputes_the_split_using_resolvable_end():
    """evaluate_and_record()'s own recompute of train_end/val_start (to get raw per-trade
    returns for the worst-trade check) must use resolvable_end, not the raw window_end —
    otherwise its worst-trade-check validation slice would silently disagree with the one
    walk_forward_min_entry_score() itself validated against."""
    func_start = _PG_SOURCE.index("def evaluate_and_record(")
    func_end = _PG_SOURCE.index("\ndef _write_history(")
    body = _PG_SOURCE[func_start:func_end]
    assert "resolvable_end = _resolvable_window_end(window_end, style)" in body
    # The two replay_should_enter() calls inside this function's worst-trade-check recompute
    # must both pass resolvable_end as their window_end argument, not the raw window_end.
    replay_calls = [line for line in body.splitlines() if "replay_should_enter(" in line or (
        "val_start, " in line and "resolvable_end" in line
    )]
    assert any("val_start, resolvable_end" in line for line in body.splitlines())
    assert "val_start, window_end," not in body  # the raw, unadjusted form must not survive


def test_write_history_recomputes_the_split_using_resolvable_end():
    """_write_history()'s OWN third independent re-derivation of train_end/val_start (for the
    TuneHistory row's recorded window boundaries) must also use resolvable_end — a real,
    previously-uncaught risk since this function re-derives the split a third time, completely
    independently of evaluate_and_record()'s own recompute."""
    func_start = _PG_SOURCE.index("def _write_history(")
    func_end = _PG_SOURCE.index("\n    row = TuneHistory(")
    body = _PG_SOURCE[func_start:func_end]
    assert "resolvable_end = _resolvable_window_end(window_end, style)" in body


def test_write_history_records_resolvable_end_not_raw_window_end():
    """The persisted validation_window_end must be the SAME resolvable_end value actually used
    for the split/replay above it, not the original, pre-adjustment window_end — otherwise the
    tune_history audit trail would silently misrepresent what window was actually validated."""
    func_start = _PG_SOURCE.index("def _write_history(")
    func_end = _PG_SOURCE.index("\n\n\ndef ", func_start + 1) if "\n\n\ndef " in _PG_SOURCE[func_start:] else len(_PG_SOURCE)
    body = _PG_SOURCE[func_start:func_end]
    assert "validation_window_end=resolvable_end," in body
    assert "validation_window_end=window_end," not in body

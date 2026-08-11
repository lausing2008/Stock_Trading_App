"""Tests for AUD262-EXITREASON-CONFLATION-ROOT (Deep Audit #2, Tier 262).

_monitor_positions()'s stop-breach branch previously labeled EVERY stop breach outside the
break-even tolerance as "stop_hit" — conflating a genuine protective loss-cut (stop <= entry)
with a profitable trailing-stop exit (stop ratcheted above entry, then triggered). Production
evidence: 14 of 49 stop_hit trades exited PROFITABLY, up to +13.96%.

Fix: a stop breach with stop > entry (outside the break-even tolerance) now gets its own
"trailing_stop" label, distinct from "stop_hit". Because the new label is a different string,
every downstream consumer that filters `exit_reason == "stop_hit"` (the heat brake, the 5-day
re-entry cooldown) automatically stops counting profitable trailing exits — verified below both
by source inspection AND by the trivial fact that "trailing_stop" != "stop_hit" in Python.

_monitor_positions() can't be exercised end-to-end in this test environment (heavy DB/session/
live-price dependencies) — matching this repo's established source-text-extraction technique
(e.g. test_signaloutcome_writeback_blended.py, test_min_ta_score_config_wiring.py).
"""
import pathlib

_engine_path = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_engine_source = _engine_path.read_text()

_portfolio_path = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
)
_portfolio_source = _portfolio_path.read_text()


def _stop_breach_block() -> str:
    start = _engine_source.index("elif live_price <= stop:")
    end = _engine_source.index("elif target and live_price >= target:", start)
    return _engine_source[start:end]


def test_trailing_stop_is_a_distinct_label_from_stop_hit():
    block = _stop_breach_block()
    assert 'exit_reason = "trailing_stop"' in block
    assert 'exit_reason = "stop_hit"' in block
    # the two labels are literally different strings — this is what makes every downstream
    # `PaperTrade.exit_reason == "stop_hit"` filter automatically exclude trailing exits
    assert "trailing_stop" != "stop_hit"


def test_trailing_stop_branch_requires_stop_above_entry():
    """The new branch must only fire when the stop that triggered had ratcheted ABOVE entry —
    a stop at or below entry (a genuine loss-cut) must still be stop_hit.

    AUD262-BREAKEVEN-COOLDOWN-60X-TOO-SHORT (2026-08-06) added `and live_price >= entry` to
    this condition — a stop marginally above entry combined with a hard gap-down fill well
    below entry must NOT be mislabeled trailing_stop just because `stop > entry` alone was
    true; see test_breakeven_fill_price_check.py for the behavioral cases."""
    block = _stop_breach_block()
    trailing_idx = block.index("elif stop > entry and live_price >= entry:")
    stophit_idx = block.index('exit_reason = "stop_hit"')
    assert trailing_idx < stophit_idx
    assert "elif stop > entry and live_price >= entry:" in block


def test_breakeven_check_still_runs_before_the_trailing_stop_check():
    """Ordering must stay: breakeven (stop ~= entry) is checked FIRST, then trailing_stop
    (stop > entry beyond the breakeven tolerance), then stop_hit as the final else."""
    block = _stop_breach_block()
    be_idx = block.index('exit_reason = "breakeven_stop"')
    trailing_idx = block.index('exit_reason = "trailing_stop"')
    stophit_idx = block.index('exit_reason = "stop_hit"')
    assert be_idx < trailing_idx < stophit_idx


def test_fill_price_uses_live_price_directly_for_every_exit_reason():
    """AUD262-MIN-STOP-FILL-NOOP: the old `min(stop, live_price) if exit_reason in
    ("stop_hit", "trailing_stop") else live_price` conditional was dead code — stop_hit/
    breakeven_stop/trailing_stop are ONLY ever reached from the `elif live_price <= stop:`
    branch, so live_price <= stop always held there and min() always equaled live_price. Fixed
    by using live_price directly for every label (the real, single behavior the dead
    conditional already produced), rather than leaving a branch that implies a stop-limit-vs-
    market-fill distinction the code never actually implemented."""
    assert 'exit_price = round(live_price * (1 - slippage), 4)' in _engine_source
    assert "fill_base" not in _engine_source
    assert 'if exit_reason in ("stop_hit", "trailing_stop")' not in _engine_source


def test_heat_brake_query_still_filters_only_stop_hit_not_trailing_stop():
    """The heat brake must NOT be updated to also match trailing_stop — excluding profitable
    trailing exits from the 'adverse conditions' count is exactly the fix; the heat brake
    resolves 'for free' once the label split exists, with zero code change needed here."""
    start = _engine_source.index("_heat_max = cfg.get(\"heat_brake_max_stops\"")
    end = _engine_source.index("return\n", start)
    block = _engine_source[start:end]
    assert 'PaperTrade.exit_reason == "stop_hit"' in block
    assert '"trailing_stop"' not in block


def test_cooldown_query_still_filters_only_stop_hit_not_trailing_stop():
    """Same property for the 5-day re-entry cooldown — resolves for free via the label split,
    no query change needed."""
    start = _engine_source.index("stop_cooldown_hours = cfg.get(")
    end = _engine_source.index("be_cooldown_hours = cfg.get(", start)
    block = _engine_source[start:end]
    assert 'PaperTrade.exit_reason == "stop_hit"' in block
    assert '"trailing_stop"' not in block


def test_mechanical_exit_reasons_includes_trailing_stop():
    """trailing_stop is exactly as plan-consistent as stop_hit (same mechanism, different
    stop-vs-entry position) — it belongs in the postmortem's mechanical-exit set."""
    assert '_MECHANICAL_EXIT_REASONS = {"stop_hit", "trailing_stop", "breakeven_stop", "target_reached", "time_stop"}' in _portfolio_source

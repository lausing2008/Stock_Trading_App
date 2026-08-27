"""Tests for AUD261-BARE-GT-ZERO-NO-HURDLE (Deep Audit #1, Tier 261).

factor_exposure() and filter_audit() previously used a bare `ret > 0` / `exit_p > entry`
win test with no cost hurdle — diverging from the canonical _OUTCOME_WIN_HURDLE_PCT convention
used elsewhere (evaluate_signal_outcomes, rolling_accuracy, signal_accuracy). Both endpoints
are BUY-only (confirmed via their own Signal.signal == SignalType.BUY filters), so there was
no sign error — but a move at, say, +0.1% (below realistic trading cost) counted as a win.
filter_audit()'s "win" field directly drives its "harmful"/"predictive" filter verdict, which
real tuning decisions read.

Fix: all 5 bare `> 0` win-test sites in these two functions now use `> _OUTCOME_WIN_HURDLE_PCT`
instead. Both functions are large FastAPI routes with heavy DB/session dependencies — the
import constraint (outcomes.py can't be imported directly; conftest.py stubs the `common`
package wholesale) means this is covered by source-text regression checks, matching this
repo's established convention for functions of this shape and size.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _function_body(name: str, next_marker: str) -> str:
    start = _ROUTES_SOURCE.index(f"def {name}(")
    end = _ROUTES_SOURCE.index(next_marker, start)
    return _ROUTES_SOURCE[start:end]


def _factor_exposure_body() -> str:
    return _function_body("factor_exposure", '@router.get("/trade_performance")')


def _filter_audit_body() -> str:
    return _function_body("filter_audit", '@router.get("/walkforward")')


# ── factor_exposure() ─────────────────────────────────────────────────────────────────────

def test_factor_exposure_win_test_uses_the_cost_hurdle():
    body = _factor_exposure_body()
    assert "correct = (exit_p - entry) / entry > _OUTCOME_WIN_HURDLE_PCT" in body
    assert "correct = exit_p > entry" not in body


def test_factor_exposure_is_still_buy_only_no_sign_error_possible():
    """Confirms the fix's own premise — this endpoint only ever evaluates BUY signals, so a
    bare (unsigned) hurdle comparison is correct with no SELL-direction sign issue."""
    body = _factor_exposure_body()
    assert "Signal.signal == SignalType.BUY" in body


# ── filter_audit() ────────────────────────────────────────────────────────────────────────

def test_filter_audit_per_trade_win_field_uses_the_cost_hurdle():
    body = _filter_audit_body()
    assert '"win":          ret > _OUTCOME_WIN_HURDLE_PCT,' in body


def test_filter_audit_by_filter_count_wins_uses_the_cost_hurdle():
    body = _filter_audit_body()
    assert "wins = sum(1 for r in rets if r > _OUTCOME_WIN_HURDLE_PCT)" in body


def test_filter_audit_by_filter_name_active_and_inactive_win_rates_use_the_cost_hurdle():
    """These two feed the edge_pct/'harmful' verdict a real tuning decision reads directly."""
    body = _filter_audit_body()
    assert "act_wr   = round(sum(1 for r in act   if r > _OUTCOME_WIN_HURDLE_PCT)" in body
    assert "inact_wr = round(sum(1 for r in inact if r > _OUTCOME_WIN_HURDLE_PCT)" in body


def test_filter_audit_has_no_remaining_bare_gt_zero_win_test():
    """No occurrence of the OLD bare `r > 0` / `ret > 0` win test should remain anywhere in
    this function — every one of the 4 sites must have been migrated to the hurdle."""
    body = _filter_audit_body()
    assert "if r > 0" not in body
    assert "ret > 0" not in body


def test_filter_audit_is_still_buy_only_no_sign_error_possible():
    body = _filter_audit_body()
    assert "Signal.signal == SignalType.BUY" in body


def test_filter_audit_overall_win_rate_reads_the_already_hurdle_corrected_win_field():
    """overall_win_rate_pct reads back t["win"] from per_trade (already fixed above) rather
    than re-deriving its own bare comparison — confirms there's no 6th, separately-broken
    site hiding in the summary computation."""
    body = _filter_audit_body()
    assert 'overall_wr = round(sum(1 for t in per_trade if t["win"])' in body

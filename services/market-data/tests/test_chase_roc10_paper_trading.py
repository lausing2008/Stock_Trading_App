"""AUD-CHASE-ROC10-PAPERPORT: port the validated anti-chasing filter to paper trading.

scheduler.py's AUD-CHASE-ROC10 added a guard to the EMAIL-alert conviction gate
(_is_conviction_buy()) blocking BUYs on stocks that already ran roc_10 >= 10% in the prior 10
trading days, and validated it out-of-sample (fit period -2.22% -> -0.57%, HOLDOUT -0.96% ->
-0.79%, ~83% of signals retained — see SHORT_SQUEEZE_ALERT_TUNING_REVIEW.md's sibling doc
WHY_SIGNALS_FIRE_LATE.md for the full derivation).

That guard was never ported to _should_enter() in paper_trading_engine.py — a SEPARATE entry
qualifier with its own, narrower "gap-up chasing" check (AUD-GAPCHASE-EARNINGSVOL) that only
measures the gap SINCE the signal fired (minutes to hours), and is blind to a move that already
happened over the prior 10 trading days. That is precisely the SNOW case documented right next
to it: entered at $377.34 with reasons["last_price"]=$377.995 (~0% measured "gap") despite
having already run hard before the signal ever computed; it later stopped out at -19.0%.

Mirrored as a separate constant (_MAX_ROC10_FOR_ENTRY_PAPER) rather than imported from
scheduler.py: scheduler.py already imports FROM paper_trading_engine.py at module level
(get_last_regime, paper_trading_step, ...), so the reverse import would be circular. Same
threshold value (10.0), deliberately not re-derived — paper trading's own trades are a subset
of the population the original out-of-sample analysis covered, not an independent population
that would justify its own separately-fitted cutoff.

paper_trading_engine.py can't be imported directly in this test environment (heavy dependency
chain — conftest.py stubs sqlalchemy itself). The guard is a pure threshold check with no DB
access, so it's verified via source-text regression checks plus a behavioral model of the exact
expression, matching test_conviction_buy_overextension_guards.py's established technique for
this same guard shape in scheduler.py.
"""
import pathlib

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py").read_text()


def _should_enter_fn() -> str:
    start = _SOURCE.index("def _should_enter(")
    return _SOURCE[start:_SOURCE.index("\n\n\ndef ", start)]


# ── the guard exists and reads the right things ──────────────────────────────

def test_constant_mirrors_the_scheduler_value():
    assert "_MAX_ROC10_FOR_ENTRY_PAPER = 10.0" in _SOURCE


def test_constant_is_defined_before_should_enter():
    const_idx = _SOURCE.index("_MAX_ROC10_FOR_ENTRY_PAPER = 10.0")
    fn_idx = _SOURCE.index("def _should_enter(")
    assert const_idx < fn_idx


def test_guard_reads_roc_10_from_reasons():
    fn = _should_enter_fn()
    assert '_roc10_paper = reasons.get("roc_10")' in fn


def test_guard_uses_the_paper_specific_constant_not_a_hardcoded_10():
    fn = _should_enter_fn()
    assert "float(_roc10_paper) >= _MAX_ROC10_FOR_ENTRY_PAPER" in fn


def test_guard_returns_the_same_false_minus99_shape_as_every_other_reject():
    """_should_enter()'s own established contract: (False, -99, [reason]) for a hard reject —
    must match every sibling guard (R:R, earnings, gap-chase) exactly."""
    fn = _should_enter_fn()
    idx = fn.index("_roc10_paper = reasons.get")
    block = fn[idx:idx + 400]
    assert "return False, -99, [" in block
    assert "chasing an extended move" in block


def test_guard_sits_after_the_existing_gap_chase_check():
    """Documents the actual gap this closes — the SNOW case the gap-chase check already
    couldn't catch. Ordering isn't load-bearing, but pins that this is genuinely additive."""
    fn = _should_enter_fn()
    assert fn.index("AUD-GAPCHASE-EARNINGSVOL") < fn.index("_roc10_paper = reasons.get")


def test_guard_fails_open_when_roc_10_is_absent():
    """Older/degraded reasons payloads without roc_10 must not be blocked by a value that
    isn't there — matches the email-alert sibling's own fail-open contract exactly."""
    fn = _should_enter_fn()
    idx = fn.index("_roc10_paper = reasons.get")
    block = fn[idx:idx + 200]
    assert "if _roc10_paper is not None and" in block


# ── behavior of the threshold itself ─────────────────────────────────────────

def _rejects(roc10) -> bool:
    """The exact condition the guard applies."""
    return roc10 is not None and float(roc10) >= 10.0


def test_snow_style_extended_move_is_now_rejected():
    """The motivating case: a stock that ran hard over the prior 10 days."""
    assert _rejects(23.0) is True


def test_moderate_runup_still_enters():
    assert _rejects(5.0) is False


def test_falling_stock_still_enters():
    """The genuinely profitable bucket per WHY_SIGNALS_FIRE_LATE.md (+0.09%, 52.9% win) — a
    real dip entry must not be blocked."""
    assert _rejects(-6.0) is False


def test_threshold_is_inclusive_at_the_boundary():
    assert _rejects(10.0) is True
    assert _rejects(9.9) is False


def test_missing_roc10_does_not_reject():
    assert _rejects(None) is False

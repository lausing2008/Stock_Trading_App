"""Tests for AUD266-DE-GATE-WHITELISTS-NONEXISTENT-SCALE-VERDICT (Deep Audit #6, Tier 266).

check_signal_alerts()'s DE gate previously whitelisted a "SCALE" verdict that decision-engine
NEVER returns (verified by grep: 0 occurrences anywhere in services/decision-engine/src/ — it
returns exactly BUY/HOLD/SKIP/BLOCKED, per routes.py:296-301's verdict assignment). This
silently reduced the gate to BUY-only, rejecting DE's HOLD verdict — a deliberate near-miss
(score >= min_score - 2), not a genuine rejection. Production: 4,824 alerts passed the 5-layer
conviction gate in 48h, 4,782 were rejected by this gate, only 27 fired.

Fix: the whitelist now admits ("BUY", "HOLD") instead of ("BUY", "SCALE").

check_signal_alerts() can't be exercised end-to-end in this test environment (heavy DB/session/
httpx/Redis dependencies) — matching this repo's established source-text-extraction technique.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()

_de_routes_path = (
    pathlib.Path(__file__).resolve().parents[3]
    / "services" / "decision-engine" / "src" / "api" / "routes.py"
)
_de_routes_source = _de_routes_path.read_text() if _de_routes_path.exists() else ""


def _de_gate_block() -> str:
    start = _scheduler_source.index("# DE gate: for BUY transitions")
    end = _scheduler_source.index("# Build game plan for BUY transitions", start)
    return _scheduler_source[start:end]


def test_gate_no_longer_whitelists_the_dead_scale_verdict():
    """The actual CODE (not the explanatory comment, which legitimately mentions "SCALE" in
    prose while describing the bug it fixes) must not check against the tuple ("BUY", "SCALE")."""
    block = _de_gate_block()
    assert '("BUY", "SCALE")' not in block
    assert 'if de_verdict not in ("BUY", "SCALE")' not in block


def test_gate_whitelists_buy_and_hold():
    block = _de_gate_block()
    assert 'if de_verdict not in ("BUY", "HOLD"):' in block


def test_hold_is_a_real_verdict_decision_engine_returns():
    """Guards against the class of bug this fix corrects: only whitelist verdicts that
    decision-engine actually assigns somewhere in its own verdict logic."""
    if not _de_routes_source:
        return  # decision-engine checkout not present in this test environment; skip gracefully
    assert 'verdict = "HOLD"' in _de_routes_source
    assert 'verdict = "BUY"' in _de_routes_source
    assert '"SCALE"' not in _de_routes_source


def test_an_unavailable_decision_engine_DEFERS_a_buy_rather_than_allowing_it():
    """REVERSED 2026-09-28 by the email audit (EA-08). This test previously read:

        def test_fail_open_on_de_unreachable_is_unchanged():
            '''Regression guard: the fail-open-on-exception behavior (never block an alert on
            a DE infrastructure failure) must be untouched by this fix.'''
            assert "except Exception as _de_exc:" in _scheduler_source
            assert 'note="DE unreachable — fail-open, allowing alert"' in _scheduler_source

    — it pinned the fail-open as a REQUIREMENT, so correcting it read as a regression.

    The original instinct is right for an EXIT and wrong for a BUY. A safety veto that fails
    open is not a veto: a non-200 fell through with no branch at all and an exception was
    swallowed at debug level, and either way the BUY was emitted exactly as though
    decision-engine had approved it. Withholding an exit costs the reader a chance to reduce
    risk; emitting a BUY without its veto asks them to commit money on a check that never ran.

    Deferring — not dropping. `last_signal` is deliberately not advanced, which is the same
    treatment a real SKIP verdict already receives, so the transition stays pending and the
    next run re-asks once DE is back."""
    assert "except Exception as _de_exc:" in _scheduler_source
    assert 'note="DE unreachable — fail-open, allowing alert"' not in _scheduler_source, \
        "the fail-open is back"
    assert "_de_available = True" in _scheduler_source
    assert "if not _de_available:" in _scheduler_source
    # A non-200 must be handled too, not only an exception.
    assert "signal_alert.de_gate_unavailable" in _scheduler_source


def test_the_deferral_does_not_consume_the_transition():
    """If `last_signal` advanced here, a decision-engine outage would permanently swallow every
    BUY that happened during it — trading a fail-open for a silent-drop, which is worse."""
    body = _scheduler_source[_scheduler_source.index("if not _de_available:"):]
    body = body[:body.index("# Build game plan")]
    assert "alert.last_signal" not in body
    assert "continue" in body

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


def test_fail_open_on_de_unreachable_is_unchanged():
    """Regression guard: the fail-open-on-exception behavior (never block an alert on a DE
    infrastructure failure) must be untouched by this fix."""
    assert "except Exception as _de_exc:" in _scheduler_source
    assert "note=\"DE unreachable — fail-open, allowing alert\"" in _scheduler_source

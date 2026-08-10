"""Tests for T232-DL-DUALSCORER-DEBT — threading T221-E's portfolio heat brake
(recent_stop_count / heat_brake_max_stops) from paper_trading_engine.py's _scan_for_entries()
into decision-engine's config_overrides.

Genuinely per-portfolio state (a count of THIS portfolio's own recent stop_hit exits) — but the
count itself is computed once per scan cycle, BEFORE the per-candidate loop begins, the exact
same shape as recent_win_rate/consec_losses/initial_capital, which were already threaded
through successfully. The one real fix needed alongside the new parameter: _recent_stops was
previously scoped inside `if _heat_max > 0:` (never computed at all when the gate is disabled),
so it had to be hoisted to a properly-initialized `None` default to survive, unconditionally,
to the call site below — matching the identical hoisting fix already applied to the index-trend
gate's _idx_ret for the same reason.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, matching test_price_drift_config_wiring.py's/
test_index_trend_config_wiring.py's established technique.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_recent_stop_count_is_threaded_into_config_overrides():
    """The exact fix: recent_stop_count (the measured recent-stop count) and
    heat_brake_max_stops (the threshold) must both actually be included in the
    config_overrides dict sent to decision-engine, not just exist in _scan_for_entries' own
    T221-E pre-filter with nothing downstream reading them."""
    assert '"recent_stop_count":' in _decision_body
    assert '"heat_brake_max_stops":' in _decision_body


def test_heat_brake_max_stops_falls_back_to_the_real_default_config_value():
    """The write side's fallback must match _scan_for_entries' own real fallback
    (cfg.get("heat_brake_max_stops", 3), i.e. _DEFAULT_CONFIG["heat_brake_max_stops"]) exactly
    — not a differently-valued literal that would silently diverge from the upstream T221-E
    pre-filter."""
    start = _decision_body.index('"heat_brake_max_stops":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert '_DEFAULT_CONFIG["heat_brake_max_stops"]' in line


def test_default_config_heat_brake_max_stops_is_3():
    """Cross-check: the fallback referenced above must actually resolve to the real, documented
    default (3 stops) — not a stale/drifted value."""
    assert '"heat_brake_max_stops":      3,' in _pte_source


def test_recent_stop_count_and_threshold_are_conditional_on_recent_stop_count_being_present():
    """Both keys must only be sent when a real recent_stop_count value is also being computed —
    sending a threshold with no measured stop-count to compare against would be meaningless,
    matching the existing conditional-inclusion pattern already used for every other gate
    ported this session. The `if ... is not None else {}` guard closes the dict-spread
    expression AFTER both keys (matching the exact **( {...} if cond else {} ) shape every
    other conditional gate in this function already uses), so the guard is searched for
    FORWARD from each key, not backward."""
    for key in ('"recent_stop_count":', '"heat_brake_max_stops":'):
        start = _decision_body.index(key)
        following = _decision_body[start:start + 500]
        assert "recent_stop_count is not None" in following, (
            f"{key} not conditionally guarded on recent_stop_count is not None"
        )


def test_call_site_passes_the_hoisted_recent_stops_variable():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass
    _recent_stops — the SAME variable the T221-E gate itself computes a few lines earlier in
    the function — not a re-fetch or a different derivation."""
    assert "recent_stop_count=_recent_stops" in _pte_source


def test_recent_stops_is_hoisted_to_a_typed_none_default_before_the_conditional_block():
    """Regression guard against the exact class of bug this port's own hoisting fix avoids:
    _recent_stops must be initialized to None BEFORE the REAL `if _heat_max > 0:` statement
    (not merely mentioned in a preceding comment), not only assigned inside it — otherwise a
    portfolio with the gate disabled (heat_brake_max_stops<=0) would hit a real NameError the
    moment _call_decision_engine() tries to read the name."""
    hoist_idx = _pte_source.index("_recent_stops: int | None = None")
    # Anchor on the real executable statement, not a comment that also happens to mention this
    # exact string in prose (this module's own hoisting-fix comment does, right above it).
    heat_max_if_idx = _pte_source.index("\n    if _heat_max > 0:")
    assert hoist_idx < heat_max_if_idx, (
        "_recent_stops must be hoisted to a None default BEFORE the real `if _heat_max > 0:` statement"
    )


def test_recent_stop_count_param_exists_on_call_decision_engine_signature():
    """The function signature itself must declare recent_stop_count as an optional keyword
    parameter defaulting to None, matching the established pattern for every other
    gate-parity parameter (kscore, ta_score, confidence_delta, index_return_pct, sig_ref_price)."""
    sig_start = _pte_source.index("def _call_decision_engine(")
    sig_end = _pte_source.index(") -> tuple[bool, str, int, str | None] | None:", sig_start)
    signature = _pte_source[sig_start:sig_end]
    assert "recent_stop_count: int | None = None" in signature

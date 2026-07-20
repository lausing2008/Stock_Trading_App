"""Tests for AUD256 — threading min_rr_ratio/regime_min_rr_ratio from paper_trading_engine.py's
calibrated defaults into decision-engine's config_overrides.

Two related but distinct gaps, both confirmed in decision-engine's hard_rejects.py (which
already has the READ side: `cfg.get("min_rr_ratio", 2.0)` and, in choppy/risk_off regimes,
`cfg.get("regime_min_rr_ratio", 3.0)`):

1. min_rr_ratio WAS sent, but its own fallback literal (2.0) bypassed
   SELFIMPROVE-NEVER-CALIBRATED-PARAMS' calibration entirely — _should_enter() resolves the
   same key via _default_min_rr_ratio("neutral"), which returns the calibrated value once
   min_rr_calibration.json exists, falling back to 2.0 only if calibration has never run.
   _call_decision_engine() used the bare 2.0 literal directly, so DE's neutral-regime floor
   never tracked calibration even after it ran.

2. regime_min_rr_ratio was NEVER sent at all — decision-engine always fell back to its own
   hardcoded 3.0 for choppy/risk_off regimes (T190), completely blind to calibration, even
   though _should_enter() has been correctly regime-aware here since AUD232-060.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, same technique as test_llm_scoring_config_wiring.py and
test_min_kscore_config_wiring.py.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_min_rr_ratio_routes_through_the_calibrated_default_not_a_bare_literal():
    """The exact fix for gap 1: min_rr_ratio's fallback must call _default_min_rr_ratio("neutral"),
    not a hardcoded 2.0 literal that would silently bypass calibration forever."""
    assert '"min_rr_ratio":' in _decision_body
    start = _decision_body.index('"min_rr_ratio":')
    line = _decision_body[start:_decision_body.index("\n", start)]
    assert '_default_min_rr_ratio("neutral")' in line
    assert "2.0" not in line, "must not fall back to a bare hardcoded literal — that bypasses calibration"


def test_regime_min_rr_ratio_is_threaded_into_config_overrides():
    """The exact fix for gap 2: regime_min_rr_ratio must actually be included in the
    config_overrides dict sent to decision-engine, not just exist in _should_enter()'s own
    regime-aware branch with nothing downstream reading the threshold itself."""
    assert '"regime_min_rr_ratio":' in _decision_body


def test_regime_min_rr_ratio_falls_back_to_the_calibrated_default_via_regime_state():
    """Must resolve through _default_min_rr_ratio(regime_state) — using the ACTUAL regime_state
    parameter, not a hardcoded "neutral"/"choppy" literal that could silently drift from the
    real regime the candidate is being evaluated under."""
    start = _decision_body.index('"regime_min_rr_ratio":')
    line = _decision_body[start:_decision_body.index("\n", start)]
    assert "_default_min_rr_ratio(regime_state)" in line
    assert "3.0" not in line, "must not fall back to a bare hardcoded literal — that bypasses calibration"


def test_call_decision_engine_accepts_a_regime_state_parameter():
    """_call_decision_engine() must accept regime_state so the caller can supply the real,
    current regime — without this parameter there is nothing for the config_overrides
    resolution above to actually use."""
    start = _pte_source.index("def _call_decision_engine(")
    end = _pte_source.index(") -> tuple[bool, str, int, str | None] | None:", start)
    signature = _pte_source[start:end]
    assert "regime_state" in signature


def test_call_site_passes_the_real_live_regime_state_not_a_hardcoded_value():
    """The actual call to _call_decision_engine() (inside _scan_for_entries()) must derive
    regime_state from the real live_regime dict in scope, not a hardcoded literal — otherwise
    the parameter added above would always resolve to the same value regardless of the
    candidate's actual market regime."""
    start = _pte_source.index("de_result = _call_decision_engine(")
    end = _pte_source.index("\n        _max_corr = _max_correlation_with_open_positions", start)
    call_body = _pte_source[start:end]
    assert 'regime_state=(live_regime.get("state", "neutral") if live_regime else "neutral")' in call_body

"""Tests for T232-DL-DUALSCORER-DEBT — threading T221's index-trend gate (index_return_pct /
index_trend_gate_pct) from paper_trading_engine.py's _scan_for_entries() into decision-engine's
config_overrides.

Unlike min_kscore/min_ta_score/HK-flow/low-volume (all of which already reached decision-engine
for free — either via sig.reasons already being sent wholesale, or via a value already
computed per-candidate), index_return_pct was NEVER already flowing to decision-engine anywhere
(not in sig.reasons, not in /stocks/regime, not in any existing config_overrides key). This is a
genuine write-side change: _idx_ret is computed ONCE per scan cycle (before the candidate loop
even starts, via a single yfinance fast_info call) and now must be hoisted to survive to the
per-candidate _call_decision_engine() call site, and threaded through explicitly.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, matching test_min_kscore_config_wiring.py's established technique.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_index_return_pct_is_threaded_into_config_overrides():
    """The exact fix: index_return_pct (the measured value) and index_trend_gate_pct (the
    threshold) must both actually be included in the config_overrides dict sent to
    decision-engine, not just exist in _scan_for_entries' own pre-filter with nothing
    downstream reading them."""
    assert '"index_return_pct":' in _decision_body
    assert '"index_trend_gate_pct":' in _decision_body


def test_index_trend_gate_pct_falls_back_to_the_real_default_of_negative_1_5_pct():
    """The write side's fallback must match _scan_for_entries' own real fallback
    (cfg.get("index_trend_gate_pct", -0.015)) exactly — not a differently-signed or
    differently-valued literal that would silently diverge from the upstream pre-filter."""
    start = _decision_body.index('"index_trend_gate_pct":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert 'cfg.get("index_trend_gate_pct", -0.015)' in line


def test_index_return_pct_and_threshold_are_conditional_on_index_return_pct_being_present():
    """Both keys must only be sent when a real index_return_pct value is also being
    computed — sending a threshold with no measured value to compare it against would be
    meaningless, matching the existing conditional-inclusion pattern already used for every
    other gate ported this session."""
    for key in ('"index_return_pct":', '"index_trend_gate_pct":'):
        start = _decision_body.index(key)
        surrounding = _decision_body[max(0, start - 400):start + 100]
        assert "index_return_pct is not None" in surrounding, (
            f"{key} not conditionally guarded on index_return_pct is not None"
        )


def test_idx_ret_is_hoisted_with_a_typed_none_default_before_the_conditional_block():
    """_idx_ret must be initialized to None BEFORE the try/except block that may or may not
    set it — otherwise a NameError would occur when the block's condition
    (index_trend_gate_enabled) is False, or when the try body raises before assignment,
    since a bare `except Exception: pass` swallows the error but leaves the name unbound."""
    start = _pte_source.index("_idx_ret: float | None = None")
    assert start != -1
    # Must appear BEFORE the try/except block that conditionally assigns a real value.
    try_start = _pte_source.index("try:\n            import yfinance as yf", start)
    assert start < try_start


def test_call_site_passes_idx_ret_as_index_return_pct():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass
    through the SAME _idx_ret local already computed earlier in this scan cycle — not a
    fresh per-candidate yfinance call, matching how confidence_delta/kscore_f/ta_score_f are
    each threaded exactly once per cycle/iteration, not re-fetched per candidate."""
    assert "index_return_pct=_idx_ret" in _pte_source

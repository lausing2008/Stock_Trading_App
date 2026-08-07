"""Tests for T232-DL-DUALSCORER-DEBT — threading T215/T222-B's multi-timeframe confluence
gate (short_signal / confluence_check_enabled) from paper_trading_engine.py's
_scan_for_entries() into decision-engine's config_overrides.

Genuinely a new write-side change (like index_return_pct/sig_ref_price), not a free port
(like min_kscore/HK-flow/low-volume, whose values were already flowing to decision-engine
somewhere) — short_signal was never threaded anywhere before this fix.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, matching test_price_drift_config_wiring.py's established technique.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_short_signal_is_threaded_into_config_overrides():
    """The exact fix: short_signal (the measured SHORT-horizon signal) and
    confluence_check_enabled (the portfolio's own opt-out flag) must both actually be
    included in the config_overrides dict sent to decision-engine, not just exist in
    _scan_for_entries' own T215/T222-B pre-filter with nothing downstream reading them."""
    assert '"short_signal":' in _decision_body
    assert '"confluence_check_enabled":' in _decision_body


def test_confluence_check_enabled_falls_back_to_the_real_default_of_true():
    """The write side's fallback must match _scan_for_entries' own real fallback
    (cfg.get("confluence_check_enabled", True)) exactly — a portfolio that never explicitly
    set this key must still be gated, matching the upstream pre-filter's own default-on
    behavior."""
    start = _decision_body.index('"confluence_check_enabled":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert 'cfg.get("confluence_check_enabled", True)' in line


def test_short_signal_and_flag_are_conditional_on_short_signal_being_present():
    """Both keys must only be sent when a real short_signal value is also being computed —
    sending the enabled-flag with no measured SHORT signal to compare against would be
    meaningless, matching the existing conditional-inclusion pattern already used for every
    other gate ported this session."""
    for key in ('"short_signal":', '"confluence_check_enabled":'):
        start = _decision_body.index(key)
        surrounding = _decision_body[max(0, start - 100):start + 250]
        assert "short_signal is not None" in surrounding, (
            f"{key} not conditionally guarded on short_signal is not None"
        )


def test_call_site_passes_the_short_signals_batch_lookup():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass
    _short_signals.get(stock.id) — the SAME batch-queried SHORT-horizon signal map the
    T215/T222-B gate itself reads a few lines earlier in the loop — not a re-fetch."""
    assert "short_signal=_short_signals.get(stock.id)" in _pte_source


def test_short_signal_param_exists_on_call_decision_engine_signature():
    """The function signature itself must declare short_signal as an optional keyword
    parameter defaulting to None, matching the established pattern for every other
    gate-parity parameter (kscore, ta_score, confidence_delta, index_return_pct, sig_ref_price)."""
    sig_start = _pte_source.index("def _call_decision_engine(")
    sig_end = _pte_source.index(") -> tuple[bool, str, int, str | None] | None:", sig_start)
    signature = _pte_source[sig_start:sig_end]
    assert "short_signal: str | None = None" in signature

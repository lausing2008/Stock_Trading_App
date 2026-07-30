"""Tests for T232-DL-DUALSCORER-DEBT — threading T196's price-drift gate (sig_ref_price /
max_price_drift_pct) from paper_trading_engine.py's _scan_for_entries() into decision-engine's
config_overrides.

Deliberately does NOT reuse sig.reasons["last_price"] the way T171's premarket-gap gate does —
that value is a frozen snapshot captured at signal-GENERATION time (signal-engine's own daily-
close read), while T196's own _sig_ref_prices lookup is a fresh, as-of-the-signal's-own-date
Price query re-derived every scan cycle. These were verified to genuinely diverge whenever a
candidate is evaluated in a LATER refresh cycle than the one that generated its signal — not a
hypothetical risk, since _scan_for_entries evaluates fresh buy_signals every cycle with no
guarantee a given signal was generated in that same cycle. This is therefore a genuine
write-side change (like index_return_pct), not a free port (like min_kscore/HK-flow/low-volume,
whose values were already flowing to decision-engine somewhere).

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, matching test_index_trend_config_wiring.py's established technique.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_sig_ref_price_is_threaded_into_config_overrides():
    """The exact fix: sig_ref_price (the measured reference close) and max_price_drift_pct
    (the threshold) must both actually be included in the config_overrides dict sent to
    decision-engine, not just exist in _scan_for_entries' own T196 pre-filter with nothing
    downstream reading them."""
    assert '"sig_ref_price":' in _decision_body
    assert '"max_price_drift_pct":' in _decision_body


def test_max_price_drift_pct_falls_back_to_the_real_default_of_3_pct():
    """The write side's fallback must match _scan_for_entries' own real fallback
    (cfg.get("max_price_drift_pct", 3.0)) exactly — not a differently-valued literal that
    would silently diverge from the upstream T196 pre-filter."""
    start = _decision_body.index('"max_price_drift_pct":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert 'cfg.get("max_price_drift_pct", 3.0)' in line


def test_sig_ref_price_and_threshold_are_conditional_on_sig_ref_price_being_present():
    """Both keys must only be sent when a real sig_ref_price value is also being computed —
    sending a threshold with no measured reference price to compare against would be
    meaningless, matching the existing conditional-inclusion pattern already used for every
    other gate ported this session."""
    for key in ('"sig_ref_price":', '"max_price_drift_pct":'):
        start = _decision_body.index(key)
        surrounding = _decision_body[max(0, start - 400):start + 100]
        assert "sig_ref_price is not None" in surrounding, (
            f"{key} not conditionally guarded on sig_ref_price is not None"
        )


def test_call_site_passes_a_fresh_sig_ref_prices_lookup_not_the_frozen_reasons_snapshot():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass
    _sig_ref_prices.get(stock.id) — the SAME fresh, per-candidate T196 reference lookup the
    gate itself uses a few lines earlier in the loop — not reasons["last_price"] or any other
    stale/frozen value."""
    assert "sig_ref_price=_sig_ref_prices.get(stock.id)" in _pte_source


def test_call_site_does_not_reuse_reasons_last_price_for_sig_ref_price():
    """Guards against a regression where a future edit swaps the fresh _sig_ref_prices lookup
    for the frozen reasons["last_price"] snapshot T171 already uses — the two were verified to
    genuinely diverge across refresh cycles, so this substitution would silently reintroduce
    the exact staleness risk this port was designed to avoid."""
    call_site_start = _pte_source.index("sig_ref_price=_sig_ref_prices.get(stock.id)")
    line_end = _pte_source.index("\n", call_site_start)
    line = _pte_source[call_site_start:line_end]
    assert 'reasons["last_price"]' not in line
    assert "reasons.get(\"last_price\")" not in line


def test_sig_ref_price_param_exists_on_call_decision_engine_signature():
    """The function signature itself must declare sig_ref_price as an optional keyword
    parameter defaulting to None, matching the established pattern for every other
    gate-parity parameter (kscore, ta_score, confidence_delta, index_return_pct)."""
    sig_start = _pte_source.index("def _call_decision_engine(")
    sig_end = _pte_source.index(") -> tuple[bool, str, int, str | None] | None:", sig_start)
    signature = _pte_source[sig_start:sig_end]
    assert "sig_ref_price: float | None = None" in signature

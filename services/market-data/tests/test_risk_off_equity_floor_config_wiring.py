"""Tests for T232-DL-DUALSCORER-DEBT — threading T226-A's risk_off hard block and T201's
equity-floor circuit breaker from paper_trading_engine.py into decision-engine's
config_overrides/DecisionRequest.

Both are portfolio-wide safety gates the fallback engine (_scan_for_entries) already
enforces, with zero equivalent in decision-engine's hard_rejects.py before this fix:

- risk_off hard block (T226-A): regime_state was ALREADY sent to decision-engine on every
  real call (used for the T190 R:R-stiffening check) — a genuinely free port, just the
  regime_risk_off_gate config + time-boxed override needed threading.
- equity floor (T201): `equity` was already sent (used by sizer.py's illustrative
  position-sizing preview), but `initial_capital` was never sent at all — a genuine new
  DecisionRequest field, not a free port, since the ratio needs both values.

paper_trading_engine.py can't be imported directly in this test environment (its import
chain pulls in apscheduler/db.models) — tested via source-text extraction, matching
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


# ── T226-A: risk_off hard block ──────────────────────────────────────────────

def test_regime_risk_off_gate_is_threaded_into_config_overrides():
    assert '"regime_risk_off_gate":' in _decision_body


def test_regime_risk_off_gate_falls_back_to_true_matching_the_real_default():
    """T226-A's own default in _scan_for_entries (cfg.get("regime_risk_off_gate", True)) —
    the write side's fallback must match exactly, since True is the safety-conscious default
    (the gate blocks unless explicitly disabled)."""
    start = _decision_body.index('"regime_risk_off_gate":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert 'cfg.get("regime_risk_off_gate", True)' in line


def test_regime_risk_off_gate_is_sent_unconditionally_not_gated_on_a_presence_check():
    """Unlike per-candidate values (kscore, ta_score, sig_ref_price), regime_risk_off_gate
    must be sent on EVERY call, matching regime_state's own always-sent convention — there's
    no "absent" state for a boolean config flag the way there is for an optional measured
    value, so gating it on a None-check would be wrong."""
    start = _decision_body.index('"regime_risk_off_gate":')
    surrounding = _decision_body[max(0, start - 50):start]
    assert "**(" not in surrounding[-3:], (
        "regime_risk_off_gate should not be wrapped in a conditional-inclusion dict-spread"
    )


def test_regime_risk_off_override_until_is_conditionally_included():
    """The override timestamp must only be sent when one is actually configured — an absent
    override must not send an empty/None value that could be misread as a real override."""
    start = _decision_body.index('"regime_risk_off_override_until":')
    surrounding = _decision_body[max(0, start - 200):start + 200]
    assert 'cfg.get("regime_risk_off_override_until")' in surrounding
    assert "**(" in surrounding  # confirms the conditional dict-spread pattern, not a bare send


# ── T201: equity-floor circuit breaker ───────────────────────────────────────

def test_initial_capital_is_threaded_into_the_request_body():
    assert '"initial_capital":' in _decision_body


def test_initial_capital_falls_back_to_equity_when_absent():
    """Matches DecisionRequest's own default semantics of 'no real portfolio context' —
    equity/equity = 1.0 (a 100% ratio) means the equity-floor gate can never fire for a
    caller with no real portfolio to reference, rather than crashing or defaulting to some
    arbitrary literal."""
    start = _decision_body.index('"initial_capital":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert "initial_capital if initial_capital is not None else equity" in line


def test_call_decision_engine_accepts_an_initial_capital_parameter():
    sig_start = _pte_source.index("def _call_decision_engine(")
    sig_end = _pte_source.index(") -> tuple[bool, str, int, str | None] | None:", sig_start)
    sig = _pte_source[sig_start:sig_end]
    assert "initial_capital: float | None = None" in sig


def test_real_call_site_passes_portfolio_initial_capital():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass the
    real portfolio's own initial_capital — not a hardcoded/omitted value, which would make
    the equity-floor gate a permanent no-op on the actual production path.

    Scoped specifically to the `de_result = _call_decision_engine(...)` call block — a bare
    substring search for "initial_capital=portfolio.initial_capital" anywhere in the file
    would also match an unrelated, pre-existing occurrence elsewhere (the equity-floor
    circuit breaker's own log call), which would silently pass even if THIS specific call
    site's argument were removed."""
    call_start = _pte_source.index("de_result = _call_decision_engine(")
    call_end = _pte_source.index(")\n", call_start)
    call_block = _pte_source[call_start:call_end]
    assert "initial_capital=portfolio.initial_capital" in call_block

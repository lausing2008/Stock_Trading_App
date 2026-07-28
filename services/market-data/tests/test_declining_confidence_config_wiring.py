"""Tests for T232-DL-DUALSCORER-DEBT — threading T202's declining-confidence gate
(max_confidence_decline / confidence_delta) from paper_trading_engine.py's _scan_for_entries()
pre-filter into decision-engine's config_overrides.

_scan_for_entries() already enforces a hard T202 pre-filter (confidence_delta <
cfg.get("max_confidence_decline", -8.0) -> skip entirely, before decision-engine is ever
called) — but decision-engine's own hard_rejects.py had no equivalent at all. This means
/decide/{symbol} could approve a candidate whose confidence is degrading, for any caller that
doesn't replicate the pre-filter itself (e.g. decide.tsx). Fixing the read side requires the
THRESHOLD itself, not just the candidate's confidence_delta value, to actually reach
config_overrides — this file guards the write side. Same shape as
test_min_kscore_config_wiring.py / test_min_ta_score_config_wiring.py's established pattern.

Unlike min_kscore/min_ta_score (positive floors), max_confidence_decline is a NEGATIVE
threshold and the gate blocks when the delta falls BELOW it, not above a floor — tests below
verify this sign is preserved through the wiring, not just that the keys are present.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, matching test_min_kscore_config_wiring.py's technique.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_confidence_delta_is_threaded_into_config_overrides():
    """The exact fix: confidence_delta (the candidate's own value) and
    max_confidence_decline (the threshold) must both actually be included in the
    config_overrides dict sent to decision-engine, not just exist in _scan_for_entries' own
    pre-filter with nothing downstream reading them."""
    assert '"confidence_delta":' in _decision_body
    assert '"max_confidence_decline":' in _decision_body


def test_max_confidence_decline_falls_back_to_the_real_default_of_negative_eight():
    """The write side's fallback must match _scan_for_entries' own real fallback
    (cfg.get("max_confidence_decline", -8.0)) exactly — not a differently-signed or
    differently-valued literal that would silently diverge from the upstream pre-filter."""
    start = _decision_body.index('"max_confidence_decline":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert 'cfg.get("max_confidence_decline", -8.0)' in line


def test_confidence_delta_and_threshold_are_conditional_on_confidence_delta_being_present():
    """Both keys must only be sent when a real confidence_delta value is also being computed —
    sending a threshold with no candidate value to compare it against would be meaningless,
    matching the existing conditional-inclusion pattern already used for kscore/min_kscore and
    ta_score/min_ta_score."""
    for key in ('"confidence_delta":', '"max_confidence_decline":'):
        start = _decision_body.index(key)
        surrounding = _decision_body[max(0, start - 400):start + 100]
        assert "confidence_delta is not None" in surrounding, (
            f"{key} not conditionally guarded on confidence_delta is not None"
        )


def test_confidence_delta_is_computed_from_the_sa26_trajectory_query_not_recomputed():
    """confidence_delta must be the SAME local variable T202's own declining-confidence gate
    already computes via its SA-26 prior-signal query — not a second, independently-derived
    value that could silently diverge from what that gate itself saw."""
    start = _pte_source.index("confidence_delta = round(float(sig.confidence)")
    preceding = _pte_source[max(0, start - 700):start]
    assert "prior_conf = session.execute(" in preceding
    assert "Signal.ts < sig.ts" in preceding


def test_call_site_passes_confidence_delta_as_the_same_local_variable():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass through
    the SAME confidence_delta local already computed earlier in this loop iteration — not a
    fresh query, matching how kscore_f/ta_score_f are threaded. This kwarg lives at the CALL
    site (inside _scan_for_entries), a different location than _decision_body (the function
    BODY of _call_decision_engine itself), so it's checked against the full source."""
    assert "confidence_delta=confidence_delta" in _pte_source

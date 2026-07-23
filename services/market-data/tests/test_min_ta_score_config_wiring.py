"""Tests for T232-DL-DUALSCORER-DEBT — threading min_ta_score from paper_trading_engine.py's
_scan_for_entries() pre-filter into decision-engine's config_overrides.

_scan_for_entries() already enforces a hard min_ta_score pre-filter (ta_score < cfg.get(
"min_ta_score", 0.0) -> skip entirely, before decision-engine is ever called) — but
decision-engine's own hard_rejects.py had no equivalent at all. This means /decide/{symbol}
could approve a candidate _scan_for_entries would have discarded before ever reaching a
scorer, for any caller that doesn't replicate the pre-filter itself (e.g. decide.tsx). Fixing
the read side (decision-engine's hard_rejects.py) requires the THRESHOLD itself, not just the
candidate's ta_score value, to actually reach config_overrides — this file guards the write
side. Same shape as test_min_kscore_config_wiring.py's own established pattern.

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


def test_ta_score_is_threaded_into_config_overrides():
    """The exact fix: ta_score (the candidate's own value) and min_ta_score (the threshold)
    must both actually be included in the config_overrides dict sent to decision-engine, not
    just exist in _scan_for_entries' own pre-filter with nothing downstream reading them."""
    assert '"ta_score":' in _decision_body
    assert '"min_ta_score":' in _decision_body


def test_min_ta_score_falls_back_to_cfg_get_zero_not_a_default_config_key():
    """min_ta_score has NO _DEFAULT_CONFIG entry anywhere in this file — it's only ever set
    via _STYLE_OVERRIDES (SWING=0.50) or _HK_MARKET_OVERRIDES (0.65). The read side's own
    fallback (_scan_for_entries, cfg.get("min_ta_score", 0.0)) is a bare 0.0, which disables
    the gate. The write side must match this EXACT fallback, not reference a
    _DEFAULT_CONFIG["min_ta_score"] key that doesn't exist (which would KeyError)."""
    start = _decision_body.index('"min_ta_score":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert 'cfg.get("min_ta_score", 0.0)' in line
    assert "_DEFAULT_CONFIG" not in line


def test_ta_score_and_min_ta_score_are_conditional_on_ta_score_being_present():
    """Both keys must only be sent when a real ta_score value is also being computed —
    sending a threshold with no candidate value to compare it against would be meaningless,
    matching the existing conditional-inclusion pattern already used for kscore/min_kscore."""
    for key in ('"ta_score":', '"min_ta_score":'):
        start = _decision_body.index(key)
        surrounding = _decision_body[max(0, start - 200):start + 100]
        assert "ta_score is not None" in surrounding, f"{key} not conditionally guarded on ta_score is not None"


def test_ta_score_f_is_computed_from_sig_reasons_before_the_call():
    """ta_score_f must be derived from the same sig.reasons dict the pre-existing TA-score
    hard-reject gate (T224-C/T225-A, earlier in _scan_for_entries) already reads from — not
    re-fetched from a different source that could silently diverge from what that gate saw."""
    start = _pte_source.index("ta_score_f = float(_ta_score_raw)")
    preceding = _pte_source[max(0, start - 200):start]
    assert '(sig.reasons or {}).get("ta_score")' in preceding

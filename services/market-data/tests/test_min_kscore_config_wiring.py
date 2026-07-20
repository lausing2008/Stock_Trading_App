"""Tests for T232-DL-DUALSCORER-DEBT — threading min_kscore from paper_trading_engine.py's
_scan_for_entries() pre-filter into decision-engine's config_overrides.

_scan_for_entries() already enforces a hard min_kscore pre-filter (Ranking.score < cfg["min_kscore"]
-> skip entirely, before decision-engine is ever called) — but decision-engine's own hard_rejects.py
had no equivalent, only a soft ±1 scoring layer for the ACTUAL kscore value (AUD232-042). This
means /decide/{symbol} could approve a candidate _scan_for_entries would have discarded before ever
reaching a scorer, for any caller that doesn't replicate the pre-filter itself (e.g. decide.tsx).
Fixing the read side (decision-engine's hard_rejects.py) requires the THRESHOLD itself, not just
the candidate's kscore value, to actually reach config_overrides — this file guards the write side.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, same technique as test_llm_scoring_config_wiring.py.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_min_kscore_is_threaded_into_config_overrides():
    """The exact fix: min_kscore must actually be included in the config_overrides dict sent
    to decision-engine, not just exist in _scan_for_entries' own pre-filter with nothing
    downstream reading the threshold itself."""
    assert '"min_kscore":' in _decision_body


def test_min_kscore_falls_back_to_the_real_default_config_value():
    """Must read from cfg (the merged per-portfolio/per-style config), falling back to
    _DEFAULT_CONFIG's own min_kscore — not a hardcoded literal that could silently drift from
    the real default used elsewhere in this same file."""
    assert '_DEFAULT_CONFIG["min_kscore"]' in _decision_body


def test_min_kscore_is_conditional_on_kscore_being_present():
    """min_kscore must only be sent when a real kscore value is also being sent — sending a
    threshold with no candidate value to compare it against would be meaningless, and matches
    the existing conditional-inclusion pattern already used for kscore itself and the
    llm_scoring_enabled block elsewhere in this same call."""
    start = _decision_body.index('"min_kscore":')
    # The conditional wrapping this key must reference `kscore is not None`, matching the
    # existing kscore inclusion's own guard one line above it.
    surrounding = _decision_body[max(0, start - 200):start + 100]
    assert "kscore is not None" in surrounding

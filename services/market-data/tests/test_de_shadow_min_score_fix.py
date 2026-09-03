"""Regression test for AUD-DECIDE2-SHADOWMINSCORE (Decision-Making deep audit, 2026-09-03):
_call_decision_engine() previously discarded decision-engine's own real, regime-adjusted
min_score from its HTTP response (DecisionResponse.min_score, computed by
min_score_for_regime()) — every caller instead recorded its OWN pre-call
cfg.get("min_entry_score", ...) as if it were decision-engine's verdict. Confirmed live in
production: a single symbol logged 2 different de_min_score values 12 seconds apart in
paper_trading_engine's own decision-log, neither matching decision-engine's real min_score for
that period — the /paper-portfolio/de-divergences "DE Audit" UI was rendering a systematically
wrong pass bar.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models) — matching test_de_shadow_comparison.py's own established
source-text-extraction convention for this exact file.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _extract(start_marker: str, end_marker: str) -> str:
    start = _pte_source.index(start_marker)
    end = _pte_source.index(end_marker, start)
    return _pte_source[start:end]


_CALL_DE_SOURCE = _extract("def _call_decision_engine(", "\ndef ")


def test_call_decision_engine_reads_the_real_min_score_from_the_response():
    assert 'de_min_score  = result.get("min_score")' in _CALL_DE_SOURCE


def test_call_decision_engine_returns_a_5_tuple_including_min_score():
    assert "return should_enter, verdict, score, blocked, de_min_score" in _CALL_DE_SOURCE


def test_primary_mode_unpacks_the_5_tuple_and_uses_the_real_min_score():
    """The exact bug: this call site previously passed cfg.get("min_entry_score", ...) — its
    OWN pre-call config value — as if it were decision-engine's verdict. Must now prefer the
    real de_min_score from the response, falling back to cfg only when the response omitted it."""
    section = _extract(
        'if de_mode == "primary":',
        "else:\n            # Legacy shadow mode",
    )
    assert "should_enter, de_verdict, score, de_blocked, de_min_score = de_result" in section
    assert "de_min_score if de_min_score is not None else cfg.get(" in section


def test_legacy_shadow_mode_also_unpacks_the_5_tuple_and_uses_the_real_min_score():
    section = _extract(
        "# Legacy shadow mode: _should_enter() decides",
        "\n        if not should_enter:",
    )
    assert "_, de_verdict, de_score, de_blocked, de_min_score = de_result" in section
    assert "de_min_score if de_min_score is not None else cfg.get(" in section


def test_no_call_site_still_passes_the_bare_pre_call_cfg_value_unconditionally():
    """Regression guard: neither call site should regress back to unconditionally passing
    cfg.get("min_entry_score", ...) as decision-engine's own verdict without at least
    preferring a real de_min_score when the response provided one."""
    primary_section = _extract('if de_mode == "primary":', "else:\n            # Legacy shadow mode")
    legacy_section = _extract("# Legacy shadow mode: _should_enter() decides", "\n        if not should_enter:")
    for section in (primary_section, legacy_section):
        # The old bug's exact call shape: the 4th positional arg to _record_de_shadow_comparison
        # was the bare cfg.get(...) with no de_min_score preference at all.
        assert "de_verdict, score, cfg.get(" not in section
        assert "de_verdict, de_score, cfg.get(" not in section

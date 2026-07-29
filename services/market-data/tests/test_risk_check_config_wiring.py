"""Tests for CLAUDE-API-COST-AUDIT — threading risk_check_enabled from paper portfolio config
into decision-engine's config_overrides, and into paper_portfolio.py's allowed_keys.

risk_check_enabled (decision-engine's risk_agent.py, T258-WHATCOULDGOWRONG-AGENT) had the
EXACT same gap T203-LLMWIRE fixed for llm_scoring_enabled: decision-engine's own routes.py
already reads cfg.get("risk_check_enabled", False), but nothing in paper_trading_engine.py
ever included it in the config_overrides dict sent to POST /decide/{symbol} — a
built-but-dormant opt-in with no way to turn it on for any real portfolio — and no
allowed_keys entry in paper_portfolio.py's /configure endpoint either (silently dropped as an
"unknown key" if anyone tried to set it via the API directly). Found while building the Admin
AI Assistant Features page, which needs this wiring to actually work end-to-end.

Both files can't be imported directly in this test environment — tested via source-text
extraction, matching test_llm_scoring_config_wiring.py's established technique exactly.
"""
import pathlib

_pp_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_pp_source = _pp_path.read_text()

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _load_configure_portfolio_body():
    start = _pp_source.index('def configure_portfolio(')
    end = _pp_source.index("\n\n\n@router.post", start)
    return _pp_source[start:end]


_configure_body = _load_configure_portfolio_body()


def test_risk_check_enabled_is_in_allowed_keys():
    """The exact regression this guards against: setting risk_check_enabled via
    POST /configure would previously be silently dropped as an unrecognized key."""
    assert '"risk_check_enabled"' in _configure_body


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_risk_check_enabled_is_threaded_into_config_overrides():
    """The exact fix: risk_check_enabled must actually be included in the config_overrides
    dict sent to decision-engine, not just exist in the portfolio's own cfg dict with
    nothing downstream reading it."""
    assert '"risk_check_enabled":' in _decision_body


def test_risk_check_enabled_is_conditional_on_the_portfolio_flag():
    """Must only be sent (as True) when the portfolio's own cfg.get('risk_check_enabled') is
    truthy — matching llm_scoring_enabled's exact conditional-inclusion pattern, so a
    portfolio that never opted in never sends the key at all."""
    start = _decision_body.index('"risk_check_enabled":')
    surrounding = _decision_body[max(0, start - 150):start + 150]
    assert 'cfg.get("risk_check_enabled")' in surrounding


def test_risk_check_enabled_comes_after_llm_scoring_enabled_block():
    """Both opt-in AI-feature flags should sit together in config_overrides for
    discoverability — not a strict requirement, but confirms this wasn't accidentally
    inserted somewhere unrelated in the dict."""
    llm_idx = _decision_body.index('"llm_scoring_enabled": True')
    risk_idx = _decision_body.index('"risk_check_enabled":')
    assert llm_idx < risk_idx

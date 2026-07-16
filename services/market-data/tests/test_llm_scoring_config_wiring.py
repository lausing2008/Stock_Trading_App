"""Tests for T203-LLMWIRE — threading llm_scoring_enabled/llm_score_weight/llm_model from
paper portfolio config into decision-engine's config_overrides.

decision-engine's llm_scorer.py has supported llm_scoring_enabled since T203, but nothing
in paper_trading_engine.py ever included it in the config_overrides dict sent to
POST /decide/{symbol} — a built-but-dormant feature with no way to turn it on for any real
portfolio, and no allowed_keys entry in paper_portfolio.py's /configure endpoint either
(silently dropped as an "unknown key" if anyone tried to set it via the API directly).

paper_portfolio.py can't be imported directly in this test environment (its import chain
pulls in db.models as a real package, which the stubbed conftest.py doesn't provide) — the
range-check logic is tested via source-text extraction, same technique as
test_price_alert_price_check.py.
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


# ── allowed_keys / validation wiring ────────────────────────────────────────────

def test_llm_config_keys_are_in_allowed_keys():
    """The exact regression this guards against: setting llm_scoring_enabled via
    POST /configure would previously be silently dropped as an unrecognized key."""
    for key in ("llm_scoring_enabled", "llm_score_weight", "llm_model"):
        assert f'"{key}"' in _configure_body, f"{key} missing from allowed_keys"


def test_llm_score_weight_has_a_bounded_range_check():
    """llm_score_weight must be range-checked (1-5) — an unbounded weight would let a
    single LLM verdict dominate the ~0-10 point entry score entirely."""
    assert '"llm_score_weight": (1, 5,' in _configure_body


# ── config_overrides threading in paper_trading_engine.py ─────────────────────

def _decision_call_body():
    start = _pte_source.index('de_url = _gs_de().decision_engine_url')
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_llm_scoring_enabled_is_threaded_into_config_overrides():
    """The exact fix: llm_scoring_enabled/llm_score_weight must actually be included in the
    config_overrides dict sent to decision-engine, not just exist in the portfolio config
    with nothing reading it."""
    assert '"llm_scoring_enabled": True' in _decision_body
    assert '"llm_score_weight": cfg.get("llm_score_weight"' in _decision_body


def test_llm_config_is_conditional_on_the_enabled_flag_not_always_sent():
    """llm_scoring_enabled/llm_score_weight must only be included when
    cfg.get("llm_scoring_enabled") is truthy — always sending llm_scoring_enabled: True
    regardless of the portfolio's own setting would silently turn this on for every
    portfolio, including ones that never opted in."""
    assert 'if cfg.get("llm_scoring_enabled") else {}' in _decision_body

"""Tests for T232-DL-DUALSCORER-DEBT — threading T221-B's market cluster cap
(market_open_count / max_market_positions) from paper_trading_engine.py's _scan_for_entries()
into decision-engine's config_overrides.

HK stocks are highly correlated — a market-wide down day stops out all positions in that market
simultaneously — so _scan_for_entries() blocks ALL new entries once a portfolio is at its
per-market position cap, rather than scoring individual candidates. This is genuinely
per-portfolio state, but the count itself (_mkt_open_count) is computed ONCE per scan cycle,
unconditionally (no enclosing if-block, unlike T221-E's heat brake), before the early-return
check and before the per-candidate loop begins — the same "single-portfolio count computed
once" shape as recent_win_rate/consec_losses/recent_stop_count, which were already threaded
through successfully. No hoisting fix was needed here (unlike heat brake's _recent_stops),
since _mkt_open_count is never scoped inside a conditional in the first place.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — tested via
source-text extraction, matching test_heat_brake_config_wiring.py's established technique.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _decision_call_body():
    start = _pte_source.index("de_url = _gs_de().decision_engine_url")
    end = _pte_source.index("\n        if r.status_code", start)
    return _pte_source[start:end]


_decision_body = _decision_call_body()


def test_market_open_count_is_threaded_into_config_overrides():
    """The exact fix: market_open_count (the measured per-market open-position count) and
    max_market_positions (the threshold) must both actually be included in the
    config_overrides dict sent to decision-engine, not just exist in _scan_for_entries' own
    T221-B pre-filter with nothing downstream reading them."""
    assert '"market_open_count":' in _decision_body
    assert '"max_market_positions":' in _decision_body


def test_max_market_positions_falls_back_to_the_real_default_config_value():
    """The write side's fallback must match _scan_for_entries' own real fallback
    (cfg.get("max_market_positions", 4), i.e. _DEFAULT_CONFIG["max_market_positions"]) exactly
    — not a differently-valued literal that would silently diverge from the upstream T221-B
    pre-filter."""
    start = _decision_body.index('"max_market_positions":')
    line_end = _decision_body.index("\n", start)
    line = _decision_body[start:line_end]
    assert '_DEFAULT_CONFIG["max_market_positions"]' in line


def test_default_config_max_market_positions_is_4():
    """Cross-check: the fallback referenced above must actually resolve to the real, documented
    default (4 positions) — not a stale/drifted value."""
    assert '"max_market_positions":      4,' in _pte_source


def test_market_open_count_and_threshold_are_conditional_on_market_open_count_being_present():
    """Both keys must only be sent when a real market_open_count value is also being computed —
    sending a threshold with no measured open-count to compare against would be meaningless,
    matching the existing conditional-inclusion pattern already used for every other gate
    ported this session. The `if ... is not None else {}` guard closes the dict-spread
    expression AFTER both keys, so the guard is searched for FORWARD from each key, not
    backward."""
    for key in ('"market_open_count":', '"max_market_positions":'):
        start = _decision_body.index(key)
        following = _decision_body[start:start + 500]
        assert "market_open_count is not None" in following, (
            f"{key} not conditionally guarded on market_open_count is not None"
        )


def test_call_site_passes_the_computed_mkt_open_count_variable():
    """The real _call_decision_engine() call site inside _scan_for_entries() must pass
    _mkt_open_count — the SAME variable the T221-B gate itself computes a few lines earlier in
    the function — not a re-fetch or a different derivation."""
    assert "market_open_count=_mkt_open_count" in _pte_source


def test_mkt_open_count_is_computed_unconditionally_before_the_early_return():
    """Unlike T221-E's heat brake (whose _recent_stops needed a hoisting fix since it was
    originally scoped inside an if-block), _mkt_open_count must be assigned OUTSIDE any
    conditional, strictly before the `if _mkt_open_count >= _max_mkt_pos:` early-return check
    — confirming no equivalent hoisting bug exists here, rather than assuming it from the
    heat-brake precedent alone."""
    assign_idx = _pte_source.index("_mkt_open_count = sum(1 for _, st in _prefetched_open if st.market == _mkt)")
    check_idx = _pte_source.index("if _mkt_open_count >= _max_mkt_pos:")
    assert assign_idx < check_idx, (
        "_mkt_open_count must be assigned BEFORE the `if _mkt_open_count >= _max_mkt_pos:` check"
    )


def test_market_open_count_param_exists_on_call_decision_engine_signature():
    """The function signature itself must declare market_open_count as an optional keyword
    parameter defaulting to None, matching the established pattern for every other
    gate-parity parameter (kscore, ta_score, confidence_delta, index_return_pct, sig_ref_price,
    recent_stop_count)."""
    sig_start = _pte_source.index("def _call_decision_engine(")
    sig_end = _pte_source.index(") -> tuple[bool, str, int, str | None] | None:", sig_start)
    signature = _pte_source[sig_start:sig_end]
    assert "market_open_count: int | None = None" in signature

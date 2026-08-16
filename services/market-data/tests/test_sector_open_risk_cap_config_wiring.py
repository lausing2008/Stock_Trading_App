"""Tests for T232-DL-DUALSCORER-DEBT — threading the sector dollar-exposure cap
(open_sector_value / max_sector_pct) and the open-risk cap (open_risk_total /
max_open_risk_pct) from paper_trading_engine.py's _scan_for_entries() into decision-engine's
config_overrides.

Both were previously present ONLY in _scan_for_entries' own fallback gate (the real, exact
dollar-value checks — see that function's own comment: "the real engine's dollar-exposure cap,
max_sector_pct, needs live per-position prices this endpoint never receives") — a genuine,
documented gap, not a free port (unlike min_kscore/HK-flow/low-volume, whose values were
already flowing to decision-engine somewhere). The candidate's own not-yet-sized contribution
(stop_distance/shares aren't computed until AFTER the decision-engine call in the real
function) is approximated on the DE side using max_position_pct/max_loss_per_trade_pct (both
already sent) as the same worst-case ceilings the real sizing logic itself caps against.

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


def test_open_sector_value_and_max_sector_pct_are_threaded_into_config_overrides():
    assert '"open_sector_value": open_sector_value' in _decision_body
    assert '"max_sector_pct": cfg.get("max_sector_pct", 0.25)' in _decision_body


def test_open_risk_total_and_max_open_risk_pct_are_threaded_into_config_overrides():
    assert '"open_risk_total": open_risk_total' in _decision_body
    assert '"max_open_risk_pct": cfg.get("max_open_risk_pct", 0.12)' in _decision_body


def test_both_new_fields_are_conditional_on_their_own_value_being_present():
    """Sending a threshold with no real aggregate to compare against would be meaningless —
    matches the existing conditional-inclusion pattern (min_kscore, sig_ref_price, etc.)."""
    for key, guard in (
        ('"open_sector_value": open_sector_value', "open_sector_value is not None"),
        ('"open_risk_total": open_risk_total', "open_risk_total is not None"),
    ):
        start = _decision_body.index(key)
        surrounding = _decision_body[start:start + 200]
        assert guard in surrounding, f"{key} not conditionally guarded on {guard!r}"


def test_call_decision_engine_signature_declares_both_new_optional_params():
    sig_start = _pte_source.index("def _call_decision_engine(")
    sig_end = _pte_source.index(") -> tuple[bool, str, int, str | None] | None:", sig_start)
    signature = _pte_source[sig_start:sig_end]
    assert "open_sector_value: float | None = None" in signature
    assert "open_risk_total: float | None = None" in signature


def test_real_call_site_passes_the_candidates_own_sector_value_not_a_different_sectors():
    """The call site must look up open_sector_value keyed on THIS candidate's own sector
    (stock.sector), not a hardcoded or unrelated sector — a wrong lookup here would silently
    gate every candidate against the wrong sector's exposure."""
    assert '_open_sector_values.get(stock.sector or "unclassified", 0.0)' in _pte_source


def test_real_call_site_passes_the_portfolio_wide_open_risk_total():
    assert "open_risk_total=_open_risk_total" in _pte_source


def test_open_sector_values_dict_is_built_once_per_cycle_before_the_candidate_loop():
    """_open_sector_values/_open_risk_total must be computed ONCE per scan cycle (from the
    already-prefetched open book), not re-derived per candidate — matches _open_sector_counts'
    own established once-per-cycle convention immediately above it in the same function."""
    build_idx = _pte_source.index("_open_sector_values: dict[str, float] = {}")
    counts_idx = _pte_source.index("_open_sector_counts: dict[str, int] = dict(_Counter(")
    call_site_idx = _pte_source.index("open_sector_value=_open_sector_values.get(")
    assert counts_idx < build_idx < call_site_idx


def test_open_sector_values_and_open_risk_total_are_derived_from_the_prefetched_open_book():
    """Must reuse the ALREADY-prefetched _prefetched_open list (no new per-candidate DB query)
    — matches the fallback gate's own AUD19-PERF2 discipline of eliminating N+1 queries for
    exactly this class of aggregate."""
    build_start = _pte_source.index("_open_sector_values: dict[str, float] = {}")
    build_end = _pte_source.index("_open_risk_total = sum(")
    build_end = _pte_source.index("\n", build_end)
    build_end = _pte_source.index("\n", build_end + 1)
    body = _pte_source[build_start:build_end]
    assert "_prefetched_open" in body
    assert "session.execute" not in body

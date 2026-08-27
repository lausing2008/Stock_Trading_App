"""Tests for T288-KSCORE-WEIGHT-SWEEP — the walk-forward validated sweep of K-Score's 6
factor weights (POST /rankings/tune_kscore_weights, GET /rankings/kscore_weights_status).

The pure candidate-generation / recompute / cross-sectional-EV functions
(_kscore_candidate_weight_sets, _kscore_recompute, _kscore_active_weights_for_row,
_kscore_cross_sectional_ev) take plain data (dicts, simple row-like objects) with zero
DB/session dependency, so they're imported and exercised directly — routes.py can be imported
in this test environment (db/sqlalchemy are stubbed as MagicMock(), which never raises on
attribute access), matching test_screener_signal_scoping.py's own documented precedent.

tune_kscore_weights() itself (the actual endpoint) has heavy DB/session dependencies
disproportionate to testing via a full functional exercise — its wiring (route registration
order, gate ordering, promotion criteria) is instead covered by source-text regression checks,
matching test_rank_symbol_market_scoping.py's / test_screener_signal_scoping.py's own
established proportionate-testing convention for this test suite.
"""
import pathlib
from dataclasses import dataclass
from datetime import date

from src.api.routes import (
    _kscore_active_weights_for_row,
    _kscore_candidate_weight_sets,
    _kscore_cross_sectional_ev,
    _kscore_recompute,
)

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()

_BASE_WEIGHTS = {
    "technical": 0.22, "momentum": 0.23, "value": 0.13,
    "growth": 0.14, "volatility": 0.18, "relative_strength": 0.10,
}


@dataclass
class _FakeRankingRow:
    id: int
    stock_id: int
    as_of: date
    technical: float
    momentum: float
    volatility: float
    value: float | None
    growth: float | None
    rs_score: float | None


def _row(rid=1, technical=60.0, momentum=60.0, volatility=60.0, value=60.0, growth=60.0, rs_score=60.0, as_of=None):
    return _FakeRankingRow(
        id=rid, stock_id=rid, as_of=as_of or date(2026, 1, 1),
        technical=technical, momentum=momentum, volatility=volatility,
        value=value, growth=growth, rs_score=rs_score,
    )


# ── _kscore_active_weights_for_row ─────────────────────────────────────────────────────────

def test_active_weights_excludes_none_value_and_renormalizes_the_rest_is_left_to_recompute():
    """_kscore_active_weights_for_row itself only excludes — recompute() does the renormalize.
    Confirms exclusion alone here."""
    row = _row(value=None)
    active = _kscore_active_weights_for_row(_BASE_WEIGHTS, row)
    assert "value" not in active
    assert set(active.keys()) == {"technical", "momentum", "growth", "volatility", "relative_strength"}


def test_active_weights_excludes_none_growth():
    row = _row(growth=None)
    active = _kscore_active_weights_for_row(_BASE_WEIGHTS, row)
    assert "growth" not in active


def test_active_weights_excludes_none_relative_strength():
    row = _row(rs_score=None)
    active = _kscore_active_weights_for_row(_BASE_WEIGHTS, row)
    assert "relative_strength" not in active


def test_active_weights_keeps_all_six_when_nothing_is_none():
    row = _row()
    active = _kscore_active_weights_for_row(_BASE_WEIGHTS, row)
    assert set(active.keys()) == set(_BASE_WEIGHTS.keys())


# ── _kscore_recompute ──────────────────────────────────────────────────────────────────────

def test_recompute_with_all_factors_matches_a_hand_computed_weighted_sum():
    row = _row(technical=80.0, momentum=60.0, volatility=40.0, value=20.0, growth=100.0, rs_score=50.0)
    expected = (
        0.22 * 80.0 + 0.23 * 60.0 + 0.13 * 20.0 + 0.14 * 100.0 + 0.18 * 40.0 + 0.10 * 50.0
    )
    assert abs(_kscore_recompute(_BASE_WEIGHTS, row) - expected) < 1e-9


def test_recompute_renormalizes_when_a_factor_is_none():
    """value=None must exclude the 0.13 weight entirely and renormalize the remaining 5
    weights to sum to 1.0 — not silently divide by the original (now-too-large) sum."""
    row = _row(technical=80.0, momentum=60.0, volatility=40.0, value=None, growth=100.0, rs_score=50.0)
    remaining_sum = 0.22 + 0.23 + 0.14 + 0.18 + 0.10
    expected = (
        0.22 / remaining_sum * 80.0 + 0.23 / remaining_sum * 60.0
        + 0.14 / remaining_sum * 100.0 + 0.18 / remaining_sum * 40.0 + 0.10 / remaining_sum * 50.0
    )
    assert abs(_kscore_recompute(_BASE_WEIGHTS, row) - expected) < 1e-9


def test_recompute_returns_none_when_every_factor_is_excluded():
    """A degenerate all-None candidate weight set (or a row with every optional factor
    missing AND a weight set that somehow zeroes the remaining ones) must fail safe to None,
    never a ZeroDivisionError."""
    degenerate_weights = {"technical": 0.0, "momentum": 0.0, "value": 0.0, "growth": 0.0, "volatility": 0.0, "relative_strength": 0.0}
    row = _row()
    assert _kscore_recompute(degenerate_weights, row) is None


# ── _kscore_candidate_weight_sets ──────────────────────────────────────────────────────────

def test_candidate_generation_produces_12_candidates_for_6_factors_perturbed_both_directions():
    candidates = _kscore_candidate_weight_sets(_BASE_WEIGHTS)
    assert len(candidates) == 2 * len(_BASE_WEIGHTS)


def test_every_candidate_still_sums_to_one():
    """Each weight is rounded to 4 decimals for readability/Redis-storage compactness, so the
    re-summed total can drift by a few ten-thousandths from exactly 1.0 — tolerance matches
    that real rounding precision, not an unreachably tight bound."""
    candidates = _kscore_candidate_weight_sets(_BASE_WEIGHTS)
    for cand in candidates:
        assert abs(sum(cand.values()) - 1.0) < 1e-3


def test_a_candidate_never_goes_negative_even_perturbed_down_from_a_small_weight():
    """relative_strength starts at 0.10, delta is 0.05 — a further-negative perturbation would
    need the 0.01 floor to kick in only for weights smaller than the delta itself. Confirm the
    floor never lets any weight go to or below zero, on the full real base weight set."""
    candidates = _kscore_candidate_weight_sets(_BASE_WEIGHTS)
    for cand in candidates:
        assert all(w > 0 for w in cand.values())


def test_perturbing_up_actually_increases_that_factors_relative_share():
    """A candidate that perturbs 'momentum' up must end with a LARGER momentum weight than the
    base set (post-renormalization it might not be exactly base + delta, but it must still be
    strictly greater — proves the perturb-then-renormalize order is correct, not accidentally
    cancelled out)."""
    candidates = _kscore_candidate_weight_sets(_BASE_WEIGHTS)
    momentum_up_candidates = [
        c for c in candidates
        if c["momentum"] > _BASE_WEIGHTS["momentum"]
    ]
    assert momentum_up_candidates, "expected at least one candidate with momentum perturbed upward"


# ── _kscore_cross_sectional_ev ─────────────────────────────────────────────────────────────

def test_cross_sectional_ev_picks_the_top_decile_by_recomputed_score_not_by_stock_order():
    """Construct 10 stocks on one day: 9 with a mediocre score/forward-return, 1 with the
    HIGHEST recomputed score AND a distinctly different forward return. The top-decile
    (n=1 of 10) average must equal exactly that one stock's forward return, proving the
    function actually ranks by score, not by insertion order."""
    rows_by_date = {
        date(2026, 1, 1): [
            _row(rid=i, technical=50.0, momentum=50.0, volatility=50.0, value=50.0, growth=50.0, rs_score=50.0)
            for i in range(1, 10)
        ] + [_row(rid=10, technical=99.0, momentum=99.0, volatility=99.0, value=99.0, growth=99.0, rs_score=99.0)]
    }
    forward_returns = {i: 0.01 for i in range(1, 10)}
    forward_returns[10] = 0.50  # the top-scoring stock's own distinct, much larger return
    stats = _kscore_cross_sectional_ev(
        rows_by_date, forward_returns, lambda row: _kscore_recompute(_BASE_WEIGHTS, row),
    )
    assert stats is not None
    assert abs(stats["ev_pct"] - 50.0) < 0.01  # 0.50 * 100, exactly the top stock's own return


def test_cross_sectional_ev_returns_none_when_no_day_has_enough_stocks():
    """A day with fewer than 3 scoreable stocks is skipped entirely (not enough to form a
    meaningful 'top decile') — if EVERY day in the slice is this thin, must return None, not a
    spurious EV from too few underlying data points."""
    rows_by_date = {date(2026, 1, 1): [_row(rid=1), _row(rid=2)]}
    forward_returns = {1: 0.05, 2: 0.05}
    assert _kscore_cross_sectional_ev(
        rows_by_date, forward_returns, lambda row: _kscore_recompute(_BASE_WEIGHTS, row),
    ) is None


def test_cross_sectional_ev_skips_rows_with_no_resolvable_forward_return():
    """A row whose id is absent from forward_return_by_id (not enough elapsed trading days
    yet) must be excluded from that day's ranking, not treated as a 0% return."""
    rows_by_date = {
        date(2026, 1, 1): [_row(rid=i) for i in range(1, 5)],
    }
    forward_returns = {1: 0.10, 2: 0.10, 3: 0.10}  # rid=4 deliberately has no forward return
    stats = _kscore_cross_sectional_ev(
        rows_by_date, forward_returns, lambda row: _kscore_recompute(_BASE_WEIGHTS, row),
    )
    assert stats is not None
    assert stats["n_scored"] <= 3  # never counts the unresolvable 4th row


def test_cross_sectional_ev_averages_across_multiple_days_not_just_the_last_one():
    rows_by_date = {
        date(2026, 1, 1): [_row(rid=i) for i in range(1, 5)],
        date(2026, 1, 2): [_row(rid=i) for i in range(5, 9)],
    }
    forward_returns = {**{i: 0.10 for i in range(1, 5)}, **{i: 0.30 for i in range(5, 9)}}
    stats = _kscore_cross_sectional_ev(
        rows_by_date, forward_returns, lambda row: _kscore_recompute(_BASE_WEIGHTS, row),
    )
    assert stats is not None
    assert stats["n_days"] == 2


# ── Source-text regression checks on tune_kscore_weights()'s wiring ────────────────────────
# Proportionate to this endpoint's heavy DB/session dependency — matches
# test_rank_symbol_market_scoping.py's own established precedent for functions in this file.

def test_kscore_weights_status_is_registered_before_the_symbol_catchall():
    """BUG233-ROUTERORDER's own documented bug class: a literal-path GET route registered
    AFTER a bare GET /{symbol} catch-all is silently shadowed by it. kscore_weights_status
    must appear before rank_symbol() in the file's source text."""
    status_idx = _ROUTES_SOURCE.index('def kscore_weights_status(')
    symbol_idx = _ROUTES_SOURCE.index('def rank_symbol(')
    assert status_idx < symbol_idx


def test_tune_endpoint_rejects_non_positive_ev_lift_unconditionally():
    """The unconditional 'never promote a non-positive lift' floor — matching every other
    sweep in this codebase's own established discipline (T232-OC3, tune_strategy)."""
    assert "if ev_lift <= 0:" in _ROUTES_SOURCE


def test_tune_endpoint_treats_an_unmeasurable_baseline_as_a_skip_not_an_assumed_zero():
    """T232-OC3 convention: no honest baseline on validation must skip, never assume EV=0
    (which would overstate the lift and apply too eagerly)."""
    assert '"baseline_unmeasurable_on_validation"' in _ROUTES_SOURCE


def test_tune_endpoint_records_tune_history_on_every_branch_including_rejections():
    """One TuneHistory row per attempt (promoted or not) — matching every sibling mechanism's
    own audit-trail discipline. Count every _record_kscore_tune_history( call site inside
    tune_kscore_weights()'s own body.

    Bound is EXACT (== 6), not >= 6: T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B added
    tune_kscore_curve() right after this function (also inside routes.py, also ending right
    before def refresh()) — its own body ALSO makes 6 _record_kscore_tune_history( calls.
    A loose >= bound would silently pass even if the end-boundary regressed back to the old
    "def refresh(" marker, since sweeping BOTH functions' calls together gives 12, which still
    clears >= 6 — verified directly (12 != 6) before tightening this to catch that exact class
    of boundary regression. Bound to the next @router.post decorator, which is exactly where
    tune_kscore_weights()'s own body actually ends."""
    start = _ROUTES_SOURCE.index("def tune_kscore_weights(")
    end = _ROUTES_SOURCE.index('@router.post("/tune_kscore_curve")', start)
    body = _ROUTES_SOURCE[start:end]
    assert body.count("_record_kscore_tune_history(") == 6  # every skip branch + the promoted branch


def test_tune_endpoint_only_writes_to_redis_after_all_validation_gates_pass():
    """The Redis setex write must be textually AFTER the ev_lift<=0 rejection check — otherwise
    a candidate could be written to Redis before being validated."""
    start = _ROUTES_SOURCE.index("def tune_kscore_weights(")
    ev_lift_check_idx = _ROUTES_SOURCE.index("if ev_lift <= 0:", start)
    redis_write_idx = _ROUTES_SOURCE.index(".setex(_KSCORE_WEIGHTS_REDIS_KEY", start)
    assert ev_lift_check_idx < redis_write_idx


def test_forward_return_lookup_uses_bar_index_offset_not_calendar_days():
    """The forward-return computation must be a bar-index offset into the same stock's own
    chronological close list (never a calendar-date arithmetic, which would need its own
    weekend/holiday handling) — matches gate_harness.py's own T196 precedent."""
    assert "fwd_idx = idx + _KSCORE_SWEEP_FORWARD_BARS" in _ROUTES_SOURCE

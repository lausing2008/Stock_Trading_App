"""Tests for T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B — the walk-forward validated sweep of
K-Score's 3 real curve-shape constants (#17 RSI breakpoints/slopes, #18 ADX-boost
normalization, #19 volatility scale factor). POST /rankings/tune_kscore_curve,
GET /rankings/kscore_curve_status.

The pure candidate-generation function (_kscore_curve_candidate_sets) takes plain data with
zero DB/session dependency, so it's imported and exercised directly.

_kscore_curve_raw_cache()/_kscore_curve_composite_fn() and tune_kscore_curve() itself all have
real DB/Session dependencies disproportionate to a full functional exercise — matching
test_kscore_weight_sweep.py's own established proportionate-testing convention for this exact
service, their wiring is instead covered by source-text regression checks.
"""
import pathlib

from src.api.routes import _kscore_curve_candidate_sets, _KSCORE_CURVE_SWEEP_DELTA

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()

_BASE_CURVE = {
    "rsi_low": 30.0, "rsi_mid": 50.0, "rsi_high": 70.0,
    "score_at_low": 50.0, "score_at_mid": 90.0, "score_at_high": 100.0,
    "rsi_overbought_decay_per_point": 2.5,
    "adx_center": 15.0, "adx_divisor": 25.0, "adx_boost_scale": 10.0,
    "volatility_scale": 1500.0,
}


# ── _kscore_curve_candidate_sets ────────────────────────────────────────────────────────────

def test_candidate_generation_produces_two_per_real_delta_key_perturbed_both_directions():
    candidates = _kscore_curve_candidate_sets(_BASE_CURVE)
    assert len(candidates) == 2 * len(_KSCORE_CURVE_SWEEP_DELTA)


def test_every_candidate_is_a_single_key_override_not_a_multi_key_grid():
    """The whole point of one-parameter-perturbed-at-a-time — a candidate must never vary more
    than one curve constant at once, or the sweep silently becomes an intractable joint grid."""
    candidates = _kscore_curve_candidate_sets(_BASE_CURVE)
    for cand in candidates:
        assert len(cand) == 1


def test_a_zero_valued_base_constant_produces_no_candidates_for_that_key():
    """abs(0) * pct == 0 has no meaningful relative step — must be skipped entirely, not
    produce two identical (unperturbed) candidates."""
    zeroed = {**_BASE_CURVE, "rsi_low": 0.0}
    candidates = _kscore_curve_candidate_sets(zeroed)
    assert not any("rsi_low" in c for c in candidates)
    # every OTHER key's own candidates must still be generated normally
    assert any("volatility_scale" in c for c in candidates)


def test_perturbing_up_and_down_produces_genuinely_different_values_for_the_same_key():
    candidates = _kscore_curve_candidate_sets(_BASE_CURVE)
    volatility_candidates = sorted(c["volatility_scale"] for c in candidates if "volatility_scale" in c)
    assert len(volatility_candidates) == 2
    assert volatility_candidates[0] < _BASE_CURVE["volatility_scale"] < volatility_candidates[1]


def test_every_real_delta_key_is_represented_in_the_generated_candidates():
    candidates = _kscore_curve_candidate_sets(_BASE_CURVE)
    touched_keys = {k for c in candidates for k in c}
    assert touched_keys == set(_KSCORE_CURVE_SWEEP_DELTA.keys())


# ── Source-text regression checks on tune_kscore_curve()'s wiring ──────────────────────────
# Proportionate to this endpoint's heavy DB/session dependency — matches
# test_kscore_weight_sweep.py's own established precedent for functions in this file.

def test_kscore_curve_status_is_registered_before_the_symbol_catchall():
    """BUG233-ROUTERORDER's own documented bug class: a literal-path GET route registered
    AFTER a bare GET /{symbol} catch-all is silently shadowed by it."""
    status_idx = _ROUTES_SOURCE.index('def kscore_curve_status(')
    symbol_idx = _ROUTES_SOURCE.index('def rank_symbol(')
    assert status_idx < symbol_idx


def test_tune_curve_endpoint_rejects_non_positive_ev_lift_unconditionally():
    """The unconditional 'never promote a non-positive lift' floor, scoped to tune_kscore_
    curve()'s own body — not just a bare substring check against the whole file, since
    tune_kscore_weights() has an identical-looking check for its own, separate sweep."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    assert "if ev_lift <= 0:" in body


def test_tune_curve_endpoint_treats_an_unmeasurable_baseline_as_a_skip_not_an_assumed_zero():
    """T232-OC3 convention: no honest baseline on validation must skip, never assume EV=0."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    assert '"baseline_unmeasurable_on_validation"' in body


def test_tune_curve_endpoint_records_tune_history_on_every_branch_including_rejections():
    """One TuneHistory row per attempt (promoted or not) — matching tune_kscore_weights()'s own
    audit-trail discipline. Bound is EXACT (== 6): every skip/reject/redis-failure branch plus
    the promoted branch, scoped strictly to this function's own body."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    assert body.count("_record_kscore_tune_history(") == 6


def test_tune_curve_endpoint_tags_every_tune_history_call_with_the_curve_parameter_class():
    """A regression this exact codebase's own history already flagged as a real, previously-
    unresolved gap: _record_kscore_tune_history()'s parameter_class defaults to
    'kscore_weights' (tune_kscore_weights()'s own value) — tune_kscore_curve() must explicitly
    override it at EVERY one of its 6 call sites, or its rows would be silently mistagged as
    weights-sweep rows in the TuneHistory audit trail, indistinguishable from the sibling
    sweep's own real attempts."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    assert body.count('parameter_class="kscore_curve"') == 6
    assert body.count('parameter_name="curve_shape"') == 6


def test_tune_curve_endpoint_never_leaves_a_call_site_on_the_weights_default():
    """The inverse of the above — every _record_kscore_tune_history( call inside
    tune_kscore_curve()'s body must be one of the 6 explicitly-overridden ones; none may
    silently fall through to the weights-sweep default by omission."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    end = None
    lines = _ROUTES_SOURCE[start:].split("\n")
    for i, l in enumerate(lines[1:], 1):
        if l.startswith("@router.") or (l.startswith("def ") and not l.startswith("def tune_kscore_curve(")):
            end = start + len("\n".join(lines[:i])) + 1
            break
    body = _ROUTES_SOURCE[start:end]
    assert body.count("_record_kscore_tune_history(") == body.count('parameter_class="kscore_curve"')


def test_tune_curve_endpoint_only_writes_to_redis_after_all_validation_gates_pass():
    """The Redis setex write must be textually AFTER the ev_lift<=0 rejection check — otherwise
    a candidate could be written to Redis before being validated."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    ev_lift_check_idx = body.index("if ev_lift <= 0:")
    redis_write_idx = body.index(".setex(_KSCORE_CURVE_REDIS_KEY")
    assert ev_lift_check_idx < redis_write_idx


def test_tune_curve_endpoint_uses_a_bar_index_forward_return_not_calendar_days():
    """Reuses the SAME _KSCORE_SWEEP_FORWARD_BARS bar-index offset tune_kscore_weights() itself
    uses (never a second, independently-derived offset) — matches gate_harness.py's own T196
    precedent for why calendar-day arithmetic must be avoided."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    assert "fwd_idx = idx + _KSCORE_SWEEP_FORWARD_BARS" in body


def test_tune_curve_endpoint_computes_the_expensive_raw_cache_exactly_once_not_per_candidate():
    """The whole point of the raw/mapping split — _kscore_curve_raw_cache( must be called
    exactly once per sweep run, BEFORE the candidate loop, never once per candidate (which
    would reintroduce the ~800s-per-sweep cost the raw/mapping split exists to avoid)."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    raw_cache_call_idx = body.index("raw_cache = _kscore_curve_raw_cache(")
    candidate_loop_idx = body.index("for cand in candidates:")
    assert raw_cache_call_idx < candidate_loop_idx
    assert body.count("_kscore_curve_raw_cache(") == 1


def test_tune_curve_endpoint_resolves_current_curve_via_the_live_override_not_hardcoded_defaults():
    """current_curve must come from _load_active_curve_params() (the live-resolution helper),
    never _CURVE_DEFAULTS directly — otherwise a sweep run after an earlier promotion would
    silently re-sweep from the ORIGINAL hardcoded values instead of the currently-live ones."""
    start = _ROUTES_SOURCE.index("def tune_kscore_curve(")
    body = _ROUTES_SOURCE[start:]
    assert "current_curve = _load_active_curve_params()" in body

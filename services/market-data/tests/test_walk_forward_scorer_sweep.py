"""Tests for T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group A's walk_forward_scorer_sweep()
(gate_harness.py) — the sweep over decision-engine's compute_score()/min_score_for_regime()
threshold constants (items #3, #8, #9, #10, #11, #12, #14), calling the REAL scoring path via
POST /decide/score-replay rather than a re-implementation.

gate_harness.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy/db wholesale) — this file uses source-text regression checks for the DB/HTTP-
dependent functions (matching test_walk_forward_calibration_feedback.py's own established
convention for this exact constraint), plus a direct, real exec() extraction of
_scorer_sweep_candidates() (pure — zero DB/network dependency, so it's genuinely testable
behaviorally rather than only via source-text presence checks) and _scorer_backtest_result()
(also pure — folds an already-fetched HTTP response list into a BacktestResult, no DB/network
of its own).
"""
import pathlib

_GH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
_GH_SOURCE = _GH_PATH.read_text()


def _sweep_function_body() -> str:
    start = _GH_SOURCE.index("def walk_forward_scorer_sweep(")
    return _GH_SOURCE[start:]  # last function in the file — safe to read to EOF


def _fetch_inputs_body() -> str:
    start = _GH_SOURCE.index("def _fetch_score_replay_inputs(")
    end = _GH_SOURCE.index("\ndef _score_replay_via_http(", start)
    return _GH_SOURCE[start:end]


def _http_caller_body() -> str:
    start = _GH_SOURCE.index("def _score_replay_via_http(")
    end = _GH_SOURCE.index("\ndef _scorer_backtest_result(", start)
    return _GH_SOURCE[start:end]


# ── _scorer_sweep_candidates(): real exec() extraction, pure/dependency-free ────────────────

def _load_candidates_fn():
    start = _GH_SOURCE.index("_SCORER_SWEEP_STEP = {")
    end = _GH_SOURCE.index("\ndef _fetch_score_replay_inputs(")
    ns: dict = {}
    exec(_GH_SOURCE[start:end], ns)
    return ns["_scorer_sweep_candidates"], ns["_SCORER_SWEEP_STEP"]


def test_candidates_each_vary_exactly_one_key_from_the_default():
    fn, _ = _load_candidates_fn()
    candidates = fn()
    for c in candidates:
        assert len(c) == 1


def test_candidates_cover_every_configured_key_in_both_directions():
    fn, step_table = _load_candidates_fn()
    candidates = fn()
    keys_seen = {k for c in candidates for k in c}
    assert keys_seen == set(step_table.keys())
    # every key should appear in (up to) 2 candidates — one +step, one -step
    for key in step_table:
        count = sum(1 for c in candidates if key in c)
        assert count in (1, 2)


def test_a_probability_bound_clamp_is_never_exceeded():
    """ml_bull_prob_*_threshold has lo=0.0/hi=1.0 — confirms the clamp is actually applied,
    not just declared in the step table with no effect."""
    fn, _ = _load_candidates_fn()
    candidates = fn()
    for c in candidates:
        for key, val in c.items():
            if key.startswith("ml_bull_prob_"):
                assert 0.0 <= val <= 1.0


def test_no_real_configured_key_currently_produces_a_value_equal_to_its_own_default():
    """None of the real, current _SCORER_SWEEP_STEP entries happen to clamp all the way back to
    their own default today — this test locks in that fact so a future step-table edit that
    DOES accidentally introduce one is caught (a candidate identical to the baseline would
    silently waste an HTTP round-trip testing 'the baseline vs. itself' with no real signal)."""
    fn, step_table = _load_candidates_fn()
    candidates = fn()
    for c in candidates:
        for key, val in c.items():
            default = step_table[key][0]
            assert val != default


def test_a_clamp_that_collapses_a_candidate_onto_the_default_is_dropped_not_emitted():
    """Direct, real behavioral proof of the guard itself — a synthetic step table specifically
    engineered so default+step clamps back to exactly the default (default=1.0, step=5.0,
    hi=1.0: 1.0+5.0=6.0, clamped to hi=1.0 == default) must NOT appear in the output at all.
    This is the property the sabotage-and-observe check found the real production step table
    can never exercise on its own — a synthetic table is required to actually test the guard."""
    fn, _ = _load_candidates_fn()
    synthetic_table = {"probe_key": (1.0, 5.0, 0.0, 1.0)}
    candidates = fn.__globals__["_SCORER_SWEEP_STEP"]
    original = dict(candidates)
    candidates.clear()
    candidates.update(synthetic_table)
    try:
        result = fn()
    finally:
        candidates.clear()
        candidates.update(original)
    # sign=+1 clamps to exactly 1.0 (== default, must be dropped); sign=-1 gives -4.0, clamped
    # to lo=0.0 (!= default 1.0, must survive).
    assert result == [{"probe_key": 0.0}]


# ── _fetch_score_replay_inputs(): source-text structural checks ─────────────────────────────

def test_fetch_inputs_reuses_the_same_pit_safe_helpers_replay_should_enter_already_uses():
    """Must reuse _historical_atr/_build_game_plan_for_style/_historical_confidence_delta/
    _historical_kscore — never a second, independently-drifting reconstruction of the same
    point-in-time-safe logic replay_should_enter() already has proven correct."""
    body = _fetch_inputs_body()
    assert "_historical_atr(session, stock.id, outcome.signal_date)" in body
    assert "_build_game_plan_for_style(stock.symbol, style, live_price, sig.reasons or {}, atr)" in body
    assert "_historical_confidence_delta(" in body
    assert "_historical_kscore(session, stock.id, outcome.signal_date)" in body


def test_fetch_inputs_skips_signals_with_no_usable_entry_price():
    """Matches replay_should_enter()'s own `if not live_price or live_price <= 0: continue`."""
    body = _fetch_inputs_body()
    assert "if not live_price or live_price <= 0:" in body
    assert "continue" in body


def test_fetch_inputs_forces_regime_state_neutral_not_a_reconstructed_value():
    """live_regime is the module's own disclosed permanent gap — must never silently invent a
    regime_state value from an unreliable substitute (e.g. signal-engine's own differently-
    scaled market_regime field, a documented wrong-vocabulary trap elsewhere in this file)."""
    body = _fetch_inputs_body()
    assert '"regime_state": "neutral",' in body


def test_fetch_inputs_never_sends_research_fields_it_cannot_reconstruct():
    """No historical research-report table exists to replay against — must stay None, not a
    fabricated/guessed value."""
    body = _fetch_inputs_body()
    assert '"research_rec": None,' in body
    assert '"research_score_val": None,' in body


# ── _score_replay_via_http(): source-text structural checks ─────────────────────────────────

def test_http_caller_batches_the_whole_window_not_one_request_per_signal():
    """The whole point of the batched endpoint design — one POST per candidate cfg carrying
    every input, not an N-round-trip loop. A per-signal httpx.post inside a `for input in
    inputs:` loop would defeat this entirely."""
    body = _http_caller_body()
    assert 'json={"inputs": chunk, "cfg": cfg}' in body
    # only ONE httpx.post call site in this function body (the chunking loop reuses it, never
    # a second, per-item call site).
    assert body.count("httpx.post(") == 1


def test_http_caller_authenticates_via_the_service_token():
    body = _http_caller_body()
    assert "_svc_token()" in body
    assert 'headers={"Authorization": f"Bearer {_svc_token()}"}' in body


def test_http_caller_never_raises_on_failure_returns_none_instead():
    """Matches _call_decision_engine()'s own contract — a candidate that fails the HTTP call
    must be treated as unmeasurable by the caller, never crash the whole sweep."""
    body = _http_caller_body()
    assert "except Exception:" in body
    assert "return None" in body


def test_http_caller_chunks_at_the_real_5000_input_request_cap():
    """ScoreReplayRequest.inputs caps at 5000 — must chunk, not silently truncate or crash on
    a window with more resolved signals than that."""
    body = _http_caller_body()
    assert "range(0, len(inputs), 5000)" in body


# ── walk_forward_scorer_sweep(): source-text orchestration checks ───────────────────────────

def test_sweep_pulls_window_end_back_by_the_style_resolution_lag_before_splitting():
    body = _sweep_function_body()
    assert "resolvable_end = _resolvable_window_end(window_end, style)" in body
    assert "if resolvable_end <= window_start:" in body


def test_sweep_uses_a_chronological_not_random_split():
    body = _sweep_function_body()
    assert "split_days = max(1, int(total_days * 0.7))" in body


def test_sweep_checks_train_slice_before_spending_the_validation_slice():
    """Must only fetch val_inputs / call the validation-slice HTTP requests AFTER confirming at
    least one candidate beat the baseline on the train slice."""
    body = _sweep_function_body()
    no_winner_idx = body.index("if best_candidate is None:")
    val_fetch_idx = body.index("val_inputs = _fetch_score_replay_inputs(")
    assert no_winner_idx < val_fetch_idx


def test_sweep_only_validates_the_single_best_train_slice_candidate_not_every_candidate():
    """Re-scoring every candidate on the validation slice would be both wasteful (an HTTP call
    per candidate per slice) and a real multiple-comparisons risk this function's own note
    already discloses avoiding by picking exactly one winner first."""
    body = _sweep_function_body()
    # exactly one candidate-branded HTTP call in the validation section (not one per candidate
    # in a loop) — confirmed by there being exactly one occurrence of cand_val's own construction.
    assert body.count("cand_val_results = _score_replay_via_http(val_inputs, cand_cfg)") == 1


def test_sweep_promotion_decision_uses_the_shared_promotion_margin_gate():
    body = _sweep_function_body()
    assert "promoted = _passes_promotion_margin(cand_val, baseline_val)" in body


def test_sweep_replays_baseline_and_candidate_against_the_same_validation_window():
    body = _sweep_function_body()
    assert (
        'baseline_val = _scorer_backtest_result(\n        style, market, "baseline (validation)", val_start, resolvable_end,'
        in body
    )
    assert "cand_val = _scorer_backtest_result(" in body


def test_sweep_note_discloses_this_is_research_only_not_a_live_config_change():
    body = _sweep_function_body()
    assert "does NOT change any live decision-engine config" in body


def test_sweep_note_discloses_the_freshness_layer_is_never_scored():
    """Item #4's own as_of-injection prerequisite is not built yet — the note must not silently
    imply Layer 3e (signal freshness) was swept along with the other 7 items."""
    body = _sweep_function_body()
    assert "signal freshness is never scored at all" in body


def test_sweep_baseline_train_uses_the_unmodified_base_cfg_with_no_override():
    """The train-slice baseline must be base_cfg itself, not base_cfg merged with any candidate
    — confirms candidates are only ever compared against the TRUE unmodified starting point."""
    body = _sweep_function_body()
    assert (
        "baseline_train_results = _score_replay_via_http(train_inputs, base_cfg)" in body
    )

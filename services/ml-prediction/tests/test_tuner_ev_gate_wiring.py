"""Source-text regression checks for T233-SELFIMPROVE-PHASE4's wiring inside tuner.py.

tuner.py can't be imported directly in this test environment — it pulls in optuna/xgboost/
sklearn plus DB access via _load_prices()/_load_best_params(), none of which are safely
exercisable in a unit test without either installing heavy ML deps or mocking so much of the
function that the test would no longer prove anything real. Matches this codebase's
established pattern for exactly this constraint (test_min_kscore_config_wiring.py,
test_regime_min_rr_config_wiring.py, test_tune_strategy.py's own wiring checks) — these
regression-guard the SHAPE of the wiring (holdout kept not discarded, gate runs before persist,
history always recorded) rather than the full numerical behavior (already covered directly and
behaviorally in test_ev_gate.py).
"""
import pathlib

_TUNER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "tuner.py"
_TUNER_SOURCE = _TUNER_PATH.read_text()


def _tune_symbol_body() -> str:
    start = _TUNER_SOURCE.index("def tune_symbol(")
    return _TUNER_SOURCE[start:]  # last top-level function in the file


def test_holdout_slice_is_kept_not_discarded():
    """The core regression this whole module exists to fix: the old code discarded the last
    15% of feature rows (`X, y_dir = X.iloc[:cutoff], y_dir.iloc[:cutoff]`) with no holdout
    kept at all. y_ret must now be captured from build_features() and the holdout slice
    (X_holdout/y_ret_holdout) must exist before the search-slice truncation."""
    body = _tune_symbol_body()
    assert "X, y_dir, y_ret = build_features(" in body, (
        "build_features()'s y_ret return value must be captured — it's silently discarded "
        "if the call still unpacks to `X, y_dir, _`"
    )
    assert "X_holdout, y_ret_holdout = X.iloc[cutoff:], y_ret.iloc[cutoff:]" in body
    # The holdout slice must be captured BEFORE X is truncated to the search slice, or it
    # would slice an already-shrunk X and silently produce garbage/empty holdout data.
    holdout_idx = body.index("X_holdout, y_ret_holdout = X.iloc[cutoff:]")
    truncate_idx = body.index("X, y_dir = X.iloc[:cutoff], y_dir.iloc[:cutoff]")
    assert holdout_idx < truncate_idx


def test_ev_gate_runs_before_persisting_params():
    """A rejected candidate must never reach the atomic params-file write or the retrain call —
    those are exactly the two side effects the gate exists to prevent for a losing candidate."""
    body = _tune_symbol_body()
    gate_call_idx = body.index("ev_gate_result = evaluate_candidate_ev(")
    persist_idx = body.index('with open(tmp, "w") as f:')
    retrain_idx = body.index("result = train_model(symbol,")
    assert gate_call_idx < persist_idx
    assert gate_call_idx < retrain_idx


def test_rejected_candidate_returns_before_persisting():
    """The `if not gate_promoted:` branch must `return` — not merely log — so a rejected
    candidate can't fall through to the persist/retrain code below it."""
    body = _tune_symbol_body()
    reject_idx = body.index("if not gate_promoted:")
    persist_idx = body.index('with open(tmp, "w") as f:')
    assert reject_idx < persist_idx
    reject_branch = body[reject_idx:persist_idx]
    assert "return {" in reject_branch


def test_tune_history_is_recorded_before_the_promotion_branch():
    """_record_tune_history() must fire regardless of promoted/rejected outcome — called once,
    before the `if not gate_promoted` branch splits into return-early vs. persist-and-continue,
    matching every other tuning mechanism's 'record every attempt' convention."""
    body = _tune_symbol_body()
    record_idx = body.index("_record_tune_history(")
    reject_idx = body.index("if not gate_promoted:")
    assert record_idx < reject_idx


def test_baseline_uses_current_live_params_not_a_fresh_default():
    """The baseline comparison must load the symbol's ACTUAL current live params
    (_load_best_params), not a hardcoded/default hyperparameter set — otherwise the gate could
    never meaningfully compare 'new vs. what's live now'."""
    body = _tune_symbol_body()
    assert "current_params = _load_best_params(symbol)" in body
    assert "if current_params else None" in body


def test_candidate_and_baseline_scored_on_the_identical_holdout_rows():
    """Both _fit_and_predict_holdout calls must receive the SAME X_holdout_arr — an
    apples-to-different-oranges comparison (different rows for each side) would make the EV
    lift meaningless."""
    body = _tune_symbol_body()
    assert "candidate_probs = _fit_and_predict_holdout(best_params, X_arr, y_arr, X_holdout_arr)" in body
    assert "_fit_and_predict_holdout(current_params, X_arr, y_arr, X_holdout_arr)" in body


def test_fit_and_predict_holdout_uses_the_same_weighting_convention_as_optunas_own_objective():
    """The refit used for the EV gate must use the identical recency/class-balance weighting
    (_recency_weights + _blend_weights) as objective()'s own per-fold fit — otherwise the gate
    would be comparing a differently-trained model than what Optuna actually searched over."""
    fn_start = _TUNER_SOURCE.index("def _fit_and_predict_holdout(")
    fn_end = _TUNER_SOURCE.index("\ndef _suggest(")
    fn_body = _TUNER_SOURCE[fn_start:fn_end]
    assert "_recency_weights(len(y_arr), newest_to_oldest_ratio=5.0)" in fn_body
    assert "_blend_weights(y_arr, recency_w)" in fn_body
    assert "sample_weight=w" in fn_body


def test_small_holdout_falls_back_to_cv_only_verdict_rather_than_crashing():
    """A too-small holdout must degrade gracefully (skip the gate, promote based on Optuna's
    own CV result) rather than let compute_holdout_ev's own MIN_HOLDOUT_SIGNALED_ROWS floor
    raise or silently divide-by-zero inside tune_symbol() itself."""
    body = _tune_symbol_body()
    assert "if len(X_holdout) < MIN_HOLDOUT_SIGNALED_ROWS:" in body
    guard_idx = body.index("if len(X_holdout) < MIN_HOLDOUT_SIGNALED_ROWS:")
    gate_call_idx = body.index("ev_gate_result = evaluate_candidate_ev(")
    assert guard_idx < gate_call_idx


def test_record_tune_history_writes_are_wrapped_in_try_except():
    """A DB write failure inside _record_tune_history must never abort tune_symbol() itself —
    tuning must still proceed even if the history table is briefly unreachable."""
    fn_start = _TUNER_SOURCE.index("def _record_tune_history(")
    fn_end = _TUNER_SOURCE.index("\ndef _fit_and_predict_holdout(")
    fn_body = _TUNER_SOURCE[fn_start:fn_end]
    assert "try:" in fn_body
    assert "except Exception" in fn_body


def test_record_tune_history_derives_market_from_symbol_suffix():
    fn_start = _TUNER_SOURCE.index("def _record_tune_history(")
    fn_end = _TUNER_SOURCE.index("\ndef _fit_and_predict_holdout(")
    fn_body = _TUNER_SOURCE[fn_start:fn_end]
    assert '"HK" if symbol.upper().endswith(".HK") else "US"' in fn_body


def test_load_best_params_is_actually_imported():
    """Regression guard: _load_best_params(symbol) is called at the EV-gate call site to load
    the current live params as the baseline, but was initially missing from tuner.py's own
    `from .trainer import ...` line — caught by pyflakes (undefined name), not by any test,
    before this guard existed. A missing import here would only surface as a real NameError
    the moment tune_symbol() actually runs past Optuna's search in production."""
    import_line_start = _TUNER_SOURCE.index("from .trainer import ")
    import_line_end = _TUNER_SOURCE.index("\n", import_line_start)
    import_line = _TUNER_SOURCE[import_line_start:import_line_end]
    assert "_load_best_params" in import_line

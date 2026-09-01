"""Tests for MPE-04's feature_ablation.py — the BASELINE/+SHORT/+OPTIONS/+SHORT+OPTIONS
holdout-EV comparison.

feature_ablation.py can't be imported directly in this local dev environment: its own real,
non-mockable dependencies (tuner.py's _fit_and_predict_holdout, trainer.py's _load_best_params/
_load_fund_snapshots/_load_options_snapshots/_load_prices/_load_fundamentals) need optuna
(not installed here) and a real DB session — unlike meta_trainer.py's own test precedent
(test_meta_trainer.py), which only needs ONE plain-dict attribute from its sibling module and
can stub the whole thing with a MagicMock, this module's real orchestration logic depends on
REAL functions it would be pointless to fake (faking them would just test the fakes, not the
real wiring). So:
  - _mask_columns() is a pure function (numpy/pandas only) — extracted via source-text and
    exercised directly with real DataFrames, matching test_analyst_pt_upside.py's own
    _extract_upside_guard_source() precedent for exactly this class of function.
  - run_feature_ablation()'s own orchestration (which columns get dropped for which group,
    that no group is ever silently promoted/written anywhere) is covered via source-text
    regression checks, matching test_load_options_snapshots.py's established pattern for
    functions in this same optuna/DB-dependent constraint class.
"""
import pathlib

import numpy as np
import pandas as pd

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "feature_ablation.py"
_SOURCE = _MODULE_PATH.read_text()


def _extract_mask_columns():
    start = _SOURCE.index("def _mask_columns(")
    end = _SOURCE.index("\n\ndef run_feature_ablation(", start)
    namespace = {"np": np, "pd": pd}
    exec(_SOURCE[start:end], namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_mask_columns"]


_mask_columns = _extract_mask_columns()


def test_mask_columns_replaces_the_named_columns_with_nan():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]})
    result = _mask_columns(df, ["b"])
    assert np.isnan(result[:, 1]).all()
    np.testing.assert_array_equal(result[:, 0], [1.0, 2.0, 3.0])
    np.testing.assert_array_equal(result[:, 2], [7.0, 8.0, 9.0])


def test_mask_columns_with_an_empty_drop_list_leaves_every_value_unchanged():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    result = _mask_columns(df, [])
    np.testing.assert_array_equal(result, df.values.astype(float))


def test_mask_columns_ignores_a_drop_name_not_present_in_the_dataframe():
    """A requested drop column that doesn't exist in X (e.g. OPTIONS_COLUMNS on a variant that
    never had them in the first place) must not raise — matching the `if col in X_masked.columns`
    guard's own defensive intent."""
    df = pd.DataFrame({"a": [1, 2, 3]})
    result = _mask_columns(df, ["nonexistent_column"])
    np.testing.assert_array_equal(result, df.values.astype(float))


def test_mask_columns_does_not_mutate_the_original_dataframe():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    _mask_columns(df, ["a"])
    assert not df["a"].isna().any(), "the original DataFrame passed in must be untouched"


def test_mask_columns_drops_multiple_columns_at_once():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6], "d": [7, 8]})
    result = _mask_columns(df, ["b", "d"])
    assert np.isnan(result[:, 1]).all()
    assert np.isnan(result[:, 3]).all()
    np.testing.assert_array_equal(result[:, 0], [1.0, 2.0])
    np.testing.assert_array_equal(result[:, 2], [5.0, 6.0])


# ── run_feature_ablation() orchestration — source-text regression checks ───────────────────

def test_baseline_group_drops_both_short_and_options_columns():
    """BASELINE must be missing BOTH feature groups — the point of comparison every other
    variant is measured against."""
    start = _SOURCE.index('_drop_map = {')
    end = _SOURCE.index("\n\n    results:", start)
    body = _SOURCE[start:end]
    assert '"baseline": SHORT_INTEREST_COLUMNS + OPTIONS_COLUMNS,' in body


def test_short_group_drops_only_options_columns_keeps_short_interest():
    start = _SOURCE.index('_drop_map = {')
    end = _SOURCE.index("\n\n    results:", start)
    body = _SOURCE[start:end]
    assert '"short": OPTIONS_COLUMNS,' in body


def test_options_group_drops_only_short_interest_columns_keeps_options():
    start = _SOURCE.index('_drop_map = {')
    end = _SOURCE.index("\n\n    results:", start)
    body = _SOURCE[start:end]
    assert '"options": SHORT_INTEREST_COLUMNS,' in body


def test_short_options_group_drops_nothing():
    """The full-feature variant — nothing ablated, matching what production actually uses
    today (both groups already live in FEATURE_COLUMNS)."""
    start = _SOURCE.index('_drop_map = {')
    end = _SOURCE.index("\n\n    results:", start)
    body = _SOURCE[start:end]
    assert '"short_options": [],' in body


def test_short_interest_columns_names_the_3_real_t204_features():
    assert 'SHORT_INTEREST_COLUMNS = ["short_ratio", "short_ratio_delta", "short_percent_of_float"]' in _SOURCE


def test_all_4_variants_use_the_same_params_not_a_fresh_search_each():
    """The whole point of the ablation study is isolating the feature-column effect — a
    per-variant hyperparameter re-search would confound that. All 4 calls to
    _fit_and_predict_holdout must pass the SAME `params` variable."""
    calls = [line for line in _SOURCE.splitlines() if "_fit_and_predict_holdout(params," in line]
    assert len(calls) == 1  # inside the for-loop, called once per iteration with the same `params`


def test_never_writes_to_any_model_file_or_redis_key():
    # A research/diagnostic tool, matching gate_harness.py's own read-only convention — must
    # never persist a model, params file, or Redis key as a side effect. Checked via the
    # actual dangerous CALL PATTERNS (a real .save(...)/joblib.dump(...)/get_redis()/.setex(
    # invocation), not a blanket substring-absence check across the whole file — this module's
    # own docstrings legitimately mention "Redis"/"save" in prose while explaining what it
    # does NOT do, the exact "matched the docstring, not the real code" trap this codebase's
    # own history has hit before.
    assert ".save(" not in _SOURCE
    assert "joblib.dump(" not in _SOURCE
    assert ".setex(" not in _SOURCE
    assert "get_redis(" not in _SOURCE
    assert "import redis" not in _SOURCE


def test_falls_back_to_default_params_when_no_tuned_params_exist():
    assert "params = _load_best_params(symbol) or _DEFAULT_PARAMS" in _SOURCE


def test_options_coverage_flag_is_computed_and_reported():
    """A symbol outside options_flow_snapshots' bounded coverage must be reported honestly
    (options_coverage: False), not silently compared as if real data existed."""
    assert "options_coverage = bool(X[OPTIONS_COLUMNS].notna().any().any())" in _SOURCE
    assert '"options_coverage": options_coverage,' in _SOURCE


def test_skips_rather_than_fabricates_below_the_300_sample_floor():
    """Matches tune_symbol()'s own established floor exactly — never a re-derived number."""
    assert "if len(X_train) < 300:" in _SOURCE

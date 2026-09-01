"""MPE-04: feature-ablation harness — does the SHORT-INTEREST feature group (short_ratio,
short_ratio_delta, short_percent_of_float — already live in FUNDAMENTAL_COLUMNS) and the new
OPTIONS-flow feature group (opt_cp_ratio, opt_whale_count — MPE-04's own new OPTIONS_COLUMNS)
actually pull their weight in the model, or could they be dropped with no real loss?

Per the Market Pressure Engine scoping doc's own disposition table: "this is genuinely new
work... it tells us whether the ALREADY-EXISTING short-interest ML features are pulling their
weight before any new feature is added on top." Scoped to a 2-group ablation (SHORT, OPTIONS),
not the original proposal's full margin-inclusive 8-cell grid — this app has no real margin/
leverage concept to build a MARGIN group from (a cash-only paper-trading platform, confirmed
directly via _open_paper_trade()'s own hard cash-only gate).

Reuses tuner.py's already-proven _fit_and_predict_holdout() (same scaling/weighting convention
as every other holdout-EV comparison in this codebase) and ev_gate.py's compute_holdout_ev()
(the same holdout-EV metric, direction-check-free here since this is a comparison across
feature groups, not a promotion gate deciding whether to overwrite a live model — see
run_feature_ablation()'s own docstring for why no group is ever auto-applied).

Deliberately does NOT run a fresh Optuna search per feature-group variant — that would be
4x the cost of a single tune for a diagnostic tool whose whole point is asking "does dropping
these columns matter," not finding the best possible model per variant. Uses the symbol's
already-tuned live hyperparameters when they exist (_load_best_params()), falling back to a
fixed, reasonable default set otherwise — the SAME params across all 4 variants, so any EV
difference is attributable to the feature columns themselves, not a confounded hyperparameter
search each variant.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from common.logging import get_logger

from ..features import OPTIONS_COLUMNS, build_features, compute_label_threshold, fetch_macro_features, fetch_sector_features, fetch_signal_outcome_features
from .ev_gate import compute_holdout_ev
from .trainer import _load_best_params, _load_fund_snapshots, _load_fundamentals, _load_options_snapshots, _load_prices
from .tuner import _fit_and_predict_holdout

log = get_logger("feature_ablation")

# The 3 SHORT-interest features already live in FUNDAMENTAL_COLUMNS (T204) — named explicitly
# here (not "every FUNDAMENTAL_COLUMNS entry") since the ablation study is specifically about
# THESE, not the whole fundamentals block (recommendation_mean, piotroski_score, etc. stay
# present in every variant, including BASELINE).
SHORT_INTEREST_COLUMNS = ["short_ratio", "short_ratio_delta", "short_percent_of_float"]

# Fixed default hyperparameters used ONLY when a symbol has no tuned params on file yet —
# matches tuner.py's own _SEARCH space's rough middle, not a re-derivation of what Optuna would
# find (this harness deliberately does not run Optuna per variant, see module docstring).
_DEFAULT_PARAMS = {
    "n_estimators": 300, "max_depth": 4, "learning_rate": 0.05,
    "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 5,
    "gamma": 0.0, "reg_alpha": 0.5, "reg_lambda": 1.5,
}

FEATURE_GROUPS = ("baseline", "short", "options", "short_options")


def _mask_columns(X: pd.DataFrame, drop_cols: list[str]) -> np.ndarray:
    """Return X's values with `drop_cols` forced to NaN — XGBoost handles NaN natively (every
    column already in this array is drawn from FEATURE_COLUMNS, all of which either tolerate
    NaN by design or were required non-null upstream in build_features()'s own row filter), so
    this is a clean, honest way to "remove" a feature group's INFORMATION without changing the
    array's shape (which _fit_and_predict_holdout() assumes stays fixed across calls sharing
    the same column ordering).
    """
    X_masked = X.copy()
    for col in drop_cols:
        if col in X_masked.columns:
            X_masked[col] = np.nan
    return X_masked.values


def run_feature_ablation(symbol: str, horizon: int = 5, style: str = "SWING") -> dict:
    """Fit the SAME model (same hyperparameters) 4 times against 4 different feature-column
    subsets of an IDENTICAL train/holdout split, and compare each variant's holdout EV. This is
    a research/diagnostic tool, not a promotion gate — it never writes to any model file or
    Redis key, matching gate_harness.py's own read-only `/backtest/*` convention exactly.
    Returns a comparison dict; a human (or a future automated policy built on TOP of this,
    should one ever be warranted) decides whether a feature group is worth its real API cost
    (options_flow_snapshots' own bounded-symbol-set coverage means the OPTIONS group is only
    ever measurable for the subset of symbols that table actually covers).

    Skipped (not fabricated) when either (a) fewer than 300 clean feature rows exist after the
    holdout split, matching tune_symbol()'s own floor, or (b) the OPTIONS columns are entirely
    NaN across the whole training+holdout window for this symbol (this table's bounded
    coverage means most symbols will hit this — reported explicitly as `options_coverage:
    false` rather than silently comparing a group against itself with zero real variation).
    """
    try:
        df = _load_prices(symbol)
    except ValueError as exc:
        log.warning("feature_ablation.skipped", symbol=symbol, reason=str(exc))
        return {"symbol": symbol, "skipped": True, "reason": str(exc)}

    macro_df = None
    start_date = None
    try:
        start_date = pd.to_datetime(df["ts"]).min().date()
        end_date = date.today() + timedelta(days=1)
        macro_df = fetch_macro_features(start_date, end_date, symbol=symbol)
    except Exception as exc:
        log.warning("feature_ablation.macro_features_failed", symbol=symbol, error=str(exc))
        end_date = date.today() + timedelta(days=1)

    sector_df = None
    if start_date is not None:
        try:
            sector_df = fetch_sector_features(symbol, start_date, end_date)
        except Exception as exc:
            log.warning("feature_ablation.sector_features_failed", symbol=symbol, error=str(exc))

    outcome_df = None
    if start_date is not None:
        try:
            outcome_df = fetch_signal_outcome_features(symbol, start_date, end_date)
        except Exception as exc:
            log.warning("feature_ablation.outcome_features_failed", symbol=symbol, error=str(exc))

    _thresh_cutoff = max(int(len(df) * 0.70), 60)
    label_threshold = compute_label_threshold(df.iloc[:_thresh_cutoff], horizon, symbol=symbol)

    fund_data: dict = {}
    try:
        fund_data = _load_fundamentals(symbol) or {}
    except Exception as exc:
        log.warning("feature_ablation.fundamentals_load_failed", symbol=symbol, error=str(exc))
    fund_data["_symbol"] = symbol

    fund_snapshots: list[dict] = []
    try:
        fund_snapshots = _load_fund_snapshots(symbol)
    except Exception as exc:
        log.warning("feature_ablation.fund_snapshots_load_failed", symbol=symbol, error=str(exc))

    options_snapshots: list[dict] = []
    try:
        options_snapshots = _load_options_snapshots(symbol)
    except Exception as exc:
        log.warning("feature_ablation.options_snapshots_load_failed", symbol=symbol, error=str(exc))

    X, y_dir, y_ret = build_features(
        df, horizon=horizon, macro_df=macro_df, label_threshold=label_threshold,
        fund_data=fund_data, sector_df=sector_df, outcome_df=outcome_df,
        fund_snapshots=fund_snapshots, options_snapshots=options_snapshots,
    )

    cutoff = int(len(X) * 0.85)
    X_holdout, y_ret_holdout = X.iloc[cutoff:], y_ret.iloc[cutoff:]
    X_train, y_train = X.iloc[:cutoff], y_dir.iloc[:cutoff]
    if len(X_train) < 300:
        reason = f"only {len(X_train)} clean samples (need >=300 for the ablation study)"
        log.warning("feature_ablation.skipped", symbol=symbol, reason=reason)
        return {"symbol": symbol, "skipped": True, "reason": reason}

    options_coverage = bool(X[OPTIONS_COLUMNS].notna().any().any())
    if not options_coverage:
        log.info("feature_ablation.no_options_coverage", symbol=symbol)

    params = _load_best_params(symbol) or _DEFAULT_PARAMS
    y_arr = y_train.values
    y_ret_holdout_arr = y_ret_holdout.values

    _drop_map = {
        "baseline": SHORT_INTEREST_COLUMNS + OPTIONS_COLUMNS,
        "short": OPTIONS_COLUMNS,
        "options": SHORT_INTEREST_COLUMNS,
        "short_options": [],
    }

    results: dict[str, dict] = {}
    for group in FEATURE_GROUPS:
        X_train_arr = _mask_columns(X_train, _drop_map[group])
        X_holdout_arr = _mask_columns(X_holdout, _drop_map[group])
        probs = _fit_and_predict_holdout(params, X_train_arr, y_arr, X_holdout_arr)
        results[group] = compute_holdout_ev(probs, y_ret_holdout_arr)

    return {
        "symbol": symbol,
        "skipped": False,
        "options_coverage": options_coverage,
        "n_train": len(X_train),
        "n_holdout": len(X_holdout),
        "results": results,
        "short_interest_lift_pct": (
            (results["short"]["ev_pct"] - results["baseline"]["ev_pct"])
            if results["short"]["ev_pct"] is not None and results["baseline"]["ev_pct"] is not None
            else None
        ),
        "options_lift_pct": (
            (results["options"]["ev_pct"] - results["baseline"]["ev_pct"])
            if results["options"]["ev_pct"] is not None and results["baseline"]["ev_pct"] is not None
            else None
        ),
    }

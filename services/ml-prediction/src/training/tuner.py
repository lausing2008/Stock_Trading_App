"""Optuna hyperparameter search for XGBoost using TimeSeriesSplit cross-validation.

Usage (via API):
  POST /ml/tune        {"symbol": "AAPL"}
  POST /ml/tune_all    — tunes every active symbol sequentially (background task)

For each symbol the tuner:
  1. Runs `n_trials` Optuna trials, each scored by mean TimeSeriesSplit AUC
  2. Saves best params to  {model_dir}/xgboost/{symbol}_params.json
  3. Retrains the final model with those best params so predictions update immediately
"""
from __future__ import annotations

import json
import os

import numpy as np
import optuna
from optuna.pruners import MedianPruner
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from common.logging import get_logger

from ..features import build_features, compute_label_threshold, fetch_macro_features
from .trainer import _blend_weights, _load_prices, _params_path, _recency_weights, train_model

log = get_logger("tuner")

optuna.logging.set_verbosity(optuna.logging.WARNING)

# Search space bounds
_SEARCH = {
    "n_estimators":    ("int",   100,  1000),
    "max_depth":       ("int",   2,    8),
    "learning_rate":   ("float", 0.005, 0.3,  True),   # log scale
    "subsample":       ("float", 0.5,  1.0),
    "colsample_bytree":("float", 0.5,  1.0),
    "min_child_weight":("int",   1,    30),
    "gamma":           ("float", 0.0,  2.0),
    "reg_alpha":       ("float", 0.0,  3.0),
    "reg_lambda":      ("float", 0.5,  5.0),
}


def _suggest(trial: optuna.Trial, name: str) -> int | float:
    spec = _SEARCH[name]
    kind = spec[0]
    if kind == "int":
        return trial.suggest_int(name, spec[1], spec[2])
    log_scale = len(spec) > 3 and spec[3]
    return trial.suggest_float(name, spec[1], spec[2], log=log_scale)


def tune_symbol(symbol: str, n_trials: int = 60, horizon: int = 5, style: str = "SWING") -> dict:
    """Run Optuna search for `symbol`, save best params, retrain final model.

    Returns a result dict with best_params, best_cv_auc, and final train metrics.
    Uses the same label_threshold and macro features as train_model for consistency.
    """
    from datetime import date, timedelta
    import pandas as pd

    log.info("tune.start", symbol=symbol, n_trials=n_trials)

    try:
        df = _load_prices(symbol)
    except ValueError as exc:
        log.warning("tune.skipped", symbol=symbol, reason=str(exc))
        return {"symbol": symbol, "skipped": True, "reason": str(exc)}

    # Fetch macro features (same as train_model for consistency)
    macro_df = None
    try:
        start_date = pd.to_datetime(df["ts"]).min().date()
        macro_df = fetch_macro_features(start_date, date.today() + timedelta(days=1))
    except Exception:
        pass

    label_threshold = compute_label_threshold(df, horizon)
    X, y_dir, _ = build_features(
        df, horizon=horizon, macro_df=macro_df, label_threshold=label_threshold
    )
    # Restrict tuner to first 85% of data to avoid leaking the test period
    cutoff = int(len(X) * 0.85)
    X, y_dir = X.iloc[:cutoff], y_dir.iloc[:cutoff]
    if len(X) < 300:
        reason = f"only {len(X)} clean samples (need ≥300 for tuning)"
        log.warning("tune.skipped", symbol=symbol, reason=reason)
        return {"symbol": symbol, "skipped": True, "reason": reason}

    X_arr = X.values
    y_arr = y_dir.values

    def objective(trial: optuna.Trial) -> float:
        params = {name: _suggest(trial, name) for name in _SEARCH}
        clf = XGBClassifier(
            **params,
            eval_metric="logloss",
            n_jobs=-1,
            random_state=42,
            tree_method="hist",
        )
        tscv = TimeSeriesSplit(n_splits=5)
        aucs: list[float] = []
        for fold, (tr_idx, val_idx) in enumerate(tscv.split(X_arr)):
            X_tr, X_val = X_arr[tr_idx], X_arr[val_idx]
            y_tr, y_val = y_arr[tr_idx], y_arr[val_idx]
            sc = StandardScaler()
            # ML-FIX-2 + ML-FIX-3: blended weights (recency × balanced class) in tuner too
            recency_w = _recency_weights(len(tr_idx), newest_to_oldest_ratio=5.0)
            w = _blend_weights(y_tr, recency_w)
            clf.fit(sc.fit_transform(X_tr), y_tr, sample_weight=w, verbose=False)
            preds = clf.predict_proba(sc.transform(X_val))[:, 1]
            if len(np.unique(y_val)) > 1:
                aucs.append(roc_auc_score(y_val, preds))

            # ML-FIX-3: report running mean so MedianPruner can kill weak trials early
            if aucs:
                trial.report(-float(np.mean(aucs)), step=fold)
                if trial.should_prune():
                    raise optuna.exceptions.TrialPruned()

        # Return a large positive value (bad objective) when no fold produced a valid AUC.
        # Optuna minimises, so 1.0 (= AUC of 0.0 inverted) is correctly worse than
        # any real model's objective (which is negative for AUC > 0).
        return -float(np.mean(aucs)) if aucs else 1.0

    # ML-FIX-3: MedianPruner kills trials that are below the median after 10 startup trials.
    # n_warmup_steps=2 means the first 2 folds are never pruned (not enough data for the pruner).
    pruner = MedianPruner(n_startup_trials=10, n_warmup_steps=2)
    study = optuna.create_study(
        direction="minimize",
        pruner=pruner,
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best_params = study.best_params
    best_cv_auc = -study.best_value

    # Persist best params — atomic write to avoid partial-read race with _load_best_params
    p = _params_path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(best_params, f, indent=2)
    os.replace(tmp, p)
    log.info("tune.best_params", symbol=symbol, cv_auc=round(best_cv_auc, 4), **best_params)

    # Retrain final model using best params (train_model will pick them up via _load_best_params)
    result = train_model(symbol, "xgboost", horizon, hyperparams=best_params, style=style)
    result["best_params"] = best_params
    result["best_cv_auc"] = round(best_cv_auc, 4)
    return result

"""T241-POSITION-SCALING Phase 3: position-scaling gate training pipeline.

Trains and validates the classifier that decides WHETHER to act on a candidate pullback-add
and HOW MUCH size to use, given the features assembled at each re-evaluation event. This sits
between the primary AI signal (already fired) and position sizing (Phase 5, not built yet). It
is trained on the output of Phase 2's triple_barrier_labeling.py.

Named "position-scaling gate" (NOT "meta-labeling gate") per the T241 design-review's resolved
naming decision — this codebase already has an unrelated "meta-model" (T89,
ml-prediction/src/training/meta_trainer.py's predict_meta()) that answers "is this BUY/SELL
direction call right," a different question from this component's "should I add now, how much."

Adapted from the reference meta_labeling_gate.py in
Improvements/Position_Scaling/AI_Investment_Position_Scaling_Architecture.pdf Appendix C, with
these adaptations to this codebase's real field names/scales (per the T241 Phase 0 gap analysis):
  - primary_signal_probability / signal_confidence_delta read from AIConfidence.confidence
    (0-100 scale, NOT bullish_probability's 0-1 scale — the gap analysis flagged these two
    scales exist simultaneously and must not be confused).
  - regime_is_favorable derived from the existing rule-based regime state label
    (get_last_regime()/get_last_hk_regime(), NOT the raw HMM posterior vector, which the gap
    analysis found is not exposed outside market-data's own process today).
  - volume_zscore renamed to match this codebase's existing key: sig.reasons["volume_z"].
  - distance_to_support_atr reads support_level from sig.reasons's existing S/R fields rather
    than a hypothetical new field.

IMPORTANT — training data volume caveat (found while building this phase, 2026-07-09): this
app's REAL historical scale-in events (production PaperTrade rows with a SCALE_IN note) number
only ~12 as of this writing — nowhere near enough for a trustworthy walk-forward split (this
codebase's own established floor, MIN_SAMPLES_PER_SPLIT=15, wouldn't even be met by ONE fold).
Per the architecture doc's section 5.1, candidate events are NOT limited to actual historical
adds — every point where a pullback-add COULD have happened (an existing hypothetical open
position, still within its holding window, with a fresh BUY signal at a price below the
position's cost basis) is a valid labeling opportunity. There are ~4500 real BUY signals across
166 stocks with daily price history in this app's DB, which is a large enough candidate universe
— but assembling that candidate-event dataset (mining signals + synthesizing hypothetical
positions Phase 2 can then label) is its own data-engineering task, NOT built in this pass. This
module's fit()/predict() are fully real and tested against synthetic-but-realistic data; training
a model you'd actually trust requires that candidate-event-mining step to run first.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier

FEATURE_COLUMNS = [
    "primary_signal_confidence",   # AIConfidence.confidence at re-evaluation time, 0-100 scale
    "signal_confidence_delta",     # change in confidence since the position's last entry/add
    "current_drawdown_pct",        # negative if underwater vs current weighted-average cost basis
    "regime_is_favorable",         # 1 if the rule-based regime state favors this trade type, else 0
    "realized_vol_percentile",     # trailing 20d realized vol, percentile vs 1yr history
    "volume_zscore",               # sig.reasons["volume_z"] — volume anomaly vs 20d average
    "sector_correlation",          # how much of the move is sector-wide vs idiosyncratic
    "days_since_last_entry",
    "existing_position_pct_of_portfolio",
    "num_prior_adds",              # tranche count so far
    "distance_to_support_atr",     # (current_price - support_level) / atr
]


@dataclass
class PositionScalingPrediction:
    act_probability: float          # P(this is a good moment to add)
    suggested_size_multiplier: float  # 0.0-1.0, scales the base tranche size
    should_act: bool


class PositionScalingGate:
    """Wraps a calibrated classifier. Calibration matters more here than in most
    classification tasks, because the raw probability IS the signal used downstream for
    sizing (Phase 5, multiplied in per the T241 resolved sizing-composition decision) — an
    uncalibrated model that's "usually right but overconfident" would systematically oversize
    positions.
    """

    def __init__(self, act_threshold: float = 0.55):
        self.act_threshold = act_threshold
        base_model = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=3,             # shallow trees — this dataset is not huge, avoid overfitting
            learning_rate=0.05,
            subsample=0.8,
        )
        self.model = CalibratedClassifierCV(base_model, method="isotonic", cv=5)
        self._is_fitted = False

    def fit(self, features: pd.DataFrame, labels: pd.Series) -> None:
        """features: columns matching FEATURE_COLUMNS. labels: label_add_was_correct from
        triple_barrier_labeling.py.

        IMPORTANT: split features/labels using walk-forward (see walk_forward_train() below),
        not random shuffling, before calling this — fit only on the training fold. Never call
        this directly on the full unsplit dataset if the intent is an honest validation report.
        """
        X = features[FEATURE_COLUMNS].values
        self.model.fit(X, labels.values)
        self._is_fitted = True

    def predict(self, features: pd.Series) -> PositionScalingPrediction:
        if not self._is_fitted:
            raise RuntimeError("PositionScalingGate must be fit before predicting")

        X = features[FEATURE_COLUMNS].values.reshape(1, -1)
        act_probability = float(self.model.predict_proba(X)[0, 1])

        # Size multiplier scales smoothly with confidence above threshold, rather than a
        # binary on/off — a 0.56 probability and a 0.95 probability should not get the same
        # tranche size.
        if act_probability < self.act_threshold:
            size_multiplier = 0.0
        else:
            span = 1.0 - self.act_threshold
            size_multiplier = min(1.0, (act_probability - self.act_threshold) / span)

        return PositionScalingPrediction(
            act_probability=act_probability,
            suggested_size_multiplier=round(size_multiplier, 3),
            should_act=act_probability >= self.act_threshold,
        )

    def feature_importances(self) -> pd.Series:
        """Sanity-check the model: if 'current_drawdown_pct' is the dominant feature, the
        model has probably just re-learned naive averaging-down. Regime and signal-decay
        features should carry real weight — that's the difference this component is supposed
        to add over a price-only rule.
        """
        if not self._is_fitted:
            raise RuntimeError("Model must be fit first")
        # CalibratedClassifierCV wraps estimators per fold; average them.
        importances = np.mean(
            [clf.estimator.feature_importances_ for clf in self.model.calibrated_classifiers_],
            axis=0,
        )
        return pd.Series(importances, index=FEATURE_COLUMNS).sort_values(ascending=False)


def compute_features_for_event(
    primary_signal_confidence: float,
    signal_confidence_at_last_entry: float,
    current_price: float,
    weighted_avg_cost_basis: float,
    regime_is_favorable: bool,
    realized_vol_percentile: float,
    volume_zscore: float,
    sector_correlation: float,
    days_since_last_entry: int,
    existing_position_pct_of_portfolio: float,
    num_prior_adds: int,
    support_level: float,
    atr: float,
) -> pd.Series:
    """Convenience function to assemble one feature row at inference time.
    Keep this in sync with FEATURE_COLUMNS above.
    """
    return pd.Series({
        "primary_signal_confidence": primary_signal_confidence,
        "signal_confidence_delta": primary_signal_confidence - signal_confidence_at_last_entry,
        "current_drawdown_pct": (current_price - weighted_avg_cost_basis) / weighted_avg_cost_basis
                                 if weighted_avg_cost_basis > 0 else 0.0,
        "regime_is_favorable": float(regime_is_favorable),
        "realized_vol_percentile": realized_vol_percentile,
        "volume_zscore": volume_zscore,
        "sector_correlation": sector_correlation,
        "days_since_last_entry": days_since_last_entry,
        "existing_position_pct_of_portfolio": existing_position_pct_of_portfolio,
        "num_prior_adds": num_prior_adds,
        "distance_to_support_atr": (current_price - support_level) / atr if atr > 0 else 0.0,
    })


@dataclass
class WalkForwardFoldResult:
    fold_index: int
    train_start: int
    train_end: int
    val_start: int
    val_end: int
    n_train: int
    n_val: int
    hit_rate: float | None = None          # fraction of validation predictions matching the label
    mean_realized_return: float | None = None  # among predicted should_act=True events
    max_drawdown_proxy: float | None = None    # worst single realized_return among acted events
    skipped_reason: str | None = None


def walk_forward_train(
    features: pd.DataFrame,
    labels: pd.Series,
    realized_returns: pd.Series,
    n_splits: int = 5,
    min_samples_per_split: int = 15,
    act_threshold: float = 0.55,
) -> tuple[list[WalkForwardFoldResult], PositionScalingGate]:
    """Rolling-window train/validate splits — never a random shuffle on time-series data,
    which leaks future information into training (per the design doc's section 5.3).

    features/labels/realized_returns must already be sorted chronologically by the caller
    (event_timestamp ascending) — this function does not re-sort, matching gate_harness.py's
    existing MIN_SAMPLES_PER_SPLIT floor pattern (T234-SIG-INSAMPLE-GATE-TUNING / T232-OC3) for
    what counts as a large-enough fold to trust.

    Returns (per-fold results, a gate refit on ALL data) — the all-data refit is what you'd
    actually deploy after reviewing the walk-forward report, matching the same "report, then
    decide, then use the full-data model" pattern already established for
    hmm_regime.py/meta_trainer.py in this codebase.
    """
    n = len(features)
    fold_results: list[WalkForwardFoldResult] = []
    if n < min_samples_per_split * 2:
        fold_results.append(WalkForwardFoldResult(
            fold_index=0, train_start=0, train_end=0, val_start=0, val_end=0,
            n_train=0, n_val=0,
            skipped_reason=f"only {n} total events (need >= {min_samples_per_split * 2} for even 1 fold)",
        ))
        return fold_results, PositionScalingGate(act_threshold=act_threshold)

    fold_size = max(n // (n_splits + 1), min_samples_per_split)
    for i in range(n_splits):
        train_end = fold_size * (i + 1)
        val_start = train_end
        val_end = min(val_start + fold_size, n)
        if val_end - val_start < min_samples_per_split or train_end < min_samples_per_split:
            fold_results.append(WalkForwardFoldResult(
                fold_index=i, train_start=0, train_end=train_end, val_start=val_start, val_end=val_end,
                n_train=train_end, n_val=val_end - val_start,
                skipped_reason=f"fold below min_samples_per_split={min_samples_per_split}",
            ))
            continue

        gate = PositionScalingGate(act_threshold=act_threshold)
        gate.fit(features.iloc[:train_end], labels.iloc[:train_end])

        val_X = features.iloc[val_start:val_end]
        val_y = labels.iloc[val_start:val_end]
        val_ret = realized_returns.iloc[val_start:val_end]

        preds = [gate.predict(val_X.iloc[j]) for j in range(len(val_X))]
        pred_should_act = pd.Series([p.should_act for p in preds], index=val_y.index)

        hits = (pred_should_act == val_y).sum()
        hit_rate = round(hits / len(val_y), 4) if len(val_y) else None

        acted_returns = val_ret[pred_should_act]
        mean_ret = round(float(acted_returns.mean()), 4) if len(acted_returns) else None
        max_dd = round(float(acted_returns.min()), 4) if len(acted_returns) else None

        fold_results.append(WalkForwardFoldResult(
            fold_index=i, train_start=0, train_end=train_end, val_start=val_start, val_end=val_end,
            n_train=train_end, n_val=val_end - val_start,
            hit_rate=hit_rate, mean_realized_return=mean_ret, max_drawdown_proxy=max_dd,
        ))

    final_gate = PositionScalingGate(act_threshold=act_threshold)
    final_gate.fit(features, labels)
    return fold_results, final_gate


def walk_forward_report(fold_results: list[WalkForwardFoldResult]) -> dict:
    """Summarize walk-forward validation across all folds — per the design doc's Phase 3
    acceptance criteria: Sharpe/drawdown/hit-rate per fold, not just classification accuracy.
    """
    valid = [f for f in fold_results if f.skipped_reason is None]
    if not valid:
        return {
            "n_folds": len(fold_results),
            "n_valid_folds": 0,
            "all_folds_skipped": True,
            "skip_reasons": [f.skipped_reason for f in fold_results],
        }
    hit_rates = [f.hit_rate for f in valid if f.hit_rate is not None]
    mean_rets = [f.mean_realized_return for f in valid if f.mean_realized_return is not None]
    return {
        "n_folds": len(fold_results),
        "n_valid_folds": len(valid),
        "mean_hit_rate": round(float(np.mean(hit_rates)), 4) if hit_rates else None,
        "mean_realized_return_across_folds": round(float(np.mean(mean_rets)), 4) if mean_rets else None,
        "worst_fold_drawdown_proxy": round(min(f.max_drawdown_proxy for f in valid
                                                if f.max_drawdown_proxy is not None), 4)
                                     if any(f.max_drawdown_proxy is not None for f in valid) else None,
        "per_fold": [vars(f) for f in fold_results],
    }

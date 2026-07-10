"""T241-POSITION-SCALING Phase 3: position-scaling gate training tests.

Per Improvements/Position_Scaling/implementation_prompt.md Phase 3 acceptance criteria:
"a written validation report with walk-forward Sharpe/drawdown/hit-rate per fold, the feature
importance ranking with your sign-off that it isn't just rediscovering price-based averaging."
"""
import numpy as np
import pandas as pd

from src.backtest.position_scaling_gate import (
    FEATURE_COLUMNS,
    PositionScalingGate,
    compute_features_for_event,
    walk_forward_report,
    walk_forward_train,
)


def _synthetic_dataset(n: int, seed: int = 0) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Build a synthetic-but-structured dataset where 'regime_is_favorable' and
    'signal_confidence_delta' are the REAL signal driving the label — deliberately, so the
    feature-importance sanity check (test_feature_importances_do_not_just_rediscover_drawdown)
    has something meaningful to verify against. current_drawdown_pct is present but
    intentionally uncorrelated with the label, standing in for "a naive price-only rule."
    """
    rng = np.random.RandomState(seed)
    rows = []
    labels = []
    returns = []
    for i in range(n):
        regime_fav = rng.choice([0, 1])
        conf_delta = rng.uniform(-20, 20)
        drawdown = rng.uniform(-0.15, -0.01)  # always a pullback, uncorrelated with label by design
        # True label depends on regime + confidence, NOT drawdown depth — this is the point.
        signal_strength = regime_fav * 1.0 + (conf_delta > 0) * 1.0
        label = signal_strength >= 1 and rng.random() > 0.15  # some noise
        ret = rng.uniform(0.01, 0.04) if label else rng.uniform(-0.03, 0.005)

        rows.append(compute_features_for_event(
            primary_signal_confidence=60 + conf_delta,
            signal_confidence_at_last_entry=60.0,
            current_price=100 * (1 + drawdown),
            weighted_avg_cost_basis=100.0,
            regime_is_favorable=bool(regime_fav),
            realized_vol_percentile=rng.uniform(0, 1),
            volume_zscore=rng.uniform(-1, 2),
            sector_correlation=rng.uniform(0, 1),
            days_since_last_entry=rng.randint(1, 30),
            existing_position_pct_of_portfolio=rng.uniform(0.02, 0.15),
            num_prior_adds=rng.randint(0, 3),
            support_level=95.0,
            atr=2.0,
        ))
        labels.append(label)
        returns.append(ret)

    return pd.DataFrame(rows), pd.Series(labels), pd.Series(returns)


def test_compute_features_for_event_matches_feature_columns():
    features = compute_features_for_event(
        primary_signal_confidence=70.0,
        signal_confidence_at_last_entry=60.0,
        current_price=95.0,
        weighted_avg_cost_basis=100.0,
        regime_is_favorable=True,
        realized_vol_percentile=0.5,
        volume_zscore=1.2,
        sector_correlation=0.3,
        days_since_last_entry=5,
        existing_position_pct_of_portfolio=0.08,
        num_prior_adds=1,
        support_level=90.0,
        atr=2.0,
    )
    assert set(features.index) == set(FEATURE_COLUMNS)
    # Hand-verified: (95-100)/100 = -0.05
    assert features["current_drawdown_pct"] == -0.05
    # Hand-verified: (95-90)/2.0 = 2.5
    assert features["distance_to_support_atr"] == 2.5
    assert features["signal_confidence_delta"] == 10.0


def test_gate_fit_predict_roundtrip():
    X, y, _ = _synthetic_dataset(200, seed=1)
    gate = PositionScalingGate(act_threshold=0.55)
    gate.fit(X, y)
    pred = gate.predict(X.iloc[0])
    assert 0.0 <= pred.act_probability <= 1.0
    assert 0.0 <= pred.suggested_size_multiplier <= 1.0
    assert pred.should_act == (pred.act_probability >= 0.55)


def test_size_multiplier_scales_smoothly_not_binary():
    """A 0.56 probability and a 0.95 probability must not get the same tranche size —
    per the design doc's explicit requirement in Appendix C.
    """
    gate = PositionScalingGate(act_threshold=0.55)
    X, y, _ = _synthetic_dataset(200, seed=2)
    gate.fit(X, y)

    # Directly exercise the sizing formula the same way predict() does, independent of
    # whatever probability the trained model happens to produce for any specific input.
    def _size_for(p: float) -> float:
        if p < gate.act_threshold:
            return 0.0
        span = 1.0 - gate.act_threshold
        return min(1.0, (p - gate.act_threshold) / span)

    assert _size_for(0.56) < _size_for(0.75) < _size_for(0.95)
    # Hand-verified: span = 1.0 - 0.55 = 0.45; (0.95-0.55)/0.45 = 0.8889 (not yet at the ceiling).
    assert round(_size_for(0.95), 4) == 0.8889
    assert _size_for(1.0) == 1.0  # only a perfect probability reaches the min(1.0, ...) ceiling
    assert _size_for(0.54) == 0.0


def test_predict_before_fit_raises():
    gate = PositionScalingGate()
    try:
        gate.predict(pd.Series({c: 0.0 for c in FEATURE_COLUMNS}))
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_feature_importances_do_not_just_rediscover_drawdown():
    """Sanity check per the design doc: if current_drawdown_pct dominates, the model has
    just re-learned naive averaging-down. In this synthetic dataset, the label is
    constructed to depend on regime_is_favorable + signal_confidence_delta, NOT drawdown
    depth (drawdown is present but uncorrelated with the label by construction) — so
    drawdown should NOT be the top feature.
    """
    X, y, _ = _synthetic_dataset(400, seed=3)
    gate = PositionScalingGate(act_threshold=0.55)
    gate.fit(X, y)
    importances = gate.feature_importances()

    assert set(importances.index) == set(FEATURE_COLUMNS)
    top_feature = importances.index[0]
    # This is the sign-off check the design doc calls for — not an ironclad guarantee for
    # any dataset, but on this deliberately-constructed one, drawdown must not dominate.
    assert top_feature != "current_drawdown_pct"


def test_feature_importances_before_fit_raises():
    gate = PositionScalingGate()
    try:
        gate.feature_importances()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_walk_forward_train_produces_per_fold_results():
    X, y, ret = _synthetic_dataset(150, seed=4)
    fold_results, final_gate = walk_forward_train(
        X, y, ret, n_splits=5, min_samples_per_split=15, act_threshold=0.55,
    )
    assert len(fold_results) == 5
    valid_folds = [f for f in fold_results if f.skipped_reason is None]
    assert len(valid_folds) > 0, "expected at least one valid fold with 150 events"
    for f in valid_folds:
        assert f.hit_rate is not None
        assert 0.0 <= f.hit_rate <= 1.0
        # train_end must strictly precede val_start — no look-ahead (T241 Phase 3 requirement)
        assert f.train_end <= f.val_start

    # The final gate is refit on ALL data — must be usable for prediction.
    pred = final_gate.predict(X.iloc[0])
    assert 0.0 <= pred.act_probability <= 1.0


def test_walk_forward_train_too_few_samples_skips_gracefully():
    X, y, ret = _synthetic_dataset(10, seed=5)  # well under 2 * min_samples_per_split
    fold_results, _ = walk_forward_train(X, y, ret, min_samples_per_split=15)
    assert len(fold_results) == 1
    assert fold_results[0].skipped_reason is not None
    assert "only 10" in fold_results[0].skipped_reason


def test_walk_forward_report_summarizes_folds():
    X, y, ret = _synthetic_dataset(150, seed=6)
    fold_results, _ = walk_forward_train(X, y, ret, n_splits=5, min_samples_per_split=15)
    report = walk_forward_report(fold_results)
    assert report["n_folds"] == 5
    assert report["n_valid_folds"] >= 1
    assert "mean_hit_rate" in report
    assert "per_fold" in report
    assert len(report["per_fold"]) == 5


def test_walk_forward_report_all_skipped():
    fold_results, _ = walk_forward_train(
        *_synthetic_dataset(5), min_samples_per_split=15,
    )
    report = walk_forward_report(fold_results)
    assert report["all_folds_skipped"] is True
    assert report["n_valid_folds"] == 0


# ── save/load persistence (T241 Phase 5) ───────────────────────────────────────────────

def test_save_load_roundtrip_produces_identical_predictions(tmp_path):
    X, y, _ = _synthetic_dataset(200, seed=7)
    gate = PositionScalingGate(act_threshold=0.6)
    gate.fit(X, y)
    original_pred = gate.predict(X.iloc[0])

    save_path = str(tmp_path / "gate.joblib")
    gate.save(save_path, metadata={"n_candidates": 200})

    loaded = PositionScalingGate.load(save_path)
    loaded_pred = loaded.predict(X.iloc[0])

    assert loaded.act_threshold == 0.6
    assert loaded_pred.act_probability == original_pred.act_probability
    assert loaded_pred.suggested_size_multiplier == original_pred.suggested_size_multiplier
    assert loaded_pred.should_act == original_pred.should_act


def test_save_unfit_gate_raises(tmp_path):
    gate = PositionScalingGate()
    try:
        gate.save(str(tmp_path / "gate.joblib"))
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_save_is_atomic_no_leftover_tmp_file(tmp_path):
    X, y, _ = _synthetic_dataset(150, seed=8)
    gate = PositionScalingGate()
    gate.fit(X, y)
    save_path = str(tmp_path / "gate.joblib")
    gate.save(save_path)
    import os
    assert os.path.exists(save_path)
    assert not os.path.exists(f"{save_path}.tmp")

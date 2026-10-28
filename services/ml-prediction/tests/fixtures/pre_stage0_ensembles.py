"""Pre-F04-Stage-0 ensemble functions, copied verbatim from trainer.py at a00021ff.

Golden reference for test_f04_no_eligible_model.py: with every member eligible, the patched
functions must return exactly what these did, plus the new eligibility fields. Not imported by
any service code. Do not edit; regenerate only if the eligible-path behaviour is meant to change.
"""

def predict_latest_ensemble(symbol: str, horizon: int = 5, style: str = "SWING") -> dict:
    """Average XGBoost + RandomForest predictions, weighted by each model's CV AUC.

    Falls back to XGBoost-only if the RF model has not been trained for this symbol.
    Using two structurally different models (gradient boosting vs. bagging) reduces
    variance and smooths out individual model over-reactions to recent noise.
    """
    xgb = predict_latest(symbol, "xgboost", horizon, style=style)

    rf_path = _artifact_path(symbol, "random_forest", style)
    if not rf_path.exists():
        return {**xgb, "ensemble": False, "model": "xgboost"}

    try:
        rf = predict_latest(symbol, "random_forest", horizon, style=style)
    except Exception as exc:
        log.warning("predict_latest_ensemble.rf_predict_failed", symbol=symbol, error=str(exc))
        return {**xgb, "ensemble": False, "model": "xgboost"}

    # Prefer held-out test AUC (unbiased) over CV AUC for internal weighting.
    # T237-ML1B: `or` treats a real, legitimate auc=0.0 (a perfectly rank-inverted model —
    # rare but real) as falsy and silently substitutes 0.55, giving a degenerate model
    # near-normal weight instead of the ~zero weight it deserves. `is not None` is the
    # correct presence check here — the fallback to 0.55 should only fire when the metric
    # is genuinely absent, not merely equal to 0.
    def _cv_auc_mean(m: dict) -> float:
        cv = (m.get("metrics") or {}).get("cv_auc_mean")
        return float(cv) if cv is not None else 0.55

    def _model_auc(m: dict) -> float:
        metrics = m.get("metrics") or {}
        auc = metrics.get("auc")
        if auc is None:
            auc = metrics.get("cv_auc_mean")
        return float(auc) if auc is not None else 0.55

    xgb_auc = _model_auc(xgb)
    rf_auc  = _model_auc(rf)
    # T237-ML1 (mirrored from predict_latest_ensemble_three): a model already flagged
    # oos_suppressed by predict_latest() (CV-AUC < 0.52, coin-flip territory) still had its
    # own real held-out AUC feed this weighting formula at full strength — the top-level
    # oos_suppressed flag below only informed signal-engine's downstream compression AFTER
    # the blend already happened. Zero out a suppressed model's weight here too, falling
    # back to using it alone only if BOTH models are suppressed (never leave the ensemble
    # with zero total weight).
    if xgb.get("oos_suppressed"):
        xgb_auc = 0.0
    if rf.get("oos_suppressed"):
        rf_auc = 0.0
    total = xgb_auc + rf_auc
    if total <= 0:
        # Two ways to land here: (a) both models are suppressed — restore their own real
        # AUCs so the ensemble isn't left with zero total weight; (b) neither is suppressed
        # but both genuinely report a real auc=0.0 (a degenerate-but-real edge case) — in
        # that case there is no meaningful AUC signal to weight by at all, so split evenly
        # rather than dividing by zero or fabricating a preference between two equally
        # uninformative models.
        xgb_auc = _model_auc(xgb) if xgb.get("oos_suppressed") else xgb_auc
        rf_auc  = _model_auc(rf) if rf.get("oos_suppressed") else rf_auc
        total = xgb_auc + rf_auc
        if total <= 0:
            xgb_auc = rf_auc = 0.5
            total = 1.0
    w_xgb, w_rf = xgb_auc / total, rf_auc / total

    prob = float(w_xgb * xgb["bullish_probability"] + w_rf * rf["bullish_probability"])
    # Weight-average each model's precision-optimised threshold by the same AUC weights
    # so the ensemble threshold reflects both models, not just XGBoost's.
    def _model_thr(m: dict) -> float:
        thr = (m.get("metrics") or {}).get("buy_threshold")
        return float(thr) if thr is not None else 0.5

    xgb_thr = _model_thr(xgb)
    rf_thr  = _model_thr(rf)
    buy_threshold = float(w_xgb * xgb_thr + w_rf * rf_thr)

    return {
        "symbol": symbol,
        "model": "ensemble_xgb_rf",
        "bullish_probability": prob,
        "direction": "up" if prob > buy_threshold else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
        "ensemble": True,
        "weights": {"xgboost": round(w_xgb, 2), "random_forest": round(w_rf, 2)},
        "metrics": {
            "mean_model_test_auc": round((xgb_auc + rf_auc) / 2, 4),
            # Report each model's OWN real cv_auc_mean here — xgb_auc/rf_auc above are the
            # (possibly zeroed-for-suppression) WEIGHTING values, not the raw metric, so
            # reusing them as a fallback would misreport a suppressed model's real CV AUC as 0.
            "cv_auc_mean": round(
                (_cv_auc_mean(xgb) + _cv_auc_mean(rf)) / 2, 4
            ),
            "buy_threshold": buy_threshold,
        },
        # T237-ML-OOS1: propagate the coin-flip-model safety flag through the ensemble boundary —
        # signal-engine's SA-27 compression relies on this to discount noisy ML contributions.
        "oos_suppressed": bool(xgb.get("oos_suppressed") or rf.get("oos_suppressed")),
    }


def predict_latest_ensemble_three(symbol: str, horizon: int = 5, style: str = "SWING") -> dict:
    """XGBoost (40%) + LightGBM (35%) + RandomForest (25%) weighted ensemble.

    Agreement logic (SA-8):
    - Unanimous (all 3 same direction): nudge probability 0.05 toward that direction.
    - Split (2-1): compress 0.05 toward 0.5 to reflect the disagreement.
    Per-model probabilities and agreement status are stored in the response for
    inclusion in Signal.reasons.

    Falls back gracefully: if LightGBM or RF is not trained, falls back to
    predict_latest_ensemble (XGBoost + RF), then to XGBoost-only.
    """
    xgb = predict_latest(symbol, "xgboost", horizon, style=style)

    lgb_path = _artifact_path(symbol, "lightgbm", style)
    rf_path  = _artifact_path(symbol, "random_forest", style)

    lgb_res = None
    if lgb_path.exists():
        try:
            lgb_res = predict_latest(symbol, "lightgbm", horizon, style=style)
        except Exception as exc:
            log.warning("predict_latest_ensemble_three.lgb_predict_failed", symbol=symbol, error=str(exc))
            lgb_res = None

    rf_res = None
    if rf_path.exists():
        try:
            rf_res = predict_latest(symbol, "random_forest", horizon, style=style)
        except Exception as exc:
            log.warning("predict_latest_ensemble_three.rf_predict_failed", symbol=symbol, error=str(exc))
            rf_res = None

    # Determine which models are available and blend accordingly
    # T228-ENSEMBLE-WEIGHTS: LightGBM handles 59-feature financial data better than XGBoost
    all_available = [(xgb, 0.30)]
    if lgb_res is not None:
        all_available.append((lgb_res, 0.45))
    if rf_res is not None:
        all_available.append((rf_res, 0.25))

    # T237-ML1: a model with oos_suppressed=True (CV-AUC < 0.52, coin-flip territory) was
    # still contributing its neutral 0.5 at FULL nominal weight to the blended probability,
    # buy_threshold average, and agreement vote — the top-level oos_suppressed flag only
    # informed signal-engine's downstream compression AFTER the fact, it never stopped the
    # bad value from corrupting the blend itself. Exclude suppressed models from the weighted
    # blend (still shown in model_probabilities for transparency); only fall back to using
    # them if literally every available model is suppressed, so the ensemble is never empty.
    available = [(m, w) for m, w in all_available if not m.get("oos_suppressed")]
    if not available:
        available = all_available

    if len(available) == 1:
        # Only one usable model — no ensemble. T237-ML1: this used to always be XGBoost (the
        # only way to reach here was lgb/rf artifacts simply not existing), but excluding
        # oos_suppressed models above means the single survivor can now be any of the three —
        # return the actual survivor's own data, not always xgb's.
        _sole_model, _ = available[0]
        _sole_name = (
            "xgboost" if _sole_model is xgb
            else "lightgbm" if _sole_model is lgb_res
            else "random_forest"
        )
        return {**_sole_model, "ensemble": False, "model": _sole_name,
                "model_probabilities": {_sole_name: round(_sole_model["bullish_probability"], 4)},
                "ensemble_agreement": "single_model"}

    # Renormalize weights to sum to 1.0 for whatever subset is available
    total_w = sum(w for _, w in available)
    prob = sum(m["bullish_probability"] * w / total_w for m, w in available)

    # T89: meta model as 4th ensemble member (15% weight, blended after 3-model renormalization)
    # predict_meta() returns None when the meta model hasn't been trained yet — falls back silently.
    _meta_prob: float | None = None
    # R09: ONE resolution of the flags, before the call. Reading them again later could
    # disagree with what actually happened — a flag flipped mid-request would then be reported
    # as a contributor that never ran, or hide one that did.
    _meta_blend_on = _meta_blend_enabled()
    _meta_shadow_on = (not _meta_blend_on) and _meta_shadow_enabled()
    try:
        if not (_meta_blend_on or _meta_shadow_on):
            # Skipped entirely: no import, no sector/market-cap lookup, no inference. The
            # feature work behind this call is not free, and its result would be discarded.
            raise _MetaDisabled
        # T237-ML-META3: `from training.meta_trainer import ...` (bare, no `src.`/`.` prefix) has
        # never actually resolved in the running app — sys.path here is ['', '/app/shared', '/app',
        # ...], never '/app/src', so this raised ModuleNotFoundError on every call, silently caught
        # by the except below. The T89 meta-model ensemble member has never engaged in production
        # since it was added. Use the same relative import routes.py already uses successfully.
        from .meta_trainer import predict_meta as _predict_meta
        # Derive confidence + signal scores from the XGBoost result (best available proxy)
        _confidence = float(xgb.get("confidence", 0.0)) / 100.0  # convert to [0,1] range
        _fused_prob = float(xgb.get("bullish_probability", 0.5))
        # T228-TA-SCORE-META: use 3-model ensemble probability as ta_score proxy
        _ta_score = float(prob)
        # T237-ML-META2: predict_meta() never fetches sector/market_cap itself — it only encodes
        # whatever is passed in. This call previously hardcoded None for both (a stale comment
        # claimed predict_meta looked them up internally), forcing every live prediction into the
        # "unknown sector, unknown/micro cap" bucket even though train_meta_model() trains on the
        # real values. Fetch them here so inference matches training.
        _sector, _market_cap = _load_sector_and_market_cap(symbol)
        _meta_prob = _predict_meta(
            symbol=symbol,
            horizon=style,
            confidence=_confidence,
            fused_prob=_fused_prob,
            ta_score=_ta_score,
            sector=_sector,
            market_cap=_market_cap,
        )
    except _MetaDisabled:
        _meta_prob = None
    except Exception as exc:
        # AUD291-SILENT-EXCEPTIONS-MLPRED: this exact except block's own comment above
        # (T237-ML-META3) documents a real, previously-undiscovered bug that hid here
        # completely silently for a long time — a bare, unimported name that raised
        # ModuleNotFoundError on every single call. Logging here is specifically to make
        # sure a regression of that class (or any other meta-model call failure) is never
        # invisible again — a missing log line is exactly what let it go unnoticed before.
        log.warning("predict_latest_ensemble_three.meta_predict_failed", symbol=symbol, error=str(exc))
        _meta_prob = None

    _meta_applied_weight = 0.0
    if _meta_prob is not None and _meta_blend_on:
        # Blend: reduce 3-model ensemble by 15%, add meta at 15%
        prob = prob * 0.85 + _meta_prob * 0.15
        _meta_applied_weight = 0.15

    # Agreement: bullish if prob > 0.5 per model
    probs = [m["bullish_probability"] for m, _ in available]
    directions = [p > 0.5 for p in probs]
    n_bull = sum(directions)
    n_models = len(probs)

    # Collect per-model AUCs for nudge gate (use test AUC when available, fallback to cv_auc_mean)
    # AUD-ML1B-NUDGEGATE (2026-08-31): a third, previously-undiscovered instance of the same
    # falsy-zero bug already fixed twice in this same function (predict_latest_ensemble()'s own
    # T237-ML1B fix, and this function's own separate AUD-ML1B-3MODEL fix for the REPORTED
    # mean_auc metric below) — `or` treats a real, legitimate auc=0.0 (a perfectly rank-inverted
    # model, oos_suppressed=False since its own cv_auc_mean can independently be >= 0.52) as
    # falsy and silently substitutes cv_auc_mean instead, wrongly clearing the `_min_auc > 0.57`
    # unanimous-agreement confidence-boost gate below even though one contributing model is
    # genuinely unreliable. `is not None` is the correct presence check — the gate's own 0.0
    # absent-fallback (deliberately conservative: a missing AUC should never look "reliable")
    # is unchanged, only the falsy-zero coercion is fixed.
    _auc_vals_for_gate = []
    for _m, _ in available:
        _m_metrics = _m.get("metrics") or {}
        _m_auc_raw = _m_metrics.get("auc")
        if _m_auc_raw is None:
            _m_auc_raw = _m_metrics.get("cv_auc_mean")
        _m_auc = float(_m_auc_raw) if _m_auc_raw is not None else 0.0
        _auc_vals_for_gate.append(_m_auc)
    _min_auc = min(_auc_vals_for_gate) if _auc_vals_for_gate else 0.0

    if n_bull == n_models:
        agreement = "unanimous_bull"
        # Only apply nudge when all models are reliable (min AUC > 0.57)
        if _min_auc > 0.57:
            prob = min(0.95, prob + 0.05)
    elif n_bull == 0:
        agreement = "unanimous_bear"
        # Only apply nudge when all models are reliable (min AUC > 0.57)
        if _min_auc > 0.57:
            prob = max(0.05, prob - 0.05)
    else:
        agreement = "majority_bull" if n_bull > n_models / 2 else "majority_bear"
        # Slight compression toward 0.5 for disagreement
        prob = prob + (0.5 - prob) * 0.05

    prob = float(prob)

    model_probs = {"xgboost": round(xgb["bullish_probability"], 4)}
    if lgb_res is not None:
        model_probs["lightgbm"] = round(lgb_res["bullish_probability"], 4)
    if rf_res is not None:
        model_probs["random_forest"] = round(rf_res["bullish_probability"], 4)
    # R09: a member is listed here only if it actually moved the number. A shadow run is
    # reported below under its own name with weight 0.0, not smuggled in as a contributor.
    if _meta_prob is not None and _meta_applied_weight > 0:
        model_probs["meta"] = round(_meta_prob, 4)

    _is_contributor = _meta_applied_weight > 0
    model_name = f"ensemble_xgb{'_lgb' if lgb_res else ''}{'_rf' if rf_res else ''}{'_meta' if _is_contributor else ''}"

    # R09: the weights that were actually applied, so a reader does not have to infer them from
    # the model name and a constant buried in this function.
    _applied_weights = {name: round(w / total_w, 4) for (m, w), name in zip(
        available,
        [("xgboost" if m is xgb else "lightgbm" if m is lgb_res else "random_forest")
         for m, _ in available])}
    if _is_contributor:
        _applied_weights = {k: round(v * 0.85, 4) for k, v in _applied_weights.items()}
        _applied_weights["meta"] = _meta_applied_weight

    # Weight-average thresholds by the same portfolio weights used for blending
    buy_threshold = sum(
        float((m.get("metrics") or {}).get("buy_threshold") or 0.5) * w / total_w
        for m, w in available
    )

    # AUD-ML1B-3MODEL: sibling of predict_latest_ensemble()'s own T237-ML1B fix (see that
    # function's docstring for the full rationale) — `or` treats a real, legitimate auc=0.0
    # (a perfectly rank-inverted model) as falsy and silently substitutes 0.55, giving a
    # degenerate model near-normal reported AUC instead of the ~0 it deserves. That reported
    # mean_model_test_auc is what signal-engine's _fetch_ml_data() (signals.py) reads to set
    # this model's fusion weight against TA — a corrupted 0.55 here lands squarely on the
    # else-branch boundary of that formula, giving a known-bad model 20% weight instead of 0%.
    # This block was NEVER covered by the earlier T237-ML1/predict_latest_ensemble() fix —
    # that fix only touched the 2-model sibling function; this 3-model function's own separate
    # mean_auc computation (used only for the *reported* metric, not the probability blend
    # weights above, which already correctly exclude oos_suppressed models) was untouched.
    def _model_auc_3(m: dict) -> float:
        metrics = m.get("metrics") or {}
        auc = metrics.get("auc")
        if auc is None:
            auc = metrics.get("cv_auc_mean")
        return float(auc) if auc is not None else 0.55

    # Also zero out a suppressed model's contribution to the REPORTED mean AUC — a model
    # already known to be coin-flip-quality (oos_suppressed=True) should not inflate the
    # metric a downstream fusion-weight formula trusts, even if its own point-estimate `auc`
    # happens to look like a real, non-degenerate number.
    _auc_weighted = [(_model_auc_3(xgb), bool(xgb.get("oos_suppressed")))]
    if lgb_res:
        _auc_weighted.append((_model_auc_3(lgb_res), bool(lgb_res.get("oos_suppressed"))))
    if rf_res:
        _auc_weighted.append((_model_auc_3(rf_res), bool(rf_res.get("oos_suppressed"))))

    auc_vals = [0.0 if suppressed else auc for auc, suppressed in _auc_weighted]
    if sum(auc_vals) <= 0:
        # Every contributing model is suppressed (or genuinely all report a real auc=0.0) —
        # restore real AUCs rather than reporting a misleading, uniformly-zeroed mean.
        auc_vals = [auc for auc, _ in _auc_weighted]
    mean_auc = sum(auc_vals) / len(auc_vals)

    return {
        "symbol": symbol,
        "model": model_name,
        "bullish_probability": prob,
        "direction": "up" if prob > buy_threshold else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
        "ensemble": True,
        "model_probabilities": model_probs,
        "ensemble_agreement": agreement,
        # R09: the weights actually applied to produce `bullish_probability`, so provenance is
        # read rather than inferred from the model name. Sums to 1.0.
        "applied_weights": _applied_weights,
        # R09: a shadow run is reported here, under its own name, with an applied weight of
        # 0.0 — never inside `model_probabilities` and never in the model name. Absent unless
        # shadow mode is explicitly switched on.
        **({"meta_shadow_probability": round(_meta_prob, 4),
            "meta_shadow_applied_weight": 0.0} if (_meta_shadow_on and _meta_prob is not None)
           else {}),
        "metrics": {
            "mean_model_test_auc": round(mean_auc, 4),
            "cv_auc_mean": round(mean_auc, 4),
            "buy_threshold": buy_threshold,
        },
        # T237-ML-OOS1: propagate the coin-flip-model safety flag through the ensemble boundary —
        # signal-engine's SA-27 compression relies on this to discount noisy ML contributions.
        "oos_suppressed": bool(any(m.get("oos_suppressed") for m, _ in available)),
    }


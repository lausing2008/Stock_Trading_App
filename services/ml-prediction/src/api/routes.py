"""ML endpoints: list, train, tune, predict."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from common.jwt_auth import get_current_username
from common.logging import get_logger

log = get_logger("ml.routes")

from ..models import list_models
from ..training import predict_latest, predict_latest_ensemble, predict_latest_ensemble_three, train_model, tune_symbol, validate_walkforward

router = APIRouter(prefix="/ml", tags=["ml"])

# Training horizon per style: SHORT holds 1-5 days, SWING 1-4 weeks, LONG 1-3 months.
# Matching the label horizon to the intended hold period improves signal precision.
_HORIZON_BY_STYLE: dict[str, int] = {
    "SHORT":  5,
    "SWING":  10,
    "LONG":   20,
    "GROWTH": 15,  # breakout extension horizon: longer than SWING, shorter than LONG
}


class TrainRequest(BaseModel):
    symbol: str
    model: str = "xgboost"
    horizon: int = 5
    style: str = "SWING"


class TuneRequest(BaseModel):
    symbol: str
    n_trials: int = 60
    horizon: int = 5
    style: str = "SWING"


class PredictRequest(BaseModel):
    symbol: str
    model: str = "xgboost"
    horizon: int = 5
    style: str = "SWING"  # route to per-style artifact; falls back to SWING if absent


@router.get("/models")
def models():
    return {"models": list_models()}


@router.get("/status")
def ml_status():
    """Return model counts per type — a quick health check for the ML service."""
    from pathlib import Path
    from common.config import get_settings

    settings = get_settings()
    model_dir = Path(settings.model_dir)
    counts = {}
    for model_name in list_models():
        mdir = model_dir / model_name
        counts[model_name] = len(list(mdir.glob("*.joblib"))) if mdir.exists() else 0

    total = sum(counts.values())
    return {
        "status": "ok" if total > 0 else "no_models",
        "total_artifacts": total,
        "by_model": counts,
        "model_dir": str(model_dir),
    }


@router.post("/train")
def train(req: TrainRequest, tasks: BackgroundTasks, _: str = Depends(get_current_username)):
    if req.model not in list_models():
        raise HTTPException(400, f"Unknown model: {req.model}")
    horizon = _HORIZON_BY_STYLE.get(req.style.upper(), req.horizon)
    tasks.add_task(train_model, req.symbol, req.model, horizon, style=req.style)
    return {"status": "scheduled", "symbol": req.symbol, "model": req.model, "style": req.style, "horizon": horizon}


@router.post("/train_all")
def train_all(tasks: BackgroundTasks, style: str = "SWING", _: str = Depends(get_current_username)):
    """Schedule xgboost training for every active stock (uses tuned params if available).

    Horizon is derived from style: SHORT=5d, SWING=10d, LONG=20d.
    """
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    horizon = _HORIZON_BY_STYLE.get(style.upper(), 5)
    for sym in symbols:
        tasks.add_task(train_model, sym, "xgboost", horizon, style=style)

    return {"status": "scheduled", "count": len(symbols), "symbols": symbols, "style": style, "horizon": horizon}


@router.post("/tune")
def tune(req: TuneRequest, tasks: BackgroundTasks, _: str = Depends(get_current_username)):
    """Run Optuna hyperparameter search for one symbol, then retrain with best params."""
    horizon = _HORIZON_BY_STYLE.get(req.style.upper(), req.horizon)
    tasks.add_task(tune_symbol, req.symbol, req.n_trials, horizon, req.style)
    return {"status": "scheduled", "symbol": req.symbol, "n_trials": req.n_trials, "style": req.style, "horizon": horizon}


@router.post("/tune_all")
def tune_all(tasks: BackgroundTasks, n_trials: int = 60, style: str = "SWING", _: str = Depends(get_current_username)):
    """Run Optuna tuning sequentially for every active stock (weekend job).

    Each symbol gets `n_trials` Optuna trials then a full retrain. Runs in background.
    Horizon is derived from style: SHORT=5d, SWING=10d, LONG=20d.
    With 123 symbols × 60 trials this takes roughly 3-5 hours on EC2.
    """
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    horizon = _HORIZON_BY_STYLE.get(style.upper(), 5)

    def _run_all():
        results = []
        for sym in symbols:
            try:
                result = tune_symbol(sym, n_trials=n_trials, horizon=horizon, style=style)
            except Exception as exc:
                log.warning("tune_all.symbol_failed", symbol=sym, error=str(exc))
                result = {"symbol": sym, "skipped": True, "reason": str(exc)}
            results.append(result)

        # TIER95: After all models are retrained, trigger signal refreshes so new models
        # are used immediately (not at the next scheduled refresh 5× per day).
        tuned_count = sum(1 for r in results if not r.get("skipped"))
        if tuned_count > 0:
            log.info("tune_all.complete", tuned=tuned_count, total=len(symbols))
            try:
                from common.config import get_settings as _gs
                import httpx as _hx
                import uuid as _uuid
                from jose import jwt as _jwt
                _s = _gs()
                _tok = _jwt.encode(
                    {"sub": "ml-prediction", "jti": str(_uuid.uuid4()), "exp": int(__import__("time").time()) + 3600},
                    _s.jwt_secret, algorithm="HS256",
                )
                _hdrs = {"Authorization": f"Bearer {_tok}"}
                for _mkt in ("US", "HK"):
                    try:
                        _r = _hx.post(
                            f"{_s.signal_engine_url}/signals/refresh",
                            params={"market": _mkt},
                            headers=_hdrs, timeout=30,
                        )
                        log.info("tune_all.post_signal_refresh", market=_mkt, status=_r.status_code)
                    except Exception as _exc:
                        log.warning("tune_all.post_signal_refresh_failed", market=_mkt, error=str(_exc))
            except Exception as _exc:
                log.warning("tune_all.post_refresh_setup_failed", error=str(_exc))

        return results

    tasks.add_task(_run_all)
    return {
        "status": "scheduled",
        "count": len(symbols),
        "symbols": symbols,
        "n_trials_per_symbol": n_trials,
        "style": style,
        "horizon": horizon,
        "note": "Check container logs for progress. Each symbol logs tune.best_params when done.",
    }


@router.post("/train_meta")
def train_meta(tasks: BackgroundTasks, _: str = Depends(get_current_username)):
    """Train or retrain the cross-symbol meta-learning model (T89).

    Trains a single XGBoost model on ALL signal_outcomes across ALL symbols.
    Used as cold-start prior and 4th ensemble member (15% weight) in predict_ensemble_three.
    Runs in background — check container logs for progress.
    """
    from ..training.meta_trainer import train_meta_model as _train_meta
    tasks.add_task(_train_meta)
    return {
        "status": "scheduled",
        "note": "Meta model training running in background. Check logs for 'meta_trainer.trained'.",
    }


def _resolve_horizon(req: "PredictRequest") -> int:
    """AUD232-029: req.horizon defaulted to 5 (SHORT's horizon) regardless of req.style, and
    signal-engine's caller never sends horizon at all — every non-SHORT-style live prediction
    silently fell back to the wrong horizon. Currently harmless (build_features only uses
    horizon for the y-target, discarded at inference time via inference_mode=True) but fragile
    against any future feature that reads horizon for an X-column. Derive it server-side from
    style, matching the same _HORIZON_BY_STYLE mapping already used at train time, rather than
    trusting a fixed request-body default."""
    return _HORIZON_BY_STYLE.get(req.style.upper(), req.horizon)


@router.post("/predict")
def predict(req: PredictRequest, _: str = Depends(get_current_username)):
    try:
        return predict_latest(req.symbol, req.model, _resolve_horizon(req), style=req.style)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/predict_ensemble")
def predict_ensemble(req: PredictRequest, _: str = Depends(get_current_username)):
    """XGBoost + RandomForest ensemble prediction, weighted by each model's CV AUC.

    Falls back to XGBoost-only if RF model not yet trained for this symbol.
    Train both models first with POST /ml/train_all_ensemble.
    """
    try:
        return predict_latest_ensemble(req.symbol, _resolve_horizon(req), style=req.style)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/predict_ensemble_three")
def predict_ensemble_three(req: PredictRequest, _: str = Depends(get_current_username)):
    """XGBoost (40%) + LightGBM (35%) + RandomForest (25%) ensemble with agreement detection.

    Falls back gracefully if LightGBM or RF haven't been trained yet.
    Train all three with POST /ml/train_all_ensemble_three.
    """
    try:
        return predict_latest_ensemble_three(req.symbol, _resolve_horizon(req), style=req.style)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/train_all_ensemble_three")
def train_all_ensemble_three(tasks: BackgroundTasks, style: str = "SWING", _: str = Depends(get_current_username)):
    """Train XGBoost + LightGBM + RandomForest for every active symbol.

    Enables 3-model ensemble predictions via POST /ml/predict_ensemble_three.
    """
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    horizon = _HORIZON_BY_STYLE.get(style.upper(), 5)
    for sym in symbols:
        tasks.add_task(train_model, sym, "xgboost", horizon, style=style)
        tasks.add_task(train_model, sym, "lightgbm", horizon, style=style)
        tasks.add_task(train_model, sym, "random_forest", horizon, style=style)

    return {
        "status": "scheduled",
        "count": len(symbols),
        "models": ["xgboost", "lightgbm", "random_forest"],
        "style": style,
        "horizon": horizon,
        "note": "After completion, use POST /ml/predict_ensemble_three for 3-model predictions.",
    }


@router.post("/train_all_ensemble")
def train_all_ensemble(tasks: BackgroundTasks, style: str = "SWING", _: str = Depends(get_current_username)):
    """Train XGBoost AND RandomForest for every active symbol.

    Enables ensemble predictions via POST /ml/predict_ensemble.
    Horizon is derived from style: SHORT=5d, SWING=10d, LONG=20d.
    """
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    horizon = _HORIZON_BY_STYLE.get(style.upper(), 5)
    for sym in symbols:
        tasks.add_task(train_model, sym, "xgboost", horizon, style=style)
        tasks.add_task(train_model, sym, "random_forest", horizon, style=style)

    return {
        "status": "scheduled",
        "count": len(symbols),
        "models": ["xgboost", "random_forest"],
        "style": style,
        "horizon": horizon,
        "note": "After completion, use POST /ml/predict_ensemble for ensemble predictions.",
    }


@router.post("/train_all_horizons")
def train_all_horizons(tasks: BackgroundTasks, _: str = Depends(get_current_username)):
    """Train XGBoost + RandomForest for all 4 horizon-specific styles for every active stock.

    T217-C: RF trained alongside XGBoost so predict_latest_ensemble_three() has
    all three models (XGB + LGB + RF) available. Signal engine auto-routes by style.
    Run nightly after market close.
    """
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    scheduled: list[dict] = []
    for style, horizon in _HORIZON_BY_STYLE.items():
        for sym in symbols:
            tasks.add_task(train_model, sym, "xgboost", horizon, style=style)
            tasks.add_task(train_model, sym, "random_forest", horizon, style=style)
        scheduled.append({"style": style, "horizon": horizon})

    return {
        "status": "scheduled",
        "symbol_count": len(symbols),
        "styles": scheduled,
        "total_tasks": len(symbols) * len(_HORIZON_BY_STYLE) * 2,
        "note": "XGBoost + RandomForest per style per symbol. Ensemble uses both.",
    }


@router.get("/metrics/{symbol}")
def get_metrics(symbol: str, model: str = "xgboost", style: str = "SWING"):
    """Return the training metrics stored in the .joblib bundle for a given symbol."""
    from ..training.trainer import _artifact_path
    import joblib

    path = _artifact_path(symbol.upper(), model, style)
    if not path.exists():
        raise HTTPException(404, f"No trained {model} model for {symbol.upper()}")
    try:
        bundle = joblib.load(path)
        return {
            "symbol": symbol.upper(),
            "model": model,
            "metrics": bundle.get("metrics", {}),
            "buy_threshold": bundle.get("buy_threshold"),
            "feature_columns": bundle.get("feature_columns", []),
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to load model bundle: {exc}") from exc


@router.get("/features/{symbol}")
def get_feature_importance(symbol: str, model: str = "xgboost", style: str = "SWING"):
    """Return feature importance for a symbol's trained model.

    Each feature is classified as 'fundamental', 'macro', or 'technical'.
    Results are sorted by importance descending so callers can render top-N.
    """
    from ..training.trainer import _artifact_path
    from ..features import FUNDAMENTAL_COLUMNS, MACRO_COLUMNS
    import joblib

    path = _artifact_path(symbol.upper(), model, style)
    if not path.exists():
        raise HTTPException(404, f"No trained {model} model for {symbol.upper()}")
    try:
        bundle = joblib.load(path)
    except Exception as exc:
        raise HTTPException(500, f"Failed to load model bundle: {exc}") from exc

    fi = bundle.get("feature_importance", {})
    if not fi:
        raise HTTPException(404, f"No feature importance in model bundle for {symbol.upper()}")

    fund_set = set(FUNDAMENTAL_COLUMNS)
    macro_set = set(MACRO_COLUMNS)

    features = [
        {
            "name": col,
            "importance": score,
            "category": "fundamental" if col in fund_set else ("macro" if col in macro_set else "technical"),
        }
        for col, score in sorted(fi.items(), key=lambda x: x[1], reverse=True)
    ]
    return {
        "symbol": symbol.upper(),
        "model": model,
        "features": features,
        "trained_at": bundle.get("trained_at"),
    }


@router.get("/metrics")
def list_all_metrics(model: str = "xgboost"):
    """Return training metrics for every symbol that has a trained model."""
    from pathlib import Path
    from common.config import get_settings
    import joblib

    settings = get_settings()
    model_dir = Path(settings.model_dir) / model
    if not model_dir.exists():
        return {"model": model, "symbols": []}

    results = []
    for artifact in sorted(model_dir.glob("*.joblib")):
        sym = artifact.stem
        try:
            bundle = joblib.load(artifact)
            m = bundle.get("metrics", {})
            results.append({
                "symbol": sym,
                "model": model,
                "test_auc": m.get("auc"),           # bundle stores as "auc", not "test_auc"
                "cv_auc": m.get("cv_auc_mean"),     # bundle stores as "cv_auc_mean"
                "accuracy": m.get("accuracy"),
                "overfit_gap": m.get("overfit_gap"),
                "buy_threshold": bundle.get("buy_threshold"),
            })
        except Exception:
            results.append({"symbol": sym, "model": model, "error": "failed to load"})

    results.sort(key=lambda x: (x.get("test_auc") or 0), reverse=True)
    return {"model": model, "count": len(results), "symbols": results}


@router.get("/walkforward/{symbol}")
def walkforward_oos(
    symbol: str,
    model: str = "xgboost",
    style: str = "SWING",
    train_days: int = 252,
    test_days: int = 63,
    _: str = Depends(get_current_username),
):
    """True out-of-sample walk-forward validation for one symbol.

    Retrains a temporary model on each rolling training window and evaluates on
    the subsequent test_days — a genuine OOS simulation (no lookahead).

    Slower than reading cached metrics (each window retrains from scratch).
    Use for auditing a single symbol's OOS performance; for fleet-wide metrics
    use GET /ml/metrics which returns the stored CV AUC from the existing bundle.

    Returns: per-window OOS precision, AUC, avg return, IC; summary statistics.
    """
    result = validate_walkforward(
        symbol.upper(),
        model_name=model,
        style=style,
        train_days=train_days,
        test_days=test_days,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# T211/T233-ARCH-HMMREGIME: HMM regime classifier moved to market-data 2026-07-04 —
# GET /stocks/regime-state and POST /stocks/regime-refit. paper_trading_engine was the
# only consumer anywhere in the codebase; colocating eliminates a real HTTP hop that ran
# on every regime computation. See services/market-data/src/services/hmm_regime.py.

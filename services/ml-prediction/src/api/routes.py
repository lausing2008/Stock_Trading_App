"""ML endpoints: list, train, tune, predict."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from common.jwt_auth import get_current_username

from ..models import list_models
from ..training import predict_latest, predict_latest_ensemble, predict_latest_ensemble_three, train_model, tune_symbol

router = APIRouter(prefix="/ml", tags=["ml"])

# Training horizon per style: SHORT holds 1-5 days, SWING 1-4 weeks, LONG 1-3 months.
# Matching the label horizon to the intended hold period improves signal precision.
_HORIZON_BY_STYLE: dict[str, int] = {
    "SHORT": 5,
    "SWING": 10,
    "LONG": 20,
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
            result = tune_symbol(sym, n_trials=n_trials, horizon=horizon, style=style)
            results.append(result)
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


@router.post("/predict")
def predict(req: PredictRequest):
    try:
        return predict_latest(req.symbol, req.model, req.horizon)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/predict_ensemble")
def predict_ensemble(req: PredictRequest):
    """XGBoost + RandomForest ensemble prediction, weighted by each model's CV AUC.

    Falls back to XGBoost-only if RF model not yet trained for this symbol.
    Train both models first with POST /ml/train_all_ensemble.
    """
    try:
        return predict_latest_ensemble(req.symbol, req.horizon)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/predict_ensemble_three")
def predict_ensemble_three(req: PredictRequest):
    """XGBoost (40%) + LightGBM (35%) + RandomForest (25%) ensemble with agreement detection.

    Falls back gracefully if LightGBM or RF haven't been trained yet.
    Train all three with POST /ml/train_all_ensemble_three.
    """
    try:
        return predict_latest_ensemble_three(req.symbol, req.horizon)
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


@router.get("/metrics/{symbol}")
def get_metrics(symbol: str, model: str = "xgboost"):
    """Return the training metrics stored in the .joblib bundle for a given symbol."""
    from ..training.trainer import _artifact_path
    import joblib

    path = _artifact_path(symbol.upper(), model)
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

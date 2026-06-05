"""ML endpoints: list, train, tune, predict."""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..models import list_models
from ..training import predict_latest, predict_latest_ensemble, train_model, tune_symbol

router = APIRouter(prefix="/ml", tags=["ml"])


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


@router.post("/train")
def train(req: TrainRequest, tasks: BackgroundTasks):
    if req.model not in list_models():
        raise HTTPException(400, f"Unknown model: {req.model}")
    tasks.add_task(train_model, req.symbol, req.model, req.horizon, style=req.style)
    return {"status": "scheduled", "symbol": req.symbol, "model": req.model, "style": req.style}


@router.post("/train_all")
def train_all(tasks: BackgroundTasks, style: str = "SWING"):
    """Schedule xgboost training for every active stock (uses tuned params if available)."""
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    for sym in symbols:
        tasks.add_task(train_model, sym, "xgboost", 5, style=style)

    return {"status": "scheduled", "count": len(symbols), "symbols": symbols, "style": style}


@router.post("/tune")
def tune(req: TuneRequest, tasks: BackgroundTasks):
    """Run Optuna hyperparameter search for one symbol, then retrain with best params."""
    tasks.add_task(tune_symbol, req.symbol, req.n_trials, req.horizon, req.style)
    return {"status": "scheduled", "symbol": req.symbol, "n_trials": req.n_trials, "style": req.style}


@router.post("/tune_all")
def tune_all(tasks: BackgroundTasks, n_trials: int = 60, style: str = "SWING"):
    """Run Optuna tuning sequentially for every active stock (weekend job).

    Each symbol gets `n_trials` Optuna trials then a full retrain. Runs in background.
    With 60 symbols × 60 trials this takes roughly 2-4 hours on EC2.
    """
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    def _run_all():
        results = []
        for sym in symbols:
            result = tune_symbol(sym, n_trials=n_trials, style=style)
            results.append(result)
        return results

    tasks.add_task(_run_all)
    return {
        "status": "scheduled",
        "count": len(symbols),
        "symbols": symbols,
        "n_trials_per_symbol": n_trials,
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


@router.post("/train_all_ensemble")
def train_all_ensemble(tasks: BackgroundTasks, style: str = "SWING"):
    """Train XGBoost AND RandomForest for every active symbol.

    Enables ensemble predictions via POST /ml/predict_ensemble.
    Takes roughly 1.5× longer than train_all (RF trains faster than XGBoost).
    """
    from sqlalchemy import select
    from db import Stock, SessionLocal

    with SessionLocal() as session:
        symbols = list(session.execute(
            select(Stock.symbol).where(Stock.active.is_(True))
        ).scalars())

    for sym in symbols:
        tasks.add_task(train_model, sym, "xgboost", 5, style=style)
        tasks.add_task(train_model, sym, "random_forest", 5, style=style)

    return {
        "status": "scheduled",
        "count": len(symbols),
        "models": ["xgboost", "random_forest"],
        "note": "After completion, use POST /ml/predict_ensemble for ensemble predictions.",
    }

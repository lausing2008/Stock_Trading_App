"""ML endpoints: list, train, predict."""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..models import list_models
from ..training import predict_latest, train_model

router = APIRouter(prefix="/ml", tags=["ml"])


class TrainRequest(BaseModel):
    symbol: str
    model: str = "xgboost"
    horizon: int = 5


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
    tasks.add_task(train_model, req.symbol, req.model, req.horizon)
    return {"status": "scheduled", "symbol": req.symbol, "model": req.model}


@router.post("/predict")
def predict(req: PredictRequest):
    try:
        return predict_latest(req.symbol, req.model, req.horizon)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

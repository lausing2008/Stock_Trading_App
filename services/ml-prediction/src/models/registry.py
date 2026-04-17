from __future__ import annotations

from .base import BaseModel
from .gbm import GradientBoostingModel
from .lstm import LSTMModel
from .rf import RandomForestModel
from .xgb import XGBModel

_MODELS: dict[str, type[BaseModel]] = {
    "random_forest": RandomForestModel,
    "xgboost": XGBModel,
    "gradient_boosting": GradientBoostingModel,
    "lstm": LSTMModel,
}


def list_models() -> list[str]:
    return sorted(_MODELS.keys())


def get_model(name: str, **kwargs) -> BaseModel:
    if name not in _MODELS:
        raise KeyError(f"Unknown model: {name}. Available: {list_models()}")
    return _MODELS[name](**kwargs)

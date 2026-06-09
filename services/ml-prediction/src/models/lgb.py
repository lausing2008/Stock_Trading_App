from __future__ import annotations

import numpy as np
from lightgbm import LGBMClassifier

from .base import BaseModel


class LightGBMModel(BaseModel):
    name = "lightgbm"

    def __init__(self, **kwargs) -> None:
        self.clf = LGBMClassifier(
            n_estimators=kwargs.get("n_estimators", 400),
            max_depth=kwargs.get("max_depth", 6),
            learning_rate=kwargs.get("learning_rate", 0.05),
            num_leaves=kwargs.get("num_leaves", 31),
            subsample=kwargs.get("subsample", 0.8),
            colsample_bytree=kwargs.get("colsample_bytree", 0.8),
            min_child_samples=kwargs.get("min_child_samples", 20),
            reg_alpha=kwargs.get("reg_alpha", 0.1),
            reg_lambda=kwargs.get("reg_lambda", 1.0),
            n_jobs=-1,
            random_state=42,
            verbose=-1,
        )

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> None:
        callbacks = kwargs.pop("callbacks", None)
        self.clf.fit(X, y)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)

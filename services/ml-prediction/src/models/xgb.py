from __future__ import annotations

import numpy as np
from xgboost import XGBClassifier

from .base import BaseModel


class XGBModel(BaseModel):
    name = "xgboost"

    def __init__(self, **kwargs) -> None:
        self.clf = XGBClassifier(
            n_estimators=kwargs.get("n_estimators", 500),
            max_depth=kwargs.get("max_depth", 5),
            learning_rate=kwargs.get("learning_rate", 0.05),
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            n_jobs=-1,
            random_state=42,
            tree_method="hist",
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self.clf.fit(X, y)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)[:, 1]

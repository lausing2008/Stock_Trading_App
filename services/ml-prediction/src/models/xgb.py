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
            subsample=kwargs.get("subsample", 0.8),
            colsample_bytree=kwargs.get("colsample_bytree", 0.8),
            min_child_weight=kwargs.get("min_child_weight", 5),
            gamma=kwargs.get("gamma", 0.1),
            reg_alpha=kwargs.get("reg_alpha", 0.1),
            reg_lambda=kwargs.get("reg_lambda", 1.0),
            eval_metric="logloss",
            early_stopping_rounds=kwargs.get("early_stopping_rounds", None),
            n_jobs=-1,
            random_state=42,
            tree_method="hist",
        )

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> None:
        self.clf.fit(X, y, **kwargs)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)

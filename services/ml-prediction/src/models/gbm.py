from __future__ import annotations

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

from .base import BaseModel


class GradientBoostingModel(BaseModel):
    name = "gradient_boosting"

    def __init__(self, **kwargs) -> None:
        self.clf = GradientBoostingClassifier(
            n_estimators=kwargs.get("n_estimators", 300),
            max_depth=kwargs.get("max_depth", 3),
            learning_rate=kwargs.get("learning_rate", 0.05),
            random_state=42,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self.clf.fit(X, y)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)[:, 1]

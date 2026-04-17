from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from .base import BaseModel


class RandomForestModel(BaseModel):
    name = "random_forest"

    def __init__(self, **kwargs) -> None:
        self.clf = RandomForestClassifier(
            n_estimators=kwargs.get("n_estimators", 400),
            max_depth=kwargs.get("max_depth", 8),
            min_samples_leaf=kwargs.get("min_samples_leaf", 20),
            n_jobs=-1,
            random_state=42,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self.clf.fit(X, y)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)[:, 1]

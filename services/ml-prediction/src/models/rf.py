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
            max_features=kwargs.get("max_features", "sqrt"),
            class_weight="balanced",  # guards against class imbalance when sample_weight is absent
            n_jobs=-1,
            random_state=42,
        )

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> None:
        self.clf.fit(X, y, sample_weight=kwargs.get("sample_weight"))

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(X)

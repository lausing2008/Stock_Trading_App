import numpy as np
import pandas as pd

from src.features import FEATURE_COLUMNS, build_features


def test_feature_columns():
    n = 400
    df = pd.DataFrame(
        {
            "close": 100 + np.random.default_rng(0).normal(0, 1, n).cumsum(),
            "high": 102 + np.random.default_rng(1).normal(0, 1, n).cumsum(),
            "low": 98 + np.random.default_rng(2).normal(0, 1, n).cumsum(),
            "volume": np.random.default_rng(3).integers(1000, 5000, n),
        }
    )
    X, y_dir, y_ret = build_features(df, horizon=5)
    assert list(X.columns) == FEATURE_COLUMNS
    assert len(X) == len(y_dir) == len(y_ret)
    assert set(y_dir.unique()) <= {0, 1}

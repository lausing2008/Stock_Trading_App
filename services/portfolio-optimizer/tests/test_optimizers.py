import numpy as np
import pandas as pd

from src.optimizers import mean_variance, risk_parity


def _returns():
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "A": rng.normal(0.0005, 0.01, 500),
            "B": rng.normal(0.0003, 0.015, 500),
            "C": rng.normal(0.0007, 0.02, 500),
        }
    )


def test_mvo_weights_sum_to_one():
    r = mean_variance(_returns())
    assert abs(sum(r.weights.values()) + r.cash - 1.0) < 1e-3
    assert all(0 <= w <= 0.35 + 1e-3 for w in r.weights.values())


def test_risk_parity_weights_sum_to_one():
    r = risk_parity(_returns())
    assert abs(sum(r.weights.values()) - 1.0) < 1e-3

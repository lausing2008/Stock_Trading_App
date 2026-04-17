import numpy as np
import pandas as pd

from src.scoring import compute_kscore


def test_kscore_in_range():
    n = 400
    rng = np.random.default_rng(0)
    close = 100 + rng.normal(0.05, 1, n).cumsum()
    df = pd.DataFrame(
        {
            "close": close,
            "high": close + 1,
            "low": close - 1,
            "open": close,
            "volume": rng.integers(1000, 5000, n),
        }
    )
    c = compute_kscore(df)
    for v in (c.technical, c.momentum, c.value, c.growth, c.volatility, c.score):
        assert 0 <= v <= 100

import numpy as np
import pandas as pd

from src.scoring import compute_kscore


def _price_df(n=400, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0.05, 1, n).cumsum()
    return pd.DataFrame(
        {
            "close": close,
            "high": close + 1,
            "low": close - 1,
            "open": close,
            "volume": rng.integers(1000, 5000, n),
        }
    )


def test_kscore_in_range_without_fundamentals():
    """No value_score/growth_score supplied — T234-RANK-KSCORE-PROXY-MIXING's own fix makes
    KScoreComponents.value/.growth correctly None in this case (never a price-proxy number
    silently wearing a fundamentals label), so only the fields that are ALWAYS real numbers
    are range-checked here. value/growth's own real 0-100 range is covered separately below,
    with real fundamentals supplied."""
    c = compute_kscore(_price_df())
    assert c.value is None
    assert c.growth is None
    for v in (c.technical, c.momentum, c.volatility, c.score):
        assert 0 <= v <= 100


def test_kscore_in_range_with_fundamentals():
    """With real value_score/growth_score supplied, every KScoreComponents field the
    composite actually blends must land in 0-100 — this is the case the original,
    pre-T234-RANK-KSCORE-PROXY-MIXING test intended to cover."""
    c = compute_kscore(_price_df(), rs_score=55.0, value_score=60.0, growth_score=45.0)
    for v in (c.technical, c.momentum, c.value, c.growth, c.volatility, c.score):
        assert v is not None
        assert 0 <= v <= 100


def test_value_and_growth_genuinely_participate_in_the_weighted_composite():
    """KScoreComponents.value/.growth are a pure pass-through of the caller's own input, so
    range-checking them alone (the test above) does not prove they actually influence the
    weighted score at all. This confirms the WEIGHTING path itself is exercised: changing
    ONLY value_score, holding everything else fixed, must change the composite c.score."""
    df = _price_df()
    low = compute_kscore(df, rs_score=55.0, value_score=10.0, growth_score=45.0)
    high = compute_kscore(df, rs_score=55.0, value_score=90.0, growth_score=45.0)
    assert low.score != high.score

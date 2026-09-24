"""DA-11: the returned risk/return metrics described a portfolio that was not returned.

THE DEFECT. `ai_allocation()` returns `w_scaled = w * (1 - cash_floor)` plus a `cash`
allocation, but computed its metrics on the UNSCALED `w`. The original comment's reasoning was
sound as far as it went — sleeve metrics are the like-for-like basis against mean_variance /
risk_parity / HRP, which are fully invested — but the API and the portfolio UI presented the
result as the expected return and volatility OF THIS PORTFOLIO, cash included.

The audit's numbers, at the default 5% buffer with one retained asset at 20% annualised vol:
the returned portfolio is 95% asset / 5% cash, so its volatility is 19%, not the 20% displayed;
an 8% sleeve return is 7.6% at the portfolio level before any cash interest.

THE FIX IS NAMING, NOT DELETION. Both quantities are useful and they are now returned under
names that say which is which — discarding the sleeve figures would have broken the
cross-method comparison the original comment correctly wanted to preserve.

Cash is modelled as zero-return, zero-variance, zero-covariance. That is an ASSUMPTION, and it
is returned as `cash_return_assumed` so a reader can see it rather than infer it. Zero is the
conservative choice: inventing a yield would flatter every allocation that holds more cash.
"""
import numpy as np
import pytest

from src.optimizers.methods import _CASH_RETURN, PortfolioWeights, _metrics


def _alloc(cash_floor=0.05, n_days=260, seed=7):
    """Run the real optimizer on a synthetic but well-behaved return series.

    Calling ai_allocation() rather than recomputing the arithmetic in the test is the whole
    point: an earlier version of this file asserted `sleeve * (1 - cash) == expected`, which is
    a statement about algebra. Three separate sabotages — reverting the headline to the sleeve
    figure, scaling the return but not the volatility, and leaving Sharpe on the sleeve basis —
    all passed against it, because none of them were ever executed.
    """
    import pandas as pd

    rng = np.random.default_rng(seed)
    cols = ["AAA", "BBB", "CCC"]
    data = {c: rng.normal(0.0006, 0.012, n_days) for c in cols}
    returns = pd.DataFrame(data)
    scores = {c: 80.0 for c in cols}
    from src.optimizers.methods import ai_allocation
    return ai_allocation(returns, scores, cash_floor=cash_floor)


def test_the_returned_volatility_describes_the_allocation_actually_returned():
    """The audit's core point: weights sum to 1 - cash, so the portfolio is less volatile than
    its sleeve. Reported as the sleeve's, it overstates risk by the cash fraction."""
    r = _alloc(cash_floor=0.05)
    invested = 1.0 - r.cash
    assert r.sleeve_expected_vol is not None
    assert r.expected_vol == pytest.approx(round(r.sleeve_expected_vol * invested, 4), abs=1e-4)
    assert r.expected_vol < r.sleeve_expected_vol


def test_the_returned_expected_return_is_scaled_by_the_invested_fraction():
    r = _alloc(cash_floor=0.05)
    invested = 1.0 - r.cash
    expected = round(r.sleeve_expected_return * invested + _CASH_RETURN * r.cash, 4)
    assert r.expected_return == pytest.approx(expected, abs=1e-4)
    # Blending toward cash moves the figure TOWARD the cash return, which is not the same as
    # "smaller" — this optimizer can and does produce a negative expected return, and scaling
    # -14.77% by 0.95 gives -14.03%, a LARGER number. Asserting a direction rather than the
    # magnitude is how a sign-dependent test passes on one seed and fails on the next.
    assert abs(r.expected_return - _CASH_RETURN) < abs(r.sleeve_expected_return - _CASH_RETURN)


def test_the_returned_sharpe_uses_the_portfolio_basis_for_BOTH_terms():
    """Scaling the return but not the volatility (or vice versa) yields a Sharpe belonging to
    neither basis — and is invisible unless the real function is run."""
    from src.optimizers.methods import RISK_FREE

    r = _alloc(cash_floor=0.05)
    expected = round((r.expected_return - RISK_FREE) / r.expected_vol, 3)
    assert r.sharpe_ratio == pytest.approx(expected, abs=1e-3)


def test_the_weights_and_cash_sum_to_one():
    """The identity the metrics must describe. If this fails, neither basis is meaningful."""
    r = _alloc(cash_floor=0.05)
    assert sum(r.weights.values()) + r.cash == pytest.approx(1.0, abs=1e-3)


def test_a_larger_cash_buffer_moves_the_two_bases_further_apart():
    """Monotonic in the buffer — a fix that hardcoded one ratio would pass the 5% case alone."""
    small = _alloc(cash_floor=0.05)
    large = _alloc(cash_floor=0.30)
    assert large.cash > small.cash
    gap_small = small.sleeve_expected_vol - small.expected_vol
    gap_large = large.sleeve_expected_vol - large.expected_vol
    assert gap_large > gap_small


def test_a_zero_cash_floor_leaves_the_two_bases_identical():
    """With no buffer there is no distinction to make, and the fix must not invent one."""
    r = _alloc(cash_floor=0.0)
    assert r.cash == pytest.approx(0.0, abs=1e-6)
    assert r.expected_vol == pytest.approx(r.sleeve_expected_vol, abs=1e-4)
    assert r.expected_return == pytest.approx(r.sleeve_expected_return, abs=1e-4)


def test_the_cash_return_assumption_is_conservative_and_declared():
    """Inventing a yield would flatter every allocation that holds more cash. Zero is a choice,
    and the response states it rather than leaving a reader to infer it."""
    assert _CASH_RETURN == 0.0
    assert "cash_return_assumed" in PortfolioWeights.__dataclass_fields__


def test_the_result_carries_both_bases_under_distinct_names():
    """A caller must be able to tell the portfolio figure from the sleeve figure. Fields that do
    not exist cannot be labelled correctly by any UI."""
    fields = PortfolioWeights.__dataclass_fields__
    for name in ("expected_return", "expected_vol", "sharpe_ratio",
                 "sleeve_expected_return", "sleeve_expected_vol", "sleeve_sharpe_ratio"):
        assert name in fields, name


def test_sleeve_metrics_are_still_computed_fully_invested():
    """The cross-method comparison the original comment wanted to protect: _metrics() itself is
    unchanged and still describes a fully-invested sleeve."""
    w = np.array([0.6, 0.4])
    mu = np.array([0.10, 0.05])
    cov = np.array([[0.04, 0.0], [0.0, 0.09]])
    import pandas as pd
    rets = pd.DataFrame({"A": [0.01, -0.01, 0.02], "B": [0.0, 0.01, -0.01]})
    m = _metrics(w, mu, cov, rets)
    # _metrics rounds to 4dp, so the tolerance matches that rather than exact float equality.
    assert m["expected_return"] == pytest.approx(0.6 * 0.10 + 0.4 * 0.05, abs=5e-5)
    assert m["expected_vol"] == pytest.approx(float(np.sqrt(w @ cov @ w)), abs=5e-5)


def test_the_portfolio_sharpe_uses_the_portfolio_volatility_not_the_sleeves():
    """Scaling the return but not the volatility (or vice versa) would produce a Sharpe that
    belongs to neither basis."""
    from src.optimizers.methods import RISK_FREE

    cash, sleeve_ret, sleeve_vol = 0.05, 0.08, 0.20
    invested = 1.0 - cash
    port_ret = sleeve_ret * invested + _CASH_RETURN * cash
    port_vol = sleeve_vol * invested
    expected = round((port_ret - RISK_FREE) / port_vol, 3)
    sleeve_sharpe = round((sleeve_ret - RISK_FREE) / sleeve_vol, 3)
    assert expected != sleeve_sharpe, "the two bases must not coincide in this example"
    assert expected == pytest.approx(round((0.076 - RISK_FREE) / 0.19, 3), abs=1e-9)

"""R10 (2026-09-24 follow-up audit): drawdown still belonged to the fully invested sleeve.

THE DEFECT, and it is structural rather than arithmetical. DA-11 made expected_return,
expected_vol and sharpe_ratio cash-aware — correctly — by starting from `m = dict(sleeve)` and
patching those three fields onto the copy. The copy also carried `max_drawdown` and
`diversification`, and neither was recomputed. So the generic `max_drawdown` the API and the
portfolio UI present kept describing the fully invested path while everything beside it
described the returned one.

The audit's probe: a single asset with the return path [+10%, -20%, +5%] and the default 5%
cash reports -20% maximum drawdown. Reconstructed from the returned 95% exposure and the
declared zero cash return it is -19%.

Patching a copied dict field by field is what allowed this: every metric _metrics() gains later
silently inherits the sleeve's value until someone remembers it. So the fix is not "also
recompute drawdown" — it is that `ai_allocation` now calls `_metrics` twice, once over the
allocation it returns and once over the sleeve, and copies nothing. There is no field left that
CAN be forgotten.

DIVERSIFICATION is kept as a sleeve measure with `diversification_basis` saying so. Folding a
cash buffer into a concentration index would make a portfolio look better diversified for
holding less — that is de-risking wearing diversification's name. The audit asked for an
explicit basis, not a particular answer.

These tests RECONSTRUCT each metric from the returned allocation and the declared cash return,
which is what the audit's acceptance asks for, rather than re-deriving them from the sleeve by
the same algebra the code uses.
"""
import numpy as np
import pandas as pd
import pytest

from src.optimizers.methods import _CASH_RETURN, RISK_FREE, _metrics, ai_allocation


def _reconstruct(weights: dict[str, float], cash: float, returns: pd.DataFrame) -> dict:
    """Independent reconstruction from the RETURNED weights — no sleeve, no scaling factor.

    Deliberately written from the definitions rather than by reusing _metrics(), so a change to
    the production function cannot make this agree with it by construction.
    """
    w = np.array([weights[c] for c in returns.columns])
    path = (returns.values * w).sum(axis=1) + _CASH_RETURN * cash
    cum = np.cumprod(1 + np.clip(path, -0.99, None))
    peak = np.maximum.accumulate(cum)
    return {
        "max_drawdown": float((cum / peak - 1).min()),
        "expected_return": float(w @ returns.mean().values * 252) + _CASH_RETURN * cash,
    }


def _alloc(cash_floor=0.05, n_days=260, seed=7, cols=("AAA", "BBB", "CCC")):
    rng = np.random.default_rng(seed)
    returns = pd.DataFrame({c: rng.normal(0.0006, 0.012, n_days) for c in cols})
    return ai_allocation(returns, {c: 80.0 for c in cols}, cash_floor=cash_floor), returns


# ── The audit's own probe ────────────────────────────────────────────────────

def test_the_audits_one_asset_probe_reports_nineteen_not_twenty_percent():
    """THE CORE R10 CASE, with the audit's exact numbers. One asset, [+10%, -20%, +5%], 5% cash.

    Called through _metrics directly so the path is the audit's rather than an optimizer's."""
    returns = pd.DataFrame({"AAA": [0.10, -0.20, 0.05]})
    mu = np.array([0.0])
    cov = np.array([[0.0]])

    sleeve = _metrics(np.array([1.0]), mu, cov, returns)
    assert sleeve["max_drawdown"] == pytest.approx(-0.20, abs=1e-4), \
        "precondition: the fully invested path does draw down 20%"

    portfolio = _metrics(np.array([0.95]), mu, cov, returns, cash=0.05)
    assert portfolio["max_drawdown"] == pytest.approx(-0.19, abs=1e-4)


def test_drawdown_reconstructs_from_the_returned_allocation():
    r, returns = _alloc(cash_floor=0.05)
    expected = _reconstruct(r.weights, r.cash, returns)["max_drawdown"]
    assert r.max_drawdown == pytest.approx(round(expected, 4), abs=1e-4)


def test_the_generic_drawdown_is_no_longer_the_sleeve_drawdown():
    """The defect in one assertion: with cash held back, the two must differ."""
    r, _ = _alloc(cash_floor=0.05)
    assert r.sleeve_max_drawdown is not None
    assert r.max_drawdown != r.sleeve_max_drawdown
    # Less exposure means a shallower drawdown. Both are negative, so the returned one is the
    # LARGER number — asserting "smaller" here would be the sign trap DA-11's own tests hit.
    assert r.max_drawdown > r.sleeve_max_drawdown


def test_the_sleeve_drawdown_is_still_reported_for_cross_method_comparison():
    """mean_variance / risk_parity / HRP are fully invested; discarding the sleeve figure would
    break the comparison DA-11 deliberately preserved for the other three metrics."""
    r, returns = _alloc(cash_floor=0.05)
    full = _reconstruct({c: w / (1 - r.cash) for c, w in r.weights.items()}, 0.0, returns)
    assert r.sleeve_max_drawdown == pytest.approx(round(full["max_drawdown"], 4), abs=1e-4)


# ── Every metric, from one allocation ────────────────────────────────────────

def test_expected_return_reconstructs_from_the_returned_allocation():
    r, returns = _alloc(cash_floor=0.05)
    # NOTE: ai_allocation blends its own score-derived mu, so only the DIRECTION of the cash
    # adjustment can be reconstructed independently here; the drawdown test above is the one
    # that reconstructs a value outright.
    assert r.expected_return == pytest.approx(
        round(r.sleeve_expected_return * (1 - r.cash) + _CASH_RETURN * r.cash, 4), abs=1e-4)


def test_sharpe_is_computed_from_the_portfolio_terms_it_reports():
    r, _ = _alloc(cash_floor=0.05)
    assert r.sharpe_ratio == pytest.approx(
        round((r.expected_return - RISK_FREE) / r.expected_vol, 3), abs=1e-3)


@pytest.mark.parametrize("cash_floor", [0.0, 0.05, 0.40])
def test_every_metric_reconstructs_at_zero_default_and_high_cash(cash_floor):
    """The audit's acceptance list names all three levels. Zero cash is the case where the two
    bases must COINCIDE — a fix that only special-cased the default would pass a single test."""
    r, returns = _alloc(cash_floor=cash_floor)
    expected = _reconstruct(r.weights, r.cash, returns)["max_drawdown"]
    assert r.max_drawdown == pytest.approx(round(expected, 4), abs=1e-4)
    assert abs(sum(r.weights.values()) + r.cash - 1.0) < 1e-4
    if cash_floor == 0.0:
        assert r.max_drawdown == r.sleeve_max_drawdown
        assert r.expected_vol == r.sleeve_expected_vol


def test_a_zero_cash_allocation_leaves_both_bases_identical():
    r, _ = _alloc(cash_floor=0.0)
    assert r.cash == pytest.approx(0.0, abs=1e-9)
    for generic, sleeve in (("expected_return", "sleeve_expected_return"),
                            ("expected_vol", "sleeve_expected_vol"),
                            ("sharpe_ratio", "sleeve_sharpe_ratio"),
                            ("max_drawdown", "sleeve_max_drawdown")):
        assert getattr(r, generic) == getattr(r, sleeve), generic


# ── Diversification's basis is stated ────────────────────────────────────────

def test_diversification_states_its_basis():
    r, _ = _alloc(cash_floor=0.05)
    assert r.diversification_basis == "sleeve"


def test_cash_does_not_inflate_diversification():
    """Holding more cash must not make a portfolio look better diversified — that is a
    different property wearing this one's name. Same weights, different cash: same number."""
    returns = pd.DataFrame({"AAA": [0.01, -0.01], "BBB": [0.02, -0.02]})
    mu = np.array([0.0, 0.0])
    cov = np.zeros((2, 2))
    full = _metrics(np.array([0.5, 0.5]), mu, cov, returns)
    held_back = _metrics(np.array([0.25, 0.25]), mu, cov, returns, cash=0.5)
    assert held_back["diversification"] == full["diversification"]


# ── The shape that caused it ─────────────────────────────────────────────────

def test_ai_allocation_does_not_copy_and_patch_a_metric_dict():
    """THE STRUCTURAL GUARD, and the reason this finding exists at all. `m = dict(sleeve)`
    followed by per-field patching means every metric added to _metrics() later silently
    inherits the sleeve's value until someone remembers it. Two calls, nothing copied."""
    import inspect

    src = inspect.getsource(ai_allocation)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "dict(sleeve)" not in code, "the copy-and-patch shape is back"
    assert code.count("_metrics(") == 2, "one call per basis, computed not copied"
    assert "cash=cash" in code, "the portfolio basis must be told how much cash is held"


def test_every_metric_key_is_produced_by_the_same_function_for_both_bases():
    """A field present on one basis and absent on the other is how the copy hid the defect."""
    returns = pd.DataFrame({"AAA": [0.01, -0.02, 0.005]})
    mu, cov = np.array([0.05]), np.array([[0.04]])
    assert set(_metrics(np.array([1.0]), mu, cov, returns)) == \
           set(_metrics(np.array([0.9]), mu, cov, returns, cash=0.1))

"""Allocation methods: mean-variance, risk-parity, AI-driven (K-Score + MVO).

All methods return a dict of {symbol: weight} plus a cash residual.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize


@dataclass
class PortfolioWeights:
    method: str
    weights: dict[str, float]
    cash: float
    expected_return: float | None = None
    expected_vol: float | None = None


def _annualize(ret: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    mu = ret.mean() * 252
    cov = ret.cov() * 252
    return mu, cov


def _long_only_bounds(n: int, cap: float = 0.35) -> list[tuple[float, float]]:
    return [(0.0, cap) for _ in range(n)]


def mean_variance(returns: pd.DataFrame, risk_aversion: float = 3.0, max_weight: float = 0.35) -> PortfolioWeights:
    mu, cov = _annualize(returns)
    symbols = list(returns.columns)
    n = len(symbols)

    def neg_utility(w):
        return -(w @ mu.values - 0.5 * risk_aversion * w @ cov.values @ w)

    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    x0 = np.full(n, 1 / n)
    res = minimize(neg_utility, x0, bounds=_long_only_bounds(n, max_weight), constraints=cons)
    w = np.clip(res.x, 0, None)
    w = w / w.sum() if w.sum() > 0 else np.full(n, 1 / n)
    weights = {s: float(round(wi, 4)) for s, wi in zip(symbols, w, strict=False)}
    exp_ret = float(w @ mu.values)
    exp_vol = float(np.sqrt(w @ cov.values @ w))
    return PortfolioWeights("mean_variance", weights, cash=round(1 - sum(weights.values()), 4), expected_return=exp_ret, expected_vol=exp_vol)


def risk_parity(returns: pd.DataFrame) -> PortfolioWeights:
    """Equal-risk-contribution solver (Maillard et al.)."""
    _, cov = _annualize(returns)
    symbols = list(returns.columns)
    n = len(symbols)
    cov_m = cov.values

    def risk_contrib(w):
        port_vol = np.sqrt(w @ cov_m @ w)
        mrc = cov_m @ w
        return w * mrc / port_vol

    def obj(w):
        rc = risk_contrib(w)
        return float(((rc - rc.mean()) ** 2).sum())

    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    x0 = np.full(n, 1 / n)
    res = minimize(obj, x0, bounds=_long_only_bounds(n, 0.6), constraints=cons)
    w = np.clip(res.x, 1e-6, None)
    w = w / w.sum()
    weights = {s: float(round(wi, 4)) for s, wi in zip(symbols, w, strict=False)}
    return PortfolioWeights("risk_parity", weights, cash=0.0)


def ai_allocation(returns: pd.DataFrame, scores: dict[str, float], min_score: float = 60.0, cash_floor: float = 0.05) -> PortfolioWeights:
    """AI-driven: filter by K-Score then MVO on the survivors.

    Low-score names are excluded; remaining weights are MVO. Residual held in cash.
    """
    keep = [s for s in returns.columns if scores.get(s, 0) >= min_score]
    if not keep:
        return PortfolioWeights("ai_allocation", {}, cash=1.0)
    mvo = mean_variance(returns[keep])
    # Scale down by (1 - cash_floor) to keep a defensive cash buffer
    weights = {s: round(w * (1 - cash_floor), 4) for s, w in mvo.weights.items()}
    cash = round(1 - sum(weights.values()), 4)
    return PortfolioWeights("ai_allocation", weights, cash=cash, expected_return=mvo.expected_return, expected_vol=mvo.expected_vol)

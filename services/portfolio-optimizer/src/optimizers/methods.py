"""Allocation methods: mean-variance (Sharpe-maximizing), risk-parity, HRP, AI-driven.

Improvements over naive MVO:
  - Ledoit-Wolf analytical covariance shrinkage (reduces estimation error)
  - James-Stein return shrinkage toward grand mean (reduces noise)
  - Tangency portfolio (maximize Sharpe) instead of utility-weighted MVO
  - Hierarchical Risk Parity using Ward clustering + recursive bisection
  - AI allocation blends K-Score views with historical returns before optimizing
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

RISK_FREE = 0.04  # annualized risk-free rate (US T-bill proxy)


@dataclass
class PortfolioWeights:
    method: str
    weights: dict[str, float]
    cash: float
    expected_return: float | None = None
    expected_vol: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    diversification: float | None = None


# ─── Covariance & return estimators ──────────────────────────────────────────

def _lw_covariance(returns: pd.DataFrame) -> np.ndarray:
    """Ledoit-Wolf analytical shrinkage — much better than raw sample cov for n<500."""
    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf(assume_centered=False).fit(returns.values)
        return lw.covariance_ * 252
    except ImportError:
        return returns.cov().values * 252


def _shrink_returns(mu: np.ndarray, shrink: float = 0.5) -> np.ndarray:
    """James-Stein shrinkage toward grand mean — reduces return estimation noise."""
    grand_mean = mu.mean()
    return (1.0 - shrink) * mu + shrink * grand_mean


def _prepare(returns: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return shrunk annualized (mu, cov)."""
    mu_raw = returns.mean().values * 252
    mu = _shrink_returns(mu_raw)
    cov = _lw_covariance(returns)
    return mu, cov


# ─── Portfolio metrics ────────────────────────────────────────────────────────

def _metrics(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, returns: pd.DataFrame) -> dict:
    exp_ret = float(w @ mu)
    exp_vol = float(np.sqrt(np.clip(w @ cov @ w, 0, None)))
    sharpe = round((exp_ret - RISK_FREE) / exp_vol, 3) if exp_vol > 1e-9 else 0.0

    port_rets = (returns.values * w).sum(axis=1)
    cum = np.cumprod(1 + np.clip(port_rets, -0.99, None))
    running_max = np.maximum.accumulate(cum)
    max_dd = float((cum / running_max - 1).min())

    hhi = float((w ** 2).sum())
    return {
        "expected_return": round(exp_ret, 4),
        "expected_vol": round(exp_vol, 4),
        "sharpe_ratio": sharpe,
        "max_drawdown": round(max_dd, 4),
        "diversification": round(1 - hhi, 4),
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _normalize(w: np.ndarray) -> np.ndarray:
    s = w.sum()
    return w / s if s > 1e-9 else np.full(len(w), 1 / len(w))


def _pack(symbols: list[str], w: np.ndarray, method: str,
          mu: np.ndarray, cov: np.ndarray, returns: pd.DataFrame,
          cash: float = 0.0) -> PortfolioWeights:
    m = _metrics(w, mu, cov, returns)
    weights = {s: float(round(wi, 4)) for s, wi in zip(symbols, w)}
    return PortfolioWeights(method, weights, cash=round(cash, 4), **m)


# ─── Method 1: Mean-Variance (Sharpe-maximizing tangency portfolio) ───────────

def mean_variance(returns: pd.DataFrame, max_weight: float = 0.40) -> PortfolioWeights:
    """Maximize Sharpe ratio (tangency portfolio) with Ledoit-Wolf covariance."""
    mu, cov = _prepare(returns)
    symbols = list(returns.columns)
    n = len(symbols)

    def neg_sharpe(w: np.ndarray) -> float:
        ret = float(w @ mu)
        vol = float(np.sqrt(np.clip(w @ cov @ w, 1e-12, None)))
        return -(ret - RISK_FREE) / vol

    x0 = np.full(n, 1 / n)
    res = minimize(
        neg_sharpe, x0,
        bounds=[(0.0, max_weight)] * n,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
        method="SLSQP",
        options={"ftol": 1e-9, "maxiter": 1000},
    )
    w = _normalize(np.clip(res.x, 0, None))
    return _pack(symbols, w, "mean_variance", mu, cov, returns)


# ─── Method 2: Equal-Risk-Contribution (Risk Parity) ─────────────────────────

def risk_parity(returns: pd.DataFrame, max_weight: float = 0.60) -> PortfolioWeights:
    """Equal-risk-contribution with Ledoit-Wolf covariance."""
    mu, cov = _prepare(returns)
    symbols = list(returns.columns)
    n = len(symbols)

    def risk_contribs(w: np.ndarray) -> np.ndarray:
        vol = np.sqrt(np.clip(w @ cov @ w, 1e-12, None))
        return w * (cov @ w) / vol

    def obj(w: np.ndarray) -> float:
        rc = risk_contribs(w)
        return float(((rc - rc.mean()) ** 2).sum())

    x0 = np.full(n, 1 / n)
    res = minimize(
        obj, x0,
        bounds=[(0.0, max_weight)] * n,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
        method="SLSQP",
        options={"ftol": 1e-12, "maxiter": 2000},
    )
    w = _normalize(np.clip(res.x, 1e-6, None))
    return _pack(symbols, w, "risk_parity", mu, cov, returns)


# ─── Method 3: Hierarchical Risk Parity (HRP) ────────────────────────────────

def hierarchical_risk_parity(returns: pd.DataFrame) -> PortfolioWeights:
    """HRP via Ward clustering + recursive bisection — robust to estimation error."""
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    mu, cov = _prepare(returns)
    symbols = list(returns.columns)
    n = len(symbols)

    vols = np.sqrt(np.diag(cov))
    outer_v = np.outer(vols, vols)
    corr = np.clip(cov / (outer_v + 1e-12), -1, 1)
    dist = np.sqrt(np.clip((1 - corr) / 2, 0, 1))
    np.fill_diagonal(dist, 0)

    link = linkage(squareform(dist, checks=False), method="ward")

    # Recover leaf order from linkage (quasi-diagonalization)
    def _order(node_id: int, n_leaves: int) -> list[int]:
        if node_id < n_leaves:
            return [node_id]
        row = link[int(node_id - n_leaves)]
        return _order(int(row[0]), n_leaves) + _order(int(row[1]), n_leaves)

    sorted_ix = _order(2 * n - 2, n)
    sorted_syms = [symbols[i] for i in sorted_ix]

    def _cluster_var(subset: list[str]) -> float:
        idx = [symbols.index(s) for s in subset]
        sub_cov = cov[np.ix_(idx, idx)]
        w_eq = np.full(len(idx), 1 / len(idx))
        return float(w_eq @ sub_cov @ w_eq)

    def _bisect(items: list[str]) -> dict[str, float]:
        if len(items) == 1:
            return {items[0]: 1.0}
        mid = len(items) // 2
        left, right = items[:mid], items[mid:]
        cv_l = _cluster_var(left)
        cv_r = _cluster_var(right)
        total = cv_l + cv_r
        alpha_r = cv_l / total if total > 0 else 0.5  # more weight to lower-risk cluster
        w_l = _bisect(left)
        w_r = _bisect(right)
        return {s: v * (1 - alpha_r) for s, v in w_l.items()} | \
               {s: v * alpha_r for s, v in w_r.items()}

    raw = _bisect(sorted_syms)
    w = np.array([raw.get(s, 0.0) for s in symbols])
    w = _normalize(w)
    return _pack(symbols, w, "hierarchical_risk_parity", mu, cov, returns)


# ─── Method 4: AI Allocation (K-Score views + Sharpe maximization) ───────────

def ai_allocation(
    returns: pd.DataFrame,
    scores: dict[str, float],
    min_score: float = 60.0,
    cash_floor: float = 0.05,
    max_weight: float = 0.40,
) -> PortfolioWeights:
    """Filter by K-Score, blend score-based return views with historical, maximize Sharpe."""
    keep = [s for s in returns.columns if scores.get(s, 0) >= min_score]
    if not keep:
        return PortfolioWeights("ai_allocation", {}, cash=1.0)

    ret_sub = returns[keep]
    mu, cov = _prepare(ret_sub)
    n = len(keep)

    # Normalize K-Scores → [0,1] then map to return range [µ-5%, µ+15%]
    raw_scores = np.array([scores.get(s, 50.0) for s in keep], dtype=float)
    s_min, s_max = raw_scores.min(), raw_scores.max()
    norm = (raw_scores - s_min) / (s_max - s_min + 1e-9)
    market_avg = float(mu.mean())
    score_views = market_avg - 0.05 + norm * 0.20  # [avg-5%, avg+15%]

    # Blend: 60% historical (shrunk) + 40% K-Score views
    blended_mu = 0.60 * mu + 0.40 * score_views

    def neg_sharpe(w: np.ndarray) -> float:
        ret = float(w @ blended_mu)
        vol = float(np.sqrt(np.clip(w @ cov @ w, 1e-12, None)))
        return -(ret - RISK_FREE) / vol

    x0 = np.full(n, 1 / n)
    res = minimize(
        neg_sharpe, x0,
        bounds=[(0.0, max_weight)] * n,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
        method="SLSQP",
        options={"ftol": 1e-9, "maxiter": 1000},
    )
    w = _normalize(np.clip(res.x, 0, None))
    w_scaled = w * (1 - cash_floor)
    cash = round(1 - float(w_scaled.sum()), 4)
    return _pack(keep, w_scaled, "ai_allocation", blended_mu, cov, ret_sub, cash=cash)

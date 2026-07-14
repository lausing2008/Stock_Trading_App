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

from common.logging import get_logger

log = get_logger("portfolio-optimizer")

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

    # TA-PO1: the equality constraint sum(w)=1.0 is infeasible whenever n * max_weight < 1.0
    # (e.g. n=2, max_weight=0.40 -> max feasible sum is 0.80). SLSQP then always reports
    # res.success=False and the code below silently falls back to flat 1/n weights with no
    # error surfaced — every 2-symbol mean_variance request was silently forced to 50/50
    # regardless of actual expected returns/risk. Skip optimization outright when infeasible,
    # matching the existing n==1 bypass already used in ai_allocation for the same root cause.
    if n * max_weight < 1.0:
        w = np.full(n, 1.0 / n)
    else:
        x0 = np.full(n, 1 / n)
        res = minimize(
            neg_sharpe, x0,
            bounds=[(0.0, max_weight)] * n,
            constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
            method="SLSQP",
            options={"ftol": 1e-9, "maxiter": 1000},
        )
        if res.success:
            w = _normalize(np.clip(res.x, 0, None))
        else:
            # T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT: SLSQP non-convergence (ill-conditioned
            # covariance, maxiter exhaustion, etc.) previously fell back to flat 1/n weights
            # with NO log line anywhere in this module — indistinguishable in the API response
            # from a genuine optimization result. Log so this is visible/debuggable in production.
            log.warning("portfolio.slsqp_failed_fallback_to_equal_weight", method="mean_variance",
                        n_symbols=n, message=res.message)
            w = np.full(n, 1.0 / n)
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

    # TA-PO1: same generalized infeasibility guard as mean_variance/ai_allocation — sum(w)=1.0
    # is infeasible whenever n * max_weight < 1.0. Latent today since routes.py only ever calls
    # this with the default max_weight=0.60 (n*0.60>=1.0 for all n>=2), but risk_parity() never
    # received this guard when TA-PO1 was applied elsewhere, so a smaller max_weight (direct
    # call, future request-schema field per skill.md's documented-but-unimplemented contract)
    # would silently hit the exact same SLSQP-infeasible-so-flat-1/n bug this guard prevents.
    if n * max_weight < 1.0:
        w = np.full(n, 1.0 / n)
    else:
        x0 = np.full(n, 1 / n)
        res = minimize(
            obj, x0,
            bounds=[(0.0, max_weight)] * n,
            constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
            method="SLSQP",
            options={"ftol": 1e-12, "maxiter": 2000},
        )
        if res.success:
            w = _normalize(np.clip(res.x, 1e-6, None))
        else:
            # T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT: see identical comment in mean_variance().
            log.warning("portfolio.slsqp_failed_fallback_to_equal_weight", method="risk_parity",
                        n_symbols=n, message=res.message)
            w = np.full(n, 1.0 / n)
    return _pack(symbols, w, "risk_parity", mu, cov, returns)


# ─── Method 3: Hierarchical Risk Parity (HRP) ────────────────────────────────

def _cap_and_redistribute(w: np.ndarray, max_weight: float) -> np.ndarray:
    """Clip any weight above max_weight down to it, redistributing the excess proportionally
    across the still-uncapped positions — a standard "water-filling" cap. Once a position is
    capped it is FROZEN at max_weight for the rest of the pass; only never-yet-capped
    positions absorb further excess. Without freezing, redistributing into a position that is
    itself close to the cap can push it back over on the very next iteration, oscillating
    between two capped positions forever without ever converging (found via a 3-asset test
    case during development: LOWVOL and MIDVOL alternated above/below 0.40 across every
    iteration and the fixed-iteration-count loop returned mid-oscillation, still violating the
    cap). Freezing guarantees each iteration either finishes or permanently caps at least one
    more position, so this always converges in at most n iterations.
    Falls back to equal weight if max_weight * n < 1.0 (capping alone can never reach 100%
    invested — same infeasibility condition TA-PO1 already guards for the SLSQP methods)."""
    n = len(w)
    if max_weight * n < 1.0:
        return np.full(n, 1.0 / n)
    w = w.copy()
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        candidates = ~frozen
        over = candidates & (w > max_weight)
        if not over.any():
            break
        excess = float((w[over] - max_weight).sum())
        w[over] = max_weight
        frozen |= over
        free = ~frozen
        free_total = float(w[free].sum())
        if free_total <= 1e-12:
            # Every remaining free position is ~0 — nothing meaningful left to redistribute
            # into; spread the remainder equally among them instead of dividing by ~0.
            if free.any():
                w[free] = excess / free.sum()
            break
        w[free] += excess * (w[free] / free_total)
    return _normalize(w)


def hierarchical_risk_parity(returns: pd.DataFrame, max_weight: float = 0.40) -> PortfolioWeights:
    """HRP via Ward clustering + recursive bisection — robust to estimation error.

    T247-PORTFOLIOOPTIMIZER-HRP-MAXWEIGHT: the recursive bisection had no concentration
    cap at all, unlike the other three allocation methods (which enforce max_weight via
    SLSQP bounds) — reproduced numerically with two very-different-volatility symbols
    yielding a 99.4%/0.6% split. Default 0.40 matches mean_variance/ai_allocation's default.
    """
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
    w = _cap_and_redistribute(w, max_weight)
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
    # T237-PO1: `scores` now only contains symbols the caller successfully fetched a score for
    # (see routes.py's _fetch_scores) — a symbol missing from this dict means its fetch FAILED,
    # not that it scored 0. Only filter on real scores so a transient ranking-engine failure
    # doesn't silently masquerade as "this stock doesn't meet the quality bar".
    keep = [s for s in returns.columns if scores.get(s, -1) >= min_score]
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

    # TA-PO1: generalized from the original n==1-only check — sum(w)=1.0 is infeasible
    # whenever n * max_weight < 1.0 (e.g. n=2, max_weight=0.40 -> max feasible sum 0.80),
    # not just n==1. See the identical fix/comment in mean_variance() above.
    if n * max_weight < 1.0:
        w = np.full(n, 1.0 / n)
    else:
        x0 = np.full(n, 1 / n)
        res = minimize(
            neg_sharpe, x0,
            bounds=[(0.0, max_weight)] * n,
            constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
            method="SLSQP",
            options={"ftol": 1e-9, "maxiter": 1000},
        )
        if res.success:
            w = _normalize(np.clip(res.x, 0, None))
        else:
            # T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT: see identical comment in mean_variance().
            log.warning("portfolio.slsqp_failed_fallback_to_equal_weight", method="ai_allocation",
                        n_symbols=n, message=res.message)
            w = np.full(n, 1.0 / n)
    w_scaled = w * (1 - cash_floor)
    cash = round(1 - float(w_scaled.sum()), 4)
    # Compute risk/return metrics on w (fully invested, sums to 1.0) so they are
    # comparable to mean_variance/risk_parity/HRP outputs. w_scaled (which sums to
    # 1-cash_floor) would understate expected_return and Sharpe by the cash fraction.
    m = _metrics(w, blended_mu, cov, ret_sub)
    return PortfolioWeights("ai_allocation",
                            {s: float(round(wi, 4)) for s, wi in zip(keep, w_scaled)},
                            cash=cash, **m)

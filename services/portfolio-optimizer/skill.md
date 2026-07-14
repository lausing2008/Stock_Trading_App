# Portfolio Optimizer — Domain Knowledge & Coding Standards

Runs quantitative portfolio optimization across multiple methods: mean-variance (Markowitz),
risk parity, hierarchical risk parity (HRP), and AI-guided allocation.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Optimization algorithms | `optimizers/methods.py` (~258 lines) |
| Optimization endpoints | `api/routes.py` (~113 lines) |

---

## Optimization Methods (`optimizers/methods.py`)

### Mean-Variance (Markowitz)
Classic efficient frontier optimization. Inputs: expected returns vector, covariance matrix.
Output: weight vector that maximizes Sharpe ratio (return per unit of risk).

Known limitation: sensitive to input estimation error. Small changes in expected returns can
cause large weight swings. Use with caution for individual stock selection.

### Risk Parity
Allocates weights so each asset contributes equally to portfolio volatility.
More stable than mean-variance — doesn't require expected return estimates.
Useful for diversification across uncorrelated assets.

### Hierarchical Risk Parity (HRP)
Clusters assets by correlation, then applies risk parity within and across clusters.
Most robust method — handles collinear assets and doesn't invert the covariance matrix.
Best default for multi-sector portfolios.

### AI Allocation
Corrected 2026-07-04 — this section previously said it calls the research engine; it does not.
`_fetch_scores()` (`api/routes.py`) calls **ranking-engine**'s `GET /rankings/{symbol}` and reads
the `"score"` field (K-score, 0–100), not a research-engine conviction/recommendation. The
`ai_allocation()` function itself (`optimizers/methods.py`) has no HTTP calls at all — it's a
pure numeric blender taking a plain `scores: dict[str, float]` argument (60% historical returns +
40% score-derived views). There is no reference to research-engine anywhere in this service's
source. If you want research-engine's AVOID/BUY conviction to influence allocation, that
integration does not exist yet — it would need to be built, not assumed present.

---

## Input / Output Contract

**Input (POST /portfolio/optimize) — this is the ONLY endpoint this service exposes; the
previously-documented `/portfolio/frontier` and `/portfolio/correlation` endpoints do not exist:**
```json
{
  "symbols": ["AAPL", "MSFT", "GOOG"],
  "method": "hierarchical_risk_parity", // the actual Literal value — NOT "hrp" or "ai" as
                                          // previously documented. Valid values: mean_variance |
                                          // risk_parity | hierarchical_risk_parity | ai_allocation
  "lookback_days": 252,         // historical window for covariance estimation
  "min_score": 60.0,            // ai_allocation only — K-Score floor for inclusion
  "constraints": {
    "max_weight": 0.40          // optional; maximum single position weight, applied to
                                  // whichever method is chosen (default 0.40/0.60 per method
                                  // if omitted — see methods.py's own defaults)
  }
}
```
**Corrected 2026-07-14 (T247-PORTFOLIOOPTIMIZER-SKILLMD-SCHEMA):** this section previously
also documented `target_return` and `constraints.min_weight` — NEITHER exists. No optimizer
method has target-return or lower-bound support; a request including them had those fields
silently dropped by Pydantic (no `extra="forbid"` on `OptimizeRequest`) and ran with default
behavior instead, with no error indicating the constraint was never applied.
`constraints.max_weight` (only) is real as of this fix — every method already computed its
own internal max_weight, this just exposes it through the request.

**Output:**
```json
{
  "weights": {"AAPL": 0.35, "MSFT": 0.40, "GOOG": 0.25},
  "expected_return": 0.18,
  "expected_volatility": 0.14,
  "sharpe_ratio": 1.29,
  "method": "hierarchical_risk_parity"
}
```

---

## Data Dependency

This service fetches historical prices from market-data for the covariance computation.
Lookback window: typically 252 trading days (1 year). HK and US stocks can be mixed, but
timezone normalization must happen before covariance calculation.

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /portfolio/optimize` | Yes | Run optimization, return weights — this is the ONLY endpoint `api/routes.py` defines |

Corrected 2026-07-04: `/portfolio/frontier` and `/portfolio/correlation` were previously
documented here but do not exist in the code. Do not build frontend features assuming they're
available without checking `api/routes.py` first.

## Known Stale Tracker Entry: Regime-Aware Sizing Was Never Built Here

An earlier `improvements.tsx` entry (Tier ~130s) documents "Portfolio-optimizer fetches regime
and applies position multiplier (bull=1.0, choppy=0.75, bear=0.60, risk_off=0.50)" as shipped.
**No such code exists** — no reference to `regime`, `decision_engine_url`, or `/decide/regime`
anywhere in `services/portfolio-optimizer/src/`. The regime-multiplier that DOES exist
(`_REGIME_MULT` in decision-engine's `sizer.py`) is not reachable from this service. Tracked as
`T232-DL7` — either correct the tracker entry or actually build this feature; do not assume it's
live.

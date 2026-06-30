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
Calls the research engine to get conviction scores per symbol, then uses them as tilt weights
on top of HRP. High-conviction BUY signals get a higher weight; AVOID signals get lower.

---

## Input / Output Contract

**Input (POST /portfolio/optimize):**
```json
{
  "symbols": ["AAPL", "MSFT", "GOOG"],
  "method": "hrp",              // mean_variance | risk_parity | hrp | ai
  "lookback_days": 252,         // historical window for covariance estimation
  "target_return": null,        // optional; used by mean_variance only
  "constraints": {
    "min_weight": 0.05,         // minimum position weight
    "max_weight": 0.40          // maximum single position weight
  }
}
```

**Output:**
```json
{
  "weights": {"AAPL": 0.35, "MSFT": 0.40, "GOOG": 0.25},
  "expected_return": 0.18,
  "expected_volatility": 0.14,
  "sharpe_ratio": 1.29,
  "method": "hrp"
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
| `POST /portfolio/optimize` | Yes | Run optimization, return weights |
| `GET /portfolio/frontier` | Yes | Efficient frontier curve (mean-variance only) |
| `GET /portfolio/correlation` | Yes | Correlation matrix for given symbols |

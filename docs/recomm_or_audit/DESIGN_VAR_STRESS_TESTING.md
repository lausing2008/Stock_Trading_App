# IF-01: VaR & Stress Testing

## Problem Statement

Currently no quantified measurement of portfolio downside risk. Users cannot answer:
- "What's my worst-case daily loss at 95% confidence?"
- "How would my portfolio perform in a 2008-style crash?"
- "Am I taking more risk than I realize?"

---

## Solution Overview

Implement Value-at-Risk (VaR) calculation and historical stress testing to quantify portfolio risk and simulate extreme scenarios.

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Scheduler      │────▶│  Risk Engine     │────▶│  risk_metrics   │
│  (daily job)    │     │  (new service)   │     │  (new table)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  stress_tests    │
                        │  (new table)     │
                        └──────────────────┘
```

---

## Database Schema

```python
# shared/db/models.py

class PortfolioRiskMetric(Base):
    """Daily risk metrics for each portfolio."""
    __tablename__ = "portfolio_risk_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    
    # VaR metrics (negative values = potential loss)
    var_95_1d: Mapped[float | None] = mapped_column(Float, nullable=True)  # 95% 1-day VaR
    var_99_1d: Mapped[float | None] = mapped_column(Float, nullable=True)  # 99% 1-day VaR
    var_95_10d: Mapped[float | None] = mapped_column(Float, nullable=True) # 95% 10-day VaR
    
    # CVaR (Expected Shortfall) - average loss beyond VaR
    cvar_95_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Portfolio volatility
    volatility_20d: Mapped[float | None] = mapped_column(Float, nullable=True)  # annualized
    volatility_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Concentration risk
    herfindahl_index: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1, higher = more concentrated
    top3_weight: Mapped[float | None] = mapped_column(Float, nullable=True)  # % in top 3 positions
    
    # Correlation risk
    avg_correlation: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg pairwise correlation
    max_correlation: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("portfolio_id", "as_of", name="uq_risk_metric_portfolio_date"),
    )


class StressTestResult(Base):
    """Results of stress test scenarios."""
    __tablename__ = "stress_test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    scenario_name: Mapped[str] = mapped_column(String(64))  # e.g., "2008_crisis", "covid_crash"
    
    # Scenario parameters
    scenario_description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    market_shock_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # e.g., -35%
    
    # Results
    portfolio_impact_pct: Mapped[float] = mapped_column(Float)  # estimated portfolio loss %
    portfolio_impact_usd: Mapped[float] = mapped_column(Float)  # estimated portfolio loss $
    worst_position_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    worst_position_impact_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Position-level breakdown (JSON)
    position_impacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("portfolio_id", "as_of", "scenario_name", name="uq_stress_test_portfolio_date_scenario"),
    )
```

---

## API Endpoints

```python
# services/risk-engine/src/api/routes.py

@router.get("/portfolio/{portfolio_id}/var")
async def get_portfolio_var(portfolio_id: int) -> dict:
    """Get current VaR metrics for a portfolio."""
    return {
        "portfolio_id": portfolio_id,
        "as_of": "2026-08-16",
        "var_95_1d": -2450.00,  # 95% chance daily loss won't exceed $2,450
        "var_99_1d": -4120.00,
        "cvar_95_1d": -3200.00,  # If loss exceeds VaR, expect ~$3,200 avg loss
        "volatility_20d_annualized": 0.18,
        "interpretation": "95% confident daily loss won't exceed $2,450"
    }


@router.get("/portfolio/{portfolio_id}/stress-test")
async def get_stress_tests(portfolio_id: int) -> list[dict]:
    """Get stress test results for predefined scenarios."""
    return [
        {
            "scenario": "2008_financial_crisis",
            "market_shock": -0.35,
            "portfolio_impact_pct": -0.28,
            "portfolio_impact_usd": -28000,
            "description": "S&P 500 dropped 35% in 2008"
        },
        {
            "scenario": "covid_crash_2020",
            "market_shock": -0.34,
            "portfolio_impact_pct": -0.31,
            "portfolio_impact_usd": -31000,
            "description": "March 2020 COVID crash"
        },
        {
            "scenario": "rate_hike_2022",
            "market_shock": -0.25,
            "portfolio_impact_pct": -0.22,
            "portfolio_impact_usd": -22000,
            "description": "2022 Fed rate hike cycle"
        }
    ]


@router.post("/portfolio/{portfolio_id}/stress-test/custom")
async def run_custom_stress_test(portfolio_id: int, body: CustomStressTestRequest) -> dict:
    """Run a custom stress test with user-defined shocks."""
    # body: {"market_shock_pct": -0.20, "sector_shocks": {"Technology": -0.30}}
    pass
```

---

## Core Calculation Logic

```python
# services/risk-engine/src/services/var_calculator.py

import numpy as np
from scipy import stats

def calculate_historical_var(
    returns: np.ndarray,
    confidence: float = 0.95,
    horizon_days: int = 1
) -> float:
    """
    Historical VaR using actual return distribution.
    
    Args:
        returns: Array of daily returns (e.g., [-0.02, 0.01, -0.005, ...])
        confidence: Confidence level (0.95 = 95%)
        horizon_days: Time horizon in days
    
    Returns:
        VaR as a negative percentage (e.g., -0.025 = -2.5% potential loss)
    """
    # Sort returns ascending (worst to best)
    sorted_returns = np.sort(returns)
    
    # Find the return at the (1 - confidence) percentile
    index = int((1 - confidence) * len(sorted_returns))
    var_1d = sorted_returns[index]
    
    # Scale to horizon (square root of time rule)
    var_nd = var_1d * np.sqrt(horizon_days)
    
    return var_nd


def calculate_parametric_var(
    returns: np.ndarray,
    confidence: float = 0.95,
    horizon_days: int = 1
) -> float:
    """
    Parametric VaR assuming normal distribution.
    """
    mu = np.mean(returns)
    sigma = np.std(returns)
    
    # Z-score for confidence level
    z = stats.norm.ppf(1 - confidence)
    
    var_1d = mu + z * sigma
    var_nd = var_1d * np.sqrt(horizon_days)
    
    return var_nd


def calculate_cvar(returns: np.ndarray, confidence: float = 0.95) -> float:
    """
    Conditional VaR (Expected Shortfall) - average loss beyond VaR.
    """
    var = calculate_historical_var(returns, confidence)
    
    # Average of returns worse than VaR
    tail_returns = returns[returns <= var]
    cvar = np.mean(tail_returns) if len(tail_returns) > 0 else var
    
    return cvar


def calculate_portfolio_var(
    positions: list[dict],  # [{"symbol": "AAPL", "weight": 0.25, "returns": [...]}]
    confidence: float = 0.95
) -> dict:
    """
    Portfolio VaR accounting for correlations.
    """
    n = len(positions)
    weights = np.array([p["weight"] for p in positions])
    
    # Build covariance matrix
    returns_matrix = np.array([p["returns"] for p in positions])
    cov_matrix = np.cov(returns_matrix)
    
    # Portfolio variance
    port_variance = weights @ cov_matrix @ weights.T
    port_std = np.sqrt(port_variance)
    
    # Portfolio mean return
    port_mean = np.mean([np.mean(p["returns"]) * p["weight"] for p in positions])
    
    # Parametric VaR
    z = stats.norm.ppf(1 - confidence)
    var = port_mean + z * port_std
    
    return {
        "var": var,
        "portfolio_std": port_std,
        "diversification_benefit": 1 - (port_std / np.sum(weights * np.std(returns_matrix, axis=1)))
    }
```

---

## Stress Test Scenarios

```python
# services/risk-engine/src/services/stress_scenarios.py

PREDEFINED_SCENARIOS = {
    "2008_financial_crisis": {
        "description": "2008 Global Financial Crisis",
        "market_shock": -0.35,
        "sector_shocks": {
            "Financials": -0.55,
            "Real Estate": -0.45,
            "Consumer Discretionary": -0.40,
            "Technology": -0.35,
            "Healthcare": -0.20,
            "Utilities": -0.15,
        },
        "vix_level": 80,
    },
    "covid_crash_2020": {
        "description": "March 2020 COVID-19 Crash",
        "market_shock": -0.34,
        "sector_shocks": {
            "Energy": -0.50,
            "Financials": -0.40,
            "Industrials": -0.38,
            "Technology": -0.25,
            "Healthcare": -0.15,
        },
        "vix_level": 82,
    },
    "rate_hike_2022": {
        "description": "2022 Fed Rate Hike Cycle",
        "market_shock": -0.25,
        "sector_shocks": {
            "Technology": -0.35,
            "Consumer Discretionary": -0.30,
            "Real Estate": -0.28,
            "Utilities": -0.10,
            "Energy": 0.15,  # Energy outperformed
        },
        "vix_level": 35,
    },
    "flash_crash": {
        "description": "Sudden 10% Market Drop (1-day)",
        "market_shock": -0.10,
        "duration_days": 1,
    },
    "stagflation": {
        "description": "High Inflation + Slow Growth",
        "market_shock": -0.20,
        "sector_shocks": {
            "Technology": -0.30,
            "Consumer Discretionary": -0.25,
            "Energy": 0.10,
            "Materials": 0.05,
        },
    },
}


def run_stress_test(positions: list[dict], scenario_name: str) -> dict:
    """
    Apply stress scenario to portfolio positions.
    
    Args:
        positions: [{"symbol": "AAPL", "sector": "Technology", "value": 10000}, ...]
        scenario_name: Key from PREDEFINED_SCENARIOS
    
    Returns:
        Impact analysis
    """
    scenario = PREDEFINED_SCENARIOS[scenario_name]
    market_shock = scenario["market_shock"]
    sector_shocks = scenario.get("sector_shocks", {})
    
    total_value = sum(p["value"] for p in positions)
    position_impacts = []
    
    for pos in positions:
        # Use sector-specific shock if available, else market shock
        shock = sector_shocks.get(pos["sector"], market_shock)
        impact_usd = pos["value"] * shock
        
        position_impacts.append({
            "symbol": pos["symbol"],
            "sector": pos["sector"],
            "current_value": pos["value"],
            "shock_applied": shock,
            "impact_usd": impact_usd,
            "impact_pct": shock,
        })
    
    total_impact = sum(p["impact_usd"] for p in position_impacts)
    
    return {
        "scenario": scenario_name,
        "description": scenario["description"],
        "market_shock": market_shock,
        "portfolio_impact_usd": total_impact,
        "portfolio_impact_pct": total_impact / total_value,
        "position_impacts": position_impacts,
        "worst_position": min(position_impacts, key=lambda x: x["impact_usd"]),
    }
```

---

## Scheduler Job

```python
# services/market-data/src/services/scheduler.py

def compute_daily_risk_metrics() -> None:
    """Daily job to compute VaR and stress tests for all portfolios."""
    with _get_session() as session:
        portfolios = session.query(PaperPortfolio).filter(
            PaperPortfolio.is_active == True
        ).all()
        
        for portfolio in portfolios:
            # Get open positions
            positions = _get_portfolio_positions(session, portfolio.id)
            if not positions:
                continue
            
            # Fetch 252 days of returns for each position
            position_data = []
            for pos in positions:
                returns = _fetch_daily_returns(pos.symbol, days=252)
                position_data.append({
                    "symbol": pos.symbol,
                    "weight": pos.value / portfolio.equity,
                    "returns": returns,
                    "sector": pos.sector,
                    "value": pos.value,
                })
            
            # Calculate VaR
            var_result = calculate_portfolio_var(position_data, confidence=0.95)
            
            # Save risk metrics
            metric = PortfolioRiskMetric(
                portfolio_id=portfolio.id,
                as_of=date.today(),
                var_95_1d=var_result["var"] * portfolio.equity,
                var_99_1d=calculate_portfolio_var(position_data, 0.99)["var"] * portfolio.equity,
                cvar_95_1d=calculate_cvar([p["returns"] for p in position_data], 0.95) * portfolio.equity,
                volatility_20d=var_result["portfolio_std"] * np.sqrt(252),
            )
            session.merge(metric)
            
            # Run stress tests
            for scenario_name in PREDEFINED_SCENARIOS:
                result = run_stress_test(position_data, scenario_name)
                stress = StressTestResult(
                    portfolio_id=portfolio.id,
                    as_of=date.today(),
                    scenario_name=scenario_name,
                    portfolio_impact_pct=result["portfolio_impact_pct"],
                    portfolio_impact_usd=result["portfolio_impact_usd"],
                    position_impacts=result["position_impacts"],
                )
                session.merge(stress)
            
            session.commit()

# Register: runs daily after market close
scheduler.add_job(compute_daily_risk_metrics, "cron", hour=17, minute=30)
```

---

## Frontend Integration

```typescript
// frontend/src/pages/risk.tsx

interface RiskDashboard {
  var95: number;
  var99: number;
  cvar95: number;
  volatility: number;
  stressTests: StressTestResult[];
}

// Display components:
// 1. VaR gauge showing current risk level
// 2. Stress test table with scenario impacts
// 3. Risk trend chart over time
// 4. Position-level risk contribution breakdown
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| VaR breach rate | < 5% of days (for 95% VaR) |
| Stress test coverage | All portfolios daily |
| Computation time | < 30s per portfolio |

---

*Created: 2026-08-16*

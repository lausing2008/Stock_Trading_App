# IF-07: Factor Exposure Analysis

## Problem Statement

Unknown risk factor concentrations in portfolio. Cannot answer:
- How much of my return comes from market beta vs stock selection?
- Am I overexposed to growth/value/momentum factors?
- What happens if interest rates rise?

---

## Solution Overview

Decompose portfolio returns into factor exposures using Fama-French + custom factors.

---

## Database Schema

```python
class FactorExposure(Base):
    """Daily factor exposure for portfolios."""
    __tablename__ = "factor_exposures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    
    # Fama-French factors
    beta_market: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_smb: Mapped[float | None] = mapped_column(Float, nullable=True)  # Small minus Big
    beta_hml: Mapped[float | None] = mapped_column(Float, nullable=True)  # High minus Low (value)
    beta_mom: Mapped[float | None] = mapped_column(Float, nullable=True)  # Momentum
    beta_qmj: Mapped[float | None] = mapped_column(Float, nullable=True)  # Quality
    
    # Sector exposures
    sector_exposures: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    # Interest rate sensitivity
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # R-squared of factor model
    r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha: Mapped[float | None] = mapped_column(Float, nullable=True)  # Unexplained return

    __table_args__ = (UniqueConstraint("portfolio_id", "as_of"),)
```

---

## Core Logic

```python
import statsmodels.api as sm

def calculate_factor_exposures(portfolio_returns: pd.Series, factor_returns: pd.DataFrame) -> dict:
    """
    Regress portfolio returns on factor returns.
    
    portfolio_returns: Daily returns of portfolio
    factor_returns: DataFrame with columns [Mkt-RF, SMB, HML, Mom, QMJ]
    """
    # Align dates
    aligned = pd.concat([portfolio_returns, factor_returns], axis=1).dropna()
    
    y = aligned.iloc[:, 0]
    X = sm.add_constant(aligned.iloc[:, 1:])
    
    model = sm.OLS(y, X).fit()
    
    return {
        "alpha": model.params["const"] * 252,  # Annualized
        "beta_market": model.params.get("Mkt-RF", 0),
        "beta_smb": model.params.get("SMB", 0),
        "beta_hml": model.params.get("HML", 0),
        "beta_mom": model.params.get("Mom", 0),
        "r_squared": model.rsquared,
    }
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Factor model R² | > 0.70 |
| Alpha significance | p < 0.05 |

---

# IF-08: Alternative Data Integration

## Problem Statement

Limited to price/fundamental data. Missing:
- Consumer behavior signals
- Company operational metrics
- Real-time economic activity

---

## Data Sources

| Source | Signal | Stocks Affected |
|--------|--------|-----------------|
| Satellite parking lots | Retail traffic | WMT, TGT, COST |
| App downloads | Growth signal | Tech companies |
| Job postings | Expansion signal | All |
| Web traffic | Engagement | E-commerce, media |
| Credit card data | Consumer spending | Retail, restaurants |

---

## Database Schema

```python
class AlternativeDataPoint(Base):
    """Alternative data readings."""
    __tablename__ = "alternative_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    data_type: Mapped[str] = mapped_column(String(32), index=True)  # app_downloads, web_traffic, etc.
    as_of: Mapped[date] = mapped_column(Date, index=True)
    
    value: Mapped[float] = mapped_column(Float)
    value_yoy_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_mom_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    z_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # vs historical
    signal: Mapped[str | None] = mapped_column(String(16), nullable=True)  # BULLISH/BEARISH/NEUTRAL
    
    source: Mapped[str] = mapped_column(String(32))
    
    __table_args__ = (UniqueConstraint("stock_id", "data_type", "as_of"),)
```

---

# IF-09: Market Microstructure

## Problem Statement

Missing short-term flow signals:
- Order book imbalance
- Dark pool activity
- Retail vs institutional flow

---

## Signals

```python
MICROSTRUCTURE_SIGNALS = {
    "order_book_imbalance": {
        "calculation": "(bid_volume - ask_volume) / (bid_volume + ask_volume)",
        "bullish_threshold": 0.3,
        "bearish_threshold": -0.3,
    },
    "odd_lot_ratio": {
        "calculation": "odd_lot_volume / total_volume",
        "interpretation": "High ratio = retail dominated",
    },
    "dark_pool_pct": {
        "calculation": "dark_pool_volume / total_volume",
        "interpretation": "High % = institutional activity",
    },
}
```

---

# IF-10: Portfolio Attribution

## Problem Statement

Unknown source of returns. Cannot answer:
- Did I make money from stock picking or sector allocation?
- Which positions contributed most to performance?
- How much did trading costs hurt returns?

---

## Brinson Attribution

```python
def brinson_attribution(
    portfolio_weights: dict,
    portfolio_returns: dict,
    benchmark_weights: dict,
    benchmark_returns: dict
) -> dict:
    """
    Decompose active return into allocation and selection effects.
    
    Allocation Effect = Σ (Wp - Wb) × Rb
    Selection Effect = Σ Wb × (Rp - Rb)
    Interaction Effect = Σ (Wp - Wb) × (Rp - Rb)
    """
    allocation = 0
    selection = 0
    interaction = 0
    
    sectors = set(portfolio_weights.keys()) | set(benchmark_weights.keys())
    
    for sector in sectors:
        wp = portfolio_weights.get(sector, 0)
        wb = benchmark_weights.get(sector, 0)
        rp = portfolio_returns.get(sector, 0)
        rb = benchmark_returns.get(sector, 0)
        
        allocation += (wp - wb) * rb
        selection += wb * (rp - rb)
        interaction += (wp - wb) * (rp - rb)
    
    return {
        "allocation_effect": allocation,
        "selection_effect": selection,
        "interaction_effect": interaction,
        "total_active_return": allocation + selection + interaction,
    }
```

---

# IF-11: Multi-Strategy Framework

## Problem Statement

All strategies share same capital pool:
- Can't isolate strategy performance
- Correlated drawdowns compound
- No dynamic capital allocation

---

## Database Schema

```python
class StrategyAllocation(Base):
    """Capital allocation to each strategy."""
    __tablename__ = "strategy_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id"), index=True)
    strategy_name: Mapped[str] = mapped_column(String(64), index=True)
    
    allocated_capital: Mapped[float] = mapped_column(Float)
    current_equity: Mapped[float] = mapped_column(Float)
    max_drawdown_limit: Mapped[float] = mapped_column(Float, default=0.15)
    
    # Performance tracking
    inception_date: Mapped[date] = mapped_column(Date)
    total_return: Mapped[float] = mapped_column(Float, default=0)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_drawdown: Mapped[float] = mapped_column(Float, default=0)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
```

---

# IF-12: Compliance & Audit

## Problem Statement

No pre-trade checks or audit trail:
- Position limits not enforced
- No trade surveillance
- Decisions not logged

---

## Pre-Trade Compliance

```python
class ComplianceRule(Base):
    """Compliance rules for pre-trade checks."""
    __tablename__ = "compliance_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(32))  # position_limit, sector_limit, etc.
    
    # Rule parameters
    max_position_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_sector_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    restricted_symbols: Mapped[list | None] = mapped_column(JSON, nullable=True)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


def check_compliance(order: dict, portfolio: dict, rules: list[ComplianceRule]) -> tuple[bool, list[str]]:
    """Check if order passes all compliance rules."""
    violations = []
    
    for rule in rules:
        if rule.rule_type == "position_limit":
            new_position_pct = (order["value"] + portfolio["positions"].get(order["symbol"], 0)) / portfolio["equity"]
            if new_position_pct > rule.max_position_pct:
                violations.append(f"Position limit exceeded: {new_position_pct:.1%} > {rule.max_position_pct:.1%}")
        
        elif rule.rule_type == "sector_limit":
            # Check sector concentration
            pass
        
        elif rule.rule_type == "restricted_list":
            if order["symbol"] in rule.restricted_symbols:
                violations.append(f"Symbol {order['symbol']} is on restricted list")
    
    return len(violations) == 0, violations
```

---

# IF-13: Regime-Aware Position Sizing

## Problem Statement

Static sizing ignores market conditions:
- Same size in bull and bear markets
- No volatility targeting
- Kelly criterion assumes stable parameters

---

## Solution

```python
class RegimeAwareSizer:
    """Position sizing that adapts to market regime."""
    
    REGIME_MULTIPLIERS = {
        "bull": 1.0,
        "high_vol": 0.6,
        "bear": 0.4,
        "crisis": 0.2,
    }
    
    def calculate_size(
        self,
        base_kelly: float,
        regime: str,
        current_vol: float,
        target_vol: float = 0.15
    ) -> float:
        """
        Adjust position size for regime and volatility.
        
        base_kelly: Kelly-optimal fraction
        regime: Current market regime
        current_vol: Current annualized volatility
        target_vol: Target portfolio volatility
        """
        # Regime adjustment
        regime_mult = self.REGIME_MULTIPLIERS.get(regime, 0.5)
        
        # Volatility targeting
        vol_mult = target_vol / current_vol if current_vol > 0 else 1.0
        vol_mult = min(1.5, max(0.5, vol_mult))  # Cap adjustments
        
        # Combined
        adjusted_size = base_kelly * regime_mult * vol_mult
        
        return min(0.25, adjusted_size)  # Never more than 25% in one position
```

---

*Created: 2026-08-16*

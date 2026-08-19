# IF-04: Cross-Asset Signals

## Problem Statement

Equity-only view misses macro context:
- Bond yields predict sector rotation
- Credit spreads signal risk appetite
- Currency moves impact multinationals
- Commodities drive sector performance

---

## Solution Overview

Ingest cross-asset data and generate signals that inform equity positioning.

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Data Sources   │────▶│  Cross-Asset     │────▶│  cross_asset    │
│  (FRED, yf)     │     │  Engine          │     │  _signals       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Sector rotation │
                        │  recommendations │
                        └──────────────────┘
```

---

## Database Schema

```python
class CrossAssetReading(Base):
    """Daily cross-asset market readings."""
    __tablename__ = "cross_asset_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, unique=True, index=True)
    
    # Treasury yields
    yield_2y: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_10y: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_30y: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_curve_2s10s: Mapped[float | None] = mapped_column(Float, nullable=True)  # 10y - 2y spread
    
    # Credit spreads
    ig_spread: Mapped[float | None] = mapped_column(Float, nullable=True)  # Investment grade OAS
    hy_spread: Mapped[float | None] = mapped_column(Float, nullable=True)  # High yield OAS
    
    # Currencies
    dxy: Mapped[float | None] = mapped_column(Float, nullable=True)  # Dollar index
    eurusd: Mapped[float | None] = mapped_column(Float, nullable=True)
    usdjpy: Mapped[float | None] = mapped_column(Float, nullable=True)
    usdcny: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Commodities
    wti_oil: Mapped[float | None] = mapped_column(Float, nullable=True)
    gold: Mapped[float | None] = mapped_column(Float, nullable=True)
    copper: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Volatility
    vix: Mapped[float | None] = mapped_column(Float, nullable=True)
    move_index: Mapped[float | None] = mapped_column(Float, nullable=True)  # Bond volatility
    
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CrossAssetSignal(Base):
    """Derived signals from cross-asset analysis."""
    __tablename__ = "cross_asset_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    signal_type: Mapped[str] = mapped_column(String(32), index=True)
    
    # Signal details
    direction: Mapped[str] = mapped_column(String(16))  # RISK_ON, RISK_OFF, NEUTRAL
    strength: Mapped[float] = mapped_column(Float)  # 0-100
    
    # Sector implications
    sectors_favored: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sectors_avoid: Mapped[list | None] = mapped_column(JSON, nullable=True)
    
    # Reasoning
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("as_of", "signal_type", name="uq_cross_asset_signal"),
    )
```

---

## Signal Definitions

```python
# services/market-data/src/services/cross_asset_signals.py

CROSS_ASSET_RULES = {
    "yield_curve_inversion": {
        "condition": lambda r: r.yield_curve_2s10s < 0,
        "direction": "RISK_OFF",
        "sectors_favored": ["Utilities", "Healthcare", "Consumer Staples"],
        "sectors_avoid": ["Financials", "Industrials"],
        "reasoning": "Inverted yield curve signals recession risk"
    },
    
    "yield_curve_steepening": {
        "condition": lambda r: r.yield_curve_2s10s > 1.0,
        "direction": "RISK_ON",
        "sectors_favored": ["Financials", "Industrials", "Materials"],
        "sectors_avoid": ["Utilities", "Real Estate"],
        "reasoning": "Steep curve benefits banks, signals growth"
    },
    
    "credit_stress": {
        "condition": lambda r: r.hy_spread > 500,  # 500 bps
        "direction": "RISK_OFF",
        "sectors_favored": ["Healthcare", "Utilities"],
        "sectors_avoid": ["Consumer Discretionary", "Technology"],
        "reasoning": "Wide credit spreads signal stress"
    },
    
    "dollar_strength": {
        "condition": lambda r: r.dxy > 105,
        "direction": "NEUTRAL",
        "sectors_favored": ["Domestic-focused"],
        "sectors_avoid": ["Multinationals", "Emerging Markets"],
        "reasoning": "Strong dollar hurts exporters"
    },
    
    "oil_spike": {
        "condition": lambda r: r.wti_oil > 90,
        "direction": "MIXED",
        "sectors_favored": ["Energy"],
        "sectors_avoid": ["Airlines", "Consumer Discretionary"],
        "reasoning": "High oil benefits energy, hurts consumers"
    },
    
    "copper_gold_ratio_rising": {
        "condition": lambda r, prev: (r.copper / r.gold) > (prev.copper / prev.gold) * 1.05,
        "direction": "RISK_ON",
        "sectors_favored": ["Industrials", "Materials", "Technology"],
        "sectors_avoid": ["Gold Miners", "Utilities"],
        "reasoning": "Rising copper/gold ratio signals growth optimism"
    },
    
    "vix_spike": {
        "condition": lambda r: r.vix > 25,
        "direction": "RISK_OFF",
        "sectors_favored": ["Utilities", "Consumer Staples"],
        "sectors_avoid": ["Technology", "Small Caps"],
        "reasoning": "Elevated VIX signals fear"
    },
}


def generate_cross_asset_signals(reading: CrossAssetReading, prev_reading: CrossAssetReading) -> list[dict]:
    """Generate signals from cross-asset readings."""
    signals = []
    
    for signal_type, rule in CROSS_ASSET_RULES.items():
        try:
            if "prev" in rule["condition"].__code__.co_varnames:
                triggered = rule["condition"](reading, prev_reading)
            else:
                triggered = rule["condition"](reading)
            
            if triggered:
                signals.append({
                    "signal_type": signal_type,
                    "direction": rule["direction"],
                    "sectors_favored": rule["sectors_favored"],
                    "sectors_avoid": rule["sectors_avoid"],
                    "reasoning": rule["reasoning"],
                })
        except Exception:
            continue
    
    return signals
```

---

## Data Fetching

```python
# services/market-data/src/services/cross_asset_fetcher.py

import yfinance as yf
from fredapi import Fred

FRED_API_KEY = os.getenv("FRED_API_KEY")

async def fetch_cross_asset_data() -> dict:
    """Fetch all cross-asset data points."""
    fred = Fred(api_key=FRED_API_KEY)
    
    data = {}
    
    # Treasury yields from FRED
    data["yield_2y"] = fred.get_series("DGS2").iloc[-1]
    data["yield_10y"] = fred.get_series("DGS10").iloc[-1]
    data["yield_30y"] = fred.get_series("DGS30").iloc[-1]
    data["yield_curve_2s10s"] = data["yield_10y"] - data["yield_2y"]
    
    # Credit spreads from FRED
    data["ig_spread"] = fred.get_series("BAMLC0A0CM").iloc[-1]  # ICE BofA IG OAS
    data["hy_spread"] = fred.get_series("BAMLH0A0HYM2").iloc[-1]  # ICE BofA HY OAS
    
    # Currencies and commodities from yfinance
    tickers = {
        "dxy": "DX-Y.NYB",
        "eurusd": "EURUSD=X",
        "usdjpy": "JPY=X",
        "wti_oil": "CL=F",
        "gold": "GC=F",
        "copper": "HG=F",
        "vix": "^VIX",
    }
    
    for key, ticker in tickers.items():
        try:
            t = yf.Ticker(ticker)
            data[key] = t.fast_info.get("lastPrice")
        except:
            data[key] = None
    
    return data
```

---

## API Endpoints

```python
@router.get("/cross-asset/signals")
async def get_cross_asset_signals() -> list[dict]:
    """Get current cross-asset signals."""
    return [
        {
            "signal_type": "yield_curve_steepening",
            "direction": "RISK_ON",
            "strength": 72,
            "sectors_favored": ["Financials", "Industrials"],
            "sectors_avoid": ["Utilities"],
            "reasoning": "Steep curve benefits banks"
        }
    ]


@router.get("/cross-asset/readings")
async def get_cross_asset_readings(days: int = 30) -> list[dict]:
    """Get historical cross-asset readings."""
    pass


@router.get("/sectors/{sector}/cross-asset-outlook")
async def get_sector_outlook(sector: str) -> dict:
    """Get cross-asset outlook for a specific sector."""
    return {
        "sector": "Technology",
        "outlook": "NEUTRAL",
        "supporting_signals": ["copper_gold_rising"],
        "opposing_signals": ["dollar_strength"],
        "net_score": 55
    }
```

---

## Integration with Signals

```python
def _apply_cross_asset_modifier(symbol: str, sector: str, base_confidence: float) -> float:
    """Adjust signal confidence based on cross-asset environment."""
    
    signals = get_active_cross_asset_signals()
    
    modifier = 1.0
    for signal in signals:
        if sector in signal.get("sectors_favored", []):
            modifier += 0.05
        if sector in signal.get("sectors_avoid", []):
            modifier -= 0.05
    
    return base_confidence * modifier
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Data freshness | < 1 hour lag |
| Signal accuracy | Sector rotation matches 60%+ |
| Coverage | All major asset classes |

---

*Created: 2026-08-16*

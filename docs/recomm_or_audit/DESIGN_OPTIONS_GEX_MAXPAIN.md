# IF-05: Options GEX/Max Pain

## Problem Statement

Missing dealer hedging flow impact:
- Gamma exposure (GEX) creates price magnets/repellents
- Max pain predicts expiry-week price targets
- Options positioning reveals smart money bets

---

## Solution Overview

Calculate gamma exposure and max pain levels to predict short-term price behavior around options expiry.

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Options Chain  │────▶│  GEX Calculator  │────▶│  options_gex    │
│  (yfinance)     │     │                  │     │  (new table)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Price magnet    │
                        │  predictions     │
                        └──────────────────┘
```

---

## Database Schema

```python
class OptionsGexSnapshot(Base):
    """Daily gamma exposure and max pain calculations."""
    __tablename__ = "options_gex_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    expiry_date: Mapped[date] = mapped_column(Date, index=True)  # Options expiry being analyzed
    
    # Current price context
    spot_price: Mapped[float] = mapped_column(Float)
    
    # Gamma Exposure
    net_gex: Mapped[float | None] = mapped_column(Float, nullable=True)  # Net gamma $ per 1% move
    call_gex: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_gex: Mapped[float | None] = mapped_column(Float, nullable=True)
    gex_flip_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # Price where GEX flips sign
    
    # Max Pain
    max_pain_price: Mapped[float] = mapped_column(Float)
    max_pain_distance_pct: Mapped[float] = mapped_column(Float)  # Distance from spot to max pain
    
    # Key levels
    highest_call_oi_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    highest_put_oi_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_wall: Mapped[float | None] = mapped_column(Float, nullable=True)  # Major resistance from calls
    put_wall: Mapped[float | None] = mapped_column(Float, nullable=True)   # Major support from puts
    
    # Positioning
    total_call_oi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_put_oi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    put_call_oi_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # GEX by strike (JSON for flexibility)
    gex_by_strike: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    # Prediction
    predicted_magnet_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    magnet_strength: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100
    
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_id", "as_of", "expiry_date", name="uq_options_gex_snapshot"),
    )
```

---

## Core Calculation Logic

```python
# services/market-data/src/services/gex_calculator.py

import numpy as np
from scipy.stats import norm

def calculate_gamma(S, K, T, r, sigma, option_type="call"):
    """
    Calculate option gamma using Black-Scholes.
    
    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry (years)
        r: Risk-free rate
        sigma: Implied volatility
    """
    if T <= 0:
        return 0
    
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    
    return gamma


def calculate_gex(options_chain: list[dict], spot_price: float) -> dict:
    """
    Calculate Gamma Exposure (GEX) from options chain.
    
    GEX = Gamma × Open Interest × 100 × Spot Price²
    
    Positive GEX = dealers are long gamma = price stabilizing
    Negative GEX = dealers are short gamma = price volatile
    """
    gex_by_strike = {}
    total_call_gex = 0
    total_put_gex = 0
    
    for opt in options_chain:
        strike = opt["strike"]
        oi = opt["openInterest"]
        gamma = opt.get("gamma") or calculate_gamma(
            spot_price, strike, opt["dte"] / 365, 0.05, opt["impliedVolatility"]
        )
        
        # GEX in dollars per 1% move
        contract_gex = gamma * oi * 100 * spot_price * spot_price * 0.01
        
        if opt["type"] == "call":
            # Dealers are typically short calls (sold to buyers)
            # Short call = short gamma
            gex = -contract_gex
            total_call_gex += gex
        else:
            # Dealers are typically long puts (bought from sellers)
            # Long put = long gamma
            gex = contract_gex
            total_put_gex += gex
        
        if strike not in gex_by_strike:
            gex_by_strike[strike] = 0
        gex_by_strike[strike] += gex
    
    net_gex = total_call_gex + total_put_gex
    
    # Find GEX flip price (where net GEX changes sign)
    gex_flip = _find_gex_flip(gex_by_strike, spot_price)
    
    return {
        "net_gex": net_gex,
        "call_gex": total_call_gex,
        "put_gex": total_put_gex,
        "gex_flip_price": gex_flip,
        "gex_by_strike": gex_by_strike,
    }


def calculate_max_pain(options_chain: list[dict]) -> float:
    """
    Calculate max pain price - the strike where option holders lose the most.
    
    Max pain theory: price gravitates toward strike where total option value
    is minimized (most options expire worthless).
    """
    strikes = sorted(set(opt["strike"] for opt in options_chain))
    
    min_pain = float("inf")
    max_pain_strike = strikes[len(strikes) // 2]
    
    for test_price in strikes:
        total_pain = 0
        
        for opt in options_chain:
            strike = opt["strike"]
            oi = opt["openInterest"]
            
            if opt["type"] == "call":
                # Call pain = max(0, test_price - strike) × OI
                intrinsic = max(0, test_price - strike)
            else:
                # Put pain = max(0, strike - test_price) × OI
                intrinsic = max(0, strike - test_price)
            
            total_pain += intrinsic * oi * 100
        
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = test_price
    
    return max_pain_strike


def find_key_levels(options_chain: list[dict], spot_price: float) -> dict:
    """Find significant support/resistance from options positioning."""
    
    # Group by strike
    call_oi_by_strike = {}
    put_oi_by_strike = {}
    
    for opt in options_chain:
        strike = opt["strike"]
        oi = opt["openInterest"]
        
        if opt["type"] == "call":
            call_oi_by_strike[strike] = call_oi_by_strike.get(strike, 0) + oi
        else:
            put_oi_by_strike[strike] = put_oi_by_strike.get(strike, 0) + oi
    
    # Find highest OI strikes
    highest_call_strike = max(call_oi_by_strike, key=call_oi_by_strike.get) if call_oi_by_strike else None
    highest_put_strike = max(put_oi_by_strike, key=put_oi_by_strike.get) if put_oi_by_strike else None
    
    # Call wall = highest call OI above spot (resistance)
    call_wall = None
    for strike in sorted(call_oi_by_strike.keys()):
        if strike > spot_price and call_oi_by_strike[strike] > 1000:
            call_wall = strike
            break
    
    # Put wall = highest put OI below spot (support)
    put_wall = None
    for strike in sorted(put_oi_by_strike.keys(), reverse=True):
        if strike < spot_price and put_oi_by_strike[strike] > 1000:
            put_wall = strike
            break
    
    return {
        "highest_call_oi_strike": highest_call_strike,
        "highest_put_oi_strike": highest_put_strike,
        "call_wall": call_wall,
        "put_wall": put_wall,
    }


def predict_price_magnet(
    spot_price: float,
    max_pain: float,
    gex_flip: float,
    call_wall: float,
    put_wall: float,
    days_to_expiry: int
) -> dict:
    """Predict where price is likely to gravitate."""
    
    # Weight factors based on days to expiry
    # Max pain effect strongest in final week
    max_pain_weight = 0.5 if days_to_expiry <= 5 else 0.2
    gex_weight = 0.3
    walls_weight = 0.2
    
    # Calculate weighted magnet
    magnets = []
    
    if max_pain:
        magnets.append((max_pain, max_pain_weight))
    if gex_flip:
        magnets.append((gex_flip, gex_weight))
    
    # Walls act as boundaries
    if call_wall and spot_price < call_wall:
        magnets.append((call_wall * 0.98, walls_weight / 2))  # Slight pull toward resistance
    if put_wall and spot_price > put_wall:
        magnets.append((put_wall * 1.02, walls_weight / 2))  # Slight pull toward support
    
    if not magnets:
        return {"predicted_magnet_price": spot_price, "magnet_strength": 0}
    
    total_weight = sum(w for _, w in magnets)
    weighted_price = sum(p * w for p, w in magnets) / total_weight
    
    # Strength based on convergence of signals
    price_spread = max(p for p, _ in magnets) - min(p for p, _ in magnets)
    convergence = 1 - (price_spread / spot_price)
    strength = convergence * 100 * (1 if days_to_expiry <= 5 else 0.5)
    
    return {
        "predicted_magnet_price": round(weighted_price, 2),
        "magnet_strength": round(min(100, max(0, strength)), 1),
    }
```

---

## API Endpoints

```python
@router.get("/{symbol}/gex")
async def get_gex_analysis(symbol: str) -> dict:
    """Get gamma exposure analysis for a symbol."""
    return {
        "symbol": "AAPL",
        "spot_price": 185.50,
        "expiry_date": "2026-08-21",
        "days_to_expiry": 5,
        "net_gex": 2500000000,  # $2.5B positive = stabilizing
        "gex_interpretation": "Positive GEX - dealers will buy dips, sell rips (stabilizing)",
        "max_pain": 182.50,
        "max_pain_distance_pct": -1.6,
        "call_wall": 190.00,
        "put_wall": 180.00,
        "predicted_magnet": 183.00,
        "magnet_strength": 72,
        "trading_implication": "Price likely to drift toward $183 by Friday expiry"
    }


@router.get("/{symbol}/options-levels")
async def get_options_levels(symbol: str) -> dict:
    """Get key options-derived support/resistance levels."""
    return {
        "symbol": "AAPL",
        "levels": [
            {"price": 190.00, "type": "call_wall", "strength": "strong"},
            {"price": 185.00, "type": "gex_flip", "strength": "medium"},
            {"price": 182.50, "type": "max_pain", "strength": "strong"},
            {"price": 180.00, "type": "put_wall", "strength": "strong"},
        ]
    }
```

---

## Scheduler Job

```python
def compute_daily_gex() -> None:
    """Daily job to compute GEX for watchlist stocks."""
    with _get_session() as session:
        # Get symbols with active options
        symbols = _get_optionable_watchlist_symbols(session)
        
        for symbol in symbols:
            try:
                # Fetch options chain
                ticker = yf.Ticker(symbol)
                spot = ticker.fast_info.get("lastPrice")
                
                # Get nearest monthly expiry
                expiries = ticker.options
                if not expiries:
                    continue
                
                nearest_expiry = expiries[0]
                chain = ticker.option_chain(nearest_expiry)
                
                # Combine calls and puts
                options_data = []
                for _, row in chain.calls.iterrows():
                    options_data.append({
                        "strike": row["strike"],
                        "type": "call",
                        "openInterest": row["openInterest"],
                        "impliedVolatility": row["impliedVolatility"],
                        "gamma": row.get("gamma"),
                        "dte": (datetime.strptime(nearest_expiry, "%Y-%m-%d").date() - date.today()).days,
                    })
                for _, row in chain.puts.iterrows():
                    options_data.append({
                        "strike": row["strike"],
                        "type": "put",
                        "openInterest": row["openInterest"],
                        "impliedVolatility": row["impliedVolatility"],
                        "gamma": row.get("gamma"),
                        "dte": (datetime.strptime(nearest_expiry, "%Y-%m-%d").date() - date.today()).days,
                    })
                
                # Calculate GEX
                gex_result = calculate_gex(options_data, spot)
                max_pain = calculate_max_pain(options_data)
                levels = find_key_levels(options_data, spot)
                
                dte = (datetime.strptime(nearest_expiry, "%Y-%m-%d").date() - date.today()).days
                magnet = predict_price_magnet(
                    spot, max_pain, gex_result["gex_flip_price"],
                    levels["call_wall"], levels["put_wall"], dte
                )
                
                # Save snapshot
                snapshot = OptionsGexSnapshot(
                    stock_id=_get_stock_id(session, symbol),
                    symbol=symbol,
                    as_of=date.today(),
                    expiry_date=datetime.strptime(nearest_expiry, "%Y-%m-%d").date(),
                    spot_price=spot,
                    net_gex=gex_result["net_gex"],
                    call_gex=gex_result["call_gex"],
                    put_gex=gex_result["put_gex"],
                    gex_flip_price=gex_result["gex_flip_price"],
                    max_pain_price=max_pain,
                    max_pain_distance_pct=(max_pain - spot) / spot * 100,
                    **levels,
                    **magnet,
                    gex_by_strike=gex_result["gex_by_strike"],
                )
                session.merge(snapshot)
                
            except Exception as e:
                log.warning(f"GEX calculation failed for {symbol}: {e}")
                continue
        
        session.commit()

# Register: runs daily after market open
scheduler.add_job(compute_daily_gex, "cron", hour=10, minute=30)
```

---

## Integration with Signals

```python
def _apply_gex_modifier(symbol: str, signal_direction: str, base_confidence: float) -> float:
    """Adjust confidence based on GEX environment."""
    
    gex = get_latest_gex(symbol)
    if not gex:
        return base_confidence
    
    modifier = 1.0
    
    # Positive GEX = mean reversion environment
    if gex.net_gex > 0:
        # BUY signals near put wall are stronger
        if signal_direction == "BUY" and gex.put_wall:
            distance_to_support = (gex.spot_price - gex.put_wall) / gex.spot_price
            if distance_to_support < 0.03:  # Within 3% of put wall
                modifier += 0.10
        
        # SELL signals near call wall are stronger
        if signal_direction == "SELL" and gex.call_wall:
            distance_to_resistance = (gex.call_wall - gex.spot_price) / gex.spot_price
            if distance_to_resistance < 0.03:
                modifier += 0.10
    
    # Negative GEX = trending environment
    elif gex.net_gex < 0:
        # Momentum signals are stronger
        modifier += 0.05
    
    return base_confidence * modifier
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Max pain accuracy | Price within 3% of max pain on expiry > 50% |
| GEX prediction | Correct volatility regime 65%+ |
| Level accuracy | Price respects walls 60%+ |

---

*Created: 2026-08-16*

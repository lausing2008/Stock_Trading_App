# IF-02: Alpha Decay Tracking

## Problem Statement

Currently unknown:
- How quickly do signals lose predictive power after generation?
- Should we act immediately or wait for confirmation?
- Are we leaving money on the table by acting too slow?

---

## Solution Overview

Track signal performance by age (hours/days since generation) to measure alpha decay and optimize execution timing.

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Signal Engine  │────▶│  Decay Tracker   │────▶│  alpha_decay    │
│  (generates)    │     │  (measures)      │     │  (new table)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Optimal timing  │
                        │  recommendations │
                        └──────────────────┘
```

---

## Database Schema

```python
# shared/db/models.py

class AlphaDecayMeasurement(Base):
    """Measures signal performance at different ages."""
    __tablename__ = "alpha_decay_measurements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    
    # Signal identification
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    horizon: Mapped[str] = mapped_column(String(16), index=True)  # SHORT/SWING/LONG
    signal_direction: Mapped[str] = mapped_column(String(8))  # BUY/SELL
    signal_confidence: Mapped[float] = mapped_column(Float)
    
    # Timing
    signal_generated_at: Mapped[datetime] = mapped_column(DateTime)
    
    # Returns at different ages (hours after signal)
    return_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_4h: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Peak return and timing
    max_favorable_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    hours_to_peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Did acting at each age result in profit?
    profitable_1h: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    profitable_1d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    profitable_2d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("signal_id", name="uq_alpha_decay_signal"),
        Index("ix_alpha_decay_horizon_direction", "horizon", "signal_direction"),
    )


class AlphaDecaySummary(Base):
    """Aggregated decay statistics by signal type."""
    __tablename__ = "alpha_decay_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # Grouping
    horizon: Mapped[str] = mapped_column(String(16), index=True)
    signal_direction: Mapped[str] = mapped_column(String(8))
    market: Mapped[str] = mapped_column(String(8))
    confidence_band: Mapped[str] = mapped_column(String(16))  # "50-64", "65-79", "80+"
    
    # Sample info
    sample_count: Mapped[int] = mapped_column(Integer)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    
    # Decay curve (avg return at each age)
    avg_return_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_return_4h: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_return_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_return_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_return_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Optimal timing
    optimal_entry_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    half_life_hours: Mapped[float | None] = mapped_column(Float, nullable=True)  # Time for alpha to decay 50%
    
    # Win rates by timing
    win_rate_immediate: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate_1d_delay: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate_2d_delay: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("horizon", "signal_direction", "market", "confidence_band", 
                        name="uq_alpha_decay_summary"),
    )
```

---

## Core Calculation Logic

```python
# services/signal-engine/src/services/alpha_decay.py

import numpy as np
from scipy.optimize import curve_fit

def measure_signal_decay(
    signal_id: int,
    signal_time: datetime,
    prices: list[tuple[datetime, float]]  # [(timestamp, price), ...]
) -> dict:
    """
    Measure returns at different time intervals after signal.
    
    Args:
        signal_id: The signal being measured
        signal_time: When signal was generated
        prices: Price history after signal
    """
    if not prices:
        return {}
    
    entry_price = prices[0][1]  # Price at signal time
    
    results = {
        "signal_id": signal_id,
        "entry_price": entry_price,
    }
    
    # Define measurement windows
    windows = {
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
        "2d": timedelta(days=2),
        "3d": timedelta(days=3),
        "5d": timedelta(days=5),
        "10d": timedelta(days=10),
    }
    
    for label, delta in windows.items():
        target_time = signal_time + delta
        # Find closest price to target time
        price_at_window = _find_price_at_time(prices, target_time)
        if price_at_window:
            ret = (price_at_window - entry_price) / entry_price
            results[f"return_{label}"] = ret
    
    # Find peak favorable return
    max_return = 0
    hours_to_peak = 0
    for ts, price in prices:
        ret = (price - entry_price) / entry_price
        if ret > max_return:
            max_return = ret
            hours_to_peak = (ts - signal_time).total_seconds() / 3600
    
    results["max_favorable_return"] = max_return
    results["hours_to_peak"] = hours_to_peak
    
    return results


def calculate_half_life(decay_curve: list[float], time_points: list[float]) -> float:
    """
    Fit exponential decay and calculate half-life.
    
    Alpha decays as: alpha(t) = alpha_0 * exp(-lambda * t)
    Half-life = ln(2) / lambda
    """
    if len(decay_curve) < 3:
        return None
    
    # Normalize to start at 1.0
    alpha_0 = decay_curve[0] if decay_curve[0] != 0 else 0.001
    normalized = [a / alpha_0 for a in decay_curve]
    
    # Fit exponential decay
    def exp_decay(t, lam):
        return np.exp(-lam * t)
    
    try:
        popt, _ = curve_fit(exp_decay, time_points, normalized, p0=[0.1])
        lam = popt[0]
        half_life = np.log(2) / lam if lam > 0 else float('inf')
        return half_life
    except:
        return None


def compute_optimal_entry_timing(
    horizon: str,
    direction: str,
    market: str
) -> dict:
    """
    Analyze historical decay data to find optimal entry timing.
    """
    with get_session() as session:
        # Get all decay measurements for this signal type
        measurements = session.query(AlphaDecayMeasurement).filter(
            AlphaDecayMeasurement.horizon == horizon,
            AlphaDecayMeasurement.signal_direction == direction,
        ).all()
        
        if len(measurements) < 30:
            return {"status": "insufficient_data", "count": len(measurements)}
        
        # Build decay curve
        time_points = [1, 4, 24, 48, 72, 120, 240]  # hours
        avg_returns = []
        
        for hours in time_points:
            field = _hours_to_field(hours)
            returns = [getattr(m, field) for m in measurements if getattr(m, field) is not None]
            avg_returns.append(np.mean(returns) if returns else 0)
        
        # Find optimal entry (highest avg return)
        optimal_idx = np.argmax(avg_returns)
        optimal_hours = time_points[optimal_idx]
        
        # Calculate half-life
        half_life = calculate_half_life(avg_returns, time_points)
        
        # Win rates at different delays
        win_rate_immediate = np.mean([m.profitable_1h for m in measurements if m.profitable_1h is not None])
        win_rate_1d = np.mean([m.profitable_1d for m in measurements if m.profitable_1d is not None])
        
        return {
            "horizon": horizon,
            "direction": direction,
            "sample_count": len(measurements),
            "optimal_entry_hours": optimal_hours,
            "half_life_hours": half_life,
            "decay_curve": dict(zip(time_points, avg_returns)),
            "win_rate_immediate": win_rate_immediate,
            "win_rate_1d_delay": win_rate_1d,
            "recommendation": _generate_timing_recommendation(optimal_hours, half_life),
        }


def _generate_timing_recommendation(optimal_hours: float, half_life: float) -> str:
    """Generate human-readable timing recommendation."""
    if half_life and half_life < 4:
        return "URGENT: Signal decays rapidly. Act within 4 hours."
    elif half_life and half_life < 24:
        return "Act same day. Signal loses half its edge within 24 hours."
    elif optimal_hours <= 4:
        return "Best results from immediate action."
    elif optimal_hours <= 24:
        return "Slight benefit from waiting for confirmation (1 day)."
    else:
        return "Signal is slow-moving. Can wait for better entry."
```

---

## API Endpoints

```python
# services/signal-engine/src/api/routes.py

@router.get("/alpha-decay/{horizon}/{direction}")
async def get_alpha_decay_analysis(horizon: str, direction: str) -> dict:
    """Get alpha decay analysis for a signal type."""
    return {
        "horizon": "SWING",
        "direction": "BUY",
        "sample_count": 1247,
        "half_life_hours": 18.5,
        "optimal_entry_hours": 4,
        "decay_curve": {
            "1h": 0.0082,
            "4h": 0.0095,  # Peak
            "24h": 0.0071,
            "48h": 0.0052,
            "72h": 0.0038,
        },
        "win_rates": {
            "immediate": 0.58,
            "1d_delay": 0.54,
            "2d_delay": 0.49,
        },
        "recommendation": "Act within 4 hours for best results. Signal half-life is ~18 hours."
    }


@router.get("/signals/{symbol}/timing")
async def get_signal_timing_advice(symbol: str) -> dict:
    """Get timing advice for current signal on a symbol."""
    # Fetch current signal
    signal = _get_latest_signal(symbol)
    if not signal:
        return {"error": "No active signal"}
    
    # Get decay profile for this signal type
    decay = compute_optimal_entry_timing(signal.horizon, signal.signal, signal.market)
    
    signal_age_hours = (datetime.utcnow() - signal.ts).total_seconds() / 3600
    remaining_alpha_pct = _estimate_remaining_alpha(signal_age_hours, decay["half_life_hours"])
    
    return {
        "symbol": symbol,
        "signal": signal.signal,
        "signal_age_hours": signal_age_hours,
        "half_life_hours": decay["half_life_hours"],
        "remaining_alpha_pct": remaining_alpha_pct,
        "urgency": "HIGH" if remaining_alpha_pct > 70 else "MEDIUM" if remaining_alpha_pct > 40 else "LOW",
        "recommendation": f"Signal is {signal_age_hours:.1f}h old with ~{remaining_alpha_pct:.0f}% alpha remaining."
    }
```

---

## Scheduler Job

```python
# services/market-data/src/services/scheduler.py

def measure_alpha_decay() -> None:
    """Daily job to measure decay for signals from 10+ days ago."""
    with _get_session() as session:
        # Get signals from 10-15 days ago (enough time for full decay measurement)
        cutoff_start = date.today() - timedelta(days=15)
        cutoff_end = date.today() - timedelta(days=10)
        
        signals = session.query(Signal).filter(
            Signal.ts >= cutoff_start,
            Signal.ts <= cutoff_end,
            Signal.signal.in_(["BUY", "SELL"]),
        ).all()
        
        for signal in signals:
            # Skip if already measured
            existing = session.query(AlphaDecayMeasurement).filter(
                AlphaDecayMeasurement.signal_id == signal.id
            ).first()
            if existing:
                continue
            
            # Fetch price history after signal
            prices = _fetch_prices_after(signal.stock_id, signal.ts, days=10)
            
            # Measure decay
            decay_data = measure_signal_decay(signal.id, signal.ts, prices)
            
            # Save measurement
            measurement = AlphaDecayMeasurement(
                signal_id=signal.id,
                symbol=signal.stock.symbol,
                horizon=signal.horizon.value,
                signal_direction=signal.signal.value,
                signal_confidence=signal.confidence,
                signal_generated_at=signal.ts,
                **decay_data
            )
            session.add(measurement)
        
        session.commit()
        
        # Recompute summaries weekly
        if date.today().weekday() == 0:  # Monday
            _recompute_decay_summaries(session)

# Register: runs daily
scheduler.add_job(measure_alpha_decay, "cron", hour=6, minute=0)
```

---

## Frontend Integration

```typescript
// frontend/src/components/AlphaDecayChart.tsx

// Display:
// 1. Decay curve chart (return vs time since signal)
// 2. Half-life indicator
// 3. "Act Now" urgency badge on signal cards
// 4. Timing recommendation text

interface DecayCurve {
  timePoints: number[];  // hours
  avgReturns: number[];
  halfLifeHours: number;
  optimalEntryHours: number;
}
```

---

## Integration with Paper Trading

```python
# Modify paper_trading_engine.py to use decay data

def _should_enter(signal, portfolio_config) -> tuple[bool, str]:
    """Enhanced entry decision using alpha decay."""
    
    # Get decay profile
    decay = get_decay_profile(signal.horizon, signal.signal)
    
    signal_age_hours = (datetime.utcnow() - signal.ts).total_seconds() / 3600
    remaining_alpha = estimate_remaining_alpha(signal_age_hours, decay["half_life_hours"])
    
    # Reject stale signals
    if remaining_alpha < 0.30:  # Less than 30% alpha remaining
        return False, f"Signal too old ({signal_age_hours:.0f}h), only {remaining_alpha*100:.0f}% alpha remaining"
    
    # Reduce position size for aging signals
    if remaining_alpha < 0.60:
        # Scale down position size proportionally
        size_multiplier = remaining_alpha / 0.60
        # Apply to position sizing...
    
    return True, "Signal fresh enough to act"
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Decay measurement coverage | > 90% of signals |
| Half-life accuracy | Within 20% of actual |
| Timing improvement | +2-3% win rate from optimal timing |

---

*Created: 2026-08-16*

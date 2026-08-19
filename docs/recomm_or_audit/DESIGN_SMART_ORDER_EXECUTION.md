# IF-06: Smart Order Execution

## Problem Statement

Large orders cause market impact:
- Slippage on market orders
- Information leakage on limit orders
- No execution quality measurement

---

## Solution Overview

Implement TWAP/VWAP algorithms and execution quality tracking.

---

## Database Schema

```python
class ExecutionOrder(Base):
    """Smart order with execution algorithm."""
    __tablename__ = "execution_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    
    # Order details
    side: Mapped[str] = mapped_column(String(8))  # BUY/SELL
    total_shares: Mapped[float] = mapped_column(Float)
    algorithm: Mapped[str] = mapped_column(String(16))  # TWAP, VWAP, ICEBERG, MARKET
    
    # Algorithm parameters
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # For TWAP
    participation_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # For VWAP
    slice_size: Mapped[float | None] = mapped_column(Float, nullable=True)  # For ICEBERG
    
    # Execution tracking
    filled_shares: Mapped[float] = mapped_column(Float, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_price: Mapped[float] = mapped_column(Float)  # Price when order was decided
    arrival_price: Mapped[float] = mapped_column(Float)  # Price when execution started
    
    # Quality metrics
    slippage_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    implementation_shortfall: Mapped[float | None] = mapped_column(Float, nullable=True)
    vwap_benchmark: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/active/filled/cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExecutionSlice(Base):
    """Individual slice of a smart order."""
    __tablename__ = "execution_slices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("execution_orders.id"), index=True)
    
    slice_number: Mapped[int] = mapped_column(Integer)
    target_shares: Mapped[float] = mapped_column(Float)
    filled_shares: Mapped[float] = mapped_column(Float, default=0)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    scheduled_at: Mapped[datetime] = mapped_column(DateTime)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

---

## Execution Algorithms

```python
# services/market-data/src/services/execution_algos.py

class TWAPExecutor:
    """Time-Weighted Average Price - split order evenly over time."""
    
    def __init__(self, total_shares: float, duration_minutes: int, interval_minutes: int = 5):
        self.total_shares = total_shares
        self.duration_minutes = duration_minutes
        self.interval_minutes = interval_minutes
        self.num_slices = duration_minutes // interval_minutes
        self.slice_size = total_shares / self.num_slices
    
    def generate_schedule(self, start_time: datetime) -> list[dict]:
        """Generate execution schedule."""
        schedule = []
        for i in range(self.num_slices):
            schedule.append({
                "slice_number": i + 1,
                "target_shares": self.slice_size,
                "scheduled_at": start_time + timedelta(minutes=i * self.interval_minutes),
            })
        return schedule


class VWAPExecutor:
    """Volume-Weighted Average Price - match historical volume profile."""
    
    def __init__(self, total_shares: float, participation_rate: float = 0.10):
        self.total_shares = total_shares
        self.participation_rate = participation_rate  # Max % of volume to take
    
    def generate_schedule(self, symbol: str, start_time: datetime, duration_minutes: int) -> list[dict]:
        """Generate schedule based on historical volume profile."""
        # Fetch historical intraday volume profile
        volume_profile = self._get_volume_profile(symbol)
        
        schedule = []
        remaining = self.total_shares
        
        for bucket in volume_profile:
            if remaining <= 0:
                break
            
            # Target shares = participation_rate × expected volume
            target = min(remaining, bucket["expected_volume"] * self.participation_rate)
            
            schedule.append({
                "slice_number": len(schedule) + 1,
                "target_shares": target,
                "scheduled_at": bucket["time"],
            })
            remaining -= target
        
        return schedule


class IcebergExecutor:
    """Iceberg order - show only small portion of total size."""
    
    def __init__(self, total_shares: float, visible_size: float):
        self.total_shares = total_shares
        self.visible_size = visible_size
        self.num_slices = int(np.ceil(total_shares / visible_size))
    
    def get_next_slice(self, filled_so_far: float) -> float:
        """Get next visible slice size."""
        remaining = self.total_shares - filled_so_far
        return min(self.visible_size, remaining)
```

---

## Execution Quality Metrics

```python
def calculate_execution_quality(order: ExecutionOrder, market_vwap: float) -> dict:
    """Calculate execution quality metrics."""
    
    # Slippage vs arrival price (in basis points)
    slippage_bps = (order.avg_fill_price - order.arrival_price) / order.arrival_price * 10000
    if order.side == "SELL":
        slippage_bps = -slippage_bps  # Negative slippage is bad for sells
    
    # Implementation shortfall vs decision price
    impl_shortfall = (order.avg_fill_price - order.decision_price) / order.decision_price * 10000
    if order.side == "SELL":
        impl_shortfall = -impl_shortfall
    
    # VWAP comparison
    vwap_diff_bps = (order.avg_fill_price - market_vwap) / market_vwap * 10000
    if order.side == "SELL":
        vwap_diff_bps = -vwap_diff_bps
    
    return {
        "slippage_bps": round(slippage_bps, 2),
        "implementation_shortfall_bps": round(impl_shortfall, 2),
        "vwap_diff_bps": round(vwap_diff_bps, 2),
        "quality_score": _compute_quality_score(slippage_bps, impl_shortfall, vwap_diff_bps),
    }


def _compute_quality_score(slippage: float, impl_short: float, vwap_diff: float) -> int:
    """0-100 score where 100 = perfect execution."""
    # Penalize negative metrics
    penalty = abs(min(0, slippage)) + abs(min(0, impl_short)) + abs(min(0, vwap_diff))
    score = max(0, 100 - penalty / 2)
    return int(score)
```

---

## API Endpoints

```python
@router.post("/orders/smart")
async def create_smart_order(body: SmartOrderRequest) -> dict:
    """Create a smart order with execution algorithm."""
    # body: {symbol, side, shares, algorithm, duration_minutes, ...}
    pass

@router.get("/orders/{order_id}/quality")
async def get_execution_quality(order_id: int) -> dict:
    """Get execution quality metrics for an order."""
    return {
        "order_id": 123,
        "slippage_bps": -3.2,
        "implementation_shortfall_bps": -5.1,
        "vwap_diff_bps": 1.8,
        "quality_score": 92,
        "interpretation": "Good execution - beat VWAP by 1.8 bps"
    }
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Avg slippage | < 5 bps |
| VWAP beat rate | > 50% |
| Quality score | > 85 avg |

---

*Created: 2026-08-16*

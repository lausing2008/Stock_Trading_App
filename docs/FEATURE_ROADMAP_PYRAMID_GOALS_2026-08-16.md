# Feature Roadmap: Pyramid Trading, Goals & Advanced Trading Tools

**Created:** 2026-08-16  
**Status:** Planning Document

---

## 1. Pyramid Trading System

### 1.1 What is Pyramid Trading?
Pyramid trading is a position-building strategy where you add to winning positions as the trade moves in your favor, rather than entering the full position at once.

### 1.2 Database Schema Changes

```sql
-- Add to shared/db/models.py

class PyramidLevel(Base):
    """Track each pyramid entry for a position."""
    __tablename__ = "pyramid_levels"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("user_positions.id", ondelete="CASCADE"))
    level: Mapped[int] = mapped_column(Integer)  # 1=initial, 2=first add, 3=second add
    shares: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date)
    trigger_type: Mapped[str] = mapped_column(String(32))  # breakout|pullback|target_hit
    stop_for_level: Mapped[float] = mapped_column(Float)  # trailing stop for this tranche
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

class PyramidPlan(Base):
    """Pre-defined pyramid plan for a stock."""
    __tablename__ = "pyramid_plans"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32))
    max_levels: Mapped[int] = mapped_column(Integer, default=3)
    initial_size_pct: Mapped[float] = mapped_column(Float, default=0.50)  # 50% of planned position
    level_2_trigger_pct: Mapped[float] = mapped_column(Float, default=3.0)  # +3% to add
    level_3_trigger_pct: Mapped[float] = mapped_column(Float, default=6.0)  # +6% to add
    level_2_size_pct: Mapped[float] = mapped_column(Float, default=0.30)
    level_3_size_pct: Mapped[float] = mapped_column(Float, default=0.20)
    trail_stop_pct: Mapped[float] = mapped_column(Float, default=5.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

### 1.3 Pyramid Trading UI (Stock Detail Page)

Add a "Pyramid Plan" tab showing:
- Current pyramid status (Level 1/2/3)
- Next add trigger price
- Size allocation per level
- Trailing stop per tranche
- Visual pyramid diagram

### 1.4 Pyramid Alert Integration

```python
# In scheduler.py - add check_pyramid_triggers()
async def check_pyramid_triggers():
    """Check if any pyramid level triggers are hit."""
    plans = session.query(PyramidPlan).filter(PyramidPlan.is_active == True).all()
    for plan in plans:
        price = get_live_price(plan.symbol)
        position = get_position(plan.user_id, plan.symbol)
        current_level = get_current_pyramid_level(position)
        
        if current_level < plan.max_levels:
            trigger_price = calculate_next_trigger(plan, position)
            if price >= trigger_price:
                send_pyramid_alert(plan, current_level + 1, price)
```

---

## 2. Long-Term & Short-Term Goals per Stock

### 2.1 Database Schema

```sql
class StockGoal(Base):
    """User-defined goals for a stock position."""
    __tablename__ = "stock_goals"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32))
    goal_type: Mapped[str] = mapped_column(String(16))  # short_term|long_term
    
    # Target metrics
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Timeline
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    horizon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Progress tracking
    start_price: Mapped[float] = mapped_column(Float)
    start_date: Mapped[date] = mapped_column(Date)
    current_progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Status
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|achieved|failed|cancelled
    achieved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

### 2.2 Goal Types

| Goal Type | Horizon | Example |
|-----------|---------|---------|
| **Short-Term** | 1-30 days | "Capture 5% swing on NVDA pullback" |
| **Long-Term** | 3-12+ months | "Build 100 shares of AAPL for dividend income" |
| **Accumulation** | Ongoing | "DCA $500/month into MSFT" |
| **Income** | Recurring | "Generate $200/month dividend from portfolio" |

### 2.3 Goals UI (Stock Detail + Positions Page)

```
┌─────────────────────────────────────────────────┐
│ 📎 NVDA Goals                                   │
├─────────────────────────────────────────────────┤
│ SHORT-TERM (30d)           LONG-TERM (12mo)     │
│ ┌─────────────────┐       ┌─────────────────┐   │
│ │ Target: $145    │       │ Target: 50 shares│  │
│ │ Current: $138   │       │ Current: 25      │  │
│ │ Progress: 72%   │       │ Progress: 50%    │  │
│ │ ████████░░ 72%  │       │ █████░░░░░ 50%   │  │
│ │ Days left: 12   │       │ Months left: 6   │  │
│ └─────────────────┘       └─────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## 3. Additional Features & Recommendations

### 3.1 Risk Management Tools

#### A. Portfolio Heat Map
- Visual grid showing correlation between positions
- Identify concentration risk
- Sector/market exposure warnings

#### B. Max Drawdown Alerts
```python
class DrawdownAlert(Base):
    __tablename__ = "drawdown_alerts"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    alert_type: Mapped[str] = mapped_column(String(32))  # position|portfolio
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)  # e.g., 10%
    current_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    peak_value: Mapped[float] = mapped_column(Float)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
```

#### C. Position Sizing Calculator Enhancement
- Add Kelly Criterion calculator
- Risk-of-ruin calculator
- Optimal F calculator

### 3.2 Entry Timing Tools

#### A. Entry Score System
```python
def calculate_entry_score(symbol: str) -> dict:
    """Score 0-100 for entry timing quality."""
    return {
        "overall_score": 75,
        "components": {
            "trend_alignment": 80,      # Price above key MAs
            "pullback_quality": 70,     # RSI oversold, near support
            "volume_confirmation": 75,  # Volume pattern supportive
            "sector_momentum": 80,      # Sector rotating in
            "market_regime": 70,        # Bull/neutral regime
        },
        "recommendation": "GOOD_ENTRY",  # EXCELLENT|GOOD|FAIR|WAIT
        "wait_for": ["RSI < 40", "Test of $135 support"]
    }
```

#### B. Entry Zone Visualization
- Show optimal entry zones on chart
- Highlight support confluence areas
- Mark FVG (Fair Value Gap) zones

### 3.3 Exit Strategy Tools

#### A. Trailing Stop Manager
```python
class TrailingStopConfig(Base):
    __tablename__ = "trailing_stop_configs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("user_positions.id"))
    stop_type: Mapped[str] = mapped_column(String(32))  # percentage|atr|chandelier|parabolic
    
    # Percentage-based
    trail_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # ATR-based
    atr_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Chandelier
    chandelier_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Current values
    current_stop: Mapped[float] = mapped_column(Float)
    highest_price: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

#### B. Partial Exit Planner
- Scale-out at target levels (25% at T1, 50% at T2, 25% runner)
- Automatic profit-taking alerts
- Track realized vs unrealized P&L per tranche

### 3.4 Market Intelligence

#### A. Earnings Calendar Integration
```python
class EarningsPlaybook(Base):
    """Pre-earnings and post-earnings strategy."""
    __tablename__ = "earnings_playbooks"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    earnings_date: Mapped[date] = mapped_column(Date)
    
    # Pre-earnings strategy
    pre_earnings_action: Mapped[str] = mapped_column(String(32))  # hold|reduce|exit|add
    iv_rank_at_setup: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Expected move
    expected_move_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_avg_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    
    # Post-earnings plan
    beat_action: Mapped[str] = mapped_column(String(256))  # JSON: what to do if beats
    miss_action: Mapped[str] = mapped_column(String(256))  # JSON: what to do if misses
```

#### B. Sector Rotation Tracker
- Weekly sector strength rankings
- Rotation signals (money flowing into/out of sectors)
- Sector-relative strength for each position

### 3.5 AI-Powered Features

#### A. Trade Journal AI Analysis
```python
async def analyze_trade_journal(user_id: int) -> dict:
    """AI analysis of trading patterns and mistakes."""
    trades = get_closed_trades(user_id, days=90)
    
    return {
        "win_rate": 0.45,
        "avg_winner": 8.5,
        "avg_loser": -4.2,
        "profit_factor": 1.8,
        "patterns_identified": [
            "Tend to exit winners too early (avg hold 3d vs optimal 7d)",
            "Best performance in Technology sector",
            "Losses concentrated in first hour of trading",
        ],
        "recommendations": [
            "Consider using trailing stops instead of fixed targets",
            "Avoid trading in first 30 minutes",
            "Increase position size in Technology trades",
        ]
    }
```

#### B. AI Trade Coach
- Real-time feedback on trade decisions
- "Are you sure?" prompts for risky trades
- Post-trade analysis and lessons

### 3.6 Automation Features

#### A. Auto-DCA (Dollar Cost Averaging)
```python
class DCASchedule(Base):
    __tablename__ = "dca_schedules"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    amount_per_period: Mapped[float] = mapped_column(Float)  # $500
    frequency: Mapped[str] = mapped_column(String(16))  # daily|weekly|biweekly|monthly
    next_execution: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Smart DCA options
    skip_if_rsi_above: Mapped[float | None] = mapped_column(Float, nullable=True)  # Skip if RSI > 70
    increase_if_rsi_below: Mapped[float | None] = mapped_column(Float, nullable=True)  # 2x if RSI < 30
```

#### B. Conditional Order Chains
- "If NVDA breaks $140, buy 50 shares with stop at $135"
- "If my AAPL position is up 10%, sell 25% and move stop to breakeven"

### 3.7 Performance Analytics

#### A. Attribution Analysis
```python
def calculate_attribution(user_id: int, period: str) -> dict:
    """Break down P&L by source."""
    return {
        "total_return": 12.5,
        "attribution": {
            "stock_selection": 8.2,      # Alpha from picking stocks
            "market_timing": 2.1,        # Entry/exit timing
            "position_sizing": 1.5,      # Size decisions
            "sector_allocation": 0.7,    # Sector bets
        },
        "benchmark_return": 7.2,  # S&P 500
        "alpha": 5.3,
    }
```

#### B. Risk-Adjusted Metrics Dashboard
- Sharpe Ratio (rolling 30/90/365 day)
- Sortino Ratio
- Calmar Ratio
- Max Drawdown history

---

## 4. Implementation Priority

### Phase 1 (P0) - Quick Wins (1-2 weeks)
| Feature | Effort | Impact |
|---------|--------|--------|
| Stock Goals (basic) | 3 days | High |
| Entry Score Display | 2 days | High |
| Trailing Stop Manager | 3 days | High |
| Drawdown Alerts | 2 days | Medium |

### Phase 2 (P1) - Core Features (3-4 weeks)
| Feature | Effort | Impact |
|---------|--------|--------|
| Pyramid Trading System | 1 week | High |
| Partial Exit Planner | 4 days | High |
| Earnings Playbook | 4 days | Medium |
| Trade Journal AI | 1 week | High |

### Phase 3 (P2) - Advanced (4-6 weeks)
| Feature | Effort | Impact |
|---------|--------|--------|
| Auto-DCA | 1 week | Medium |
| Conditional Order Chains | 2 weeks | High |
| Attribution Analysis | 1 week | Medium |
| AI Trade Coach | 2 weeks | High |

---

## 5. API Endpoints to Add

```python
# Pyramid Trading
POST   /pyramid/plans                    # Create pyramid plan
GET    /pyramid/plans/{symbol}           # Get plan for symbol
PUT    /pyramid/plans/{id}               # Update plan
POST   /pyramid/levels/{position_id}     # Record pyramid entry
GET    /pyramid/status/{symbol}          # Current pyramid status

# Goals
POST   /goals                            # Create goal
GET    /goals                            # List all goals
GET    /goals/{symbol}                   # Goals for symbol
PUT    /goals/{id}                       # Update goal
DELETE /goals/{id}                       # Delete goal
GET    /goals/progress                   # All goals with progress

# Trailing Stops
POST   /trailing-stops                   # Create trailing stop
GET    /trailing-stops/{position_id}     # Get config
PUT    /trailing-stops/{id}              # Update
DELETE /trailing-stops/{id}              # Remove

# Analytics
GET    /analytics/attribution            # P&L attribution
GET    /analytics/journal-analysis       # AI trade journal analysis
GET    /analytics/entry-score/{symbol}   # Entry timing score
```

---

## 6. Frontend Pages to Add/Modify

| Page | Changes |
|------|---------|
| `/stock/[symbol]` | Add Goals tab, Pyramid tab, Entry Score card |
| `/positions` | Add Goals progress column, Pyramid status badges |
| `/goals` | New page: Goal dashboard with progress tracking |
| `/analytics` | New page: Performance attribution, journal analysis |
| `/automation` | New page: DCA schedules, conditional orders |

---

## 7. Expected Outcomes

| Metric | Current | Target |
|--------|---------|--------|
| Average position hold time | 5 days | 12 days (with pyramiding) |
| Win rate on pyramided trades | N/A | 55%+ |
| Goal achievement rate | N/A | 70%+ |
| Risk-adjusted return (Sharpe) | 0.8 | 1.2+ |

---

## 8. Quick Start Implementation

### Step 1: Add Goals (Simplest First)
1. Add `StockGoal` model to `shared/db/models.py`
2. Add CRUD routes in `api-gateway`
3. Add Goals card to stock detail page
4. Add Goals column to positions page

### Step 2: Add Entry Score
1. Create `entry_score.py` in signal-engine
2. Add `/entry-score/{symbol}` endpoint
3. Display score badge on stock detail page

### Step 3: Add Pyramid Tracking
1. Add `PyramidLevel` and `PyramidPlan` models
2. Add pyramid routes
3. Add Pyramid tab to stock detail page
4. Add pyramid trigger alerts to scheduler

---

*Document maintained by StockAI development team*

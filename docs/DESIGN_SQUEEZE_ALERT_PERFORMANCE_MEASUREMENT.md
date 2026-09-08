# Short Squeeze Alert Performance Measurement — Design Document

**Date:** 2026-08-21  
**Author:** AI Assistant  
**Status:** DESIGN PROPOSAL  
**Priority:** HIGH — User explicitly requested this measurement

---

## 1. Problem Statement

### Current State
The squeeze alert system fires alerts when:
- **Short Squeeze:** ≥15% short float + ≥3% intraday move
- **Gamma Unwind (Calls):** ≥85% calls-dominant OI near expiry (bullish)
- **Gamma Unwind (Puts):** ≥55% puts-dominant OI near expiry (bearish)

### What's Working ✅
- Alerts are being **recorded** in `squeeze_alert_outcomes` table (107 rows)
- Entry prices are being filled (86/107 = 80%)
- Evaluator job runs daily at 18:15 ET

### What's NOT Working ❌
- **Forward returns (5d/10d/20d) are ALL NULL** — the evaluator runs but doesn't fill returns
- Alerts are too recent (earliest: 2026-08-15, entry_date: 2026-08-17)
- 5d window closes 2026-08-22 (tomorrow) — returns will start appearing then

### User's Question
> "Once I receive the short squeeze alert, will the price go up the other day or later? How well does it perform?"

---

## 2. Proposed Measurement Framework

### 2.1 Time Windows to Measure

| Window | Purpose | Why This Window |
|--------|---------|-----------------|
| **T+1 (Next Day)** | Immediate reaction | Did the squeeze continue the next trading day? |
| **T+2** | Short-term follow-through | 2-day momentum confirmation |
| **T+3** | Extended short-term | 3-day trend validation |
| **T+5 (1 week)** | Weekly performance | Standard short-term horizon |
| **T+10 (2 weeks)** | Medium-term | Did the squeeze thesis play out? |
| **T+20 (1 month)** | Long-term | Full squeeze cycle completion |

### 2.2 Metrics to Track

#### A. Return Metrics
```
return_Nd = (price_at_T+N - entry_price) / entry_price
```

| Metric | Formula | Purpose |
|--------|---------|---------|
| `return_1d` | Close T+1 vs Entry | Next-day performance |
| `return_2d` | Close T+2 vs Entry | 2-day performance |
| `return_3d` | Close T+3 vs Entry | 3-day performance |
| `return_5d` | Close T+5 vs Entry | Weekly performance |
| `max_gain_5d` | Max high in T+1 to T+5 vs Entry | Best possible exit |
| `max_drawdown_5d` | Max low in T+1 to T+5 vs Entry | Worst intraday pain |

#### B. Win Rate Metrics
```
win = return > cost_hurdle (0.5% for BUY thesis)
win = return < -cost_hurdle (for SELL/puts thesis)
```

| Metric | Formula | Purpose |
|--------|---------|---------|
| `win_rate_1d` | % of alerts where T+1 return > 0.5% | Next-day success rate |
| `win_rate_5d` | % of alerts where T+5 return > 0.5% | Weekly success rate |
| `positive_rate_1d` | % of alerts where T+1 return > 0% | Any positive move |

#### C. Quality Metrics
```
sharpe = mean(returns) / std(returns) * sqrt(252/holding_days)
```

| Metric | Formula | Purpose |
|--------|---------|---------|
| `avg_return` | Mean of all returns | Expected value |
| `median_return` | Median of all returns | Typical outcome |
| `sharpe_ratio` | Risk-adjusted return | Quality of signal |
| `profit_factor` | Sum(wins) / Sum(losses) | Edge magnitude |

---

## 3. Database Schema Changes

### 3.1 Add New Columns to `squeeze_alert_outcomes`

```sql
ALTER TABLE squeeze_alert_outcomes ADD COLUMN IF NOT EXISTS
    price_1d DOUBLE PRECISION,
    return_1d DOUBLE PRECISION,
    is_correct_1d BOOLEAN,
    
    price_2d DOUBLE PRECISION,
    return_2d DOUBLE PRECISION,
    is_correct_2d BOOLEAN,
    
    price_3d DOUBLE PRECISION,
    return_3d DOUBLE PRECISION,
    is_correct_3d BOOLEAN,
    
    high_5d DOUBLE PRECISION,      -- Max high in T+1 to T+5
    low_5d DOUBLE PRECISION,       -- Min low in T+1 to T+5
    max_gain_5d DOUBLE PRECISION,  -- (high_5d - entry) / entry
    max_drawdown_5d DOUBLE PRECISION,  -- (low_5d - entry) / entry
    
    -- Squeeze-specific context at fire time
    short_float_pct DOUBLE PRECISION,  -- Already stored as qualifying_metric for short_squeeze
    days_to_cover DOUBLE PRECISION,
    intraday_move_pct DOUBLE PRECISION,
    volume_ratio DOUBLE PRECISION;     -- Volume vs 20d avg at alert time
```

### 3.2 New Summary Table (Optional)

```sql
CREATE TABLE IF NOT EXISTS squeeze_alert_performance_summary (
    id SERIAL PRIMARY KEY,
    alert_type VARCHAR(24) NOT NULL,
    window_days INT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    
    -- Counts
    n_alerts INT NOT NULL,
    n_evaluated INT NOT NULL,
    
    -- Returns
    avg_return DOUBLE PRECISION,
    median_return DOUBLE PRECISION,
    std_return DOUBLE PRECISION,
    min_return DOUBLE PRECISION,
    max_return DOUBLE PRECISION,
    
    -- Win rates
    win_rate DOUBLE PRECISION,
    positive_rate DOUBLE PRECISION,
    
    -- Quality
    sharpe_ratio DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    
    computed_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(alert_type, window_days, period_start, period_end)
);
```

---

## 4. Backend Changes

### 4.1 Update `evaluate_squeeze_alert_outcomes()` in scheduler.py

```python
# Add to _SQUEEZE_OUTCOME_WINDOWS
_SQUEEZE_OUTCOME_WINDOWS = (1, 2, 3, 5, 10, 20)  # Was (5, 10, 20)

def evaluate_squeeze_alert_outcomes() -> None:
    """Enhanced evaluator with 1d/2d/3d windows and max gain/drawdown tracking."""
    # ... existing code ...
    
    for row in pending:
        # Fill 1d, 2d, 3d, 5d, 10d, 20d returns
        for window in _SQUEEZE_OUTCOME_WINDOWS:
            # ... existing logic ...
        
        # Calculate max gain and max drawdown for 5d window
        if row.entry_price and row.entry_date:
            highs_lows = _get_highs_lows_in_window(
                bucket, row.entry_date, row.entry_date + timedelta(days=7)
            )
            if highs_lows:
                row.high_5d = highs_lows['max_high']
                row.low_5d = highs_lows['min_low']
                row.max_gain_5d = (row.high_5d - row.entry_price) / row.entry_price
                row.max_drawdown_5d = (row.low_5d - row.entry_price) / row.entry_price
```

### 4.2 New API Endpoint: `/admin/squeeze-alert-performance/detailed`

```python
@router.get("/admin/squeeze-alert-performance/detailed")
def squeeze_alert_performance_detailed(
    days_back: int = 90,
    alert_type: str | None = None,
    session: Session = Depends(get_session),
):
    """Detailed performance breakdown by time window."""
    cutoff = date.today() - timedelta(days=days_back)
    
    query = select(SqueezeAlertOutcome).where(
        SqueezeAlertOutcome.fired_date >= cutoff
    )
    if alert_type:
        query = query.where(SqueezeAlertOutcome.alert_type == alert_type)
    
    rows = session.execute(query).scalars().all()
    
    # Compute stats for each window
    windows = [1, 2, 3, 5, 10, 20]
    stats = {}
    
    for w in windows:
        returns = [getattr(r, f"return_{w}d") for r in rows if getattr(r, f"return_{w}d") is not None]
        if not returns:
            stats[f"{w}d"] = {"n": 0, "note": "No data yet"}
            continue
        
        wins = [r for r in returns if r > 0.005]  # 0.5% hurdle
        
        stats[f"{w}d"] = {
            "n": len(returns),
            "avg_return_pct": round(np.mean(returns) * 100, 2),
            "median_return_pct": round(np.median(returns) * 100, 2),
            "std_return_pct": round(np.std(returns) * 100, 2),
            "win_rate": round(len(wins) / len(returns), 3),
            "positive_rate": round(len([r for r in returns if r > 0]) / len(returns), 3),
            "best_return_pct": round(max(returns) * 100, 2),
            "worst_return_pct": round(min(returns) * 100, 2),
        }
    
    return {
        "period": {"start": cutoff.isoformat(), "end": date.today().isoformat()},
        "total_alerts": len(rows),
        "by_window": stats,
        "by_alert_type": _group_by_alert_type(rows),
    }
```

---

## 5. Frontend Changes

### 5.1 Enhanced Performance Page

Add to `squeeze-alert-performance.tsx`:

```tsx
// New component: Detailed Window Breakdown
function WindowBreakdown({ data }: { data: DetailedPerformance }) {
  const windows = ['1d', '2d', '3d', '5d', '10d', '20d'];
  
  return (
    <div style={{ marginTop: 24 }}>
      <h3>Performance by Holding Period</h3>
      <p style={{ fontSize: 11, color: '#64748b' }}>
        "If I bought when the alert fired and sold N days later, what would my return be?"
      </p>
      
      <table>
        <thead>
          <tr>
            <th>Window</th>
            <th>Avg Return</th>
            <th>Win Rate</th>
            <th>Positive Rate</th>
            <th>Best</th>
            <th>Worst</th>
            <th>Sample</th>
          </tr>
        </thead>
        <tbody>
          {windows.map(w => {
            const stat = data.by_window[w];
            if (!stat || stat.n === 0) return (
              <tr key={w}>
                <td>{w}</td>
                <td colSpan={6} style={{ color: '#64748b' }}>No data yet</td>
              </tr>
            );
            return (
              <tr key={w}>
                <td style={{ fontWeight: 700 }}>{w}</td>
                <td style={{ color: stat.avg_return_pct >= 0 ? '#22c55e' : '#ef4444' }}>
                  {stat.avg_return_pct >= 0 ? '+' : ''}{stat.avg_return_pct}%
                </td>
                <td>
                  <WinRatePill rate={stat.win_rate} />
                </td>
                <td>{(stat.positive_rate * 100).toFixed(0)}%</td>
                <td style={{ color: '#22c55e' }}>+{stat.best_return_pct}%</td>
                <td style={{ color: '#ef4444' }}>{stat.worst_return_pct}%</td>
                <td style={{ color: '#64748b' }}>n={stat.n}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// New component: Next-Day Performance Focus
function NextDayPerformance({ data }: { data: DetailedPerformance }) {
  const stat = data.by_window['1d'];
  if (!stat || stat.n === 0) return null;
  
  return (
    <div style={{ 
      padding: 20, 
      background: '#0d1424', 
      borderRadius: 12,
      border: '1px solid #1e293b',
      marginBottom: 20
    }}>
      <h3 style={{ fontSize: 14, color: '#e2e8f0', marginBottom: 8 }}>
        🎯 Next-Day Performance (T+1)
      </h3>
      <p style={{ fontSize: 11, color: '#64748b', marginBottom: 16 }}>
        "Did the price go up the next day after I received the alert?"
      </p>
      
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: '#64748b' }}>Positive Rate</div>
          <div style={{ fontSize: 28, fontWeight: 800, color: stat.positive_rate >= 0.5 ? '#22c55e' : '#f59e0b' }}>
            {(stat.positive_rate * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: 10, color: '#475569' }}>
            of alerts had price UP the next day
          </div>
        </div>
        
        <div>
          <div style={{ fontSize: 11, color: '#64748b' }}>Avg Return</div>
          <div style={{ fontSize: 28, fontWeight: 800, color: stat.avg_return_pct >= 0 ? '#22c55e' : '#ef4444' }}>
            {stat.avg_return_pct >= 0 ? '+' : ''}{stat.avg_return_pct}%
          </div>
          <div style={{ fontSize: 10, color: '#475569' }}>
            average next-day move
          </div>
        </div>
        
        <div>
          <div style={{ fontSize: 11, color: '#64748b' }}>Win Rate (>0.5%)</div>
          <div style={{ fontSize: 28, fontWeight: 800, color: stat.win_rate >= 0.5 ? '#22c55e' : '#f59e0b' }}>
            {(stat.win_rate * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: 10, color: '#475569' }}>
            cleared the 0.5% cost hurdle
          </div>
        </div>
      </div>
    </div>
  );
}
```

---

## 6. Improvement Suggestions

### 6.1 Signal Quality Improvements

#### A. Add Pre-Alert Momentum Filter
```python
# Only fire alert if stock was already trending up before the squeeze
def _check_pre_squeeze_momentum(prices: list, lookback: int = 5) -> bool:
    """Require positive momentum BEFORE the squeeze day."""
    if len(prices) < lookback + 1:
        return True  # Not enough data, allow
    pre_squeeze_return = (prices[-2] - prices[-lookback-1]) / prices[-lookback-1]
    return pre_squeeze_return > 0  # Was already going up
```

**Rationale:** A squeeze on a stock that was already falling is more likely to be a dead-cat bounce.

#### B. Add Volume Confirmation
```python
# Require volume spike on squeeze day
def _check_volume_confirmation(volume: float, avg_volume: float) -> bool:
    """Require at least 1.5x average volume."""
    return volume >= avg_volume * 1.5
```

**Rationale:** Real squeezes have massive volume. Low-volume moves are more likely to reverse.

#### C. Add Short Interest Trend
```python
# Check if short interest is INCREASING (shorts doubling down = more fuel)
def _check_short_interest_trend(current_si: float, prev_si: float) -> str:
    if current_si > prev_si * 1.1:
        return "increasing"  # Shorts doubling down — more squeeze fuel
    elif current_si < prev_si * 0.9:
        return "decreasing"  # Shorts covering — squeeze may be ending
    return "stable"
```

**Rationale:** Rising short interest means more potential buyers (shorts who must cover).

### 6.2 Alert Timing Improvements

#### A. Intraday Alert Timing
```python
# Track WHEN during the day the alert fired
# Early alerts (9:30-11:00) may perform differently than late alerts (14:00-16:00)
def _categorize_alert_time(fired_at: datetime) -> str:
    hour = fired_at.hour
    if hour < 11:
        return "morning"  # 9:30-11:00
    elif hour < 14:
        return "midday"   # 11:00-14:00
    else:
        return "afternoon"  # 14:00-16:00
```

**Rationale:** Morning squeezes may have more follow-through than afternoon squeezes.

#### B. Day-of-Week Analysis
```python
# Track which day of week performs best
# Monday squeezes may differ from Friday squeezes
```

**Rationale:** Friday squeezes may reverse Monday as shorts cover over weekend.

### 6.3 Risk Management Improvements

#### A. Add Stop-Loss Recommendations
```python
# Based on historical max drawdown, recommend stop-loss levels
def _recommend_stop_loss(alert_type: str, historical_drawdowns: list) -> float:
    """Recommend stop-loss based on 90th percentile historical drawdown."""
    if not historical_drawdowns:
        return -0.05  # Default 5% stop
    p90_drawdown = np.percentile(historical_drawdowns, 90)
    return round(p90_drawdown * 1.1, 3)  # 10% buffer beyond historical
```

#### B. Add Position Sizing Guidance
```python
# Based on win rate and avg return, calculate Kelly criterion
def _kelly_position_size(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Kelly criterion for optimal position sizing."""
    if avg_loss == 0:
        return 0.0
    b = avg_win / abs(avg_loss)  # Win/loss ratio
    p = win_rate
    q = 1 - p
    kelly = (b * p - q) / b
    return max(0, min(kelly * 0.5, 0.25))  # Half-Kelly, capped at 25%
```

### 6.4 Alert Filtering Improvements

#### A. Blacklist Poor Performers
```python
# Track per-symbol performance and blacklist consistent losers
_SQUEEZE_SYMBOL_BLACKLIST = set()

def _update_symbol_blacklist(session: Session):
    """Blacklist symbols with <30% win rate over 20+ alerts."""
    stats = session.execute(text("""
        SELECT symbol, 
               COUNT(*) as n,
               AVG(CASE WHEN is_correct_5d THEN 1 ELSE 0 END) as win_rate
        FROM squeeze_alert_outcomes
        WHERE is_correct_5d IS NOT NULL
        GROUP BY symbol
        HAVING COUNT(*) >= 20
    """)).all()
    
    for symbol, n, win_rate in stats:
        if win_rate < 0.30:
            _SQUEEZE_SYMBOL_BLACKLIST.add(symbol)
```

#### B. Add Sector Filter
```python
# Some sectors squeeze better than others
# Tech/biotech may squeeze differently than utilities
def _check_sector_performance(sector: str, historical_data: dict) -> bool:
    """Only fire alerts for sectors with historical >40% win rate."""
    sector_win_rate = historical_data.get(sector, {}).get("win_rate", 0.5)
    return sector_win_rate >= 0.40
```

### 6.5 New Alert Types to Consider

#### A. Pre-Breakout Coiling Alert
Already exists as `PreBreakoutAlertOutcome` — measures stocks BEFORE the squeeze starts.

#### B. Squeeze Exhaustion Alert
```python
# Alert when a squeeze may be ENDING (time to take profits)
def _check_squeeze_exhaustion(
    days_in_squeeze: int,
    current_short_interest: float,
    peak_short_interest: float,
) -> bool:
    """Detect when squeeze fuel is running low."""
    si_decline = (peak_short_interest - current_short_interest) / peak_short_interest
    return days_in_squeeze >= 5 and si_decline >= 0.30  # 30% of shorts covered
```

#### C. Failed Squeeze Alert
```python
# Alert when a squeeze attempt FAILED (potential short opportunity)
def _check_failed_squeeze(
    triggered_squeeze: bool,
    return_3d: float,
) -> bool:
    """Detect failed squeeze — potential reversal."""
    return triggered_squeeze and return_3d < -0.05  # Gave back 5%+
```

---

## 7. Implementation Priority

| Priority | Task | Effort | Impact |
|----------|------|--------|--------|
| P0 | Add 1d/2d/3d return columns | 1 hour | Answers user's core question |
| P0 | Update evaluator for new windows | 2 hours | Enables measurement |
| P1 | Add NextDayPerformance UI component | 2 hours | User-facing answer |
| P1 | Add max_gain/max_drawdown tracking | 2 hours | Risk insight |
| P2 | Add volume confirmation filter | 1 hour | Improve signal quality |
| P2 | Add pre-squeeze momentum filter | 1 hour | Improve signal quality |
| P3 | Add symbol blacklist | 2 hours | Filter poor performers |
| P3 | Add sector analysis | 3 hours | Deeper insight |

---

## 8. Success Metrics

After implementation, we should see:

| Metric | Target | Current |
|--------|--------|---------|
| 1d positive rate | >55% | Unknown |
| 5d win rate | >50% | Unknown (0 data) |
| Avg 5d return | >1% | Unknown |
| Max drawdown 5d | <-8% (90th pct) | Unknown |

---

## 9. Next Steps

1. **Immediate:** Wait for 5d window to close (2026-08-22) to get first real data
2. **This week:** Add 1d/2d/3d columns and update evaluator
3. **Next week:** Build enhanced UI with NextDayPerformance component
4. **Month 1:** Implement signal quality filters based on measured performance

---

*Document created: 2026-08-21*

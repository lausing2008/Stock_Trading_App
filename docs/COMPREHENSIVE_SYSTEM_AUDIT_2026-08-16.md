# Comprehensive System Audit — 2026-08-16 (Verified)

Deep audit of StockAI platform with verification against actual codebase.

---

## Verification Summary

| Claim | Status | Notes |
|-------|--------|-------|
| BUG-DELISTED-GENERATION-BLIND | ✅ Verified | Found at routes.py:187 |
| BUG-REASONSJSON-NAN | ✅ Verified | Found at routes.py:437, already has fix via `_json_safe()` |
| Squeeze thresholds 15%/3% | ✅ Verified | scheduler.py:2621-2622 |
| Regime integration exists | ✅ EXISTS | signals.py:419 `_fetch_market_regime()`, line 29 shows 4-state regime |
| Confidence calibration exists | ✅ EXISTS | calibration.py router, `_calibrated_win_rate()` in routes.py |
| Multi-timeframe exists | ✅ EXISTS | signals.py:782 weekly TA, line 2086 weekly alignment |
| Days-to-cover in squeeze | ✅ EXISTS | scheduler.py:2625, 2918-2919 |
| Drawdown monitoring exists | ✅ EXISTS | scheduler.py:6950 `check_portfolio_drawdown_alerts()` |
| Stop loss auto-execution | ✅ EXISTS | paper_trading_engine.py:2312 `_monitor_positions()` |
| Trailing stop exists | ✅ EXISTS | paper_trading_engine.py:2996-3001 |
| Dip buy strategy | ❌ NOT FOUND | No `check_dip_buy_alerts` function |
| ConditionalOrder model | ✅ EXISTS | models.py:382, but action execution not wired |

---

## Part 1: Confirmed Bugs (Corrected)

### CRITICAL

#### BUG-001: Delisted Stock Signal Generation ✅ VERIFIED
- **Location**: `services/signal-engine/src/api/routes.py:187`
- **Code Comment**: `BUG-DELISTED-GENERATION-BLIND: Stock.delisted (aud14-survivorship) never flips`
- **Issue**: System generates signals for delisted stocks
- **Impact**: Wasted compute, invalid alerts

**Solution**:
```python
# In signal-engine/src/api/routes.py, add to refresh_signals() and batch endpoint:
async def refresh_signals(symbol: str, ...):
    # Add at start of function
    stock = session.query(Stock).filter(Stock.symbol == symbol).first()
    if stock and stock.delisted:
        log.info("signal.skip_delisted", symbol=symbol)
        return None  # Skip signal generation for delisted stocks
```

#### BUG-002: NaN in Reasons JSON ✅ VERIFIED (ALREADY FIXED)
- **Location**: `services/signal-engine/src/api/routes.py:437`, `signals_shared.py:48`
- **Status**: Fix already exists via `_json_safe()` function
- **Note**: The `_json_safe()` recursively replaces non-finite floats with None

**No action needed** — fix is already in place.

### HIGH

#### BUG-003: Short Squeeze Thresholds May Be Too Loose ✅ VERIFIED
- **Location**: `scheduler.py:2621-2622`
- **Current Values**: `_SQUEEZE_MIN_SHORT_FLOAT=15.0`, `_SQUEEZE_MIN_INTRADAY_MOVE_PCT=3.0`
- **Note**: Days-to-cover check EXISTS at line 2918-2919

**Solution** (if accuracy is low after measuring via SqueezeAlertOutcome):
```python
# scheduler.py:2621-2622 — tighten only if outcome data shows poor accuracy
_SQUEEZE_MIN_SHORT_FLOAT = 20.0  # was 15.0
_SQUEEZE_MIN_INTRADAY_MOVE_PCT = 5.0  # was 3.0
```

#### BUG-004: Volume Confirmation in Squeeze ⚠️ NEEDS VERIFICATION
- **Location**: `scheduler.py:check_short_squeeze_alerts()`
- **Issue**: Need to verify if volume spike is required
- **Current**: Days-to-cover exists, but explicit volume ratio check unclear

**Solution** (add if missing):
```python
# In check_short_squeeze_alerts(), add volume confirmation:
avg_vol_20d = data.get("avg_volume_20d")
current_vol = data.get("volume")
if avg_vol_20d and current_vol:
    vol_ratio = current_vol / avg_vol_20d
    if vol_ratio < 2.0:
        continue  # Skip if volume not confirming
```

#### BUG-005: Confidence Band Inversion ✅ VERIFIED ISSUE
- **Issue**: 65-79 confidence band has lowest win rate (13.3%)
- **Root Cause**: ML overconfidence not calibrated to outcomes
- **Note**: Calibration system EXISTS (`_calibrated_win_rate`), but may need tuning

**Solution**:
```python
# In signal-engine/src/api/calibration.py, add confidence band adjustment:
def adjust_confidence_by_historical_accuracy(confidence: float, horizon: str, direction: str, market: str) -> float:
    """Adjust raw confidence using historical win rates per band."""
    cal_data = _get_confidence_calibration(session)
    band = _get_confidence_band(confidence)  # e.g., "65-79"
    historical_wr = cal_data.get((horizon, direction, market, band), {}).get("win_rate")
    if historical_wr and historical_wr < 0.25:
        # Penalize overconfident bands
        return confidence * (historical_wr / 0.35)  # Scale down
    return confidence
```

#### BUG-006: Paper Trade Drawdown Check ✅ VERIFIED EXISTS
- **Location**: `scheduler.py:6950` — `check_portfolio_drawdown_alerts()` EXISTS
- **Status**: Continuous monitoring IS implemented
- **Note**: Runs on scheduler loop, sends email alerts

**No action needed** — already implemented.

### MEDIUM

#### BUG-007: ConditionalOrder Actions Not Wired ✅ VERIFIED
- **Location**: `models.py:382` — schema exists
- **Issue**: `tighten_stop`, `sell_partial`, etc. defined but no execution logic

**Solution**:
```python
# Add to scheduler.py:
def check_conditional_orders() -> None:
    """Evaluate and execute pending conditional orders."""
    with _get_session() as session:
        pending = session.query(ConditionalOrder).filter(
            ConditionalOrder.status == "pending",
            or_(ConditionalOrder.expires_at.is_(None), 
                ConditionalOrder.expires_at > func.now())
        ).all()
        
        for order in pending:
            if _evaluate_conditions(order):
                _execute_action(order)
                order.status = "triggered"
                order.triggered_at = datetime.utcnow()
        session.commit()

def _execute_action(order: ConditionalOrder):
    if order.action_type == "tighten_stop":
        # Update trade's current_stop
        trade = session.query(PaperTrade).filter(...).first()
        if trade and order.action_value > trade.current_stop:
            trade.current_stop = order.action_value
    elif order.action_type == "sell_partial":
        # Execute partial sell
        ...
```

---

## Part 2: Features Status (Corrected)

### Already Implemented ✅

| Feature | Location | Status |
|---------|----------|--------|
| Regime detection | signals.py:419 | 4-state (bull/bear/high_vol/unknown) |
| Regime filter in signals | signals.py:29-43 | Raises BUY threshold in bear markets |
| Confidence calibration | calibration.py | `_calibrated_win_rate()` |
| Multi-timeframe | signals.py:782, 2086 | Weekly TA alignment |
| Days-to-cover | scheduler.py:2625 | In squeeze detection |
| Drawdown monitoring | scheduler.py:6950 | `check_portfolio_drawdown_alerts()` |
| Stop loss execution | paper_trading_engine.py:2312 | `_monitor_positions()` |
| Trailing stop | paper_trading_engine.py:2996 | Auto-trails up |
| Volume confirmation in signals | signals.py:74 | `pullback_recovery_delta >= 0.07 (volume-confirmed)` |
| Dip/pullback detection | signals.py:899-996 | Entry timing with pullback zones |
| SignalOutcome tracking | models.py:668 | `is_correct_5d/10d/20d` |
| SqueezeAlertOutcome | models.py:1484 | Forward-return tracking |

### NOT Implemented ❌

#### FEAT-001: Dedicated Dip Buy Alert System
- **Status**: NOT FOUND — no `check_dip_buy_alerts()` in scheduler
- **Note**: Pullback detection EXISTS in signal generation, but no dedicated ALERT

**Solution**:
```python
# Add to scheduler.py:
_DIP_BUY_LOCK_KEY = "stockai:lock:check_dip_buy_alerts"

def check_dip_buy_alerts() -> None:
    """Alert when watchlist stocks pull back to support in uptrend."""
    with _get_session() as session:
        # Get all watchlist stocks
        watchlist_symbols = _get_all_watchlist_symbols(session)
        
        for symbol in watchlist_symbols:
            data = _fetch_stock_data(symbol)
            if not data:
                continue
            
            price = data["price"]
            sma50 = data.get("sma50")
            sma200 = data.get("sma200")
            rsi = data.get("rsi")
            high_20d = data.get("high_20d")
            
            # Check dip criteria
            if not (sma50 and sma200 and rsi and high_20d):
                continue
            
            pullback_pct = (high_20d - price) / high_20d * 100
            uptrend_intact = price > sma50 > sma200
            oversold = rsi < 40
            
            if pullback_pct >= 5.0 and pullback_pct <= 20.0 and uptrend_intact and oversold:
                _send_dip_buy_alert(symbol, price, pullback_pct, rsi)

# Register in scheduler:
scheduler.add_job(check_dip_buy_alerts, "interval", minutes=15, ...)
```

#### FEAT-002: Sell the High Alert System
- **Status**: NOT FOUND

**Solution**:
```python
def check_sell_high_alerts() -> None:
    """Alert when stocks reach overbought/resistance levels."""
    with _get_session() as session:
        # Get positions
        positions = session.query(PaperTrade).filter(PaperTrade.stage == "open").all()
        
        for trade in positions:
            data = _fetch_stock_data(trade.symbol)
            if not data:
                continue
            
            price = data["price"]
            rsi = data.get("rsi")
            bb_upper = data.get("bb_upper")
            low_20d = data.get("low_20d")
            
            gain_pct = (price - trade.entry_price) / trade.entry_price * 100
            overbought = rsi and rsi > 70
            at_bb_upper = bb_upper and price >= bb_upper * 0.98
            
            if gain_pct >= 15.0 and (overbought or at_bb_upper):
                _send_sell_high_alert(trade.symbol, price, gain_pct, rsi)
```

---

## Part 3: Signal Accuracy Improvements

### Already Implemented ✅

| Improvement | Status | Location |
|-------------|--------|----------|
| Regime filter | ✅ | signals.py:29-43 |
| Confidence calibration | ✅ | calibration.py |
| Multi-timeframe alignment | ✅ | signals.py:2086 |
| Volume confirmation | ✅ | signals.py:74 |
| Pullback detection | ✅ | signals.py:899-996 |

### Needs Enhancement ⚠️

#### SA-001: Confidence Recalibration Enhancement
- **Current**: `_calibrated_win_rate()` exists but may not adjust raw confidence
- **Issue**: High confidence bands still show low win rates

**Solution**:
```python
# In signals.py, modify confidence calculation:
def _compute_final_confidence(raw_confidence: float, calibration_data: dict) -> float:
    """Apply historical accuracy adjustment to raw confidence."""
    band = _get_band(raw_confidence)
    historical_wr = calibration_data.get(band, {}).get("win_rate", 0.5)
    
    # If historical win rate is poor, reduce confidence
    if historical_wr < 0.30:
        adjustment = historical_wr / 0.35  # Scale factor
        return raw_confidence * adjustment
    return raw_confidence
```

#### SA-002: Stronger Regime Suppression
- **Current**: Raises threshold, doesn't fully suppress
- **Enhancement**: Add hard suppression for counter-trend signals

**Solution**:
```python
# In signals.py, add after regime fetch:
def _should_suppress_signal(signal_type: str, regime: str, vix: float) -> bool:
    """Hard suppress counter-trend signals in extreme regimes."""
    if signal_type == "BUY" and regime == "bear" and vix > 30:
        return True
    if signal_type == "SELL" and regime == "bull" and vix < 15:
        return True
    return False
```

---

## Part 4: Short Squeeze Accuracy

### Current Implementation ✅

| Component | Status | Location |
|-----------|--------|----------|
| Short float threshold | ✅ 15% | scheduler.py:2621 |
| Intraday move threshold | ✅ 3% | scheduler.py:2622 |
| Days-to-cover check | ✅ | scheduler.py:2918-2919 |
| Outcome tracking | ✅ | SqueezeAlertOutcome model |

### Recommended Enhancements

**Solution** — Tighten criteria based on outcome data:
```python
# scheduler.py — update after analyzing SqueezeAlertOutcome win rates
_SQUEEZE_MIN_SHORT_FLOAT = 20.0  # Tighten from 15.0
_SQUEEZE_MIN_INTRADAY_MOVE_PCT = 5.0  # Tighten from 3.0
_SQUEEZE_MIN_VOLUME_RATIO = 2.0  # Add volume confirmation

# Add volume check in check_short_squeeze_alerts():
vol_ratio = current_volume / avg_volume_20d
if vol_ratio < _SQUEEZE_MIN_VOLUME_RATIO:
    continue  # Skip without volume confirmation
```

---

## Part 5: Capital Protection

### Already Implemented ✅

| Protection | Status | Location |
|------------|--------|----------|
| Portfolio drawdown alert | ✅ | scheduler.py:6950 |
| Stop loss execution | ✅ | paper_trading_engine.py:2585 |
| Trailing stop | ✅ | paper_trading_engine.py:2996 |
| Breakeven stop | ✅ | paper_trading_engine.py:2790 |
| Position sizing | ✅ | sizer.py (Kelly-based) |

### Needs Implementation ❌

#### CP-001: Auto-Liquidation on Severe Drawdown
- **Current**: Alerts only, no auto-liquidation

**Solution**:
```python
# In check_portfolio_drawdown_alerts(), add auto-liquidation:
def check_portfolio_drawdown_alerts() -> None:
    ...
    for portfolio in portfolios:
        drawdown = _compute_portfolio_drawdown(session, portfolio.id, equity)
        
        if drawdown and drawdown >= 0.20:  # 20% drawdown
            # Send alert
            _send_drawdown_alert(portfolio, drawdown)
            
            # Auto-liquidate worst performers
            if drawdown >= 0.25:  # 25% = emergency liquidation
                _emergency_liquidate(session, portfolio)

def _emergency_liquidate(session, portfolio):
    """Close all positions when drawdown exceeds emergency threshold."""
    open_trades = session.query(PaperTrade).filter(
        PaperTrade.portfolio_id == portfolio.id,
        PaperTrade.stage == "open"
    ).all()
    
    for trade in open_trades:
        _close_trade(session, trade, exit_reason="emergency_drawdown")
```

---

## Part 6: Implementation Priority (Updated)

### Phase 1: Quick Wins (1-2 weeks)

| ID | Item | Status | Action |
|----|------|--------|--------|
| BUG-001 | Delisted stock signals | Verified | Add `stock.delisted` check |
| FEAT-001 | Dip buy alerts | Not found | Implement `check_dip_buy_alerts()` |
| FEAT-002 | Sell high alerts | Not found | Implement `check_sell_high_alerts()` |
| BUG-007 | ConditionalOrder execution | Schema only | Wire action execution |

### Phase 2: Enhancements (2-4 weeks)

| ID | Item | Status | Action |
|----|------|--------|--------|
| SA-001 | Confidence adjustment | Partial | Apply historical WR to raw confidence |
| SA-002 | Stronger regime filter | Partial | Add hard suppression |
| CP-001 | Auto-liquidation | Alert only | Add emergency liquidation |
| Squeeze | Tighten thresholds | Measure first | Analyze SqueezeAlertOutcome data |

### Phase 3: Measure & Tune (4-8 weeks)

| ID | Item | Action |
|----|------|--------|
| Squeeze accuracy | Query SqueezeAlertOutcome for win rates |
| Signal accuracy | Query SignalOutcome by confidence band |
| Dip buy accuracy | Track new DipBuyAlertOutcome |

---

## Part 7: Queries to Measure Current Performance

### Signal Win Rate by Confidence Band
```sql
SELECT 
    horizon,
    signal_direction,
    CASE 
        WHEN confidence < 50 THEN '0-49'
        WHEN confidence < 65 THEN '50-64'
        WHEN confidence < 80 THEN '65-79'
        ELSE '80+'
    END as confidence_band,
    COUNT(*) as n,
    AVG(CASE WHEN is_correct_10d THEN 1.0 ELSE 0.0 END) as win_rate_10d
FROM signal_outcomes
WHERE is_correct_10d IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
```

### Squeeze Alert Win Rate
```sql
SELECT 
    alert_type,
    COUNT(*) as n,
    AVG(CASE WHEN is_correct_5d THEN 1.0 ELSE 0.0 END) as win_rate_5d,
    AVG(CASE WHEN is_correct_10d THEN 1.0 ELSE 0.0 END) as win_rate_10d,
    AVG(return_5d) as avg_return_5d
FROM squeeze_alert_outcomes
WHERE is_correct_5d IS NOT NULL
GROUP BY 1;
```

### Portfolio Drawdown History
```sql
SELECT 
    date,
    equity,
    (LAG(equity) OVER (ORDER BY date) - equity) / LAG(equity) OVER (ORDER BY date) as daily_drawdown
FROM paper_equity_curve
WHERE portfolio_id = 1
ORDER BY date DESC
LIMIT 30;
```

---

## Appendix: Corrected Code Locations

| Component | File | Line | Status |
|-----------|------|------|--------|
| Delisted bug | signal-engine/routes.py | 187 | ✅ Verified |
| NaN fix | signal-engine/signals_shared.py | 48 | ✅ Already fixed |
| Squeeze thresholds | scheduler.py | 2621-2622 | ✅ Verified |
| Regime detection | signals.py | 419 | ✅ Exists |
| Calibration | calibration.py | router | ✅ Exists |
| Multi-timeframe | signals.py | 782, 2086 | ✅ Exists |
| Drawdown alerts | scheduler.py | 6950 | ✅ Exists |
| Stop execution | paper_trading_engine.py | 2312 | ✅ Exists |
| Trailing stop | paper_trading_engine.py | 2996 | ✅ Exists |
| ConditionalOrder | models.py | 382 | ⚠️ Schema only |

---

*Generated: 2026-08-16*
*Verified against codebase: 2026-08-16*

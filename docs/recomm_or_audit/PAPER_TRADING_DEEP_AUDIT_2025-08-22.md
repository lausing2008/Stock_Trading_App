# Paper Trading Deep Audit & Optimization

**Date:** 2025-08-22  
**Scope:** Paper trading performance, decision making, config optimization  
**Data Source:** Production EC2 PostgreSQL (18.205.121.71)  
**Total Trades Analyzed:** 111 (97 closed, 14 open)

---

## Executive Summary

### Overall Performance

| Metric | Value | Status |
|--------|-------|--------|
| Total Trades | 111 | - |
| Closed Trades | 97 | - |
| Win Rate | 32.99% | 🔴 POOR |
| Total P&L | -$6,781.75 | 🔴 LOSING |
| Avg P&L/Trade | -$69.91 | 🔴 NEGATIVE |
| Avg Winner | +$430.85 | ✅ Good |
| Avg Loser | -$316.45 | ⚠️ Acceptable |
| Win/Loss Ratio | 1.36:1 | ✅ Good |

### Key Findings

| Finding | Severity | Impact |
|---------|----------|--------|
| **Risk-Off Regime = 0% Win Rate** | 🔴 CRITICAL | -$10,805 from 10 trades |
| **Entry Score 5-6 = Disaster** | 🔴 CRITICAL | -$12,429 from 47 trades |
| **SWING Style Bleeding** | 🔴 CRITICAL | -$8,792 (27.66% win rate) |
| **Short Hold = Losses** | 🟠 HIGH | 0-4 days = 22.58% win rate |
| **Trailing Stop = 100% Win** | ✅ EXCELLENT | +$4,141 from 7 trades |
| **GROWTH Style Profitable** | ✅ GOOD | +$2,010 (38% win rate) |
| **Choppy Regime = Best** | ✅ EXCELLENT | 71.43% win rate, +$3,023 |

---

## Part 1: Performance by Portfolio

| ID | Portfolio | Initial | Trades | Closed | Win Rate | Total P&L | Avg P&L |
|----|-----------|---------|--------|--------|----------|-----------|---------|
| 1 | GROWTH Paper | $50,000 | 43 | 36 | 36.11% | +$93.16 | +$2.59 |
| 2 | HK SWING | $300,000 | 4 | 4 | **0.00%** | **-$6,610** | -$1,652 |
| 3 | US SWING | $50,000 | 38 | 34 | 35.29% | -$1,392 | -$40.97 |
| 4 | HK GROWTH | $300,000 | 15 | 14 | **42.86%** | **+$1,917** | +$136.97 |
| 5 | ETrade SWING | $50,000 | 11 | 9 | 11.11% | -$789 | -$87.67 |

**Key Insights:**
- GROWTH portfolios (1, 4) are profitable or near breakeven
- SWING portfolios (2, 3, 5) are all losing money
- HK SWING has 0% win rate with massive losses
- HK GROWTH has the best win rate at 42.86%

---

## Part 2: Performance by Trading Style

| Style | Trades | Wins | Win Rate | Total P&L | Avg P&L | Avg Win | Avg Loss |
|-------|--------|------|----------|-----------|---------|---------|----------|
| **GROWTH** | 50 | 19 | **38.00%** | **+$2,010** | +$40.22 | +$584.73 | -$293.52 |
| SWING | 47 | 13 | 27.66% | -$8,792 | -$187.08 | +$205.96 | -$337.35 |

**Analysis:**
- GROWTH has 10% higher win rate than SWING
- GROWTH avg winner (+$584) is 2.8x larger than SWING avg winner (+$205)
- GROWTH avg loser (-$293) is smaller than SWING avg loser (-$337)
- **Recommendation:** Default to GROWTH style, restrict SWING usage

---

## Part 3: Performance by Exit Reason

| Exit Reason | Trades | Wins | Win Rate | Total P&L | Avg P&L |
|-------------|--------|------|----------|-----------|---------|
| **trailing_stop** | 7 | 7 | **100%** | **+$4,141** | +$591.62 |
| **target_reached** | 7 | 7 | **100%** | **+$1,968** | +$281.17 |
| breakeven_stop | 29 | 4 | 13.79% | +$370 | +$12.77 |
| stop_hit | 52 | 14 | 26.92% | -$13,066 | -$251.27 |
| momentum_exit | 1 | 0 | 0% | -$9.79 | -$9.79 |
| signal_exit | 1 | 0 | 0% | -$185.67 | -$185.67 |

**Key Insights:**
- Trailing stop and target_reached are the ONLY profitable exit types
- 52 trades (54%) hit stop_loss = -$13,066 total loss
- Breakeven stop saves money but rarely produces winners

**Recommendation:** Activate trailing stops earlier (reduce trail_trigger_pct from 5% to 3%)

---

## Part 4: Performance by Market Regime

| Regime | Trades | Wins | Win Rate | Total P&L | Avg P&L |
|--------|--------|------|----------|-----------|---------|
| **choppy** | 7 | 5 | **71.43%** | **+$3,023** | +$431.88 |
| bull | 80 | 27 | 33.75% | +$1,000 | +$12.51 |
| **risk_off** | 10 | 0 | **0.00%** | **-$10,805** | -$1,080.55 |

**🔴 CRITICAL:** Risk-off regime has 0% win rate and -$10,805 P&L. The bear gate is NOT working.

**Recommendation:** 
1. Hard block ALL entries during risk_off regime
2. Increase position size in choppy regime (currently 0.75x, consider 1.0x)

---

## Part 5: Performance by Entry Score

| Score | Trades | Wins | Win Rate | Total P&L | Avg P&L | Status |
|-------|--------|------|----------|-----------|---------|--------|
| 3 | 16 | 6 | 37.50% | -$156 | -$9.78 | ⚠️ Marginal |
| **4** | 20 | 11 | **55.00%** | **+$1,950** | +$97.54 | ✅ BEST |
| **5** | 29 | 4 | **13.79%** | **-$6,073** | -$209.42 | 🔴 DISASTER |
| **6** | 18 | 5 | **27.78%** | **-$6,356** | -$353.16 | 🔴 BAD |
| 7 | 5 | 2 | 40.00% | +$523 | +$104.78 | ✅ OK |
| 8 | 6 | 2 | 33.33% | +$1,099 | +$183.30 | ✅ OK |
| **9** | 3 | 2 | **66.67%** | **+$2,230** | +$743.35 | ✅ EXCELLENT |

**🔴 CRITICAL:** Entry scores 5-6 have the WORST performance:
- Score 5: 13.79% win rate, -$6,073 P&L
- Score 6: 27.78% win rate, -$6,356 P&L
- Combined: -$12,429 from 47 trades

**Recommendation:**
1. Block entry scores 5-6 entirely
2. Only allow scores 4, 7, 8, 9
3. Or redesign the scoring formula

---

## Part 6: Performance by R:R Ratio

| R:R Band | Trades | Wins | Win Rate | Total P&L | Avg P&L |
|----------|--------|------|----------|-----------|---------|
| 2.0-2.5 | 42 | 11 | 26.19% | -$8,867 | -$211.14 |
| **2.5-3.0** | 39 | 14 | **35.90%** | **+$4,823** | +$123.67 |
| 3.0-3.5 | 5 | 3 | 60.00% | -$712 | -$142.45 |
| 3.5+ | 11 | 4 | 36.36% | -$2,024 | -$184.07 |

**Key Insight:** R:R 2.5-3.0 is the sweet spot with best P&L (+$4,823)

**Recommendation:** Set min_rr_ratio to 2.5 (currently 2.0)

---

## Part 7: Performance by Confidence Band

| Confidence | Trades | Wins | Win Rate | Total P&L | Avg P&L |
|------------|--------|------|----------|-----------|---------|
| **< 55** | 37 | 10 | 27.03% | **+$1,804** | +$48.76 |
| 55-65 | 17 | 7 | 41.18% | -$2,837 | -$166.92 |
| 65-75 | 17 | 5 | 29.41% | -$1,508 | -$88.72 |
| 75-85 | 15 | 6 | 40.00% | -$4,140 | -$276.06 |
| 85+ | 11 | 4 | 36.36% | -$98 | -$8.99 |

**Surprising Finding:** Low confidence (<55%) has the BEST P&L (+$1,804)!

This confirms the inverted confidence calibration issue. The current min_confidence of 15% is actually working better than higher thresholds.

**Recommendation:** Keep min_confidence low (15-30%) until confidence formula is fixed

---

## Part 8: Performance by Hold Duration

| Hold Days | Trades | Wins | Win Rate | Total P&L | Avg P&L |
|-----------|--------|------|----------|-----------|---------|
| **0-4 days** | 62 | 14 | **22.58%** | **-$8,134** | -$131.20 |
| 5-9 days | 13 | 3 | 23.08% | -$1,102 | -$84.84 |
| **10-19 days** | 18 | 11 | **61.11%** | **+$1,545** | +$85.87 |
| **20-29 days** | 3 | 3 | **100%** | **+$722** | +$240.83 |
| 30+ days | 1 | 1 | 100% | +$187 | +$187.16 |

**🔴 CRITICAL:** Short holds (0-4 days) have terrible performance:
- 62 trades (64% of all trades)
- 22.58% win rate
- -$8,134 total loss

**Key Insight:** Longer holds = better performance
- 10-19 days: 61.11% win rate
- 20-29 days: 100% win rate

**Recommendation:**
1. Increase min hold before stop triggers (currently stops hit too fast)
2. Consider time-based stop widening for first 5 days
3. Or require stronger signals for short-term trades

---

## Part 9: Winner vs Loser Characteristics

| Metric | Winners (32) | Losers (65) | Insight |
|--------|--------------|-------------|---------|
| Avg Hold Days | **10.3** | 4.0 | Winners hold 2.5x longer |
| Avg Entry Score | 5.0 | 5.1 | No difference |
| Avg Confidence | 62.7 | 61.9 | No difference |
| Avg R:R | **3.37** | 2.72 | Winners have higher R:R |
| Avg K-Score | 68.5 | 68.7 | No difference |

**Key Differentiators:**
1. **Hold Duration:** Winners hold 10.3 days vs losers 4.0 days
2. **R:R Ratio:** Winners have 3.37 R:R vs losers 2.72

**Recommendation:** 
- Require min R:R of 2.5+ (filters out 2.72 avg loser R:R)
- Implement time-based stop protection for first 5-7 days

---

## Part 10: Exit Type Characteristics

| Exit Type | Avg Hold | Avg Score | Avg Conf | Avg R:R |
|-----------|----------|-----------|----------|---------|
| breakeven_stop | 4.7 | 5.1 | 63.6 | 2.80 |
| stop_hit | 5.7 | 4.9 | 62.1 | 2.73 |
| target_reached | 5.7 | 4.1 | 58.3 | 2.21 |
| **trailing_stop** | **15.4** | **6.9** | **68.4** | **5.46** |

**Key Insight:** Trailing stop winners have:
- Longest hold (15.4 days)
- Highest entry score (6.9)
- Highest confidence (68.4)
- Highest R:R (5.46)

**Recommendation:** For high-score, high-confidence trades, use wider stops and longer hold targets

---

## Part 11: Improvements Tracker Status

### Paper Trading Improvements (All Marked Done)

| ID | Feature | Status | Working? |
|----|---------|--------|----------|
| pt-live-price-fallback | Price fallback chain | ✅ Done | ✅ Yes |
| pt-atr-none-crash | ATR validation | ✅ Done | ✅ Yes |
| pt-hold-days-calendar | Trading days calc | ✅ Done | ⚠️ Unclear |
| pt-drawdown-circuit-breaker | 20% max drawdown | ✅ Done | ✅ Yes |
| pt-open-risk-limit | 12% max open risk | ✅ Done | ✅ Yes |
| pt-slippage-model | 10bps slippage | ✅ Done | ✅ Yes |
| pt-market-hours | 9:30-16:00 ET only | ✅ Done | ✅ Yes |
| pt-entry-score-calibration | Logistic regression | ✅ Done | 🔴 NOT WORKING |
| pt-regime-adaptive-stops | Regime trail adjust | ✅ Done | ⚠️ Unclear |
| pt-earnings-position-sizing | DTE size reduction | ✅ Done | ✅ Yes |
| pt-n-plus-one-signals | Batch signal query | ✅ Done | ✅ Yes |
| pt-atr-caching | Batch ATR fetch | ✅ Done | ✅ Yes |
| pt-trade-attribution | Attribution endpoint | ✅ Done | ✅ Yes |
| pt-regime-equity-overlay | Regime on chart | ✅ Done | ✅ Yes |
| pt-multi-portfolio | Multiple portfolios | ✅ Done | ✅ Yes |

### Features NOT Working as Expected

1. **pt-entry-score-calibration**: Entry scores 5-6 still have worst performance
2. **Risk-off gate**: 10 trades entered during risk_off with 0% win rate
3. **Trailing stop activation**: Only 7 trades (7%) reached trailing stop

---

## Part 12: Current Config Analysis

### GROWTH Paper Portfolio (Best Performer)

```json
{
  "trading_style": "GROWTH",
  "min_confidence": 15.0,      // ✅ Low is good (inverted calibration)
  "min_kscore": 48.0,          // ✅ Reasonable
  "min_rr_ratio": 2.0,         // ⚠️ Should be 2.5
  "min_entry_score": 3,        // ⚠️ Should block 5-6
  "max_hold_days": 60,         // ✅ Good for GROWTH
  "max_positions": 12,         // ✅ OK
  "trail_trigger_pct": 0.05,   // ⚠️ Should be 0.03
  "breakeven_trigger_pct": 0.03, // ✅ OK
  "risk_per_trade_pct": 0.01,  // ✅ Conservative
  "max_portfolio_drawdown_pct": 0.2 // ✅ OK
}
```

### ETrade Sandbox SWING (Worst Performer)

```json
{
  "trading_style": "SWING",
  "min_confidence": 30.0,      // ⚠️ Higher conf = worse (inverted)
  "min_kscore": 50.0,          // ✅ OK
  "min_rr_ratio": 2.0,         // ⚠️ Should be 2.5
  "min_entry_score": 4,        // ✅ Good (blocks 3)
  "max_hold_days": 20,         // ⚠️ Too short for SWING
  "max_positions": 6,          // ✅ OK
  "trail_trigger_pct": 0.03,   // ✅ Good
  "regime_risk_off_gate": true // ✅ Good but not working?
}
```

---

## Part 13: Optimized Config Recommendations

### For GROWTH Style (Recommended Default)

```json
{
  "trading_style": "GROWTH",
  "market": "US",
  
  // Entry Filters
  "min_confidence": 15.0,        // Keep low (inverted calibration)
  "min_kscore": 50.0,            // Slightly higher
  "min_rr_ratio": 2.5,           // ⬆️ Increased from 2.0
  "min_entry_score": 4,          // ⬆️ Block score 3
  "blocked_entry_scores": [5, 6], // 🆕 Block disaster scores
  
  // Position Sizing
  "max_positions": 10,
  "max_position_pct": 0.10,
  "risk_per_trade_pct": 0.01,
  "max_open_risk_pct": 0.12,
  "max_sector_pct": 0.30,
  
  // Hold & Exit
  "max_hold_days": 60,
  "trail_trigger_pct": 0.03,     // ⬇️ Earlier trailing (was 0.05)
  "trail_atr_mult": 1.8,         // ⬇️ Tighter trail (was 2.0)
  "breakeven_trigger_pct": 0.025, // ⬇️ Earlier breakeven (was 0.03)
  "min_hold_before_stop": 3,     // 🆕 Don't stop out in first 3 days
  
  // Regime
  "enable_regime_filter": true,
  "regime_risk_off_gate": true,  // 🆕 Hard block risk_off
  "regime_bear_size_mult": 0.0,  // No entries in bear
  "regime_risk_off_size_mult": 0.0, // 🆕 No entries in risk_off (was 0.5)
  "regime_choppy_size_mult": 1.0, // ⬆️ Full size in choppy (was 0.75)
  "regime_bull_size_mult": 1.0,
  
  // Risk Controls
  "max_portfolio_drawdown_pct": 0.15, // ⬇️ Tighter (was 0.20)
  "max_daily_loss_pct": 0.03,    // ⬇️ Tighter (was 0.04)
  "max_entries_per_day": 3,      // ⬇️ Fewer entries (was 5)
  "max_consecutive_losses": 4    // 🆕 Pause after 4 losses
}
```

### For SWING Style (Use Sparingly)

```json
{
  "trading_style": "SWING",
  "market": "US",
  
  // Entry Filters - STRICTER
  "min_confidence": 15.0,
  "min_kscore": 55.0,            // ⬆️ Higher bar
  "min_rr_ratio": 2.5,
  "min_entry_score": 4,
  "blocked_entry_scores": [5, 6],
  "min_ta_score": 0.55,          // 🆕 Require good TA
  
  // Position Sizing - SMALLER
  "max_positions": 6,
  "max_position_pct": 0.08,      // ⬇️ Smaller positions
  "risk_per_trade_pct": 0.008,   // ⬇️ Less risk per trade
  
  // Hold & Exit - FASTER TRAILING
  "max_hold_days": 25,           // ⬇️ Shorter hold
  "trail_trigger_pct": 0.025,    // ⬇️ Very early trailing
  "trail_atr_mult": 1.5,         // ⬇️ Tight trail
  "breakeven_trigger_pct": 0.02,
  
  // Regime - VERY STRICT
  "regime_risk_off_gate": true,
  "regime_bear_size_mult": 0.0,
  "regime_risk_off_size_mult": 0.0,
  "regime_choppy_size_mult": 0.5, // ⬇️ Half size in choppy
  
  // Risk Controls - TIGHTER
  "max_portfolio_drawdown_pct": 0.12,
  "max_daily_loss_pct": 0.025,
  "max_entries_per_day": 2,
  "max_consecutive_losses": 3
}
```

---

## Part 14: Code Changes Required

### 1. Block Entry Scores 5-6

```python
# In paper_trading_engine.py _should_enter()
def _should_enter(self, signal, game_plan, cfg, live_regime=None):
    # Add blocked scores check
    blocked_scores = cfg.get("blocked_entry_scores", [5, 6])
    if entry_score in blocked_scores:
        return False, f"entry_score_{entry_score}_blocked"
    
    # Rest of function...
```

### 2. Hard Block Risk-Off Regime

```python
# In paper_trading_engine.py _scan_for_entries()
def _scan_for_entries(self, portfolio, cfg, session):
    live_regime = self._fetch_market_regime(cfg)
    
    # Hard block risk_off - no override
    if live_regime.get("state") == "risk_off":
        log.warning("paper.regime_gate_risk_off_hard", 
                   msg="Blocking ALL entries in risk_off regime")
        return []
    
    # Rest of function...
```

### 3. Minimum Hold Before Stop

```python
# In paper_trading_engine.py _monitor_positions()
def _check_stop_loss(self, trade, live_price, cfg):
    min_hold = cfg.get("min_hold_before_stop", 3)
    hold_days = (date.today() - trade.entry_date).days
    
    if hold_days < min_hold:
        # Don't trigger stop in first N days unless catastrophic
        catastrophic_loss = (trade.entry_price - live_price) / trade.entry_price > 0.15
        if not catastrophic_loss:
            return False, "min_hold_protection"
    
    # Normal stop check
    if live_price <= trade.current_stop:
        return True, "stop_hit"
    
    return False, None
```

### 4. Earlier Trailing Stop Activation

```python
# In paper_trading_engine.py _update_trailing_stop()
def _update_trailing_stop(self, trade, live_price, atr, cfg):
    # Current: trail_trigger_pct = 0.05 (5%)
    # New: trail_trigger_pct = 0.03 (3%)
    trail_trigger = cfg.get("trail_trigger_pct", 0.03)  # Changed default
    
    pnl_pct = (live_price - trade.entry_price) / trade.entry_price
    
    if pnl_pct >= trail_trigger:
        # Activate trailing stop
        trail_mult = cfg.get("trail_atr_mult", 1.8)  # Changed from 2.0
        new_stop = live_price - (atr * trail_mult)
        
        if new_stop > trade.current_stop:
            trade.current_stop = new_stop
            return True
    
    return False
```

---

## Part 15: Measurement Framework

### Daily Metrics

| Metric | Query | Target | Alert |
|--------|-------|--------|-------|
| Win Rate (7d) | paper_trades | > 35% | < 25% |
| Risk-Off Entries | paper_trades | 0 | > 0 |
| Score 5-6 Entries | paper_trades | 0 | > 0 |
| Avg Hold (winners) | paper_trades | > 8 days | < 5 days |
| Trailing Stop % | exit_reason | > 15% | < 5% |

### Weekly Reports

1. Win rate by style (GROWTH vs SWING)
2. Win rate by entry score
3. Win rate by regime at entry
4. Exit reason distribution
5. Hold duration distribution

### Config Tuning Schedule

| Parameter | Tune Frequency | Method |
|-----------|----------------|--------|
| min_rr_ratio | Monthly | Analyze R:R band performance |
| trail_trigger_pct | Monthly | Analyze trailing stop activation rate |
| blocked_entry_scores | Quarterly | Analyze entry score performance |
| regime multipliers | Monthly | Analyze regime performance |

---

## Part 16: Implementation Priority

| Priority | Task | Effort | Impact |
|----------|------|--------|--------|
| **P0** | Hard block risk_off entries | 30 min | Prevents -$10K losses |
| **P0** | Block entry scores 5-6 | 30 min | Prevents -$12K losses |
| **P1** | Increase min_rr_ratio to 2.5 | Config | +$4K improvement |
| **P1** | Earlier trailing (3% vs 5%) | Config | More winners captured |
| **P1** | Increase choppy size mult to 1.0 | Config | Capture best regime |
| **P2** | Add min_hold_before_stop | 2 hours | Reduce early stops |
| **P2** | Tighter drawdown limit (15%) | Config | Better risk control |
| **P3** | Redesign entry score formula | 1 week | Fix non-monotonicity |

---

## Appendix A: SQL Queries for Monitoring

### Daily Win Rate Check
```sql
SELECT 
    trading_style,
    COUNT(*) as trades,
    COUNT(*) FILTER (WHERE pnl > 0) as wins,
    ROUND(COUNT(*) FILTER (WHERE pnl > 0) * 100.0 / NULLIF(COUNT(*), 0), 2) as win_rate,
    ROUND(SUM(pnl)::numeric, 2) as total_pnl
FROM paper_trades
WHERE exit_time IS NOT NULL
AND exit_time > NOW() - INTERVAL '7 days'
GROUP BY trading_style;
```

### Risk-Off Leakage Check
```sql
SELECT COUNT(*) as risk_off_entries
FROM paper_trades
WHERE market_regime_at_entry = 'risk_off'
AND entry_time > NOW() - INTERVAL '7 days';
```

### Entry Score Performance
```sql
SELECT 
    entry_score,
    COUNT(*) as trades,
    ROUND(COUNT(*) FILTER (WHERE pnl > 0) * 100.0 / NULLIF(COUNT(*), 0), 2) as win_rate,
    ROUND(SUM(pnl)::numeric, 2) as total_pnl
FROM paper_trades
WHERE exit_time IS NOT NULL
AND exit_time > NOW() - INTERVAL '30 days'
GROUP BY entry_score
ORDER BY entry_score;
```

### Trailing Stop Activation Rate
```sql
SELECT 
    exit_reason,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) as pct
FROM paper_trades
WHERE exit_time IS NOT NULL
AND exit_time > NOW() - INTERVAL '30 days'
GROUP BY exit_reason
ORDER BY count DESC;
```

---

**Verified by:** Production database queries on 2025-08-22  
**Confidence:** HIGH - all findings based on actual trade outcomes  
**Next Review:** 2025-08-29

---

*End of Paper Trading Audit*

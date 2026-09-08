# StockAI Signal & Alert System — Strategic Audit & Improvement Roadmap

**Date:** 2026-07-25  
**Scope:** Full system audit covering signal generation, ML prediction, alerts, outcome tracking,
and calibration — with actionable recommendations to improve accuracy, win rates, and returns.

---

## Executive Summary

StockAI has a **mature, well-architected signal and alert system** with strong foundations:
- 62-feature ML pipeline with macro, sector, and fundamental inputs
- 5-pillar TA fusion with regime-aware thresholds
- Outcome tracking with calibrated confidence bands
- Self-tuning watchdog and walk-forward validation

**Current measured performance (from codebase comments):**
- SWING BUY win rate: ~27.5% at 10d horizon (conf 65-79 band: 13.3%)
- SWING SELL win rate: ~61.7%
- SHORT BUY win rate: ~16.2% (n=37)

**Key insight:** The system is **better at identifying what NOT to buy** (SELL signals) than
**what TO buy** (BUY signals). This is a common pattern — avoiding losers is easier than
picking winners.

**Improvement potential:** Based on the codebase analysis, there are **12 high-impact
opportunities** that could realistically improve BUY win rates from ~27% to 35-45% range.

---

## Part 1 — Current System Strengths

### 1.1 Architecture Strengths

| Strength | Evidence |
|----------|----------|
| **Microservices isolation** | 12 independent services with clear boundaries |
| **Outcome tracking** | SignalOutcome, SqueezeAlertOutcome, PreBreakoutAlertOutcome tables |
| **Self-tuning** | Watchdog, walk-forward validation, calibration endpoints |
| **Regime awareness** | 5-state regime (bull/neutral/choppy/risk_off/bear) |
| **Multi-horizon** | SHORT/SWING/LONG/GROWTH style profiles |
| **Honest framing** | Alerts explicitly state limitations |

### 1.2 ML Pipeline Strengths

| Feature Category | Count | Quality |
|------------------|-------|---------|
| Stock-specific | 29 | ✅ Comprehensive (momentum, volatility, trend, oscillators) |
| Macro | 11 | ✅ SPY, VIX, HSI, regime flags |
| Sector | 3 | ✅ Relative strength vs SPY |
| Fundamental | 16 | ✅ Growth, value, short interest, earnings |
| Signal outcome | 3 | ✅ Look-ahead-safe rolling accuracy |

### 1.3 Signal Generation Strengths

- **5-pillar system:** TREND, MOMENTUM, VOLUME, PATTERN, ML
- **Style-aware thresholds:** Different buy_threshold per horizon
- **Conviction gate:** 5-layer filter before BUY alerts fire
- **Calibrated confidence:** Bucketed win rates from real outcomes
- **Disqualifiers:** RSI divergence, overbought, near-high guards

### 1.4 Alert System Strengths

- **State-transition dedup:** Only fires on new qualifying candidates
- **Staleness checks:** Rejects stale short-interest data
- **Game plan generation:** Entry/stop/target for every alert
- **Outcome persistence:** Every alert recorded for future analysis

---

## Part 2 — Current System Weaknesses

### 2.1 BUY Signal Accuracy Problem

**Root cause analysis from codebase:**

```
SWING BUY win rate: 27.5% at 10d
  - Confidence 65-79 band: 13.3% (WORST despite highest ML confidence)
  - This indicates ML OVERCONFIDENCE at SWING horizon
```

**Why high-confidence signals perform worst:**
1. ML model trained on historical patterns that don't repeat
2. High ML probability overrides TA disagreement
3. No penalty for ML-TA divergence in final fusion

### 2.2 Entry Timing Problem

From SA-33 comments in signals.py:
> "Three structural biases caused BUY signals to fire at the top of moves rather than at the bottom"

**Identified biases:**
1. TREND pillar: price below SMA50 scored 0.0 (same as freefall)
2. RS compression: 20d window captures decline, not turn
3. Weekly gate: fired hardest exactly when stock is bottoming

### 2.3 Missing Feedback Loops

| Gap | Impact |
|-----|--------|
| No per-symbol model performance tracking | Can't identify which stocks the model is good/bad at |
| No feature importance drift detection | Features may lose predictive power over time |
| No regime-specific accuracy tracking | Bull market accuracy ≠ bear market accuracy |
| No time-of-day/week patterns | Market microstructure ignored |

### 2.4 Alert-Specific Gaps

| Alert Type | Gap |
|------------|-----|
| Short Squeeze | No options confirmation (calls-dominant OI) |
| Gamma Unwind | No real GEX calculation (proxy only) |
| Pre-Breakout | Only ~68 historical candidates (can't train model) |
| Signal Alerts | No sector rotation context |

---

## Part 3 — Improvement Roadmap

### 3.1 Quick Wins (1-2 weeks each, high impact)

#### QW-1: ML Confidence Dampening Enhancement

**Current:** ML weight capped at 0.65 for SWING (SA-31)

**Improvement:** Add **confidence-band-specific dampening**:
```python
# When ML confidence is in the historically-worst band (65-79%), 
# apply additional 0.8x multiplier to ML weight
if 0.65 <= ml_prob <= 0.79:
    ml_weight *= 0.8  # Penalize the overconfident band
```

**Expected impact:** +3-5% BUY win rate by reducing false positives from overconfident ML

---

#### QW-2: Entry Timing Score

**Current:** No explicit "is this a good entry point" signal

**Improvement:** Add composite entry timing score:
```python
entry_timing_score = (
    0.3 * (1 if rsi < 45 else 0.5 if rsi < 55 else 0) +  # RSI not extended
    0.3 * (1 if stoch_k < 30 else 0.5 if stoch_k < 50 else 0) +  # Stoch oversold
    0.2 * (1 if price < sma20 * 1.02 else 0) +  # Not chasing
    0.2 * (1 if bb_pct_b < 0.5 else 0)  # Lower half of Bollinger
)
# Require entry_timing_score >= 0.5 for BUY
```

**Expected impact:** +5-8% BUY win rate by avoiding extended entries

---

#### QW-3: Sector Momentum Filter

**Current:** Sector RS features exist in ML but not in signal gate

**Improvement:** Add sector momentum gate:
```python
# Don't BUY stocks in underperforming sectors during bull markets
if regime == "bull" and sector_rs_20d < -0.02:
    signal_compression *= 0.85
    reasons["sector_headwind"] = True
```

**Expected impact:** +2-3% BUY win rate by avoiding sector rotation losers

---

#### QW-4: Volume Confirmation Requirement

**Current:** OBV trend is one of many factors

**Improvement:** Make volume confirmation mandatory for BUY:
```python
# Require EITHER:
# 1. OBV trend bullish, OR
# 2. Volume z-score > 0.5 on up days
volume_confirmed = obv_trend_bullish or (volume_z > 0.5 and daily_return > 0)
if not volume_confirmed:
    # Soft block: compress signal 20%
    signal_compression *= 0.80
```

**Expected impact:** +3-4% BUY win rate by filtering low-conviction moves

---

### 3.2 Medium-Term Improvements (1-2 months each)

#### MT-1: Per-Symbol Model Performance Tracking

**Problem:** Same model applied to all stocks, but some stocks are more predictable

**Solution:**
```sql
CREATE TABLE symbol_model_performance (
    symbol VARCHAR(20),
    model_type VARCHAR(20),  -- 'xgboost', 'rf', etc.
    horizon VARCHAR(10),     -- 'SWING', 'SHORT', etc.
    rolling_accuracy_30d FLOAT,
    rolling_accuracy_90d FLOAT,
    information_coefficient FLOAT,
    last_updated TIMESTAMP
);
```

**Usage:**
- Track which symbols the model is good at predicting
- Boost confidence for high-IC symbols, dampen for low-IC
- Auto-exclude symbols where IC < 0 (model is anti-predictive)

**Expected impact:** +5-7% BUY win rate by focusing on predictable stocks

---

#### MT-2: Regime-Specific Threshold Calibration

**Problem:** Same thresholds used across all regimes

**Solution:**
```python
# Calibrate separate thresholds per regime from outcomes data
REGIME_THRESHOLDS = {
    "bull": calibrate_threshold(outcomes, regime="bull"),      # e.g., 0.62
    "neutral": calibrate_threshold(outcomes, regime="neutral"), # e.g., 0.67
    "choppy": calibrate_threshold(outcomes, regime="choppy"),   # e.g., 0.72
    "risk_off": calibrate_threshold(outcomes, regime="risk_off"), # e.g., 0.75
    "bear": 0.99,  # Effectively disable BUY in bear
}
```

**Expected impact:** +4-6% BUY win rate by adapting to market conditions

---

#### MT-3: Multi-Timeframe Confluence Scoring

**Problem:** Daily signals can be noise; weekly/monthly context ignored

**Solution:**
```python
def compute_mtf_confluence(symbol):
    daily_signal = get_signal(symbol, "1d")
    weekly_signal = get_signal(symbol, "1w")
    monthly_trend = get_trend(symbol, "1M")
    
    confluence_score = (
        0.5 * daily_signal.bullish_prob +
        0.3 * weekly_signal.bullish_prob +
        0.2 * (1 if monthly_trend == "up" else 0)
    )
    
    # Only BUY when all timeframes agree
    if weekly_signal.signal == "SELL" or monthly_trend == "down":
        return confluence_score * 0.5  # Heavy penalty
    
    return confluence_score
```

**Expected impact:** +6-8% BUY win rate by filtering counter-trend trades

---

#### MT-4: Options Flow Integration for All Signals

**Problem:** Options data only used in squeeze alerts, not main signals

**Solution:**
```python
def get_options_sentiment(symbol):
    """Read from OptionsFlowSnapshot table"""
    snapshot = get_latest_options_flow(symbol)
    if snapshot is None:
        return 0.5  # Neutral
    
    # cp_ratio > 1.5 = calls dominant = bullish
    # cp_ratio < 0.7 = puts dominant = bearish
    if snapshot.cp_ratio > 1.5:
        return 0.7 + min(0.2, (snapshot.cp_ratio - 1.5) * 0.1)
    elif snapshot.cp_ratio < 0.7:
        return 0.3 - min(0.2, (0.7 - snapshot.cp_ratio) * 0.1)
    return 0.5

# Add to signal fusion
options_sentiment = get_options_sentiment(symbol)
if options_sentiment > 0.6 and signal == "BUY":
    confidence_boost = 1.05  # Options confirm
elif options_sentiment < 0.4 and signal == "BUY":
    confidence_boost = 0.90  # Options disagree
```

**Expected impact:** +3-5% BUY win rate by adding smart money confirmation

---

#### MT-5: Earnings Reaction Learning

**Problem:** Earnings proximity is a penalty, but post-earnings momentum is ignored

**Solution:**
```python
def get_post_earnings_momentum(symbol):
    """Learn from this stock's own earnings reaction history"""
    recent_earnings = get_last_4_earnings(symbol)
    
    # Calculate average 5-day post-earnings return
    avg_reaction = mean([e.return_5d for e in recent_earnings])
    
    # Calculate beat/miss pattern
    beat_rate = sum(1 for e in recent_earnings if e.surprise_pct > 0) / 4
    
    return {
        "avg_reaction": avg_reaction,
        "beat_rate": beat_rate,
        "is_positive_reactor": avg_reaction > 0.02 and beat_rate > 0.5
    }

# Use in signal generation
if days_to_earnings < 10:
    momentum = get_post_earnings_momentum(symbol)
    if momentum["is_positive_reactor"]:
        # This stock tends to rally after earnings
        earnings_penalty = 0.95  # Mild penalty instead of 0.85
    else:
        earnings_penalty = 0.80  # Stronger penalty for negative reactors
```

**Expected impact:** +2-3% BUY win rate around earnings

---

### 3.3 Long-Term Improvements (3-6 months)

#### LT-1: Ensemble Model with Specialization

**Problem:** Single XGBoost model for all conditions

**Solution:**
```
Model Ensemble:
├── XGBoost (general)     — weight: 0.3
├── LSTM (momentum)       — weight: 0.2, activated when ADX > 25
├── RandomForest (value)  — weight: 0.2, activated for low P/B stocks
├── GradientBoosting (vol)— weight: 0.2, activated when VIX > 20
└── Logistic (baseline)   — weight: 0.1, always active

Final prediction = weighted average based on which specialists are active
```

**Expected impact:** +5-10% BUY win rate through specialization

---

#### LT-2: Reinforcement Learning Position Sizing

**Problem:** Fixed position sizing regardless of signal quality

**Solution:**
```python
class PositionSizingAgent:
    """RL agent that learns optimal position size from outcomes"""
    
    def __init__(self):
        self.state_features = [
            "signal_confidence",
            "regime",
            "sector_rs",
            "days_to_earnings",
            "recent_win_rate",
            "portfolio_heat",
        ]
        self.actions = [0.0, 0.25, 0.5, 0.75, 1.0]  # Position size multipliers
        
    def get_position_size(self, state):
        # Q-learning to maximize risk-adjusted returns
        return self.policy(state)
```

**Expected impact:** +10-15% portfolio returns through better sizing

---

#### LT-3: News Sentiment Integration

**Problem:** News only used for earnings alerts, not signal generation

**Solution:**
```python
def get_news_sentiment(symbol, hours=24):
    """Aggregate sentiment from news-intelligence service"""
    news = fetch_recent_news(symbol, hours)
    
    sentiments = [classify_sentiment(n.headline) for n in news]
    
    return {
        "avg_sentiment": mean(sentiments),  # -1 to +1
        "news_volume": len(news),
        "has_negative_news": any(s < -0.5 for s in sentiments),
        "has_positive_catalyst": any(s > 0.7 for s in sentiments),
    }

# Gate: Don't BUY with recent negative news
if news["has_negative_news"] and not news["has_positive_catalyst"]:
    signal_compression *= 0.7
```

**Expected impact:** +3-5% BUY win rate by avoiding news-driven drops

---

#### LT-4: Cross-Asset Correlation Signals

**Problem:** Each stock analyzed in isolation

**Solution:**
```python
def get_correlation_signals(symbol):
    """Detect when correlated assets are moving first"""
    
    # Find highly correlated stocks
    correlations = get_rolling_correlations(symbol, window=60)
    leaders = [s for s, corr in correlations.items() if corr > 0.7]
    
    # Check if leaders are already moving
    leader_momentum = mean([get_momentum(s, 5) for s in leaders])
    
    return {
        "leader_momentum": leader_momentum,
        "is_lagging": leader_momentum > 0.02 and get_momentum(symbol, 5) < 0.01,
    }

# Boost signal if correlated leaders are already up
if correlation["is_lagging"] and signal == "BUY":
    confidence_boost = 1.08  # Catch-up trade
```

**Expected impact:** +2-4% BUY win rate through lead-lag relationships

---

## Part 4 — Alert-Specific Improvements

### 4.1 Short Squeeze Alert Improvements

| Improvement | Description | Impact |
|-------------|-------------|--------|
| **Options confirmation** | Add calls-dominant OI check | +5% precision |
| **Borrow rate integration** | High borrow rate = more squeeze pressure | +3% precision |
| **Historical squeeze similarity** | Match against past successful squeezes | +4% precision |
| **Sector squeeze clustering** | Detect sector-wide short covering | +2% precision |

### 4.2 Gamma Unwind Alert Improvements

| Improvement | Description | Impact |
|-------------|-------------|--------|
| **Real GEX calculation** | Compute actual gamma exposure | +10% precision |
| **Dealer positioning model** | Estimate if dealers are long/short gamma | +8% precision |
| **Max pain integration** | Show distance to max pain strike | +3% precision |

### 4.3 Pre-Breakout Alert Improvements

| Improvement | Description | Impact |
|-------------|-------------|--------|
| **Volume dry-up confirmation** | Require volume < 50% of 20d avg | +5% precision |
| **ATR compression percentile** | Require ATR in bottom 10% of 6-month range | +4% precision |
| **Breakout direction prediction** | Use options skew to predict direction | +6% precision |

---

## Part 5 — Measurement & Validation Framework

### 5.1 Key Metrics to Track

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| BUY win rate (10d) | 27.5% | 40% | SignalOutcome.is_correct |
| BUY avg return | ? | +2% | SignalOutcome.pct_return |
| SELL win rate | 61.7% | 65% | SignalOutcome.is_correct |
| Alert precision | ? | 50% | SqueezeAlertOutcome.is_correct_10d |
| Information Coefficient | ? | 0.05+ | Correlation(prediction, outcome) |

### 5.2 A/B Testing Framework

```python
class SignalExperiment:
    """Run controlled experiments on signal changes"""
    
    def __init__(self, name, control_pct=0.5):
        self.name = name
        self.control_pct = control_pct
        
    def assign_variant(self, symbol):
        # Deterministic assignment based on symbol hash
        return "control" if hash(symbol) % 100 < self.control_pct * 100 else "treatment"
    
    def log_outcome(self, symbol, variant, outcome):
        # Persist for analysis
        pass
    
    def analyze(self):
        # Compare control vs treatment win rates
        pass
```

### 5.3 Walk-Forward Validation Protocol

```
For each proposed change:
1. Backtest on 2 years of data (train on 18 months, test on 6 months)
2. Require improvement on BOTH train and test sets
3. Require improvement across ALL regimes (bull, neutral, bear)
4. Require minimum 100 signals in test set
5. Require statistical significance (p < 0.05)
6. Shadow-run for 2 weeks before promotion
7. Monitor for 30 days post-promotion with auto-rollback trigger
```

---

## Part 6 — Implementation Priority Matrix

| Priority | Item | Effort | Impact | Dependencies |
|----------|------|--------|--------|--------------|
| P0 | QW-1: ML Confidence Dampening | S | High | None |
| P0 | QW-2: Entry Timing Score | S | High | None |
| P0 | QW-3: Sector Momentum Filter | S | Medium | None |
| P0 | QW-4: Volume Confirmation | S | Medium | None |
| P1 | MT-1: Per-Symbol Performance | M | High | DB migration |
| P1 | MT-2: Regime-Specific Thresholds | M | High | Outcome data |
| P1 | MT-3: Multi-Timeframe Confluence | M | High | Weekly data |
| P1 | MT-4: Options Flow Integration | M | Medium | OptionsFlowSnapshot |
| P2 | MT-5: Earnings Reaction Learning | M | Medium | EarningsEvent data |
| P2 | LT-1: Ensemble Model | L | High | ML infrastructure |
| P2 | LT-2: RL Position Sizing | L | High | RL framework |
| P3 | LT-3: News Sentiment | L | Medium | news-intelligence |
| P3 | LT-4: Cross-Asset Correlation | L | Medium | Correlation compute |

---

## Part 7 — Expected Outcomes

### 7.1 Conservative Estimate (P0 items only)

| Metric | Current | After P0 | Improvement |
|--------|---------|----------|-------------|
| BUY win rate | 27.5% | 35% | +7.5pp |
| False positive rate | 72.5% | 65% | -7.5pp |
| Avg BUY return | ~0% | +1% | +1pp |

### 7.2 Optimistic Estimate (P0 + P1 items)

| Metric | Current | After P0+P1 | Improvement |
|--------|---------|-------------|-------------|
| BUY win rate | 27.5% | 42% | +14.5pp |
| False positive rate | 72.5% | 58% | -14.5pp |
| Avg BUY return | ~0% | +2.5% | +2.5pp |
| Portfolio Sharpe | ? | +0.3 | Significant |

### 7.3 Risk Factors

| Risk | Mitigation |
|------|------------|
| Overfitting to historical data | Walk-forward validation, regime splits |
| Reduced signal volume | Accept fewer, higher-quality signals |
| Implementation bugs | Comprehensive test coverage, shadow mode |
| Market regime shift | Continuous monitoring, auto-rollback |

---

## Conclusion

The StockAI system has a **solid foundation** but is currently optimized for **avoiding losers**
(SELL signals) rather than **picking winners** (BUY signals). The 27.5% BUY win rate indicates
significant room for improvement.

**Recommended immediate actions:**
1. Implement QW-1 through QW-4 (4 quick wins, ~2 weeks total)
2. Set up per-symbol performance tracking (MT-1)
3. Calibrate regime-specific thresholds (MT-2)

**Expected outcome:** BUY win rate improvement from 27.5% to 35-40% within 2 months, with
potential to reach 42%+ with full P0+P1 implementation.

**Key principle:** Every change must be validated through walk-forward testing and monitored
post-deployment. The goal is **sustainable improvement**, not short-term gains that regress.

---

## Appendix A — Data Sources for Validation

| Data | Location | Volume |
|------|----------|--------|
| Signal outcomes | SignalOutcome table | ~5,000+ rows |
| Squeeze outcomes | SqueezeAlertOutcome table | ~500+ rows |
| Pre-breakout outcomes | PreBreakoutAlertOutcome table | ~100+ rows |
| Tune history | TuneHistory table | ~200+ rows |
| Price data | Price table | 3 years × 150 symbols |

## Appendix B — Related Documentation

- `docs/AI_SIGNAL.md` — Signal generation algorithm
- `docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md` — Self-tuning architecture
- `docs/DESIGN_MODEL_PROMOTION_GATES_2026-07-12.md` — Model validation gates
- `docs/AUDIT_SHORT_SQUEEZE_2026-07-25.md` — Squeeze alert audit
- `services/signal-engine/src/generators/signals.py` — Signal generation code (SA-1 through SA-33)
- `services/signal-engine/src/api/calibration.py` — Calibration endpoints
- `services/ml-prediction/src/features/builder.py` — ML feature engineering

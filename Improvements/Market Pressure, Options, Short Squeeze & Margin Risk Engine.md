# Claude Code Prompt — Market Pressure, Options, Short Squeeze & Margin Risk Engine

You are modifying an existing Stock Trading Intelligence Platform.

The platform already has:
- yfinance market data
- FMP fundamentals integration/planned integration
- Unusual Whales API integration/planned integration
- Data provider abstraction
- Feature engineering
- Technical indicators
- ML prediction
- Walk-forward validation
- Backtesting
- AI Buy/Hold/Sell/Watch decision engine
- Trade journal
- Risk management

## CRITICAL RULE

DO NOT rebuild the existing architecture.

First inspect the repository and identify the existing:
- Data provider interfaces
- yfinance adapter
- FMP adapter
- Unusual Whales adapter
- Feature Engine
- ML Engine
- Decision Engine
- Risk Engine
- Backtesting
- Walk-forward validation
- Database models
- API endpoints
- UI components

Reuse existing components.

Implement the new functionality as an extension of the current architecture.

---

# OBJECTIVE

Add a new:

## MARKET PRESSURE INTELLIGENCE ENGINE

It must analyze three DIFFERENT mechanisms:

1. SHORT SQUEEZE
2. OPTIONS / GAMMA / DEALER HEDGING
3. MARGIN / LIQUIDATION RISK

Do NOT treat these as the same thing.

The final system should produce:

```text
Short Squeeze Score       0-100
Options Pressure Score    0-100
Margin Risk Score         0-100
Combined Market Pressure  0-100
```

The scores must remain independently visible.

---

# ARCHITECTURE

Implement:

                    MARKET PRESSURE ENGINE
                             |
              ┌──────────────┼──────────────┐
              ↓              ↓              ↓
        SHORT SQUEEZE     OPTIONS        MARGIN RISK
           ENGINE          ENGINE           ENGINE
              |              |              |
              ↓              ↓              ↓
        Short Covering    Gamma/GEX       Liquidation
        Probability       Dealer Hedge    Risk
              |              |              |
              └──────────────┼──────────────┘
                             ↓
                    MARKET PRESSURE SCORE
                             ↓
                    AI DECISION ENGINE
                             ↓
                   BUY / HOLD / SELL / WATCH

---

# 1. SHORT SQUEEZE ENGINE

Use Unusual Whales data where available.

Potential inputs:

- Short interest
- Short interest percentage
- Short interest change
- Short volume
- Utilization
- Borrow fee/rate
- Shares available
- Days to cover
- Price momentum
- Relative volume
- Volume acceleration
- Options activity
- Options expiration proximity

Do not invent data that Unusual Whales does not provide.

Every feature must have:

- value
- timestamp
- provider
- data quality
- confidence

Calculate:

## Short Squeeze Score

0-100.

Possible weighting:

Short Interest              20%
Borrow/Utilization          20%
Days to Cover               10%
Short Interest Change       10%
Relative Volume             10%
Price Momentum              10%
Options Activity             10%
Expiration Proximity         10%

Make weights configurable.

---

# 2. SHORT COVERING PROBABILITY

Do NOT say:

"Forced covering WILL happen."

Instead calculate:

```text
Short Covering Pressure
```

and optionally:

```text
Estimated Short Covering Probability
```

Only calculate probability if the available historical data supports proper model training.

Otherwise use:

LOW
MEDIUM
HIGH

with confidence.

Example:

```text
Short Squeeze Score: 87
Short Covering Pressure: HIGH
Confidence: 81%

Drivers:
+ High short interest
+ High utilization
+ Rising borrow cost
+ Increasing volume
+ Strong price momentum

Risks:
- No confirmed covering event
- Short-interest data may be delayed
```

---

# 3. OPTIONS INTELLIGENCE ENGINE

Use Unusual Whales as the primary source.

Analyze:

- Call volume
- Put volume
- Call open interest
- Put open interest
- Put/Call volume ratio
- Put/Call OI ratio
- Options volume vs average
- Large trades
- Sweeps
- Premium
- Expiration
- Near-term expiration concentration
- Unusual options activity

Do not assume:

CALL = bullish
PUT = bearish

Attempt to identify:

- opening activity
- closing activity
- spreads
- directional activity

ONLY when the data supports the distinction.

If the API does not provide enough information to determine direction, mark the feature as uncertain.

---

# 4. GAMMA / DEALER HEDGING ENGINE

Implement proper options exposure calculations where the underlying data permits.

Calculate, where possible:

- Gamma exposure
- Net GEX
- Call GEX
- Put GEX
- Gamma concentration
- Gamma flip / zero-gamma level
- Major call strikes
- Major put strikes
- Expiration concentration

Do NOT label the current heuristic OI concentration calculation as "true GEX."

If true GEX cannot be calculated from available data, explicitly distinguish:

```text
OI Concentration
```

from:

```text
Calculated GEX
```

Do not fabricate Greeks.

---

# 5. OPTIONS PRESSURE SCORE

Create:

```text
Options Pressure Score = 0-100
```

Possible inputs:

Options Activity          20%
Unusual Flow              20%
Open Interest             15%
Gamma Exposure             20%
Expiration Concentration  10%
Volume Anomaly             10%
Premium Flow                5%

Make configurable.

Output:

- Score
- Confidence
- Bullish pressure
- Bearish pressure
- Neutral pressure
- Major strikes
- Expiration dates
- Explanation

---

# 6. OPTIONS EXPIRATION ENGINE

Track upcoming expirations.

For each expiration:

- Date
- Days remaining
- Call OI
- Put OI
- Total OI
- Volume
- Major strikes
- Gamma concentration
- Estimated pinning risk where supported

Classify:

NORMAL
ELEVATED
HIGH
EXTREME

Do NOT assume expiration automatically causes volatility.

Determine whether positioning is significant relative to historical norms.

---

# 7. MARGIN / LIQUIDATION RISK ENGINE

IMPORTANT:

Individual broker margin positions are generally NOT directly observable.

Do not pretend that the platform knows exactly when individual investors will receive margin calls.

Instead estimate:

```text
Market-wide Margin/Liquidation Risk
```

using observable proxies.

Potential inputs:

- Stock volatility
- ATR
- Intraday volatility
- Gap risk
- Leverage-sensitive market conditions
- Large price declines
- VIX
- Market drawdown
- Sector drawdown
- ETF flows where available
- Options positioning
- Short positioning
- Broker margin requirement changes IF available
- Portfolio leverage for OUR OWN portfolio

Separate:

## Market Margin Risk

from:

## Portfolio Margin Risk

---

# 8. PORTFOLIO MARGIN RISK

For positions held by our own trading system, calculate actual portfolio-level risk.

Inputs:

- Account equity
- Cash
- Position market value
- Leverage
- Maintenance requirement
- Concentration
- Sector exposure
- Correlation
- Volatility
- Margin utilization

Calculate:

```text
Margin Utilization %
Available Buying Power
Estimated Maintenance Requirement
Estimated Liquidation Distance
Portfolio Leverage
Portfolio Margin Risk Score
```

Example:

```text
Portfolio Equity:        $100,000
Position Value:          $140,000
Leverage:                1.40x
Margin Utilization:      62%
Liquidation Distance:    18%

Margin Risk:             MEDIUM
```

Do not use invented broker rules.

Make broker-specific margin requirements configurable.

---

# 9. MARKET PRESSURE SCORE

Create:

```text
Combined Market Pressure Score
```

This is NOT simply an average.

Use context-aware weighting.

Initial configuration:

Short Squeeze:       35%
Options Pressure:    35%
Margin Risk:         30%

But distinguish:

## Bullish Pressure

and

## Bearish Pressure

For example:

```text
Bullish Pressure: 82
Bearish Pressure: 34
Net Pressure:     +48
```

This is better than a single number.

---

# 10. MARKET PRESSURE STATE

Classify:

VERY_BEARISH
BEARISH
NEUTRAL
BULLISH
VERY_BULLISH

Also classify confidence:

LOW
MEDIUM
HIGH

Example:

```text
Market Pressure

Bullish:       86
Bearish:       27
Net:           +59

State:         VERY BULLISH
Confidence:    HIGH
```

---

# 11. FEATURE ENGINE INTEGRATION

Add the new features to the existing Feature Engine.

Example:

```text
short_interest
short_interest_change
borrow_rate
utilization
days_to_cover
short_volume_ratio

options_call_volume
options_put_volume
options_call_oi
options_put_oi
options_volume_ratio
put_call_ratio
unusual_flow_score
sweep_score
premium_flow_score

gex
call_gex
put_gex
gamma_flip
gamma_concentration
expiration_pressure

market_margin_risk
portfolio_margin_risk
margin_utilization
```

All features must be point-in-time.

---

# 12. DATA LEAKAGE PREVENTION

This is mandatory.

Never use future information.

For every feature:

```text
feature_timestamp <= decision_timestamp
```

Examples of prohibited leakage:

- Future options OI
- Future short-interest reports
- Future news
- Future price
- Future expiration information that wasn't known at decision time
- Revised financial data unavailable at decision time

Document data availability delays.

Short interest may be reported with a delay.

Do not backtest it as if it were real-time information.

---

# 13. ML INTEGRATION

Add the new features to the existing ML pipeline.

Do NOT automatically increase their weights.

Run controlled experiments:

### Model A

Existing features.

### Model B

Existing features + Short Squeeze.

### Model C

Existing features + Options.

### Model D

Existing features + Short + Options + Margin.

Compare:

- Profit Factor
- Sharpe
- Sortino
- Maximum Drawdown
- CAGR
- Out-of-Sample Return
- Win Rate

Primary objective:

## PROFIT FACTOR

Second:

## RISK-ADJUSTED RETURN

Third:

## MAXIMUM DRAWDOWN

Fourth:

## OUT-OF-SAMPLE PERFORMANCE

Win rate is secondary.

---

# 14. FEATURE ABLATION TESTING

Implement feature-group ablation.

Test:

```text
BASELINE

BASELINE + OPTIONS

BASELINE + SHORT

BASELINE + MARGIN

BASELINE + OPTIONS + SHORT

BASELINE + OPTIONS + MARGIN

BASELINE + SHORT + MARGIN

BASELINE + ALL
```

Determine whether each group adds statistically/economically useful predictive value.

Do not keep features simply because they improve in-sample results.

---

# 15. WALK-FORWARD VALIDATION

Reuse the existing walk-forward validation system.

Do NOT create a competing validation framework.

Use:

TRAIN
↓
VALIDATE
↓
TEST
↓
MOVE FORWARD
↓
RETRAIN

All new features must participate in the existing point-in-time/walk-forward process.

---

# 16. DECISION ENGINE

Integrate Market Pressure into the existing AI Decision Engine.

Do not let Market Pressure override everything.

Example:

Technical Score       85
Fundamental Score     90
Momentum Score        82
Options Score         91
Squeeze Score         88
Market Pressure       86
ML Probability        84%

Final:

BUY

But:

```text
Technical:            42
Fundamental:          75
Options Pressure:     91
Squeeze Score:        94
```

must NOT automatically produce BUY.

The system must understand that squeeze setups can be extremely risky.

---

# 17. SIGNAL CHANGE

When market pressure changes materially, trigger a signal reevaluation.

Example:

Previous:

BUY
Confidence: 88

Current:

HOLD
Confidence: 69

Log:

```text
BUY → HOLD

Reasons:
- Options pressure deteriorated
- Gamma exposure changed
- Short-covering pressure declined
- Technical trend remains positive
```

Another example:

```text
HOLD → SELL

Reasons:
- Price broke support
- Options pressure turned bearish
- Market regime deteriorated
- Expected return became negative
```

Do not trade on tiny score changes.

Use configurable hysteresis.

---

# 18. RISK MANAGEMENT

Market Pressure must affect risk sizing.

Example:

High squeeze score does NOT mean:

"Increase position size."

Instead:

```text
High upside pressure
+
High volatility
+
High squeeze risk
=
Potentially REDUCE position size
```

Use volatility-adjusted position sizing.

Consider:

- ATR
- Expected volatility
- Stop distance
- Portfolio exposure
- Correlation
- Market regime
- Margin utilization

---

# 19. UI

Add a new:

# Market Pressure Panel

Example:

```text
MARKET PRESSURE

Bullish Pressure       84
Bearish Pressure       29
Net Pressure           +55

Status                 BULLISH
Confidence             HIGH
```

Then:

```text
SHORT SQUEEZE

Score                  88
Short Interest         HIGH
Borrow Cost            HIGH
Utilization            97%
Days to Cover          4.8
Cover Pressure         HIGH
```

```text
OPTIONS

Score                  91
Call/Put Ratio         2.8
Unusual Flow            HIGH
Sweep Activity          HIGH
GEX                     +$XXXM
Gamma Flip              $XXX
Nearest Expiry          YYYY-MM-DD
```

```text
MARGIN

Market Risk             MEDIUM
Portfolio Risk          LOW
Leverage                 1.2x
Margin Utilization       38%
```

---

# 20. DATA SOURCE TRANSPARENCY

Every score must show its sources.

Example:

```text
Options Pressure: 91

Sources:
✓ Unusual Whales
✓ yfinance fallback
```

If a provider fails:

```text
Options Pressure: N/A

Reason:
Unusual Whales data unavailable

Confidence:
Reduced
```

Never silently substitute missing values.

---

# 21. API

Add or extend:

```text
GET /stocks/{symbol}/market-pressure

GET /stocks/{symbol}/short-squeeze

GET /stocks/{symbol}/options-pressure

GET /stocks/{symbol}/margin-risk

GET /stocks/{symbol}/options-expiration
```

Use existing API conventions.

Return structured JSON.

---

# 22. DATABASE

Reuse existing database patterns.

Add only necessary tables/entities.

Potential entities:

```text
short_pressure_snapshot
options_pressure_snapshot
options_expiration_snapshot
margin_risk_snapshot
market_pressure_snapshot
```

Every snapshot should contain:

- symbol
- timestamp
- provider
- score
- confidence
- feature values
- data quality
- model version if ML-generated

---

# 23. CACHING AND API COST

Unusual Whales API currently costs approximately $125/month on the plan being used.

Treat API calls as a limited resource.

Implement:

- Persistent caching
- Incremental updates
- Request deduplication
- Appropriate TTL
- Batch requests where supported
- Retry with exponential backoff
- Rate-limit handling

Track:

```text
UW API requests
Cache hits
Cache misses
Errors
Latency
Data volume
```

Never repeatedly download the same historical options data.

---

# 24. TESTING

Create tests for:

## Short Engine

- High short interest
- Low short interest
- Increasing borrow cost
- Decreasing borrow cost
- High utilization
- Missing data

## Options Engine

- Call-heavy flow
- Put-heavy flow
- Large sweep
- High OI
- Expiration concentration
- Missing Greeks

## Margin Engine

- High leverage
- Low leverage
- High volatility
- Portfolio concentration
- Margin requirement changes

## Market Pressure

- Bullish
- Bearish
- Conflicting signals
- Missing providers

## ML

Verify:

- No leakage
- Point-in-time correctness
- Walk-forward compatibility
- Feature reproducibility

---

# 25. OBSERVABILITY

Log:

- Provider requests
- Provider failures
- Data quality
- Feature calculations
- Score calculations
- Signal changes
- ML predictions
- Decisions

Make it possible to answer:

"Why did the system issue BUY?"

Example:

```text
BUY decision

Technical:       86
Fundamental:     91
Momentum:        82
Options:         88
Short Squeeze:   79
Market Pressure: 84
ML Probability:  87%

Primary reasons:
1. Strong trend
2. Positive options flow
3. Elevated short-covering pressure
4. Strong fundamentals

Risk:
High volatility
```

---

# 26. IMPORTANT DISCLAIMERS IN THE ENGINE

Never make claims such as:

"Shorts WILL be forced to cover."

"Options expiration WILL cause a squeeze."

"Margin calls WILL cause the stock to fall."

Instead use probabilistic language:

- Elevated pressure
- Increased probability
- Potential covering pressure
- Potential dealer hedging pressure
- Elevated liquidation risk

---

# 27. MODEL PERFORMANCE REQUIREMENT

Do NOT optimize for:

"70% win rate."

The optimization objective is:

1. Profit Factor
2. Sharpe / Sortino
3. Maximum Drawdown
4. Out-of-Sample Performance
5. CAGR
6. Calmar Ratio
7. Win Rate

A lower win-rate strategy is acceptable if it produces substantially better risk-adjusted returns.

Example:

Strategy A:
Win Rate = 70%
Profit Factor = 1.25
Max DD = -28%

Strategy B:
Win Rate = 58%
Profit Factor = 2.10
Max DD = -12%

Prefer Strategy B.

---

# 28. MODEL PROMOTION

Use the existing model promotion gate.

Do NOT promote a new model merely because:

- Backtest return increased
- Win rate increased
- Training accuracy increased

Require robust:

- Out-of-sample performance
- Profit Factor
- Sharpe/Sortino
- Drawdown
- Regime stability
- Statistical robustness

---

# 29. IMPLEMENTATION ORDER

Implement in this order:

PHASE 1
Inspect existing architecture.

PHASE 2
Map existing options/short/margin functionality.

PHASE 3
Implement/extend Unusual Whales adapter.

PHASE 4
Implement Short Squeeze Engine.

PHASE 5
Implement Options Intelligence Engine.

PHASE 6
Implement Options Expiration Engine.

PHASE 7
Implement Market/Portfolio Margin Risk Engine.

PHASE 8
Implement Market Pressure Engine.

PHASE 9
Integrate into Feature Engine.

PHASE 10
Integrate into ML.

PHASE 11
Run feature ablation experiments.

PHASE 12
Integrate into Decision Engine.

PHASE 13
Integrate signal-change logic.

PHASE 14
Add UI.

PHASE 15
Add tests.

PHASE 16
Run walk-forward backtests.

PHASE 17
Compare baseline vs enhanced model.

---

# 30. FIRST RESPONSE REQUIRED FROM CLAUDE

Before modifying code, provide:

1. Existing architecture summary
2. Existing options implementation
3. Existing short-interest implementation
4. Existing margin/risk implementation
5. Existing ML feature pipeline
6. Existing walk-forward pipeline
7. Existing decision engine
8. Existing database schema
9. Existing API structure
10. Files that will be modified
11. New files that will be created
12. Database migrations required
13. Potential conflicts
14. Data-provider limitations
15. Recommended implementation sequence

Then WAIT for confirmation before making large architectural changes.

---

# SUCCESS CRITERIA

The implementation is successful when:

✓ yfinance continues working

✓ FMP remains isolated behind its provider interface

✓ Unusual Whales is isolated behind its provider interface

✓ Short Squeeze is independent from Options

✓ Options/Gamma is independent from Margin

✓ Market Margin Risk is separated from Portfolio Margin Risk

✓ All features are point-in-time safe

✓ No future-data leakage exists

✓ Scores are reproducible

✓ Missing provider data is handled safely

✓ API costs are controlled

✓ Existing walk-forward validation is reused

✓ Existing backtesting is reused

✓ Feature ablation can determine whether the new data actually helps

✓ AI decisions explain changes in market pressure

✓ Model optimization prioritizes Profit Factor, risk-adjusted return, maximum drawdown, and out-of-sample performance

✓ Win rate remains a secondary metric

✓ No automatic live trading is enabled by this implementation

The final result should be a production-quality Market Pressure Intelligence layer integrated into the existing Stock Trading Intelligence Platform, not a standalone prototype.
# MASTER IMPLEMENTATION PROMPT
# AI Market Intelligence & Adaptive Trading System

You are the lead quantitative engineer, ML engineer, trading-system architect, and senior software engineer responsible for upgrading my existing AI Stock Trading Platform.

## 0. PRIMARY OBJECTIVE

Transform the existing platform from a primarily stock-analysis / signal-generation application into a **proactive AI Market Intelligence & Adaptive Trading System**.

The system should continuously analyze:

- Market conditions
- Macro economics
- News
- SEC filings
- Earnings
- Analyst/estimate changes
- Individual stocks
- Sectors
- Technical indicators
- Volume / order-flow proxies
- Options flow
- Options open interest
- IV / IV Rank
- Greeks
- Dealer positioning / GEX
- Options expiration
- Short interest / short pressure
- Short squeeze conditions
- Margin / leverage / liquidation risk
- Institutional activity
- Insider activity
- Fundamentals
- Valuation
- Competitive advantage / quantitative moat
- Market regime
- Sector regime
- Stock regime
- Portfolio exposure
- Existing positions
- Correlations
- Volatility
- Liquidity

Then:

1. Detect what has changed.
2. Determine whether the change is significant.
3. Determine how the change affects the investment/trading thesis.
4. Predict probable future scenarios across multiple time horizons.
5. Calculate expected return, risk, volatility, and drawdown.
6. Determine the best trading/investment vehicle.
7. Determine position size.
8. Determine entry, stop, target, and invalidation conditions.
9. Continuously monitor the position and thesis.
10. React when conditions change.
11. Recommend BUY / SELL / HOLD / REDUCE / EXIT / HEDGE / OPTIONS / WAIT / NO TRADE.
12. Learn from actual outcomes.
13. Validate all improvements using proper out-of-sample testing.

The system should behave more like an **expert quantitative trading desk** than a static stock screener.

IMPORTANT:

Do NOT promise perfect prediction.

The objective is:

> Maximize repeatable positive expected value while controlling risk, drawdown, tail risk, and model uncertainty.

Optimize for:

1. Profit Factor
2. Risk-adjusted return
3. Sharpe / Sortino
4. Maximum Drawdown
5. Out-of-Sample performance
6. CAGR / total return
7. Calmar ratio
8. Stability across market regimes
9. Win rate

Do NOT optimize primarily for win rate.

A strategy with 58% win rate, 2.1 Profit Factor, and 10% max drawdown may be superior to a strategy with 72% win rate, 1.2 Profit Factor, and 30% drawdown.

---

# 1. FIRST: INSPECT THE EXISTING PLATFORM

Before changing code:

1. Thoroughly inspect the entire repository.
2. Understand the existing architecture.
3. Identify:
   - backend
   - frontend
   - database
   - APIs
   - data providers
   - feature engine
   - signal engine
   - K-Score
   - ML/training
   - walk-forward validation
   - backtesting
   - model promotion
   - news intelligence
   - options implementation
   - paper trading
   - trade journal
   - portfolio management
   - risk management
   - scheduling/background jobs
   - caching
   - alerting
   - existing dashboards
4. Identify reusable components.
5. Identify duplicate functionality.
6. Identify technical debt.
7. Identify current data-quality weaknesses.
8. Identify current sources of look-ahead/data leakage.
9. Identify missing abstractions.

DO NOT rebuild functionality that already exists.

Extend the current architecture.

Preserve existing APIs and UI behavior unless changes are required.

Before implementation, produce:

`/docs/YYYY-MM-DD/AI_MARKET_INTELLIGENCE_ARCHITECTURE_AUDIT.md`

containing:

- current architecture
- reusable components
- existing data providers
- current signal pipeline
- current ML pipeline
- current backtesting pipeline
- current options implementation
- current paper trading implementation
- identified gaps
- proposed integration points
- implementation plan

Use the actual current date for `YYYY-MM-DD`.

---

# 2. DATA PROVIDER ARCHITECTURE

Maintain the existing provider abstraction.

Do NOT allow the ML/decision engine to depend directly on vendor-specific APIs.

Use normalized canonical models.

Preferred providers:

## yfinance

Use as:

- price/history
- OHLCV
- baseline market data
- basic fundamentals
- fallback data source

## FMP

Use as primary source where available for:

- financial statements
- income statement
- balance sheet
- cash flow
- earnings
- EPS
- revenue
- estimates
- valuation
- ratios
- growth
- analyst estimates
- company fundamentals

## Unusual Whales

Use as primary source where available for:

- options flow
- options sweeps
- options trades
- open interest
- options volume
- unusual activity
- put/call activity
- options positioning
- short-related intelligence
- squeeze-related information

Do not add another provider simply because it exists.

Every provider must support:

- caching
- rate limiting
- retries
- timeout handling
- provider health
- data freshness
- source attribution
- error handling
- fallback
- normalized schemas

---

# 3. CANONICAL DATA QUALITY LAYER

Implement or improve a formal Data Quality framework.

Every data object should have metadata such as:

```text
source
retrieved_at
event_time
effective_time
available_at
data_timestamp
freshness
quality_status
confidence
```

Quality statuses should be typed, for example:

```text
FRESH
STALE
PARTIAL
MISSING
INVALID
ESTIMATED
DEGRADED
```

Models must be able to consume data-quality information.

Never silently treat stale or missing data as current.

Critical requirement:

## POINT-IN-TIME CORRECTNESS

Historical backtests must only use information that was actually available at the time of the simulated decision.

Prevent:

- look-ahead bias
- survivorship bias
- future earnings data leakage
- revised fundamental data leakage
- future analyst estimate leakage
- future options information leakage
- future short-interest information leakage

---

# 4. EVENT & CHANGE DETECTION ENGINE

Create a central:

`EventChangeDetectionEngine`

Its job is not merely to collect data.

Its job is to determine:

> WHAT CHANGED?

Examples:

```text
Price +3.2%
Volume +240%
RSI crosses 50
MACD bullish crossover
Price reclaims 200 EMA
Earnings estimate revised upward
Revenue guidance increased
Large call sweep detected
Put/Call ratio changes sharply
GEX flips negative → positive
Short interest increases
Short borrow cost increases
Sector relative strength improves
QQQ changes regime
VIX spikes
10Y yield moves sharply
Fed expectations change
Breaking news appears
SEC filing appears
Institutional ownership changes
```

Every event should receive:

```text
event_type
symbol
timestamp
importance
direction
magnitude
confidence
source
novelty
persistence
market_relevance
```

Classify events:

```text
BULLISH
BEARISH
NEUTRAL
MIXED
UNKNOWN
```

Also classify significance:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

---

# 5. MARKET INTELLIGENCE ENGINE

Create:

`MarketIntelligenceEngine`

Analyze:

### Macro

- Fed
- interest rates
- Treasury yields
- yield curve
- CPI
- PCE
- GDP
- employment
- unemployment
- consumer confidence
- USD
- oil
- gold
- VIX
- credit conditions
- liquidity

### Market Breadth

- advance/decline
- new highs/lows
- volume breadth
- sector breadth
- index breadth
- momentum breadth

### Major indices

At minimum:

```text
SPY
QQQ
DIA
IWM
VIX
```

### Regime detection

Classify:

```text
RISK_ON
RISK_OFF
TRENDING_BULL
TRENDING_BEAR
SIDEWAYS
HIGH_VOLATILITY
LOW_VOLATILITY
TRANSITION
CRISIS
```

Use multiple signals rather than a single indicator.

Generate:

```text
market_regime
regime_confidence
regime_change_probability
```

---

# 6. SECTOR INTELLIGENCE ENGINE

Create:

`SectorIntelligenceEngine`

Analyze:

- sector relative strength
- sector momentum
- sector breadth
- sector ETF trends
- sector volume
- sector volatility
- sector news
- sector earnings
- sector valuation
- sector rotation

Determine:

```text
LEADING
IMPROVING
WEAKENING
LAGGING
```

A stock signal should be adjusted based on sector regime.

Example:

```text
Strong stock + strong sector + strong market
```

should receive greater confirmation than:

```text
Strong stock + weak sector + risk-off market
```

---

# 7. STOCK INTELLIGENCE ENGINE

Improve the existing stock analysis.

Analyze:

## Technical

- price
- VWAP
- EMA 20/50/200
- SMA
- RSI
- MACD
- MACD histogram
- ATR
- ADX
- Bollinger Bands
- volume
- relative volume
- OBV
- support
- resistance
- breakouts
- breakdowns
- trend
- momentum
- volatility

## Price structure

Detect:

- higher highs
- higher lows
- lower highs
- lower lows
- consolidation
- breakout
- failed breakout
- reversal
- accumulation
- distribution

## Relative strength

Compare stock against:

- SPY
- QQQ
- sector ETF
- relevant benchmark

Generate:

```text
relative_strength_score
trend_score
momentum_score
technical_score
```

---

# 8. FUNDAMENTAL INTELLIGENCE ENGINE

Build a robust fundamental engine using FMP + yfinance + SEC/public information where available.

Calculate:

## Quality

- ROIC
- ROE
- ROA
- gross margin
- operating margin
- net margin
- FCF margin
- earnings quality
- cash conversion
- revenue stability

## Growth

- revenue growth
- EPS growth
- FCF growth
- earnings revisions
- forward growth
- historical growth consistency

## Financial Health

- debt/equity
- net debt
- interest coverage
- current ratio
- cash
- FCF
- debt maturity risk

## Valuation

- P/E
- forward P/E
- PEG
- EV/EBITDA
- EV/Sales
- P/S
- P/B
- FCF yield

Compare valuation against:

- historical valuation
- sector
- industry
- peers
- growth rate

---

# 9. QUANTITATIVE MOAT ENGINE

Create:

`QuantitativeMoatEngine`

Do NOT call it "Morningstar Moat."

Call it:

`AI Quantitative Moat Score`

or

`QMS`

Score 0–100.

Include:

### ROIC advantage

- ROIC level
- ROIC consistency
- ROIC vs WACC
- ROIC vs peers

### Pricing power

- gross margin
- operating margin
- margin stability
- pricing/margin trend

### Customer/revenue stickiness

- recurring revenue where available
- revenue stability
- retention proxies
- customer concentration

### Competitive position

- market share
- market share trend
- industry concentration
- peer comparison

### Switching costs / network effects

Use structured qualitative data where available.

### Intangible advantages

- R&D
- patents
- IP
- brand proxies

### Capital efficiency

- FCF margin
- asset turnover
- reinvestment efficiency

Generate:

```text
qms_score
qms_confidence
qms_trend
qms_strength
```

Do not claim this is Morningstar's proprietary rating.

---

# 10. FAIR VALUE ENGINE

Create:

`FairValueEngine`

Use multiple valuation methods where data supports them.

Potential models:

- DCF
- owner earnings
- FCF yield
- earnings multiple
- revenue multiple
- EV/EBITDA
- peer-relative valuation
- historical valuation

Do not rely on a single valuation model.

Generate:

```text
fair_value_low
fair_value_base
fair_value_high
current_price
upside_percent
downside_percent
valuation_score
valuation_confidence
```

Use scenario analysis:

```text
BEAR
BASE
BULL
```

Never present fair value as certainty.

---

# 11. UNCERTAINTY ENGINE

Create:

`UncertaintyEngine`

Estimate uncertainty based on:

- earnings volatility
- revenue volatility
- margin volatility
- valuation dispersion
- analyst estimate dispersion
- historical drawdowns
- beta
- ATR
- implied volatility
- liquidity
- event risk
- earnings proximity
- macro sensitivity
- data quality

Generate:

```text
uncertainty_score
uncertainty_level
tail_risk_score
event_risk_score
```

---

# 12. NEWS & EVENT INTELLIGENCE

Upgrade the existing news system.

Analyze:

- breaking news
- earnings
- guidance
- SEC filings
- PR
- M&A
- product launches
- lawsuits
- regulation
- analyst upgrades/downgrades
- insider transactions
- institutional activity

Every event should be analyzed for:

```text
sentiment
importance
novelty
credibility
expected_duration
financial_impact
competitive_impact
price_impact_probability
```

Do not merely use generic positive/negative sentiment.

Determine:

> Is this event actually likely to change the company's expected future cash flows or market positioning?

---

# 13. OPTIONS INTELLIGENCE ENGINE

Create or significantly upgrade:

`OptionsIntelligenceEngine`

Analyze:

- calls
- puts
- volume
- open interest
- IV
- IV Rank
- IV percentile
- delta
- gamma
- theta
- vega
- expiration
- strike concentration
- unusual trades
- sweeps
- block trades
- directional flow
- put/call ratios

Separate:

```text
FLOW
POSITIONING
VOLATILITY
DEALER HEDGING
```

Do not equate a large call trade automatically with bullish intent.

Consider:

- opening vs closing if available
- trade side
- premium
- volume/OI relationship
- expiration
- strike
- underlying price
- IV change

---

# 14. TRUE GAMMA / DEALER HEDGING ENGINE

Create:

`DealerGammaEngine`

If actual GEX data is available from the provider, use it.

If not, clearly label calculated values as estimates.

Do not call a simple OI concentration heuristic "true GEX."

Calculate where possible:

- gamma exposure
- gamma flip
- positive gamma
- negative gamma
- strike concentration
- dealer hedging pressure
- pin risk
- expiration effects

Generate:

```text
dealer_gamma_regime
gamma_score
gamma_flip_level
hedging_pressure
gamma_confidence
```

---

# 15. OPTIONS EXPIRATION ENGINE

Create:

`OptionsExpirationEngine`

Detect:

- weekly expiration
- monthly expiration
- quarterly expiration
- major OI concentrations
- pin risk
- gamma changes
- expected volatility changes
- post-expiration unwind risk

The system should understand that options expiration can alter market mechanics.

Do not make deterministic claims such as:

"Price will go to $X because of gamma."

Use probabilistic language.

---

# 16. SHORT SQUEEZE ENGINE

Create:

`ShortSqueezeEngine`

Analyze:

- short interest
- short interest ratio
- days to cover
- borrow cost/rate where available
- utilization where available
- shares available where available
- short volume
- price momentum
- volume acceleration
- options activity
- float
- liquidity

Generate:

```text
squeeze_score
squeeze_probability
short_pressure
covering_probability
squeeze_risk
```

IMPORTANT:

Never state:

"Shorts WILL be forced to cover."

Instead:

"Conditions are consistent with elevated short-covering probability."

---

# 17. MARGIN / LIQUIDATION RISK ENGINE

Create:

`MarginRiskEngine`

Keep these two concepts separate:

## A. Market-wide margin/liquidation risk

Estimate using available proxies:

- volatility
- leverage proxies
- market breadth
- credit conditions
- liquidity
- sharp index declines
- VIX
- correlations
- funding conditions

Generate:

```text
market_margin_risk
liquidation_risk
forced_selling_risk
```

These are estimates, not direct observations of individual investors' margin calls.

## B. Portfolio margin risk

For our own portfolio, calculate as accurately as possible using configurable broker/margin rules.

Monitor:

- buying power
- leverage
- maintenance margin
- concentration
- collateral
- margin utilization
- liquidation distance
- stress scenarios

Generate:

```text
portfolio_margin_utilization
margin_buffer
liquidation_distance
margin_stress_score
```

---

# 18. MARKET PRESSURE ENGINE

Combine:

```text
Short Squeeze
+
Options / Dealer Gamma
+
Expiration
+
Margin / Liquidation
+
Volume
+
Volatility
+
Market Breadth
```

into:

`MarketPressureEngine`

Generate:

```text
market_pressure_score
market_pressure_direction
market_pressure_confidence
```

But preserve the individual components.

Never allow the combined score to hide important conflicting signals.

Example:

```text
Short Pressure:       +72
Options Pressure:     +81
Gamma Pressure:       -45
Margin Risk:          -78
Market Regime:        -60

Combined Pressure:    -21
```

The AI should explain the conflict.

---

# 19. MULTI-HORIZON PREDICTION ENGINE

Upgrade prediction from simple BUY/SELL classification.

Predict probabilities/scenarios across:

```text
5m
15m
1h
1D
3D
1W
1M
3M
```

Only support horizons appropriate for the available data.

Predict:

### Direction

```text
P(UP)
P(DOWN)
P(SIDEWAYS)
```

### Expected return

```text
expected_return
```

### Volatility

```text
expected_volatility
```

### Drawdown

```text
expected_drawdown
```

### Tail risk

```text
tail_risk_probability
```

### Confidence

```text
prediction_confidence
```

Do not attempt to predict an exact future stock price as the primary model target.

Use probabilistic outcomes.

---

# 20. REGIME-AWARE ML

Models must understand that market behavior changes.

Train/evaluate separately across regimes:

```text
Bull
Bear
Sideways
High Volatility
Low Volatility
Risk-On
Risk-Off
Crisis
```

The model should be able to answer:

> "Does this strategy work in the current regime?"

Do not assume a signal that worked during a bull market will work during a bear market.

---

# 21. FEATURE GROUP ARCHITECTURE

Organize features into groups:

```text
MARKET
MACRO
SECTOR
TECHNICAL
MOMENTUM
VOLUME
FUNDAMENTAL
VALUATION
MOAT
NEWS
SENTIMENT
OPTIONS
GAMMA
SHORT
SQUEEZE
MARGIN
LIQUIDITY
VOLATILITY
REGIME
PORTFOLIO
```

Track feature provenance.

Every model prediction should be explainable back to feature groups.

---

# 22. FEATURE ABLATION TESTING

Implement systematic ablation experiments.

At minimum:

```text
Baseline
Baseline + Fundamentals
Baseline + News
Baseline + Options
Baseline + Short
Baseline + Margin
Baseline + Market Regime
All Features
```

Measure:

```text
Profit Factor
Sharpe
Sortino
Max Drawdown
CAGR
Calmar
Win Rate
Average Winner
Average Loser
Expectancy
Turnover
```

Determine which data sources actually improve OOS performance.

Do not assume that more data = better model.

---

# 23. AI TRADE DECISION ENGINE

Create:

`TradeDecisionEngine`

Possible actions:

```text
BUY_STOCK
BUY_LEAP
BUY_CALL
BUY_PUT
SELL_CALL
SELL_PUT
VERTICAL_SPREAD
COVERED_CALL
CASH_SECURED_PUT
HEDGE
REDUCE
EXIT
HOLD
WAIT
NO_TRADE
```

The engine must select the strategy based on:

- expected return
- probability of success
- volatility
- IV
- liquidity
- risk
- max loss
- max gain
- time decay
- capital requirement
- portfolio exposure
- correlation
- tax/transaction assumptions if configured
- market regime
- event risk

Do not automatically prefer options.

The system must be allowed to conclude:

```text
Stock > Option
Option > Stock
Hedge > New Position
No Trade > Everything
```

---

# 24. STOCK STRATEGY ENGINE

For stock trades calculate:

```text
entry
stop
target
position_size
risk_per_share
reward_per_share
risk_reward
expected_return
expected_drawdown
```

Use volatility-adjusted stops rather than arbitrary percentages where appropriate.

---

# 25. OPTIONS STRATEGY ENGINE

Build strategy selection logic for:

### Directional

- long call
- long put
- LEAPS

### Income

- covered call
- cash-secured put

### Risk-defined

- call spread
- put spread
- collars

### Hedging

- protective put
- put spread
- collar
- index hedge

The engine should compare strategies using expected value and risk.

Example:

```text
Stock:
Expected Return +6%
Risk -3%

Call:
Expected Return +18%
Risk -100% premium

Covered Call:
Expected Return +4%
Downside -X%

Protective Put:
Expected Return +2%
Downside protected below X
```

Then choose based on portfolio objectives.

---

# 26. PASSIVE-INCOME ENGINE

Create:

`IncomeStrategyEngine`

Identify opportunities for:

- covered calls
- cash-secured puts
- other supported defined-risk income strategies

Evaluate:

```text
premium
yield
annualized yield
probability of profit
assignment probability
downside exposure
upside sacrificed
IV Rank
earnings risk
liquidity
distance to strike
```

Do NOT optimize for premium yield alone.

A high premium can indicate high risk.

---

# 27. CAPITAL PROTECTION ENGINE

Create:

`CapitalProtectionEngine`

Monitor:

- portfolio drawdown
- volatility spike
- market regime deterioration
- correlated exposure
- earnings concentration
- options concentration
- margin utilization
- liquidity
- tail risk

Actions:

```text
HOLD
REDUCE
EXIT
HEDGE
RAISE CASH
REDUCE LEVERAGE
```

The system must prioritize survival.

Avoid catastrophic loss even when expected return is attractive.

---

# 28. PORTFOLIO INTELLIGENCE ENGINE

The system should not analyze every position independently.

Analyze the entire portfolio.

Calculate:

- total exposure
- sector exposure
- beta
- volatility
- correlation
- concentration
- factor exposure
- options exposure
- delta
- gamma
- vega
- theta
- downside stress
- margin utilization
- cash
- portfolio VaR / expected shortfall where appropriate

Stress test:

```text
SPY -5%
QQQ -7%
VIX +30%
rates +50 bps
sector -10%
individual position -20%
```

Determine portfolio-level damage.

---

# 29. DYNAMIC POSITION SIZING

Position size should depend on:

- confidence
- expected edge
- volatility
- liquidity
- stop distance
- portfolio exposure
- correlation
- regime
- drawdown
- tail risk

High confidence does NOT automatically mean large position size.

Example:

```text
High confidence
+
extreme volatility
+
high correlation
+
high event risk
=
smaller position
```

Use configurable maximum risk per trade and portfolio.

---

# 30. THESIS ENGINE

Every trade must have an explicit thesis.

Store:

```text
thesis
bull_case
bear_case
key_drivers
supporting_evidence
contradicting_evidence
entry_reason
expected_catalysts
invalidation_conditions
exit_conditions
```

The AI must continuously ask:

> "Is the original thesis still valid?"

---

# 31. REACTION / MONITORING ENGINE

Create a continuous:

`TradeMonitoringEngine`

Monitor open positions and watchlist candidates.

Trigger reevaluation when:

- price crosses technical level
- volume changes materially
- new news arrives
- earnings estimate changes
- options flow changes
- gamma changes
- short pressure changes
- market regime changes
- sector regime changes
- volatility changes
- thesis condition changes

Do not recompute everything unnecessarily.

Use event-driven/incremental evaluation where practical.

---

# 32. SIGNAL STATE MACHINE

Signals should evolve:

```text
WATCH
↓
SETUP
↓
PLAN
↓
READY
↓
ACTIVE
↓
PROFIT
↓
CLOSE
```

Or:

```text
WATCH
↓
INVALIDATED
```

A BUY signal should not remain BUY indefinitely.

Store:

```text
signal_created_at
signal_updated_at
signal_expiration
signal_version
reason_for_change
previous_signal
new_signal
```

---

# 33. ALERT PRIORITIZATION

Create intelligent alerts.

Not every event deserves an alert.

Classify:

```text
INFO
WATCH
ACTIONABLE
URGENT
CRITICAL
```

Alert only when the expected decision impact is meaningful.

Examples:

```text
"NVDA bullish probability increased 61% → 78%"
"Thesis invalidation detected"
"Market regime changed RISK_ON → RISK_OFF"
"Portfolio margin buffer fell below threshold"
"Large options positioning change detected"
"Unexpected earnings/news event"
```

---

# 34. DECISION EXPLANATION

Every decision must be explainable.

Example:

```text
ACTION: BUY NVDA

Confidence: 81%

Why:
+ Market regime bullish
+ Semiconductor sector strengthening
+ Price above 20/50/200 EMA
+ Relative strength increasing
+ Earnings revisions positive
+ Options positioning bullish
+ Short pressure moderate
+ Valuation acceptable

Risks:
- IV elevated
- Earnings in 9 days
- QQQ extended

Invalidation:
- Price closes below $XXX
- Sector regime changes
- Earnings estimate reverses
- Market regime becomes risk-off
```

The system must explicitly identify both bullish and bearish evidence.

---

# 35. AI SHOULD CHALLENGE ITS OWN TRADE

Before recommending a trade, run:

`AdversarialTradeReview`

Ask:

1. Why might this trade be wrong?
2. What evidence contradicts the thesis?
3. What would cause the trade to fail?
4. Is the expected return sufficient for the risk?
5. Is there a better alternative?
6. Is this already priced in?
7. Is the signal simply chasing momentum?
8. Is options activity misleading?
9. Is the market regime unfavorable?
10. Is there an upcoming event that changes the risk?

Only proceed if the trade survives the review.

---

# 36. OPPORTUNITY RANKING

For a watchlist, rank opportunities by:

```text
Expected Value
+
Risk-adjusted Return
+
Confidence
+
Catalyst Quality
+
Technical Setup
+
Fundamental Quality
+
Options Confirmation
+
Market Regime
-
Event Risk
-
Drawdown Risk
-
Liquidity Risk
-
Portfolio Correlation
```

Do not simply rank by predicted percentage gain.

---

# 37. TRADE JOURNAL & LEARNING LOOP

Every decision must be logged.

Store:

```text
model_version
feature_snapshot
data_sources
prediction
confidence
decision
strategy
entry
stop
target
position_size
thesis
risk
expected_return
actual_return
MAE
MFE
exit_reason
market_regime
```

After closing:

Analyze:

- prediction accuracy
- calibration
- expected vs actual return
- expected vs actual drawdown
- signal performance
- strategy performance
- feature-group performance
- regime performance

---

# 38. MODEL CALIBRATION

Do not only measure classification accuracy.

Measure:

- Brier score
- calibration curves
- precision/recall where applicable
- expected calibration error
- probability buckets

If the model says:

```text
80% probability UP
```

then approximately 80% of comparable historical predictions should actually be UP.

---

# 39. WALK-FORWARD VALIDATION

Reuse the existing walk-forward validation infrastructure.

Never replace it with random train/test splitting.

Use:

```text
TRAIN
↓
VALIDATE
↓
TEST
↓
MOVE WINDOW
↓
RETRAIN
↓
TEST
```

All data must be point-in-time correct.

Measure performance across:

- bull
- bear
- sideways
- high volatility
- low volatility
- crisis
- individual sectors

---

# 40. REALISTIC BACKTESTING

Backtests must account for:

- commissions
- bid/ask spread
- slippage
- liquidity
- option spread
- option assignment
- expiration
- gaps
- delayed information
- realistic fills
- position sizing
- transaction costs

Do not assume perfect fills.

---

# 41. MODEL PROMOTION GATE

A new model must NOT automatically become production.

Require improvement in:

- Profit Factor
- Sharpe
- Sortino
- Max Drawdown
- OOS return
- calibration
- stability

Do not promote a model simply because:

- win rate increased
- backtest return increased
- training accuracy increased

Require statistically and practically meaningful improvement.

Store:

```text
model_id
model_version
training_period
validation_period
test_period
features
hyperparameters
metrics
promotion_status
```

---

# 42. DATA SOURCE VALUE MEASUREMENT

Measure whether each provider actually improves trading performance.

Track:

```text
yfinance contribution
FMP contribution
UW contribution
News contribution
SEC contribution
Options contribution
Short contribution
Macro contribution
```

Use feature ablation and controlled experiments.

If a provider costs money but does not improve OOS performance enough to justify its cost, flag it.

---

# 43. COST CONTROL

For paid APIs:

- cache aggressively
- batch requests
- incremental updates
- avoid unnecessary historical refetches
- prioritize watchlist/active positions
- prioritize events
- configure rate limits
- track API usage
- track cost per symbol
- track cost per decision

Build:

`DataUsageMonitor`

---

# 44. UI / DASHBOARD

Add an AI Market Intelligence dashboard.

## Market Overview

Display:

```text
Market Regime
Regime Confidence
SPY
QQQ
IWM
VIX
Breadth
Sector Rotation
Macro Risk
```

## Opportunity Radar

Display:

```text
Symbol
Action
Confidence
Expected Return
Risk
Risk/Reward
Regime
Catalyst
Options
Squeeze
Valuation
```

## Position Monitor

Display:

```text
Position
Thesis Status
Current Signal
P/L
Risk
Stop
Target
Hedge
Margin Risk
```

## AI Decision Card

Show:

```text
ACTION
CONFIDENCE
EXPECTED RETURN
RISK
BEST STRATEGY
WHY
RISKS
INVALIDATION
```

## Change Feed

Show:

```text
WHAT CHANGED
WHEN
WHY IT MATTERS
EXPECTED IMPACT
ACTION
```

---

# 45. API DESIGN

Expose APIs for:

```text
/market/intelligence
/market/regime
/sectors/intelligence
/stocks/{symbol}/intelligence
/stocks/{symbol}/changes
/stocks/{symbol}/prediction
/stocks/{symbol}/options
/stocks/{symbol}/squeeze
/stocks/{symbol}/margin-risk
/stocks/{symbol}/valuation
/stocks/{symbol}/moat
/stocks/{symbol}/strategies
/portfolio/intelligence
/portfolio/risk
/portfolio/hedges
/signals
/signals/{id}/history
/decisions
/alerts
/models
/models/performance
```

Use the project's existing API conventions rather than blindly creating new patterns.

---

# 46. DATABASE

Use the existing database architecture.

Create normalized entities where necessary for:

```text
MarketRegime
SectorRegime
MarketEvent
StockEvent
FeatureSnapshot
Prediction
PredictionOutcome
TradeDecision
TradeThesis
OptionsSnapshot
GammaSnapshot
ShortPressureSnapshot
MarginRiskSnapshot
PortfolioRiskSnapshot
StrategyRecommendation
SignalTransition
Alert
ModelVersion
ModelEvaluation
```

Avoid unnecessary duplication.

---

# 47. AUTOMATION / SCHEDULING

Implement different refresh frequencies.

Example:

### Fast

```text
1–5 minutes
```

For:

- price
- volume
- active positions
- options flow where supported
- alerts

### Medium

```text
15–60 minutes
```

For:

- technical analysis
- market regime
- sector analysis
- options positioning

### Slow

```text
Daily
```

For:

- fundamentals
- valuation
- moat
- financial health
- portfolio review

### Event-driven

Immediately evaluate:

- breaking news
- SEC filing
- earnings
- major options flow
- regime change
- price breakout
- risk threshold violation

Use the existing scheduling architecture.

---

# 48. PAPER TRADING FIRST

All automated decision/action capabilities must initially operate in:

`PAPER TRADING MODE`

Do not connect live trading automatically.

Create clear safety gates:

```text
ANALYSIS
↓
PAPER SIGNAL
↓
PAPER TRADE
↓
VALIDATION
↓
PROMOTION REVIEW
↓
LIVE TRADING ELIGIBLE
```

Live execution must remain explicitly disabled unless separately configured.

---

# 49. RISK GUARDRAILS

Implement hard limits.

Examples:

```text
max risk per trade
max portfolio risk
max position size
max sector exposure
max correlated exposure
max options exposure
max leverage
max margin utilization
max daily loss
max drawdown
```

When a hard limit is violated:

```text
DO NOT TRADE
```

Risk engine overrides strategy engine.

Strategy engine overrides prediction engine.

Risk always wins.

---

# 50. CONFIDENCE GATING

Do not execute merely because the model predicts UP.

Require:

```text
Prediction confidence
+
Data quality
+
Risk/reward
+
Regime compatibility
+
Liquidity
+
Portfolio compatibility
+
Thesis confirmation
```

Example:

```text
Prediction: 82%
But:

Data Quality: 55%
Liquidity: poor
Earnings tomorrow
Portfolio already concentrated

=> NO TRADE
```

---

# 51. CONTINUOUS SELF-EVALUATION

The system should answer daily:

```text
Which predictions were correct?

Which were wrong?

Which signals produced profits?

Which signals produced losses?

Which features contributed?

Which features were noise?

Which strategies worked?

Which market regimes caused failures?

Did options data improve decisions?

Did short data improve decisions?

Did fundamental data improve decisions?

Did news improve decisions?

Did the model become overconfident?
```

Generate:

`/docs/YYYY-MM-DD/DAILY_MODEL_PERFORMANCE.md`

and retain historical reports.

---

# 52. EXPERIMENT FRAMEWORK

Every major model change should create an experiment.

Example:

```text
Experiment:
UW Options Features v2

Baseline PF: 1.62
New PF:      1.84

Baseline Sharpe: 1.21
New Sharpe:      1.39

Baseline Max DD: -14.2%
New Max DD:      -12.1%

OOS improvement: YES
```

Do not promote based on in-sample results.

---

# 53. TESTING

Implement comprehensive tests.

## Unit tests

For:

- indicators
- scoring
- options calculations
- GEX calculations
- squeeze calculations
- margin calculations
- valuation
- moat
- position sizing
- risk
- strategy selection

## Integration tests

For:

- provider adapters
- event pipeline
- feature pipeline
- ML pipeline
- decision engine
- paper trading

## Regression tests

Ensure existing platform functionality continues working.

## Data leakage tests

Explicitly test that future information cannot enter historical decisions.

## Backtest tests

Validate deterministic behavior and realistic assumptions.

---

# 54. OBSERVABILITY

Add logging/metrics for:

```text
provider failures
stale data
missing data
model latency
prediction latency
decision latency
API usage
signal changes
trade decisions
risk overrides
model performance
```

Every AI decision should be traceable.

---

# 55. FAIL-SAFE BEHAVIOR

When critical information is missing:

DO NOT GUESS.

Examples:

```text
Missing options chain
→ options strategy unavailable

Stale market data
→ reduce confidence / no trade

Missing fundamentals
→ fundamental score unavailable

Unknown margin state
→ do not increase leverage

Provider outage
→ fallback if validated
```

The system should degrade gracefully.

---

# 56. IMPORTANT ANTI-OVERFITTING RULES

Never:

- tune endlessly against the test set
- use future data
- use revised information unavailable at the time
- optimize solely for win rate
- optimize solely for total return
- add indicators simply because they sound useful
- assume correlation equals causation
- assume options flow predicts direction
- assume high short interest guarantees a squeeze
- assume high IV means options should be sold
- assume low P/E means undervaluation
- assume high moat means stock will rise immediately

Every feature must demonstrate incremental value.

---

# 57. FINAL AI DECISION OBJECT

Standardize the output.

Example:

```json
{
  "symbol": "NVDA",
  "timestamp": "...",
  "action": "BUY_STOCK",
  "confidence": 0.81,
  "expected_return": 0.054,
  "expected_drawdown": -0.021,
  "risk_reward": 2.57,

  "market_regime": "RISK_ON",
  "sector_regime": "LEADING",

  "technical_score": 88,
  "fundamental_score": 93,
  "valuation_score": 71,
  "moat_score": 94,

  "options_score": 82,
  "gamma_score": 67,
  "squeeze_score": 54,
  "margin_risk_score": 22,

  "strategy": {
    "type": "STOCK",
    "entry": 0,
    "stop": 0,
    "target": 0,
    "position_size": 0
  },

  "thesis": "...",
  "bull_case": "...",
  "bear_case": "...",
  "invalidation": [],

  "data_quality": "FRESH",
  "decision_reason": []
}
```

Use the project's existing domain models/conventions if they already provide equivalent structures.

---

# 58. IMPLEMENTATION ORDER

Implement in phases.

## Phase 1 — Architecture Audit

Inspect existing system.

Create:

`/docs/YYYY-MM-DD/AI_MARKET_INTELLIGENCE_ARCHITECTURE_AUDIT.md`

Do not code major changes before understanding the current architecture.

## Phase 2 — Data Quality + Event Framework

Implement:

- canonical data models
- data quality
- timestamps
- point-in-time handling
- event/change detection

## Phase 3 — Market Intelligence

Implement:

- macro
- market regime
- breadth
- sector intelligence

## Phase 4 — Fundamental Intelligence

Implement:

- FMP integration
- quality
- growth
- financial health
- valuation
- quantitative moat
- fair value
- uncertainty

Preserve yfinance as fallback.

## Phase 5 — Options / Pressure Intelligence

Implement:

- options flow
- positioning
- GEX/dealer hedging
- expiration
- short squeeze
- margin/liquidation
- combined market pressure

Preserve existing options implementation and improve it rather than replacing it blindly.

## Phase 6 — Prediction Engine

Implement:

- multi-horizon predictions
- regime awareness
- probability outputs
- calibration
- uncertainty

## Phase 7 — Strategy Engine

Implement:

- stock
- LEAPS
- calls
- puts
- covered calls
- cash-secured puts
- spreads
- hedges
- no-trade

## Phase 8 — Portfolio & Risk

Implement:

- portfolio intelligence
- dynamic sizing
- margin risk
- capital protection
- correlation
- stress testing

## Phase 9 — Adaptive Monitoring

Implement:

- event-driven reevaluation
- thesis monitoring
- signal state machine
- intelligent alerts
- change detection

## Phase 10 — Validation

Implement:

- feature ablation
- walk-forward
- OOS
- realistic backtesting
- calibration
- model promotion gates

## Phase 11 — UI/API

Integrate the new intelligence into the existing application.

## Phase 12 — Paper Trading

Connect the decision engine to paper trading only.

---

# 59. DO NOT STOP AFTER DOCUMENTATION

Actually implement the system.

Do not merely:

- describe architecture
- create placeholder classes
- create TODOs
- mock provider responses
- create fake scores
- hardcode predictions
- hardcode BUY signals

Use real provider data through the existing adapter architecture.

Where a provider does not supply a required metric:

1. identify the limitation
2. calculate a defensible derived metric if possible
3. clearly label it as derived/estimated
4. attach confidence/data quality
5. never fabricate data

---

# 60. DO NOT BREAK THE EXISTING SYSTEM

Before changing anything:

- run existing tests
- understand existing contracts
- preserve APIs where possible
- preserve existing workflows
- preserve Watch → Plan → Active → Close
- preserve current paper trading
- preserve existing walk-forward validation
- preserve existing backtesting
- preserve model promotion gates

Refactor only when necessary.

---

# 61. REQUIRED DOCUMENTATION

Create/update:

```text
/docs/YYYY-MM-DD/
```

At minimum:

```text
AI_MARKET_INTELLIGENCE_ARCHITECTURE_AUDIT.md
AI_MARKET_INTELLIGENCE_ARCHITECTURE.md
DATA_PROVIDER_ARCHITECTURE.md
EVENT_CHANGE_DETECTION.md
MARKET_REGIME_ENGINE.md
FUNDAMENTAL_INTELLIGENCE.md
QUANTITATIVE_MOAT.md
FAIR_VALUE_ENGINE.md
OPTIONS_INTELLIGENCE.md
GAMMA_DEALER_ENGINE.md
SHORT_SQUEEZE_ENGINE.md
MARGIN_RISK_ENGINE.md
MARKET_PRESSURE_ENGINE.md
PREDICTION_ENGINE.md
STRATEGY_ENGINE.md
PORTFOLIO_RISK_ENGINE.md
ADAPTIVE_MONITORING.md
MODEL_VALIDATION.md
MODEL_PROMOTION.md
PAPER_TRADING.md
```

Document:

- architecture
- formulas
- assumptions
- data sources
- limitations
- tests
- validation results

---

# 62. ACCEPTANCE CRITERIA

The implementation is NOT complete until:

### Data

- Multiple providers work through adapters.
- Data freshness is tracked.
- Point-in-time correctness is enforced.

### Intelligence

The system can evaluate:

- market
- macro
- sector
- stock
- fundamentals
- valuation
- moat
- news
- options
- gamma
- expiration
- squeeze
- margin
- portfolio risk

### Prediction

The system produces:

- multi-horizon probabilities
- expected return
- expected drawdown
- volatility
- confidence
- regime awareness

### Decision

The system can choose:

- stock
- options
- income strategy
- hedge
- reduce
- exit
- no trade

### Adaptation

The system detects changes and reevaluates existing positions.

### Risk

Risk limits override trading decisions.

### Validation

All important models have:

- walk-forward testing
- OOS testing
- realistic backtesting
- calibration
- feature ablation

### Explainability

Every decision explains:

- why
- evidence
- risks
- expected outcome
- invalidation

### Paper Trading

The system can execute recommendations in paper mode and record results.

---

# 63. MOST IMPORTANT DESIGN PRINCIPLE

The system should NOT think:

> "NVDA looks bullish, therefore BUY."

It should think:

> "What is happening in the market?"

Then:

> "What changed?"

Then:

> "How does this affect NVDA?"

Then:

> "What does price/volume/fundamentals/options/short positioning/news/macro indicate?"

Then:

> "What are the competing bullish and bearish scenarios?"

Then:

> "What is the probability distribution of outcomes?"

Then:

> "What is the best risk-adjusted way to express the thesis?"

Then:

> "How does this affect my existing portfolio?"

Then:

> "What could invalidate this thesis?"

Then:

> "What should I do now?"

And after the decision:

> "Did I make the right decision, and what did I learn?"

---

# 64. FINAL DELIVERABLE

At the end of implementation provide:

1. Architecture summary
2. Files changed
3. New modules
4. Database migrations
5. API changes
6. UI changes
7. Provider changes
8. ML changes
9. Backtesting changes
10. Test results
11. Walk-forward results
12. OOS results
13. Feature ablation results
14. Model performance comparison
15. Known limitations
16. Remaining TODOs
17. Recommended next implementation phase

Most importantly, provide a quantitative comparison:

```text
BEFORE vs AFTER

Profit Factor
Sharpe
Sortino
Max Drawdown
CAGR
Calmar
Win Rate
Expectancy
OOS Performance
Calibration
```

If the new system does not improve OOS performance, say so clearly.

Do not manufacture positive results.

---

# FINAL INSTRUCTION

Act as the lead engineer for a serious quantitative trading platform.

Inspect first.

Reuse existing architecture.

Implement incrementally.

Test continuously.

Validate with walk-forward and out-of-sample data.

Measure whether each new intelligence source actually improves performance.

Protect capital before chasing returns.

Never fabricate data.

Never claim certainty.

Never optimize solely for win rate.

The ultimate goal is not to create a system that generates lots of BUY signals.

The goal is to create a system that knows:

> **WHEN TO BUY, WHAT TO BUY, HOW TO EXPRESS THE TRADE, HOW MUCH TO RISK, WHEN TO TAKE PROFITS, WHEN TO HEDGE, WHEN TO EXIT, AND—MOST IMPORTANTLY—WHEN NOT TO TRADE.**
Absolutely. I’d have Claude implement this as a **data/AI architecture upgrade**, not simply add three API calls. The prompt below emphasizes keeping yfinance, adding FMP and Unusual Whales behind provider interfaces, and making the ML/decision engine measurable with proper out-of-sample validation.

# Claude Prompt — AI Stock Intelligence Data & Decision Engine Upgrade

```markdown
# PROJECT: Upgrade Stock Trading Platform with Multi-Provider AI Intelligence

You are a Senior Quantitative Developer, ML Engineer, Data Architect, Portfolio Manager, and Production Software Engineer.

I already have an existing Stock Trading Platform that supports stock watching, research/planning, technical analysis, entry/stop-loss suggestions, and trade tracking.

DO NOT rewrite the existing application unnecessarily.

First inspect the existing codebase, architecture, database, APIs, frontend, tests, configuration, and current yfinance implementation. Reuse existing functionality wherever possible.

The objective is to evolve the platform into a production-quality AI Stock Trading Intelligence Platform.

---

# 1. TARGET ARCHITECTURE

Implement this architecture:

                         DATA PROVIDERS
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ↓               ↓                ↓
          yfinance            FMP       Unusual Whales
              │               │                │
              │               │                │
        Market Data       Fundamentals     Options/Flow
        Historical        Earnings         Open Interest
        OHLCV             Financials       Expiration
        Basic Options     News             Unusual Activity
                                            Options Flow
                                            Squeeze Signals
              │               │                │
              └───────────────┼────────────────┘
                              ↓
                    DATA NORMALIZATION LAYER
                              ↓
                     FEATURE ENGINEERING
                              ↓
              ┌───────────────┼────────────────┐
              ↓               ↓                ↓
         TECHNICAL        OPTIONS FLOW      SHORT/SQUEEZE
          SIGNALS           SIGNALS            SIGNALS
              │               │                │
              └───────────────┼────────────────┘
                              ↓
                       ML PREDICTION
                              ↓
                       AI DECISION ENGINE
                              ↓
                  BUY / HOLD / SELL / WATCH
                              ↓
                     PORTFOLIO / RISK
                              ↓
                    PAPER TRADE ENGINE
                              ↓
                    TRADE JOURNAL
                              ↓
                     LEARNING LOOP

IMPORTANT:

The AI/ML layer must NOT directly depend on a specific vendor.

Use provider interfaces/adapters so providers can later be replaced without rewriting the application.

---

# 2. DATA PROVIDERS

Initially implement ONLY:

1. yfinance
2. Financial Modeling Prep (FMP)
3. Unusual Whales

Do not add additional providers unless necessary.

Create a provider abstraction.

Example:

MarketDataProvider
FundamentalDataProvider
NewsProvider
OptionsDataProvider

Implement adapters such as:

YFinanceProvider
FMPProvider
UnusualWhalesProvider

The rest of the application must communicate through these interfaces.

---

# 3. YFINANCE

Keep yfinance as the initial market-data provider.

Use it for:

- Historical OHLCV
- Daily prices
- Intraday prices where available
- Splits
- Dividends
- Basic company information
- Benchmark data
- Basic options information where useful

Do not remove the existing yfinance implementation if it already works.

Refactor it behind the provider abstraction if necessary.

Implement caching to avoid unnecessary requests.

Handle:

- Rate limits
- Missing data
- Network errors
- Invalid symbols
- Delisted securities
- Market holidays
- Time zones

Never allow a temporary provider failure to crash the entire trading platform.

---

# 4. FMP FUNDAMENTALS

Integrate Financial Modeling Prep.

Configuration must come from environment variables:

FMP_API_KEY

Never hard-code API credentials.

Implement support for:

## Financial Statements

- Income Statement
- Balance Sheet
- Cash Flow Statement

## Metrics

- Revenue
- Revenue growth
- EPS
- EPS growth
- Gross margin
- Operating margin
- Net margin
- EBITDA
- Free cash flow
- ROE
- ROIC
- Debt/Equity
- Current ratio
- Cash
- Total debt

## Valuation

- PE
- Forward PE where available
- PEG
- Price/Sales
- Price/Book
- EV/EBITDA
- EV/Sales

## Earnings

- Earnings date
- EPS estimate
- Actual EPS
- EPS surprise
- Revenue estimate
- Actual revenue
- Revenue surprise

## Analyst / Company Data

Where supported:

- Analyst estimates
- Price targets
- Analyst upgrades/downgrades
- Company profile
- Sector
- Industry

Store raw provider responses where appropriate so calculations can be reproduced.

Normalize FMP data into the application's canonical financial model.

---

# 5. UNUSUAL WHALES

Integrate Unusual Whales behind an OptionsDataProvider abstraction.

Configuration:

UNUSUAL_WHALES_API_KEY

Never hard-code credentials.

Use Unusual Whales for the data actually available through the subscribed API.

Potential data:

- Options chains
- Options volume
- Open interest
- Calls
- Puts
- Put/Call ratio
- Large trades
- Unusual options activity
- Sweeps
- Premium
- Expiration
- Options flow
- Dark pool data if available through the API
- Short/squeeze-related information if available

DO NOT invent fields that the API does not provide.

First inspect the current Unusual Whales API documentation/configuration available to the project.

Build the adapter so unavailable endpoints degrade gracefully.

---

# 6. DATA NORMALIZATION

Create a canonical internal data model.

Do not let FMP-specific, yfinance-specific, or Unusual Whales-specific fields leak throughout the application.

Example:

StockPrice
FinancialMetrics
EarningsEvent
OptionsContract
OptionsFlowEvent
ShortInterestSnapshot
MarketNewsEvent
MarketRegime
TechnicalFeatures
FundamentalFeatures
OptionsFeatures
SqueezeFeatures

Each record should contain:

- symbol
- market
- timestamp
- source
- data timestamp
- ingestion timestamp
- quality/status
- raw/reference ID where useful

Support US stocks first.

Design the symbol model so Hong Kong stocks can be added later.

---

# 7. DATA QUALITY ENGINE

This is critical.

For every data provider:

Check:

- Missing values
- Stale data
- Duplicate records
- Timestamp consistency
- Unexpected price changes
- Corporate actions
- Invalid values
- API errors

Create data-quality statuses:

VALID
PARTIAL
STALE
INVALID
UNAVAILABLE

Never silently treat missing data as zero.

Example:

BAD:

RSI = 0 because data missing

GOOD:

RSI = NULL
data_quality = PARTIAL

---

# 8. FEATURE ENGINE

Create a centralized Feature Engineering Engine.

It should transform normalized data into ML-ready features.

Do NOT calculate indicators in random controllers or frontend code.

All features should be reproducible.

---

# 9. TECHNICAL FEATURES

Calculate:

## Trend

- EMA 20
- EMA 50
- EMA 200
- SMA 50
- SMA 200
- Golden Cross
- Death Cross
- MACD
- ADX
- Supertrend

## Momentum

- RSI
- Stochastic RSI
- ROC
- OBV
- Relative Strength
- Relative Volume
- Volume acceleration

## Volatility

- ATR
- Historical volatility
- Bollinger Bands

## Price Structure

- Support
- Resistance
- Breakout
- Breakdown
- Distance from EMA 50
- Distance from EMA 200

Also support Center of Gravity (COG) as an optional feature.

---

# 10. OPTIONS FEATURES

Build an Options Intelligence Feature Engine.

Calculate/use available data for:

- Put volume
- Call volume
- Put/Call volume ratio
- Put open interest
- Call open interest
- Put/Call OI ratio
- Relative options volume
- Large trade activity
- Sweep activity
- Premium flow
- Expiration concentration
- Near-term expiration exposure

Where data supports it, derive:

- Call concentration
- Put concentration
- Options activity anomaly
- Bullish/bearish flow score

Do NOT assume that call buying is automatically bullish.

Attempt to distinguish:

- opening vs closing where available
- directional flow vs spreads where available
- unusual activity vs normal activity

If the data cannot reliably distinguish these, explicitly mark the feature as uncertain.

---

# 11. SHORT / SQUEEZE FEATURES

Create a Short/Squeeze Intelligence Engine.

Use available data to calculate:

- Short interest
- Short interest change
- Utilization
- Borrow cost
- Shares available
- Days to cover
- Short volume
- Price momentum
- Relative volume
- Options activity
- Options expiration proximity

Create:

Short Squeeze Score: 0-100

But DO NOT claim:

"forced covering will happen."

Instead estimate:

Potential Short Squeeze Risk/Opportunity

with:

- Score
- Confidence
- Supporting factors
- Risk factors

---

# 12. FUNDAMENTAL SCORE

Create a Fundamental Score from 0-100.

Suggested categories:

Growth: 30%
Quality: 25%
Cash Flow: 20%
Financial Strength: 15%
Valuation: 10%

Make weights configurable.

Do not hard-code the final weighting throughout the code.

---

# 13. TECHNICAL SCORE

Create Technical Score 0-100.

Suggested:

Trend: 40%
Momentum: 30%
Volume: 15%
Price Structure: 15%

Make weights configurable.

---

# 14. OPTIONS SCORE

Create Options Intelligence Score 0-100.

Suggested:

Options Activity: 25%
Flow: 25%
Open Interest: 15%
Expiration Positioning: 15%
Volume Anomaly: 10%
Premium Activity: 10%

Make weights configurable.

---

# 15. SQUEEZE SCORE

Create:

Squeeze Score = 0-100

Possible weighting:

Short Interest: 20%
Borrow/Utilization: 20%
Days to Cover: 10%
Short Interest Change: 10%
Relative Volume: 10%
Price Momentum: 10%
Options Activity: 10%
Expiration: 10%

Adjust based on available data.

---

# 16. MARKET REGIME

Implement market regime detection.

Analyze:

- SPY
- QQQ
- VIX where available
- Market trend
- Sector trend
- Breadth where available

Classify:

BULL
BEAR
SIDEWAYS
HIGH_VOLATILITY

Use market regime as a feature and risk modifier.

---

# 17. ML PREDICTION ENGINE

Start with robust tabular ML.

Use:

- XGBoost
- LightGBM
- Random Forest

Do NOT start with neural networks unless there is evidence they improve out-of-sample results.

The ML model should NOT attempt to predict an exact future stock price.

Instead predict probabilities.

For example:

Probability of:

- +5% within 20 trading days
- +10% within 20 trading days
- -5% within 20 trading days
- -10% within 20 trading days

Also calculate expected return and expected downside.

Example:

Prediction:

P(+5%) = 72%
P(+10%) = 51%
P(-5%) = 18%
P(-10%) = 7%

Expected Return = +8.4%

Expected Drawdown = -3.7%

---

# 18. AVOID DATA LEAKAGE

This is mandatory.

The ML system must NEVER use information that was unavailable at the decision timestamp.

Examples of prohibited leakage:

- Future earnings
- Future prices
- Future options OI
- Future revised financial statements
- Future news
- Future corporate actions not known at the time

Use point-in-time data where possible.

Document any limitations.

---

# 19. WALK-FORWARD VALIDATION

Do NOT rely on random train/test splits for time-series trading data.

Implement:

Training Window
↓
Validation Window
↓
Test Window
↓
Move Forward
↓
Retrain
↓
Repeat

Example:

Train:
2018-2022

Validate:
2023

Test:
2024

Then roll forward.

Keep a completely untouched final out-of-sample period.

---

# 20. PERFORMANCE OPTIMIZATION TARGET

DO NOT optimize primarily for win rate.

The primary optimization objectives are:

### 1. Profit Factor

Gross Profit / Gross Loss

### 2. Risk-Adjusted Return

Prioritize:

- Sharpe Ratio
- Sortino Ratio
- Calmar Ratio

### 3. Maximum Drawdown

Minimize:

Maximum Portfolio Drawdown

### 4. Out-of-Sample Performance

The strategy must perform on data that was not used for training or optimization.

### 5. Win Rate

Win rate is a secondary metric.

A 55% win-rate strategy can be better than a 70% win-rate strategy if the risk/reward and drawdown characteristics are superior.

Never manipulate thresholds simply to achieve a desired win rate.

---

# 21. BACKTESTING ENGINE

Create a realistic backtesting engine.

Account for:

- Transaction costs
- Slippage
- Bid/ask spread where data permits
- Market hours
- Position sizing
- Stop loss
- Take profit
- Partial exits

Avoid survivorship bias.

Where possible include delisted securities in historical testing.

---

# 22. AI DECISION ENGINE

Create the final decision layer.

Inputs:

Technical Score
Momentum Score
Fundamental Score
Options Score
Squeeze Score
News/Sentiment Score when available
Market Regime
ML Probability
Expected Return
Expected Drawdown
Current Position
Portfolio Risk

Output:

BUY
HOLD
SELL
WATCH

Also output:

- Confidence
- Entry price
- Stop loss
- Target
- Position size
- Risk/reward
- Expected return
- Expected downside
- Holding period
- Decision explanation
- Data quality

---

# 23. BUY DECISION

Do not use simplistic rules such as:

RSI < 30 = BUY

Instead evaluate the complete evidence.

Example:

BUY if:

- Expected return sufficiently positive
- Risk/reward acceptable
- ML probability favorable
- Technical trend supportive
- Market regime compatible
- Portfolio exposure acceptable
- Data quality sufficient

All thresholds must be configurable.

---

# 24. HOLD DECISION

A signal changing does NOT automatically mean SELL.

For an existing position, evaluate:

- Original investment thesis
- Trend deterioration
- Fundamental deterioration
- Momentum
- Support
- Volatility
- Market regime
- New expected return
- Alternative opportunities

Example:

Original BUY score:
88

Current score:
76

Decision:

HOLD

Reason:

Trend remains intact and expected return remains positive.

---

# 25. SELL DECISION

Sell if evidence indicates:

- Thesis invalidated
- Expected return deteriorated materially
- Risk increased
- Stop loss triggered
- Major fundamental deterioration
- Major technical breakdown
- Portfolio risk requires reduction
- Superior opportunity warrants capital rotation

Log the exact reason.

---

# 26. SIGNAL CHANGE ENGINE

This is a critical feature.

Every time new data arrives:

Compare:

Previous Decision
vs
Current Decision

Examples:

BUY → HOLD
BUY → SELL
HOLD → BUY
HOLD → SELL
SELL → BUY

Create a Signal Change Event.

Example:

Previous:

BUY
Confidence 84

Current:

HOLD
Confidence 68

Change:

BUY → HOLD

Reasons:

- Momentum decreased
- Options flow weakened
- Support remains intact
- Fundamentals unchanged

Do NOT create unnecessary trades from minor score fluctuations.

Implement configurable hysteresis / minimum change thresholds.

---

# 27. SIGNAL CONFIDENCE

Confidence must reflect:

- Agreement between models
- Data quality
- Historical performance of the signal
- Market regime
- Feature completeness

Example:

BUY
Confidence: 86%

But:

Data Quality: PARTIAL

should reduce confidence.

---

# 28. TRADE JOURNAL

Every decision must be permanently recorded.

Store:

- Timestamp
- Symbol
- Decision
- Confidence
- All relevant scores
- ML prediction
- Market regime
- Entry
- Stop
- Target
- Position size
- Data sources
- Feature snapshot
- Reasoning
- Outcome

This creates the dataset for future ML training.

---

# 29. LEARNING LOOP

After every completed trade:

Capture:

FEATURES
↓
DECISION
↓
TRADE
↓
OUTCOME

Calculate:

- Return
- Maximum favorable excursion
- Maximum adverse excursion
- Holding period
- Stop distance
- Target achievement

Use completed trades to evaluate model performance.

Do NOT automatically retrain production models after every trade.

Create:

Candidate Model
↓
Backtest
↓
Walk-forward validation
↓
Out-of-sample validation
↓
Compare with Production Model
↓
Promote only if statistically and economically better

---

# 30. MODEL VERSIONING

Every ML model must have:

- Model ID
- Version
- Training period
- Features
- Hyperparameters
- Dataset version
- Metrics
- Validation results
- Creation timestamp

Never overwrite a production model without versioning.

---

# 31. FEATURE IMPORTANCE

Show:

- Feature importance
- SHAP values where appropriate
- Positive factors
- Negative factors

Example:

NVDA

Positive:
+ Relative strength
+ Earnings growth
+ Volume expansion
+ Options activity

Negative:
- Valuation
- RSI elevated

This makes the AI explainable.

---

# 32. DATABASE DESIGN

Review the existing schema and add only what is necessary.

Likely entities:

data_provider
market_data
fundamental_snapshot
earnings_event
options_contract
options_flow
short_interest_snapshot
news_event
technical_features
fundamental_features
options_features
squeeze_features
market_regime
ml_prediction
ai_decision
signal_change
trade
trade_event
model_version
backtest_run
performance_metric

Use indexes appropriate for:

- symbol
- timestamp
- provider
- decision
- trade
- model version

Avoid storing redundant data unnecessarily.

---

# 33. API DESIGN

Create internal APIs such as:

GET /stocks/{symbol}/analysis
GET /stocks/{symbol}/signals
GET /stocks/{symbol}/fundamentals
GET /stocks/{symbol}/options
GET /stocks/{symbol}/squeeze
GET /stocks/{symbol}/prediction
GET /stocks/{symbol}/decision

Portfolio:

GET /portfolio
GET /portfolio/risk
GET /portfolio/performance

Trading:

GET /trades
GET /trades/{id}

Models:

GET /models
GET /models/{id}/performance

Backtesting:

POST /backtests
GET /backtests/{id}

Use the existing API conventions if the project already has them.

---

# 34. CACHING

Implement appropriate caching.

Do not repeatedly request:

- Historical prices
- Financial statements
- Options chains

Cache based on data frequency.

Example:

Daily fundamentals:
Long cache

Historical prices:
Persistent database

Options:
Short cache

Intraday:
Short TTL

---

# 35. FAILURE HANDLING

If one provider fails:

Example:

Unusual Whales unavailable

The system should NOT crash.

Instead:

Options Score:
UNAVAILABLE

Overall confidence:
Reduced

Decision:
Potentially HOLD/WATCH depending on rules

Log provider failure.

---

# 36. COST CONTROL

Track API usage.

Create provider metrics:

- API requests
- Errors
- Latency
- Cache hit rate
- Data volume
- Estimated cost where possible

Avoid unnecessary API calls.

Use batch endpoints where supported.

---

# 37. SECURITY

Never commit:

- API keys
- Secrets
- Tokens

Use:

.env
secret manager
deployment environment variables

Add .env.example.

---

# 38. TESTING

Implement:

## Unit Tests

- Indicators
- Feature calculations
- Scores
- Position sizing
- Risk calculations

## Integration Tests

- yfinance adapter
- FMP adapter
- Unusual Whales adapter

Mock external APIs.

Do NOT require live provider credentials for normal CI tests.

## ML Tests

Verify:

- No future data leakage
- Feature timestamps
- Reproducibility
- Train/test separation

## Backtesting Tests

Use deterministic sample data.

---

# 39. OBSERVABILITY

Add logging for:

- Provider requests
- Provider failures
- Data quality
- Feature generation
- ML predictions
- Decisions
- Signal changes
- Trades
- Model versions

Never log API secrets.

---

# 40. UI

Enhance the stock detail page.

Show:

## AI Overview

Decision:
BUY / HOLD / SELL / WATCH

Confidence:
86%

Expected Return:
+8.4%

Risk:
MEDIUM

Risk/Reward:
2.7

---

## Scorecard

Technical: 87
Momentum: 81
Fundamental: 93
Options: 76
Squeeze: 68
Market: 79
Risk: 61

---

## AI Reasoning

Positive factors:
...

Negative factors:
...

What changed:
...

---

## Signal History

Show:

BUY → HOLD → BUY

with timestamps and reasons.

---

# 41. PAPER TRADING

All automated decisions must initially operate in paper-trading mode.

Support:

- Initial capital
- Position sizing
- Orders
- Fills
- Slippage
- Commissions
- P/L

Compare portfolio performance with:

- SPY
- QQQ
- relevant sector ETF
- HSI for HK where applicable

---

# 42. RISK CONTROLS

Default safeguards:

- Max risk per trade: 1%
- Max position: 10%
- Max sector exposure: 25%
- Maximum portfolio drawdown threshold
- Maximum daily loss
- Maximum number of concurrent positions

Make all configurable.

The system must be able to refuse a BUY recommendation because of portfolio risk.

---

# 43. DO NOT DO THESE THINGS

Do NOT:

- Guarantee profits
- Guarantee 70% win rate
- Optimize only for win rate
- Use future information
- Use random train/test splits for time-series data
- Overfit historical data
- Automatically promote ML models
- Automatically execute real trades
- Treat unusual call activity as guaranteed bullish
- Treat high short interest as guaranteed squeeze
- Treat AI/LLM reasoning as quantitative evidence
- Hide uncertainty
- Fill missing data with fabricated values

---

# 44. PERFORMANCE SCORECARD

Every strategy/model must report:

Primary:

1. Profit Factor
2. Sharpe Ratio
3. Sortino Ratio
4. Maximum Drawdown
5. Out-of-Sample Return

Secondary:

6. CAGR
7. Win Rate
8. Average Winner
9. Average Loser
10. Number of Trades
11. Exposure
12. Calmar Ratio

Also report:

- Bull-market performance
- Bear-market performance
- Sideways-market performance
- High-volatility performance

---

# 45. MODEL ACCEPTANCE CRITERIA

A new model must NOT replace the production model merely because it has higher historical returns.

Require improvement in multiple dimensions:

- Better or comparable profit factor
- Better or comparable Sharpe/Sortino
- Controlled maximum drawdown
- Strong out-of-sample results
- Stable performance across market regimes
- No evidence of leakage/overfitting

Prefer robust models over spectacular backtests.

---

# 46. DEVELOPMENT PROCESS

Follow this process:

PHASE 1
Analyze existing codebase.

PHASE 2
Document current architecture.

PHASE 3
Identify existing yfinance integration.

PHASE 4
Create provider abstraction.

PHASE 5
Implement/refactor yfinance adapter.

PHASE 6
Implement FMP adapter.

PHASE 7
Implement Unusual Whales adapter.

PHASE 8
Create normalized data model.

PHASE 9
Create feature engine.

PHASE 10
Create Technical/Options/Squeeze scores.

PHASE 11
Create ML prediction engine.

PHASE 12
Create AI Decision Engine.

PHASE 13
Create signal-change engine.

PHASE 14
Create backtesting/walk-forward validation.

PHASE 15
Create paper trading.

PHASE 16
Create performance dashboard.

PHASE 17
Create model evaluation/retraining pipeline.

Do not attempt to implement everything in one huge uncontrolled change.

---

# 47. FIRST TASK

Before changing code:

1. Inspect the entire repository.
2. Identify:
   - Backend
   - Frontend
   - Database
   - APIs
   - Existing yfinance integration
   - Existing technical indicators
   - Existing stock scoring
   - Existing AI/ML code
   - Existing watch/plan/trade workflow
3. Identify reusable components.
4. Identify technical debt.
5. Identify missing tests.
6. Produce a proposed architecture.
7. Produce a migration plan.
8. Identify files that will be modified.
9. Identify new files.
10. Identify database migrations.

DO NOT start coding until you have completed this assessment.

Then implement Phase 1 and Phase 2 first.

After each major phase:
- Run tests
- Check for regressions
- Explain what changed
- Report remaining work

---

# 48. FINAL OBJECTIVE

Transform the current platform from:

WATCH
→ RESEARCH
→ ENTRY/STOP SUGGESTION

into:

DATA
→ FEATURES
→ TECHNICAL SIGNALS
→ OPTIONS FLOW
→ SHORT/SQUEEZE ANALYSIS
→ FUNDAMENTALS
→ ML PREDICTION
→ AI DECISION
→ RISK MANAGEMENT
→ PAPER TRADE
→ PERFORMANCE
→ LEARNING

The ultimate goal is NOT to maximize the number of BUY signals.

The goal is to produce fewer, higher-quality decisions with:

- Strong profit factor
- High risk-adjusted return
- Controlled maximum drawdown
- Robust out-of-sample performance
- Consistent performance across market regimes
- Transparent reasoning
- Reproducible results

Win rate is important, but it is only ONE metric.

Build the system like a serious quantitative trading platform, not like a stock-picking chatbot.
```

### One architectural decision I'd strongly recommend

Have Claude preserve **yfinance** initially rather than replacing it. Your first production data stack should be:

**yfinance → price/history**
**FMP → fundamentals/earnings/company data**
**Unusual Whales → options/flow/squeeze intelligence**

Then your own platform owns the important IP:

**Feature Engine → Scoring → ML → Decision Engine → Risk Management → Backtesting**

That way, if you later decide that yfinance isn't reliable enough for production, you can swap it for Twelve Data, Polygon/Massive, Databento, etc. **without changing your AI/ML or trading logic.**

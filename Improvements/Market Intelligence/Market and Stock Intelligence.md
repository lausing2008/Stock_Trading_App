# IMPLEMENT MARKET & STOCK INTELLIGENCE ENGINE

You are acting as a Principal Quant Engineer, Senior Trading Systems Architect, Data Engineer, and Full-Stack Engineer.

You are working inside an EXISTING AI Stock Trading Platform.

Your job is to inspect the current repository, understand the architecture, reuse existing capabilities, identify gaps, and implement a production-quality **Market & Stock Intelligence Engine**.

Do NOT blindly rebuild functionality that already exists.

Do NOT make large architectural changes until you understand the existing system.

The objective is to produce reliable, explainable analysis for:

1. Overall market direction
2. Sector and industry direction
3. Individual stock direction
4. Earnings and fundamental momentum
5. Valuation
6. Technical trends
7. Relative strength
8. News and catalysts
9. Options positioning
10. Risk
11. Conditional trade setups
12. Stock-vs-options strategy selection
13. Portfolio-level exposure and correlation

The primary trading horizons are:

- 1–5 trading days
- 1–4 weeks
- 1–3 months

The system must analyze these horizons independently.

The platform should NOT simply output BUY / SELL predictions.

It must answer:

> What is happening?
> Why is it happening?
> What evidence supports the thesis?
> What contradicts it?
> What conditions would confirm it?
> What conditions would invalidate it?
> What is the risk?
> What is the most capital-efficient way to express the thesis?

---

# 0. FIRST: AUDIT THE EXISTING REPOSITORY

Before writing code, inspect the entire repository.

Identify:

- languages/frameworks
- frontend architecture
- backend architecture
- database/storage
- existing APIs
- market-data providers
- fundamental-data providers
- news providers
- options providers
- broker integrations
- caching
- schedulers
- background jobs
- alerting
- ML models
- AI/LLM integrations
- feature-engineering modules
- technical indicators
- portfolio management
- paper trading
- backtesting
- authentication
- configuration
- secrets handling
- tests
- Docker/deployment
- monitoring/logging

Specifically search for existing integrations with:

- yfinance
- Unusual Whales
- Financial Modeling Prep
- Finnhub
- broker APIs
- news/sentiment APIs

Reuse existing provider abstractions whenever possible.

Do not duplicate existing functionality.

---

# 1. CREATE AN IMPLEMENTATION GAP ANALYSIS

Create:

/docs/YYYY-MM-DD/market-stock-intelligence-gap-analysis.md

Document:

## Existing Capabilities

What already exists?

## Missing Capabilities

What needs implementation?

## Weak Capabilities

What exists but needs improvement?

## Data Gaps

What information cannot currently be obtained?

## Technical Debt

What should be cleaned up?

## Proposed Architecture

Show how the new intelligence engine fits into the existing platform.

## Implementation Phases

Break work into logical, testable phases.

Then begin implementation.

Do not stop after writing the document unless there is a genuine blocker.

---

# 2. TARGET ARCHITECTURE

Prefer this logical architecture unless the existing architecture provides a better equivalent:

Market Data
↓
Normalized Data Layer
↓
Feature Engine
↓
┌──────────────────────────────────────┐
│ Market Regime Engine                 │
│ Sector/Industry Engine               │
│ Fundamental Engine                   │
│ Earnings Intelligence               │
│ Estimate Revision Engine             │
│ Valuation Engine                     │
│ Technical Engine                     │
│ Relative Strength Engine             │
│ Options Intelligence                 │
│ News/Catalyst Engine                 │
│ Short Interest Engine                │
│ Risk Engine                          │
└──────────────────────────────────────┘
↓
Evidence Aggregation Engine
↓
Multi-Timeframe Analysis
↓
Scenario Engine
↓
Trade Setup Engine
↓
Portfolio Risk Engine
↓
AI Explanation Layer
↓
Dashboard / Alerts / API

IMPORTANT:

The LLM must NOT calculate indicators that deterministic code can calculate.

Use deterministic code for:

- technical indicators
- returns
- volatility
- valuation calculations
- position sizing
- option payoff calculations
- correlations
- exposure calculations
- scoring inputs

Use the LLM primarily for:

- summarization
- evidence synthesis
- contradiction analysis
- management commentary interpretation
- news interpretation
- scenario explanations

---

# 3. DATA PROVENANCE IS MANDATORY

Every important data point must contain metadata such as:

source
source_timestamp
retrieved_at
market_session
is_stale
confidence
units

Never silently mix stale and current information.

The report must begin with:

DATA QUALITY

Market data as of:
Fundamentals as of:
Latest earnings:
Options data as of:
News updated:
Missing sources:
Stale sources:

If required information is unavailable:

DO NOT FABRICATE IT.

Mark it:

UNKNOWN
UNAVAILABLE
STALE

---

# 4. MARKET REGIME ENGINE

Implement analysis for:

SPY
QQQ
IWM
DIA
SMH
SOXX

Calculate:

1D return
5D return
20D return
63D return

20 EMA
50 EMA
100 SMA
200 SMA

Moving-average slopes

RSI(14)
MACD
MACD histogram
ADX
ATR

Volume
20-day average volume
Relative volume

52-week high
52-week low

Market structure:

Higher High / Higher Low
Lower High / Lower Low
Range

Determine separately:

1–5 day trend
1–4 week trend
1–3 month trend

Allowed states:

STRONG_BULLISH
BULLISH
NEUTRAL
BEARISH
STRONG_BEARISH

Do not force all horizons into the same classification.

---

# 5. MARKET BREADTH

Implement when data is available:

Advance/Decline

% stocks above:
20 DMA
50 DMA
200 DMA

52-week highs
52-week lows

RSP/SPY

QQEW/QQQ

IWM/SPY

SOXX/QQQ

SMH/QQQ

Detect divergences such as:

QQQ making new highs
BUT
percentage of Nasdaq stocks above 50 DMA declining.

Generate:

breadth_status

STRONG
IMPROVING
NEUTRAL
DETERIORATING
WEAK

And:

breadth_divergence = true/false

---

# 6. VOLATILITY / RISK REGIME

Analyze where data exists:

VIX
VIX9D
VVIX
VIX term structure
SKEW
Put/Call ratios

Credit:

HYG
LQD
credit spreads if available

Output:

LOW_VOLATILITY
NORMAL
ELEVATED
EXTREME

And:

RISK_ON
NEUTRAL
RISK_OFF

---

# 7. MACRO ENGINE

Collect and analyze:

Federal Funds Rate

Fed expectations

2Y Treasury
10Y Treasury
30Y Treasury

2Y–10Y spread

Real yields

DXY

CPI
Core CPI

PCE
Core PCE

GDP

Unemployment

Nonfarm Payrolls

Initial Jobless Claims

ISM Manufacturing

ISM Services

Retail Sales

Consumer Confidence

Determine:

growth_trend

ACCELERATING
STABLE
SLOWING

inflation_trend

ACCELERATING
STABLE
DECLINING

fed_stance

DOVISH
NEUTRAL
HAWKISH

macro_equity_effect

BULLISH
NEUTRAL
BEARISH

growth_stock_effect

BULLISH
NEUTRAL
BEARISH

---

# 8. LIQUIDITY ENGINE

Where data exists, analyze:

Fed balance sheet
Treasury General Account
Reverse Repo
Money supply
Financial conditions
DXY
credit conditions

Output:

IMPROVING
NEUTRAL
TIGHTENING

Explain what is driving the classification.

---

# 9. SECTOR ROTATION

Analyze:

XLK
XLF
XLE
XLV
XLI
XLY
XLP
XLU
XLB
XLRE
SMH
SOXX

Calculate:

1D
1W
1M
3M performance

Relative strength vs SPY

Relative strength vs QQQ

Rank:

leaders
improving
neutral
weakening
laggards

Detect:

RISK_ON_ROTATION
DEFENSIVE_ROTATION
MIXED_ROTATION

---

# 10. SEMICONDUCTOR INTELLIGENCE MODULE

This is especially important.

Track:

NVDA
AVGO
AMD
MU
TSM
ASML
ARM
MRVL
INTC
AMAT
LRCX
KLAC
AAOI
SMTC

Also support:

DRAM — Roundhill Memory ETF
SOXL
SK Hynix when data access permits.

Track:

SOXX/QQQ
SMH/QQQ

Analyze semiconductor subgroups separately:

AI Accelerators
Memory/HBM
Foundry
Networking
Optical
Semiconductor Equipment
Legacy/Turnaround

Example mappings:

NVDA → AI accelerator
MU → Memory/HBM
SK Hynix → Memory/HBM
TSM → Foundry
MRVL → Networking/custom silicon
AAOI → Optical
SMTC → Connectivity/semiconductor
AMAT/LRCX/KLAC → Equipment

Do NOT treat semiconductor stocks as one homogeneous group.

---

# 11. MEMORY / HBM INTELLIGENCE

Create a dedicated module for:

MU
SK Hynix
DRAM ETF

Track when data is available:

DRAM pricing
NAND pricing
HBM demand
HBM pricing
HBM capacity
customer qualification
inventory
capex
utilization
gross-margin trends

Determine:

MEMORY_CYCLE

EARLY_RECOVERY
EXPANSION
PEAK
CONTRACTION
TROUGH
UNKNOWN

Explain evidence.

Never infer memory pricing without actual data.

---

# 12. COMPANY FUNDAMENTAL ENGINE

For every company calculate/store:

Revenue
Revenue YoY
Revenue QoQ

EPS
EPS growth

Gross margin
Operating margin
Net margin

Operating cash flow

Free cash flow
FCF margin

ROE
ROIC

Cash
Debt
Net debt

Debt/equity

Share count

Share dilution

Stock-based compensation

Determine each trend:

IMPROVING
STABLE
DETERIORATING

---

# 13. EARNINGS INTELLIGENCE

Store multiple quarters.

For each earnings event capture:

Revenue actual
Revenue estimate
Revenue surprise

EPS actual
EPS estimate
EPS surprise

Guidance

Gross margin
Operating margin

FCF

Bookings
Backlog
Inventory
CapEx

Extract management commentary regarding:

demand
pricing
customers
competition
product adoption
supply
capacity
industry outlook
risks

Determine:

guidance_status

RAISED
MAINTAINED
LOWERED
UNKNOWN

earnings_momentum

STRONG_POSITIVE
POSITIVE
NEUTRAL
NEGATIVE
STRONG_NEGATIVE

---

# 14. ESTIMATE REVISION ENGINE

This is a high-priority feature.

Track analyst estimates historically.

Compare:

7 days
30 days
90 days

For:

Revenue
EPS

Calculate:

revision breadth
revision magnitude
number raising
number lowering

Output:

STRONG_POSITIVE
POSITIVE
NEUTRAL
NEGATIVE
STRONG_NEGATIVE

Do not substitute analyst price targets for earnings revisions.

---

# 15. COMPANY STATUS ENGINE

Analyze:

Product launches
Product delays

Technology progress

Customer wins
Customer losses

Major contracts

Market share

Pricing power

Competitive position

Management execution

Capital allocation

Insider transactions

Institutional ownership

Industry cycle

TAM

Supply/demand

Regulatory developments

Competitor earnings

Create structured events.

Example:

{
  "type": "CUSTOMER_WIN",
  "company": "XYZ",
  "importance": "HIGH",
  "time_horizon": "MEDIUM_TERM",
  "source": "...",
  "timestamp": "...",
  "summary": "..."
}

---

# 16. VALUATION ENGINE

Support multiple valuation models.

Profitable companies:

Forward P/E
PEG
EV/EBITDA
EV/FCF
FCF yield

High-growth:

EV/Sales
Revenue growth
Gross margin
FCF margin
Rule of 40

Semiconductors:

Forward P/E
Normalized P/E
EV/EBITDA
Price/Book
FCF
cycle-adjusted earnings

Compare:

current
3-year history
5-year history
industry peers

Output:

CHEAP
FAIR
ELEVATED
EXPENSIVE

But always include explanation.

Do NOT conclude:

Low P/E = cheap.

---

# 17. TECHNICAL ENGINE

Analyze multiple timeframes:

Weekly
Daily
4H
1H

Calculate:

20 EMA
50 EMA
100 SMA
200 SMA

RSI
MACD
MACD histogram

ADX
ATR

OBV

VWAP where appropriate

Volume
Relative volume

Support
Resistance

52-week high/low

Recent swing highs/lows

Breakout/breakdown levels

Gap zones

Determine:

trend
momentum
volume state
support/resistance

Detect:

ACCUMULATION
DISTRIBUTION
NEUTRAL_VOLUME

---

# 18. MULTI-TIMEFRAME ENGINE

Never collapse all timeframes into one direction.

Example:

Weekly = BULLISH
Daily = BULLISH
4H = NEUTRAL
1H = BEARISH

Interpretation:

Primary trend remains bullish while short-term momentum is correcting.

This should result in something like:

WAIT_FOR_PULLBACK_CONFIRMATION

rather than automatically:

BUY.

---

# 19. RELATIVE STRENGTH

Compare each stock against:

SPY
QQQ
sector ETF
industry ETF
peer basket

Calculate relative-strength trends over:

5D
20D
63D

Output:

OUTPERFORMING
NEUTRAL
UNDERPERFORMING

And:

IMPROVING
STABLE
DETERIORATING

---

# 20. OPTIONS INTELLIGENCE

Reuse the existing Unusual Whales integration.

Analyze when available:

IV
IV Rank
IV Percentile

Expected move

Call volume
Put volume

Put/Call ratios

Open interest

Unusual activity

Sweeps
Blocks

Premium

Bid/ask execution

Opening vs closing when identifiable

Gamma exposure

Call walls
Put walls

Dealer positioning

Do NOT classify every call purchase as bullish.

Attempt to distinguish:

speculation
hedging
closing transactions
spreads
opening positions

Store the evidence and confidence.

---

# 21. SHORT INTEREST ENGINE

Collect when available:

Short interest

Short interest %

Days to cover

Borrow fee

Shares available

Short-interest change

Fails to deliver

Combine with options positioning.

Output:

LOW
MEDIUM
HIGH

squeeze_risk

Do not automatically classify high short interest as bullish.

---

# 22. NEWS INTELLIGENCE

Every material news item should contain:

headline
source
timestamp
company
sector
category

Categories:

EARNINGS
MACRO
FED
PRODUCT
CUSTOMER
M&A
REGULATION
LEGAL
MANAGEMENT
ANALYST
GEOPOLITICAL
INDUSTRY

Determine:

positive/negative/neutral impact

expected duration:

INTRADAY
DAYS
WEEKS
MONTHS

Determine:

likely priced in?
YES
PARTIAL
NO
UNKNOWN

Compare actual price reaction against expected reaction.

A supposedly positive event followed by heavy selling is meaningful evidence.

---

# 23. CATALYST CALENDAR

Track:

Earnings

Investor days

Product launches

FDA/regulatory events

Industry conferences

Fed meetings

CPI

PCE

Employment reports

Options expiration

Monthly OPEX

Quarterly OPEX

Other company-specific events

Calculate:

days_until_event

Highlight events occurring inside the planned holding period.

---

# 24. EVIDENCE ENGINE

This is a critical architectural component.

Do NOT simply create one arbitrary AI score.

Maintain separate evidence buckets:

market
sector
industry
fundamentals
earnings
revisions
valuation
technical
relative strength
options
news
catalysts
risk

For every bucket store:

direction
strength
confidence
evidence
contradictions
timestamp

Example:

{
  "category": "EARNINGS",
  "direction": "BULLISH",
  "strength": "STRONG",
  "confidence": "HIGH",
  "evidence": [
    "Revenue beat",
    "EPS beat",
    "Guidance raised"
  ],
  "contradictions": [
    "Gross margin declined"
  ]
}

Never hide conflicting evidence.

---

# 25. CONFIDENCE MODEL

Confidence should depend on:

data completeness
data freshness
agreement among independent signals
historical reliability
number of contradictions

Confidence:

LOW
MEDIUM
HIGH

Do not produce fake precision such as:

87.43% confidence

unless that probability comes from a properly calibrated and validated statistical model.

---

# 26. SCENARIO ENGINE

Generate:

BULL CASE
BASE CASE
BEAR CASE

For each:

required conditions
catalysts
technical confirmation
invalidation
target zone if supported by data

Do NOT fabricate scenario probabilities.

---

# 27. TRADE SETUP ENGINE

Generate conditional setups.

BREAKOUT

Trigger
Entry zone
Volume confirmation
Stop
Target 1
Target 2
Risk/reward
Invalidation

PULLBACK

Support
Entry zone
Required confirmation
Stop
Target 1
Target 2
Risk/reward
Invalidation

BREAKDOWN

Only when bearish setup exists.

Never recommend chasing far above the defined entry.

---

# 28. POSITION SIZING

Inputs:

portfolio_value
risk_percentage
entry
stop

Calculate:

risk_budget =
portfolio_value * risk_percentage

risk_per_share =
abs(entry - stop)

shares =
floor(risk_budget / risk_per_share)

Return:

shares
capital required
portfolio exposure
planned loss

Also warn:

STOP ORDERS DO NOT GUARANTEE THE PLANNED LOSS.

Gap risk may create larger losses.

---

# 29. STOCK VS OPTIONS ENGINE

Determine whether a thesis is better expressed using:

STOCK

LONG_CALL

CALL_DEBIT_SPREAD

BULL_PUT_SPREAD

COVERED_CALL

PROTECTIVE_PUT

LONG_PUT

PUT_DEBIT_SPREAD

BEAR_CALL_SPREAD

NO_TRADE

Evaluate:

direction
time horizon
IV
IV Rank
theta
vega
liquidity
bid/ask spread
catalysts
earnings
capital efficiency
maximum loss

For options calculate:

expiration
strikes
debit/credit
max profit
max loss
breakeven
delta
theta
vega

Never recommend undefined-risk options strategies by default.

---

# 30. PORTFOLIO CORRELATION ENGINE

This is essential.

The platform must detect hidden concentration.

Example portfolio:

NVDA
MU
TSM
MRVL
AAOI
DRAM
SOXL
SK Hynix

This may appear diversified by ticker count while remaining heavily exposed to:

AI infrastructure
semiconductors
memory
Nasdaq beta

Calculate:

pairwise correlations
sector exposure
industry exposure
factor exposure where possible

Estimate effective exposure from leveraged ETFs.

SOXL must NOT be treated like an ordinary 1x ETF.

Display warnings such as:

HIGH SEMICONDUCTOR CONCENTRATION

MEMORY EXPOSURE OVERLAP

LEVERAGED ETF EXPOSURE

AI CAPEX CORRELATION RISK

---

# 31. PORTFOLIO STRESS TESTING

Implement scenarios such as:

QQQ -5%
QQQ -10%

SOXX -10%
SOXX -20%

NVDA -15%

Memory sector -15%

VIX spike

10Y Treasury +50 bps

AI capex slowdown

DRAM/HBM pricing deterioration

Estimate portfolio impact where statistically reasonable.

Do not pretend these are predictions.

They are stress scenarios.

---

# 32. REPORT OUTPUT

Create a Market Intelligence Report.

Example:

MARKET INTELLIGENCE
===================

Data As Of:

Market Regime:
Risk Regime:

1–5 Days:
1–4 Weeks:
1–3 Months:

Breadth:
Volatility:
Macro:
Liquidity:
Credit:

Leading Sectors:

Weak Sectors:

Semiconductor Trend:

Memory/HBM Trend:

Key Catalysts:

Key Risks:

Evidence Supporting Bull Case:

Evidence Supporting Bear Case:

Conditions That Change The View:

---

Create a Stock Intelligence Report.

STOCK INTELLIGENCE
==================

Ticker:
Company:
Price:
Data As Of:

MARKET
Market Regime:

SECTOR
Sector Trend:

INDUSTRY
Industry Trend:

FUNDAMENTALS
Status:

EARNINGS
Momentum:

REVISIONS
Trend:

VALUATION
Status:

TECHNICAL

Weekly:
Daily:
4H:
1H:

RELATIVE STRENGTH:

OPTIONS:

NEWS:

CATALYSTS:

RISKS:

BULL CASE:

BASE CASE:

BEAR CASE:

DIRECTION

1–5 Days:
1–4 Weeks:
1–3 Months:

CONFIDENCE:

WHY:

CONTRADICTORY EVIDENCE:

WHAT CHANGES THE VIEW:

TRADE STATUS:

ENTER_NOW
WAIT_FOR_BREAKOUT
WAIT_FOR_PULLBACK
HOLD
REDUCE
EXIT
HEDGE
NO_TRADE

ENTRY:

STOP:

TARGET 1:

TARGET 2:

RISK/REWARD:

POSITION SIZE:

PREFERRED VEHICLE:

STOCK / OPTION STRUCTURE

---

# 33. DASHBOARD

Implement or improve the UI.

Create:

MARKET DASHBOARD

Show:

Market Regime

1D / 1W / 1M trend

Breadth

VIX

Rates

Liquidity

Sector Heatmap

Semiconductor Status

Memory/HBM Status

Upcoming Catalysts

Major Risks

---

STOCK DASHBOARD

Show cards for:

Market
Sector
Fundamentals
Earnings
Revisions
Valuation
Technical
Relative Strength
Options
News
Risk

Do NOT hide everything behind one score.

Use colors/icons only as visual aids.

The underlying evidence must always be accessible.

---

# 34. WATCHLIST

Allow users to monitor:

QQQ
MU
NVDA
TSM
AAOI
SOXL
CRWV
GDX
INTC
RKLB
NBIS
PLTR
SMTC
TSLA
SPCX
RVMD
NOW
MRVL
ZS
CRWD
HOOD
DRAM

and additional symbols dynamically.

Do not hardcode analysis logic to these symbols.

---

# 35. ALERT ENGINE

Generate alerts when meaningful conditions occur.

Examples:

Market regime changed

QQQ lost 50 DMA

SOXX relative strength broke down

Stock broke resistance with volume

Stock lost major support

Earnings estimates materially revised

Guidance changed

Major unusual options activity

Major customer announcement

Material insider transaction

Volatility regime changed

Portfolio concentration exceeded limit

Do not spam users with insignificant events.

Implement severity:

INFO
WATCH
IMPORTANT
CRITICAL

---

# 36. HISTORICAL SNAPSHOTS

Store every intelligence report.

We need to know:

What did the system believe?

What data did it have?

What happened afterward?

Store:

timestamp
inputs
features
evidence
direction
confidence
trade status
entry
stop
targets

Then calculate future returns:

1D
5D
10D
20D
60D

This creates the dataset required to evaluate whether the intelligence engine actually works.

---

# 37. PERFORMANCE MEASUREMENT

Measure:

Directional accuracy

Win rate

Average win

Average loss

Profit factor

Expectancy

Sharpe ratio

Sortino ratio

Maximum drawdown

Calmar ratio

Risk-adjusted return

Performance by:

market regime
sector
ticker
strategy
time horizon
confidence level

Do NOT optimize for win rate alone.

A strategy with:

70% wins
but huge losses

may be inferior to:

50% wins
with much better expectancy.

---

# 38. WALK-FORWARD / OUT-OF-SAMPLE TESTING

Avoid look-ahead bias.

Implement:

Train period
Validation period
Test period

Use walk-forward analysis.

Never allow future earnings, revised data, or future news into historical decisions.

Prevent survivorship bias when possible.

Report:

in-sample performance
out-of-sample performance
performance degradation

---

# 39. AI LEARNING LOOP

Do NOT allow the AI to blindly rewrite its own trading logic.

Instead:

Store predictions
Store evidence
Store outcomes

Analyze failures.

Determine:

Which indicators added value?

Which signals generated false positives?

Which regimes produced poor results?

Propose model changes.

Backtest changes.

Validate out-of-sample.

Only promote improvements after validation.

Use model/version tracking.

---

# 40. RISK GUARDRAILS

Implement configurable limits:

max risk per trade

max portfolio exposure

max sector exposure

max semiconductor exposure

max leveraged ETF exposure

max options premium at risk

max correlated exposure

max daily loss

max drawdown

The system should be able to block a proposed trade if portfolio risk limits are violated.

---

# 41. DATABASE / DATA MODEL

Design schemas for at least:

market_snapshots

market_regimes

sector_snapshots

stock_snapshots

fundamental_snapshots

earnings_events

estimate_revisions

technical_features

options_snapshots

news_events

catalysts

evidence

analysis_reports

trade_setups

portfolio_exposures

alerts

predictions

prediction_outcomes

model_versions

backtest_runs

Use the existing database technology when appropriate.

Add migrations rather than destructive schema changes.

---

# 42. API DESIGN

Expose clean APIs such as:

GET /api/intelligence/market

GET /api/intelligence/market/history

GET /api/intelligence/stock/{ticker}

GET /api/intelligence/stock/{ticker}/history

GET /api/intelligence/stock/{ticker}/evidence

GET /api/intelligence/stock/{ticker}/technical

GET /api/intelligence/stock/{ticker}/earnings

GET /api/intelligence/stock/{ticker}/options

GET /api/intelligence/portfolio/risk

GET /api/intelligence/catalysts

GET /api/intelligence/alerts

POST /api/intelligence/analyze

Adapt these routes to existing project conventions.

---

# 43. CACHING / RATE LIMITING

External financial APIs may have expensive or limited quotas.

Implement:

provider-specific caching

TTL by data type

request deduplication

rate limiting

retry with exponential backoff

circuit breakers where appropriate

graceful fallback

Example TTL:

quotes → seconds/minutes

technical history → minutes

fundamentals → hours/day

earnings → until new report

macro → based on release schedule

Do not repeatedly call paid APIs for unchanged information.

---

# 44. OBSERVABILITY

Implement structured logging.

Log:

provider
endpoint
latency
success/failure
cache hit/miss
rate-limit status
analysis duration
missing data
stale data

Add health checks.

Never log API secrets.

---

# 45. TESTING

Implement:

Unit tests

Integration tests

Data validation tests

Indicator tests

Position sizing tests

Options payoff tests

API tests

Provider failure tests

Stale-data tests

Backtesting tests

Look-ahead bias tests

Use deterministic fixtures.

---

# 46. SECURITY

Never commit:

API keys
broker tokens
passwords
private keys

Use existing environment configuration.

Validate ticker/user input.

Protect expensive API endpoints.

Do not allow LLM output to directly execute broker trades.

---

# 47. TRADING SAFETY ARCHITECTURE

Maintain strict separation:

ANALYSIS
↓
SIGNAL
↓
PROPOSED TRADE
↓
RISK VALIDATION
↓
PAPER TRADE
↓
PERFORMANCE VALIDATION
↓
USER/BROKER AUTHORIZATION
↓
LIVE ORDER

Never allow:

LLM text → direct broker order

without deterministic validation and explicit authorization controls.

---

# 48. IMPLEMENTATION ORDER

Implement in phases.

PHASE 1

Repository audit
Gap analysis
Data provenance
Normalized models

PHASE 2

Market regime
Technical engine
Sector rotation
Relative strength

PHASE 3

Fundamentals
Earnings
Estimate revisions
Valuation

PHASE 4

News
Catalysts
Options
Short interest

PHASE 5

Evidence aggregation
Multi-timeframe analysis
Scenario engine
Trade setups

PHASE 6

Portfolio correlation
Position sizing
Stress testing
Risk controls

PHASE 7

Dashboard
Reports
Alerts

PHASE 8

Historical snapshots
Backtesting
Performance measurement
Walk-forward testing

PHASE 9

AI learning/evaluation loop

After each phase:

run tests

fix regressions

update documentation

commit logically separated changes if repository workflow allows.

---

# 49. DOCUMENTATION

Create documentation under:

/docs/YYYY-MM-DD/

Include:

market-stock-intelligence-gap-analysis.md

architecture.md

data-sources.md

market-regime-engine.md

stock-intelligence-engine.md

semiconductor-intelligence.md

options-intelligence.md

risk-engine.md

backtesting.md

api.md

implementation-summary.md

Document:

what was changed

why

files changed

database changes

API changes

configuration changes

testing performed

remaining limitations

next recommended improvements

---

# 50. FINAL ACCEPTANCE CRITERIA

The implementation is complete only when I can select a ticker such as:

NVDA
MU
TSM
MRVL
AAOI
PLTR
TSLA
CRWV
DRAM

and receive a report containing:

Freshness/data-quality status

Market direction

Sector direction

Industry direction

Company fundamentals

Latest earnings analysis

Estimate revisions

Company/business status

Valuation

Technical trend

Multi-timeframe trend

Relative strength

Options intelligence when available

News/catalysts

Risk factors

Bull/base/bear scenarios

Conditional entry setup

Stop/invalidation

Targets

Risk/reward

Position size

Stock-vs-options comparison

1–5 day direction

1–4 week direction

1–3 month direction

Confidence

Supporting evidence

Contradictory evidence

Conditions that change the thesis

AND:

The platform can generate a Market Intelligence Report explaining:

Current market regime

Risk-on/risk-off state

Market breadth

Volatility

Macro

Liquidity

Sector rotation

Semiconductor trend

Memory/HBM trend

Major catalysts

Major risks

AND:

Every important conclusion can be traced back to timestamped source data.

---

# 51. CRITICAL ENGINEERING PRINCIPLES

Follow these rules throughout implementation:

1. Never fabricate missing financial data.

2. Never silently use stale data.

3. Never treat an LLM opinion as market data.

4. Prefer deterministic calculations over LLM calculations.

5. Keep raw data separate from derived features.

6. Keep derived features separate from AI interpretation.

7. Preserve contradictory evidence.

8. Do not use one arbitrary overall score as the decision engine.

9. Analyze different time horizons independently.

10. Do not optimize for win rate alone.

11. Evaluate expected value and downside risk.

12. Prevent look-ahead bias.

13. Backtest before trusting new signals.

14. Validate out-of-sample.

15. Account for transaction costs and slippage.

16. Account for options spreads and liquidity.

17. Treat leveraged ETFs such as SOXL differently from ordinary ETFs.

18. Detect correlated portfolio exposure.

19. Do not allow AI-generated text to directly execute trades.

20. Capital preservation takes precedence over maximizing trade frequency.

---

# 52. START NOW

Begin by auditing the repository.

Do not immediately start creating new modules.

First determine:

1. What already exists?
2. What can be reused?
3. What is missing?
4. Which current components are unreliable?
5. Which data providers can supply each required feature?
6. Which required features currently have no reliable data source?
7. What is the lowest-risk implementation path?

Create the gap-analysis document.

Then implement the system phase-by-phase.

Do not stop at recommendations.

Write the production code, migrations, tests, APIs, UI changes and documentation necessary to integrate this into the existing platform.

At the end, run the relevant test suites and provide:

IMPLEMENTED
PARTIALLY IMPLEMENTED
NOT IMPLEMENTED
BLOCKED

for every major capability.

For anything PARTIALLY IMPLEMENTED or BLOCKED, explain exactly:

- what is missing
- why
- what data/API/dependency is required
- the recommended next step

Finally provide a concise implementation summary containing:

Files created
Files modified
Database migrations
New APIs
New UI components
New scheduled jobs
New configuration
Tests added
Test results
Known limitations
Recommended next phase
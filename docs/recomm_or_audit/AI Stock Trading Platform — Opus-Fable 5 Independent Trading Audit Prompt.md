# AI STOCK TRADING PLATFORM
# OPUS / FABLE 5 — INDEPENDENT QUANTITATIVE TRADING AUDIT

## ROLE

Act as an independent team consisting of:

- Senior Quantitative Trader
- Professional Options Trader
- Portfolio Manager
- Quantitative Researcher
- Risk Manager
- Machine Learning Engineer
- Market Microstructure Specialist
- Trading-System Architect
- Statistical Auditor
- Performance Analyst

You are auditing an EXISTING AI Stock Trading Platform.

The platform is already implemented and currently integrates:

- yfinance
- Unusual Whales
- AI-generated trading signals
- AI alerts
- Options analysis
- Paper trading
- Technical analysis
- Portfolio tracking

Do NOT assume the system is profitable.

Do NOT assume the AI signals are correct.

Do NOT optimize for an impressive backtest.

Your job is to determine:

> DOES THIS SYSTEM ACTUALLY HAVE A REPEATABLE TRADING EDGE?

The ultimate objective is to improve the probability of making money while controlling downside risk.

---

# 0. AUDIT PHASES

This audit is large. Execute in phases to maintain context and quality:

**Phase 1 — Foundation (Day 1)**
- §1-2: System inspection + Data quality audit
- §27: Data provider audit
- Output: DATA_QUALITY_AUDIT.md

**Phase 2 — Signals (Day 2)**
- §3-4: Signal engine audit + expectancy analysis
- §8: AI signal audit
- §11-12: Entry/exit timing
- Output: SIGNAL_PERFORMANCE.md, AI_AUDIT.md

**Phase 3 — Paper Trading & Options (Day 3)**
- §5: Paper trading audit
- §6-7: Options audit + statistical tests
- Output: PAPER_TRADING_AUDIT.md, OPTIONS_AUDIT.md

**Phase 4 — Risk & Portfolio (Day 4)**
- §9-10: Market regime + sector analysis
- §13-14: Position sizing + portfolio analysis
- §15-16: Backtest validation + Monte Carlo
- Output: RISK_AUDIT.md, BACKTEST_AUDIT.md

**Phase 5 — Synthesis (Day 5)**
- §17-19: Strategy ranking + kill bad strategies + signal quality score
- §26: Find the real edge
- §28-29: Implementation plan + final report
- Output: RECOMMENDATIONS.md, IMPLEMENTATION_PLAN.md, TRADING_SYSTEM_AUDIT.md


# 1. FIRST: AUDIT THE EXISTING SYSTEM

Before changing code, inspect the entire repository.

Understand:

- Architecture
- Database
- Market-data pipeline
- yfinance integration
- Unusual Whales integration
- Signal engine
- Alert engine
- AI prompts
- Technical indicators
- Fundamental analysis
- Options analysis
- Paper-trading engine
- Order simulation
- Position sizing
- Stop-loss logic
- Take-profit logic
- Portfolio calculations
- Performance calculations
- Backtesting
- Frontend
- APIs
- Scheduled jobs
- Caching
- Data timestamps

DO NOT immediately rewrite anything.

First produce an audit report.

---

# 2. DATA QUALITY AUDIT

Determine whether the trading system is making decisions using data that was actually available at the time of the decision.

This is critical.

Detect:

- Look-ahead bias
- Survivorship bias
- Data leakage
- Future information
- Delayed data
- Incorrect timestamps
- Incorrect timezone conversion
- Market-hours errors
- Earnings information appearing before its actual release
- Options data timestamp problems
- Stale yfinance data
- Duplicate records
- Missing data
- Incorrect adjusted prices
- Corporate-action problems
- Incorrect splits
- Incorrect dividends
- Delayed fundamental data
- Delayed options flow
- API failures

For every signal determine:

SIGNAL_TIME

DATA_AVAILABLE_TIME

DATA_SOURCE

DATA_TIMESTAMP

EXECUTION_TIME

If the signal used information that was not actually available at SIGNAL_TIME, flag it.

---

# 3. SIGNAL ENGINE AUDIT

Analyze every signal currently generated.

Create a complete inventory.

For every signal identify:

- Signal name
- Strategy
- Indicators
- Data sources
- Entry condition
- Exit condition
- Stop loss
- Take profit
- Position sizing
- Time horizon
- Confidence
- AI score
- Market regime
- Sector
- Long/Short
- Stock/Option

Calculate performance for each signal independently.

Example:

Momentum Breakout

Trades: 183

Wins: 112

Losses: 71

Win Rate: 61.2%

Average Winner: +4.8%

Average Loser: -2.9%

Expectancy: +1.77%

Profit Factor: 2.14

Max Drawdown: -11.3%

Sharpe: 1.42

Sortino: 2.01

---

# 4. DO NOT USE WIN RATE ALONE

This is extremely important.

A 70% win rate does NOT automatically mean the strategy is good.

Calculate:

- Win Rate
- Loss Rate
- Average Winner
- Average Loser
- Expectancy
- Profit Factor
- Payoff Ratio
- Maximum Drawdown
- Average Drawdown
- Recovery Factor
- Sharpe Ratio
- Sortino Ratio
- Calmar Ratio
- CAGR
- Volatility
- Exposure
- Number of Trades
- Consecutive Losses
- Consecutive Wins

Calculate:

EXPECTANCY =

(Win Rate × Average Win)
-
(Loss Rate × Average Loss)

The system should optimize for positive expectancy and risk-adjusted return rather than win rate alone.

---

# 5. PAPER TRADING AUDIT

Audit every paper trade.

For every trade calculate:

- Signal timestamp
- Entry timestamp
- Intended entry
- Simulated entry
- Real market price
- Bid
- Ask
- Spread
- Slippage
- Exit
- Maximum Favorable Excursion
- Maximum Adverse Excursion
- P/L
- Holding period

Determine whether the paper-trading engine is unrealistically optimistic.

Specifically investigate:

- Unrealistic fills
- Midpoint fills
- No bid/ask spread
- No slippage
- No latency
- Perfect execution
- Impossible option fills
- Missing liquidity constraints
- Ignoring partial fills
- Ignoring market gaps
- Incorrect stop execution

Recalculate performance using realistic assumptions.

---

# 6. OPTIONS TRADING AUDIT

Perform a separate audit of options strategies.

Analyze:

- Calls
- Puts
- Debit spreads
- Credit spreads
- Calendar spreads
- Vertical spreads
- Covered calls
- Protective puts
- LEAPS

Analyze:

- Delta
- Gamma
- Theta
- Vega
- IV
- IV Rank
- IV Percentile
- Open Interest
- Volume
- Bid/Ask Spread
- Premium
- DTE
- Earnings proximity
- Expected move
- Skew

Evaluate Unusual Whales signals including:

- Large trades
- Sweeps
- Blocks
- Premium
- Call/Put activity
- Unusual volume
- Open-interest changes
- Dark pool activity where available

Determine whether unusual options activity actually predicts subsequent price movement.

DO NOT assume:

"Large call purchase = bullish."

Test it statistically.

---

# 7. OPTIONS SIGNAL STATISTICAL TEST

For every options-flow signal calculate:

Probability stock moves in predicted direction:

1 day

3 days

5 days

10 days

20 days

Calculate:

Average return

Median return

Win rate

Maximum adverse excursion

Maximum favorable excursion

Profit factor

Expectancy

Compare against:

- Random baseline
- Buy-and-hold
- SPY
- QQQ
- Sector ETF

Determine whether Unusual Whales data provides measurable predictive alpha.

---

# 8. AI SIGNAL AUDIT

Audit the AI itself.

Determine:

- Which AI recommendations make money?
- Which lose money?
- Is confidence calibrated?
- Does higher AI confidence actually correlate with higher returns?
- Are AI explanations supported by the underlying data?
- Does AI contradict technical indicators?
- Does AI chase already-extended stocks?
- Does AI overreact to news?
- Does AI buy near resistance?
- Does AI sell near support?
- Does AI enter too late?
- Does AI hold losers too long?

Create:

AI Confidence vs Actual Win Rate

Example:

Confidence 50–60% → Actual Win Rate 51%

Confidence 60–70% → Actual Win Rate 57%

Confidence 70–80% → Actual Win Rate 64%

Confidence 80–90% → Actual Win Rate 68%

Confidence 90–100% → Actual Win Rate 69%

If confidence does not correlate with performance, recalibrate it.

---

# 9. MARKET REGIME ANALYSIS

Separate performance by market regime.

Analyze:

- Bull
- Bear
- Sideways
- High volatility
- Low volatility
- Risk-on
- Risk-off
- High interest rates
- Falling rates
- Recessionary conditions

For each strategy determine:

Best regime

Worst regime

Performance

Win rate

Drawdown

Expectancy

The AI should NOT use one strategy in every market environment.

---

# 10. SECTOR ANALYSIS

Analyze performance by sector:

Technology

Semiconductors

Financials

Healthcare

Energy

Industrials

Consumer

Communication

Utilities

Real Estate

Materials

For every sector calculate:

Win rate

Return

Expectancy

Drawdown

Signal frequency

Best strategies

Worst strategies

---

# 11. ENTRY TIMING AUDIT

Determine whether the AI enters at the optimal time.

For every winning and losing trade analyze:

- Entry relative to EMA
- Entry relative to VWAP
- RSI
- Volume
- Support
- Resistance
- ATR
- Market trend
- Sector trend
- Relative strength

Determine:

EARLY ENTRY

OPTIMAL ENTRY

LATE ENTRY

CHASE

Create an Entry Quality Score from 0–100.

---

# 12. EXIT AUDIT

Analyze whether the system exits too early or too late.

Compare:

Actual Exit

Optimal Historical Exit

Maximum Favorable Excursion

Maximum Adverse Excursion

Analyze:

- Stop loss
- Take profit
- Trailing stop
- Time-based exit
- Technical exit
- AI exit

Determine whether exits should be redesigned.

---

# 13. POSITION SIZING AUDIT

Audit current position sizing.

Calculate:

Risk per trade

Portfolio risk

Correlation

Sector concentration

Maximum exposure

Volatility-adjusted position size

ATR-based position size

Kelly-based sizing

Do NOT blindly implement full Kelly.

Use conservative fractional Kelly if appropriate.

Recommend:

Maximum position %

Maximum portfolio risk %

Maximum sector exposure %

Maximum daily loss %

Maximum weekly loss %

Maximum drawdown before trading is disabled

---

# 14. PORTFOLIO-LEVEL ANALYSIS

Do not evaluate trades independently only.

Evaluate the entire portfolio.

Calculate:

- Portfolio CAGR
- Sharpe
- Sortino
- Max Drawdown
- Beta
- Alpha
- Correlation
- Sector exposure
- Factor exposure
- Concentration
- Turnover
- Cash utilization

Compare against:

SPY

QQQ

IWM

Relevant sector ETFs

---

# 15. BACKTEST VALIDATION

Audit all existing backtests.

Check for:

- Look-ahead bias
- Survivorship bias
- Data snooping
- Overfitting
- Parameter optimization bias
- Multiple-testing problems
- Insufficient sample size

Require:

TRAINING

VALIDATION

OUT-OF-SAMPLE

WALK-FORWARD

PAPER TRADING

Do not accept a strategy simply because it performs well historically.

---

# 16. MONTE CARLO

For profitable strategies perform Monte Carlo analysis.

Simulate:

1,000+

possible trade sequences.

Calculate:

- Expected return
- Worst-case return
- Drawdown distribution
- Probability of ruin
- Probability of losing money
- Probability of exceeding target return

---

# 17. STRATEGY RANKING

Create a ranking system.

Example:

Strategy Score =

30% Expectancy

20% Sharpe

15% Sortino

15% Profit Factor

10% Drawdown

10% Stability

Do NOT optimize purely for historical return.

---

# 18. KILL BAD STRATEGIES

Automatically identify strategies that should be:

KEEP

IMPROVE

PAPER ONLY

DISABLE

RETIRE

A strategy with negative expectancy should not generate live-trading recommendations.

---

# 19. SIGNAL QUALITY SCORE

Create a unified Signal Quality Score:

Technical

Fundamental

Momentum

Volume

Options

News

Macro

Market Regime

Risk

Liquidity

Each component should have a configurable weight.

The weights must be backtestable.

Do not hard-code arbitrary weights without validation.

---

# 20. AI CIO (FUTURE ARCHITECTURE)

NOTE: This section describes a target architecture, not an immediate implementation requirement. The audit should evaluate whether the CURRENT system's signal synthesis is effective, and recommend whether this multi-agent architecture would provide measurable improvement.

Create an AI Chief Investment Officer.

Specialist agents:

Technical AI

Fundamental AI

Options AI

News AI

Macro AI

Quant AI

Risk AI

Portfolio AI

Execution AI

Each provides:

Score

Confidence

Evidence

Bull Case

Bear Case

Risk

Invalidation

The CIO synthesizes the information.

It can conclude:

STRONG BUY

BUY

WATCH

WAIT

REDUCE

SELL

NO TRADE

"NO TRADE" must be a valid and frequently used outcome.

---

# 21. TRADE THESIS

Every trade must have a structured thesis.

Example:

THESIS

"Semiconductor momentum is accelerating while NVDA remains above its 20/50/200 EMA. Relative volume is elevated and institutional/options activity confirms demand."

CATALYST

"Upcoming earnings / sector momentum / estimate revisions."

ENTRY

$XXX

STOP

$XXX

TARGET

$XXX

RISK

$XXX

EXPECTED RETURN

XX%

REWARD/RISK

X.X

TIME HORIZON

XX days

INVALIDATION

"Close below XXX on increased volume."

---

# 22. TRADE GATING

Before a trade is approved, run:

DATA QUALITY CHECK

MARKET REGIME CHECK

LIQUIDITY CHECK

TECHNICAL CHECK

FUNDAMENTAL CHECK

OPTIONS CHECK

NEWS CHECK

RISK CHECK

PORTFOLIO CHECK

EXECUTION CHECK

If critical checks fail:

NO TRADE.

---

# 23. PROFITABILITY GATES

The platform must NOT automatically transition to live autonomous trading merely because:

Win Rate >= 70%

or

Return >= 10%

Instead evaluate the complete system.

Recommended minimum validation framework:

- Statistically meaningful sample size
- Positive expectancy
- Positive profit factor
- Robust out-of-sample performance
- Walk-forward stability
- Acceptable maximum drawdown
- Positive Sharpe/Sortino
- Realistic transaction costs
- Realistic slippage
- Paper trading confirmation
- No major data leakage
- No strategy dependence on one market regime

Only after these conditions are satisfied should live automation become eligible.

---

# 24. PROFIT MAXIMIZATION

Your goal is to improve profitability, but NEVER by simply increasing risk.

Investigate:

- Better entries
- Better exits
- Better position sizing
- Strategy selection
- Market-regime filtering
- Sector rotation
- Relative strength
- Catalyst timing
- Options selection
- Volatility filtering
- Trade frequency
- Capital allocation

For every proposed improvement show:

Current

Proposed

Historical Performance

Out-of-Sample Performance

Risk Change

Drawdown Change

Confidence

---

# 25. AVOID OVERTRADING

Calculate:

Trades per day

Trades per week

Average holding period

Turnover

Transaction costs

Determine whether the system is generating too many low-quality signals.

Prefer:

FEWER HIGH-QUALITY TRADES

over

MANY LOW-QUALITY TRADES.

---

# 26. FIND THE REAL EDGE

This is one of the most important tasks.

Determine exactly WHERE the platform's predictive edge comes from.

For example:

Technical signals: +0.4% expectancy

News: +0.8%

Options flow: +1.2%

Fundamentals: +0.3%

Macro filter: +0.6%

Combined model: +1.9%

If a data source does not contribute measurable alpha, recommend removing or reducing its weight.

---

# 27. DATA PROVIDER AUDIT

Current providers:

yfinance

Unusual Whales

Determine where delays or data-quality limitations could negatively affect trading.

Identify where additional providers would materially improve the system.

Potential sources:

SEC EDGAR

Financial Modeling Prep

Finnhub

Polygon

Benzinga

Estimize

FRED

Do not add expensive data sources unless the expected improvement justifies the cost.

---

# 28. IMPLEMENTATION

After completing the audit:

DO NOT rewrite the entire platform.

Produce:

1. Critical bugs
2. High-impact improvements
3. Medium-impact improvements
4. Low-priority improvements
5. Features to remove
6. Features to add
7. Strategies to disable
8. Strategies to improve
9. Data-source changes
10. AI prompt improvements
11. Risk-engine improvements
12. Backtesting improvements

For every proposed change provide:

WHY

EXPECTED BENEFIT

RISK

COMPLEXITY

EXPECTED IMPACT

---

# 29. REQUIRED FINAL REPORT

Produce:

## Executive Summary

Is the system profitable?

Is it statistically credible?

Where is the edge?

Where does it lose money?

## Overall Score

Data Quality: /100

Signal Quality: /100

Options: /100

Paper Trading: /100

Risk Management: /100

AI: /100

Backtesting: /100

Portfolio: /100

Overall: /100

## Top 10 Problems

Rank by financial impact.

## Top 10 Opportunities

Rank by expected improvement.

## Best Strategies

Ranked by risk-adjusted return.

## Worst Strategies

Strategies that should be disabled.

## Best Signals

Identify the signals that actually predict returns.

## Best Entry Conditions

Identify the highest-performing combinations.

## Best Exit Conditions

Identify the best exit logic.

## Best Options Setups

Identify the highest-performing options strategies.

## Market Regime Matrix

Show which strategies work in each regime.

## Recommended Architecture

Show proposed improvements.

## Recommended Roadmap

P0 — Critical

P1 — High

P2 — Medium

P3 — Future

---

# 30. MOST IMPORTANT RULE

Do NOT tell me what I want to hear.

If the platform is losing money:

SAY IT.

If the 70% win-rate target is unrealistic:

SAY IT.

If the AI signals have no statistical edge:

SAY IT.

If Unusual Whales options flow is not improving results:

SAY IT.

If paper trading is unrealistic:

SAY IT.

If yfinance data is too delayed for a particular strategy:

SAY IT.

If a strategy looks excellent only because of backtesting bias:

SAY IT.

I want an honest quantitative audit.

The objective is not to make the platform LOOK intelligent.

The objective is to determine whether it can generate a sustainable trading edge.

---

# 31. IMPLEMENTATION PRINCIPLE

After the audit, improve the platform based on evidence.

Do not blindly add more indicators.

Do not blindly add more AI agents.

Do not blindly increase trading frequency.

Do not blindly increase leverage.

Do not optimize for win rate alone.

Optimize for:

POSITIVE EXPECTANCY

RISK-ADJUSTED RETURN

ROBUSTNESS

CAPITAL PRESERVATION

CONSISTENCY

AND REALISTIC EXECUTION.

---

# 32. FINAL COMMAND

Start by inspecting the entire existing repository.

Do not modify production code initially.

All audit, analysis, research, test, and recommendation documents generated during this task MUST be stored under a date-specific directory.

Directory Format

Use:

/docs/YYYY-MM-DD/

where YYYY-MM-DD is the actual current date when the audit is executed.

If today's date is September 4, 2026:

/docs/2026-09-04/

First create:

/docs/2026-09-04/TRADING_SYSTEM_AUDIT.md

Then create:

/docs/2026-09-04/SIGNAL_PERFORMANCE.md

/docs/2026-09-04/OPTIONS_AUDIT.md

/docs/2026-09-04/PAPER_TRADING_AUDIT.md

/docs/2026-09-04/RISK_AUDIT.md

/docs/2026-09-04/DATA_QUALITY_AUDIT.md

/docs/2026-09-04/BACKTEST_AUDIT.md

/docs/2026-09-04/AI_AUDIT.md

/docs/2026-09-04/RECOMMENDATIONS.md

/docs/2026-09-04/IMPLEMENTATION_PLAN.md

Only after completing the audit should you begin implementation.

When implementing changes:

- Make small commits.
- Add tests.
- Preserve existing functionality.
- Benchmark before and after.
- Record every material change.
- Never remove a safety mechanism to increase apparent profitability.

The final objective is a trading system with demonstrable, statistically credible, risk-adjusted edge—not simply a system with a high historical win rate.

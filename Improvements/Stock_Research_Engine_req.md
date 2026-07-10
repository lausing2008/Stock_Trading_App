# Claude Prompt - Planning Stage Stock Research Intelligence Engine

You are a Senior Quantitative Analyst, CFA-Level Fundamental Analyst, Professional Trader, Portfolio Manager, Financial Research Analyst, and Principal Software Architect.

I have an existing AI Stock Trading Platform with the following workflow:

WATCH → PLAN → ACTIVE → CLOSE

I need you to design and implement a comprehensive Planning Stage Research Intelligence Engine.

When a stock is moved into the PLAN stage, the system must automatically perform:

1. Technical Analysis
2. Fundamental Analysis
3. Company Research
4. Industry Research
5. Economic Research
6. Investment Scoring
7. Trading Readiness Scoring
8. Buy / Watch / Avoid Recommendation
9. Risk Management Analysis
10. Entry, Exit, and Position Planning

The objective is to help users determine:

- Should I buy this stock?
- Why should I buy it?
- Why should I avoid it?
- What are the risks?
- What conditions need to improve?
- What is the ideal entry zone?
- What is the stop loss?
- What are the profit targets?
- What would invalidate the trade?

The recommendation must be evidence-based, explainable, transparent, and supported by data.

---

# REPORT STRUCTURE

Generate a complete Planning Stage Research Report.

## Executive Summary

Display:

- Company Name
- Symbol
- Current Price
- Market Cap
- Sector
- Industry

Show:

- Overall Score (0-100)
- Confidence Level (0-100)
- Recommendation

Possible Recommendations:

- STRONG BUY
- BUY
- WATCH
- AVOID
- SELL

Provide:

- Top 5 Bullish Factors
- Top 5 Bearish Factors
- Key Risks
- Key Opportunities

---

# TECHNICAL ANALYSIS ENGINE

## Trend Analysis

Analyze:

### Price vs 50 EMA

Bullish:
Price > 50 EMA

Bearish:
Price < 50 EMA

Explain significance.

---

### Price vs 200 EMA

Strong Bullish:
Price > 200 EMA

Bearish:
Price < 200 EMA

Explain significance.

---

### Golden Cross / Death Cross

Detect:

- Golden Cross
- Death Cross
- No Cross

Golden Cross:
50 EMA crosses above 200 EMA

Death Cross:
50 EMA crosses below 200 EMA

Explain interpretation.

---

### RSI Analysis

Interpret:

RSI < 30
Oversold

30-40
Weak

40-60
Healthy

60-70
Strong

70+
Overbought

Explain whether current RSI is favorable.

---

### MACD Analysis

Analyze:

- MACD Line
- Signal Line
- Histogram

Detect:

- Bullish Crossover
- Bearish Crossover
- No Crossover

Provide interpretation.

---

### Histogram Analysis

Classify:

Green Growing
Green Shrinking
Red Growing
Red Shrinking

Interpretation:

Green Growing
Momentum accelerating

Green Shrinking
Momentum slowing

Red Growing
Selling pressure increasing

Red Shrinking
Bearish momentum weakening

---

### Volume Analysis

Calculate:

- Current Volume
- 20-Day Average Volume
- Relative Volume (RVOL)

Interpret:

RVOL > 1.5
Strong participation

RVOL 1.0-1.5
Healthy participation

RVOL < 1
Weak participation

---

### Support and Resistance

Identify:

- Nearest Support
- Major Support
- Nearest Resistance
- Major Resistance

Provide reasoning.

---

### ATR Analysis

Calculate:

- ATR
- ATR %
- Volatility Rating

Interpret risk.

---

### Trend Verdict

Return:

- Strong Bullish
- Bullish
- Neutral
- Bearish
- Strong Bearish

Provide explanation.

---

# FUNDAMENTAL ANALYSIS ENGINE

## Income Statement Analysis

Evaluate:

### Revenue

- Revenue Growth YoY
- Revenue Growth QoQ

Assessment:

- Excellent
- Good
- Average
- Weak

---

### EPS

Analyze:

- EPS Growth YoY
- EPS Growth QoQ

Assessment:

- Excellent
- Good
- Average
- Weak

---

### Margins

Evaluate:

- Gross Margin
- Operating Margin
- Net Margin

Compare against industry averages.

---

## Balance Sheet Analysis

Evaluate:

- Total Cash
- Total Debt
- Debt/Equity Ratio
- Current Ratio
- Quick Ratio

Determine:

- Strong Balance Sheet
- Average Balance Sheet
- Weak Balance Sheet

---

## Cash Flow Analysis

Evaluate:

- Operating Cash Flow
- Free Cash Flow
- Free Cash Flow Growth
- FCF Margin

Determine:

- Excellent
- Good
- Average
- Poor

---

## Valuation Analysis

Calculate:

- P/E Ratio
- Forward P/E
- PEG Ratio
- Price/Sales
- EV/EBITDA

Determine:

- Undervalued
- Fairly Valued
- Overvalued

Explain reasoning.

---

## Profitability Analysis

Evaluate:

- ROE
- ROA
- ROIC

Grade:

- Excellent
- Good
- Average
- Poor

---

# COMPANY RESEARCH ENGINE

## Business Model

Explain:

What does the company do?

Provide summary in 2-3 sentences.

---

## Competitive Advantage

Evaluate:

- Brand Strength
- Network Effects
- Patents
- Switching Costs
- Economies of Scale
- Distribution Advantage

---

## Moat Analysis

Rate:

- Very Strong
- Strong
- Moderate
- Weak
- None

Provide explanation.

---

## Insider Activity

Analyze:

- Insider Buying
- Insider Selling

Determine:

- Bullish
- Neutral
- Bearish

Explain significance.

---

## Institutional Ownership

Analyze:

- Institutional Ownership %
- Trend Increasing?
- Trend Decreasing?

Interpret significance.

---

## Management Quality

Evaluate:

- Capital Allocation
- Historical Execution
- Growth Strategy
- Shareholder Friendliness

Rate:

- Excellent
- Good
- Average
- Weak

---

# INDUSTRY RESEARCH ENGINE

## Industry Status

Determine:

- Growing
- Mature
- Declining
- Disrupted

Provide evidence.

---

## Total Addressable Market (TAM)

Evaluate:

- TAM Size
- TAM Growth
- Future Expansion Potential

Rate:

- Excellent
- Good
- Average
- Weak

---

## Market Share Analysis

Analyze:

- Current Market Share
- Market Share Trend
- Competitive Position

Determine:

- Gaining Share
- Stable
- Losing Share

---

## Competitor Analysis

Compare with major competitors:

- Revenue Growth
- Margins
- Valuation
- Profitability
- Market Position

Provide rankings.

---

## Regulatory Risk

Rate:

- Low
- Medium
- High

Explain risks.

---

## Industry Verdict

Return:

- Strong Tailwind
- Moderate Tailwind
- Neutral
- Headwind
- Severe Headwind

---

# ECONOMIC RESEARCH ENGINE

## Federal Reserve Analysis

Determine:

- Fed Hiking
- Fed Holding
- Fed Cutting

Explain impact on stock.

---

## Inflation Analysis

Evaluate CPI trend:

- Improving
- Stable
- Worsening

Explain impact.

---

## GDP Analysis

Determine:

- Expanding
- Flat
- Contracting

Explain significance.

---

## Employment Analysis

Evaluate:

- Strong
- Neutral
- Weak

---

## Recession Risk Checklist

Evaluate:

- Yield Curve Inverted?
- GDP Negative Two Consecutive Quarters?
- Unemployment Rising?
- Consumer Confidence Falling?

Risk Rating:

- Low
- Moderate
- High

---

## Market Environment Analysis

Determine:

Which style is favored?

- Growth Stocks
- Value Stocks
- Dividend Stocks
- Defensive Stocks

Explain reasoning.

---

# MASTER CHECKLIST

## Layer 1 — Company

Can I explain what this company does in 2 sentences?

Revenue growing year-over-year?

EPS growing year-over-year?

Free cash flow positive and growing?

Debt manageable (Debt/Equity under 2)?

Clear competitive moat?

Insiders buying or holding?

Institutional ownership above 50%?

Display:

PASS
WARNING
FAIL

---

## Layer 2 — Industry

Industry growing?

Large TAM?

Market share increasing?

Low regulatory risk?

No disruptive competitors?

Display:

PASS
WARNING
FAIL

---

## Layer 3 — Economy

Fed supportive?

Inflation improving?

GDP growing?

No major recession signals?

Stock style favored?

Display:

PASS
WARNING
FAIL

---

## Layer 4 — Technical

Price above 200 EMA?

Price above 50 EMA?

Golden Cross present?

RSI healthy?

MACD bullish or neutral?

Volume confirming move?

Support level identified?

Display:

PASS
WARNING
FAIL

---

# SCORING ENGINE

Score each category:

Technical Analysis
0-100

Fundamental Analysis
0-100

Company Research
0-100

Industry Research
0-100

Economic Research
0-100

Apply weights:

Technical Analysis:
25%

Fundamental Analysis:
30%

Company Research:
15%

Industry Research:
15%

Economic Research:
15%

Calculate:

Overall Score:
0-100

---

# RECOMMENDATION ENGINE

Overall Score:

90-100
STRONG BUY

80-89
BUY

65-79
WATCH

50-64
AVOID

0-49
SELL

Provide detailed reasoning.

---

# ENTRY PLANNING ENGINE

Generate:

## Aggressive Entry

Best price zone for aggressive traders.

---

## Conservative Entry

Best price zone for confirmation-based traders.

---

## Stop Loss

Determine using:

- Support Levels
- ATR
- Volatility

Explain reasoning.

---

## Take Profit Targets

Generate:

Target 1

Target 2

Target 3

Provide expected gains.

---

## Risk Reward Analysis

Calculate:

Expected Reward

Expected Risk

Risk/Reward Ratio

Assessment:

- Excellent
- Good
- Average
- Poor

---

# POSITION SIZING

Assume user-configurable:

Portfolio Size

Maximum Risk Per Trade

Calculate:

- Position Size
- Dollar Risk
- Share Quantity

Provide recommendation.

---

# TRADE INVALIDATION LOGIC

Identify:

What would invalidate the trade?

Examples:

- Break below support
- Death Cross
- Earnings miss
- Revenue slowdown
- Institutional selling
- MACD bearish crossover

Provide detailed conditions.

---

# AI INVESTMENT VERDICT

Provide final summary:

### Can I Buy This Stock Today?

Answer:

YES
NO
WAIT

### Why?

Provide detailed explanation.

### What Are The Biggest Risks?

List risks.

### What Must Improve Before Buying?

List missing conditions.

### What Would Make This A Strong Buy?

List future catalysts.

### Confidence Level

0-100%

### Final Recommendation

STRONG BUY
BUY
WATCH
AVOID
SELL

---

# UI DASHBOARD REQUIREMENTS

Executive Summary Card

Technical Dashboard

Fundamental Dashboard

Company Dashboard

Industry Dashboard

Economic Dashboard

Checklist Dashboard

Risk Management Dashboard

Trading Plan Dashboard

All checklist items must display:

✅ PASS

⚠ WARNING

❌ FAIL

Use charts, tables, scoring, and explanations wherever possible.

The goal is to create a research engine comparable to a combination of:

- TradingView
- Finviz
- Seeking Alpha
- Morningstar
- Motley Fool
- Institutional Equity Research

The output should help users confidently determine whether a stock deserves to move from PLAN → ACTIVE or remain in PLAN / WATCH status.
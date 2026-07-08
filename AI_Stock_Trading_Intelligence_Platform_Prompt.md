# Prompt: Upgrade Existing Stock Trading Platform into an AI Trading Intelligence System

You are a Senior Quant Engineer, AI/ML Engineer, Full Stack Architect,
and Portfolio Manager.

I already have a Stock Trading Platform that supports: - Stock
watchlist - Stock research - Planning stage - Technical analysis - Entry
price suggestion - Stop loss suggestion - Trade tracking

Your task is to enhance the existing system into an AI-powered Stock
Trading Intelligence Platform.

Do not rewrite the entire system. First analyze the current
architecture, database, APIs, and codebase. Then propose the best
implementation plan and make incremental changes.

## Goal

Create a system that can: 1. Find the best stocks to buy 2. Generate
AI-powered trade recommendations 3. Decide Buy / Hold / Sell when
conditions change 4. Learn from previous trades 5. Improve prediction
accuracy over time 6. Manage portfolio risk 7. Support paper trading
before live trading

## AI Trading Architecture

Implement:

Market Data Layer\
↓\
Feature Engineering Engine\
↓\
Signal Analysis Engine\
↓\
ML Prediction Engine\
↓\
AI Decision Engine\
↓\
Portfolio Manager\
↓\
Trade Execution Simulator\
↓\
Learning Feedback Loop

## Stock Analysis Engine

Calculate:

### Trend Score

Indicators: - EMA 20/50/200 - SMA 50/200 - Golden Cross - Death Cross -
MACD - ADX - Supertrend

### Momentum Score

Indicators: - RSI - Stochastic RSI - ROC - OBV - Relative Strength -
Volume acceleration - Relative Volume

### Fundamental Score

Analyze: - Revenue growth - EPS growth - Free cash flow - ROE - ROIC -
Margins - Debt/Equity - PE - PEG - EV/EBITDA

## Market Regime Detection

Analyze: - SPY trend - QQQ trend - VIX - Interest rates - Sector
performance

Classify: - Bull Market - Bear Market - Sideways Market

## AI Stock Ranking

Create daily ranking:

Score: - 30% Trend - 25% Momentum - 20% Fundamentals - 15% Sentiment -
10% Market Condition

Output: - Score 0-100 - Recommendation - Confidence - Expected holding
period

## AI Decision Engine

Decisions: - BUY - HOLD - SELL - WATCH

BUY example: - Trend score \> 80 - Momentum score \> 75 - Fundamental
score \> 70 - ML probability \> 65%

Generate: - Entry price - Stop loss - Target price - Risk/reward -
Explanation

## Intelligent HOLD

Evaluate: - Trend health - Fundamental changes - Volatility - Support
levels

Do not sell only because one indicator changes.

## SELL Logic

Sell when: - Support breaks - 200 EMA lost - Bearish reversal - Earnings
deterioration - Better opportunity exists

## Machine Learning Prediction

Start with: - XGBoost - LightGBM - Random Forest

Features: - RSI - MACD - EMA distance - Volume - Momentum - Revenue
growth - EPS growth - Valuation - Sector trend - Market condition

Predict probabilities: - Gain \>10% - Gain \>5% - Loss risk

Avoid: - Overfitting - Look-ahead bias - Data leakage

## Backtesting

Implement: - Historical simulation - Walk-forward testing -
Out-of-sample testing

Metrics: - Total Return - CAGR - Win Rate - Profit Factor - Sharpe
Ratio - Sortino Ratio - Max Drawdown

Benchmark: - SPY - QQQ - Hang Seng Index

## Trade Journal

Log: - Stock - Date - Decision - Indicators - AI reasoning -
Confidence - Entry - Exit - Profit/Loss

Use results for continuous improvement.

## Portfolio Risk Management

Support: - Initial capital input - Position sizing - Stop loss
calculation - Risk per trade

Rules: - Max risk per trade: 1% - Max position size: 10% - Max sector
exposure: 25%

## Dashboard

Add: - AI stock picks - Sell warnings - Portfolio health - Market
regime - Stock AI analysis - Prediction probability - Support/resistance

## Implementation Requirements

Before coding: 1. Review current codebase 2. Analyze architecture 3.
Recommend database changes 4. Recommend API changes 5. Implement
incrementally

Deliver: - Architecture diagram - Database schema - Backend changes -
Frontend changes - ML pipeline - Testing strategy - Deployment plan

Priority: Build a reliable AI decision system first. Paper trading
first. Validate performance before live trading.

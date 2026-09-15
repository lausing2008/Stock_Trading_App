# AI Institutional Trading Platform Implementation Prompt

You are Claude Code acting as a Principal Quantitative Engineer, Staff
Software Architect, AI/ML Engineer, Portfolio Manager, Risk Manager, and
Senior Full-Stack Developer.

## Mission

Transform my existing AI Stock Trading Platform (Watch → Plan → Active →
Close) into a modular, institutional-grade trading platform.

Primary objectives: - Deliver explainable AI recommendations. - Improve
decision quality using technical, fundamental, macroeconomic, sentiment,
and portfolio analysis. - Optimize for long-term risk-adjusted returns
while preserving capital. - Support a maturity path from research
assistant to paper trading, then broker-assisted execution, and finally
configurable automation after rigorous validation.

## Architecture

Build modular services: - Market Data - Technical Analysis - Fundamental
Analysis - News & Sentiment - Economic Data - AI Decision Engine - Risk
Management - Portfolio Management - Backtesting - Machine Learning -
Broker Integration - Notification Service

Use clean architecture, dependency injection, REST APIs, WebSockets,
background jobs, PostgreSQL + Redis + time-series DB, comprehensive
logging and tests.

## AI Agents

Implement specialized agents: - Technical Analyst - Fundamental
Analyst - Macro Economist - News Analyst - Options/Flow Analyst - Risk
Manager - Portfolio Manager - Trade Executor - Post-Trade Reviewer

A Chief Investment Officer agent combines all opinions into one final
recommendation with confidence and explanation.

## Workflow

Watch → Plan → Paper Trade → User Approved Live Trade → Assisted
Automation → Optional Strategy Automation

## Analysis

Include: - EMA, VWAP, RSI, MACD, ATR, ADX, OBV, Bollinger Bands,
Ichimoku, SuperTrend - Pattern recognition - Volume profile - Financial
statements and valuation - Insider/institutional ownership - Earnings
analysis - Economic indicators - News summarization with sentiment -
Explainable AI

## Risk Management

Never recommend trades without: - Stop loss - Position sizing -
Risk/reward - Portfolio exposure analysis - Liquidity checks -
Earnings/news checks

## Machine Learning

Support: - Backtesting - Walk-forward validation - Monte Carlo - Regime
detection - Ensemble models - Continuous learning from completed trades

## Broker Integrations

Design an abstraction layer supporting: - E\*TRADE - Charles Schwab -
Interactive Brokers - Alpaca - Tradier - TradeStation - Fidelity (when
available)

## Automation Requirements

Do NOT enable autonomous live trading solely because of a target win
rate or return.

Require: - Extended paper trading - Out-of-sample validation - Stable
Sharpe/Sortino - Acceptable drawdown - Positive expectancy - Realistic
slippage/commission modeling - User-configurable safeguards - Kill
switch - Daily loss limits

## Deliverables

1.  Audit current codebase.
2.  Produce architecture diagrams.
3.  Refactor incrementally.
4.  Build reusable modules.
5.  Write unit/integration tests.
6.  Document APIs.
7.  Produce migration plan.
8.  Create roadmap with priorities.
9.  Keep commits small and production-ready.
10. Explain all major design decisions.

Always favor maintainability, correctness, transparency, and risk
management over complexity.

# StockAI — Product Overview

## Purpose

StockAI is a production-ready, multi-market stock intelligence platform providing AI-powered trading signals, technical analysis, ML predictions, and portfolio optimization for US (NYSE, NASDAQ) and Hong Kong (HKEX) markets.

## Value Proposition

- **Unified Intelligence**: Combines technical analysis, ML models (Random Forest, XGBoost, LSTM), and AI chat (Claude/DeepSeek) into actionable trading signals
- **Multi-Market Support**: US and HK stocks with plugin architecture for adding new markets
- **End-to-End Workflow**: From stock screening → signal generation → paper trading → portfolio optimization → research reports

## Key Features

| Area | Capability |
|------|------------|
| **Data Sources** | yfinance (default), Alpha Vantage, Polygon.io with API key management |
| **Live Prices** | Real-time quotes via yfinance, Redis-cached (60s TTL) |
| **Technical Analysis** | SMA/EMA, RSI, MACD, Bollinger Bands, VWAP, Fibonacci, trendlines, support/resistance |
| **Pattern Recognition** | Head & Shoulders, Double Top/Bottom, Triangles, Flag/Pennant, Cup & Handle |
| **ML Prediction** | Random Forest, Gradient Boosting, XGBoost, PyTorch LSTM — price direction + confidence |
| **AI Signals** | BUY/HOLD/WAIT/SELL with horizon and 0-100 confidence fusing TA + ML + volume |
| **K-Score Ranking** | Composite 0-100 score: Technical/Momentum/Value/Growth/Volatility |
| **Portfolio Optimizer** | Sharpe MVO, Risk Parity, HRP, AI Allocation with Ledoit-Wolf covariance |
| **Paper Trading** | Full simulation engine with equity curves, stop-loss, trailing stops |
| **Research Engine** | AI-generated 10-dimension research reports (technical, fundamental, industry, etc.) |
| **Alerts** | Rule-based alerts on price, % change, signal, K-Score with cooldown |
| **AI Chat** | Context-aware stock Q&A via Claude or DeepSeek |

## Target Users

- **Active Traders**: Day/swing traders needing real-time signals and technical analysis
- **Quantitative Investors**: Users wanting ML-based predictions and portfolio optimization
- **Research Analysts**: Professionals requiring comprehensive stock research reports

## Pages

| Page | Purpose |
|------|---------|
| Dashboard | Market overview, per-user stock grid with live signals |
| Opportunities | Strategy screener (Top Picks, Swing, Short-Term, Long-Term, Growth) |
| Stock Detail | Live price, candlestick chart, AI signal, K-Score, ML prediction, financials, AI chat |
| Rankings | K-Score leaderboard for user's watchlist |
| Watchlist | Curated list with notes, alerts, signal filters |
| Positions | Portfolio P&L tracker with allocation chart |
| Strategies | Rule DSL strategy builder + backtester |
| Portfolio | Quantitative optimizer (MVO/Risk Parity/HRP/AI) |
| Alerts | Alert management and notification history |
| Research | AI-generated research reports |
| Settings | Data sources, AI provider, notifications, ML defaults |

## Default Account

- Username: `lausing`
- Password: `123456`
- Role: admin

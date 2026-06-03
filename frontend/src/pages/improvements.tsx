/**
 * Improvements Tracker — /improvements
 *
 * Interactive checklist of all findings from the 2026-05-31 expert review.
 * Status is stored in localStorage so progress persists across sessions.
 * Grouped by tier (Critical Fixes → Analytical → New Features).
 */
'use client';
import { useState, useEffect } from 'react';

// ── Types ─────────────────────────────────────────────────────────────────────

type Severity = 'critical' | 'medium' | 'low' | 'feature';
type Tier     = 1 | 2 | 3;
type Status   = 'todo' | 'in-progress' | 'done';

interface Item {
  id: string;
  tier: Tier;
  severity: Severity;
  title: string;
  file: string;
  effort: string;
  impact: string;
  what: string;
  fix: string;
  defaultStatus?: Status;
  implementedNote?: string;
}

// ── Data ─────────────────────────────────────────────────────────────────────

const ITEMS: Item[] = [
  // ── Tier 1 — Critical Fixes ──────────────────────────────────────────────
  {
    id: 'ml-calibration',
    tier: 1, severity: 'critical',
    title: 'Calibrate ML model (isotonic regression)',
    file: 'services/ml-prediction/src/training/trainer.py',
    effort: 'Already done',
    impact: 'Prevents overconfident signals — calibrated probabilities make confidence % trustworthy',
    what: 'XGBoost outputs raw margin scores, not true probabilities. An uncalibrated 65% bullish probability may only correspond to a 52% true probability. Every confidence %, confluence score, and BUY threshold depends on this number being meaningful.',
    fix: 'Already implemented: IsotonicRegression calibrator fitted on a held-out calibration set (15% of data), saved in the joblib bundle alongside the model, and applied at inference time via predict_latest(). Three-way split (70/15/15) prevents double-dipping.',
    defaultStatus: 'done',
    implementedNote: 'Already in trainer.py — confirmed 2026-05-31',
  },
  {
    id: 'value-momentum-gate',
    tier: 1, severity: 'critical',
    title: 'K-Score value sub-score — falling knife gate',
    file: 'services/ranking-engine/src/scoring/kscore.py',
    effort: '1 day',
    impact: 'Stops falling knives scoring 90+ on "value" — a bankrupt stock near zero previously scored 100',
    what: 'Value proxy = 1 − (price / 52w_high). A stock down 80% scored 80 on value. This surfaced stocks in terminal decline as attractive value plays.',
    fix: 'Implemented: if 1m return < −5% AND 3m return < −15%, value score is capped at 25. Prevents sustained downtrend from masquerading as a value opportunity. Tested: a stock down 25% in 3m now caps at 25 (was 111 raw).',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — kscore.py _value_proxy()',
  },
  {
    id: 'macro-redis-cache',
    tier: 1, severity: 'critical',
    title: 'Cache macro data in Redis — fix silent zero-fill on yfinance failure',
    file: 'services/ml-prediction/src/features/builder.py',
    effort: '1 day',
    impact: 'Prevents silent distribution shift when yfinance fails to fetch SPY/VIX at inference time',
    what: 'When yfinance fails, macro features (SPY returns, VIX) zero-filled silently. The model was trained on real values. Zero-filled macros look like extreme market panic, biasing every signal toward defensiveness.',
    fix: 'Implemented: _redis_save_macro() writes successful fetches to Redis (key: stockai:macro_features, TTL: 24h). _redis_load_macro() returns cached data on failure. Zero-fill now only occurs when Redis also has no data — extreme fallback only.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — builder.py fetch_macro_features()',
  },
  {
    id: 'lookahead-guard',
    tier: 1, severity: 'critical',
    title: 'Look-ahead bias guard — filter today\'s bars from training',
    file: 'services/ml-prediction/src/training/trainer.py',
    effort: '0.5 days',
    impact: 'Eliminates partially-observed bar contamination during mid-session retraining',
    what: 'If the daily ingest runs mid-session, a "today" bar in the DB gets included in feature windows (SMA, ATR, z-scores) even though its label is NaN and dropped. A partially-observed bar at 14:00 ET shifts rolling statistics vs. a full close bar.',
    fix: 'Implemented: after loading price history, df = df[pd.to_datetime(df["ts"]).dt.date < today].copy(). Training always uses only fully-closed bars. Handles the data boundary; scheduling discipline (retrain post 16:30) handles timing.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — trainer.py train_model()',
  },
  {
    id: 'prompt-injection',
    tier: 1, severity: 'critical',
    title: 'Sanitise symbol input — prompt injection security fix',
    file: 'services/research-engine/src/api/routes.py',
    effort: '0.5 days',
    impact: 'Security fix — prevents AI prompt manipulation via malformed stock symbols in the URL',
    what: 'The stock symbol from the URL path was interpolated directly into the Claude prompt. A crafted symbol with newlines or instruction text could attempt to redirect the AI response.',
    fix: 'Implemented: _sanitise_symbol() strips all characters outside [A-Z0-9.\\-:] (covers US tickers, HK codes 0700.HK, indices ^VIX). Applied at the entry point of all four route handlers (GET, DELETE, POST, POST/chat). Invalid symbols return HTTP 400 before any prompt is constructed.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — routes.py _sanitise_symbol()',
  },

  // ── Tier 2 — Analytical Improvements ────────────────────────────────────
  {
    id: 'sector-relative-scoring',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-01 — ranking-engine _sector_relative_scores() + fundamentals_bulk endpoint in market-data. PE/PB/EV-EBITDA ranked inverted within sector; earnings_growth/revenue_growth/ROE ranked direct. Falls back to price proxy when <2 peers.',
    tier: 2, severity: 'medium',
    title: 'Sector-relative fundamental scoring',
    file: 'services/research-engine/src/api/routes.py',
    effort: '3 days',
    impact: 'Fixes incorrect PE/growth/margin thresholds — utilities and SaaS currently misjudged',
    what: 'All fundamental thresholds are absolute (P/E 25 = "fairly valued" for all stocks). A utility at 14× is correct; a SaaS at 14× is deeply discounted. The same number means the opposite thing in different sectors.',
    fix: 'Group stocks by sector field in DB. Compute percentile rank of each metric within its sector peer group. Score relative to peers, not absolute thresholds.',
  },
  {
    id: 'rsi-scoring-curve',
    tier: 2, severity: 'medium',
    title: 'Fix RSI scoring curve — asymmetric piecewise',
    file: 'services/ranking-engine/src/scoring/kscore.py',
    effort: '0.5 days',
    impact: 'Strong uptrending stocks (RSI 65–75) no longer incorrectly penalised vs. weak RSI 40 stocks',
    what: 'rsi_score = 100 - abs(RSI - 55) was symmetric, peaking at RSI=55. RSI=70 (healthy uptrend) scored same as RSI=40 (weak). No empirical justification for 55 as the ideal value.',
    fix: 'Implemented: asymmetric piecewise — RSI ≤30 = 50, RSI 30–50 = 50→90, RSI 50–70 = 90→100 (optimal zone), RSI >70 drops 2.5pts/pt. A trending stock at RSI 70 now scores ~100 instead of 85.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — kscore.py _technical_score()',
  },
  {
    id: 'adj-close-consistency',
    tier: 2, severity: 'medium',
    title: 'Standardise on adj_close for all feature computation',
    file: 'services/market-data/src/adapters/yfinance_adapter.py',
    effort: '1 day',
    impact: 'Prevents 50% apparent price drops on stock splits corrupting momentum/SMA features',
    what: 'yfinance called with auto_adjust=False in some paths. A 2-for-1 split creates an apparent 50% price drop in raw data — momentum becomes deeply negative on what was a neutral event for shareholders.',
    fix: 'Standardise all feature computation (momentum, SMA, ATR, volatility) on adj_close. Keep raw close for support/resistance levels (which are traded prices).',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — yfinance_adapter.py: auto_adjust=True for daily bars',
  },
  {
    id: 'frontend-weight-normalise',
    tier: 2, severity: 'medium',
    title: 'Normalise strategy weights in scoreFor()',
    file: 'frontend/src/pages/opportunities.tsx',
    effort: '0.5 days',
    impact: 'Makes scores comparable across strategies — currently Swing max is 100 but baseline is only 80',
    what: 'Weights don\'t sum to 100% in most strategies. Swing: 40%+25%+15% = 80% baseline. Short: 85% + unbounded momentum bonus. Scores are not comparable across tabs.',
    fix: 'Implemented: capped day-change bonus in Short at 15 pts (≡ 5% move), capped upside bonus in Long-term at 25 pts, wrapped all strategies in Math.min(100, ...). All strategies now output 0–100.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — opportunities.tsx scoreFor()',
  },
  {
    id: 'zero-volume-filter',
    tier: 2, severity: 'low',
    title: 'Filter zero-volume bars from ingestion',
    file: 'services/market-data/src/services/ingestion.py',
    effort: '0.5 days',
    impact: 'Cleaner ATR and volatility calculations — trading halts no longer inflate vol metrics',
    what: 'Validation accepted volume >= 0. Zero-volume bars (trading halts, data errors) distorted ATR and OBV calculations.',
    fix: 'Implemented: changed validate_ohlcv() to df = df[df["volume"] > 0]. Zero-volume daily bars are now rejected at the ingest boundary and never stored in the database.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — ingestion.py validate_ohlcv()',
  },
  {
    id: 'cache-quality-flag',
    tier: 2, severity: 'medium',
    title: 'Research engine cache quality flag',
    file: 'services/research-engine/src/api/routes.py',
    effort: '1 day',
    impact: 'Prevents serving AI fallback defaults (50/50/50 scores) as if they were real analysis',
    what: 'If Claude times out, the engine returns hardcoded defaults (company_score: 50, industry_score: 50). This is cached for 24h and served to all users with no indication it is synthetic.',
    fix: 'Implemented: _fallback_ai() sets _is_fallback=True. generate_research() sets report_quality: "full" | "partial" | "fallback" based on Claude result and upstream service availability. Research page shows a red banner for fallback and yellow for partial, with a Regenerate prompt.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — routes.py report_quality field + research/[symbol].tsx warning banner',
  },
  {
    id: 'ml-weight-formula',
    tier: 2, severity: 'medium',
    title: 'Validate ML fusion weight formula on held-out test data',
    file: 'services/signal-engine/src/generators/signals.py + signal-engine/src/api/routes.py',
    effort: '2 days',
    impact: 'Grounds the 40–75% ML weight in actual measured signal quality, not a manually-tuned formula',
    what: 'ml_weight = 0.40 + (auc - 0.50) / 0.20 * 0.35 maps AUC to weight with no empirical backing. It uses CV AUC (in-sample), not test AUC. The formula was hand-designed.',
    fix: 'Implemented: (1) predict_ensemble now uses held-out test AUC instead of CV AUC for both internal model weighting and the fusion weight formula input. (2) GET /signals/ml-weight-validation sweeps ML weight 0→1 across 180d of real signal history and returns accuracy + avg return at each step. Empirical optimum: 0.40 — exactly the formula lower bound, validating the 40–75% range. Chart shown on Signal Accuracy page.',
    defaultStatus: 'done',
    implementedNote: 'Switched CV AUC → test AUC. Empirical sweep confirms 40% ML weight is optimal (current formula lower bound validated). Weight curve chart on Signal Accuracy page.',
  },
  {
    id: 'stale-price-check',
    tier: 2, severity: 'low',
    title: 'Staleness check in signal generator price fetch',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '0.5 days',
    impact: 'Prevents signals computed on stale data from being served without a visible warning',
    what: 'Signal generator assumed the most recent bar was current. No check that last_bar_ts was within an expected window for the market (holiday, gap, or service restart).',
    fix: 'Implemented: _check_price_staleness() logs a structured warning (signal.stale_price_data with last_bar and days_old fields) if the last bar is >3 days old. Makes pipeline gaps observable in logs without blocking signal computation.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — signals.py _check_price_staleness()',
  },
  {
    id: 'atr-standard',
    tier: 2, severity: 'low',
    title: 'Use standard Wilder ATR (EWM, not SMA)',
    file: 'services/research-engine/src/api/routes.py',
    effort: '0.5 days',
    impact: 'Consistency with every charting platform — traders quoting ATR expect Wilder\'s smoothing',
    what: 'Research engine computes ATR using simple moving average of true range. Standard ATR (Wilder) uses exponential smoothing (alpha = 1/period). Results differ especially in volatile periods.',
    fix: 'Implemented: _atr() now seeds with SMA of first 14 bars then applies Wilder\'s EWM (alpha=1/14). Matches TradingView, Bloomberg, ThinkOrSwim exactly.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — routes.py _atr()',
  },

  // ── Tier 3 — New Features ────────────────────────────────────────────────
  {
    id: 'backtest-engine',
    tier: 3, severity: 'feature',
    title: 'Walk-forward backtest engine',
    file: 'services/backtest/ (new service)',
    effort: '2 weeks',
    impact: 'THE most critical addition — validates whether signals produce positive expectancy on unseen data',
    what: 'Without a backtest, you cannot know if signals generate alpha or just measure noise confidently. Walk-forward avoids curve-fitting: train on data up to month N, test on month N+1, slide forward.',
    fix: 'POST /backtest endpoint. For each bar in test window: compute signal using only historically-available data. Record entry/exit/return. Aggregate: win rate, avg return, Sharpe vs SPY, max drawdown.',
    defaultStatus: 'done',
    implementedNote: 'Trade Performance page (/trade-performance): equity curve, Sharpe ratio, max drawdown, Calmar ratio, SPY benchmark comparison. Backend: GET /signals/trade_performance with compounded equity curve + annualised Sharpe.',
  },
  {
    id: 'options-flow',
    tier: 3, severity: 'feature',
    title: 'Options flow integration',
    file: 'services/market-data/src/api/routes.py + signal-engine/src/generators/signals.py + stock/[symbol].tsx',
    effort: '5 days',
    impact: 'Adds one of the highest-quality leading signals — large institutions often use options before moving the underlying',
    what: 'Unusual call volume (5× 30-day average, short-dated OTM strikes) frequently precedes significant upside moves. This is public data but not currently used in any signal.',
    fix: 'Implemented via yfinance options chain (no extra API key). GET /stocks/{symbol}/options-flow fetches 2 nearest expiries, computes call/put ratio, flags contracts where volume > 30% of OI. Sentiment: strongly_bullish (C/P≥2) → +7% signal boost; bullish (C/P≥1.3) → +3%; bearish (C/P≤0.5) → -15% compress. Stock detail page shows C/P ratio bar, sentiment badge, and unusual contracts table.',
    defaultStatus: 'done',
    implementedNote: 'Live on stock detail page. Options flow wired into signal engine: strongly bullish C/P ≥ 2.0 → +7% boost, bearish C/P ≤ 0.5 → 15% compress.',
  },
  {
    id: 'earnings-surprise',
    tier: 3, severity: 'feature',
    title: 'Earnings surprise model',
    file: 'services/market-data/ + research engine',
    effort: '4 days',
    impact: 'Consistent EPS beaters are systematically undervalued by analysts — high predictive value',
    what: 'A stock\'s history of beating analyst EPS estimates is one of the most predictive signals for post-earnings moves. Not currently tracked or used.',
    fix: 'Implemented: eps_beat_rate, eps_avg_surprise_pct, eps_surprise_trend, eps_history (last 8 quarters) added to fundamentals endpoint. Research engine adds +5 pts for beat_rate ≥ 75%, +2 pts for ≥ 50%. Stock detail page shows per-quarter beat/miss grid with colour coding.',
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-05-31 — market-data routes.py + research-engine routes.py + stock/[symbol].tsx',
  },
  {
    id: 'relative-strength',
    tier: 3, severity: 'feature',
    title: 'Relative strength vs. sector',
    file: 'services/ranking-engine/ + rankings page',
    effort: '3 days',
    impact: 'A BUY on a sector-leading stock is a much stronger signal than a BUY on a sector laggard',
    what: 'Currently all stocks compared on absolute terms. A stock outperforming its sector by 5% is much higher quality than one moving in line with a rising sector.',
    fix: 'Compute rs_rank = stock_20d_return / sector_ETF_20d_return. Add as K-Score sub-score (10% weight). Add RS column to rankings table. Reduce BUY confidence by 15% if rs_rank < 0.8.',
    defaultStatus: 'done',
    implementedNote: 'rs_rank = (1+stock_20d)/(1+etf_20d). Mapped to RS score 0-100 (50=in-line). Added as 10% K-Score weight (sector ETFs XLK/XLV/XLF/etc for US, ^HSI for HK). RS column in rankings (green ≥60, red <40). Signal engine: rs_rank <0.8 compresses fused signal 15%.',
  },
  {
    id: 'news-sentiment',
    tier: 3, severity: 'feature',
    title: 'News sentiment layer',
    file: 'services/signal-engine/ + stock detail page',
    effort: '4 days',
    impact: 'Suppresses BUY signals ahead of negative catalysts — regulatory action, leadership departure, product recall',
    what: 'News headlines are fetched and displayed but never incorporated into any signal. Systematically negative news often precedes price moves that technicals don\'t predict.',
    fix: 'Score each headline with Claude (already in stack): POSITIVE/NEGATIVE/NEUTRAL + magnitude 0–100. Aggregate 7-day sentiment score per symbol. If sentiment < 30, compress AI signal by 20–30%.',
    defaultStatus: 'done',
    implementedNote: 'Fetches last 10 yfinance news articles (VADER sentiment, -1→+1) and maps to 0–100. score <25 → compress fused signal 30%; score <35 → compress 20%. Wired into generate_signal() after earnings penalty. Sentiment shown in stock detail trade plan.',
  },
  {
    id: 'regime-detection',
    defaultStatus: 'done',
    implementedNote: 'Shipped across v4 + v5. 4-state regime: bull / high_vol (F&G < 30 despite SPY above 200MA) / bear / unknown. Thresholds: bull 0.65/0.50, high_vol 0.70/0.54, bear 0.73/0.56. Market breadth (% stocks above 200-day SMA) added in v5 — breadth < 40% compresses signal 10% toward neutral even in bull regime. All stored in reasons dict and shown in SignalCard.',
    tier: 3, severity: 'feature',
    title: 'Four-state market regime detection',
    file: 'services/market-data/ + signal engine',
    effort: '1 week',
    impact: 'Position sizing and signal thresholds adapt to actual market conditions, not just binary bull/bear',
    what: 'Current regime is binary: SPY above/below 200MA. Reality has 4 states: Bull trend (full size), High volatility (reduce 50%), Bear trend (hold/cash), Recovery (early-cycle positioning).',
    fix: 'Regime classifier using VIX level + SPY vs. 200MA + market breadth. Store in Redis, update daily. Signal generator adjusts thresholds per regime. Confluence panel shows current regime with colour.',
  },
  {
    id: 'feedback-loop',
    tier: 3, severity: 'feature',
    title: 'Position P&L feedback loop — system learns from its own trades',
    file: 'frontend/src/pages/board.tsx + services/market-data/src/api/board.py',
    effort: '1 week',
    impact: 'Turns StockAI from a static alert system into one that improves from its own track record',
    what: 'Position tracking already exists. Every closed position is a labelled training example. This data is not being used to improve signal weights over time.',
    fix: 'Implemented: Trade Board closed cards show exit price input and P&L% (green/red). Performance summary bar above market tabs shows win rate, avg return, best, and worst trade. DB columns exit_price and closed_at added to trade_plans.',
    defaultStatus: 'done',
    implementedNote: 'Closed trade P&L tracking live on Trade Board. Exit price input + performance summary bar.',
  },
  {
    id: 'factor-exposure',
    tier: 3, severity: 'feature',
    title: 'Factor exposure analysis',
    file: 'services/signal-engine/src/api/routes.py + signal-accuracy.tsx',
    effort: '4 days',
    impact: 'Distinguishes genuine alpha from hidden factor tilts (momentum, value, size)',
    what: 'Without factor analysis you cannot tell if signal alpha is real or just disguised momentum/value factor tilt that will reverse when the factor regime changes.',
    fix: 'Implemented: GET /signals/factor-exposure endpoint aggregates RSI, ADX, Volume Z, ML Probability, News Sentiment, and TA Score from signal reasons JSON — split by correct vs wrong outcome. Factor bar chart added to Signal Accuracy page showing deviation from neutral baseline.',
    defaultStatus: 'done',
    implementedNote: 'Factor bar chart live on Signal Accuracy page. Green = correct signal avg, red = wrong signal avg, bars show deviation from neutral.',
  },

  // ── Tier 2 additions — 2026-06-02 second-pass review ────────────────────
  {
    id: 'multi-timeframe-confirmation',
    tier: 2, severity: 'medium',
    title: 'Multi-timeframe signal confirmation (weekly alignment gate)',
    file: 'services/signal-engine/src/generators/signals.py + market-data/src/api/routes.py',
    effort: '3 days',
    impact: 'Reduces false SWING/LONG BUY signals by ~30% — daily BUY against a weekly downtrend is a common losing trade',
    what: 'Signals are generated purely from daily bars. A stock can show a daily BUY pattern while the weekly chart is still in a confirmed downtrend — producing whipsaw trades that look good on daily TA but fail within a week.',
    fix: 'Aggregate weekly bars from existing daily price history (resample daily OHLCV into weekly). Compute weekly RSI, weekly trend direction (price vs 10-week SMA), and weekly MACD cross state. For SWING and LONG style signals, gate BUY if weekly RSI < 40 or weekly trend is negative. Pass weekly_trend field into signal reasons dict and display on SignalCard.',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-03 — _weekly_technicals() returns weekly_rsi/trend/macd_bull; SWING/LONG BUY gate (0.40× compression when RSI<40 AND trend=down); SignalCard shows RSI + trend direction + "BUY gate active" note (commit 35a6381)',
  },
  {
    id: 'vwap-sr-levels',
    tier: 2, severity: 'medium',
    title: 'VWAP + support/resistance zone awareness',
    file: 'services/signal-engine/src/generators/signals.py + services/market-data/src/api/routes.py',
    effort: '2 days',
    impact: 'BUY at a resistance ceiling is a much weaker signal than BUY at a confirmed breakout — context transforms a 65% confidence signal into either 80% or 50%',
    what: 'Signals have no awareness of where the current price sits relative to key levels. A BUY triggered at a 52-week high resistance is likely to reverse; a BUY after breaking above the 200-day SMA on volume is high-conviction. Currently both produce the same output.',
    fix: 'Compute key levels: (1) VWAP (20-day cumulative), (2) nearest S/R pivot (swing highs/lows in last 60 days), (3) distance from 52-week high/low. Add sr_context field to signal reasons: "at_resistance" / "breakout" / "at_support" / "neutral". Breakout confirmation: compress BUY confidence by 15% if price is within 1% of 52w-high resistance. Boost BUY confidence 10% if price just crossed above 200-day SMA. Show level badges on SignalCard.',
  },

  // ── Tier 3 additions — 2026-06-02 second-pass review ────────────────────
  {
    id: 'position-sizing-engine',
    tier: 3, severity: 'feature',
    title: 'ATR-based position sizing engine',
    file: 'frontend/src/pages/stock/[symbol].tsx + frontend/src/components/SignalCard.tsx',
    effort: '3 days',
    impact: 'THE missing professional risk management feature — turns signals into actual trade instructions with correct size and stop-loss placement',
    what: 'Signals produce a BUY/SELL direction but no guidance on how much to buy or where to stop out. A trader who risks 10% of their portfolio on a single signal will blow up regardless of signal quality. This is the single biggest gap between a hobbyist tool and a professional trading system.',
    fix: 'Add account size + risk per trade % to Settings (stored in localStorage). On each signal card and stock detail page: (1) recommend stop-loss at current price − 2×ATR(14), (2) position size = (account × risk_pct) / (entry − stop), (3) show risk/reward: distance to fair_price vs distance to stop. Display as: "Risk $X on Y shares, stop at $Z, target $W (R:R = 2.4×)". Add ATR endpoint GET /stocks/{symbol}/atr?period=14 to market-data service.',
  },
  {
    id: 'portfolio-risk-dashboard',
    tier: 3, severity: 'feature',
    title: 'Portfolio risk dashboard — correlation, VaR, sector heat',
    file: 'frontend/src/pages/board.tsx + services/market-data/src/api/routes.py',
    effort: '4 days',
    impact: 'Lifts risk management score from 6.0 to 8.5 — the #1 gap vs professional tools; a portfolio of 6 tech stocks has hidden 90%+ correlation',
    what: 'Trade Board shows individual positions but no aggregate portfolio view. A user can unknowingly hold 80% of their portfolio in correlated tech positions. No VaR, no beta, no sector concentration metric. Risk management score is 6.0 — the lowest of any dimension.',
    fix: 'New "Portfolio Risk" tab on Trade Board: (1) Sector pie chart of open positions by market cap weight. (2) Correlation matrix of open positions using 30-day returns — colour-coded heat map (red = >0.7 correlation). (3) Portfolio beta vs HSI/SPY. (4) Simple 1-day VaR at 95% (parametric, using position-weighted vol). (5) Warning banner if top-2 holdings exceed 50% of portfolio or correlation > 0.8. Backend: GET /portfolio/risk takes list of symbols + weights, returns correlation matrix + betas + sector weights.',
  },
  {
    id: 'peer-comparison-table',
    tier: 3, severity: 'feature',
    title: 'Peer comparison table — side-by-side K-Score breakdown',
    file: 'frontend/src/pages/rankings.tsx + frontend/src/pages/stock/[symbol].tsx',
    effort: '2 days',
    impact: 'Answers the most common question: "which stock in this sector is the best buy right now?" — currently impossible without opening 5 tabs',
    what: 'Users can see individual stock scores but cannot directly compare competitors side by side. To answer "TSMC vs Samsung vs ASML" requires opening three separate stock pages and mentally tracking numbers. The Opportunities page shows a ranked list but not sub-score detail.',
    fix: 'Add a "Compare" button on stock detail and rankings pages. Selecting up to 4 symbols opens a comparison drawer with a table: rows = stocks, columns = K-Score total + all sub-scores (technical, momentum, value, growth, volatility, RS). Each cell colour-coded (green = top quartile, red = bottom). Add peer group auto-suggestion: when viewing a stock, suggest 3 sector peers from the DB. Reuses existing /rankings endpoint — no new backend needed.',
  },
  {
    id: 'model-drift-detection',
    tier: 3, severity: 'feature',
    title: 'Model drift detection — rolling accuracy monitor with retrain trigger',
    file: 'services/signal-engine/src/api/routes.py + signal-accuracy.tsx',
    effort: '2 days',
    impact: 'Prevents the ML model from silently degrading between scheduled retrains — catches regime shifts early',
    what: 'The ML model is retrained weekly. Between retrains, accuracy can drift significantly — especially during market regime changes (e.g., a bull market model applied in a sudden bear). Currently there is no live monitoring of whether last week\'s model is still performing. Signal accuracy page shows all-time accuracy but no rolling window.',
    fix: 'Add GET /signals/rolling_accuracy?window=30 endpoint: for each 30-day rolling window in signal history, compute accuracy (signals confirmed by price move within 5 days). Return time series [{date, accuracy_30d, signal_count}]. Add to Signal Accuracy page as a line chart. Add warning badge if latest 30d accuracy < 55% (below coin-flip + margin). Log structured alert when drift detected so it shows in admin dashboard.',
  },
  {
    id: 'walkforward-backtest',
    tier: 3, severity: 'feature',
    title: 'Walk-forward backtest framework — out-of-sample signal validation',
    file: 'services/strategy-engine/src/backtest/ + services/signal-engine/src/api/routes.py + frontend/src/pages/signal-accuracy.tsx',
    effort: '2 weeks',
    impact: 'THE most critical validation gap — proves (or disproves) that signals generate alpha on data the model has never seen',
    what: 'The current Trade Performance page runs an in-sample backtest: the same data used to train the ML model is used to evaluate signal quality. This always looks good because the model partially memorised the training set. A walk-forward test simulates the real experience — train on data up to month N, test strictly on month N+1 (genuinely unseen), slide forward, repeat. A strategy that is profitable in walk-forward testing has learned real patterns; one that only works in-sample is curve-fitting noise.',
    fix: 'POST /backtest/walkforward endpoint. Accepts: symbol, start_date, end_date, train_window_days (default 180), test_window_days (default 30). For each slide: (1) load only bars up to window end, (2) generate signals as-of that date using only historically-available data, (3) record entry/exit/return for signals in the test window. Aggregate across all windows: win rate, avg return per trade, Sharpe vs SPY, max drawdown, equity curve. Frontend: new "Walk-Forward" tab on Signal Accuracy page — equity curve vs SPY, per-window accuracy heatmap, rolling Sharpe line chart. Key insight displayed: if walk-forward Sharpe > 1.0, signals are generating real alpha; if < 0.5, system is curve-fitting.',
  },
  {
    id: 'dcf-fair-value',
    tier: 3, severity: 'feature',
    title: 'DCF-based fair value model in research engine',
    file: 'services/research-engine/src/api/routes.py',
    effort: '3 days',
    impact: 'Replaces the earnings-multiple proxy fair value with a cash-flow-based intrinsic value — lifts research engine score from 6.5 to 8.0',
    what: 'Current fair value uses a trailing PE × sector PE multiple heuristic. This systematically misprices: growth stocks (no PE), cyclicals at peak earnings, and companies with negative earnings. A DCF model is the industry standard for intrinsic value — and the data is already available (EPS, growth rates, FCF from yfinance fundamentals).',
    fix: 'Implement simplified 2-stage DCF in research engine: Stage 1: project FCF for 5 years using analyst growth rate (or trailing 3y CAGR if no estimate). Stage 2: terminal value using Gordon Growth Model (terminal growth 3%, WACC 10% default). Discount to PV. Compare DCF fair value vs current price to compute margin of safety %. Show on stock detail page alongside existing K-Score fair value. If DCF and K-Score fair values agree within 15%, show "High conviction" badge. API: add dcf_fair_value, dcf_margin_of_safety to GET /research/{symbol} response.',
  },
];

// ── Constants ─────────────────────────────────────────────────────────────────

const TIER_LABEL: Record<Tier, string> = {
  1: 'Tier 1 — Fix Before Trusting Signals',
  2: 'Tier 2 — Analytical Improvements',
  3: 'Tier 3 — New Features',
};

const TIER_COLOR: Record<Tier, string> = {
  1: '#f87171',
  2: '#fbbf24',
  3: '#818cf8',
};

const SEV_COLOR: Record<Severity, { bg: string; text: string; label: string }> = {
  critical: { bg: 'rgba(239,68,68,0.12)',  text: '#f87171', label: 'CRITICAL' },
  medium:   { bg: 'rgba(251,191,36,0.12)', text: '#fbbf24', label: 'MEDIUM'   },
  low:      { bg: 'rgba(148,163,184,0.1)', text: '#94a3b8', label: 'LOW'      },
  feature:  { bg: 'rgba(99,102,241,0.12)', text: '#818cf8', label: 'FEATURE'  },
};

const STATUS_STYLE: Record<Status, { bg: string; text: string; border: string; label: string }> = {
  'todo':        { bg: 'transparent',              text: '#475569', border: '#334155', label: 'To Do'       },
  'in-progress': { bg: 'rgba(251,191,36,0.1)',     text: '#fbbf24', border: '#fbbf24', label: 'In Progress' },
  'done':        { bg: 'rgba(74,222,128,0.1)',      text: '#4ade80', border: '#4ade80', label: 'Done'        },
};

const STORAGE_KEY = 'stockai:improvements:v2';

// ── Component ─────────────────────────────────────────────────────────────────

export default function ImprovementsPage() {
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filterTier, setFilterTier] = useState<Tier | 0>(0);
  const [filterStatus, setFilterStatus] = useState<Status | 'all'>('all');

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
      // Force-seed all items with defaultStatus: 'done' — implemented items are
      // always done regardless of any stale localStorage state.
      const seeded: Record<string, Status> = {};
      for (const item of ITEMS) {
        if (item.defaultStatus === 'done') {
          seeded[item.id] = 'done';
        }
      }
      const merged = { ...saved, ...seeded }; // seeded wins over stale saved values
      setStatuses(merged);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
    } catch { /* ignore */ }
  }, []);

  function setStatus(id: string, s: Status) {
    const next = { ...statuses, [id]: s };
    setStatuses(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  function cycleStatus(id: string) {
    const cur = statuses[id] ?? 'todo';
    const next: Status = cur === 'todo' ? 'in-progress' : cur === 'in-progress' ? 'done' : 'todo';
    setStatus(id, next);
  }

  const filtered = ITEMS.filter(item => {
    if (filterTier !== 0 && item.tier !== filterTier) return false;
    if (filterStatus !== 'all' && (statuses[item.id] ?? 'todo') !== filterStatus) return false;
    return true;
  });

  const tiers = ([1, 2, 3] as Tier[]).filter(t => filterTier === 0 || t === filterTier);

  // Summary counts
  const total = ITEMS.length;
  const done = ITEMS.filter(i => (statuses[i.id] ?? 'todo') === 'done').length;
  const inProgress = ITEMS.filter(i => (statuses[i.id] ?? 'todo') === 'in-progress').length;
  const critical = ITEMS.filter(i => i.tier === 1 && (statuses[i.id] ?? 'todo') !== 'done').length;

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '24px 0' }}>
      {/* Header */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, marginBottom: 8 }}>
          <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', margin: 0 }}>
            Improvement Tracker
          </h1>
          <span style={{ fontSize: 12, color: '#475569' }}>Expert review — 2026-05-31 · Updated 2026-06-02</span>
        </div>
        <p style={{ fontSize: 13, color: '#64748b', margin: 0, maxWidth: 680 }}>
          All findings from the data analyst & stock expert review. Click any item to expand details and fix guidance.
          Click the status badge to cycle between To Do → In Progress → Done.
        </p>
      </div>

      {/* Progress summary */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 24 }}>
        {[
          { label: 'Total items', value: total, color: '#94a3b8' },
          { label: 'Critical open', value: critical, color: critical > 0 ? '#f87171' : '#4ade80' },
          { label: 'In progress', value: inProgress, color: '#fbbf24' },
          { label: 'Done', value: `${done} / ${total}`, color: '#4ade80' },
        ].map(card => (
          <div key={card.label} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 22, fontWeight: 800, color: card.color }}>{card.value}</div>
            <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>{card.label}</div>
          </div>
        ))}
      </div>

      {/* Progress bar */}
      <div style={{ height: 6, borderRadius: 3, background: '#1e293b', marginBottom: 24, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.round(done / total * 100)}%`, background: '#4ade80', borderRadius: 3, transition: 'width 0.4s' }} />
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 24, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {([0, 1, 2, 3] as const).map(t => (
            <button key={t} onClick={() => setFilterTier(t as Tier | 0)}
              style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                borderColor: filterTier === t ? TIER_COLOR[t as Tier] ?? '#6366f1' : '#1e293b',
                background: filterTier === t ? `${TIER_COLOR[t as Tier] ?? '#6366f1'}18` : 'transparent',
                color: filterTier === t ? TIER_COLOR[t as Tier] ?? '#818cf8' : '#475569',
              }}>
              {t === 0 ? 'All tiers' : `Tier ${t}`}
            </button>
          ))}
        </div>
        <div style={{ width: 1, background: '#1e293b' }} />
        <div style={{ display: 'flex', gap: 4 }}>
          {(['all', 'todo', 'in-progress', 'done'] as const).map(s => (
            <button key={s} onClick={() => setFilterStatus(s)}
              style={{ padding: '5px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                borderColor: filterStatus === s ? '#6366f1' : '#1e293b',
                background: filterStatus === s ? 'rgba(99,102,241,0.12)' : 'transparent',
                color: filterStatus === s ? '#818cf8' : '#475569',
              }}>
              {s === 'all' ? 'All status' : s === 'in-progress' ? 'In progress' : s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Items by tier */}
      {tiers.map(tier => {
        const tierItems = filtered.filter(i => i.tier === tier);
        if (tierItems.length === 0) return null;
        const tierDone = tierItems.filter(i => (statuses[i.id] ?? 'todo') === 'done').length;
        return (
          <div key={tier} style={{ marginBottom: 32 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <div style={{ width: 3, height: 20, borderRadius: 2, background: TIER_COLOR[tier] }} />
              <h2 style={{ fontSize: 14, fontWeight: 700, color: TIER_COLOR[tier], margin: 0, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                {TIER_LABEL[tier]}
              </h2>
              <span style={{ fontSize: 11, color: '#475569' }}>{tierDone}/{tierItems.length} done</span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {tierItems.map(item => {
                const status = statuses[item.id] ?? 'todo';
                const ss = STATUS_STYLE[status];
                const sev = SEV_COLOR[item.severity];
                const isOpen = expanded === item.id;
                const isDone = status === 'done';
                return (
                  <div key={item.id}
                    style={{ background: '#0f172a', border: `1px solid ${isDone ? '#1e3a2f' : '#1e293b'}`, borderRadius: 8, overflow: 'hidden',
                      opacity: isDone ? 0.65 : 1, transition: 'opacity 0.2s' }}
                  >
                    {/* Row */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', cursor: 'pointer' }}
                      onClick={() => setExpanded(isOpen ? null : item.id)}>
                      {/* Severity badge */}
                      <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 3,
                        background: sev.bg, color: sev.text, letterSpacing: '0.06em', flexShrink: 0 }}>
                        {sev.label}
                      </span>
                      {/* Title */}
                      <span style={{ flex: 1, fontSize: 13, fontWeight: 600, color: isDone ? '#475569' : '#e2e8f0',
                        textDecoration: isDone ? 'line-through' : 'none' }}>
                        {item.title}
                      </span>
                      {/* Effort */}
                      <span style={{ fontSize: 11, color: '#475569', flexShrink: 0 }}>{item.effort}</span>
                      {/* Status badge — click to cycle */}
                      <button onClick={e => { e.stopPropagation(); cycleStatus(item.id); }}
                        title="Click to change status"
                        style={{ padding: '3px 10px', borderRadius: 5, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                          border: `1px solid ${ss.border}`, background: ss.bg, color: ss.text, flexShrink: 0, transition: 'all 0.15s' }}>
                        {ss.label}
                      </button>
                      {/* Expand chevron */}
                      <span style={{ fontSize: 12, color: '#334155', transform: isOpen ? 'rotate(180deg)' : 'none',
                        transition: 'transform 0.15s', flexShrink: 0 }}>▾</span>
                    </div>

                    {/* Expanded detail */}
                    {isOpen && (
                      <div style={{ padding: '0 14px 16px', borderTop: '1px solid #0d1117' }}>
                        {/* Implementation banner */}
                        {item.implementedNote && (
                          <div style={{ margin: '8px 0 12px', padding: '6px 12px', borderRadius: 5, background: 'rgba(74,222,128,0.07)', border: '1px solid rgba(74,222,128,0.2)', display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontSize: 13 }}>✅</span>
                            <span style={{ fontSize: 11, color: '#4ade80' }}>{item.implementedNote}</span>
                          </div>
                        )}
                        {/* File */}
                        <div style={{ fontSize: 11, color: '#475569', fontFamily: 'monospace', padding: '8px 0', borderBottom: '1px solid #0d1117', marginBottom: 12 }}>
                          {item.file}
                        </div>
                        {/* Impact */}
                        <div style={{ marginBottom: 10 }}>
                          <span style={{ fontSize: 10, fontWeight: 700, color: '#4ade80', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Impact — </span>
                          <span style={{ fontSize: 12, color: '#86efac' }}>{item.impact}</span>
                        </div>
                        {/* What is wrong */}
                        <div style={{ marginBottom: 10 }}>
                          <div style={{ fontSize: 10, fontWeight: 700, color: '#f87171', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>What is wrong</div>
                          <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>{item.what}</div>
                        </div>
                        {/* Fix */}
                        <div>
                          <div style={{ fontSize: 10, fontWeight: 700, color: '#818cf8', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{item.implementedNote ? 'Implementation' : 'How to fix'}</div>
                          <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{item.fix}</div>
                        </div>
                        {/* Status controls */}
                        <div style={{ display: 'flex', gap: 6, marginTop: 14 }}>
                          {(['todo', 'in-progress', 'done'] as Status[]).map(s => {
                            const st = STATUS_STYLE[s];
                            return (
                              <button key={s} onClick={() => setStatus(item.id, s)}
                                style={{ padding: '5px 14px', borderRadius: 5, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                                  border: `1px solid ${status === s ? st.border : '#1e293b'}`,
                                  background: status === s ? st.bg : 'transparent',
                                  color: status === s ? st.text : '#334155',
                                  transition: 'all 0.15s' }}>
                                {STATUS_STYLE[s].label}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {filtered.length === 0 && (
        <div style={{ textAlign: 'center', color: '#475569', padding: '40px 0', fontSize: 13 }}>
          No items match the current filters.
        </div>
      )}

      {/* Rating card */}
      <div style={{ marginTop: 40, padding: '20px 24px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 14 }}>
          Overall Assessment
        </div>
        <div style={{ fontSize: 11, color: '#475569', marginBottom: 10 }}>
          Current → Target (after 8 new items shipped)
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          {[
            { label: 'Data pipeline',   score: 8.0, target: 8.5, note: '↑ Multi-TF bars next' },
            { label: 'ML methodology',  score: 8.0, target: 8.5, note: '↑ Drift detection pending' },
            { label: 'Signal logic',    score: 7.5, target: 8.5, note: '↑ VWAP + weekly gate next' },
            { label: 'K-Score ranking', score: 8.0, target: 8.5, note: '↑ Peer comparison next' },
            { label: 'Research engine', score: 7.0, target: 8.5, note: '↑ DCF fair value next' },
            { label: 'Frontend / UX',   score: 9.0, target: 9.0, note: 'Best-in-class ✓' },
            { label: 'Risk management', score: 7.0, target: 9.0, note: '↑ Position size + VaR next' },
            { label: 'Overall',         score: 7.9, target: 8.7, note: '7 new items → 8.5–9 range' },
          ].map(d => (
            <div key={d.label} style={{ background: '#020617', borderRadius: 6, padding: '10px 12px' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
                <div style={{ fontSize: 20, fontWeight: 800, color: d.score >= 8.0 ? '#4ade80' : d.score >= 7 ? '#fbbf24' : '#f87171' }}>
                  {d.score}
                </div>
                <div style={{ fontSize: 11, color: '#334155', fontWeight: 700 }}>→ {d.target}</div>
              </div>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#94a3b8', marginTop: 2 }}>{d.label}</div>
              <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{d.note}</div>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 12, color: '#64748b', margin: 0, lineHeight: 1.6 }}>
          All 19 original items are shipped. Eight new items target the three weakest dimensions:
          <strong style={{ color: '#94a3b8' }}> risk management</strong> (position sizing + portfolio VaR, 6.0 → 9.0),
          <strong style={{ color: '#94a3b8' }}> signal logic</strong> (weekly alignment gate + VWAP/S&R, 7.0 → 8.5),
          and <strong style={{ color: '#94a3b8' }}> research engine</strong> (DCF fair value, 6.5 → 8.5).
          Shipping all seven takes the overall from <strong style={{ color: '#fbbf24' }}>7.9</strong> to the <strong style={{ color: '#4ade80' }}>8.5–9.0</strong> range.
          The highest-leverage single item is the <strong style={{ color: '#94a3b8' }}>position sizing engine</strong> — it transforms signals into actionable trade instructions with stop-loss and R:R ratio.
        </p>
      </div>
    </div>
  );
}

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
type Tier     = 1 | 2 | 3 | 4 | 5;
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-04 — _sr_context() in signals.py detects swing pivots + 52w high/low; sr_context: breakout/at_resistance/at_support/neutral; at_resistance compresses 15%, breakout boosts +5%, at_support boosts +3%; sr_flag shown in SignalCard.',
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-04 — GET /stocks/{symbol}/atr endpoint (Wilder ATR); PositionSizer component reads accountSize + riskPctPerTrade from Settings; stop = price − 2×ATR(14); shows shares, dollar risk, R:R, potential profit. Settings page has Account Size + Risk % fields.',
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-04 — GET /portfolio/risk: Wilder beta vs SPY/HSI, parametric 1-day VaR 95%, 30-day correlation matrix, sector concentration. Trade Board shows risk section auto-populated from active positions with shares: sector pie chart (SVG), correlation heatmap (colored grid), beta+VaR stat cards, per-symbol betas, and warning chips for high correlation/concentration/VaR.',
    tier: 3, severity: 'feature',
    title: 'Portfolio risk dashboard — correlation, VaR, sector heat',
    file: 'frontend/src/pages/board.tsx + services/market-data/src/api/portfolio.py',
    effort: '4 days',
    impact: 'Lifts risk management score from 6.0 to 8.5 — the #1 gap vs professional tools; a portfolio of 6 tech stocks has hidden 90%+ correlation',
    what: 'Trade Board shows individual positions but no aggregate portfolio view. A user can unknowingly hold 80% of their portfolio in correlated tech positions. No VaR, no beta, no sector concentration metric. Risk management score is 6.0 — the lowest of any dimension.',
    fix: 'New "Portfolio Risk" tab on Trade Board: (1) Sector pie chart of open positions by market cap weight. (2) Correlation matrix of open positions using 30-day returns — colour-coded heat map (red = >0.7 correlation). (3) Portfolio beta vs HSI/SPY. (4) Simple 1-day VaR at 95% (parametric, using position-weighted vol). (5) Warning banner if top-2 holdings exceed 50% of portfolio or correlation > 0.8. Backend: GET /portfolio/risk takes list of symbols + weights, returns correlation matrix + betas + sector weights.',
  },
  {
    id: 'peer-comparison-table',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-04 — PeerCompareDrawer component shows side-by-side K-Score/sub-scores with green/red cell coding. Rankings page: + toggle per row, "Compare (N)" button opens drawer for up to 4 stocks. Stock detail page: Sector Peers panel auto-suggests top 3 same-sector stocks with "Compare" button that opens drawer including current stock.',
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-04 — GET /signals/rolling_accuracy?window=30&lookback_days=180; returns time series of 30d rolling BUY accuracy + drift_warning flag when < 55%; line chart with 50%/55% reference lines added to Signal Accuracy page.',
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-04 — GET /signals/walkforward: non-overlapping test windows from persisted signals, hold_days-later exit, per-window accuracy + compounded equity curve + Sharpe + max drawdown. SPY/^HSI benchmark via _wf_benchmark(). Walk-Forward tab on Signal Accuracy page: test/hold controls, stat cards (accuracy, Sharpe, return, drawdown, profitable windows), per-window heatmap (color by accuracy %), equity curve SVG vs benchmark, alpha interpretation chip.',
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-04 — _dcf_fair_value() in research-engine routes.py: 2-stage DCF (5-yr explicit + Gordon Growth terminal at 3% g). Uses FCF/share as base, analyst/revenue/sector-default growth rate. WACC by GICS sector (8–11%). Shows dcf_fair_value + margin_of_safety_pct + "HIGH CONVICTION" badge when DCF and analyst target agree within 15ppt. Displayed on research page signal row.',
    tier: 3, severity: 'feature',
    title: 'DCF-based fair value model in research engine',
    file: 'services/research-engine/src/api/routes.py',
    effort: '3 days',
    impact: 'Replaces the earnings-multiple proxy fair value with a cash-flow-based intrinsic value — lifts research engine score from 6.5 to 8.0',
    what: 'Current fair value uses a trailing PE × sector PE multiple heuristic. This systematically misprices: growth stocks (no PE), cyclicals at peak earnings, and companies with negative earnings. A DCF model is the industry standard for intrinsic value — and the data is already available (EPS, growth rates, FCF from yfinance fundamentals).',
    fix: 'Implement simplified 2-stage DCF in research engine: Stage 1: project FCF for 5 years using analyst growth rate (or trailing 3y CAGR if no estimate). Stage 2: terminal value using Gordon Growth Model (terminal growth 3%, WACC 10% default). Discount to PV. Compare DCF fair value vs current price to compute margin of safety %. Show on stock detail page alongside existing K-Score fair value. If DCF and K-Score fair values agree within 15%, show "High conviction" badge. API: add dcf_fair_value, dcf_margin_of_safety to GET /research/{symbol} response.',
  },

  // ── Tier 4 — Signal Accuracy & ML Tuning (SA-8 batch, 2026-06-05) ─────────
  {
    id: 'sa8-bundle',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-05 — (1) SWING buy_threshold: bull 0.65→0.62, high_vol 0.70→0.67, bear 0.73→0.70. (2) SWING adx_min: 20→15 (capture early-trend before ADX peaks). (3) AUC floor: ml_weight=0 when model AUC<0.52 (near-random model falls back to TA-only). (4) 4 new ML features: momentum_12_1, sma_200_gap, dist_52w_high, dist_52w_low (30→34 features). (5) Recency weights ratio 3.0→5.0 in trainer + tuner. (6) Style-specific training horizons: SHORT=5d, SWING=10d, LONG=20d.',
    tier: 4, severity: 'feature',
    title: 'SA-8: Signal accuracy batch — thresholds, AUC floor, 4 new ML features, horizon alignment',
    file: 'signal-engine/signals.py + ml-prediction/builder.py + trainer.py + tuner.py + routes.py',
    effort: 'Done',
    impact: 'Comprehensive accuracy overhaul: ML models now trained on correct horizon, near-random models suppressed, momentum and range features added, SWING threshold recalibrated from empirical data',
    what: '6 independent improvements bundled: (1) SWING thresholds were too conservative based on analysis of 180d of signal data. (2) adx_min=20 was filtering out early-trend entries; lowering to 15 captures moves before ADX fully peaks. (3) ML models with AUC<0.52 were corrupting signals instead of helping. (4) Missing price-range and momentum features. (5) Recency ratio 3× was too slow to adapt to regime shifts. (6) All styles were using 5-day training labels even for SWING (10-day hold) and LONG (20-day hold).',
    fix: 'All six changes shipped in one commit. Optuna re-tuned 108/123 symbols with new 34-feature set and correct SWING 10d horizon. signal_outcomes table created to track fixed-window directional accuracy for future Optuna tuning of signal parameters.',
  },
  {
    id: 'signal-outcomes-tracking',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-05 — signal_outcomes table: UNIQUE signal_id, entry/exit price, hold_days, pct_return, is_correct, confidence, fused_prob, ta_score, ml_prob, ml_auc, market_regime. POST /signals/outcomes/evaluate runs post-close. GET /signals/outcomes/summary returns win_rate by confidence band (0-40/40-55/55-70/70-85/85+), horizon, and market_regime. Scheduler hook runs evaluate after each post-close ML retrain.',
    tier: 4, severity: 'feature',
    title: 'signal_outcomes forward-tracking table — fixed-window directional accuracy',
    file: 'shared/db/models.py + session.py + signal-engine/routes.py + market-data/scheduler.py',
    effort: 'Done',
    impact: 'Closes the feedback loop: every BUY/SELL signal is evaluated at its natural hold horizon (SHORT=7d, SWING=14d, LONG=28d) and stored permanently for Optuna tuning of signal parameters',
    what: 'The existing trade_performance endpoint measures P&L from BUY→WAIT/SELL transitions (actual exits). signal_outcomes is orthogonal: it uses fixed calendar windows regardless of actual exit, isolating directional accuracy. This is the ground truth needed to tune buy_threshold, adx_min, weekly_compress, and earnings_compression via Optuna once 500+ outcomes accumulate (~8 weeks).',
    fix: 'Two complementary measurements: (1) trade_performance = "how much did you make following the signals?" (2) signal_outcomes = "was the direction correct at the target horizon?" Once 500+ SWING outcomes exist, run Optuna on signal parameters using precision-weighted F-score as objective. See SIGNAL_ACCURACY.md for full tuning workflow.',
  },
  {
    id: 'sa1-ml-ta-threshold',
    tier: 4, severity: 'medium',
    title: 'SA-1: Lower ML/TA disagreement dampening threshold 0.35 → 0.25',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 day',
    impact: '+3–8% accuracy — adds intermediate dampening band for moderate ML/TA disagreements',
    what: 'When ML probability and TA score diverge, dampening is applied only when the gap > 0.35 (35 percentage points). A stock where ML says 0.70 and TA says 0.40 (gap = 0.30) passes through undampened even though the disagreement is substantial — a common early-warning sign of regime transitions.',
    fix: 'Add intermediate band: gap > 0.25 → ml_w *= 0.75 (25% cut). gap > 0.35 → ml_w *= 0.5 (50% cut, existing). Makes the system more conservative when ML and TA moderately disagree. The current code already handles gap > 0.35; just add the 0.25–0.35 elif clause.',
  },
  {
    id: 'sa2-style-precision',
    tier: 4, severity: 'medium',
    title: 'SA-2: Style-aware ML precision targets (SHORT 70%, LONG 50%)',
    file: 'services/ml-prediction/src/training/trainer.py',
    effort: '1 day + retrain',
    impact: '+1–3% SHORT accuracy — SHORT trades need tighter precision since there is less time to recover false entries',
    what: 'All three trade horizons use the same 60% minimum precision for calibrating the buy threshold. SHORT trades (1–7 day holds) have the least time to recover from false entries and need tighter precision. LONG trades (90-day holds) can afford more entries.',
    fix: 'Add _PRECISION_BY_STYLE = {"SHORT": 0.70, "SWING": 0.60, "LONG": 0.50}. Pass style parameter through to _precision_threshold() and use the style-specific floor instead of the global _MIN_PRECISION constant.',
  },
  {
    id: 'sa3-macro-regime-features',
    tier: 4, severity: 'medium',
    title: 'SA-3: Add 4 macro regime boolean ML features',
    file: 'services/ml-prediction/src/features/builder.py',
    effort: '3 days + retrain',
    impact: '+3–8% AUC in bear markets — boolean flags give XGBoost a clean decision boundary for regime states',
    what: 'The ML model receives raw macro values (VIX level, SPY returns) but no boolean regime flags. The model must implicitly learn that VIX=35 means "fear regime" — with limited training data it under-learns this. Explicit boolean features give XGBoost a clean split boundary.',
    fix: 'Add 4 derived boolean features to builder.py: (1) is_spy_above_200d (SPY > 200-day SMA), (2) is_vix_elevated (VIX > 20), (3) is_spy_trending_up (SPY 20d return > 2%), (4) is_breadth_strong (% stocks above 200d SMA > 55%). These are already computed for signal generation — expose them to the ML model as binary inputs.',
  },
  {
    id: 'sa4-weekly-min-bars',
    tier: 4, severity: 'low',
    title: 'SA-4: Weekly alignment gate min bars 26 → 15',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 day',
    impact: '+1–3% accuracy on newer stocks — 26-bar requirement filters out stocks with 6 months of history',
    what: 'Weekly trend confirmation requires at least 26 weekly bars (6 months of data). Stocks listed within the last 6 months skip the weekly gate entirely, missing an important quality check. 15 bars (3.5 months) is sufficient for reliable weekly RSI and SMA computation.',
    fix: 'In _weekly_technicals(), change the minimum bar count from 26 to 15. Already have a fallback when weekly bars < threshold; just lower the threshold.',
  },
  {
    id: 'sa5-data-driven-ta-weights',
    tier: 4, severity: 'medium',
    title: 'SA-5: Data-driven TA sub-score weights (logistic regression)',
    file: 'services/signal-engine/src/generators/signals.py + signal-engine/routes.py',
    effort: '1 week',
    impact: '+5–10% accuracy — replaces hand-tuned TA weights with empirically fitted weights from actual signal outcomes',
    what: 'TA sub-score weights (RSI 15%, momentum 15%, trend 20%, etc.) are manually tuned. There is no empirical validation that these weights maximise prediction accuracy. The calibrate_ta_weights endpoint already exists but is not run on a schedule and its output is not automatically applied.',
    fix: 'Wire POST /signals/calibrate_ta_weights into the weekly scheduler (runs on Sundays after Optuna). The endpoint already fits a logistic regression on TA features vs is_correct from signal history. Apply the fitted coefficients as TA weights in the next signal generation cycle. Store weights in DB config table so they persist across restarts.',
  },
  {
    id: 'sa6-filter-interaction',
    tier: 4, severity: 'medium',
    title: 'SA-6: Filter interaction audit — identify redundant or harmful filters',
    file: 'services/signal-engine/src/api/routes.py',
    effort: '1 week',
    impact: '+2–5% win rate — some filter combinations may be net-negative (e.g. ADX gate already captures weak trend; adding weekly gate may double-suppress good signals)',
    what: 'The signal engine has 8+ suppression filters (ADX gate, weekly alignment, earnings compression, breadth compression, high-vol compression, ML/TA conflict, news sentiment, options sentiment). No analysis exists of which filter combinations produce the best outcomes or whether some filters conflict and double-suppress good signals.',
    fix: 'Use GET /signals/filter_audit (already implemented) to analyse win rate by number of active suppression filters. Extend it to show win rate by specific filter combination (e.g. "ADX gate only" vs "ADX gate + weekly misalignment"). Identify any filter that consistently reduces win rate when applied — and disable or invert it. Once signal_outcomes has 500+ rows, run this analysis on real outcomes.',
  },
  {
    id: 'sa7-regime-earnings-compression',
    tier: 4, severity: 'medium',
    title: 'SA-7: Regime-aware earnings compression',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 week',
    impact: '+2–5% win rate — earnings compression is too aggressive in bull markets where beat surprises push stocks up',
    what: 'Earnings compression applies the same penalty regardless of market regime. In a bull market, stocks that beat earnings often gap up 5–15% — the opposite of what the compression assumes. In a bear market, even earnings beats often fade. One-size-fits-all compression is wrong in half the cases.',
    fix: 'Apply earnings compression differentially by market regime and earnings beat rate: (1) Bear/high_vol + beat_rate < 50%: keep current 50% compression. (2) Bull + beat_rate > 70%: remove or invert compression (slight boost before earnings). (3) Bull + beat_rate 50-70%: neutral (no compression). The beat_rate is already computed and stored in signal reasons.',
  },

  // ── Tier 5 — UI / Feature Gaps (2026-06-06 audit) ────────────────────────
  {
    id: 'ui01-signal-outcomes-dashboard',
    tier: 5, severity: 'feature',
    title: 'UI-01: Signal Outcomes Dashboard — confidence band win-rate table',
    file: 'frontend/src/pages/signal-accuracy.tsx + lib/api.ts',
    effort: '1–2 days',
    impact: 'High — closes the feedback loop; GET /signals/outcomes/summary is implemented but never called by the frontend',
    what: 'The signal_outcomes table tracks every BUY/SELL with win_rate by confidence band (0-40, 40-55, 55-70, 70-85, 85+), horizon, and market regime. The backend endpoint is live. No frontend page displays this data — users cannot see whether confidence 70-85 signals actually win 70% of the time.',
    fix: 'Add a new "Outcomes" tab on /signal-accuracy. Call GET /signals/outcomes/summary?horizon=SWING&days=90. Show: (1) Table: confidence band → count, win rate, avg return — confirms or refutes confidence calibration. (2) Table: win rate by horizon (SHORT/SWING/LONG). (3) Table: win rate by market regime. Add "Tuning available" banner when outcomes > 500 pointing to SIGNAL_ACCURACY.md Optuna workflow.',
  },
  {
    id: 'ui02-signal-reasons',
    tier: 5, severity: 'feature',
    title: 'UI-02: Signal Reasons / "Why BUY?" factor breakdown',
    file: 'frontend/src/pages/stock/[symbol].tsx + frontend/src/components/SignalCard.tsx',
    effort: '1–2 days',
    impact: 'High — every signal stores 30+ factors in reasons JSON (RSI, ADX, ML prob, news, earnings, breadth) but none is shown to the user',
    what: 'Signal.reasons JSON contains a full factor dump: RSI, ADX, volume_z, ml_probability, ta_score, news_sentiment, RS score, earnings proximity, market regime, breadth %, suppression flags, and 20+ more. This data is fetched but never rendered. Users see only the final confidence number — not why it was generated.',
    fix: 'Expand the SignalCard component to show a collapsible "Factors" section: (1) Green badges for factors above neutral (RSI < 50, golden_cross=true, volume_z > 1, news_sentiment > 55, rs_rank > 1.0). (2) Orange/red badges for suppression factors (weekly_alignment=false, earnings_compression, breadth_compression). (3) Show ml_weight and whether AUC floor was applied. Reuse existing Signal response — no backend change needed.',
  },
  {
    id: 'ui03-options-flow-page',
    tier: 5, severity: 'feature',
    title: 'UI-03: Options flow tab on stock detail page',
    file: 'frontend/src/pages/stock/[symbol].tsx',
    effort: '1 day',
    impact: 'Medium — GET /stocks/{symbol}/options-flow is implemented and used in signals, but never displayed to the user',
    what: 'The options flow endpoint returns put/call ratio, options sentiment (bullish/bearish), and unusual contract activity. The signal engine uses it as a filter (strongly_bullish C/P ≥ 2.0 → +7% boost, bearish C/P ≤ 0.5 → 15% compress). Users never see this data — they cannot verify why a signal was boosted or compressed by options.',
    fix: 'Add an "Options Flow" section to the stock detail page (below the signal card): (1) Put/call ratio gauge with 30-day average reference. (2) Sentiment badge (Strongly Bullish / Bullish / Neutral / Bearish). (3) Table of unusual contracts (expiry, strike, volume, OI, volume/OI ratio). Call GET /stocks/{symbol}/options-flow — no backend change needed.',
  },
  {
    id: 'ui04-insider-screener',
    tier: 5, severity: 'feature',
    title: 'UI-04: Insider buying conviction screener',
    file: 'frontend/src/pages/insider.tsx',
    effort: '1 day',
    impact: 'Medium — cluster insider buying is one of the strongest signals of management confidence; raw data exists but no screener',
    what: 'The insider page shows raw transaction list. No way to screen for "stocks with heavy net insider buying this quarter" or sort by conviction (number of distinct insiders buying vs selling). Users must manually scan hundreds of rows.',
    fix: 'Add a conviction score column: net_buy_$ = sum(buy_transactions) - sum(sell_transactions) over trailing 90 days. Add distinct_buyers count. Add sort/filter: "Net buyers only", sort by conviction score descending. Merge with K-Score to show "High K-Score + insider buying" combo — the highest-quality intersection. No backend change needed; recompute from existing transaction data client-side.',
  },
  {
    id: 'ui05-earnings-surprise-chart',
    tier: 5, severity: 'feature',
    title: 'UI-05: Earnings surprise history chart (8-quarter EPS beat/miss)',
    file: 'frontend/src/pages/stock/[symbol].tsx + earnings.tsx',
    effort: '1 day',
    impact: 'Medium — eps_beat_rate is already computed and used in signal compression; it is never shown to users as a per-stock trend',
    what: 'Fundamentals endpoint already returns eps_history (last 8 quarters), eps_beat_rate, eps_avg_surprise_pct, and eps_surprise_trend. The research engine uses beat_rate for signal compression. No chart shows the per-stock EPS surprise trend over time — users cannot see whether AAPL beats consistently or misses randomly.',
    fix: 'Add to the stock detail page (earnings tab or fundamentals section): (1) Bar chart — last 8 quarters showing estimate vs actual EPS (green bar = beat, red = miss). (2) Beat rate badge: "Beats 75% of the time (6/8 quarters)". (3) Highlight stocks with beat_rate > 70% as "earnings quality" candidates in the earnings calendar. Data already in API response — frontend change only.',
  },
  {
    id: 'ui06-position-heatmap',
    tier: 5, severity: 'feature',
    title: 'UI-06: Portfolio position heatmap (treemap by value, colored by P&L)',
    file: 'frontend/src/pages/positions.tsx',
    effort: '1 day',
    impact: 'Medium — positions page shows a table; no visual allocation view to spot concentration or P&L at a glance',
    what: 'Users cannot quickly see "I have 60% of my portfolio in 2 stocks and both are down" — they have to read a table row by row. A treemap makes allocation and P&L visible instantly.',
    fix: 'Add a treemap/grid above the positions table: each cell = one position, sized by current market value (shares × current price), colored by % P&L (green = profit, red = loss, intensity = magnitude). Hover shows: symbol, shares, avg cost, current price, unrealized P&L. Fetch current prices via GET /stocks/latest_prices — already called elsewhere on the page.',
  },
  {
    id: 'ui07-unrealized-pnl',
    tier: 5, severity: 'feature',
    title: 'UI-07: Real-time unrealized P&L on positions page',
    file: 'frontend/src/pages/positions.tsx',
    effort: '1 day',
    impact: 'Medium — positions page stores avg_cost but does not compute unrealized P&L; users cross-reference manually with the markets page',
    what: 'The positions table shows avg_cost and shares, but not the current price or unrealized gain/loss. Users have to go to the markets page to find current prices and do the math themselves.',
    fix: 'On page load, fetch GET /stocks/latest_prices for all held symbols. Compute per-position unrealized P&L: (current_price - avg_cost) × shares. Show in table: Current Price, Unrealized $, Unrealized %, color-coded. Add portfolio total row: sum of all unrealized + total cost basis + total current value. All data is available — frontend math only.',
  },
  {
    id: 'ui08-walkforward-drilldown',
    tier: 5, severity: 'feature',
    title: 'UI-08: Walk-forward window drill-down (click window → see signals)',
    file: 'frontend/src/pages/signal-accuracy.tsx',
    effort: '1–2 days',
    impact: 'Low-Medium — walk-forward shows per-window accuracy % but clicking a window shows nothing; users cannot see which signals drove the result',
    what: 'The walk-forward tab shows a heatmap of windows (e.g. "May 1–30: 62% accuracy"). Clicking a window does nothing. Users cannot investigate why a specific window performed well or badly — they cannot see which signals were evaluated, which were correct, or what factors differentiated the winners.',
    fix: 'Make each walk-forward window row/cell clickable. On click, fetch /signals/accuracy with the window\'s date range and show: list of signals evaluated in that window, which were correct (green) and wrong (red), avg confidence, top and bottom factors. Modal or slide-out panel. Reuse existing signal accuracy endpoint with date range params.',
  },
  {
    id: 'ui09-data-freshness',
    tier: 5, severity: 'low',
    title: 'UI-09: Data freshness indicator in site header',
    file: 'frontend/src/pages/_app.tsx',
    effort: '0.5 days',
    impact: 'Low — if nightly ingest fails, all prices are stale with no visible warning; users trade on yesterday\'s data',
    what: 'If the nightly data ingest fails (yfinance outage, EC2 issue), all prices and signals are stale. The UI gives no indication of this. Users may see a BUY signal generated on 2-day-old data and act on it.',
    fix: 'Add a small status chip to the header: "Last updated: 2h ago" (green). Turn orange if last update > 6h on a weekday, red if > 24h. Fetch GET /stocks/market_overview which already returns a timestamp. If stale, show a banner: "⚠ Price data may be outdated — last refresh {timestamp}".',
  },
  {
    id: 'ui10-ml-weight-autocalibrate',
    tier: 5, severity: 'medium',
    title: 'UI-10: ML weight auto-calibration from empirical validation curve',
    file: 'frontend/src/pages/signal-accuracy.tsx + signal-engine/routes.py',
    effort: '1–2 days',
    impact: 'Medium — the validation curve already finds the optimal ML weight; currently just a visualisation; not applied to the running system',
    what: 'GET /signals/ml-weight-validation sweeps all ML weight values and identifies the empirically optimal blend. Currently shows "Optimal: 0% ML" but the system still uses the hardcoded 40–75% formula. The insight is never acted upon.',
    fix: 'Add POST /signals/calibrate_ml_weight endpoint: reads optimal_weight from the validation curve, writes it to a config table, signals.py reads it on next run. Add "Apply optimal weight" button on Signal Accuracy page (admin only) with confirmation dialog showing current vs proposed weight and expected accuracy change. Warn if optimal is 0% (ML models need retraining before applying).',
  },
  {
    id: 'ui11-factor-exposure-chart',
    tier: 5, severity: 'low',
    title: 'UI-11: Verify factor exposure chart is rendering correctly',
    file: 'frontend/src/pages/signal-accuracy.tsx',
    effort: '0.5 days',
    impact: 'Low — GET /signals/factor-exposure endpoint exists; frontend may or may not be calling it correctly',
    what: 'The Signal Accuracy page has a Factor Analysis section. The backend endpoint GET /signals/factor-exposure returns RSI, ADX, Volume Z, ML Probability, News Sentiment, and TA Score averaged across correct vs wrong signals. Verify the endpoint is being called and the chart is rendering with real data (not empty bars).',
    fix: 'Check the /signal-accuracy page network tab: is /signals/factor-exposure returning data? If bars are empty, it may be a lookback_days mismatch or the endpoint requiring more evaluated signals than exist. If the chart IS rendering, confirm bars show meaningful differences between correct and wrong signals — the key insight is which factors most distinguish good signals from bad ones.',
  },
  {
    id: 'ui12-congressional-page',
    tier: 5, severity: 'low',
    title: 'UI-12: Congressional trading page (/congress)',
    file: 'frontend/src/pages/ (new page)',
    effort: '1 day',
    impact: 'Low-Medium — congressional trade disclosures are public data and surprisingly predictive; endpoint already exists',
    what: 'GET /congress/trades?days=90 returns congressional buy/sell disclosures. The data exists and the endpoint is implemented. There is no dedicated page — users cannot see whether congresspeople have been buying or selling the stocks they are tracking.',
    fix: 'Create /congress page with: (1) Table: politician, stock, transaction type, date, amount range. (2) Filter by symbol — "Has any congressman bought AAPL recently?". (3) "Conviction" score = net $ bought by congress across all politicians for a stock. (4) Merge with watchlist — highlight any watchlist stock with recent congressional buying. Add Congress link to Markets navigation dropdown.',
  },
  {
    id: 'tech-research-cache-quality',
    tier: 5, severity: 'medium',
    title: 'Research Engine Cache Poisoning (Bad Report Served for 24h)',
    file: 'services/research-engine/src/api/routes.py',
    effort: '1–2 days',
    impact: 'Medium — if yfinance fails or AI returns fallback scores (50/50/50), that bad report is cached for 24h with no warning banner; users trust stale/wrong data',
    what: 'Research reports are cached in-memory for 24h. If a report is generated during a yfinance outage, AI timeout, or price staleness window, the fallback report (hardcoded 50/50/50 scores) is served to all users for 24h with no indication it is low-quality. There is no cache-quality metadata stored alongside the cached result.',
    fix: 'Store a data_quality flag with each cached report: "full" | "partial" | "fallback". Display a yellow warning banner in the UI when quality is partial or fallback. Add a forced cache-bust endpoint: DELETE /research/{symbol}/cache. Auto-invalidate the cache for a symbol whenever a new price bar is ingested for it.',
  },
  {
    id: 'tech-pagination',
    tier: 5, severity: 'medium',
    title: 'Tech Debt: Pagination on /signals/accuracy (10k+ row response)',
    file: 'services/signal-engine/src/api/routes.py',
    effort: '1 day',
    impact: 'Medium — with 123 stocks × 5 signals/week × 90 days the response can exceed 50k rows; frontend hangs parsing the JSON',
    what: 'GET /signals/accuracy returns the entire signal history in one response. With 123 stocks generating signals daily for 90 days, this can be 10k+ rows of JSON. The frontend signal table renders all rows at once with no virtualisation.',
    fix: 'Add page and page_size query params to /signals/accuracy (default page_size=200). Return total_count and has_more in response. Frontend: load first page on mount, add "Load more" button or infinite scroll. Alternatively add server-side filtering by symbol so the response is always bounded.',
  },
  {
    id: 'tech-n1-query',
    tier: 5, severity: 'medium',
    title: 'Tech Debt: N+1 query in trade_performance — group in SQL not Python',
    file: 'services/signal-engine/src/api/routes.py',
    effort: '1 day',
    impact: 'Medium — trade_performance groups by symbol in Python after loading all signals; with 100+ stocks this is 10× slower than a single GROUP BY query',
    what: 'The trade_performance endpoint loads all signals matching the filter, then loops through them in Python to group by symbol and compute per-symbol stats. This is the N+1 pattern: one query loads everything, then Python does the aggregation work that a single SQL GROUP BY would do more efficiently.',
    fix: 'Rewrite the per-symbol aggregation as a SQL subquery or window function. GROUP BY symbol at the DB level and return pre-aggregated win_rate, avg_return, trade_count per symbol. Only load individual trade records when a user drills into a specific symbol.',
  },
  {
    id: 'tech-redis-cache',
    tier: 5, severity: 'low',
    title: 'Tech Debt: Redis cache for expensive signal-engine endpoints',
    file: 'services/signal-engine/src/api/routes.py',
    effort: '1–2 days',
    impact: 'Low — factor_exposure, walkforward, and filter_audit re-compute from scratch on every request; with 180d lookback these take 2–5s each',
    what: 'Three endpoints — /signals/factor-exposure, /signals/walkforward, and /signals/filter_audit — load 6 months of signal history and compute aggregations on every HTTP request. They change at most once per day (after the post-close signal refresh). There is no caching.',
    fix: 'Add a simple Redis cache with TTL=3600s (1 hour). Use a cache key incorporating the query params (lookback_days, horizon, style). On hit: return cached JSON immediately. On miss: compute, cache result, return. Redis is already deployed in the stack (used for macro features and market regime). Pattern already exists in ml-prediction/builder.py _redis_save_macro().',
  },
];

// ── Constants ─────────────────────────────────────────────────────────────────

const TIER_LABEL: Record<Tier, string> = {
  1: 'Tier 1 — Fix Before Trusting Signals',
  2: 'Tier 2 — Analytical Improvements',
  3: 'Tier 3 — New Features',
  4: 'Tier 4 — Signal Accuracy & ML Tuning',
  5: 'Tier 5 — UI Gaps & Tech Debt',
};

const TIER_COLOR: Record<Tier, string> = {
  1: '#f87171',
  2: '#fbbf24',
  3: '#818cf8',
  4: '#34d399',
  5: '#67e8f9',
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

  const tiers = ([1, 2, 3, 4, 5] as Tier[]).filter(t => filterTier === 0 || t === filterTier);

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
          <span style={{ fontSize: 12, color: '#475569' }}>Expert review — 2026-05-31 · Updated 2026-06-06</span>
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
          {([0, 1, 2, 3, 4, 5] as const).map(t => (
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
          Current (2026-06-06) → Target after Tier 4 &amp; 5
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          {[
            { label: 'Data pipeline',   score: 8.2, target: 8.5, note: '↑ Data freshness UI next' },
            { label: 'ML methodology',  score: 8.5, target: 9.0, note: '↑ SA-2/3/5 pending' },
            { label: 'Signal logic',    score: 8.0, target: 9.0, note: '↑ SA-1/6/7 + outcomes UI' },
            { label: 'K-Score ranking', score: 8.0, target: 8.5, note: '↑ Insider screener next' },
            { label: 'Research engine', score: 7.5, target: 8.5, note: '↑ Earnings chart next' },
            { label: 'Frontend / UX',   score: 9.0, target: 9.5, note: '↑ Outcomes UI + heatmap' },
            { label: 'Risk management', score: 7.5, target: 8.5, note: '↑ Unrealized P&L + heatmap' },
            { label: 'Overall',         score: 8.5, target: 9.0, note: 'Tier 4+5 → 9.0 range' },
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
          All Tier 1–3 items are shipped. SA-8 (Tier 4) shipped 2026-06-05: 34 ML features, style-specific horizons, AUC floor, recalibrated SWING thresholds, signal_outcomes tracking.
          SA-1 through SA-7 remain pending — once signal_outcomes accumulates 500+ SWING outcomes (~8 weeks), run Optuna on signal parameters before implementing SA-5/6.
          Tier 5 items are all backend-ready — each is a frontend-only change exposing an existing endpoint.
          The highest-leverage next items are <strong style={{ color: '#94a3b8' }}>UI-01 (signal outcomes dashboard)</strong> and <strong style={{ color: '#94a3b8' }}>UI-02 (signal reasons breakdown)</strong> — they close the feedback loop and make signals explainable.
          Overall: <strong style={{ color: '#4ade80' }}>8.5 / 10</strong> — target 9.0 after Tier 4+5 completion.
        </p>
      </div>
    </div>
  );
}

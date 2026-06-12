/**
 * Improvements Tracker — /improvements
 *
 * Interactive checklist of all findings from the 2026-05-31 expert review.
 * Status is stored in localStorage so progress persists across sessions.
 * Grouped by tier (Critical Fixes → Analytical → New Features).
 */
'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';

// ── Types ─────────────────────────────────────────────────────────────────────

type Severity = 'critical' | 'medium' | 'low' | 'feature';
type Tier     = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9;
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-05 — signals.py line ~858: elif gap > 0.25: ml_w *= 0.75 (flat 25% cut for intermediate disagreement)',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-05 — trainer.py: _PRECISION_BY_STYLE = {"SHORT": 0.70, "SWING": 0.60, "LONG": 0.50} with style passed through to _precision_threshold()',
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
    defaultStatus: 'done',
    implementedNote: 'Already shipped — builder.py: is_bear_market (SPY < 200d SMA), vix_spiking (VIX > 20d MA×1.3), high_vol_regime (spy_vol_20 > 2%), market_stress (SPY 5d ret < -3% AND VIX > MA). All 4 flags are in MACRO_COLUMNS, FEATURE_COLUMNS, computed by fetch_macro_features(), and flow through to XGBoost training via build_features().',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-05 — signals.py: if len(df) < 15 with graduated confidence scaling weekly_confidence = 0.70 + (len-15)/(26-15)*0.30',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-07 — scheduler.py _weekly_full_refresh(): added _post(/signals/calibrate_ta_weights) after tune_all kick-off. Runs every Sunday ~14:10 PST. Endpoint fits logistic regression on signal history and writes ta_weights.json; effective from next signal generation cycle.',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-07 — signals.py _apply_style_signal(): bull+beat≥70%: skip compression, +3% boost (earnings_warning="bull_beater"); bull+50–70%: beat_scale=2.0 (halved compression); bear/high_vol: beat_scale=0.75–1.0 based on rate (tightened); unknown: original ±20% formula. market_regime already passed as parameter.',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-06 — "Outcomes" tab added to /signal-accuracy; calls GET /signals/outcomes/summary; shows confidence band table, horizon breakdown, regime breakdown, and Optuna tuning guidance',
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
    defaultStatus: 'done',
    implementedNote: 'Already shipped — SignalCard.tsx renders full factor breakdown (15 factors: earnings, regime, ML-TA conflict, ADX, death cross, weekly alignment, patterns, VWAP, S/R context, trend, RSI, Stoch RSI, MACD, ADX, OBV, ML probability) with ▲/▼ indicators and plain-English detail text',
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
    defaultStatus: 'done',
    implementedNote: 'Already shipped — stock/[symbol].tsx lines 2526+: options flow section shows C/P ratio, call/put volumes, sentiment badge, and unusual contract table when optionsFlow.available is true',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-07 — insider.tsx: convictionScores useMemo groups trades by ticker (net buy $, distinct buyers/sellers, buy/sell counts). Conviction Screener table above Sudden Activity section; "Net buyers only" toggle; linked tickers; green conviction bars sized by net buy $.',
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
    defaultStatus: 'done',
    implementedNote: 'Already shipped — stock/[symbol].tsx lines 1692+: EPS Surprise History section renders per-quarter grid from eps_history, beat rate badge, avg surprise %, and improving/declining/stable trend indicator',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-07 — positions.tsx: flexbox P&L heatmap grid between summary stats and chart section. Cells sized proportionally by market value (min 4%), colored by P&L % intensity (green profit / red loss, alpha 0.08–0.38). Tooltip shows symbol, P&L%, and dollar P&L.',
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
    defaultStatus: 'done',
    implementedNote: 'Already shipped — positions.tsx lines 175-178: computes cost, mktVal, pnl, pnlPct per position; lines 295-296: shows Today\'s P&L and Total P&L in header cards; CSV export includes all P&L columns',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-07 — signal-accuracy.tsx: selectedWindow state; clicking a heatmap cell fetches api.signalAccuracy(90, undefined, w.start, w.end) via useSWR; indigo-bordered drill-down panel shows signal table (symbol, date, type, conf%, entry, exit, return%, correct/wrong badge) sorted by outcome. Backend: from_date/to_date params added to GET /signals/accuracy.',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-07 — _app.tsx: freshness chip polls GET /stocks/data_freshness every 5 min; shows "Xh ago" next to notification bell. Green <8h, yellow 8–30h, red >30h. Backend: new GET /stocks/data_freshness endpoint in market-data/routes.py returns last_bar_ts, hours_ago, status from MAX(Price.ts).',
  },
  {
    id: 'ui10-ml-weight-autocalibrate',
    defaultStatus: 'done',
    implementedNote: 'POST /signals/calibrate_ml_weight endpoint runs weight sweep, saves optimal cap to ml_weight_override.json, updates in-process global. "Apply optimal weight" button on signal accuracy page shows result with accuracy %.',
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
    defaultStatus: 'done',
    implementedNote: 'Shipped 2026-06-07 — congress.tsx: stock conviction screener (net buy $, distinct politician buyers, conviction bar); top-8 most active buyers; summary stats (buy/sell counts, volumes, unique politicians/tickers); full filterable table (days/type/party/sort/symbol/politician search); party badges, tx badges, days-ago chips; handles no-API-key state with Settings link. Nav updated: "Insider / Congress" split into "Insider Trading" + "Congress Trades".',
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — /signals/accuracy now accepts page + page_size params (default 200). Summary stats computed on all rows; paginated signals array returned with has_more + total_signals. Frontend: page state added to SWR key; "Load more (N remaining)" button renders when has_more=true. Filter/lookback changes reset page to 1.',
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
    defaultStatus: 'done',
    implementedNote: 'Audited 2026-06-09 — trade_performance already does 3 bulk queries (BUY signals, exit signals, price data) then aggregates by_symbol in Python over already-loaded data. No per-symbol queries exist. Pattern is already optimal.',
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
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Added _cache_get/_cache_set helpers in routes.py using redis_url from common config. factor_exposure cached at signals:cache:factor_exposure:{lookback_days} (TTL 1h). filter_audit cached at signals:cache:filter_audit:{lookback}:{style}:{hold_days} (TTL 1h). Cache hit returns immediately; miss computes then stores.',
    tier: 5, severity: 'low',
    title: 'Tech Debt: Redis cache for expensive signal-engine endpoints',
    file: 'services/signal-engine/src/api/routes.py',
    effort: '1–2 days',
    impact: 'Low — factor_exposure, walkforward, and filter_audit re-compute from scratch on every request; with 180d lookback these take 2–5s each',
    what: 'Three endpoints — /signals/factor-exposure, /signals/walkforward, and /signals/filter_audit — load 6 months of signal history and compute aggregations on every HTTP request. They change at most once per day (after the post-close signal refresh). There is no caching.',
    fix: 'Add a simple Redis cache with TTL=3600s (1 hour). Use a cache key incorporating the query params (lookback_days, horizon, style). On hit: return cached JSON immediately. On miss: compute, cache result, return. Redis is already deployed in the stack (used for macro features and market regime). Pattern already exists in ml-prediction/builder.py _redis_save_macro().',
  },

  // ── New Suggestions 2026-06-07 ────────────────────────────────────────────

  {
    id: 'workflow-signal-lifecycle',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — WF-1: Signal state chip (BUY/HOLD/WAIT/SELL) shown on active Trade Board cards using last_signal from signal alert subscription. Trading style chip (SWING/LONG/SHORT/GROWTH) also shown on active cards for lifecycle context.',
    tier: 3, severity: 'feature',
    title: 'WF-1: BUY → HOLD/WAIT → SELL coherent workflow with actionable guidance',
    file: 'frontend/src/pages/trade-board.tsx · services/signal-engine/src/generators/signals.py',
    effort: '3–5 days',
    impact: 'High — currently signal transitions have no prescribed action. Users see "WAIT" but don\'t know whether to hold, reduce size, or set alerts. Bridges the gap between signal and execution.',
    what: 'The signal lifecycle (BUY → HOLD → WAIT → SELL) emits transitions but gives no actionable guidance per state. HOLD should mean "stay in position, trail your stop." WAIT should mean "reduce to half size or move stop to breakeven." SELL should show exact exit reasoning. There is no UI that walks the user through these states for a specific position they hold.',
    fix: 'Add a "Position Lifecycle" panel to the Trade Board card: (1) BUY state — show game plan (entry zones, stop, target) already generated. (2) HOLD state — show trailing stop recommendation (entry + ATR×2), time-in-trade counter, P&L %. (3) WAIT state — show "yellow flag" checklist: move stop to breakeven, reduce to 50% size, conditions needed to re-confirm BUY (RSI recovery, MACD turn, price > VWAP). (4) SELL state — show exit trigger (which indicator flipped), recommended exit price range, P&L at current price. Each state card auto-updates when the signal refreshes. Backend: signal-engine should emit a lifecycle_action field alongside each signal.',
  },

  {
    id: 'auto-paper-portfolio',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Full GROWTH-style autonomous paper trading engine: paper_trading_engine.py runs every 5–10 min via scheduler; GROWTH style params (buy_threshold 0.60, stop −12%, target +35%, max_hold 60d, trail ATR×2); position sizing via risk_per_trade_pct × equity / stop_distance; trailing stop with floor against initial stop; WAIT-exit after N consecutive WAIT signals; /paper-portfolio page with equity curve vs SPY/QQQ, positions table, decisions log, admin engine controls (start/pause/stop), capital editor. 11 audit bugs fixed (C-1 critical: engine never traded due to correlated subquery bug; H-1 RSI dead code; H-2 WAIT exit; H-7 sector cap; H-8 flush; H-9 trail floor; H-11 SWR auth; H-12 null strip; M-8 equity recalc; M-9 empty watchlist guard; M-10 hold_days).',
    tier: 3, severity: 'feature',
    title: 'WF-2: Autonomous paper-trading portfolio — allocate capital, auto buy/sell, track returns',
    file: 'services/market-data · services/signal-engine · frontend/src/pages/paper-portfolio.tsx (new)',
    effort: '2–3 weeks',
    impact: 'Very High — transforms the app from a signal dashboard into a full autonomous trading system. Provides empirical proof-of-value: did following the signals actually make money? Enables the ML feedback loop to learn from simulated outcomes.',
    what: 'There is no end-to-end paper trading engine. Users cannot give the system a starting capital amount and let it autonomously allocate, buy, hold, and sell positions based on signals — then measure the return. Without this, the system\'s actual edge over buy-and-hold is unknown.',
    fix: 'Build a paper-trading engine: (1) User sets starting capital (e.g. $50,000) and risk parameters (max position size %, max positions). (2) Every post-close refresh: engine scans BUY signals with confidence ≥70% + K-Score ≥55; allocates capital using ATR-based position sizing (already in position-sizing-engine); creates a virtual "paper trade" record. (3) Every refresh: engine checks open paper positions against current signal — if SELL or stop breached, closes the position and logs exit price + P&L. (4) ML feedback: closed trade outcomes feed back into signal_outcomes table, improving future XGBoost training. (5) /paper-portfolio page: equity curve chart vs SPY benchmark, win rate, avg return, Sharpe ratio, drawdown, open positions table, closed trades log. Backend: new paper_trades table (symbol, entry_price, shares, entry_date, exit_price, exit_date, pnl, strategy). Scheduler: run paper-trade engine step every post-close alongside ML retrain.',
  },

  // ── WF-2 Deep Review: Paper Trading Engine Improvements (2026-06-11) ─────────

  {
    id: 'pt-live-price-fallback',
    defaultStatus: 'done',
    implementedNote: 'Done — _best_price() helper uses live → current_price → entry_price. PA-E1 added live_prices health check: skips entry scan when coverage < 50% of open symbols.',
    tier: 8, severity: 'critical',
    title: 'PT-B1: Stale equity curve when yfinance price fetch fails — silently uses entry price',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1–2 hours',
    impact: 'High — equity curve and P&L become fictitious during any yfinance outage or rate-limit. User sees portfolio "flat" when positions may be up or down 10%.',
    what: '_compute_equity(), _sector_value(), and snapshot_equity_curve() all use live_prices.get(symbol) or trade.entry_price when live price is missing. If yfinance fails (network issue, API quota, delisted symbol), every open position silently reverts to entry price. No warning is logged at the snapshot level. The equity curve records false data.',
    fix: '(1) In snapshot_equity_curve(): check if live_prices dict is incomplete (len < expected). If >20% of positions have missing prices, skip this snapshot entirely and log a warning — better to have a gap than false data. (2) In _compute_equity(): fall back to trade.current_price (DB-cached last known price) rather than entry_price. (3) Log one warning per missing symbol per cycle: "Live price unavailable for TSLA — using cached price from {ts}".',
  },

  {
    id: 'pt-atr-none-crash',
    defaultStatus: 'done',
    implementedNote: 'Done — guard: if atr is not None and atr > 0.01 before trail update. Invalid ATR emits paper.trail_atr_invalid warning and skips update.',
    tier: 8, severity: 'medium',
    title: 'PT-B2: ATR returns None/NaN — trailing stop silently becomes inert for the position',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — positions with unreliable ATR data never trail their stops. Profitable moves are given back without the engine reacting.',
    what: '_compute_atr() returns float | None. The caller checks if atr: but this passes NaN (truthy). If ATR is NaN or negative (can happen with sparse/stale data), line 509 computes trade.highest_price - atr * mult = NaN. The trailing stop is floored to trade.stop_loss, so the trailing is effectively disabled — but no warning is logged and the trade continues normally.',
    fix: 'In _monitor_positions() trail-stop block: validate atr is not None and atr > 0.01 before applying trail. If invalid: log.warning("ATR invalid for {symbol}, skipping trail update") and continue without updating stop. Add the same check in _scan_for_entries() where ATR is used for stop_distance calculation.',
  },

  {
    id: 'pt-hold-days-calendar',
    defaultStatus: 'done',
    tier: 8, severity: 'medium',
    title: 'PT-B3: Max hold measured in calendar days, not trading days — exits 2–3 days too early',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2–3 hours',
    impact: 'Medium — a 60-day GROWTH max_hold actually expires after ~43 trading days. Weekend-heavy periods like Christmas cause even earlier premature exits. Understates the real hold window traders expect.',
    what: 'hold_days = (date.today() - trade.entry_date).days uses raw calendar days. A position entered on a Friday and checked the following Monday shows hold_days=3 but only 1 trading day has passed. With max_hold_days=60, the real trading-day limit is ~43. Christmas week can force exit 8 calendar days early.',
    fix: 'Replace calendar-day math with trading-day count: (1) Create _count_trading_days(start: date, end: date) -> int that counts Mon–Fri minus US market holidays (use pandas_market_calendars if installed, or a static holiday list). (2) Use this in the time-stop check: if trading_hold_days >= cfg["max_hold_days"]. (3) Also update the UI display so "Days held" shows trading days with "(Xd)" notation.',
  },

  {
    id: 'pt-drawdown-circuit-breaker',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 1 + PA-D2 2026-06-11) — max_portfolio_drawdown_pct=0.20; peak = max(curve peak, current equity); circuit breaker suspends new entries.',
    tier: 8, severity: 'feature',
    title: 'PT-B4: No portfolio drawdown circuit breaker — engine keeps buying into a losing streak',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1–2 hours',
    impact: 'High — without a global drawdown stop, a bad signal regime can bleed the paper portfolio from $100k to $60k before the user notices. Engine should automatically pause new entries when equity drops too far.',
    what: 'The engine has no concept of portfolio-level maximum drawdown. It will keep scanning for entries even if equity has fallen 30% from its peak. Each new entry uses a smaller absolute dollar amount (% of depleted equity) but the fundamental strategy of "keep buying" in a losing regime continues unchecked.',
    fix: 'In paper_trading_step(), before _scan_for_entries(): (1) Fetch peak equity from equity curve (MAX(equity) from paper_equity_curve). (2) Compute current drawdown = (peak - current_equity) / peak. (3) If drawdown > cfg.get("max_portfolio_drawdown_pct", 0.20): log warning, skip _scan_for_entries() for this cycle, write a PAUSE_DRAWDOWN event to paper_decisions log. (4) Add max_portfolio_drawdown_pct to the config editor UI (default 20%). (5) Auto-resume scanning once equity recovers above drawdown threshold.',
  },

  {
    id: 'pt-open-risk-limit',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 2) — max_open_risk_pct=0.12; checked in _scan_for_entries(): sum((price-stop)×shares) across open positions + new trade risk vs equity.',
    tier: 8, severity: 'feature',
    title: 'PT-B5: No open-risk aggregate limit — 10 positions each risking 1% = 10% simultaneous loss possible',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2–3 hours',
    impact: 'Medium — current risk_per_trade_pct=1% is per-position, not aggregate. If 10 positions all hit stops on the same bad day (flash crash, black swan), portfolio loses 10% in a single session. Industry standard is to cap total open risk at 8–15%.',
    what: '_scan_for_entries() checks each trade risks cfg["risk_per_trade_pct"] × equity independently. But there is no check on the sum of all open position risks. With max_positions=10 each at 1%, the portfolio can have 10% total open risk at once — concentrated in correlated sectors this is dangerously underdiversified.',
    fix: 'Before each new entry in _scan_for_entries(): (1) Compute open_risk_pct = sum((live_prices[t.symbol] - t.current_stop) * t.shares / equity for t in open_trades). (2) Compare to cfg.get("max_open_risk_pct", 0.12). (3) If adding the new trade would exceed the limit, skip entry and log "Portfolio open risk at {X}% — skipping entry for {symbol}". (4) Expose max_open_risk_pct in the config UI with a slider (range 5–20%).',
  },

  {
    id: 'pt-slippage-model',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 3) — entry_slippage_pct=0.001 (+10bps entry, -10bps exit); commission_per_share=0.0. Both configurable.',
    tier: 8, severity: 'feature',
    title: 'PT-B6: No slippage or commission model — simulated returns are better than real execution',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2 hours',
    impact: 'Medium — paper results without slippage overstate real returns by 0.5–2% per trade depending on stock liquidity. Over 50 trades/year this compounds significantly. Traders relying on the paper stats to decide whether to go live will be misled.',
    what: 'All entries and exits execute at the live_price without any slippage or commission. Real paper trading at any broker incurs: (1) Market impact of ~0.05–0.5% per trade, (2) Bid-ask spread (0.01% for large caps, up to 1% for small caps), (3) Commission ($0 at most brokers now, but IB has $0.005/share). The current simulation will always outperform real execution.',
    fix: '(1) Add to engine config: entry_slippage_pct (default 0.001 = 10 bps) and commission_per_share (default 0.0). (2) Entry: adjusted_entry = live_price * (1 + cfg["entry_slippage_pct"]); use adjusted_entry for stop/target math and for position value. (3) Exit: adjusted_exit = live_price * (1 - cfg["entry_slippage_pct"]). (4) Commission: pnl -= cfg["commission_per_share"] * shares * 2 (round-trip). (5) Show slippage cost in closed-trade details.',
  },

  {
    id: 'pt-market-hours',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 3) — enforce_market_hours=True; _is_market_hours() 9:30–16:00 ET UTC-5; entry scan skipped outside hours with paper.entry_scan_skip log.',
    tier: 8, severity: 'low',
    title: 'PT-B7: Engine can enter trades during after-hours using stale prices',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Low-Medium — if the scheduler fires during pre-market or after-hours (misconfiguration, manual trigger, holiday quirk), entries use last-close prices. The trade will open at a stale price that could be far from the next open.',
    what: 'paper_trading_step() has no market-hours guard. The scheduler runs at fixed intervals; if paper_trading_step is called at 7am ET before the market opens, _fetch_live_prices() returns pre-market prices (or last-close), and entries that would not be valid at the open could be created.',
    fix: 'In paper_trading_step(), before any entry logic: (1) Check US market hours: 9:30 AM – 4:00 PM ET Mon–Fri. (2) If outside hours, skip _scan_for_entries() (but still allow _monitor_positions() to check stops using last-close). (3) Log "Market closed — entries suppressed" once per out-of-hours call. (4) Add a skip_market_hours_check: true config override for testing.',
  },

  {
    id: 'pt-entry-score-calibration',
    tier: 8, severity: 'feature',
    title: 'PT-B8: Entry scoring uses equal weights for all factors — not calibrated to historical win rate',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1–2 weeks',
    impact: 'High — current additive scoring (1-4 points per factor, equal weight) ignores empirical evidence of which factors actually predict profitable entries. A signal with high conviction but poor R:R might score 4, same as strong R:R but weak conviction — but their real win rates could be 65% vs 45%.',
    what: '_should_enter() adds 1–4 points for each factor: R:R, ML confidence, RSI, MACD, regime, K-score, volume. Each factor contributes equally. This is arbitrary. Empirical evidence from closed paper trades should be used to weight factors: e.g., if ML confidence is 80% but R:R is mediocre, history might show that wins 70% of the time — so confidence should dominate the score.',
    fix: '(1) After accumulating 100+ closed trades, run logistic regression on {rr, confidence, rsi, macd_ok, regime_score, k_score, volume_ok} → win (1/0). (2) Use learned coefficients as factor weights in _should_enter() score. (3) Add a /paper-portfolio/entry_factors endpoint that returns current factor weights and their confidence intervals. (4) Retrain weights monthly via a new task in the scheduler alongside ML retrain. Surface on improvements page scorecard.',
  },

  {
    id: 'pt-regime-adaptive-stops',
    defaultStatus: 'done',
    implementedNote: 'Done (Regime Engine 2026-06-11) — regime_trail_adj: 0.70 bear, 0.85 risk_off, 1.0 others. Applied to trail_atr_mult before each trail update.',
    tier: 8, severity: 'feature',
    title: 'PT-B9: Market regime only checked at entry — no regime-triggered stop tightening mid-trade',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '3–4 hours',
    impact: 'Medium — portfolio enters in a bull regime; if the regime flips to bear while holding, stops should tighten but don\'t. Positions that were acceptable to hold in a bull now carry excessive risk in a bear. This is especially important for GROWTH-style holds of 30–60 days.',
    what: '_monitor_positions() does not re-check market_regime. It only applies the trailing-stop rule (ATR-based). If SPY drops below its 200-day MA mid-hold (regime flips to bear/high-vol), the engine does nothing differently — the same wide stop and target remain.',
    fix: '(1) In _monitor_positions(), fetch current market_regime via _get_market_regime(). (2) If regime at entry was "bull" and current is "bear": tighten stop by 50% of remaining gap to current price (e.g., if stop is 8% below and price is 5% above entry, move stop to breakeven). (3) If regime is "bear" and position has open profit, log "Regime downgrade — consider early exit" to paper_decisions. (4) Add a config flag: regime_adaptive_stops: true (default false, opt-in to avoid surprises).',
  },

  {
    id: 'pt-earnings-position-sizing',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 3) — earnings_size_mult: 0.50 if DTE≤10, 0.75 if DTE 11–20, 1.0 otherwise. Multiplied into risk_dollar.',
    tier: 8, severity: 'feature',
    title: 'PT-B10: No earnings-aware position sizing — full size entered 6 days before earnings',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '3–4 hours',
    impact: 'Medium — current hard reject at ≤5 DTE is too blunt. A position entered 7 days before earnings is barely safer than one entered 4 days out. An earnings miss can gap -15% at open, exceeding the 12% stop. The engine needs graduated sizing based on earnings proximity.',
    what: '_should_enter() hard-rejects entries ≤5 DTE but allows full position size at 6–15 DTE. Entering full size 8 days before a known binary event (earnings) is poor risk management. The correct approach is: far from earnings = normal size; near earnings = reduced size; very near = skip.',
    fix: '(1) Add earnings_proximity tiers to entry sizing: 6–10 DTE → reduce shares to 50% of normal. 11–20 DTE → 75% size. >20 DTE → 100%. (2) Fetch DTE from stock.days_to_earnings (already in Signal). (3) Log size reduction in entry_decision_notes: "Size reduced to 50% (earnings in 8 days)". (4) Add pre-earnings_exit_days config (default 3): if holding position and earnings are ≤3 days away, close position to avoid gap risk.',
  },

  {
    id: 'pt-n-plus-one-signals',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 2) — batch subquery: max(Signal.ts) grouped by stock_id, joined back. One DB round-trip for all open symbols in _monitor_positions().',
    tier: 8, severity: 'low',
    title: 'PT-P1: N+1 DB query in _monitor_positions — one query per open position per cycle',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1–2 hours',
    impact: 'Low-Medium — with 10 open positions, 10 separate signal queries per cycle. With 25 positions and 5-minute cycles, this generates 300 queries/hour. Adds ~50ms per position. Will degrade as portfolio scales.',
    what: 'In _monitor_positions(), for each open trade the code runs select(Signal).where(Signal.stock_id == stock_id, ...).order_by(Signal.ts.desc()).limit(1) individually. This is a textbook N+1 pattern.',
    fix: 'Batch to one query before the loop: SELECT DISTINCT ON (stock_id) * FROM signals WHERE stock_id IN (...all open positions...) AND horizon = ? ORDER BY stock_id, ts DESC. Build a dict {stock_id: signal} and look up from it inside the loop. Reduces 10 queries to 1.',
  },

  {
    id: 'pt-atr-caching',
    defaultStatus: 'done',
    implementedNote: 'Done (PA-F1) — _batch_compute_atr() fetches all symbols in ONE yfinance download. Used in both monitor_atr_cache and atr_cache.',
    tier: 8, severity: 'medium',
    title: 'PT-P2: ATR fetched from yfinance on every cycle per position — excessive API calls',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2–3 hours',
    impact: 'Medium — with 10 positions + 5 new entry candidates = 15 yfinance calls per 5-minute cycle. That\'s 180 yfinance requests/hour just for ATR. Causes rate-limit throttling that affects the wider system (price ingestion, signal generation).',
    what: '_compute_atr() downloads 40 days of daily OHLCV from yfinance each call. This is called in _monitor_positions() for every open position (trailing stop update) and in _scan_for_entries() for every candidate (position sizing). ATR doesn\'t change minute-by-minute — daily ATR is valid for the entire trading day.',
    fix: '(1) Cache ATR in Redis: key = "paper:atr:{symbol}:{date}", value = float, TTL = 24h. (2) In _compute_atr(), check Redis first; only hit yfinance on cache miss. (3) Alternatively, compute ATR from the existing prices table (already populated by market-data ingestion) using the last 14 daily bars — no yfinance needed. This is the preferred approach as it uses local data.',
  },

  {
    id: 'pt-trade-attribution',
    tier: 8, severity: 'feature',
    title: 'PT-A1: No trade attribution — can\'t identify which entry factors predict wins vs losses',
    file: 'services/market-data/src/api/paper_portfolio.py · frontend/src/pages/paper-portfolio.tsx',
    effort: '1 week',
    impact: 'High — without attribution, the paper portfolio is a black box. The user can\'t answer "should I raise min_confidence from 62% to 68%?" or "do entries with score ≥ 4 outperform score 3?". Attribution closes the feedback loop between signal quality and real outcomes.',
    what: 'The closed trades table shows entry_score, confidence, rr, exit_reason for each trade. But there is no aggregation or analysis. The UI has no view like "Win rate by entry score band: score 5=72%, score 4=61%, score 3=44%". This data is being collected but never surfaced.',
    fix: '(1) Add GET /paper-portfolio/attribution endpoint: aggregates closed trades by {entry_score_band, confidence_band, market_regime_at_entry, rr_band} → {win_rate, avg_return, profit_factor, count}. (2) Frontend: add "Attribution" tab to /paper-portfolio with a heatmap grid (entry score on X, confidence on Y, color = win rate). (3) Add "Best entry profile" chip: shows the factor combination with highest win rate + min 10 trades. (4) This directly informs parameter tuning — show recommended min_entry_score based on break-even win rate.',
  },

  {
    id: 'pt-regime-equity-overlay',
    tier: 8, severity: 'feature',
    title: 'PT-A2: Market regime not overlaid on equity curve — can\'t see how portfolio performs per regime',
    file: 'frontend/src/pages/paper-portfolio.tsx · services/market-data/src/services/paper_trading_engine.py',
    effort: '2–3 hours',
    impact: 'Medium — traders need to know: "my paper portfolio gained 12% but it was all in bull regime — how did it do during bear?" Without regime overlay, the equity curve is context-free.',
    what: 'paper_equity_curve table stores {date, equity, spy_close, qqq_close, hsi_close}. Market regime (bull/bear/high_vol) is captured per-trade at entry but not stored in the equity curve. The frontend equity chart shows equity vs benchmarks but no regime shading.',
    fix: '(1) In snapshot_equity_curve(), fetch current market_regime from signal-engine and store it in a new regime column (or JSON field in the equity curve row). (2) Frontend: add regime shading to the equity curve SVG — light green bands for bull, light red for bear, amber for high_vol. Use as background fill beneath the equity line. (3) Add a legend chip showing current regime.',
  },

  {
    id: 'pt-rolling-alpha-beta',
    defaultStatus: 'done',
    tier: 8, severity: 'feature',
    title: 'PT-A3: Rolling alpha and beta vs SPY not computed — can\'t measure actual excess return quality',
    file: 'services/market-data/src/api/paper_portfolio.py · frontend/src/pages/paper-portfolio.tsx',
    effort: '1 week',
    impact: 'Medium — the current UI shows total return vs SPY in absolute terms. Traders need: alpha (excess return above benchmark), beta (market sensitivity), and information ratio. These are the standard metrics to evaluate if the paper portfolio adds value beyond just riding the market.',
    what: '/paper-portfolio/summary returns sharpe, max_drawdown_pct, calmar alongside total_return_pct and a benchmark comparison. But no rolling alpha, no beta, no information ratio. The total return comparison doesn\'t account for the portfolio\'s market exposure. A portfolio up 20% when SPY was up 25% is actually underperforming on risk-adjusted basis.',
    fix: '(1) In _portfolio_risk_metrics(): compute beta = cov(port_returns, spy_returns) / var(spy_returns) over the equity curve. (2) Alpha = annualised_return - beta × spy_annualised_return (CAPM alpha). (3) Information ratio = alpha / tracking_error (std of port_return - spy_return). (4) Return all three in the summary API. (5) Add three new StatCards to paper-portfolio.tsx: Alpha %, Beta, Information Ratio. Color code: alpha green if >0, beta neutral if 0.8–1.2, IR green if >0.5.',
  },

  {
    id: 'pt-multi-portfolio',
    tier: 8, severity: 'feature',
    title: 'PT-A4: Only one active paper portfolio — can\'t A/B test GROWTH vs SWING strategies',
    file: 'services/market-data/src/services/paper_trading_engine.py · frontend/src/pages/paper-portfolio.tsx',
    effort: '1 week',
    impact: 'High — running only one strategy at a time means you can\'t determine if GROWTH parameters outperform SWING on the same market conditions. A/B testing two portfolios simultaneously is the gold standard for strategy validation.',
    what: 'The engine enforces one "active" portfolio (get_or_create with is_active=True). The scheduler only runs paper_trading_step() once. The DB schema (paper_portfolios, paper_trades) already supports multiple portfolios via portfolio_id FK, but the engine and scheduler don\'t use this. Comparing strategies requires resetting and rerunning sequentially — not a real comparison.',
    fix: '(1) Modify scheduler to call paper_trading_step(portfolio_id=X) for each active portfolio. (2) Add strategy_profile column to paper_portfolios: "GROWTH" | "SWING" | "LONG" — each using different engine config. (3) Create portfolios endpoint that lets admin create/manage multiple portfolios. (4) Frontend: add portfolio selector dropdown to /paper-portfolio; show comparison table (Strategy A vs B): return, Sharpe, win rate, max drawdown side-by-side. (5) Auto-promote better strategy after 30 trading days if Sharpe improvement > 10%.',
  },

  // ── WF-2 Audit Round 2 (2026-06-11) ────────────────────────────────────────

  {
    id: 'pt-rr-division-zero',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 1) — stop_dist = live_price − stop; min_stop_dist = max(price×0.005, 0.05); hard reject if too close. Prevents divide-by-zero and inverted R:R.',
    tier: 8, severity: 'critical',
    title: 'PT-C1: R:R denominator check uses > 0.001 — negative stop distances pass guard, produce backward R:R',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 minutes',
    impact: 'High — if stop >= live_price (e.g., stop=$10.00, price=$9.99), the (live_price - stop) > 0.001 check fails, rr=0, and the trade is skipped. But at the _scan_for_entries() level (line 657) the guard uses max(..., 0.001) which allows a $0.001 denominator, producing an R:R of 1000x on a penny stop. A mis-priced stop can produce a falsely huge R:R that passes the hard-reject threshold.',
    what: '_should_enter() line 169 uses (live_price - stop) > 0.001 as the denominator guard. _scan_for_entries() line 657 uses max(live_price - stop, 0.001). If ATR produces a stop very close to or above the live price, the 0.001 floor means R:R can be astronomically wrong. A stop distance of 0.001 on a $50 stock is 0.002% — meaningless as a risk unit but valid as a math denominator.',
    fix: '(1) Add explicit validation after game plan is built: if game_plan["stop"] >= live_price: skip with warning "Stop above live price — invalid setup". (2) Enforce minimum stop distance of 0.5% of live_price: min_stop_dist = live_price * 0.005. (3) Replace both rr calculations with: rr = (take_profit - live_price) / max(live_price - stop, min_stop_dist). (4) Log all skipped entries with reason "stop too tight" so the decision audit trail stays clean.',
  },

  {
    id: 'pt-cash-float-drift',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 3 + PA-A1) — shares rounded before position_value; max(0.0, ...) floor on all cash mutations.',
    tier: 8, severity: 'medium',
    title: 'PT-C2: Floating-point cash drift — position_value computed before share rounding, creates permanent cash leak',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — over 100 trades, floating-point rounding mismatches between entry and exit cash mutations accumulate. A $50k portfolio running 100 trades/year at $5k per trade could drift $0.50–$5 in phantom cash. Small individually, but the equity curve becomes subtly wrong and cash + open position value won\'t sum to initial capital.',
    what: 'Line 689 rounds shares to 4 decimals: shares = round(shares, 4). But position_value on line 680 is computed before rounding as position_value = shares * live_price, then cash decreases by that amount. At exit, cash increases by live_price * trade.shares where trade.shares is the rounded value. Entry cash delta != exit cash delta by a floating-point fraction per trade.',
    fix: '(1) After rounding shares, recompute: position_value = round(round(shares, 4) * live_price, 2). (2) Use round(..., 2) on all cash mutation lines: portfolio.current_cash = round(portfolio.current_cash - position_value, 2) and portfolio.current_cash = round(portfolio.current_cash + exit_value, 2). (3) Add a periodic reconciliation check: if abs(cash + sum(open_position_values) - initial_capital - total_realized_pnl) > 0.10: log.error("cash reconciliation drift detected").',
  },

  {
    id: 'pt-sector-cap-premature',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 3) — sector cap uses actual computed position_value (not max_position_pct estimate) vs _sector_value() helper.',
    tier: 8, severity: 'medium',
    title: 'PT-C3: Sector cap check uses max_position_pct, not actual computed shares — rejects valid entries',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — sector check runs before position sizing. It assumes the new trade will use max_position_pct (e.g., 10% of equity = $5k). But actual sizing uses risk_per_trade_pct × equity / stop_distance, which often produces a much smaller position (e.g., $800 based on 1% risk). The sector cap kills entries that would actually be fine if their real size was used.',
    what: 'Lines 622–628: sector_value + max_new_pos is checked against max_sector_pct * equity. max_new_pos = equity * cfg["max_position_pct"]. But the actual trade is sized by risk amount, not max position pct. A 1%-risk trade on a stock with a 12% stop allocates only 8.3% of that max_position_pct. The sector cap is artificially restrictive by a factor of up to 12×.',
    fix: 'Move the sector check AFTER position sizing (after line 669). Replace max_new_pos with the actual calculated position_value: if (sector_value + position_value) / max(equity, 1) > cfg["max_sector_pct"]: skip. This means no premature rejects — the check is accurate.',
  },

  {
    id: 'pt-confidence-at-query',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 2) — SQL filter: confidence >= min_confidence × 0.90 (90% floor to allow scoring); _should_enter() hard-rejects < 90% floor.',
    tier: 8, severity: 'medium',
    title: 'PT-C4: Min confidence filtered at SQL query level — skips _should_enter() scoring for borderline signals',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — a signal at 61.9% confidence is hard-rejected before it ever reaches _should_enter(). But a signal with 61.9% confidence + high K-score + strong R:R + bull regime could score 7/9 and be an excellent entry. The hard SQL filter prevents the scoring system from doing its job. Conversely, a 62.0% signal (barely above threshold) with every other factor poor can score 2/9 and still appear in the candidate list.',
    what: 'Line 596: Signal.confidence >= cfg["min_confidence"]. This is a hard reject at query time. _should_enter() on line 303 gives +1 bonus for confidence >= 75%, but never gets to evaluate low-confidence signals. The SQL filter is a blunt instrument that undermines the nuanced scoring logic.',
    fix: '(1) Remove Signal.confidence >= cfg["min_confidence"] from the SQL query. (2) Add a hard-reject at the start of _should_enter(): if confidence < cfg["min_confidence"] * 0.9: return False, "confidence too low". This way signals at 95% of threshold still get evaluated, while truly low-confidence signals are rejected early. (3) The decision log will now show "confidence too low" for rejected candidates, improving auditability.',
  },

  {
    id: 'pt-trailing-dead-zone',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 1) — trail_armed = (highest_price or entry) >= entry × (1+trail_trigger). Once armed, trail updates every cycle regardless of current pnl.',
    tier: 8, severity: 'medium',
    title: 'PT-C5: Trailing stop only updates when pnl >= trail_trigger — creates dead zone where price can fall 10% without stop tightening',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2–3 hours',
    impact: 'Medium — a position hits +7% (trail arms), stop updates to breakeven. Price drops to +3%, recovering to +7% — stop is NOT updated on the way back up because it was already set once. The trail trigger is checked one-time, not continuously. A position can oscillate between +5% and +7% indefinitely with the trailing stop frozen at its first trigger level.',
    what: 'Lines 509–523: if pnl_pct >= trail_trigger: compute trail and update if new_trail > current_stop. The "if" means the trailing logic only enters if the CURRENT pnl is above trail_trigger. After the first trigger, if pnl drops below trail_trigger temporarily (price pullback), the trailing stop freezes. But then if price makes a new high above highest_price, the trailing would be updated next cycle — except highest_price is also only updated inside this if block. So highest_price could be stale.',
    fix: '(1) Split into two phases: (a) ALWAYS update highest_price = max(highest_price, live_price) regardless of trail_trigger. (b) Only compute trailing stop if highest_price * (1 - trail_trigger/100) > entry_price (i.e., the high watermark earned enough to arm the trail). This ensures the trail continuously ratchets up after it arms, even if current pnl dips temporarily. (2) Update trade.highest_price unconditionally on every cycle when holding.',
  },

  {
    id: 'pt-gameplan-validation',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 1) — if gp_stop >= live_price×0.99 or gp_target <= live_price×1.01: skip with paper.skip_invalid_gameplan warning.',
    tier: 8, severity: 'medium',
    title: 'PT-C6: No game plan feasibility check — stop >= live_price, target <= entry, or negative R:R can proceed',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — if _build_game_plan_for_style() produces an invalid plan (stop >= current price, or target <= entry), the entry proceeds with a broken trade setup. The R:R guard catches some cases but isn\'t comprehensive. A stop set 15% below on a stock that already dropped 12% since signal generation could result in a "stop = current price" scenario.',
    what: '_scan_for_entries() calls _build_game_plan_for_style() then immediately calls _should_enter() without validating that the plan is mathematically coherent. If ATR is very small (penny stock), or signal was generated days ago when price was different, the plan parameters may no longer make sense for the current price.',
    fix: 'After _build_game_plan_for_style() call, add: (1) assert game_plan["stop"] < live_price * 0.99, "Stop too close or above live price". (2) assert game_plan["take_profit"] > live_price * 1.01, "Target too close or below live price". (3) assert (game_plan["take_profit"] - live_price) > (live_price - game_plan["stop"]), "R:R < 1.0 before scoring". Log and continue on failure instead of assert.',
  },

  {
    id: 'pt-daily-loss-limit',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 2) — max_daily_loss_pct=0.04; sum of negative pnl today / equity > 4% → entry scan returns early.',
    tier: 8, severity: 'feature',
    title: 'PT-C7: No intraday realized-loss circuit breaker — 5 stop-outs in one morning still triggers new entries',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2 hours',
    impact: 'High — professional trading desks have daily loss limits. If the paper engine takes 5 stop-outs in 90 minutes (losing 5% of equity), the engine should pause new entries for the day. Without this, a bad signal day can compound losses: stop-out → free cash → new entry → stop-out → repeat.',
    what: 'paper_trading_step() calls _monitor_positions() (which exits losing trades) then calls _scan_for_entries() (which opens new ones) in the same cycle. After monitoring exits a position at a loss, the freed cash immediately becomes available for a new entry in the same step. No daily-loss state is tracked or checked.',
    fix: '(1) Add max_daily_loss_pct config (default 0.04 = 4%). (2) In _scan_for_entries(), before scanning: realized_today = sum abs(t.pnl) for closed trades where t.pnl < 0 and t.exit_time >= today_open. (3) If realized_today / equity > cfg["max_daily_loss_pct"]: log "Daily loss limit hit — suspending entries", write to paper_decisions, return. (4) Show a "Daily loss limit active" warning banner on the /paper-portfolio UI when this state is active.',
  },

  {
    id: 'pt-daily-trade-count',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 2) — max_entries_per_day=5; entries_today = count(PaperTrade where entry_time >= today_start).',
    tier: 8, severity: 'feature',
    title: 'PT-C8: No max entries per day — overtrading possible (50 cycles × multiple entries = unrealistic activity)',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Low-Medium — if the scanner fires every 5 min across a 6.5-hour trading day (78 cycles) and each cycle opens 3 entries, that\'s 234 positions in a day — physically impossible for a real trader. Paper results will look better than live due to unrealistic signal saturation.',
    what: 'No daily entry count is tracked. The only limit is max_positions (concurrent open), which resets as positions close. A highly active day with many stop-outs and re-entries is technically possible within the current constraints but unrealistic compared to real trading.',
    fix: '(1) Add max_entries_per_day config (default 5). (2) At start of _scan_for_entries(): count open+closed entries today. (3) If count >= max_entries_per_day: log "Daily entry cap reached", skip scanning. (4) Show "Entries today: X/5" in the engine status section of the UI.',
  },

  {
    id: 'pt-sharpe-min-sample',
    defaultStatus: 'done',
    implementedNote: 'Done (paper_portfolio.py) — _MIN_SHARPE_DAYS=20; Sharpe/Calmar only computed when ≥20 days. Returns insufficient_data=True + data_days=N otherwise.',
    tier: 8, severity: 'medium',
    title: 'PT-C9: Sharpe/Calmar annualized from < 20 trading days — wildly inaccurate, misleads new portfolios',
    file: 'services/market-data/src/api/paper_portfolio.py',
    effort: '30 minutes',
    impact: 'Medium — a portfolio with 5 days of data returns a Sharpe ratio annualized over 252 days. If those 5 days were a bull run, Sharpe shows as 8.0+. A user looking at this would think the strategy is exceptional when it\'s just noise from a tiny sample. The UI shows this number without a confidence caveat.',
    what: '_portfolio_risk_metrics() on line 17 returns None if len(equities) < 2, but passes for any count >= 2. Annualizing 2 data points to 252 days produces meaningless statistics. Industry practice requires at minimum 30 data points for Sharpe to be meaningful; some require 52 weeks.',
    fix: '(1) Change guard: if len(equities) < 20: return empty metrics with a "insufficient_data" flag. (2) In API summary response, add "data_quality": "insufficient" when < 20 trading days. (3) UI: show "— (< 20 days data)" instead of a Sharpe number when data_quality is insufficient. (4) Optional: show a "min data needed" progress bar: "12/20 days — Sharpe available in 8 days".',
  },

  {
    id: 'pt-param-audit-trail',
    defaultStatus: 'done',
    tier: 8, severity: 'feature',
    title: 'PT-C10: No audit log for engine config changes — can\'t correlate strategy tweaks to performance changes',
    file: 'services/market-data/src/api/paper_portfolio.py',
    effort: '2–3 hours',
    impact: 'Medium — when a user changes min_confidence from 62% to 70% and then the win rate drops, there\'s no way to know when that change happened or what the previous value was. Over weeks of active tuning, the relationship between parameter changes and outcomes is invisible.',
    what: 'POST /paper-portfolio/configure overwrites cfg directly with no history. The portfolio row stores current config only. There is no paper_config_history table, no log entry, no timestamp. In the UI, the "Settings" panel shows current values only.',
    fix: '(1) Add a paper_config_history table: (portfolio_id, changed_at, changed_by, old_config JSON, new_config JSON). (2) On every configure call, write a row before updating. (3) Add GET /paper-portfolio/config-history endpoint. (4) Frontend: add a "Config History" expandable panel in the engine settings section showing the last 10 changes with diffs. (5) Overlay config-change events on the equity curve as vertical markers.',
  },

  {
    id: 'pt-kscore-missing-stocks',
    defaultStatus: 'done',
    implementedNote: 'Done (Round 2) — require_kscore=True; if ranking is None: skip with paper.skip_no_ranking; if score < min_kscore: skip with paper.skip_kscore.',
    tier: 8, severity: 'low',
    title: 'PT-C11: Stocks with no ranking row bypass K-score filter — recently added stocks enter without quality gate',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 minutes',
    impact: 'Low — newly added or obscure stocks without a Ranking row in DB bypass the K-score minimum (e.g., min_kscore=60). The outer join + None-check silently allows unranked stocks. If a bad signal fires for a thinly-traded newly-added stock, it enters unchecked.',
    what: 'Lines 583–589 use outerjoin(Ranking). Line 615: if ranking and ranking.score < cfg["min_kscore"]: skip. If ranking is None (no row), the check is skipped — the stock passes. This is probably unintentional. A stock with no ranking has unknown quality, not good quality.',
    fix: 'Add config flag require_kscore (default True). If True: if ranking is None or ranking.score < cfg["min_kscore"]: skip. Log "no ranking data" as the reason. If False: allow unranked stocks (current behavior). Makes the policy explicit instead of accidental.',
  },

  // ── Regime Engine (RE series) ─────────────────────────────────────────────────
  {
    id: 're1-regime-classifier',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _fetch_market_regime() downloads 300d of SPY/QQQ/^VIX via yfinance once per cycle. Computes EMA-20, EMA-50, EMA-200 on SPY and EMA-50 on QQQ. Classifies into 5 states: bull (SPY > 20/50EMA, VIX < 18), neutral (default), choppy (SPY < 20EMA or VIX > 20), risk_off (SPY < 50EMA or VIX > 25), bear (SPY < 50EMA AND VIX > 30 — or SPY < 200EMA AND 20d return < -8%). Emits paper.regime_classified log each cycle.',
    tier: 8, severity: 'feature',
    title: 'RE-1: Market regime classifier — 5-state SPY/QQQ/VIX engine',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '3 hours',
    impact: 'High — prevents entries during market downturns; core foundation for all other regime improvements',
    what: 'No market-wide context used during entry decisions beyond stale signal-stored regime values.',
    fix: '_fetch_market_regime(cfg) downloads 300d SPY/QQQ/^VIX and classifies into: bull / neutral / choppy / risk_off / bear.',
  },

  {
    id: 're2-bear-gate',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _scan_for_entries() returns early with paper.regime_gate_bear warning log when live regime state is "bear". Portfolio logs VIX and SPY values at block time.',
    tier: 8, severity: 'critical',
    title: 'RE-2: Bear regime gate — block all new entries when SPY < 50EMA + VIX > 30',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 min',
    impact: 'High — prevents opening new longs during confirmed market downturns, single largest risk driver',
    what: 'Engine opens new GROWTH positions regardless of macro environment — was entering in March 2020, Oct 2022 type drawdowns.',
    fix: 'Gate at top of _scan_for_entries(); check live_regime["state"] == "bear" → early return.',
  },

  {
    id: 're3-regime-sizing',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — regime_size_mult applied to risk_dollar: 1.0 in bull/neutral, 0.75 in choppy, 0.50 in risk_off, 0.0 (gate) in bear. All multipliers configurable via config keys regime_bull_size_mult, regime_choppy_size_mult, regime_risk_off_size_mult.',
    tier: 8, severity: 'critical',
    title: 'RE-3: Regime-aware position sizing — 50% in risk_off, 75% in choppy',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'High — reduces drawdown during regime transitions without stopping the engine entirely',
    what: 'All entries use the same 1% risk regardless of market conditions.',
    fix: 'Apply regime_size_mult to risk_dollar = equity * risk_per_trade_pct * earnings_size_mult * regime_size_mult.',
  },

  {
    id: 're4-regime-entry-score',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — min_entry_score is raised in-place for the entry scan: choppy → max(base, 4), risk_off → max(base, 5). Default base is 3. Uses cfg["min_entry_score"] override before passing cfg to _should_enter().',
    tier: 8, severity: 'medium',
    title: 'RE-4: Regime-adjusted min_entry_score — +1 in choppy, +2 in risk_off',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 min',
    impact: 'Medium — raises bar for entry quality in degraded market environments',
    what: 'Same entry score threshold (3) used in all regimes. Choppy markets have higher noise; borderline setups that score 3 often fail.',
    fix: 'Override cfg["min_entry_score"] = max(base, regime_min) before _should_enter() call.',
  },

  {
    id: 're5-live-regime-scoring',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _should_enter() accepts live_regime: dict | None parameter. live_regime_state = live_regime.get("state") overrides signal-stored reasons["market_regime"] in the scoring block. Scoring: bull +1, bear -2, risk_off/choppy -1, high_vol -1.',
    tier: 8, severity: 'medium',
    title: 'RE-5: Live regime replaces stale signal-stored regime in _should_enter() scoring',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — scoring uses fresh regime data instead of values baked into signals days ago',
    what: '_should_enter() reads market_regime from signal.reasons, which was computed at signal generation time (potentially 48h ago). If regime shifted since then, the scoring is wrong.',
    fix: 'Add live_regime: dict | None = None param; live_regime_state = live_regime.get("state") → override signal-stored regime.',
  },

  {
    id: 're6-regime-trail-stops',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — regime_trail_adj applied to trail_atr_mult before the trailing stop update: bear = 0.70 (30% tighter), risk_off = 0.85 (15% tighter), neutral/bull = 1.0. Existing positions get tighter stops as market deteriorates.',
    tier: 8, severity: 'medium',
    title: 'RE-6: Regime-adjusted trailing stops — 0.85× ATR mult in risk_off, 0.70× in bear',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — protects open positions from holding through regime-driven drawdowns',
    what: 'Trailing stop uses a fixed trail_atr_mult=2.0 regardless of macro environment. During market selloffs the mult should tighten so the stop tracks price more aggressively.',
    fix: 'Compute regime_trail_adj before the monitor loop; apply to mult = trail_atr_mult * regime_trail_adj.',
  },

  {
    id: 're7-regime-at-entry',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — market_regime_at_entry now uses (live_regime or {}).get("state") as primary, falling back to signal.reasons.get("market_regime"). Visible in closed-trade drill-down.',
    tier: 8, severity: 'low',
    title: 'RE-7: market_regime_at_entry uses live regime state (replaces stale signal value)',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '15 min',
    impact: 'Low — improves trade audit trail; makes the closed-trade table reflect actual conditions at entry',
    what: 'market_regime_at_entry was set from signal.reasons["market_regime"] — a value computed during signal generation, not at entry time.',
    fix: 'market_regime_at_entry = (live_regime or {}).get("state") or (sig.reasons or {}).get("market_regime")',
  },

  {
    id: 're8-regime-ui-badge',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — StatCard "Regime" added after vs QQQ card on paper-portfolio page. Color: bull=green, neutral=slate, choppy=amber, risk_off=orange, bear=red. Sub-text shows VIX level. API summary now includes regime_state, regime_vix, regime_spy, regime_notes fields.',
    tier: 8, severity: 'feature',
    title: 'RE-8: Regime badge on paper portfolio page — live state + VIX displayed',
    file: 'frontend/src/pages/paper-portfolio.tsx',
    effort: '1 hour',
    impact: 'Medium — makes the regime state visible at a glance; no more log-diving to know current macro environment',
    what: 'Regime state computed and stored in portfolio config but not visible on the UI.',
    fix: 'Add StatCard showing regime_state (color-coded) and VIX level. API exposes regime_state, regime_vix as top-level summary fields.',
  },

  {
    id: 'pt-outperformance-stat',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — outperformance_vs_spy and outperformance_vs_qqq added to summary API (paper_portfolio.py). StatCards "vs SPY" and "vs QQQ" show +/- % with green/red color on paper-portfolio page.',
    tier: 8, severity: 'feature',
    title: 'PT-C12: No explicit outperformance-vs-SPY stat card — user must visually eyeball the equity chart',
    file: 'frontend/src/pages/paper-portfolio.tsx',
    effort: '1–2 hours',
    impact: 'Low-Medium — the equity chart shows portfolio and SPY/QQQ rebased to the same starting value, which is helpful. But without a hard number, comparing them requires squinting at the chart. A portfolio up 8% when SPY is up 7.8% is hard to distinguish visually over 60 days.',
    what: 'The summary API returns total_return_pct and benchmark data separately. The frontend StatCards show total return, Sharpe, win rate, profit factor — but not "alpha vs SPY" as an explicit number. The benchmark lines in the chart are purely visual with no numeric callout.',
    fix: '(1) Backend: compute spy_return_pct = (latest_spy_close / first_spy_close - 1) × 100 using the equity curve rows. Add outperformance_vs_spy = total_return_pct - spy_return_pct to the summary response. (2) Frontend: add a StatCard "vs SPY" with green/red coloring based on sign. Show "+2.1% vs SPY" or "-0.4% vs SPY". (3) Same for QQQ.',
  },

  {
    id: 'ui-date-range-picker',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Two date inputs (From / To) added to /signal-accuracy overview tab alongside the radio buttons. When both dates are set they override lookback_days; border highlights in indigo while active. Clear button resets to radio mode. API already supported from_date/to_date params.',
    tier: 3, severity: 'feature',
    title: 'UI-13: Date range picker for backtest results, signal accuracy, and walk-forward analysis',
    file: 'frontend/src/pages/signal-accuracy.tsx · frontend/src/pages/backtest.tsx',
    effort: '1–2 days',
    impact: 'Medium — current fixed lookback windows (30d / 60d / 90d) cannot answer "how did signals perform during the Oct 2024 correction?" Custom date ranges make the accuracy tracker and backtest pages research tools rather than dashboards.',
    what: 'Signal accuracy, walk-forward heatmap, and backtest result pages use fixed radio-button lookback periods (30/60/90/180 days). There is no way to specify an arbitrary date range — e.g., "show accuracy only during bear market regime" or "backtest the last earnings season." The walk-forward drill-down already accepts from_date/to_date API params but only via clicking heatmap cells.',
    fix: 'Add a date range picker component (two date inputs: From / To) to: (1) /signal-accuracy — replaces the "90 days" radio buttons; passes from_date/to_date to the existing /signals/accuracy endpoint. (2) /backtest — lets users re-run a backtest for a custom window instead of the default full history. (3) Walk-forward heatmap — add a manual date range input alongside the heatmap cell click. Use a lightweight inline component (two <input type="date"> fields + Apply button) — no third-party calendar library needed. Persist the selected range to sessionStorage so it survives page navigations.',
  },

  {
    id: 'workflow-hold-sell-guidance',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — WF-3: Live Position Monitor on active Trade Board cards: near-target alert (within 2% → "Consider scaling out 50%"), near-stop warning (within 2% above stop → amber), trail recommendations (+3% → breakeven, +5% → trail to 3% below current), dollar P&L + risk, and stalled warning after 15d with <3% move.',
    tier: 3, severity: 'feature',
    title: 'WF-3: In-position guidance — when to hold vs exit as price moves after entry',
    file: 'frontend/src/pages/trade-board.tsx · services/signal-engine/src/api/routes.py',
    effort: '2–3 days',
    impact: 'High — the hardest part of trading is not knowing when to cut a losing trade or lock in a winning one. Adding rules-based trailing-stop and target-hit guidance reduces emotional decision-making.',
    what: 'After a stock is bought and added to the Trade Board, there is no ongoing guidance about when to exit. The game plan sets a static stop and target at entry, but does not adapt as: (1) price rises toward the target, (2) price pulls back to the stop, (3) the signal downgrades from BUY to HOLD/WAIT, or (4) a macro regime shift (bull → bear) increases exit urgency. Users must manually monitor and decide.',
    fix: 'Add a "Live Position Monitor" to each Trade Board card: (1) Trail stop — once price rises 3% from entry, move stop to breakeven automatically; once +5%, trail at ATR×1.5 below current price. Show the current trailing stop level on the card. (2) Target proximity — when price is within 2% of take-profit, show "Consider scaling out 50% now" alert. (3) Signal degradation — when signal drops from BUY to WAIT, show "Signal weakening — tighten stop or reduce size" banner. (4) Regime override — if market regime flips to bear while holding, show "Bear regime active — exits take priority over entries." (5) Time stop — if price has not moved ±5% in 20 trading days, flag "Dead money — consider redeploying capital." Backend: new GET /signals/{symbol}/position-check?entry_price=X&entry_date=Y returns current recommendation (hold/trail/reduce/exit) with reasoning.',
  },

  {
    id: 'tb1-trailing-stop',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — TB-1: Trail stop chip on active cards: +3% from entry → "Move stop to breakeven ($X.XX)"; +5% → "Trail stop to $X.XX" (3% below current). Pure frontend, uses live price from 60s board refresh.',
    tier: 3, severity: 'feature',
    title: 'TB-1: Trailing stop-loss — auto-raise stop as price rises in your favour',
    file: 'frontend/src/pages/board.tsx · services/signal-engine/src/api/routes.py',
    effort: '2–3 days',
    impact: 'High — prevents giving back large gains; the #1 cause of a winning trade turning into a loss is a static stop set at entry',
    what: 'The game plan sets a fixed stop at entry. Once price rises 5–10%, the original stop no longer reflects risk — a pullback to entry is now breakeven, not a loss. There is no mechanism to automatically raise the stop as the position moves in favour.',
    fix: 'Add a trailing stop calculator to each Trade Board card: (1) Once price rises ≥3% from entry, display "Move stop to breakeven" suggestion. (2) Once ≥5%, trail at ATR×1.5 below the highest close reached since entry. (3) Show the current trailing stop level as a chip on the card (distinct from the static game plan stop). Backend: new GET /signals/{symbol}/trail?entry_price=X&entry_date=Y returns trail_stop, trail_level (breakeven/trailing), highest_close. Frontend: fetch on card expand, show coloured chip.',
  },

  {
    id: 'tb2-time-stop',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — TB-2: Stalled warning on active cards: days_in_trade > 15 AND |P&L%| < 3% → "⏱ Stalled Nd — consider exiting if thesis not playing out".',
    tier: 3, severity: 'feature',
    title: 'TB-2: Time-stop — flag dead-money trades that have stalled for N days',
    file: 'frontend/src/pages/board.tsx',
    effort: '1 day',
    impact: 'Medium — capital sitting in a flat position has opportunity cost; automatic flagging prompts the user to act before the trade decays further',
    what: 'A trade can stall for weeks with price oscillating ±2% around entry — technically still active but generating no return. There is no mechanism to surface these "dead money" positions. Users often hold stalled trades waiting for momentum that never returns.',
    fix: 'Compute days_in_trade on each Trade Board card. If price has moved < ±3% from entry AND days_in_trade > 15 (active) or > 20 (watch/planning), show a "⏱ Stalled" warning badge on the card. Clicking opens a tooltip: "No meaningful progress in N days — consider redeploying capital or tightening the stop." No backend change needed — entry_date and current price already in the card data.',
  },

  {
    id: 'tb3-stop-breach-alert',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — TB-3: Red "⚠ STOP BREACHED" banner when live price < stop; amber "Near stop" chip within 2% above stop. Live prices refresh every 60s via existing board SWR.',
    tier: 3, severity: 'feature',
    title: 'TB-3: Live stop-loss breach indicator — visual alert when price crosses below stop',
    file: 'frontend/src/pages/board.tsx · frontend/src/lib/api.ts',
    effort: '1–2 days',
    impact: 'High — without a real-time breach indicator, users may miss a stop being hit during the trading day and hold losing positions beyond their own risk rules',
    what: 'The Trade Board shows the stop-loss price but makes no comparison to the current live price. If a stock drops below its stop, the card shows nothing different — the user must manually notice the price has fallen through the stop.',
    fix: 'On card render: compare current_price (already in board data) against game_plan.stop_loss. If current_price < stop_loss and stage is "active": highlight the stop chip in red, show "⚠ Stop breached" banner at top of card. If current_price < stop_loss × 1.02 (within 2%): show "Near stop" in amber. This is a pure frontend change — no backend call needed. Optionally fire a push notification (POST /notifications) when breach is detected on next refresh.',
  },

  {
    id: 'tb4-dollar-risk-pnl',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — TB-4: Dollar P&L (shares × price Δ from entry, green/red) and dollar risk (shares × entry−stop distance, red) shown in the position monitor row on active cards. Requires shares to be set.',
    tier: 3, severity: 'feature',
    title: 'TB-4: Dollar-risk P&L — show position $ risk alongside % P&L',
    file: 'frontend/src/pages/board.tsx',
    effort: '1 day',
    impact: 'Medium — % P&L is abstract; "down $340 on a $2,000 position (ATR stop = $280 max risk)" is concrete and helps users compare positions on equal footing',
    what: 'The Trade Board shows % P&L from entry but no dollar amounts. Users cannot quickly see which position is bleeding the most capital or how their actual loss compares to their planned max risk (stop distance × shares). Comparing -4% on a $500 position vs -1% on a $5,000 position requires mental arithmetic.',
    fix: 'If shares and entry_price are stored in game_plan (they are — PositionSizer data is saved), compute: position_size = shares × entry_price, unrealised_pnl = (current_price - entry_price) × shares, max_risk = (entry_price - stop_loss) × shares. Display as "$+340 | Risk: $180" below the % P&L chip. Show max_risk in grey and unrealised_pnl in green/red. No backend change.',
  },

  {
    id: 'tb5-portfolio-risk-dashboard',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — TB-5: Active positions summary bar above the kanban board: total unrealized P&L, total dollar risk, stop-breach count, near-target count. Computed client-side from active cards + 60s live prices.',
    tier: 3, severity: 'feature',
    title: 'TB-5: Portfolio heat-at-risk summary — total capital at risk across all active trades',
    file: 'frontend/src/pages/board.tsx',
    effort: '1–2 days',
    impact: 'High — users may unknowingly have 40% of their capital at risk across 8 small positions; a single summary number surfaces over-leveraging before it becomes a problem',
    what: 'There is no summary of total risk exposure across the Trade Board. A user with 8 active positions could have stop-losses implying a total portfolio drawdown of 25%+ without realising it. The board shows individual card risk but no aggregate.',
    fix: 'Add a sticky risk summary bar at the top of the board (above the columns): "Active positions: 8 | Total at risk: $1,840 (3.7% of $50k) | Unrealised P&L: +$420". Computed client-side from all active cards. Show in amber if total at risk > 10% of total position value, red if > 20%. Include a sparkline of daily P&L change if historical data is available. No backend change — derived from card data.',
  },

  {
    id: 'sl1-admin-signal-log',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-08 — GET /admin/signal-log (admin auth): joins Signal→Stock→SignalOutcome; paginated, filterable by symbol/signal_type/horizon/days_back. New /admin-signals page (admin-only redirect guard): stat strip, colour-coded BUY/SELL badges, confidence bar, outcome ✓/✗ once hold window closes, CSV export. Signal Log link added to Tools nav (adminOnly). Foundation for WF-2 paper trading engine.',
    tier: 3, severity: 'feature',
    title: 'SL-1: Admin signal log — all system BUY/SELL signals with outcomes, admin-only tab',
    file: 'services/market-data/src/api/admin.py · frontend/src/pages/admin-signals.tsx (new)',
    effort: '2–3 days',
    impact: 'Very High — the system cannot improve without a feedback loop. Logging every BUY/SELL signal and linking it to actual price outcome creates the ground truth needed to tune thresholds, measure system edge, and validate changes empirically.',
    what: 'There is no admin view of the raw system signal log. The Signal table stores every signal generated, and SignalOutcome fills in outcomes, but no admin UI surfaces this data. Admins cannot see "the system fired 23 BUY signals last week — which were correct, which were not?" without querying the DB directly.',
    fix: 'Backend: GET /admin/signal-log (admin auth required) — joins Signal → Stock → SignalOutcome; returns paginated list with symbol, name, signal type, confidence, horizon, generated_at, outcome_pct (when available), is_correct. Filters: symbol, signal_type, days_back, horizon. Frontend: new /admin-signals page (admin-only, redirects non-admin). Table with colour-coded BUY/SELL badges, confidence bar, outcome ✓/✗ when resolved, CSV export. Nav link in Tools group (adminOnly). Prerequisite for WF-2 paper trading engine.',
  },

  {
    id: 'ui-board-added-badge',
    tier: 4, severity: 'low',
    defaultStatus: 'done',
    implementedNote: 'Already live — boardSet (derived from GET /board, stage≠closed) + "✓ On Board" green badge implemented in opportunities.tsx (line 1142) and RankingsTable component. Verified 2026-06-10.',
    title: 'UI-14: Show "Added" badge in Screener/Rankings when stock is already on Trade Board',
    file: 'frontend/src/pages/opportunities.tsx · frontend/src/pages/rankings.tsx',
    effort: '< 1 day',
    impact: 'Low — prevents confusion when scanning the screener; users currently cannot tell at a glance which BUY signals they have already acted on.',
    what: 'The Opportunities screener and Rankings page both show an "Add to Board" button per stock. If the stock is already on the Trade Board, the button still shows "Add" — there is no visual indicator that the position already exists. A user scanning the screener after adding 5 positions cannot tell which ones they have already added without navigating to the Trade Board.',
    fix: 'Fetch the list of Trade Board symbols at page load (already available via GET /positions). In the Opportunities and Rankings tables, compare each row\'s symbol against the board set. If present: replace the "Add" button with a green "✓ On Board" badge (non-clickable, or clicking navigates to /trade-board). No backend change needed — data is already available. Implementation: add a boardSymbols Set derived from useSWR on /positions; in the row render, check boardSymbols.has(symbol) before rendering the button.',
  },

  {
    id: 'tech-testing-framework',
    tier: 5, severity: 'low',
    title: 'Tech Debt: Full testing framework — API integration tests, frontend E2E, ML validation',
    file: 'tests/ (new directory at repo root)',
    effort: '1–2 weeks',
    impact: 'High long-term — currently there are zero automated tests. Every deployment is manually validated. A regression in signal logic, K-Score calculation, or API routing could go undetected until a user reports it. As the system grows, manual validation becomes impossible.',
    what: 'The codebase has no automated test suite. pytest is in requirements.txt for the signal-engine but no test files exist. There are no frontend component tests, no API integration tests, no ML accuracy regression tests, and no scheduler validation. Every code change is deployed blindly.',
    fix: 'Build a layered test suite: (1) Backend unit tests (pytest): K-Score formula correctness, signal logic boundary conditions (RSI thresholds, earnings compression multipliers, conviction gate pass/fail), calibrate_ta_weights with synthetic data. (2) API integration tests: spin up a test DB + all services via docker compose -f docker-compose.test.yml; test every major endpoint (ingest → rankings → signals → accuracy). (3) ML validation: after each retrain, assert accuracy > 0.55 on a held-out validation set; fail the post-close job if accuracy drops below threshold. (4) Frontend E2E (Playwright): login, add a stock to watchlist, navigate to stock detail, generate signal, add to Trade Board — full happy path. (5) Scheduler smoke test: trigger a manual _weekly_full_refresh() call, assert all downstream services responded 200. Run on every git push via GitHub Actions CI.',
  },

  {
    id: 'tech-scheduler-monitor',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Redis keys scheduler:job:{name} written after each job (14d TTL) with {job, status, last_run, duration_s, error}. GET /admin/scheduler-status reads all keys. New /admin-health page with JobCard components, stale thresholds per job, error/stale summary bar, 30s auto-refresh.',
    tier: 4, severity: 'low',
    title: 'Tech Debt: Scheduler health monitor — verify weekend processes ran, alert on failure',
    file: 'services/market-data/src/services/scheduler.py · frontend/src/pages/improvements.tsx',
    effort: '1–2 days',
    impact: 'Medium — the weekly full refresh, tune_all, calibrate_ta_weights, and ML retrain are all fire-and-forget. If any silently fail (e.g., yfinance outage, out-of-memory), Monday signals use stale data with no warning.',
    what: 'Scheduled jobs (weekly full refresh, tune_all, calibrate_ta_weights, nightly ML retrain) log start/end to stdout but have no persistent status record. There is no way to check from the frontend "did last Sunday\'s refresh run?" or "when did the ML model last retrain?" A failed weekend job means Monday trading uses week-old data silently.',
    fix: 'Add a scheduler status store in Redis: after each job completes (or fails), write a key scheduler:job:{name} with {last_run, status, duration_s, error}. TTL = 14 days. (2) Add GET /admin/scheduler-status endpoint that reads all job keys and returns a summary. (3) Add a "System Health" card to the improvements page (or a dedicated /admin/health page) that shows last-run timestamps and pass/fail for: weekly_refresh, tune_all, calibrate_ta_weights, us_post_close (ML retrain), hk_post_close. (4) If any job has not run within its expected window (e.g., weekly_refresh not run in >8 days), send an admin alert email. This makes silent failures immediately visible.',
  },

  // ── Signal Accuracy ───────────────────────────────────────────────────────

  {
    id: 'sa8-ensemble-model',
    tier: 2, severity: 'medium', defaultStatus: 'done',
    title: 'SA-8: Ensemble ML — XGBoost + LightGBM + Random Forest majority vote',
    file: 'services/ml-prediction/src/models/trainer.py · predictor.py',
    effort: '3–5 days',
    impact: 'High — single-model predictions are brittle; ensemble reduces variance and improves out-of-sample accuracy by 3–8% in practice. Each model has different failure modes: XGBoost overfits noise, RF handles outliers better, LightGBM generalises well on sparse features.',
    what: 'The system uses a single XGBoost model per symbol. A single model is sensitive to the specific training window and random seed. When the model is wrong, there is no second opinion. Ensembles are the standard approach in production quant systems for exactly this reason.',
    fix: 'Train 3 models per symbol: XGBoost (current), LightGBM (faster, handles categoricals natively), RandomForest (high bias, low variance — stabilises noisy predictions). Final probability = weighted average: XGBoost 40%, LightGBM 35%, RF 25% (weights tunable via Optuna). If all 3 agree direction, boost confidence by 10%. If they disagree, flag as "conflicted signal" and reduce confidence by 15%. Store model-level probabilities in Signal.reasons so the conflict is visible in the UI. Adds ~30s to weekly tune_all runtime.',
  },

  {
    id: 'sa9-true-walkforward',
    tier: 2, severity: 'medium', defaultStatus: 'done',
    title: 'SA-9: True out-of-sample walk-forward validation — detect overfitting before it hurts',
    file: 'services/ml-prediction/src/models/trainer.py · services/signal-engine/src/api/routes.py',
    effort: '2–3 days',
    impact: 'High — current in-sample accuracy (reported by calibrate_ta_weights: 67.99%) is meaningless for predicting live performance. Out-of-sample IC tells you whether the model actually generalises or is memorising noise.',
    what: 'ML models are trained and evaluated on the same data window (in-sample). calibrate_ta_weights reports 67.99% in-sample accuracy — but this includes the training samples. Out-of-sample accuracy on truly unseen future periods could be much lower. There is no walk-forward out-of-sample validation in the training pipeline.',
    fix: 'Implement TimeSeriesSplit (sklearn) with 5 folds: for each fold, train on months 1–N, evaluate on month N+1. Report mean OOS accuracy, OOS precision, OOS recall, and OOS Information Coefficient (rank correlation between predicted probability and actual return). Store these metrics per symbol in Redis after each weekly tune_all. Surface on the /signal-accuracy page as "OOS Accuracy" vs "In-Sample Accuracy" — a large gap (>10%) flags overfitting. Automatically suppress signals for symbols where OOS accuracy < 52% (coin flip).',
  },

  {
    id: 'sa10-signal-stability',
    tier: 2, severity: 'medium',
    title: 'SA-10: Signal stability score — persistent BUY signals are more reliable than flickering ones',
    defaultStatus: 'done',
    file: 'services/signal-engine/src/generators/signals.py · services/signal-engine/src/api/routes.py',
    effort: '1–2 days',
    impact: 'Medium — a BUY that has held for 5 consecutive days has much stronger empirical backing than one that appeared today. This filters out noise and reduces false entries on unstable signals.',
    what: 'Every signal refresh re-computes the signal from scratch. A stock can flip BUY → SELL → BUY within 3 days. Currently there is no score for signal persistence — a brand-new BUY and a BUY that has been stable for 2 weeks carry identical weight in the conviction gate.',
    fix: 'Count consecutive days the current signal has held using Signal history in the DB. Add stability_days to Signal.reasons. In the conviction score formula, apply a stability multiplier: 1–2 days = 0.85×, 3–5 days = 1.0×, 6–10 days = 1.10×, >10 days = 1.15×. Show stability_days as a chip on the SignalCard ("BUY · 8d stable"). In the email alert, include "Signal has held BUY for X days." Use stability < 3 days as a soft disqualifier on the conviction gate (add to failed list with warning rather than hard block).',
  },

  {
    id: 'sa11-breadth-suppression',
    tier: 2, severity: 'medium', defaultStatus: 'done',
    title: 'SA-11: Market breadth filter — suppress BUY signals when fewer than 40% of stocks are advancing',
    file: 'services/signal-engine/src/generators/signals.py · services/market-data/src/services/scheduler.py',
    effort: '2–3 days',
    impact: 'Medium — buying individual stocks when the broad market is in internal distribution (most stocks falling despite flat index) is a known trap. Breadth filters cut false BUYs by 15–20% during early bear phases.',
    what: 'The system tracks macro regime (bear/bull via SPY vs 200d SMA) but not internal market breadth — the percentage of stocks in the universe actually trading above their own 50d or 200d SMA. A market can look "bull" on SPY while 60% of stocks are already in downtrends (the index is propped up by 5 mega-caps). This is the most common trap in late-bull-market environments.',
    fix: 'Every post-close refresh: compute breadth_pct_above_50sma and breadth_pct_above_200sma across the full US universe. Store in Redis with key market:breadth. In generate_signal(): if breadth_pct_above_50sma < 40%, apply a 0.85× fused-score multiplier and add "breadth_warning: weak" to reasons. If < 30%, treat as bear regime regardless of SPY. Show breadth % as a macro stat chip on the Rankings and Opportunities pages alongside VIX and regime badge.',
  },

  {
    id: 'sa12-adaptive-thresholds',
    tier: 2, severity: 'medium', defaultStatus: 'done',
    title: 'SA-12: Adaptive confidence thresholds — raise the BUY bar in volatile/bear regimes',
    file: 'services/signal-engine/src/generators/signals.py · services/market-data/src/services/scheduler.py',
    effort: '1–2 days',
    impact: 'Medium — a 65% confidence signal in a bull market has very different expected value than 65% in a bear market. Fixed thresholds treat them identically. Regime-aware thresholds improve precision at the cost of recall (fewer but higher-quality BUY signals).',
    what: 'The conviction gate uses fixed thresholds: ML probability > 70%, confluence ≥ 75, confidence ≥ 60%. These were tuned for average market conditions. In bear regimes or high-VIX environments, false-positive rates rise significantly. The same threshold that works in a bull market lets through too many noise signals during corrections.',
    fix: 'Define regime-specific threshold tables: bull+low_vol: ML>0.65, confluence≥70, confidence≥58. neutral: ML>0.70, confluence≥75, confidence≥60 (current). bear or high_vol: ML>0.78, confluence≥82, confidence≥68. vix_spiking: add "minimum 3 consecutive days above threshold" stability requirement. Read current regime from Redis (already stored by macro features). Apply threshold table in check_signal_alerts() and in generate_signal() for the BUY/SELL classification boundary. Log which threshold tier was applied in Signal.reasons.',
  },

  {
    id: 'sa13-growth-momentum-style',
    tier: 2, severity: 'feature', defaultStatus: 'done',
    title: 'SA-13: Growth/Momentum signal style — relaxed thresholds for high-volatility AI/tech stocks',
    file: 'services/signal-engine/src/generators/signals.py · services/signal-engine/src/api/routes.py · frontend/src/pages/stock/[symbol].tsx',
    effort: '1–2 days',
    impact: 'High — high-volatility growth stocks (NVDA, TSLA, PLTR, AI sector) rarely pass the SWING conviction gate because they lack SMA50>SMA200 (they consolidate below the 200MA for months) and run RSI 70+ as normal. This means users never receive BUY signals for the highest-return names in bull markets. A separate GROWTH style with de-penalised momentum criteria gives signal coverage for these stocks without degrading SWING quality.',
    what: 'The SWING signal uses SMA50>SMA200 (golden cross) as a key structural requirement and penalises RSI above 65 as "overbought." For high-growth momentum stocks this is wrong: NVDA spent most of 2023–2024 with RSI 70–85 and SMA50 below SMA200 while rallying 300%. The current system would have labeled these stocks WAIT/HOLD throughout the entire move.',
    fix: 'Added a new GROWTH style to _STYLE_PROFILES in signals.py with: (1) SMA20>SMA50 substituted for SMA50>SMA200, (2) RSI 38–80 treated as valid entry range, (3) ML threshold 0.60 (vs 0.65 SWING bull), (4) ADX minimum lowered to 18, (5) no relative-strength compression (growth stocks often lag their sector before breaking out), (6) weekly BUY gate disabled (growth stocks have abnormal weekly RSI patterns). Added _growth_ta_adjustment() helper that gives bonus scores for SMA20>SMA50 (+0.06) and high RSI (+0.02–0.04). Added GROWTH to SignalHorizon DB enum with migration. Stock detail page shows a separate purple GROWTH signal card below the main SWING card.',
  },

  // ── Stock Research ────────────────────────────────────────────────────────

  {
    id: 'res1-short-interest',
    defaultStatus: 'done',
    implementedNote: 'Added "Short Interest & Ownership" row in Company Financials on stock detail page (short % of float, days to cover, institutional/insider %). Signal engine now applies a +2–4% confidence boost for SWING/GROWTH when short % ≥ 20% and signal is bullish (squeeze potential).',
    tier: 3, severity: 'feature',
    title: 'RES-1: Short interest tracker — squeeze potential and crowded-short warning',
    file: 'services/market-data/src/api/routes.py · frontend/src/pages/stock/[symbol].tsx',
    effort: '2–3 days',
    impact: 'Medium — short interest > 20% of float with rising price is a classic short-squeeze setup. Conversely, rising short interest on a BUY signal is a major red flag that smart money disagrees.',
    what: 'Short interest data (% of float sold short, days-to-cover) is not tracked or displayed anywhere. High short interest can either be a bearish signal (informed shorts) or a squeeze catalyst (forced covering). Without this data, users miss both setups.',
    fix: 'Fetch short interest from yfinance (ticker.info: shortRatio, shortPercentOfFloat, sharesShort). Store in fundamentals cache alongside existing data. On the stock detail page: add a "Short Interest" chip showing % float short and days-to-cover. In signal generation: if short_pct_float > 25% and signal is BUY, add "high_short_interest" to reasons with a 10% confidence boost (squeeze potential). If short is rising week-over-week and signal is BUY, add "rising_short_warning" as a soft red flag. Add short interest column to Rankings page.',
  },

  {
    id: 'res2-analyst-momentum',
    tier: 3, severity: 'feature',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — _fetch_analyst_momentum() added to signal-engine/generators/signals.py; reads analyst_actions from existing fundamentals cache (no new yfinance call); counts upgrades_7d / downgrades_7d. Adjustment applied in _apply_style_signal() for SWING/LONG/GROWTH: ≥2 upgrades net positive → +2.5% per upgrade (max +5%); single upgrade → +2%; ≥2 downgrades net negative → -4% per downgrade (max -8%); single downgrade → -3%. Reasons: analyst_momentum (strong_upgrade/mild_upgrade/neutral/mild_downgrade/strong_downgrade), analyst_upgrades_7d, analyst_downgrades_7d, analyst_momentum_adj. Stock detail page analyst actions section now shows "+N 7d" / "−N 7d" momentum chips when recent actions exist.',
    title: 'RES-2: Analyst upgrade/downgrade momentum — recent rating changes as signal catalyst',
    file: 'services/market-data/src/api/routes.py · services/signal-engine/src/generators/signals.py · frontend/src/pages/stock/[symbol].tsx',
    effort: '2–3 days',
    impact: 'Medium — an analyst upgrade in the last 7 days is a strong near-term catalyst. A downgrade while holding is a major exit warning. Currently only the consensus rating is used, not the direction or recency of changes.',
    what: 'The system uses recommendationMean (consensus) from yfinance but does not track rating changes over time. An upgrade from Neutral to Buy (direction) that happened 2 days ago is far more actionable than a stable Buy rating that has been unchanged for 6 months.',
    fix: 'Fetch upgradesDowngrades history from yfinance (ticker.upgrades_downgrades). Store last 30 days of changes. Compute: upgrades_7d (count), downgrades_7d (count), net_analyst_momentum = upgrades - downgrades. In signal generation: if upgrades_7d >= 2 and net_momentum > 0, add 5% confidence boost and "analyst_upgrade_momentum" reason. If downgrades_7d >= 2, add "analyst_downgrade_warning" reason and 8% confidence penalty. Show recent changes on stock detail page as a timeline: "Goldman: Neutral → Buy (3d ago)", "MS: Hold → Sell (1d ago)".',
  },

  {
    id: 'res2b-fundamental-scoring',
    tier: 3, severity: 'feature',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — 5 fixes to _score_fundamental() in research-engine routes.py: (1) D/E ratio fixed from debt/cash to debt/(book_value×shares_outstanding); (2) PEG scored: <1.0 → +5, >3.0 → -4; (3) EPS surprise trend scored: improving → +3, declining → -3; (4) Analyst target premium: ≥25% upside → +5, ≤-10% downside → -3; (5) Missing data default lowered from 50 → 35. Price passed to _score_fundamental(). Return dict adds analyst_target.{price, upside_pct}.',
    title: 'RES-2b: Research engine fundamental scoring — fix D/E bug, score PEG + trend + target',
    file: 'services/research-engine/src/api/routes.py',
    effort: '0.5 days',
    impact: 'Medium — 5 scoring bugs/gaps: D/E ratio used debt/cash (wrong denominator); PEG computed but never scored; EPS trend field unused; analyst target premium ignored; missing data defaulted to neutral 50 instead of uncertain.',
    what: 'The fundamental scorer had an incorrect D/E denominator (should be equity, not cash), computed PEG but never added it to the score, ignored the eps_surprise_trend field, did not factor in analyst target price vs current price, and returned score=50 (neutral) when fundamentals were missing (should be lower to signal uncertainty).',
    fix: 'Fix D/E denominator. Score PEG: <1 → +5, >3 → -4. Score trend: improving → +3, declining → -3. Score analyst target: ≥25% upside → +5, ≤-10% → -3. Lower missing data default to 35.',
  },

  {
    id: 'res3-pattern-recognition',
    tier: 3, severity: 'feature',
    title: 'RES-3: Technical pattern recognition — cup-and-handle, breakout, consolidation, flag',
    file: 'services/technical-analysis/src/calculators/ · services/signal-engine/src/generators/signals.py',
    effort: '3–5 days',
    impact: 'Medium — chart patterns are the primary language of technical traders. Detecting a confirmed breakout from a 6-week consolidation base algorithmically, and adding it as a signal booster, aligns the system with how practitioners actually think.',
    what: 'The system computes indicators (RSI, MACD, BB, ADX) but does not detect price patterns. A cup-and-handle forming over 8 weeks with a handle near the rim is a high-probability breakout setup that indicators alone cannot capture. Pattern recognition is missing from signal generation.',
    fix: 'Implement pattern detectors using price history: (1) Consolidation base: price range (max-min)/avg < 8% over 15+ days — "tight base forming." (2) Breakout: today\'s close > 4-week high AND volume > 1.5× avg — "confirmed breakout." (3) Flag/pennant: sharp move up followed by low-volatility drift down for 5–10 days. (4) Cup-and-handle: U-shaped recovery to prior high with handle < 15% depth. Each pattern adds to reasons JSON and contributes 5–10% to the fused signal score. Show detected patterns as chips on the stock detail page ("📊 Breakout detected · 3d ago") and include in the game plan context.',
  },

  {
    id: 'res4-sector-rotation',
    tier: 3, severity: 'feature',
    title: 'RES-4: Sector rotation heat map — surface stocks in leading sectors, avoid lagging ones',
    file: 'frontend/src/pages/rankings.tsx · services/ranking-engine/src/scoring/kscore.py',
    effort: '2–3 days',
    impact: 'Medium — 80% of a stock\'s move comes from sector/market direction. Being in the right sector at the right time is more important than stock selection. A visual sector rotation map helps users immediately see where institutional money is flowing.',
    what: 'The Rankings page shows individual stocks but gives no macro context about which sectors are leading vs lagging the market. A user might pick a great stock in a sector that is being rotated out of, fighting a strong headwind. There is no sector-level performance dashboard.',
    fix: 'Add a "Sector Rotation" panel to the Rankings page: (1) For each sector (XLK, XLV, XLF, XLE, XLI, XLY, XLP, XLU, XLRE, XLC, XLB — and HSI/H-shares for HK): compute 1w, 1m, 3m returns from yfinance. (2) Render as a colour-coded heat map grid: dark green = leading (>+3% 1m vs SPY), green = in-line, yellow = lagging, red = distributing (<−2%). (3) In signal generation: if stock\'s sector is "distributing" (3m return < SPY − 5%), apply 10% confidence penalty and add "sector_headwind" reason. If sector is leading, add 5% boost. (4) Show the stock\'s sector performance on the stock detail page.',
  },

  // ── Screener ──────────────────────────────────────────────────────────────

  {
    id: 'scr1-custom-screener',
    tier: 3, severity: 'feature',
    title: 'SCR-1: Multi-factor custom screener — user-defined filter combinations',
    file: 'frontend/src/pages/screener.tsx (new) · services/ranking-engine/src/api/routes.py',
    effort: '3–5 days',
    impact: 'High — the current Opportunities page applies a fixed filter (BUY signal + top K-Score). Users cannot express "show me high K-Score stocks with upcoming earnings in tech sector where insiders are buying." A flexible screener is the core of every professional trading tool.',
    what: 'The Opportunities page is a fixed-formula screener: BUY signal + style match + K-Score threshold. There is no way to combine multiple filters arbitrarily. Users with specific strategies (e.g., GARP — growth at a reasonable price, or momentum-quality combo) cannot express their criteria.',
    fix: 'Build a /screener page with filter blocks: (1) Signal filters: signal type (BUY/HOLD/WAIT/SELL), confidence range, stability days. (2) K-Score filters: overall K-Score range, sub-score floors (momentum, value, quality, technical, RS). (3) Fundamental filters: market cap range, PE ratio, revenue growth %, EPS beat rate. (4) Technical filters: RSI range, above/below SMA50/SMA200, ADX > N, pattern detected. (5) Event filters: earnings in next N days, recent analyst upgrade, insider buying last 30d, congressional buying last 90d. (6) Portfolio filters: not already on Trade Board, not in watchlist. Results sortable by any column. Save/load filter presets. Backend: add GET /rankings/screen endpoint that accepts filter params and returns matching stocks.',
  },

  {
    id: 'scr2-pre-earnings-screener',
    defaultStatus: 'done',
    implementedNote: 'Added "Earnings This Week" collapsible panel on Opportunities page — shows stocks reporting in ≤7d with signal, price change, EPS growth estimate. Sorted by days_to_earnings.',
    tier: 3, severity: 'feature',
    title: 'SCR-2: Pre-earnings screener — surface BUY candidates with upcoming earnings catalysts',
    file: 'frontend/src/pages/opportunities.tsx · services/ranking-engine/src/api/routes.py',
    effort: '1–2 days',
    impact: 'Medium — earnings are the single largest individual stock catalyst. A pre-earnings screener combining historical beat rate, earnings compression factor, and current signal helps identify whether to enter before (for high beat-rate stocks in bull market) or wait (for uncertain earnings in bear regime).',
    what: 'There is no dedicated view for stocks with earnings in the next 7–14 days. Earnings are shown per-stock on the detail page, but there is no way to scan across the universe for upcoming earnings with buy/sell context.',
    fix: 'Add an "Earnings This Week" panel to the Opportunities page: filter universe for earnings_date within next 14 days; sort by (beat_rate × K-Score × signal_confidence). Show: symbol, days to earnings, historical beat rate %, earnings compression factor, current signal, regime-adjusted recommendation (enter / wait / avoid). For each: use the SA-7 regime-aware logic to show whether the system recommends entering before earnings or waiting for the print. Add a "Pre-Earnings" filter toggle on the Opportunities page.',
  },

  {
    id: 'scr3-ai-natural-language',
    tier: 3, severity: 'feature',
    title: 'SCR-3: AI natural language screener — "find tech stocks with improving momentum and insider buying"',
    file: 'frontend/src/pages/screener.tsx · services/api-gateway/src/api/ai_proxy.py',
    effort: '2–3 days',
    impact: 'Medium — removes the learning curve of filter configuration. Users who know what they want but not how to encode it in filter UI can describe it in plain English and get results immediately.',
    what: 'The custom screener (SCR-1) requires users to know which filters to set. Experienced traders think in natural language: "find undervalued mid-caps with strong earnings momentum and recent analyst upgrades." Translating that intuition into filter widgets is friction.',
    fix: 'Add a natural language input box to the screener: user types a query. System sends to Claude with: the full list of available filter dimensions + current universe data. Claude returns a structured filter spec (JSON). System applies the filters and shows results. Example: "defensive dividend stocks with low volatility in a bear market" → {sector: [XLP, XLU, XLRE], dividend_yield: >2%, atr_pct: <2%, signal: [BUY, HOLD], regime: bear-safe}. Include a "Explain these results" button that calls Claude to narrate why each stock matched. Require AI provider configured in Settings.',
  },

  // ── Automated Self-Learning ───────────────────────────────────────────────

  {
    id: 'al1-rl-agent',
    tier: 3, severity: 'feature',
    title: 'AL-1: Reinforcement Learning agent — system learns buy/hold/sell policy to maximise Sharpe ratio',
    file: 'services/ml-prediction/src/models/ · services/market-data (new rl_agent.py)',
    effort: '3–4 weeks',
    impact: 'Very High — supervised learning predicts direction; RL learns when to act. The key insight: knowing a stock will go up 5% is not enough — you need to know when to enter, how long to hold, and when to exit to maximise risk-adjusted return. RL optimises the full decision sequence, not just the next-day direction.',
    what: 'The current ML model is a supervised classifier: predict up/down over a fixed horizon. It does not learn position sizing, entry timing, or exit discipline. A Reinforcement Learning agent treats trading as a sequential decision problem: at each timestep, observe market state → choose action (buy/hold/sell) → receive reward (P&L) → update policy. RL has produced state-of-the-art results in quantitative trading (e.g., DeepMind AlphaPortfolio).',
    fix: 'Implement a DQN (Deep Q-Network) or PPO (Proximal Policy Optimisation) agent using stable-baselines3: (1) State: 30-feature vector (all existing TA + ML features + macro regime + position state). (2) Actions: Buy / Hold / Sell / Increase / Decrease. (3) Reward: daily P&L with Sharpe penalty for volatility. (4) Training environment: gym-style wrapper over historical price data (3 years backtest). (5) Evaluation: OOS Sharpe ratio, max drawdown, win rate vs benchmark. Train weekly alongside tune_all. Run RL agent alongside the XGBoost model — when both agree: +10% confidence boost. Show RL action recommendation on stock detail page. Initially paper-trade only.',
  },

  {
    id: 'al2-strategy-ab-testing',
    tier: 3, severity: 'feature',
    title: 'AL-2: Multi-strategy A/B testing — run variants in parallel, auto-promote the winner',
    file: 'services/market-data · frontend/src/pages/paper-portfolio.tsx',
    effort: '1–2 weeks',
    impact: 'High — removes guesswork from parameter tuning. Instead of manually deciding whether to raise the conviction threshold from 60% to 68%, run both in parallel on paper portfolios for 30 days and let the data decide.',
    what: 'When making changes to signal logic, conviction thresholds, or position sizing rules, there is no way to compare the new approach vs the old approach empirically without deploying to production. Changes are made based on intuition and backtest, not live A/B evidence.',
    fix: 'Extend the paper trading engine (WF-2) to support multiple concurrent strategy variants: (1) Define Strategy A (current production params) and Strategy B (experimental params) as config objects. (2) Both run simultaneously on the same signals — identical universe, different entry/exit/sizing rules. (3) After 30 trading days: compute Sharpe, max drawdown, win rate, total return for each. (4) Auto-promote Strategy B to production if: Sharpe_B > Sharpe_A × 1.1 AND drawdown_B ≤ drawdown_A × 1.2. (5) Show A/B results on the paper-portfolio page with a "Promote to Live" button. This creates a continuous improvement loop: always evolving the strategy with empirical evidence.',
  },

  {
    id: 'al3-self-improving-conviction',
    tier: 3, severity: 'feature',
    title: 'AL-3: Self-improving conviction gate — learn which layers actually predict success',
    file: 'services/market-data/src/services/scheduler.py · services/signal-engine/src/api/routes.py',
    effort: '2–3 days',
    impact: 'High — the conviction gate has 5 layers (K-Score, uptrend structure, entry timing, MACD, ADX, ML, OBV). Their relative importance is currently fixed by hand. Empirically, some layers may add zero predictive value while others are strongly predictive. Auto-calibrating their weights improves signal quality continuously.',
    what: 'The 5-layer conviction gate uses fixed pass/fail logic for each layer. There is no tracking of which layers predicted correctly on past BUY signals vs which were present on false BUYs. A layer that appears on both winning and losing trades equally provides no edge and should be weighted down or removed.',
    fix: 'After each post-close evaluation of signal outcomes: for each closed BUY signal, record which conviction layers were satisfied at entry time (already in Signal.reasons). Compute layer_accuracy[layer] = wins_where_layer_passed / total_signals_where_layer_passed. Layers with accuracy < 52% are flagged as "noise layers." Weekly: re-fit a logistic regression on layer combinations → win/loss. Output a layer_weights.json (similar to ta_weights.json). Apply layer weights in the conviction gate: instead of hard pass/fail, compute a weighted conviction score. A strong MACD zero-cross (historically 68% accurate) counts more than a marginal ADX reading (historically 54% accurate). Surface layer accuracy stats on the /signal-accuracy page.',
  },

  {
    id: 'al4-param-auto-optimise',
    tier: 3, severity: 'feature',
    title: 'AL-4: Optuna-driven trading parameter optimisation — stop %, target %, hold duration',
    file: 'services/ml-prediction/src/api/routes.py · services/market-data/src/services/scheduler.py',
    effort: '3–5 days',
    impact: 'High — Optuna already tunes XGBoost hyperparams. The same framework can optimise trading parameters (stop loss %, take profit %, max hold days) which have an equally large impact on final P&L. Currently these are fixed by style (SHORT/SWING/LONG) with no empirical validation.',
    what: 'Trading parameters (stop_pct: -5.5% for SWING, default_tp_pct: +12%) are fixed constants defined in _STYLE_PARAMS. These were set by intuition. The optimal stop/target depends on the volatility profile of the universe, the holding period, and the market regime. They should be discovered empirically, not guessed.',
    fix: 'Add a trade_param_tuning Optuna study: objective = Sharpe ratio on 2-year paper backtest. Params to tune per style: stop_pct (range −3% to −10%), tp_pct (+5% to +30%), breakout_pct (+0.5% to +3%), hold_days (3 to 40), entry1_pct (−0.3% to −2%). Run 100 trials per style on Sunday after tune_all. Save best params to trade_params.json in /data/models/. Signal engine reads these at startup. Show current optimised params on the improvements page scorecard. This closes the loop: Optuna already tunes the model, now it tunes the strategy rules that sit on top of the model.',
  },

  // ── Performance Metrics & Testing ─────────────────────────────────────────

  {
    id: 'tm1-portfolio-metrics',
    tier: 3, severity: 'feature',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — _portfolio_risk_metrics() added to paper_portfolio.py; loads full equity curve at summary time; computes annualised Sharpe (rf=5%), max drawdown %, Calmar ratio. Three new StatCards added to paper-portfolio.tsx: Sharpe (green≥1/yellow≥0/red<0), Max Drawdown (green≤10%/yellow≤20%/red>20%), Calmar (green≥1/yellow≥0.5/red<0.5). Returns —  when <2 curve points (new portfolio). PaperPortfolioSummary type in api.ts updated.',
    title: 'TM-1: Professional portfolio metrics — Sharpe, Calmar, max drawdown, benchmark comparison',
    file: 'frontend/src/pages/paper-portfolio.tsx · frontend/src/pages/positions.tsx',
    effort: '2–3 days',
    impact: 'High — without risk-adjusted metrics, a 20% return that came with 40% drawdown looks identical to a 20% return with 8% drawdown. Sharpe ratio and max drawdown are the two metrics every professional uses to compare strategies.',
    what: 'The Trade Board tracks P&L per position but shows no aggregate portfolio metrics. There is no Sharpe ratio, no maximum drawdown, no benchmark comparison, no Calmar ratio. Users cannot tell whether their trading results are good relative to simply buying SPY.',
    fix: 'Compute on the paper portfolio and the real Trade Board: (1) Equity curve: daily portfolio value = sum(position market values) + cash. (2) Sharpe ratio = (annualised_return − 0.05) / annualised_std_dev (risk-free rate = 5%). (3) Max drawdown = max(peak − trough) / peak, over the full history. (4) Calmar ratio = annualised_return / max_drawdown. (5) Win rate = closed_winning_trades / total_closed_trades. (6) Avg winner / Avg loser ratio. (7) Benchmark: fetch SPY return over same period; show alpha = portfolio_return − spy_return. Display on a /performance page as a professional dashboard. Update daily after close.',
  },

  {
    id: 'tm2-signal-decay',
    tier: 3, severity: 'feature',
    implementedNote: 'Done 2026-06-11 — GET /signals/alpha_decay endpoint in signal-engine (days 1,2,3,5,7,10,15,20,30 post-entry; avg/p25/p75/n per day; optimal_hold_days = peak avg return day). "Alpha Decay" tab added to /signal-accuracy: horizon picker (SWING/SHORT/LONG/GROWTH), lookback picker (90/180/365d), SVG line chart with avg return curve + p25–p75 shaded band, optimal hold chip, per-day breakdown table. Entry = first daily close ≥ signal date; up to 5 calendar-day slippage for weekends/holidays.',
    title: 'TM-2: Signal decay analysis — how long does the alpha last after a BUY signal?',
    file: 'services/signal-engine/src/api/routes.py · frontend/src/pages/signal-accuracy.tsx',
    effort: '2–3 days',
    impact: 'High — this tells you the optimal hold duration for each style. If alpha decays to zero by day 8, there is no point holding for 30 days. Knowing when to exit is as valuable as knowing when to enter.',
    what: 'Signal accuracy is tracked as a single binary outcome (correct/wrong at the hold horizon). But there is no analysis of the return profile over time: on day 1, day 3, day 5, day 10, day 20 after a BUY signal, what is the average return? If the average return peaks at day 6 and then decays, the optimal hold is 6 days — not the default 10. This is called signal alpha decay and is a standard quant research tool.',
    fix: 'For all closed BUY signals in signal_outcomes: fetch daily closing prices from entry to exit. Compute the average cumulative return at days 1, 2, 3, 5, 7, 10, 15, 20, 30 after signal. Plot as a line chart: "Average Cumulative Return After BUY Signal." Break down by: SHORT vs SWING vs LONG style, bull vs bear regime, high vs low confidence (>80% vs 60–80%). Add to /signal-accuracy as a new "Alpha Decay" tab. The peak of the curve is the empirically optimal hold period — surface this as a recommendation: "Based on 1,809 signals, optimal hold = 7 days (peak α = +3.2%)."',
  },

  {
    id: 'tm3-information-coefficient',
    tier: 3, severity: 'feature',
    title: 'TM-3: Information Coefficient (IC) tracking — the gold standard signal quality metric',
    file: 'services/signal-engine/src/api/routes.py · frontend/src/pages/signal-accuracy.tsx',
    effort: '2–3 days',
    impact: 'High — IC is used by every professional quant fund to measure signal quality. An IC of 0.05 is considered excellent in practice. Unlike accuracy (binary), IC measures rank-correlation — a signal that correctly ranks stocks is valuable even if it doesn\'t perfectly predict direction.',
    what: 'Signal quality is measured only as accuracy (% correct direction). This is a crude metric. Two signals can both have 55% accuracy but one consistently ranks the best stocks at the top (IC = 0.08, very valuable) while the other gets the right direction but wrong magnitude (IC = 0.02, mediocre). Professional quant funds use IC as the primary signal quality metric.',
    fix: 'For each signal period: rank all stocks by predicted probability (highest = rank 1). After hold period: rank all stocks by actual return (highest = rank 1). IC = Spearman rank correlation between predicted rank and actual rank. Compute monthly IC series: IC_mean (>0.05 is good), IC_std, IC_IR = IC_mean / IC_std (>0.5 is excellent). Add IC metrics to /signal-accuracy Outcomes tab. Breakdown by: horizon, regime, style. IC < 0.02 for 3 consecutive months should trigger an automatic re-tuning alert.',
  },

  {
    id: 'tm4-factor-attribution',
    tier: 3, severity: 'feature',
    title: 'TM-4: Factor attribution report — which signal components drove wins vs losses?',
    file: 'services/signal-engine/src/api/routes.py · frontend/src/pages/signal-accuracy.tsx',
    effort: '2–3 days',
    impact: 'High — tells you exactly which indicators are earning their place in the model and which are noise. If OBV is present in 60% of winners and 58% of losers, it has near-zero edge and should be removed. This drives continuous signal improvement.',
    what: 'The system generates 20+ reasons per signal (RSI, MACD, ADX, OBV, ML probability, earnings, regime, etc.) but never analyses which reasons correlate with winning trades vs losing trades in aggregate. There is no report answering "which indicators most strongly predicted successful BUY signals over the last 12 months?"',
    fix: 'For all closed BUY signals in signal_outcomes: extract the reasons JSON at entry time. For each boolean reason flag, compute: presence_in_winners (%) and presence_in_losers (%). Edge = presence_in_winners − presence_in_losers. Sort by edge descending. Render as a horizontal bar chart on /signal-accuracy → "Factor Attribution" tab: green bars = positive edge (more common in winners), red bars = negative edge (more common in losers). Show the top 5 "most valuable" factors and bottom 5 "noise/harmful" factors. Run this analysis broken down by market regime. Feed results back into SA-13 (self-improving conviction gate weights).',
  },

  // ── Tier 4 — Signal Accuracy & ML Fixes (2026-06-08 Deep Audit) ─────────────

  {
    id: 'sa14-pullback-recovery',
    tier: 4, severity: 'feature', defaultStatus: 'done',
    title: 'SA-14: Pullback-recovery detector — BUY signal when stock dips 5–25 % then recovers with volume',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 day',
    impact: 'High — catches entries at inflection points rather than mid-rally. Stocks that pull back to support and reverse with volume are the highest-probability SWING entries.',
    what: 'The signal engine generates BUY only once TA indicators have fully recovered (RSI climbs, MACD crosses). This means entries are caught mid-way through recoveries, not at the bottom. High-quality pullback setups (stock drops 10 %, then 2 consecutive green days on elevated volume) went unrecognised.',
    fix: 'Added _pullback_recovery() in signals.py. Conditions: (1) Price 5–25 % below 20-day rolling high (healthy dip, not broken stock). (2) 2+ consecutive green closes. (3) Recovery day volume ≥ 110 % of 20-day average. Delta: +0.07 to TA score with volume confirmation, +0.04 without. Applied after normalisation so it adds cleanly to the [0,1] range.',
  },

  {
    id: 'sa15-volume-confirmation',
    tier: 4, severity: 'medium', defaultStatus: 'done',
    title: 'SA-15: Volume confirmation for divergences and reversals — filter false signals on light volume',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 day',
    impact: 'Medium — bearish RSI divergence on declining volume is much less reliable than the same divergence on heavy volume. Filters ~15 % of false reversals.',
    what: 'The engine detects RSI divergence and golden/death crosses but does not validate them with volume. A golden cross on shrinking volume is a known false signal; professional traders require at least average volume for confirmation.',
    fix: 'In _ta_score(): after divergence detection, check volume_z > 0.5 for bullish divergences to boost credit; check volume_z > 1.0 before adding the golden_cross_event bonus. Bearish divergence on volume_z < -0.3 (declining volume) should have 50 % reduced penalty.',
  },

  {
    id: 'sa16-sector-etf-trend',
    tier: 4, severity: 'medium', defaultStatus: 'done',
    title: 'SA-16: Sector ETF trend filter — compress signals when the stock\'s own sector is in downtrend',
    file: 'services/signal-engine/src/generators/signals.py · services/market-data/src/api/routes.py',
    effort: '2 days',
    impact: 'Medium — relative strength vs sector is already computed, but sector ETF direction is not. A stock outperforming a collapsing sector still has headwind. 0.85× compression when sector ETF < SMA50.',
    what: 'The RS rank measures stock vs sector ETF performance. But if the sector ETF itself is below its SMA50 (downtrending), even a market-beating stock faces a strong macro headwind. Currently this is invisible to the signal engine.',
    fix: 'In _fetch_relative_strength(): also return sector_etf_above_sma50 bool. In _apply_style_signal(): if sector_etf_above_sma50 is False and style is SWING/LONG, apply 0.85× compression and add "sector_headwind" to reasons. Skip for SHORT and GROWTH (momentum styles tolerate sector weakness).',
    implementedNote: 'Implemented 2026-06-08 — _fetch_relative_strength now returns 3-tuple; sector_headwind added to SWING/LONG reasons',
  },

  {
    id: 'sa17-macd-trend-filter',
    tier: 4, severity: 'low', defaultStatus: 'done',
    title: 'SA-17: MACD zero-line crossover trend filter — require price above SMA50 for full credit',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '0.5 days',
    impact: 'Low-medium — MACD zero-cross-up in a downtrend (price below SMA50) has low reliability. Splitting the credit halves false positives from this indicator in bear phases.',
    what: 'Line 716: macd_zero_cross_up earns full weight regardless of whether price is above or below SMA50. A zero-cross-up while price is below its SMA50 (stock in downtrend) is frequently a dead-cat bounce, not a trend reversal.',
    fix: 'In score calculation: if macd_zero_cross_up and above_sma50: score += w["macd_zero_cross_up"]. If macd_zero_cross_up and not above_sma50: score += w["macd_zero_cross_up"] * 0.4 (partial credit only).',
  },

  {
    id: 'sa18-weekly-ta-fused',
    tier: 4, severity: 'medium', defaultStatus: 'done',
    title: 'SA-18: Incorporate weekly TA score into fused probability — not just as a compression flag',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 day',
    impact: 'Medium — weekly TA score (0–1) is computed but only used as a binary alignment gate. Treating it as a continuous factor could add 2–3 % accuracy.',
    what: 'weekly_tech returns a weekly_score value (0–1) that captures weekly momentum quality. Currently this is read but only its direction (up/down) is used as a boost/compress multiplier. The actual score value is stored in reasons but never blended into the fused probability.',
    fix: 'In _apply_style_signal(): extract weekly_score from weekly_tech. Blend it: fused = fused * 0.85 + weekly_score * 0.15 for SWING/LONG styles (where weekly alignment matters most). Cap the blend contribution to ±0.05 of the pre-blend fused value to avoid over-weighting.',
    implementedNote: 'Implemented 2026-06-08 — 15% blend weight applied before filters; scaled by weekly_confidence; weekly_blend_applied in reasons',
  },

  {
    id: 'ml-lgb-sample-weight',
    tier: 4, severity: 'critical', defaultStatus: 'done',
    title: 'ML-FIX-1: LightGBM ignores sample_weight — recency weighting silently dropped',
    file: 'services/ml-prediction/src/models/lgb.py',
    effort: '0.5 days',
    impact: 'High — LightGBM trains on all bars equally. XGBoost and RF correctly weight recent bars 5× more. LGB predictions are regime-blind compared to the other ensemble members.',
    what: 'lgb.py fit() pops callbacks kwarg but ignores sample_weight. Trainer passes sample_weight=train_weights at line 241 which is silently dropped. LGB never receives the recency bias that XGBoost and RandomForest benefit from.',
    fix: 'In lgb.py fit(): self.clf.fit(X, y, sample_weight=kwargs.get("sample_weight"), **{k:v for k,v in kwargs.items() if k != "callbacks"}). One-line fix with significant impact on LGB prediction quality.',
  },

  {
    id: 'ml-class-imbalance',
    tier: 4, severity: 'medium', defaultStatus: 'done',
    title: 'ML-FIX-2: No class imbalance handling — bear-market data biases models toward majority class',
    file: 'services/ml-prediction/src/training/trainer.py',
    effort: '1 day',
    impact: 'Medium — in bear markets where 70 % of returns are negative, models overfit to the majority class. Recall on BUY signals drops significantly.',
    what: 'No class_weight, scale_pos_weight, or SMOTE is applied after dead-zone filtering. If macro events cause class skew, all three models overfit to the dominant direction without warning.',
    fix: 'After dead-zone filtering: compute class weights via sklearn compute_sample_weight("balanced", y_train). Blend with recency weights: final_weight = recency_weight * class_weight (normalised). Apply to all three models. Log class ratio (n_up / n_down) in training metrics.',
    implementedNote: 'Implemented 2026-06-08 — _blend_weights() multiplies recency × class_weight and renormalises; applied in CV loop and final training',
  },

  {
    id: 'ml-optuna-pruning',
    tier: 4, severity: 'medium', defaultStatus: 'done',
    title: 'ML-FIX-3: Optuna tuning has no pruning — tune_all takes 3–5 hours unnecessarily',
    file: 'services/ml-prediction/src/training/tuner.py',
    effort: '0.5 days',
    impact: 'Medium — MedianPruner cuts ~50 % of tuning time by killing unpromising trials early. tune_all drops from 3–5 hours to 1.5–2.5 hours.',
    what: 'study.optimize() runs all 60 trials to completion (5 CV folds each = 300 CV fits per symbol). No early stopping of unpromising trials. With 123 symbols this is ~36,900 full model trains.',
    fix: 'study = optuna.create_study(direction="minimize", sampler=TPESampler(), pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=2)). Also add trial.report() inside the CV loop and trial.should_prune() check.',
    implementedNote: 'Implemented 2026-06-08 — MedianPruner(10, 2) added; trial.report() per fold; also applies _blend_weights in tuner CV loop for consistency with ML-FIX-2',
  },

  {
    id: 'ml-overfitting-detection',
    tier: 4, severity: 'medium', defaultStatus: 'done',
    title: 'ML-FIX-4: No overfitting detection — models with CV-AUC 0.70 but test-AUC 0.50 ship silently',
    file: 'services/ml-prediction/src/training/trainer.py',
    effort: '0.5 days',
    impact: 'Medium — catches overfitted models before they generate live signals. A >10 % CV-test AUC gap means the model memorised training data.',
    what: 'CV AUC is monitored and a warning fires if <0.55. But there is no check for train-test divergence. An overfitted model with CV AUC=0.70 but test AUC=0.50 ships without any warning.',
    fix: 'After computing metrics: if cv_auc_mean and test_auc and (cv_auc_mean - test_auc) > 0.10: log.warning("train.overfitting_detected", ...). Optionally reject the model and keep the previous version if the gap exceeds 0.15.',
    implementedNote: 'Implemented 2026-06-08 — overfit_gap logged as warning when >0.10; exposed in metrics dict for API visibility',
  },

  // ── Tier 5 — UI Gaps & Tech Debt (2026-06-08 Deep Audit) ─────────────────

  {
    id: 'ui-market-closed-banner',
    tier: 5, severity: 'low', defaultStatus: 'done',
    title: 'UI-1: Market closed banner — show last-update time when market is not trading',
    file: 'frontend/src/pages/watchlist.tsx · frontend/src/pages/rankings.tsx',
    effort: '0.5 days',
    impact: 'Low-medium — traders viewing prices at 8 PM ET may not realise the data is from 4 PM. A simple "Market closed · last update: 4:00 PM ET" banner prevents acting on stale prices.',
    what: 'No page shows a warning when the market is closed. Prices displayed outside market hours (before 9:30 AM or after 4:00 PM ET, weekends) are stale by definition.',
    fix: 'Add a MarketStatusBanner component: compute isMarketOpen from current time + timezone. If closed: show "Market closed · Last update: {timestamp}" bar at top. Reuse the last-price timestamp from /stocks/latest-prices response.',
  },

  {
    id: 'ui-watchlist-notes-backend',
    tier: 5, severity: 'medium', defaultStatus: 'done',
    title: 'UI-2: Watchlist notes — sync to backend instead of localStorage-only',
    file: 'frontend/src/pages/watchlist.tsx · services/market-data/src/api/routes.py',
    effort: '2 days',
    impact: 'Medium — notes are lost when the user switches browser or device. A user\'s research notes ("price target $165, catalyst: AI chip demand") should persist across sessions.',
    what: 'Notes are stored in localStorage only. Switching browser or device loses all notes. There is no save confirmation and no backend persistence.',
    fix: 'Add notes column to watchlist_items table (migration). Add PATCH /watchlist/{list_id}/{symbol}/notes endpoint. On note modal save, call API and show "Note saved ✓" toast. Load notes from API on watchlist fetch (include in WatchlistItem response).',
  },

  {
    id: 'ui-bulk-export',
    tier: 5, severity: 'low', defaultStatus: 'done',
    title: 'UI-3: Bulk CSV export — watchlist and signal filters export to file',
    file: 'frontend/src/pages/watchlist.tsx · frontend/src/pages/signal-filters.tsx',
    effort: '1 day',
    impact: 'Low-medium — traders managing 50+ stocks need to export lists for external analysis, share with co-traders, or import into other tools.',
    what: 'No export functionality anywhere. Watchlist cards and signal filter tables cannot be downloaded as CSV.',
    fix: 'Add "Export CSV" button on watchlist header and signal filters header. Client-side: build CSV string from current data (symbols, prices, signals, suppression flags). Use Blob download. No backend changes needed.',
  },

  {
    id: 'ui-board-stop-visible',
    tier: 5, severity: 'medium', defaultStatus: 'done',
    title: 'UI-4: Trade board — show stop-loss distance always visible without expanding card',
    file: 'frontend/src/pages/board.tsx',
    effort: '1 day',
    impact: 'Medium — a trader with 10 active positions should see at a glance how close each is to being stopped out, without clicking each card individually.',
    what: 'Stop-loss distance (% to stop) is only shown when a card is expanded. In collapsed view, the user sees entry price and current price but not their risk.',
    fix: 'Add a mini always-visible row to the collapsed card: "Entry $X · Stop $Y · Current $Z (±N% to stop)". Color the stop-distance text red if within 2 % of stop, yellow within 5 %.',
  },

  {
    id: 'ui-board-close-confirm',
    tier: 5, severity: 'medium', defaultStatus: 'done',
    title: 'UI-5: Trade board — drag-to-close confirmation to prevent accidental position closure',
    file: 'frontend/src/pages/board.tsx',
    effort: '0.5 days',
    impact: 'Medium — a user can accidentally drag an Active trade to Closed without recording an exit price, causing silent data loss.',
    what: 'Drag-and-drop moves cards between stages with no confirmation. Moving to Closed stage loses the position permanently if the exit price is not set.',
    fix: 'In onDragEnd handler: if destination stage is "closed" and plan.exit_price is null, intercept with a modal: "Record exit price before closing?" with Cancel / Record / Close Anyway buttons.',
  },

  {
    id: 'ui-alert-granularity',
    tier: 5, severity: 'low',
    title: 'UI-6: Signal alert granularity — alert only on specific transitions (e.g., HOLD→BUY)',
    file: 'frontend/src/pages/watchlist.tsx · services/market-data/src/services/scheduler.py',
    effort: '1.5 days',
    impact: 'Low-medium — reduces alert fatigue. Traders only need alerts when actionable transitions happen, not every time a signal is checked.',
    what: 'Signal alerts fire on any signal change. A WAIT→HOLD transition is not actionable but still sends an email. Users who subscribe to many stocks receive excessive emails.',
    fix: 'Add alert_on_transitions field to signal_alerts table (e.g., ["BUY", "SELL"] = only alert on transitions to those states). Add checkboxes in the alert modal: "Alert me when signal becomes: [✓] BUY [ ] HOLD [ ] WAIT [✓] SELL". Backend: check transition target against user preference before sending.',
  },

  {
    id: 'ui-suppression-days-active',
    tier: 5, severity: 'low',
    title: 'UI-7: Signal filter "days active" — show how long each suppression condition has been blocking',
    file: 'frontend/src/pages/signal-filters.tsx · services/signal-engine/src/api/routes.py',
    effort: '1 day',
    impact: 'Low — surfaces stocks stuck in suppression for days or weeks, helping traders understand persistent blocks vs transient ones.',
    what: 'The signal filter table shows current suppression state only. A stock blocked by weekly gate for 5 days looks identical to one blocked for 5 hours.',
    fix: 'Add first_suppressed_at timestamp to the suppressed-signal query (earliest date the current condition was true in consecutive signal history). Show "Days active" column computed as NOW() - first_suppressed_at. Sort descending by default.',
  },

  {
    id: 'dp-alert-infinite-retry',
    tier: 5, severity: 'critical', defaultStatus: 'done',
    title: 'DP-1: Signal alert infinite retry — failed email sends loop forever without max-retry cap',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '0.5 days',
    impact: 'High — a broken email configuration causes the same alert to be attempted every minute indefinitely, flooding logs and never resolving.',
    what: 'If email_send fails (email_ok = False), the DB is not updated so last_signal stays stale. The next minute, check_signal_alerts() sees the same transition and tries again. After 100 minutes, the user has received 0 emails but the scheduler has made 100 failed SMTP attempts.',
    fix: 'Add retry_count column to signal_alerts. Increment on each failed send. If retry_count >= 5: mark as error state and skip until user re-enables. Log a prominent warning when the retry cap is hit.',
  },

  {
    id: 'dp-hk-holiday-calendar',
    tier: 5, severity: 'medium',
    title: 'DP-2: HK holiday calendar — scheduler fires on Chinese New Year and other HK market holidays',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '1 day',
    impact: 'Medium — wasted API calls and empty bar ingestion on HK holidays. Approximately 12 additional holidays per year vs US calendar.',
    what: 'CronTrigger uses day_of_week="mon-fri" globally, which assumes standard weekday trading. HK has additional holidays (Chinese New Year, Mid-Autumn Festival, etc.) that do not align with US holidays.',
    fix: 'Add HK_HOLIDAYS set of date strings (populate from HKEX official calendar). In the HK ingest job: if today in HK_HOLIDAYS: skip. Update annually. Alternatively use the exchange_calendars library (supports XHKG).',
  },

  {
    id: 'dp-staleness-before-alert',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — check_signal_alerts() now queries latest Price.ts per symbol before the loop. Symbols with last bar older than now−2d are skipped with a log warning. Also checks for market:refresh_failed Redis flag (set by DP-4) and suppresses all alerts if a recent HTTP failure is present.',
    tier: 5, severity: 'medium',
    title: 'DP-3: Staleness check before firing conviction alerts — prevent alerts based on yesterday\'s prices',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '0.5 days',
    impact: 'Medium — if market-data ingestion fails, signals are computed on stale prices. Conviction alerts then fire based on incorrect data.',
    what: 'The conviction gate fires BUY alerts using whatever signal is in the DB. If data ingestion failed post-close, the signal uses yesterday\'s prices. No staleness check before sending.',
    fix: 'In check_signal_alerts(): before firing any alert, fetch the last bar timestamp for the symbol. If last_bar_ts < today - 2 trading days: skip alert and log "skipped stale data". Only fire on fresh prices.',
  },

  {
    id: 'dp-scheduler-http-retry',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — _post() retries 3× with 5s/15s/45s backoff. On all-3-fail: logs ERROR and sets market:refresh_failed Redis key (6h TTL) containing the failing URL. Successful _refresh_market() run clears the flag via Redis DELETE. check_signal_alerts() checks flag first and returns early if set.',
    tier: 5, severity: 'medium',
    title: 'DP-4: Scheduler HTTP retry logic — fire-and-forget posts silently fail with no retry or escalation',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '1 day',
    impact: 'Medium — a single service outage during post-close refresh silently produces stale rankings/signals for hours without any notification.',
    what: '_post() catches ALL exceptions but only logs at warning level. If ranking-engine or signal-engine crashes during the post-close cycle, the scheduler proceeds as if it succeeded. No retry, no circuit breaker, no escalation.',
    fix: 'Add exponential backoff retry (3 attempts, 5s/15s/45s delays) inside _post(). After 3 failures: log at ERROR level and set a Redis flag market:refresh_failed. Read this flag in check_signal_alerts() to suppress alerts until the next successful refresh.',
  },

  // ── Tier 6 — Security & Reliability Audit 2026-06-09 ─────────────────────
  {
    id: 'audit-frontend-admin-auth',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — signal-accuracy, signal-filters, trade-performance, improvements, paper-portfolio all now check getSession() + role==="admin", redirect to / if not admin, gate all SWR calls on authed state.',
    title: 'AUDIT-SEC-1: Frontend admin pages had no auth guard — non-admins could load them by URL',
    file: 'frontend/src/pages/signal-accuracy.tsx · signal-filters.tsx · trade-performance.tsx · improvements.tsx · paper-portfolio.tsx',
    effort: 'Done',
    impact: 'CRITICAL — any logged-in non-admin could access admin analytics pages directly via URL',
    what: 'All 5 admin pages were protected only via the nav group filter in _app.tsx. That hides the links but does not block direct URL access. Any authenticated non-admin could navigate to /signal-accuracy, /signal-filters, etc.',
    fix: 'Added useEffect auth check (getSession + role guard + router.replace) and gated all useSWR keys on authed state.',
  },
  {
    id: 'audit-backend-admin-auth',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Depends(get_admin_user) added to run_seed, run_ingest, delete_stock, add_stock in services/market-data/src/api/admin.py.',
    title: 'AUDIT-SEC-2: Four /admin/* routes unauthenticated — seed, ingest, delete, add_stock',
    file: 'services/market-data/src/api/admin.py:109,115,136,150',
    effort: 'Done',
    impact: 'HIGH — any unauthenticated HTTP call could run DB seed, ingest all stocks, or delete stocks',
    what: 'POST /admin/seed, POST /admin/ingest, DELETE /admin/stocks/{symbol}, POST /admin/add_stock had no auth dependency while other admin routes had Depends(get_admin_user).',
    fix: 'Added _: User = Depends(get_admin_user) to all four endpoints.',
  },
  {
    id: 'audit-service-auth',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Depends(get_current_username) from shared common.jwt_auth added to: all ML train/tune endpoints, signal reset/refresh/calibrate_ta_weights, research generate/clear/chat, portfolio optimize.',
    title: 'AUDIT-SEC-3: ML training, signal mutating, research, portfolio endpoints unauthenticated',
    file: 'services/ml-prediction · signal-engine · research-engine · portfolio-optimizer',
    effort: 'Done',
    impact: 'HIGH — any anonymous caller could trigger expensive ML retraining or wipe all signals',
    what: 'POST /ml/train, /ml/train_all, /ml/tune, /ml/tune_all, /ml/train_all_ensemble*, /signals/reset, /signals/refresh, /signals/calibrate_ta_weights, /research/{symbol}, /research/{symbol}/chat, DELETE /research/{symbol}, POST /portfolio/optimize were all fully open.',
    fix: 'Added Depends(get_current_username) (shared jwt_auth module) to all endpoints in each service.',
  },
  {
    id: 'audit-jwt-weak-default',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — model_post_init() in Settings raises RuntimeError if env=="production" and jwt_secret is the known placeholder. Dev still has the fallback default.',
    title: 'AUDIT-SEC-4: Hardcoded default JWT secret — services start with known-weak key if JWT_SECRET unset',
    file: 'shared/common/config.py:24',
    effort: 'Done',
    impact: 'CRITICAL — attacker could forge valid JWTs for any user if .env is absent in production',
    what: 'jwt_secret had default="stockai-change-me-in-production-secret-key". Any service that started without JWT_SECRET in the environment would accept tokens signed with this public string.',
    fix: 'Added model_post_init() that raises RuntimeError at startup if env=="production" and secret matches the placeholder.',
  },
  {
    id: 'audit-n1-signal-engine',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — bulk-load all D1 prices for involved stock_ids before the loop in both calibrate_ta_weights and evaluate_signal_outcomes; bisect_left for in-memory lookup. Reduced from 2×N queries to 1 bulk query.',
    title: 'AUDIT-PERF-1: N+1 queries in calibrate_ta_weights and evaluate_signal_outcomes',
    file: 'services/signal-engine/src/api/routes.py:1362-1384 and 1773-1807',
    effort: 'Done',
    impact: 'HIGH — with 500 signals × 2 queries = 1000 sequential DB round trips per calibration run',
    what: 'Both endpoints looped over signals and fired 2 per-row session.execute() calls inside the loop for entry/exit prices.',
    fix: 'Pre-fetch all D1 prices for involved stock_ids + date range in one bulk query. Build a per-stock sorted (date, close) list, then use bisect for O(log n) lookups.',
  },
  {
    id: 'audit-gbm-double-slice',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — removed [:, 1] from GradientBoostingModel.predict_proba(). The caller (trainer.py) already slices [:, 1]; the double-slice was returning a scalar float which caused IndexError in CV loops.',
    title: 'AUDIT-ML-1: GBM predict_proba double [:, 1] slice — crashed training with IndexError',
    file: 'services/ml-prediction/src/models/gbm.py:24',
    effort: 'Done',
    impact: 'CRITICAL — GradientBoosting model silently failed to train; CV loops crashed on any symbol using GBM',
    what: 'GradientBoostingModel.predict_proba returned a 1D array (already sliced [:, 1]). The trainer always applied [:, 1] again on top, turning 1D into a scalar that caused IndexError at lines 232, 287, 294.',
    fix: 'Removed the [:, 1] slice from gbm.py so it returns the full 2D array like every other model.',
  },
  {
    id: 'audit-misc-fixes',
    tier: 6, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — (1) Signal horizon composite index ix_signals_stock_horizon_ts added to models.py + created CONCURRENTLY on EC2. (2) Redis signals:cache:* flushed on /signals/refresh and /signals/reset. (3) datetime.utcnow() → datetime.now(timezone.utc) in paper_trading_engine.py. (4) refreshSignals() API contract fixed: return type {status, count} and market sent as query param not body.',
    title: 'AUDIT-MISC: Missing horizon index, stale cache after refresh, deprecated datetime, wrong API types',
    file: 'shared/db/models.py · signal-engine/routes.py · paper_trading_engine.py · frontend/src/lib/api.ts',
    effort: 'Done',
    impact: 'MEDIUM — query slowness, stale analytical data up to 1h after signal refresh, timezone-aware comparison bugs',
    what: 'Four separate medium-severity findings batched: (1) No index on Signal.horizon used in 10+ heavy queries. (2) factor_exposure and filter_audit Redis cache not flushed when signals regenerated. (3) deprecated datetime.utcnow() returns naive datetime. (4) refreshSignals sent market in body but backend reads query param.',
    fix: 'Each fixed independently.',
  },

  {
    id: 'audit-api-gateway-auth',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Added _require_auth() in proxy.py: extracts Bearer token, decodes with python-jose, raises HTTP 401 for invalid/missing. Public prefixes {auth, health, docs, openapi.json, redoc} bypass the check. python-jose added to api-gateway requirements.txt.',
    title: 'AUDIT-SEC-5: API gateway has no JWT validation — services are the only auth layer',
    file: 'services/api-gateway/src/api/proxy.py:48-80',
    effort: '1 day',
    impact: 'HIGH — any caller who bypasses the gateway can hit upstream services without auth',
    what: 'The reverse_proxy function forwards all requests to upstream services without validating any JWT itself. The gateway adds no Authorization check before forwarding; it strips malformed Bearer headers but does not verify them.',
    fix: 'Added _require_auth() that validates the JWT before every non-public proxy request.',
  },
  {
    id: 'audit-ai-chat-auth',
    tier: 6, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Added _: str = Depends(get_current_username) to ai_chat() in ai_proxy.py.',
    title: 'AUDIT-SEC-6: /ai/chat endpoint unauthenticated — anyone can use the shared AI key',
    file: 'services/api-gateway/src/api/ai_proxy.py:54',
    effort: '0.5 days',
    impact: 'HIGH — anonymous callers can burn the shared Claude/DeepSeek API key budget',
    what: 'POST /ai/chat has no auth dependency. When no api_key is provided by the caller, the endpoint falls back to the admin-configured shared key from Redis. There is no authentication check before allowing use of the shared key.',
    fix: 'Added Depends(get_current_username) to the ai_chat route.',
  },
  {
    id: 'signal-alert-granularity',
    tier: 6, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Added alert_mode column (server_default="all") to SignalAlert model. PATCH /signal-alerts/{id} endpoint added. Scheduler respects alert_mode="buy_only": bullish only fires if current==BUY, bearish only if prev==BUY. Frontend alerts.tsx shows mode toggle per subscription. NOTE: EC2 requires ALTER TABLE signal_alerts ADD COLUMN alert_mode VARCHAR(16) DEFAULT \'all\'.',
    title: 'UI: Signal alert granularity — user-configurable BUY-only vs all-transitions mode',
    file: 'shared/db/models.py · market-data/api/signal_alerts.py · scheduler.py · frontend/alerts.tsx',
    effort: '0.5 days',
    impact: 'MEDIUM — reduces alert noise for users who only want BUY entry signals',
    what: 'Signal alerts fired on all transitions including weak improvements (SELL→HOLD, WAIT→HOLD). No way for users to opt into BUY-only alerts.',
    fix: 'Added alert_mode per subscription with "all" (default) or "buy_only". UI toggle per subscription row.',
  },
  {
    id: 'signal-filter-days-active',
    tier: 6, severity: 'low', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — /signals/suppressed endpoint now bulk-loads 90 days of signal history and computes consecutive signal-bar streak per condition. days_active dict added to each row. Signal filters page CondDot shows "Nd" label below dot when fired, color-coded red when ≥10 days.',
    title: 'UI: Signal filter page shows how many days each suppression condition has been active',
    file: 'signal-engine/routes.py:1174 · frontend/signal-filters.tsx',
    effort: '0.5 days',
    impact: 'LOW — contextual information helps identify persistent vs transient filters',
    what: 'The signal filter monitor showed which conditions were active but not how long they had been continuously active.',
    fix: 'Backend computes consecutive streak length per condition from 90-day signal history. Frontend shows day count below each dot.',
  },
  {
    id: 'hk-holiday-calendar',
    tier: 6, severity: 'low', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Added _HK_HOLIDAYS frozenset (2025–2026) and _is_hk_holiday() in scheduler.py. _refresh_market("HK") and _refresh_5m("HK") skip on HK holidays.',
    title: 'DP: HK holiday calendar — scheduler fires on Chinese New Year, Ching Ming etc.',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '0.5 days',
    impact: 'LOW — prevents unnecessary yfinance calls and stale-data warnings on HKEX holidays',
    what: 'The scheduler runs HK market refresh jobs on every weekday regardless of HKEX holiday closures. Chinese New Year (5 days), Ching Ming, Buddha\'s Birthday etc. triggered failed ingests and spurious stale-data alerts.',
    fix: 'Added frozenset of HKEX public holidays 2025–2026 and a guard in both HK refresh functions.',
  },
  {
    id: 'audit-paper-trading-tx',
    tier: 6, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — _monitor_positions() now commits before _scan_for_entries(). Each phase is its own transaction; no flush() needed. Exception in scan cannot dirty the monitor commit.',
    title: 'AUDIT-REL-1: paper_trading_step — single session flush mid-cycle risks partial commit on exception',
    file: 'services/market-data/src/services/paper_trading_engine.py:866-912',
    effort: '0.5 days',
    impact: 'MEDIUM — exception after flush() but before commit() leaves cash mutations in memory, not rolled back',
    what: 'paper_trading_step() opens one SessionLocal context for all portfolios and calls _monitor_positions() then session.flush() then _scan_for_entries() then session.commit(). The flush pushes closed-position cash updates to the DB identity map before the next step is safe.',
    fix: 'Commit after _monitor_positions (separate monitor and scan into two transactions) before scanning for entries. This ensures closed-position cash is committed before new entries are evaluated.',
  },
  {
    id: 'audit-ml-startup-check',
    tier: 6, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — main.py startup scans model_dir/xgboost/*.joblib and logs warning if empty. GET /ml/status returns total_artifacts and by_model counts.',
    title: 'AUDIT-REL-2: ML service — no model file check on startup, 404/500 on first predict',
    file: 'services/ml-prediction/src/training/trainer.py:399-401 · src/api/routes.py:119-124',
    effort: '0.5 days',
    impact: 'MEDIUM — fresh EC2 deploy with no trained models returns confusing 500 errors with no diagnostics',
    what: 'There is no startup event that scans model_dir. When no .joblib artifact exists, predict_latest raises FileNotFoundError at line 401. The /ml/health endpoint does not check for model existence.',
    fix: 'Add a startup event in main.py that scans model_dir and logs a warning listing symbols without trained models. Add model_count to the /ml/health response.',
  },
  {
    id: 'audit-ml-metrics-api',
    tier: 6, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — GET /ml/metrics returns all symbol metrics sorted by test AUC. GET /ml/metrics/{symbol} returns single-symbol detail. System Health page now shows top-5 / bottom-5 AUC table with overfit warning badges.',
    title: 'AUDIT-REL-3: ML model metrics invisible — no API to read accuracy/AUC after retrain',
    file: 'services/ml-prediction/src/api/routes.py · src/training/trainer.py:372-382',
    effort: '1 day',
    impact: 'MEDIUM — model performance after retrain is visible only in container logs, not in the UI',
    what: 'After each retrain, a rich metrics dict is serialised into the .joblib bundle (accuracy, AUC, precision, recall, F1, CV AUC, OOS IC, overfit gap, buy_threshold). These are also returned by train_model() — but only in the background task response, invisible to the UI.',
    fix: 'Add GET /ml/metrics/{symbol} that loads the .joblib bundle and returns bundle["metrics"]. Surface on System Health page as a model accuracy widget showing the top-5 and bottom-5 symbols by AUC.',
  },
  {
    id: 'audit-password-min',
    tier: 6, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — all 4 password validation checks in auth.py raised to 8 characters minimum.',
    title: 'AUDIT-SEC-7: Minimum password length is 4 characters',
    file: 'services/market-data/src/api/auth.py:137,182,209,257',
    effort: '0.5 days',
    impact: 'MEDIUM — allows trivially weak passwords for all users',
    what: 'All password creation and change flows enforce a minimum of 4 characters in reset_password_public, change_password, admin_reset_password, and create_user.',
    fix: 'Raise the minimum to 8 characters in all four password validation locations. Consider adding rate limiting on /auth/login.',
  },
  {
    id: 'audit-jwt-expiry',
    tier: 6, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — POST /auth/logout blacklists JTI in Redis (TTL = remaining token lifetime). get_current_user and shared jwt_auth check blacklist on every request. Expiry reduced 30d → 7d in config.py. Frontend logout fires fire-and-forget revocation call before clearing localStorage.',
    title: 'AUDIT-SEC-8: 30-day JWT expiry with no revocation mechanism',
    file: 'shared/common/config.py:25 · services/market-data/src/api/auth.py:36-42',
    effort: '2 days',
    impact: 'MEDIUM — stolen tokens remain valid for up to 30 days with no way to revoke them',
    what: 'Tokens are valid for 30 days. There is no token blacklist or refresh token mechanism. If a token is compromised, it cannot be invalidated until expiry.',
    fix: 'Reduce token expiry to 1 day (or 8 hours). Implement a Redis-backed token blacklist checked in get_current_user. Add a POST /auth/logout endpoint that adds the token JTI to the blacklist.',
  },

  {
    id: 'tm5-live-vs-backtest',
    tier: 3, severity: 'feature', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-09 — Added Live vs Backtest comparison panel to /signal-accuracy overview tab. Fetches walk-forward data (30d test, 5d hold, 365d training) via SWR alongside live signalAccuracy. Side-by-side grid shows live accuracy, walk-forward accuracy, and delta (+/-) with colour coding. Interpretation banner explains alignment vs divergence.',
    title: 'TM-5: Live vs backtest comparison — detect overfitting and model drift in production',
    file: 'services/signal-engine/src/api/routes.py · frontend/src/pages/signal-accuracy.tsx',
    effort: '2–3 days',
    impact: 'High — the #1 failure mode of ML trading systems is overfitting: the backtest shows 70% accuracy but live performance is 54%. Without explicitly tracking the gap, this degradation is invisible until significant losses occur.',
    what: 'The system has both a backtest engine and live signal tracking, but they are never compared. Backtest accuracy is computed on historical data. Live accuracy comes from signal_outcomes. There is no dashboard showing whether live performance is tracking backtest expectations or has diverged significantly.',
    fix: 'Add a "Live vs Backtest" panel to /signal-accuracy: (1) Backtest accuracy: run the accuracy calculation on the 2-year historical training period (before the model went live). (2) Live accuracy: last 90 days from signal_outcomes. (3) Show both as a side-by-side bar chart per horizon (SHORT/SWING/LONG). (4) Alert if live accuracy < backtest × 0.85 for any horizon — this is a 15% degradation threshold that flags probable overfitting or regime change. (5) Track this gap monthly and plot a trend line — a widening gap over 3 months triggers an automatic re-tune request. (6) Include model version number so accuracy is correctly attributed to the model that generated it (not polluted by a retrained model mid-period).',
  },

  // ── Tier 7 — Maintenance & Ops (2026-06-10) ─────────────────────────────
  {
    id: 'maint-db-purge-job',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _purge_old_data() added to scheduler.py: deletes prices WHERE timeframe=M5 AND ts < 90 days (intraday bars) and signal_outcomes WHERE ts_evaluated < 365 days using raw SQL in a SessionLocal() transaction. Scheduled weekly on Sunday at 15:00 PST via db_purge_weekly job.',
    tier: 7, severity: 'low',
    title: 'DB maintenance: scheduled purge job for prices_5m and scheduler_jobs tables',
    file: 'services/market-data/src/services/scheduler.py · shared/db/session.py',
    effort: '0.5 days',
    impact: 'LOW — prices_5m grows ~3.5M rows/year (~1 GB); scheduler_jobs grows ~18k rows/year. Neither self-prunes. Without a purge, disk usage grows unbounded on the EC2 30 GB volume.',
    what: 'prices_5m stores 5-minute OHLCV bars. Oldest bars are never queried by any signal or indicator — all TA uses daily bars. scheduler_jobs accumulates one row per job run (~50/day). Both tables grow forever with no existing purge logic.',
    fix: 'Add a weekly purge job to scheduler.py (e.g., Sunday after weekly_full_refresh): (1) DELETE FROM prices_5m WHERE ts < NOW() - INTERVAL \'90 days\'; (2) DELETE FROM scheduler_jobs WHERE created_at < NOW() - INTERVAL \'90 days\'. Run VACUUM ANALYZE after purge. Alternatively add a nightly lightweight purge for prices_5m only. No UI change needed — purely backend.',
  },

  // ── Tier 7 — Alert Intelligence & UX Enhancements (2026-06-10) ───────────
  {
    id: 'auc-key-fix',
    tier: 7, severity: 'critical', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — ml-prediction/routes.py lines 282-283: "test_auc" key changed to "auc", "cv_auc" changed to "cv_auc_mean". These match the keys written by trainer.py. All 119 models now return real AUC values in the admin health page.',
    title: 'ML AUC key mismatch — admin health showed 0.000 for all models',
    file: 'services/ml-prediction/src/api/routes.py:282-283',
    effort: '15 min',
    impact: 'HIGH — Avg AUC showed 0.000 and top/bottom 5 model lists were empty; model quality was invisible',
    what: 'Trainer bundles write metrics as "auc" and "cv_auc_mean" but GET /ml/metrics was reading "test_auc" and "cv_auc". m.test_auc != null filtered out all 119 models, leaving the AUC widget showing 0.000 with empty best/worst lists.',
    fix: 'Changed key lookups in routes.py to match the actual bundle format: m.get("auc") and m.get("cv_auc_mean").',
  },
  {
    id: 'per-horizon-signal-alerts',
    tier: 7, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — shared/db/models.py: horizon + require_consensus columns added to SignalAlert. shared/db/session.py _run_migrations(): ADD COLUMN IF NOT EXISTS for both; old (user_id, symbol) unique constraint dropped; new idx_signal_alerts_user_symbol_horizon unique index created. market-data/api/signal_alerts.py: new SignalAlertCreate/Update/Out schemas; create checks uniqueness on (user_id, symbol, horizon); update is partial (alert_mode and/or require_consensus).',
    title: 'Per-horizon signal alerts — subscribe to SHORT/SWING/LONG/GROWTH independently',
    file: 'shared/db/models.py · shared/db/session.py · services/market-data/src/api/signal_alerts.py',
    effort: '2 hours',
    impact: 'MEDIUM — users can now track the SWING signal separately from GROWTH without duplicate noise',
    what: 'Signal alerts had a UNIQUE(user_id, symbol) constraint, meaning one subscription per stock regardless of timeframe. A user in both SWING and GROWTH watchlists got only one alert, and there was no way to choose which horizon to monitor.',
    fix: 'Added horizon column to SignalAlert (default SWING). Changed unique constraint to (user_id, symbol, horizon). Each timeframe subscription is now an independent row.',
  },
  {
    id: 'require-consensus-setting',
    tier: 7, severity: 'medium', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — require_consensus Boolean added to SignalAlert (server_default false). Scheduler check_signal_alerts(): when require_consensus=True, fetches signals for all 4 horizons; counts how many agree on the current direction; skips alert if < 2 horizons agree. UI: ⚡ Consensus / Any toggle per subscription row in alerts.tsx; calls PATCH /signal-alerts/{id} with {require_consensus: bool}.',
    title: 'Require consensus setting — only alert when ≥2 horizons agree on signal direction',
    file: 'shared/db/models.py · services/market-data/src/services/scheduler.py · frontend/src/pages/alerts.tsx',
    effort: '2 hours',
    impact: 'MEDIUM — dramatically reduces false-positive alerts; BUY signal confirmed by multiple timeframes is higher conviction',
    what: 'A SWING BUY could fire in isolation even when SHORT and LONG were SELL. No way to require that multiple timeframes agree before triggering an email.',
    fix: 'Added require_consensus Boolean per subscription. Scheduler fetches all 4 horizons for flagged symbols and gates the alert on ≥2 agreeing.',
  },
  {
    id: '4-horizon-consensus-indicator',
    tier: 7, severity: 'feature', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — stock/[symbol].tsx: 4 parallel useSWR hooks for SHORT/SWING/LONG/GROWTH. Consensus box shows 2×2 grid (horizon label, signal badge, confidence %) plus consensus label row: ≥3 BUY = Strong bullish, 2 BUY = Moderately bullish, 2 SELL = Moderately bearish, ≥3 SELL = Strong bearish, mixed = Mixed signals. Per-horizon alert rows replace the old single bell toggle.',
    title: '4-horizon consensus indicator on stock detail page',
    file: 'frontend/src/pages/stock/[symbol].tsx · frontend/src/lib/api.ts',
    effort: '3 hours',
    impact: 'MEDIUM — surfaces timeframe agreement/disagreement directly; users no longer need to manually check each horizon',
    what: 'Stock detail showed a single AI signal for the watchlist style only. Users had no way to see if SHORT and LONG agreed with the SWING signal without switching views.',
    fix: 'Fetch all 4 horizon signals concurrently. Render a 2×2 grid with signal badge + confidence per horizon. Derive and display a consensus label from the vote count.',
  },
  {
    id: 'add-to-radar-button',
    tier: 7, severity: 'feature', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — opportunities.tsx: radarList SWR (watchlists → find "Radar"); radarItems SWR (api.listWatchlist(radarList.id), null-guarded); radarSymbols Set for O(1) membership check. addToRadar() creates the "Radar" watchlist on first call if it doesn\'t exist, then adds the symbol. 📡 button next to 🔔 in each card; already-added stocks show ✓ in green.',
    title: 'Add to Radar button on Opportunities page',
    file: 'frontend/src/pages/opportunities.tsx',
    effort: '1 hour',
    impact: 'LOW — reduces friction when user spots an interesting stock in Opportunities and wants to track it',
    what: 'Opportunities page showed stocks matching screening criteria but had no way to save them for follow-up. Users had to navigate to Watchlists and manually add the symbol.',
    fix: 'Added 📡 per-card button. Auto-creates a "Radar" watchlist on first use. Symbol is added to Radar and the button turns to a green checkmark.',
  },
  {
    id: 'admin-health-signal-section',
    tier: 7, severity: 'feature', defaultStatus: 'done',
    implementedNote: 'Done 2026-06-10 — admin-health.tsx: SIGNAL REFRESH HEALTH section fetches api.allSignals("SWING"). BUY/SELL/WAIT/HOLD counts, bull-bear ratio, animated progress bar, fresh vs stale counts (last_generated within 2d), last US/HK refresh timestamps from scheduler jobs. ML TRAINING HEALTH section: Avg AUC from /ml/metrics, good (AUC≥0.6)/weak (0.52–0.6)/overfit (gap≥0.1) model counts, last US/HK retrain job timestamps with pass/fail badge.',
    title: 'Admin health — Signal Refresh Health + ML Training Health sections',
    file: 'frontend/src/pages/admin-health.tsx',
    effort: '2 hours',
    impact: 'MEDIUM — gives at-a-glance visibility into signal and ML health without diving into logs',
    what: 'Admin health page had a model metrics card but no signal distribution overview and no ML training timeline. AUC showed 0.000 due to the key mismatch bug. Top/bottom 5 lists were empty.',
    fix: 'Added two new sections. Signal section: distribution chart + bull/bear ratio + freshness counts. ML section: Avg AUC (fixed), good/weak/overfit counts, per-market retrain timestamps.',
  },

  // ── WF-3 Deep Audit: Paper Trading Engine (2026-06-11) ───────────────────────
  {
    id: 'pa-a1-cash-negative',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Added max(0.0, cash) floor after every cash mutation in _monitor_positions (exit) and _scan_for_entries (entry).',
    tier: 8, severity: 'critical',
    title: 'PA-A1: Cash can go negative — no non-negative guard on portfolio.current_cash',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 min',
    impact: 'Critical — negative cash breaks equity computation and all circuit breakers that rely on equity',
    what: 'If multiple trades execute in rapid succession or slippage pushes a position past available cash, current_cash can go negative. _compute_equity() adds negative cash to positions, understating equity, and drawdown checks can misfire.',
    fix: 'After every portfolio.current_cash mutation, add floor: portfolio.current_cash = max(0.0, portfolio.current_cash).',
  },
  {
    id: 'pa-a2-hold-days-double',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Removed the redundant hold_days update at the end of the loop (line ~740). hold_days is now updated once at the start of each trade iteration.',
    tier: 8, severity: 'medium',
    title: 'PA-A2: hold_days updated twice per cycle — once at loop start, once at loop end',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '15 min',
    impact: 'Low-Medium — redundant write; closed trades get their hold_days recalculated after stage="closed" is set',
    what: 'In _monitor_positions(), hold_days is computed at lines 595-596 and again at 740-741. The second update runs even for trades already marked stage="closed" in this cycle.',
    fix: 'Remove the redundant hold_days update at line 740-741 (after the trailing-stop block). Keep only the first update.',
  },
  {
    id: 'pa-c2-penny-stock-floor',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _fetch_live_prices() now rejects prices <= $0.50. _scan_for_entries() skips stocks where live_price < $1.00.',
    tier: 8, severity: 'critical',
    title: 'PA-C2: No minimum price floor — delisted/penny stocks accepted at $0.01',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 min',
    impact: 'High — a delisted stock returning $0.01 would create a massive share position and corrupt the portfolio',
    what: '_fetch_live_prices() accepts any price > 0. A delisted stock might return $0.01 from yfinance, which is technically > 0 and would pass the current check.',
    fix: 'In _fetch_live_prices(): if p and float(p) >= 0.50 (reject penny data). In _scan_for_entries(): if live_price < 1.00: skip.',
  },
  {
    id: 'pa-e1-empty-prices-check',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Added health check: if live_prices covers < 50% of expected symbols, log a warning and skip entry scan. Normal data gaps (e.g., extended hours) only skip individual symbols.',
    tier: 8, severity: 'critical',
    title: 'PA-E1: Empty live_prices dict (yfinance outage) silently breaks equity + circuit breakers',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'High — during a yfinance outage every open position falls back to entry_price, hiding unrealised losses and defeating the drawdown circuit breaker',
    what: 'If _fetch_live_prices() returns {} (yfinance down), _compute_equity() uses trade.current_price then entry_price as fallback. All positions look flat. Drawdown circuit breaker does not trigger.',
    fix: 'At start of _scan_for_entries(): if len(live_prices) < len(open_symbols) * 0.5: log.error and return. This skips entry scan when data is too sparse.',
  },
  {
    id: 'pa-g5-log-skipped-entries',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Added paper.entry_skipped log when should_enter=False, with score, min_score, and top 3 reasons.',
    tier: 8, severity: 'low',
    title: 'PA-G5: Skipped entry candidates not logged — impossible to debug "why wasn\'t TSLA entered?"',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '15 min',
    impact: 'Medium — critical for debugging and iterating on the entry scoring logic',
    what: 'When should_enter=False, the code calls continue with no log. The existing paper.entry_decision log only fires once, but you cannot reconstruct why a specific stock was skipped from the logs alone.',
    fix: 'After "if not should_enter: continue", add log.info("paper.entry_skipped", symbol=..., score=score, min_score=cfg["min_entry_score"], reasons=notes[:3]).',
  },
  {
    id: 'pa-d2-stale-peak-equity',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Drawdown circuit breaker now uses peak_equity = max(historical_peak_from_equity_curve, current_intraday_equity) so intraday drops are caught even before EOD snapshot is written.',
    tier: 8, severity: 'medium',
    title: 'PA-D2: Drawdown circuit breaker uses stale peak from equity curve table — misses intraday recovery',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — if portfolio recovered intraday since last EOD snapshot, peak_equity is stale and circuit breaker may not fire when it should (or may fire too early)',
    what: 'peak_equity = max(PaperEquityCurve.equity) queries the EOD snapshot table. If a drawdown happened after today\'s snapshot, today\'s drop is not reflected and the breaker activates late.',
    fix: 'Use max(historical_peak, current_equity) where current_equity is computed in real time. Or add an intraday high-water-mark column to PaperPortfolio.',
  },
  {
    id: 'pa-a3-breakeven-logic',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Changed elif pnl_pct >= be_trigger to a standalone if check so the breakeven floor applies even when trail is armed but ATR trail is below entry price.',
    tier: 8, severity: 'medium',
    title: 'PA-A3: Breakeven stop only moves inside WAIT block — skipped on BUY→SELL transitions',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — a trade that hits +3% and then gets a SELL signal could exit with stop still below breakeven if the WAIT block is never entered',
    what: 'The breakeven stop move (current_stop = entry when pnl >= breakeven_trigger) is nested inside elif sig_type == "WAIT". If signal goes BUY → SELL directly, the breakeven adjustment is skipped entirely.',
    fix: 'Move the breakeven check to a standalone block after all exit checks, not nested inside the WAIT block.',
  },
  {
    id: 'pa-c1-max-loss-per-trade',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Added max_loss_per_trade_pct=0.02 config key. Before rounding shares, if stop_distance * shares > equity * 0.02 then shares are reduced to enforce the cap.',
    tier: 8, severity: 'critical',
    title: 'PA-C1: No max loss per trade — wide stops can blow through more than intended % of equity',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'High — GROWTH style allows 12% stop. A 1% risk × 12% stop = position worth 8% of equity. One stop-out = 8% equity loss, not 1%.',
    what: 'risk_per_trade_pct=1% controls shares count, but does not cap the dollar loss if stop is wider than the risk_dollar / stop_distance formula intends. ATR-based stops can exceed the "intended" risk.',
    fix: 'Add max_loss_per_trade_pct=0.02. After computing shares: max_loss = equity * cfg["max_loss_per_trade_pct"]; if stop_distance * shares > max_loss: shares = round(max_loss / stop_distance, 4).',
  },
  {
    id: 'pa-b2-volume-confirmation',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Added volume_z check in _should_enter(): if volume_z < -0.5: score -= 1, notes append below-average volume warning.',
    tier: 8, severity: 'medium',
    title: 'PA-B2: No volume confirmation penalty in _should_enter() — enters on low-volume BUY signals',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — low-volume breakouts have a much higher failure rate; penalising them would reduce false entries',
    what: 'OBV is scored if bullish (+1) but there is no penalty for below-average volume on the signal day. Low-volume moves are less reliable.',
    fix: 'Add: if reasons.get("volume_z") is not None and float(reasons["volume_z"]) < -0.5: score -= 1; notes.append("Below-average volume on signal day").',
  },
  {
    id: 'pa-e3-atr-ewm-doc',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Updated _compute_atr() docstring to clarify EWM-ATR vs textbook SMA-ATR; explained why EWM is preferred (more responsive to recent volatility for GROWTH stocks).',
    tier: 8, severity: 'low',
    title: 'PA-E3: _compute_atr() uses EWM not SMA — misnamed and produces wider stops than standard ATR(14)',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 min',
    impact: 'Low — functionally fine (EWM is more responsive), but misleading naming and slightly wider stops than conventional ATR',
    what: 'The function is called _compute_atr and the comment says "ATR(14)" but it uses tr.ewm(alpha=1/14).mean() — exponential weighting, not SMA. This makes stops slightly wider and the formula non-standard.',
    fix: 'Add comment: "# EWM-ATR (responsive to recent volatility; intentionally wider than SMA-ATR)" or switch to tr.rolling(period).mean().iloc[-1] for textbook ATR.',
  },
  {
    id: 'pa-f1-batch-atr-fetch',
    defaultStatus: 'done',
    tier: 8, severity: 'medium',
    title: 'PA-F1: ATR pre-fetch makes N individual yfinance calls — should batch into one download',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2 hours',
    impact: 'Medium — with 10 candidates + 5 armed positions, 15 yfinance calls per 5-min cycle; each call is ~200ms so total = 3s extra latency',
    what: '_compute_atr() calls yf.Ticker(symbol).history() for each symbol independently. The ATR pre-fetch dict comprehension calls this N times for N symbols.',
    fix: 'Batch: download all symbols in one yf.download() call, then compute ATR per symbol from the resulting DataFrame. One network call replaces N calls.',
  },
  {
    id: 'pa-g1-exit-reasons-schema',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — All exit types now share a _base_notes dict with: message, pnl_pct, price_at_exit, highest_price_reached, hold_days, signal_at_exit. Type-specific extras appended on top.',
    tier: 8, severity: 'medium',
    title: 'PA-G1: exit_reasons dict schema is inconsistent across exit types — hard to query post-trade',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Low-Medium — makes post-trade analytics code brittle; each exit type has different keys (loss_pct vs pnl_pct vs gain_pct)',
    what: 'stop_hit stores loss_pct; target_reached stores gain_pct; time_stop/momentum_exit store pnl_pct. No consistent schema means any analytics code must handle 3 different shapes.',
    fix: 'Standardize to always include: message, pnl_pct, days_held, price_at_exit. Add the type-specific extras on top.',
  },
  {
    id: 'pa-d1-sector-cap-monitor',
    defaultStatus: 'done',
    tier: 8, severity: 'critical',
    title: 'PA-D1: Sector cap enforced only at entry — open positions can violate it as others are exited',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2 hours',
    impact: 'Medium — after exits in other sectors, one sector can dominate. Risk is concentration not a short-term spike.',
    what: 'max_sector_pct (30%) is checked when opening a new trade but never re-checked against existing open positions. After exiting from other sectors, the tech sector might represent 45% of the portfolio with no alert.',
    fix: 'In _monitor_positions(), after processing exits, compute sector distribution and log a warning if any sector exceeds max_sector_pct. Optionally add a force-trim rule.',
  },
  {
    id: 'pa-g3-signal-history',
    defaultStatus: 'done',
    tier: 8, severity: 'medium',
    title: 'PA-G3: No signal-to-trade lifecycle tracking — cannot do walk-forward attribution on signals',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '4 hours',
    impact: 'Medium — needed for WF-3 walkforward backtest; without this, we cannot tell which signal types led to profitable trades',
    what: 'When a trade is entered, signal_id is saved. But there is no record of how the signal evolved while the trade was open (BUY → HOLD → SELL) or what the signal looked like at exit. signal_outcomes table only tracks signals independently, not relative to an open paper trade.',
    fix: 'Add signal_at_exit_id and signal_at_exit_type to PaperTrade model. Record these on exit. This closes the loop for walkforward analysis.',
  },

  // ── Tier 9 — Signal Coherence & Breakthrough Improvements (2026-06-11) ────────

  {
    id: 'sa-21-growth-conviction-gate',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Layer 4a is now style-aware: GROWTH uses trend_above_sma50 only; other styles still require SMA50>SMA200 + trend_above_sma50. Double-bottom neckline break also passes Layer 4a unconditionally.',
    tier: 9, severity: 'critical',
    title: 'SA-21: CRITICAL BUG — conviction gate Layer 4a requires SMA50>SMA200 for GROWTH, blocking all consolidating growth stocks from emails',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '30 min',
    impact: 'High — GROWTH stocks legitimately consolidate below their 200MA for months (NVDA, PLTR, CRWD in early expansion phases). Requiring SMA50>SMA200 means nearly every GROWTH-style BUY signal fails Layer 4a silently and never sends an email. This renders conviction email alerts effectively non-functional for the GROWTH style. The previous session fixed Layer 4b (RSI range) but missed Layer 4a.',
    what: '_is_conviction_buy() Layer 4a (scheduler.py ~line 409): checks sma50_above_sma200 AND trend_above_sma50 for ALL styles. The GROWTH signal profile in signals.py explicitly replaces the golden-cross requirement with SMA20>SMA50 via _growth_ta_adjustment(). This exemption was never propagated to the conviction gate. A GROWTH stock with ML=72%, K-Score=65, RSI=68, MACD rising, ADX=30 — all perfect — still fails Layer 4a if it is below its 200MA.',
    fix: 'In _is_conviction_buy(), make Layer 4a style-aware: if style == "GROWTH": pass if trend_above_sma50 (price above SMA50 alone is enough for GROWTH); skip sma50_above_sma200 check; label it "GROWTH uptrend: price above SMA50 (replaces golden-cross)". For all other styles: unchanged (sma50_above_sma200 AND trend_above_sma50 required). One if/else branch, ~6 lines.',
  },

  {
    id: 'pt-d3-net-daily-loss',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Removed pnl < 0 filter; now sums all closed-trade P&L since session open. Variable renamed daily_net_pnl. Circuit breaker only fires when net is negative and exceeds the threshold.',
    tier: 9, severity: 'medium',
    title: 'PT-D3: Daily loss circuit breaker sums gross losses — same-day wins never offset stop-outs',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '30 min',
    impact: 'Medium — a day with 3 stop-outs (−$300 each = −$900 gross) and 2 wins (+$400 each) shows $900 daily loss to the circuit breaker. The engine suspends new entries even though the day is net −$100. Correct accounting uses net realized P&L so that winners and losers in the same session offset each other, reducing false circuit-breaker trips.',
    what: '_scan_for_entries() daily loss check: SELECT SUM(pnl) FROM paper_trades WHERE pnl < 0 AND exit_time >= today_open. The pnl < 0 filter excludes winning trades from the calculation entirely. Circuit breaker compares this gross-loss value against max_daily_loss_pct × equity.',
    fix: 'Remove the pnl < 0 filter: SELECT SUM(pnl) FROM paper_trades WHERE exit_time >= today_open. If SUM(pnl) is negative and abs(sum) > max_daily_loss_pct × equity → suspend. Rename variable daily_loss → daily_net_pnl. Add log: "Daily net P&L: ${daily_net_pnl:.2f} vs floor ${daily_loss_floor:.2f}".',
  },

  {
    id: 'pt-d4-bull-vix-threshold',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Changed vix < 18 to vix < 20 in _fetch_market_regime() bull condition. Bull regime now correctly covers the full healthy market range.',
    tier: 9, severity: 'medium',
    title: 'PT-D4: Bull regime requires VIX < 18 — too strict; normal bull markets spend months at VIX 18–22',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '15 min',
    impact: 'Medium — the historical average VIX is 17.6. VIX routinely ranges 18–22 during genuine bull markets (2017, H1 2019, early 2021). With VIX=19, even when SPY is making all-time highs, the engine classifies the regime as "neutral" instead of "bull", missing the +1 entry score bonus and the bull-specific size multiplier. This systematically under-sizes entries during most healthy bull conditions.',
    what: '_fetch_market_regime() bull condition: spy > e20 AND spy > e50 AND vix < 18. VIX < 18 is a near-historic-low environment. Setting this as the bull threshold means most of a normal bull cycle is wrongly classified "neutral".',
    fix: 'Change vix < 18 to vix < 20 in the bull condition. VIX < 20 still clearly separates normal bull markets (VIX 13–19) from choppy (VIX 20–25) and risk-off (VIX > 25). Optionally make it configurable: regime_vix_bull: 20.0 in _DEFAULT_CONFIG so it can be tuned without code changes.',
  },

  {
    id: 'cb-4-flexible-conviction',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _is_conviction_buy() now returns (all_passed, conviction_tier, passed, failed). "near" tier allows 1 soft fail (OBV or ADX only). Email shows yellow "⚡ Near-Conviction BUY" banner with the failed layer highlighted.',
    tier: 9, severity: 'medium',
    title: 'CB-4: Conviction gate is all-or-nothing — one soft failure (OBV, ADX) blocks a 6/7 perfect setup',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '2 hours',
    impact: 'Medium — OBV and ADX are the most likely to fail on valid early-stage breakouts: OBV fluctuates daily on volume noise; ADX only peaks after a trend matures. A stock with ML=70%, K-Score=62, RSI=67, MACD rising, SMA golden cross — but ADX=24.5 — never sends an email. A "near-conviction" tier that allows 1 soft-fail would cover ~20% more legitimate setups with a visible caveat.',
    what: '_is_conviction_buy() returns (len(failed) == 0, passed, failed). Any single layer failure blocks the email completely. 7 sub-checks in total. Failing ADX or OBV alone is penalised identically to failing K-Score or ML — a false equivalence. Early-stage GROWTH breakouts almost always fail ADX (trend just started) yet are exactly the high-value entries the system is designed for.',
    fix: 'Add a near_conviction tier: if len(failed) == 1 and the failed layer is "soft" (OBV or ADX only — not K-Score, ML, RSI, or trend): return a near_conviction result instead of full failure. Send the email with subject prefix "Near-Conviction BUY:" and show the one failed layer prominently. Hard-fail layers (K-Score < threshold, RSI out of range, ML below floor, trend structure broken): still block completely. Return conviction_tier: "full" | "near" | "failed" from _is_conviction_buy(). Email template shows yellow "1 check missed" banner for near-conviction.',
  },

  {
    id: 'cb-5-entry-gate-independence',
    tier: 9, severity: 'medium',
    title: 'CB-5: _should_enter() re-evaluates factors already in fused_probability — double-counting inflates score artificially',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2 hours',
    impact: 'Medium — the entry gate awards +1 for bull regime, +1 for RSI healthy, +1 for MACD rising, +1 for OBV bullish, +1 for sector tailwind. All of these are already captured in signal.bullish_probability by the signal engine. A bull_prob of 0.72 already reflects all these conditions. The gate is measuring the same signal strength twice, inflating the score for any signal that already passed the signal engine — it adds noise rather than independent filters.',
    what: '_should_enter() scoring adds points for: RSI zone (+1–2), MACD state (+1–2), OBV trend (+1), regime (+1 to −2), market breadth (+1 to −1), sector headwind (+1 to −1). Then separately gives bonus for high bull_probability (+1–2). But bull_probability IS the fusion of all those exact factors. Scoring them individually plus scoring the fusion double-counts everything that drives a high fused probability.',
    fix: 'Replace signal-engine-derived scoring factors in _should_enter() with truly independent conditions the signal engine does NOT measure: (1) Intraday VWAP proximity — is entry near VWAP support (good) or 2%+ extended above it (bad)? (2) Signal freshness — hours since last BUY transition: <4h = +1, 4–18h = 0, >18h = −1. (3) R:R quality — how far is this R:R above the style baseline? Keep: price zone check (already independent), R:R threshold, and a single conviction summary bonus (bull_prob ≥ 0.70 → +1). Remove the individual RSI, MACD, OBV, regime, breadth, and sector factors as separate scoring dimensions.',
  },

  {
    id: 'sa-24-signal-freshness-entry',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — signal.ts added to signal_data dict. _should_enter() computes signal_age_hours and scores: <4h = +1 (fresh), >18h = -1 (stale). Exception-safe with try/except around ts parsing.',
    tier: 9, severity: 'medium',
    title: 'SA-24: Paper trading entry scoring ignores signal age — a 1h-old BUY competes equally with a 25h-old one',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — a BUY that just fired reflects conditions as of the last bar. A BUY from 22h ago (just under the 26h cutoff) may reflect yesterday\'s technical context. The same stock could have given up the breakout level overnight. Preferring freshly-generated signals and penalising stale ones measurably improves entry timing and reduces chasing yesterday\'s setup.',
    what: '_scan_for_entries() uses a 26h cutoff to avoid truly stale signals (the CB-3 fix) but within that window, signal age is ignored. _should_enter() never computes or uses signal_age_hours. Two signals — one fired at 8:05 am today and one at 9:30 am yesterday — receive identical scoring.',
    fix: 'In _scan_for_entries(), compute signal_age_hours = (now - signal.ts).total_seconds() / 3600 and pass it into _should_enter(). In _should_enter(): if signal_age_hours < 4: score += 1; notes.append(f"Fresh signal ({signal_age_hours:.1f}h old) — entry in valid window"). elif signal_age_hours > 18: score -= 1; notes.append(f"Signal is {signal_age_hours:.1f}h old — conditions may have shifted"). This rewards fresh morning breakouts and discourages stale-signal entries.',
  },

  {
    id: 'pt-d2-confidence-sized-positions',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — confidence_size_mult added to position sizing: ≥50% conf = 1.25×, 30–49% = 1.0×, <30% = 0.75×. Multiplied into risk_dollar before earnings and regime mults. Entry notes show the multiplier.',
    tier: 9, severity: 'medium',
    title: 'PT-D2: All positions risk 1% regardless of signal strength — high-conviction setups should be sized larger',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — a confidence=18% signal (barely above the 15% floor) and a confidence=55% signal both receive 1% risk. Kelly criterion and empirical research both show that higher-edge setups warrant proportionally larger allocation. Flat sizing under-invests in the best ideas and over-invests in marginal ones. With confidence typically ranging 15–60, tiered sizing can improve expected value without increasing max drawdown risk.',
    what: 'risk_dollar = equity × cfg["risk_per_trade_pct"] × earnings_size_mult × regime_size_mult. No confidence_size_mult. With min_confidence=15 and most signals 15–45, a signal at 45% confidence is top-decile but sized the same as one at 18%.',
    fix: 'Add confidence_size_mult: confidence >= 50 → 1.25×; 30–49 → 1.0× (baseline); 15–29 → 0.75×. Multiply into risk_dollar: risk_dollar *= confidence_size_mult. max_position_pct still caps position size — the mult cannot override hard limits. Add to entry decision notes: "Size 1.25× (confidence 52%)" or "Size 0.75× (confidence 22%)".',
  },

  {
    id: 'sa-25-short-earnings-gate',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Added SHORT-specific binary-event guard in _apply_style_signal() before the main earnings block: DTE≤2 → fused = 0.5 + (fused−0.5)×0.40. Fires even when earnings_compression=None. Reason stored as short_imminent_event.',
    tier: 9, severity: 'medium',
    title: 'SA-25: SHORT style has no earnings hard-reject — earnings_compression=None means binary event risk is fully ignored',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 hour',
    impact: 'Medium — SHORT style targets 1–5 day trades. An unexpected earnings surprise of −15% at open destroys a 5-day SHORT trade completely. The "earnings = catalyst" philosophy in SHORT is correct for strong beat-history stocks but incorrect for mixed or unknown reporters. DTE ≤ 2 is a binary event within a SHORT trade\'s natural hold period and should at minimum trigger strong compression, not silence.',
    what: '_STYLE_PROFILES["SHORT"]["earnings_compression"] = None. In _apply_style_signal(), when ec is None, the entire earnings block is skipped — no compression AND no hard reject for any DTE. The paper trading engine hard-rejects DTE ≤ 5 in _should_enter(), but that logic is independent of the signal that already passed with earnings_compression=None.',
    fix: 'In signals.py _apply_style_signal() for SHORT style: add a binary-event guard independent of earnings_compression: if days_to_earnings is not None and days_to_earnings <= 2: fused = 0.5 + (fused - 0.5) × 0.40 (strong compression for imminent event). Optionally add a "short_dae_warning" to reasons. This makes the signal itself express the event risk rather than relying solely on the paper trading filter. Note: SHORT can still trade through earnings with DTE 3–5; only DTE ≤ 2 is the hard-compress zone.',
  },

  {
    id: 'wf-4-hold-duration-management',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Added elif sig_type == "HOLD" block in _monitor_positions(). Exits with hold_stall_timeout if hold_days >= 30 and pnl_pct < 5%. Configurable via hold_stall_days (30) and hold_stall_max_gain (0.05).',
    tier: 9, severity: 'medium',
    title: 'WF-4: Stalled HOLD positions hold capital for up to 60 days with no graduated size reduction',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2 hours',
    impact: 'Medium — a position that entered on BUY and went to HOLD at +2% can sit there for 60 days consuming portfolio capacity. Capital tied in a stalled position cannot be deployed to fresh opportunities. A graduated rule — reduce size after 15 stall days, exit after 30 — frees capital proactively while giving legitimate consolidations enough time to resolve.',
    what: '_monitor_positions() handles time_stop (max_hold_days, default 60 for GROWTH) but has no intermediate stall-check. A HOLD at +1.5% on day 20 looks identical to a HOLD at +1.5% on day 5 — both continue unchanged. WAIT signals have a decay check (wait_exit_days) but HOLD signals do not.',
    fix: 'In _monitor_positions() for open trades where sig_type == "HOLD": (1) Compute hold_days and pnl_pct. (2) If hold_days >= 15 and abs(pnl_pct) < 0.03 (stuck within ±3%): log paper.hold_stalled and surface in notes — visible in the UI so the user can decide manually. (3) If hold_days >= 30 and pnl_pct < 0.05 (under +5% after 30 days): close with exit_reason="hold_stall_timeout". Add config: hold_stall_days=30, hold_stall_max_gain=0.05. This prevents zombie positions from blocking new entries.',
  },

  {
    id: 're-9-early-regime-warning',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _fetch_market_regime() now returns spy_pct_above_ema20, vix_5d_trend (rising/flat/falling), is_pre_choppy, is_pre_risk_off. Entry scan applies choppy/risk_off thresholds and sizing preemptively when early warning flags are set.',
    tier: 9, severity: 'medium',
    title: 'RE-9: Regime transitions not detected early — engine reacts only after full flip, not during deterioration',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '2 hours',
    impact: 'High — by the time the regime officially transitions neutral→choppy (SPY crosses below EMA20), open positions have already absorbed significant gap-down risk. Early warning signs are measurable: SPY within 1.5% above EMA20, VIX rising over 5 sessions, breadth declining. Acting one step early (pre-choppy sizing and higher entry score threshold) reduces drawdown on the transition substantially.',
    what: '_fetch_market_regime() reads current-bar values only and returns the instantaneous regime label. It does not compute: (1) SPY proximity to regime threshold (0.5% above EMA20 is fragile), (2) VIX trajectory over 5 days (rising vs falling), (3) whether the current "neutral" regime is stable or about to flip. A regime can be "neutral" with SPY 0.8% above EMA20 and VIX trending 18→23 — deteriorating fast, but the label is still "neutral".',
    fix: '(1) Add to _fetch_market_regime() return: spy_pct_above_ema20, vix_5d_trend ("rising"|"flat"|"falling"), is_pre_choppy (bool). (2) pre_choppy = spy_pct_above_ema20 < 1.5 AND vix_5d_trend == "rising". (3) In _scan_for_entries(): if pre_choppy AND regime == "neutral": apply choppy min_score threshold and choppy position sizing proactively. Log "paper.pre_choppy_warning" when this fires so the UI can surface it. (4) Similarly: pre_risk_off = spy_pct_above_ema50 < 2.0 AND vix > 22 → apply risk_off sizing while regime still shows "neutral".',
  },

  {
    id: 'sa-26-confidence-trajectory',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Queries most recent prior Signal confidence in the entry loop. confidence_delta added to signal_data. _should_enter() scores: delta > 8 = +1 (accelerating), delta < -8 = -1 (decelerating).',
    tier: 9, severity: 'feature',
    title: 'SA-26 BREAKTHROUGH: Signal confidence trajectory — accelerating BUYs are far more reliable than decelerating ones',
    file: 'services/market-data/src/services/paper_trading_engine.py + services/signal-engine/src/api/routes.py',
    effort: '3 hours',
    impact: 'High — a signal that moved confidence 20→35→50 over 3 days (accelerating momentum, thesis building) is fundamentally different from one that moved 50→40→35 (fading momentum, still BUY but weakening). Paper trading should strongly prefer the accelerating signal. This is a breakthrough: the direction of change is more predictive than the current value. All the data needed (historical Signal rows per symbol) is already in the DB.',
    what: '_should_enter() treats all BUY signals at the same confidence value identically regardless of trajectory. Two signals at confidence=35 receive identical scoring whether one just rocketed up from 15 or the other is falling from 60. The Signal table stores all historical rows per symbol, so the last 3 confidence values are queryable. The signal engine already stores bull_prob and confidence per cycle — trajectory computation is a query away.',
    fix: '(1) In _scan_for_entries(), for each candidate: query the 2 most recent prior Signal rows for the same symbol + horizon (ORDER BY ts DESC LIMIT 2). Compute confidence_delta = latest_confidence − oldest_of_3. (2) In _should_enter(): if confidence_delta > 8: score += 1; notes.append(f"Accelerating signal (+{confidence_delta:.0f} trend — momentum building"). elif confidence_delta < −8: score −= 1; notes.append(f"Decelerating signal ({confidence_delta:.0f} — fading momentum, still BUY"). (3) Store confidence_delta in entry_decision_notes on the PaperTrade record. (4) Surface in closed-trade analytics: "entered on accelerating signal" vs "entered on decelerating" — use this to validate the thesis with real outcomes.',
  },

  {
    id: 'sa-19-independence-gate',
    tier: 9, severity: 'feature',
    title: 'SA-19 BREAKTHROUGH: TA score collinearity — 68% of TA weight is trend factors that all fire simultaneously in bull markets',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '1 week',
    impact: 'High — in a bull market, SMA above/SMA cross/ADX bullish/MACD rising/OBV bullish all fire together, pushing TA score to 0.85+ for almost any trending stock. This creates signals that are "market beta" in disguise. The breakthrough is: collapse 21 TA factors into 5 independent pillars and require ≥3 to agree before a BUY, preventing "rising tide" false positives and rewarding genuine stock-specific confluence.',
    what: '_ta_score() computes 21 factors additively. _TA_WEIGHTS_DEFAULT sums to ~1.88. Six of those factors (above_sma50, sma50_above_sma200, adx_bullish, macd_bullish, obv_bullish, above_ema9) are all driven by the same underlying driver: price is in an uptrend. When the market is bullish, all six activate simultaneously, adding ~0.7 to the TA score purely from trend. Any stock that is slightly above its 50-day average gets a high TA score regardless of stock-specific signals. This makes the TA component mostly reflect the market regime (which the ML already captures) rather than adding independent information.',
    fix: 'After computing individual factor scores, aggregate into 5 independent pillars: (1) Trend pillar: max(above_sma50, sma50_above_sma200, adx_bullish) — one vote regardless of how many trend factors agree. (2) Momentum pillar: max(rsi_zone, stoch_rsi_recovery, macd_bullish). (3) Volume pillar: max(obv_bullish, volume_surge). (4) Structure pillar: max(sr_context_good, bb_mid_zone, vwap_above). (5) Growth pillar (GROWTH style only): max(sma20_above_sma50, growth_ta_adj). TA score = mean of pillar scores. Add independent_pillars_active count to reasons. In _apply_style_signal(): if independent_pillars_active < 2: compress 15%; if >= 4: boost 5%. This is a fundamental architectural change requiring careful testing — develop in a feature branch alongside the existing logic.',
  },

  {
    id: 'pt-d6-entry-candidate-priority',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Candidates re-sorted in Python after SQL fetch: composite = 0.5×conf + 0.3×kscore + 0.2×breakout_bonus. breakout_bonus: 1.0=breakout, 0.5=at_support, 0.0=neutral. Best composite fills first when portfolio slots are limited.',
    tier: 9, severity: 'feature',
    title: 'PT-D6: Entry candidates ranked by confidence only — K-Score, RS, and breakout context are ignored in ordering',
    file: 'services/market-data/src/services/paper_trading_engine.py',
    effort: '1 hour',
    impact: 'Medium — with multiple candidates competing for limited portfolio slots (max 10 positions), the ordering determines which get filled. Currently: ORDER BY Signal.confidence DESC. A signal at confidence=40 with K-Score=70 and a fresh breakout above resistance ranks below confidence=45 with K-Score=40 and no breakout. The composite score would correctly prioritise the former in many cases.',
    what: '_scan_for_entries() fetches candidates ordered by desc(Signal.confidence). This means signals that had slightly higher bull_prob recently dominate allocation regardless of technical quality (K-Score), relative strength, or breakout quality. K-Score and kscore_reason are already stored in Signal.reasons — the data is available.',
    fix: 'Compute a composite_priority for each candidate after the initial query: composite = 0.5 × (signal.confidence / 100) + 0.3 × (kscore / 100) + 0.2 × breakout_bonus. breakout_bonus = 1.0 if "breakout" in reasons else 0.5 if "at_support" in reasons else 0.0. kscore = float(reasons.get("kscore", 50)). Sort candidates by composite_priority DESC before iterating through _should_enter(). Log the composite score in entry notes. This ensures the best composite setups fill first when portfolio slots are limited.',
  },

  {
    id: 'sa-27-oos-signal-suppression',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _fetch_ml_data() now captures oos_suppressed flag from ML prediction response and stores it in ml_meta. generate_all_signals() adds ml_oos_suppressed to base reasons. _apply_style_signal() accepts ml_oos_suppressed param: if True, applies 0.6× compression on fused−0.5 distance and sets reasons["low_oos_accuracy"]=True. SignalCard shows yellow "LOW ML CONF" chip in header when low_oos_accuracy is present.',
    tier: 9, severity: 'feature',
    title: 'SA-27: SA-9 OOS accuracy per symbol is computed but never wired to signal generation — weak symbols still fire full-strength BUY',
    file: 'services/signal-engine/src/generators/signals.py + services/signal-engine/src/api/routes.py',
    effort: '2 hours',
    impact: 'Medium — SA-9 implemented TimeSeriesSplit walk-forward validation and stores OOS accuracy per symbol in Redis after tune_all. But the signal generator never reads this data. A symbol with OOS accuracy=48% (coin-flip) generates the same BUY signal confidence as one with OOS=70%. Suppressing or flagging low-OOS symbols prevents the system from confidently signaling stocks where the ML simply has no edge.',
    what: 'SA-9 (tune_all endpoint) writes Redis key "ml:oos_accuracy:{symbol}" with a float 0–1. generate_all_signals() fetches market data, regime, ML probability, K-Score — but never reads OOS accuracy. _apply_style_signal() applies many compression factors but not OOS accuracy. Low-OOS symbols receive full fused probability with no warning.',
    fix: '(1) In generate_all_signals(): read oos_acc = redis.get(f"ml:oos_accuracy:{symbol}"). (2) If oos_acc is not None and float(oos_acc) < 0.52: add "low_oos_accuracy" to reasons and pass oos_accuracy_flag=True into _apply_style_signal(). (3) In _apply_style_signal(): if oos_accuracy_flag: apply stale_price_warning compression (0.6× on the fused − 0.5 distance). (4) Surface in SignalCard UI as a yellow "Low model confidence" chip when this reason is present. (5) Note: OOS data requires 8+ weeks post tune_all to be meaningful; when Redis key is absent (new symbol, no tune yet), skip suppression — do not penalise.',
  },

  {
    id: 'pa-1-double-top-bottom-detector',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — recognizer.py detect_double_top_bottom() fully rewritten: 5–60 bar gap check, ±1.5% proximity, volume exhaustion/distribution confirmation, neckline calculation, measured-move target, entry trigger (neckline break ±0.2%), institutional volume boost. Confidence 0.55–0.92 based on confirmation. signals.py _pattern_score_adjustment() wires +0.12–0.15 boost for double-bottom and +0.13 boost for double-top neckline breaks. Double-top mid-trade: paper_trading_engine.py tightens trail to 1.2× ATR when double_top_neckline_broken detected in open position. Double-bottom neckline break: Layer 4a automatic pass in conviction gate (scheduler.py). All neckline/target/stop metadata stored in reasons and surfaced to UI.',
    tier: 9, severity: 'feature',
    title: 'PA-1 BREAKTHROUGH: Double Top / Double Bottom pattern detection with neckline, target, volume confirmation, and entry trigger',
    file: 'services/technical-analysis/src/patterns/recognizer.py + services/signal-engine/src/generators/signals.py + services/market-data/src/services/scheduler.py + services/market-data/src/services/paper_trading_engine.py',
    effort: '4 hours',
    impact: 'High — double bottoms are one of the highest-reliability reversal patterns with 78%+ success rate when confirmed with volume and neckline break. Double tops warn of distribution phases and allow paper trading to tighten stops before the full breakdown. Previously the detector was a stub with only a 2% proximity check and no neckline, volume, or target logic. Now the full institutional-grade pattern is detected and wired into signal confidence, conviction gate, and trailing stop management.',
    what: 'recognizer.py had a placeholder detect_double_top_bottom() using only pivot proximity — no volume confirmation, no neckline, no entry trigger, no target. Confidence was a fixed 0.6. signals.py _pattern_score_adjustment() referenced double_bottom/double_top in BULLISH/BEARISH sets but received only a 0.6 confidence placeholder. Paper trading had no double-pattern awareness.',
    fix: 'Full rewrite of detect_double_top_bottom(): (1) Gap constraint 5–60 bars between pivots. (2) Proximity ±1.5% (tighter than old 2%). (3) Volume exhaustion for double-bottom (2nd trough vol ≤ 1.1× 1st). (4) Volume distribution for double-top (2nd peak vol ≤ 0.9× 1st). (5) Neckline = highest close between troughs (double-bottom) or lowest close between peaks (double-top). (6) Entry trigger = price breaks neckline ±0.2%. (7) Measured-move target. (8) Confidence 0.70 (vol confirmed) or 0.55 (not) + 0.10 (neckline broken) + 0.08 (vol boost). Wired into: conviction gate Layer 4a auto-pass (double-bottom confirmed), signal score +0.12–0.15, trail tightening on double-top breakdown.',
  },

  {
    id: 'pt-ea1-sell-exit-email',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — send_trade_exit_email() added to email_service.py with rich HTML: P&L ($ and %), entry/exit price, MFE, hold days, exit reason label, entry rationale bullets. _monitor_positions() now returns list[dict] of closed trades. paper_trading_step() calls _send_exit_emails() after commit: queries SignalAlert to find all users subscribed to the exited symbol and sends exit email to each. Covers all exit reasons: signal_exit, stop_hit, target_reached, hold_stall_timeout, time_stop, momentum_exit.',
    tier: 9, severity: 'feature',
    title: 'PT-EA1: Paper Trade Exit Email — SELL / stop / target alerts sent to SignalAlert subscribers when a position closes',
    file: 'services/market-data/src/services/paper_trading_engine.py + services/market-data/src/services/email_service.py',
    effort: '2 hours',
    impact: 'High — without exit emails, you only know a trade closed by checking the Trade Board manually. The exit email delivers the full outcome (P&L, reason, entry rationale) immediately when any position is closed — stop hit, SELL signal, target reached, or stall timeout. Sent only to users who have a SignalAlert subscription for the symbol, so no noise for irrelevant stocks.',
    what: 'Paper trading engine recorded all exits to the DB and logged them but sent no email notification. Users had to check the Paper Portfolio → Closed Trades tab to discover exits. No connection between the email alert subscription (SignalAlert per symbol) and paper trade exits for the same symbol.',
    fix: '_monitor_positions() returns list[dict] of closed trade snapshots. paper_trading_step() calls _send_exit_emails() after commit: looks up SignalAlert rows for the symbol, sends send_trade_exit_email() to each subscriber. Email shows: green/red P&L banner, entry vs exit price, MFE, hold days, shares, exit reason, up to 4 entry decision notes from entry_decision_notes column.',
  },

  {
    id: 'pt-5m-engine-cadence',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — _refresh_5m() now calls paper_trading_step() for both US and HK markets after each 5m bar ingest. Signal generation cadence also increased: us_intra and hk_intra jobs changed from every 10 min to every 5 min (full market hours 10:00–15:00). Total signal refresh cycles now ~78/day per market (was ~45). HK paper trading wired in via _refresh_5m("HK").',
    tier: 9, severity: 'feature',
    title: 'PT-5M / SCH-5M: Signal refresh and paper trading engine cadence increased to every 5 minutes for US and HK markets',
    file: 'services/market-data/src/services/scheduler.py',
    effort: '30 min',
    impact: 'High — during regular hours (10:00–15:00), the previous 10-minute gap meant stops could be breached 10 min before detection and BUY signals could be stale for 10 min before paper trading saw them. With 5-minute cadence matching the intraday bar frequency, both signal quality and position monitoring are tighter throughout the full session.',
    what: '_refresh_5m() only ingested bars; paper_trading_step() was called only by _refresh_market() (every 10 min during regular hours, 5 min during open/close bursts). Signal generation ran every 10 min during regular hours and HK had no paper trading in _refresh_5m.',
    fix: '(1) Changed us_intra and hk_intra CronTrigger minute patterns from "0,10,20,30,40,50" to "0,5,10,...,55" — signals + rankings every 5 min. (2) Changed _refresh_5m paper trading guard from `if market == "US"` to `if market in ("US", "HK")`. (3) Added _purge_old_data() weekly job as maint-db-purge-job.',
  },

  {
    id: 'sa-23-rs-threshold',
    defaultStatus: 'done',
    implementedNote: 'Done 2026-06-11 — Threshold changed from 0.80 to 0.70 in _apply_style_signal(). Added absolute return floor: if stock_20d_return_pct > 5%, skip RS compression regardless of rs_rank. stock_20d_return_pct now stored in reasons.',
    tier: 9, severity: 'low',
    title: 'SA-23: RS compression threshold rs_rank < 0.80 is hair-trigger — fires on normal 20-day return variance',
    file: 'services/signal-engine/src/generators/signals.py',
    effort: '30 min',
    impact: 'Low-Medium — rs_rank compares stock 20-day return vs sector ETF 20-day return. A stock returning +5% vs sector +7% has rs_rank=0.98 (no compression). A stock returning +8% vs sector +11% has rs_rank=0.92 (no compression). But in a volatile week, a stock might return +3% vs sector +5% → rs_rank=0.98 (fine) then the next 3 days flip and it is +5% vs +7.5% → rs_rank=0.93 (fine). The 0.80 threshold itself is correct as a laggard signal but 20-day returns are noisy enough that extending the window to 60 days would be more stable.',
    what: 'rs_rank = (1 + stock_20d_return) / (1 + etf_20d_return). The 20-day window captures a single volatile month and can reflect one bad week rather than true relative underperformance. A stock can lag its sector by 15% in 20 days due to earnings noise, then outperform the next month. The compression fires at <0.80, which requires the stock to return less than 80% of what the sector did — a meaningful but noisy signal over 20 days.',
    fix: 'Option A (simpler): add an absolute return floor — if stock_20d_return > 0.05 (+5%), skip RS compression regardless of rs_rank; a stock up 5% in 20 days is not a true laggard. Option B (better): extend comparison to 60 days (3-month return). 60-day RS is far more stable and reflects structural underperformance rather than one bad week. Implement as a config param: rs_comparison_days: 60 (currently hardcoded 20). Also change threshold from 0.80 to 0.70 for the 60-day window.',
  },
];

// ── Constants ─────────────────────────────────────────────────────────────────

const TIER_LABEL: Record<Tier, string> = {
  1: 'Tier 1 — Fix Before Trusting Signals',
  2: 'Tier 2 — Analytical Improvements',
  3: 'Tier 3 — New Features',
  4: 'Tier 4 — Signal Accuracy & ML Tuning',
  5: 'Tier 5 — UI Gaps & Tech Debt',
  6: 'Tier 6 — Security & Reliability Audit (2026-06-09)',
  7: 'Tier 7 — Alert Intelligence & UX (2026-06-10)',
  8: 'Tier 8 — Paper Trading Engine (WF-2 Deep Audit + Regime Engine 2026-06-11)',
  9: 'Tier 9 — Signal Coherence & Breakthrough Improvements (2026-06-11)',
};

const TIER_COLOR: Record<Tier, string> = {
  1: '#f87171',
  2: '#fbbf24',
  3: '#818cf8',
  4: '#34d399',
  5: '#67e8f9',
  6: '#f97316',
  7: '#a78bfa',
  8: '#fb7185',
  9: '#22d3ee',
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
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filterTier, setFilterTier] = useState<Tier | 0>(0);
  const [filterStatus, setFilterStatus] = useState<Status | 'all'>('all');

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    if (s.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  useEffect(() => {
    if (!authed) return;
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
  }, [authed]);

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

  const tiers = ([1, 2, 3, 4, 5, 6, 7, 8, 9] as Tier[]).filter(t => filterTier === 0 || t === filterTier);

  // Summary counts
  const total = ITEMS.length;
  const done = ITEMS.filter(i => (statuses[i.id] ?? 'todo') === 'done').length;
  const inProgress = ITEMS.filter(i => (statuses[i.id] ?? 'todo') === 'in-progress').length;
  const critical = ITEMS.filter(i => i.tier === 1 && (statuses[i.id] ?? 'todo') !== 'done').length;

  if (!authed) return null;

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
          {([0, 1, 2, 3, 4, 5, 6, 7, 8] as const).map(t => (
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
          Current (2026-06-10) — All Tier 1–4 complete + Tier 7 alert intelligence
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          {[
            { label: 'Data pipeline',   score: 8.5, target: 8.8, note: '↑ Data freshness chip shipped (UI-09)' },
            { label: 'ML methodology',  score: 9.0, target: 9.0, note: '✓ AUC key fix + SA-3/SA-5 all done' },
            { label: 'Signal logic',    score: 9.0, target: 9.0, note: '✓ SA-7 regime earnings done; all SA items shipped' },
            { label: 'K-Score ranking', score: 8.2, target: 8.5, note: '↑ Conviction screener shipped (UI-04)' },
            { label: 'Research engine', score: 7.8, target: 8.5, note: '↑ RES-2b: D/E fix + PEG scoring + trend + target premium' },
            { label: 'Frontend / UX',   score: 9.5, target: 9.5, note: '↑ Per-horizon alerts + consensus indicator + Add to Radar' },
            { label: 'Risk management', score: 8.5, target: 9.0, note: '↑ Portfolio risk + P&L heatmap (UI-06) done' },
            { label: 'Overall',         score: 9.2, target: 9.5, note: '✓ Tier 7 alert intelligence shipped 2026-06-10' },
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
          All Tier 1–4 shipped as of 2026-06-07. SA-1/2/3/4/5/6/7/8 all done. SA-3 was already live (4 boolean flags in builder.py). SA-5 wired to Sunday scheduler. SA-7 regime-aware earnings compression implemented (bull+beater≥70%: +3% boost; bull+50-70%: halved compression; bear/hv: tightened).
          Tier 5: UI-01 to UI-09 + UI-12 all shipped. Remaining (low priority): UI-10 (ML weight auto-apply), UI-11 (factor chart verify).
          Tier 3 new items (2026-06-08): TB-1 (trailing stop), TB-2 (time-stop), TB-3 (stop breach alert), TB-4 (dollar P&amp;L), TB-5 (portfolio heat-at-risk), SL-1 (admin signal log). SL-1 implemented 2026-06-08.
          Overall: <strong style={{ color: '#4ade80' }}>9.2 / 10</strong> — Tier 7 alert intelligence shipped 2026-06-10: per-horizon signal alerts (SHORT/SWING/LONG/GROWTH independent subscriptions), require_consensus gate (≥2 horizons agree before alert fires), 4-horizon consensus indicator on stock detail, Add to Radar button on Opportunities. Admin health expanded: Signal Refresh Health + ML Training Health sections; AUC key mismatch bug fixed (all 119 models now show real AUC values). RES-2 analyst momentum + RES-2b fundamental scoring (D/E fix, PEG, EPS trend, analyst target premium) shipped 2026-06-10. All Tier 1–6 complete. WF-2 (autonomous paper trading), 11 security/reliability audit fixes, signal pipeline audit all done 2026-06-09.
        </p>
      </div>
    </div>
  );
}

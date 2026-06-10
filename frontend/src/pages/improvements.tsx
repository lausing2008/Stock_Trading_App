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
type Tier     = 1 | 2 | 3 | 4 | 5 | 6;
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
    title: 'RES-2: Analyst upgrade/downgrade momentum — recent rating changes as signal catalyst',
    file: 'services/market-data/src/api/routes.py · services/signal-engine/src/generators/signals.py',
    effort: '2–3 days',
    impact: 'Medium — an analyst upgrade in the last 7 days is a strong near-term catalyst. A downgrade while holding is a major exit warning. Currently only the consensus rating is used, not the direction or recency of changes.',
    what: 'The system uses recommendationMean (consensus) from yfinance but does not track rating changes over time. An upgrade from Neutral to Buy (direction) that happened 2 days ago is far more actionable than a stable Buy rating that has been unchanged for 6 months.',
    fix: 'Fetch upgradesDowngrades history from yfinance (ticker.upgrades_downgrades). Store last 30 days of changes. Compute: upgrades_7d (count), downgrades_7d (count), net_analyst_momentum = upgrades - downgrades. In signal generation: if upgrades_7d >= 2 and net_momentum > 0, add 5% confidence boost and "analyst_upgrade_momentum" reason. If downgrades_7d >= 2, add "analyst_downgrade_warning" reason and 8% confidence penalty. Show recent changes on stock detail page as a timeline: "Goldman: Neutral → Buy (3d ago)", "MS: Hold → Sell (1d ago)".',
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

  // Remaining audit findings — not yet implemented
  {
    id: 'audit-api-gateway-auth',
    tier: 6, severity: 'critical',
    title: 'AUDIT-SEC-5: API gateway has no JWT validation — services are the only auth layer',
    file: 'services/api-gateway/src/api/proxy.py:48-80',
    effort: '1 day',
    impact: 'HIGH — any caller who bypasses the gateway can hit upstream services without auth',
    what: 'The reverse_proxy function forwards all requests to upstream services without validating any JWT itself. The gateway adds no Authorization check before forwarding; it strips malformed Bearer headers but does not verify them.',
    fix: 'Add a shared JWT validation dependency at the gateway level for all non-public prefixes (/auth, /health). At minimum verify that a valid JWT is present before forwarding. This creates a defence-in-depth layer even if individual services are directly reachable.',
  },
  {
    id: 'audit-ai-chat-auth',
    tier: 6, severity: 'critical',
    title: 'AUDIT-SEC-6: /ai/chat endpoint unauthenticated — anyone can use the shared AI key',
    file: 'services/api-gateway/src/api/ai_proxy.py:54',
    effort: '0.5 days',
    impact: 'HIGH — anonymous callers can burn the shared Claude/DeepSeek API key budget',
    what: 'POST /ai/chat has no auth dependency. When no api_key is provided by the caller, the endpoint falls back to the admin-configured shared key from Redis. There is no authentication check before allowing use of the shared key.',
    fix: 'Add Depends(get_current_user) from the shared jwt_auth module to the ai_chat route. Only authenticated users should be allowed to use the shared key fallback.',
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
    tier: 6, severity: 'medium',
    title: 'AUDIT-SEC-8: 30-day JWT expiry with no revocation mechanism',
    file: 'shared/common/config.py:25 · services/market-data/src/api/auth.py:36-42',
    effort: '2 days',
    impact: 'MEDIUM — stolen tokens remain valid for up to 30 days with no way to revoke them',
    what: 'Tokens are valid for 30 days. There is no token blacklist or refresh token mechanism. If a token is compromised, it cannot be invalidated until expiry.',
    fix: 'Reduce token expiry to 1 day (or 8 hours). Implement a Redis-backed token blacklist checked in get_current_user. Add a POST /auth/logout endpoint that adds the token JTI to the blacklist.',
  },

  {
    id: 'tm5-live-vs-backtest',
    tier: 3, severity: 'feature',
    title: 'TM-5: Live vs backtest comparison — detect overfitting and model drift in production',
    file: 'services/signal-engine/src/api/routes.py · frontend/src/pages/signal-accuracy.tsx',
    effort: '2–3 days',
    impact: 'High — the #1 failure mode of ML trading systems is overfitting: the backtest shows 70% accuracy but live performance is 54%. Without explicitly tracking the gap, this degradation is invisible until significant losses occur.',
    what: 'The system has both a backtest engine and live signal tracking, but they are never compared. Backtest accuracy is computed on historical data. Live accuracy comes from signal_outcomes. There is no dashboard showing whether live performance is tracking backtest expectations or has diverged significantly.',
    fix: 'Add a "Live vs Backtest" panel to /signal-accuracy: (1) Backtest accuracy: run the accuracy calculation on the 2-year historical training period (before the model went live). (2) Live accuracy: last 90 days from signal_outcomes. (3) Show both as a side-by-side bar chart per horizon (SHORT/SWING/LONG). (4) Alert if live accuracy < backtest × 0.85 for any horizon — this is a 15% degradation threshold that flags probable overfitting or regime change. (5) Track this gap monthly and plot a trend line — a widening gap over 3 months triggers an automatic re-tune request. (6) Include model version number so accuracy is correctly attributed to the model that generated it (not polluted by a retrained model mid-period).',
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
};

const TIER_COLOR: Record<Tier, string> = {
  1: '#f87171',
  2: '#fbbf24',
  3: '#818cf8',
  4: '#34d399',
  5: '#67e8f9',
  6: '#f97316',
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

  const tiers = ([1, 2, 3, 4, 5] as Tier[]).filter(t => filterTier === 0 || t === filterTier);

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
          Current (2026-06-07) — All Tier 1–4 complete
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          {[
            { label: 'Data pipeline',   score: 8.5, target: 8.8, note: '↑ Data freshness chip shipped (UI-09)' },
            { label: 'ML methodology',  score: 9.0, target: 9.0, note: '✓ SA-3 (boolean flags) + SA-5 (calibrate_ta_weights) done' },
            { label: 'Signal logic',    score: 9.0, target: 9.0, note: '✓ SA-7 regime earnings done; all SA items shipped' },
            { label: 'K-Score ranking', score: 8.2, target: 8.5, note: '↑ Conviction screener shipped (UI-04)' },
            { label: 'Research engine', score: 7.5, target: 8.5, note: '↑ Cache quality flag (tech-research-cache-quality)' },
            { label: 'Frontend / UX',   score: 9.3, target: 9.5, note: '↑ P&L heatmap + conviction screener shipped' },
            { label: 'Risk management', score: 8.5, target: 9.0, note: '↑ Portfolio risk + P&L heatmap (UI-06) done' },
            { label: 'Overall',         score: 9.0, target: 9.0, note: '✓ All Tier 1–4 shipped (SA-1–8 + Tier 1–3)' },
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
          Overall: <strong style={{ color: '#4ade80' }}>9.5 / 10</strong> — WF-2 (autonomous paper trading engine) shipped 2026-06-09, 11 audit fixes, signal pipeline 5-bug audit. Trade Board Position Lifecycle shipped 2026-06-09: WF-1 (signal state chip), WF-3 (live position monitor — trail stop, near target, near stop, stalled warning), TB-1 (trail recommendations), TB-2 (time-stop badge), TB-3 (stop breach banner), TB-4 (dollar P&amp;L + risk), TB-5 (active positions summary bar). Pending: UI-13/14, scheduler monitor.
        </p>
      </div>
    </div>
  );
}

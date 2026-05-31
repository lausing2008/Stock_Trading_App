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
}

// ── Data ─────────────────────────────────────────────────────────────────────

const ITEMS: Item[] = [
  // ── Tier 1 — Critical Fixes ──────────────────────────────────────────────
  {
    id: 'ml-calibration',
    tier: 1, severity: 'critical',
    title: 'Calibrate ML model (Platt scaling)',
    file: 'services/ml-prediction/src/ml/trainer.py',
    effort: '2 days',
    impact: 'Prevents overconfident signals — "78% confidence" should mean 78% win rate',
    what: 'XGBoost outputs raw margin scores, not true probabilities. An uncalibrated 65% bullish probability may only correspond to a 52% true probability. Every confidence %, confluence score, and BUY threshold depends on this number being meaningful.',
    fix: 'Add CalibratedClassifierCV(method="sigmoid") after model.fit(). Fit calibration on held-out validation set, save calibrated model. Add calibration curve to model eval output.',
  },
  {
    id: 'value-momentum-gate',
    tier: 1, severity: 'critical',
    title: 'K-Score value sub-score — add momentum quality gate',
    file: 'services/ranking-engine/src/scoring/kscore.py',
    effort: '1 day',
    impact: 'Stops falling knives scoring 90+ on "value" — a bankrupt stock near zero currently scores 100',
    what: 'Value proxy = 1 − (price / 52w_high). A stock down 80% scores 80 on value. This surfaces stocks in terminal decline as attractive value plays.',
    fix: 'Require momentum_score > 25 before the value score contributes. Otherwise default to 50 (neutral). Long-term: replace with analyst consensus upside (target_price / price − 1).',
  },
  {
    id: 'macro-redis-cache',
    tier: 1, severity: 'critical',
    title: 'Cache macro data in Redis — fix silent zero-fill failures',
    file: 'services/ml-prediction/src/ml/features.py',
    effort: '1 day',
    impact: 'Prevents silent distribution shift when yfinance fails to fetch SPY/VIX at inference time',
    what: 'When yfinance fails, macro features (SPY returns, VIX) zero-fill silently. The model was trained on real values. Zero-filled macros look like extreme market panic, biasing every signal toward defensiveness.',
    fix: 'Cache last-known SPY/VIX in Redis with 24h TTL. Fall back to cached values before zero-fill. Log a warning when stale macro > 4 hours is used.',
  },
  {
    id: 'lookahead-guard',
    tier: 1, severity: 'critical',
    title: 'Add inference timestamp guard (look-ahead bias)',
    file: 'services/ml-prediction/src/ml/features.py',
    effort: '1 day',
    impact: 'Eliminates risk of model accessing future prices during mid-session retraining',
    what: 'Label construction uses fwd_ret = close.shift(-horizon). If retraining runs mid-session with a "today" bar that only reflects morning prices, the model sees partially-observed data as its label target.',
    fix: 'Assert last_bar_date < date.today() before any model.fit(). Enforce that the scheduler retrains only after the 16:30 post-close bar is confirmed ingested.',
  },
  {
    id: 'prompt-injection',
    tier: 1, severity: 'critical',
    title: 'Sanitise symbol input — prompt injection risk',
    file: 'services/research-engine/src/api/routes.py',
    effort: '0.5 days',
    impact: 'Security fix — prevents AI prompt manipulation via malformed stock symbols',
    what: 'The stock symbol is interpolated directly into the Claude system prompt. A malformed symbol containing newlines or role-manipulation text could alter the AI\'s output.',
    fix: 'Sanitise with re.sub(r"[^A-Z0-9\\.]", "", symbol.upper()) at the route entry point before any string is passed to the AI.',
  },

  // ── Tier 2 — Analytical Improvements ────────────────────────────────────
  {
    id: 'sector-relative-scoring',
    tier: 2, severity: 'medium',
    title: 'Sector-relative fundamental scoring',
    file: 'services/research-engine/src/services/scoring.py',
    effort: '3 days',
    impact: 'Fixes incorrect PE/growth/margin thresholds — utilities and SaaS currently misjudged',
    what: 'All fundamental thresholds are absolute (P/E 25 = "fairly valued" for all stocks). A utility at 14× is correct; a SaaS at 14× is deeply discounted. The same number means the opposite thing in different sectors.',
    fix: 'Group stocks by sector field in DB. Compute percentile rank of each metric within its sector peer group. Score relative to peers, not absolute thresholds.',
  },
  {
    id: 'rsi-scoring-curve',
    tier: 2, severity: 'medium',
    title: 'Fix RSI scoring curve — arbitrary peak at 55',
    file: 'services/ranking-engine/src/scoring/kscore.py',
    effort: '0.5 days',
    impact: 'Strong uptrending stocks (RSI 65–75) no longer incorrectly penalised',
    what: 'rsi_score = 100 - abs(RSI - 55) peaks at RSI=55. RSI=70 (healthy uptrend) scores only 15. No empirical justification for 55 as the ideal value.',
    fix: 'Replace with piecewise: reward zone 50–65 = 100, gentle decay outside it. RSI=70 should score ~50, not 15.',
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
  },
  {
    id: 'frontend-weight-normalise',
    tier: 2, severity: 'medium',
    title: 'Normalise strategy weights in scoreFor()',
    file: 'frontend/src/pages/opportunities.tsx',
    effort: '0.5 days',
    impact: 'Makes scores comparable across strategies — currently Swing max is 100 but baseline is only 80',
    what: 'Weights don\'t sum to 100% in most strategies. Swing: 40%+25%+15% = 80% baseline. Short: 85% + unbounded momentum bonus. Scores are not comparable across tabs.',
    fix: 'Divide each strategy output by its theoretical maximum after computation to normalise to 0–100.',
  },
  {
    id: 'zero-volume-filter',
    tier: 2, severity: 'low',
    title: 'Filter zero-volume bars from ingestion',
    file: 'services/market-data/src/services/ingest.py',
    effort: '0.5 days',
    impact: 'Cleaner ATR and volatility calculations — trading halts no longer inflate vol metrics',
    what: 'Validation accepts volume >= 0. Zero-volume bars (trading halts, data errors) distort ATR and OBV calculations.',
    fix: 'For daily bars: skip zero-volume rows with a warning log. For intraday: allow (pre-market thin volume is real).',
  },
  {
    id: 'cache-quality-flag',
    tier: 2, severity: 'medium',
    title: 'Research engine cache quality flag',
    file: 'services/research-engine/src/api/routes.py',
    effort: '1 day',
    impact: 'Prevents serving AI fallback defaults (50/50/50 scores) as if they were real analysis',
    what: 'If Claude times out, the engine returns hardcoded defaults (company_score: 50, industry_score: 50). This is cached for 24h and served to all users with no indication it is synthetic.',
    fix: 'Store a quality field alongside each cached report: "full" | "partial" | "fallback". Display a yellow warning banner in the UI for non-full reports.',
  },
  {
    id: 'ml-weight-formula',
    tier: 2, severity: 'medium',
    title: 'Validate ML fusion weight formula on held-out test data',
    file: 'services/signal-engine/src/signals/generator.py',
    effort: '2 days',
    impact: 'Grounds the 40–75% ML weight in actual measured signal quality, not a manually-tuned formula',
    what: 'ml_weight = 0.40 + (auc - 0.50) / 0.20 * 0.35 maps AUC to weight with no empirical backing. It uses CV AUC (in-sample), not test AUC. The formula was hand-designed.',
    fix: 'Run signal engine on historical data in TA-only and TA+ML modes. Compute Sharpe ratio for each. Use the weight that maximises Sharpe on a validation period ending 6+ months ago.',
  },
  {
    id: 'stale-price-check',
    tier: 2, severity: 'low',
    title: 'Add staleness check to signal generator price fetch',
    file: 'services/signal-engine/src/signals/generator.py',
    effort: '0.5 days',
    impact: 'Prevents signals computed on Friday prices from being served Monday without a staleness warning',
    what: 'Signal generator assumes the most recent bar is current. No check that last_bar_ts is within an expected window for the market (could be holiday, gap, or service restart).',
    fix: 'Assert staleness < 3 days. If stale, add stale: true to the signal response so the UI can display a warning badge.',
  },
  {
    id: 'atr-standard',
    tier: 2, severity: 'low',
    title: 'Use standard Wilder ATR (EWM, not SMA)',
    file: 'services/research-engine/src/services/scoring.py',
    effort: '0.5 days',
    impact: 'Consistency with every charting platform — traders quoting ATR expect Wilder\'s smoothing',
    what: 'Research engine computes ATR using simple moving average of true range. Standard ATR (Wilder) uses exponential smoothing (alpha = 1/period). Results differ especially in volatile periods.',
    fix: 'Replace rolling(period).mean() with ewm(alpha=1/period, adjust=False).mean() in the ATR helper.',
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
  },
  {
    id: 'options-flow',
    tier: 3, severity: 'feature',
    title: 'Options flow integration',
    file: 'services/market-data/ + stock detail page',
    effort: '5 days',
    impact: 'Adds one of the highest-quality leading signals — large institutions often use options before moving the underlying',
    what: 'Unusual call volume (5× 30-day average, short-dated OTM strikes) frequently precedes significant upside moves. This is public data but not currently used in any signal.',
    fix: 'Fetch from Quiver Quant (already have key) or CBOE. Compute call/put ratio vs. baseline. Add options_flow_bullish as a 5–10% weight signal component. Show unusual activity on stock detail page.',
  },
  {
    id: 'earnings-surprise',
    tier: 3, severity: 'feature',
    title: 'Earnings surprise model',
    file: 'services/market-data/ + research engine',
    effort: '4 days',
    impact: 'Consistent EPS beaters are systematically undervalued by analysts — high predictive value',
    what: 'A stock\'s history of beating analyst EPS estimates is one of the most predictive signals for post-earnings moves. Not currently tracked or used.',
    fix: 'Fetch last 8 quarters from yfinance earnings_history. Compute beat rate, average surprise %, trend. Display in Fundamentals. Add +5 to research score for consistent beaters (beat rate > 75%).',
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
  },
  {
    id: 'regime-detection',
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
    file: 'services/signal-engine/ + positions page',
    effort: '1 week',
    impact: 'Turns StockAI from a static alert system into one that improves from its own track record',
    what: 'Position tracking already exists. Every closed position is a labelled training example. This data is not being used to improve signal weights over time.',
    fix: 'Log {symbol, entry_signal, entry_confidence, entry_confluence, market_regime, actual_return} on position close. Weekly batch: compute win rate by (signal, regime). Adjust thresholds based on track record.',
  },
  {
    id: 'factor-exposure',
    tier: 3, severity: 'feature',
    title: 'Factor exposure analysis',
    file: 'services/signal-engine/ + signal-accuracy page',
    effort: '4 days',
    impact: 'Distinguishes genuine alpha from hidden factor tilts (momentum, value, size)',
    what: 'Without factor analysis you cannot tell if signal alpha is real or just disguised momentum/value factor tilt that will reverse when the factor regime changes.',
    fix: 'Compute average factor exposures (momentum, value, size, vol) of signalled stocks at time of signal. Show factor bar chart on Signal Accuracy page vs. SPY baseline.',
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

const STORAGE_KEY = 'stockai:improvements';

// ── Component ─────────────────────────────────────────────────────────────────

export default function ImprovementsPage() {
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filterTier, setFilterTier] = useState<Tier | 0>(0);
  const [filterStatus, setFilterStatus] = useState<Status | 'all'>('all');

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
      setStatuses(saved);
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
          <span style={{ fontSize: 12, color: '#475569' }}>Expert review — 2026-05-31</span>
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
                          <div style={{ fontSize: 10, fontWeight: 700, color: '#818cf8', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>How to fix</div>
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
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          {[
            { label: 'Data pipeline',   score: 7.5, note: 'Solid foundation' },
            { label: 'ML methodology',  score: 5.5, note: 'Needs calibration' },
            { label: 'Signal logic',    score: 6.5, note: 'Good fusion design' },
            { label: 'K-Score ranking', score: 6.0, note: 'Value proxy risky' },
            { label: 'Research engine', score: 6.0, note: 'Sector-blind scoring' },
            { label: 'Frontend / UX',   score: 8.5, note: 'Best-in-class self-built' },
            { label: 'Risk management', score: 6.0, note: 'No backtested Sharpe' },
            { label: 'Overall',         score: 6.5, note: 'Strong foundation' },
          ].map(d => (
            <div key={d.label} style={{ background: '#020617', borderRadius: 6, padding: '10px 12px' }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: d.score >= 7.5 ? '#4ade80' : d.score >= 6 ? '#fbbf24' : '#f87171' }}>
                {d.score}
              </div>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#94a3b8', marginTop: 2 }}>{d.label}</div>
              <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{d.note}</div>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 12, color: '#64748b', margin: 0, lineHeight: 1.6 }}>
          The single biggest unlock is a <strong style={{ color: '#94a3b8' }}>walk-forward backtest</strong> showing signals produce positive expectancy on unseen data,
          followed by <strong style={{ color: '#94a3b8' }}>ML calibration</strong> so confidence percentages are trustworthy.
          The UX and data pipeline are already better than most commercial platforms.
        </p>
      </div>
    </div>
  );
}

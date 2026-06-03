/**
 * Opportunities page — strategy-filtered stock screener with AI outlook.
 *
 * AI provider: whichever is configured in Settings → AI Assistant
 *              (Claude or DeepSeek). Uses temperature=0.2 (default).
 *
 * Static filtering (no AI)
 * ────────────────────────
 * Six strategy tabs filter the ranked stock universe by K-Score sub-components:
 *   Top Picks   — highest composite K-Score
 *   Swing       — BUY/HOLD signal + strong technical score
 *   Short-Term  — high momentum + volume expansion
 *   Long-Term   — undervalued fundamentals near fair value
 *   Growth      — top growth + momentum sub-scores
 *   AI Signal   — only active BUY signals, ranked by signal confidence
 *
 * AI Outlook (optional, triggered by "Generate AI Outlook" button)
 * ────────────────────────────────────────────────────────────────
 * Builds a user message with every visible stock's symbol, price, K-Score,
 * AI signal, confidence, bullish probability, sector, and news headlines.
 * System prompt: hedge fund quant analyst producing a 2–5 day directional
 * outlook (BULLISH / BEARISH / NEUTRAL) per stock with catalysts and risk.
 * Parsed as JSON OutlookItem[] array (max_tokens=8192).
 */
'use client';
import { useState, useMemo, useEffect } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type RankingRow, type LatestPrice, type SignalSummary, type WatchlistItem, type Overview } from '@/lib/api';
import { confluenceScore, confluenceGrade } from '@/lib/confluence';
import { askAI, isAiConfigured } from '@/lib/ai';

type Strategy = 'all' | 'swing' | 'short' | 'longterm' | 'growth' | 'aisignal' | 'confluence';
type Market = 'all' | 'US' | 'HK';
type OutlookDirection = 'BULLISH' | 'BEARISH' | 'NEUTRAL';

interface OutlookItem {
  symbol: string;
  direction: OutlookDirection;
  horizon: string;
  confidence: 'high' | 'medium' | 'low';
  reason: string;
  catalysts: string[];
  key_risk?: string;
}

const STRATEGIES: { key: Strategy; label: string; icon: string; tagline: string; desc: string }[] = [
  { key: 'all',      label: 'Top Picks',  icon: '⭐', tagline: 'Best overall K-Score',       desc: 'Highest composite score across technical, momentum, value, growth, and volatility.' },
  { key: 'swing',    label: 'Swing',      icon: '📊', tagline: '5–30 day hold',              desc: 'Strong AI signal + technical setup. Best for defined entry/exit around a catalyst or pattern.' },
  { key: 'short',    label: 'Short-Term', icon: '⚡', tagline: '1–5 day move',               desc: 'High recent momentum and volume expansion. Best for capitalising on short breakouts or pullbacks.' },
  { key: 'longterm', label: 'Long-Term',  icon: '🏛️', tagline: '6–24 month horizon',         desc: 'Undervalued fundamentals with strong growth trajectory. Buy and hold at or below fair value.' },
  { key: 'growth',   label: 'Growth',     icon: '🚀', tagline: 'High growth momentum',       desc: 'Top growth + momentum scores. Companies growing revenue/earnings faster than the market.' },
  { key: 'aisignal',   label: 'AI Signal',   icon: '🤖', tagline: 'BUY-signal stocks only',        desc: 'Only stocks where the AI engine has issued an active BUY signal, ranked by signal confidence and bullish probability.' },
  { key: 'confluence', label: 'Confluence',  icon: '🎯', tagline: 'All signals aligned',           desc: 'Stocks where AI Signal, K-Score, Technical, and Momentum all point in the same direction. Highest-conviction setups only.' },
];

const SIG_COLOR: Record<string, { color: string; bg: string; border: string }> = {
  BUY:  { color: '#4ade80', bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.35)'  },
  HOLD: { color: '#facc15', bg: 'rgba(250,204,21,0.12)', border: 'rgba(250,204,21,0.35)' },
  WAIT: { color: '#fb923c', bg: 'rgba(251,146,60,0.12)', border: 'rgba(251,146,60,0.35)' },
  SELL: { color: '#f87171', bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)'  },
};

const OUTLOOK_STYLE: Record<OutlookDirection, { color: string; bg: string; border: string; glow: string; icon: string }> = {
  BULLISH: { color: '#4ade80', bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.35)',  glow: 'rgba(34,197,94,0.08)',  icon: '▲' },
  BEARISH: { color: '#f87171', bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)',  glow: 'rgba(239,68,68,0.06)',  icon: '▼' },
  NEUTRAL: { color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', border: 'rgba(148,163,184,0.2)', glow: 'rgba(148,163,184,0.04)', icon: '●' },
};

const CONF_COLOR: Record<string, string> = {
  high:   '#818cf8',
  medium: '#fb923c',
  low:    '#64748b',
};

function scoreColor(n: number) {
  return n >= 70 ? '#4ade80' : n >= 50 ? '#facc15' : '#f87171';
}

function scoreFor(
  strategy: Strategy,
  r: RankingRow,
  sig?: SignalSummary,
  lp?: LatestPrice,
): number {
  const tech = r.technical ?? 0;
  const mom  = r.momentum  ?? 0;
  const val  = r.value     ?? 0;
  const grow = r.growth    ?? 0;
  const vlt  = r.volatility ?? 0;
  const sigB = sig?.signal === 'BUY' ? 20 : sig?.signal === 'HOLD' ? 10 : sig?.signal === 'WAIT' ? 4 : 0;
  const conf = sig?.confidence ?? 0;
  const chg  = lp?.change_pct ?? 0;
  const upside = r.fair_price && lp?.price ? ((r.fair_price - lp.price) / lp.price) * 100 : 0;

  switch (strategy) {
    // all: K-Score already 0-100; signal bonus capped so max ≈ 108 → display as-is
    case 'all':      return (r.score ?? 0) + (sig?.signal === 'BUY' ? 8 : sig?.signal === 'HOLD' ? 3 : 0);
    // swing max: 40+25+20+15 = 100 (sigB max 20, conf max 100)
    case 'swing':    return Math.min(100, Math.round(tech * 0.40 + mom * 0.25 + sigB + conf * 0.15));
    // short: cap day-change bonus at 15 (≡ 5% move) so max = 50+25+15+10 = 100
    case 'short':    return Math.min(100, Math.round(mom * 0.50 + tech * 0.25 + Math.min(Math.abs(chg) * 3, 15) + vlt * 0.10));
    // longterm: cap upside bonus at 25 pts so max ≈ 40+30+25+15 = 110 → clamped to 100
    case 'longterm': return Math.min(100, Math.round(val * 0.40 + grow * 0.30 + Math.min(Math.max(0, upside), 25) + vlt * 0.15));
    // growth max: 50+30+20 = 100
    case 'growth':   return Math.min(100, Math.round(grow * 0.50 + mom * 0.30 + tech * 0.20));
    // aisignal: conf 0-100 × 0.70 = 70 max; bullish_probability 0-1 × 50 = 50 → clamp to 100
    case 'aisignal':   return Math.min(100, Math.round(conf * 0.70 + (sig?.bullish_probability ?? 0) * 50 + tech * 0.15 + mom * 0.10));
    case 'confluence': return confluenceScore(r, sig);
    default:           return r.score ?? 0;
  }
}

function getReasons(
  strategy: Strategy,
  r: RankingRow,
  sig?: SignalSummary,
  lp?: LatestPrice,
): { text: string; positive: boolean }[] {
  const out: { text: string; positive: boolean }[] = [];

  if (sig?.signal === 'BUY')
    out.push({ text: `AI signal BUY — ${sig.confidence.toFixed(0)}% confidence`, positive: true });
  if (sig?.signal === 'HOLD' && (sig.confidence ?? 0) > 30)
    out.push({ text: `AI signal HOLD — holding zone with ${sig.confidence.toFixed(0)}% confidence`, positive: true });

  if (r.fair_price && lp?.price) {
    const upside = ((r.fair_price - lp.price) / lp.price) * 100;
    if (upside > 5)
      out.push({ text: `${upside.toFixed(1)}% upside to fair value $${r.fair_price.toFixed(2)}`, positive: true });
    else if (upside < -5)
      out.push({ text: `${Math.abs(upside).toFixed(1)}% above fair value — watch valuation`, positive: false });
  }

  if ((r.technical ?? 0) >= 70) out.push({ text: `Strong bullish technical setup (${r.technical?.toFixed(0)}/100)`, positive: true });
  if ((r.momentum  ?? 0) >= 70) out.push({ text: `Strong price momentum (${r.momentum?.toFixed(0)}/100)`, positive: true });
  if ((r.value     ?? 0) >= 70) out.push({ text: `Attractive valuation vs peers (${r.value?.toFixed(0)}/100)`, positive: true });
  if ((r.growth    ?? 0) >= 70) out.push({ text: `High revenue/earnings growth (${r.growth?.toFixed(0)}/100)`, positive: true });
  if ((r.volatility ?? 0) >= 70) out.push({ text: `Low volatility — stable risk profile`, positive: true });

  if ((lp?.change_pct ?? 0) > 2) out.push({ text: `Up ${lp!.change_pct!.toFixed(2)}% today — momentum building`, positive: true });
  if ((lp?.change_pct ?? 0) < -2) out.push({ text: `Down ${Math.abs(lp!.change_pct!).toFixed(2)}% today — potential dip entry`, positive: false });

  return out.slice(0, 3);
}

function getKeyMetric(
  strategy: Strategy,
  r: RankingRow,
  sig?: SignalSummary,
  lp?: LatestPrice,
): { label: string; value: string; color?: string } | null {
  switch (strategy) {
    case 'swing':
      return { label: 'Technical', value: `${(r.technical ?? 0).toFixed(0)}/100`, color: scoreColor(r.technical ?? 0) };
    case 'short':
      return lp?.change_pct != null
        ? { label: 'Today', value: `${lp.change_pct >= 0 ? '+' : ''}${lp.change_pct.toFixed(2)}%`, color: lp.change_pct >= 0 ? '#4ade80' : '#f87171' }
        : null;
    case 'longterm':
      if (r.fair_price && lp?.price) {
        const up = ((r.fair_price - lp.price) / lp.price * 100).toFixed(1);
        return { label: 'Upside', value: `${Number(up) >= 0 ? '+' : ''}${up}%`, color: Number(up) >= 0 ? '#4ade80' : '#f87171' };
      }
      return { label: 'Value', value: `${(r.value ?? 0).toFixed(0)}/100`, color: scoreColor(r.value ?? 0) };
    case 'growth':
      return { label: 'Growth', value: `${(r.growth ?? 0).toFixed(0)}/100`, color: scoreColor(r.growth ?? 0) };
    case 'aisignal':
      return sig
        ? { label: 'AI Confidence', value: `${sig.confidence.toFixed(0)}%`, color: scoreColor(sig.confidence) }
        : null;
    case 'confluence': {
      const cs = confluenceScore(r, sig);
      const g = confluenceGrade(cs);
      return { label: 'Confluence', value: `${cs} · ${g.label}`, color: g.color };
    }
    default:
      return { label: 'K-Score', value: (r.score ?? 0).toFixed(0), color: scoreColor(r.score ?? 0) };
  }
}

interface AlertSuggestion {
  label: string;
  sublabel?: string;
  condition: string;
  threshold: number;
  note: string;
}

function analyzeIndicators(
  overview: Overview | null,
  strategy: Strategy,
  r: RankingRow,
  lp?: LatestPrice,
): AlertSuggestion[] {
  const price = lp?.price;
  if (!price) return [];

  const out: AlertSuggestion[] = [];

  if (overview?.indicators?.values) {
    const vals = overview.indicators.values;

    const last = (k: string): number | null => {
      const arr = vals[k];
      if (!arr) return null;
      for (let i = arr.length - 1; i >= 0; i--) {
        if (arr[i] != null) return arr[i]!;
      }
      return null;
    };
    const prevVal = (k: string): number | null => {
      const arr = vals[k];
      if (!arr) return null;
      let found = 0;
      for (let i = arr.length - 1; i >= 0; i--) {
        if (arr[i] != null) {
          if (found === 1) return arr[i]!;
          found++;
        }
      }
      return null;
    };

    const rsi    = last('rsi_14');
    const sma20  = last('sma_20');
    const sma50  = last('sma_50');
    const sma200 = last('sma_200');
    const bbUp   = last('bb_upper');
    const bbLo   = last('bb_lower');
    const bbMid  = last('bb_mid');
    const hist     = last('hist');
    const prevHist = prevVal('hist');

    // ── RSI ───────────────────────────────────────────────────
    if (rsi !== null) {
      if (rsi >= 74) {
        const stopAt = sma20 ? parseFloat(sma20.toFixed(2)) : parseFloat((price * 0.92).toFixed(2));
        out.push({
          label: `RSI ${rsi.toFixed(0)} — heavily overbought`,
          sublabel: `Stop at SMA20 $${stopAt}`,
          condition: 'below', threshold: stopAt,
          note: 'RSI overbought stop',
        });
      } else if (rsi >= 65) {
        out.push({
          label: `RSI ${rsi.toFixed(0)} — extended, trailing stop`,
          sublabel: `Stop −7% at $${(price * 0.93).toFixed(2)}`,
          condition: 'below', threshold: parseFloat((price * 0.93).toFixed(2)),
          note: 'RSI extended stop',
        });
      } else if (rsi <= 25) {
        const target = sma20 ? parseFloat(sma20.toFixed(2)) : parseFloat((price * 1.08).toFixed(2));
        out.push({
          label: `RSI ${rsi.toFixed(0)} — severely oversold`,
          sublabel: `Alert when price reclaims SMA20 $${target}`,
          condition: 'above', threshold: target,
          note: 'Oversold bounce trigger',
        });
      } else if (rsi <= 38) {
        out.push({
          label: `RSI ${rsi.toFixed(0)} — oversold, watch EMA20 reclaim`,
          sublabel: 'Alert on EMA20 crossover',
          condition: 'cross_above_ema', threshold: 20,
          note: 'Oversold recovery entry',
        });
      }
    }

    // ── Bollinger Bands ───────────────────────────────────────
    if (bbUp && bbLo && bbMid && bbUp > bbLo) {
      const pos = (price - bbLo) / (bbUp - bbLo);
      if (pos >= 0.90) {
        out.push({
          label: `At upper Bollinger Band ($${bbUp.toFixed(2)})`,
          sublabel: `Mean reversion target BB mid $${bbMid.toFixed(2)}`,
          condition: 'below', threshold: parseFloat(bbMid.toFixed(2)),
          note: 'BB upper reversal',
        });
      } else if (pos <= 0.10) {
        out.push({
          label: `At lower Bollinger Band ($${bbLo.toFixed(2)})`,
          sublabel: `Bounce target BB mid $${bbMid.toFixed(2)}`,
          condition: 'above', threshold: parseFloat(bbMid.toFixed(2)),
          note: 'BB lower bounce',
        });
      }
    }

    // ── MACD histogram crossover ──────────────────────────────
    if (hist !== null && prevHist !== null) {
      if (prevHist < 0 && hist >= 0) {
        out.push({
          label: 'MACD just turned bullish',
          sublabel: 'Alert on EMA20 crossover to confirm momentum',
          condition: 'cross_above_ema', threshold: 20,
          note: 'MACD bullish crossover entry',
        });
      } else if (prevHist > 0 && hist <= 0) {
        const stopAt = sma20 ? parseFloat(sma20.toFixed(2)) : parseFloat((price * 0.95).toFixed(2));
        out.push({
          label: 'MACD just turned bearish',
          sublabel: `Exit signal — stop at $${stopAt}`,
          condition: 'below', threshold: stopAt,
          note: 'MACD bearish crossover exit',
        });
      }
    }

    // ── SMA200 test ───────────────────────────────────────────
    if (sma200 && Math.abs(price - sma200) / sma200 < 0.04) {
      out.push({
        label: `Testing SMA200 ($${sma200.toFixed(2)}) — critical trend line`,
        sublabel: price >= sma200 ? 'Alert if it breaks below' : 'Alert if it reclaims above',
        condition: price >= sma200 ? 'below' : 'above',
        threshold: parseFloat(sma200.toFixed(2)),
        note: 'SMA200 level break',
      });
    }

    // ── SMA50/200 gap → Golden / Death Cross ──────────────────
    if (sma50 && sma200 && out.length < 3) {
      const gap = Math.abs(sma50 - sma200) / sma200 * 100;
      if (gap < 2.5) {
        out.push({
          label: sma50 < sma200
            ? `Golden Cross imminent — SMA50/200 gap ${gap.toFixed(1)}%`
            : `Death Cross risk — SMA50/200 gap ${gap.toFixed(1)}%`,
          sublabel: sma50 < sma200 ? 'Bullish trend change signal' : 'Bearish trend change signal',
          condition: sma50 < sma200 ? 'golden_cross' : 'death_cross',
          threshold: 0,
          note: sma50 < sma200 ? 'Golden Cross alert' : 'Death Cross alert',
        });
      }
    }
  }

  // ── Nearby S/R levels ─────────────────────────────────────────
  if (overview?.levels?.support_resistance && out.length < 4) {
    const nearby = [...overview.levels.support_resistance]
      .filter(lvl => {
        const dist = Math.abs(lvl.price - price) / price;
        return dist > 0.005 && dist < 0.07;
      })
      .sort((a, b) => b.strength - a.strength)
      .slice(0, 2);
    for (const lvl of nearby) {
      out.push({
        label: `${lvl.kind === 'resistance' ? 'Resistance' : 'Support'} at $${lvl.price.toFixed(2)}`,
        sublabel: `Strength ${lvl.strength.toFixed(0)} — ${lvl.price > price ? 'breakout target' : 'break-down watch'}`,
        condition: lvl.price > price ? 'above' : 'below',
        threshold: parseFloat(lvl.price.toFixed(2)),
        note: `${lvl.kind} level`,
      });
    }
  }

  // ── Fair value target ─────────────────────────────────────────
  if (r.fair_price && r.fair_price > price * 1.03 && out.length < 4) {
    out.push({
      label: `Fair value target $${r.fair_price.toFixed(2)}`,
      sublabel: `+${(((r.fair_price - price) / price) * 100).toFixed(1)}% upside`,
      condition: 'above',
      threshold: parseFloat(r.fair_price.toFixed(2)),
      note: 'Fair value target',
    });
  }

  // ── Fallback stop loss if none generated ──────────────────────
  if (!out.some(s => s.condition === 'below')) {
    const stopPct = 0.08;
    out.push({
      label: `Stop loss −${(stopPct * 100).toFixed(0)}%`,
      sublabel: `$${(price * (1 - stopPct)).toFixed(2)}`,
      condition: 'below',
      threshold: parseFloat((price * (1 - stopPct)).toFixed(2)),
      note: 'Stop loss',
    });
  }

  return out.slice(0, 4);
}

const STRATEGY_FILTER: Record<Strategy, (r: RankingRow, sig?: SignalSummary) => boolean> = {
  all:        () => true,
  swing:      (r, sig) => (sig?.signal === 'BUY' || sig?.signal === 'HOLD') && (r.technical ?? 0) >= 45,
  short:      (r) => (r.momentum ?? 0) >= 40,
  longterm:   (r) => (r.value ?? 0) >= 40 || (r.growth ?? 0) >= 50,
  growth:     (r) => (r.growth ?? 0) >= 50,
  aisignal:   (_r, sig) => sig?.signal === 'BUY',
  confluence: (r, sig) => confluenceScore(r, sig) >= 65,
};

export default function Opportunities() {
  const [strategy, setStrategy] = useState<Strategy>('all');
  const [market, setMarket] = useState<Market>('all');

  // Alert suggestion panel state
  const [alertPanel, setAlertPanel] = useState<string | null>(null);
  const [alertEmail, setAlertEmail] = useState('');
  const [alertSaving, setAlertSaving] = useState<string | null>(null);
  const [alertDone, setAlertDone] = useState<Set<string>>(new Set());
  const [overviewCache, setOverviewCache] = useState<Record<string, Overview>>({});
  const [loadingPanel, setLoadingPanel] = useState<string | null>(null);

  useEffect(() => {
    const saved = typeof window !== 'undefined' ? localStorage.getItem('stockai_alert_email') : null;
    if (saved) setAlertEmail(saved);
  }, []);

  async function openAlertPanel(symbol: string) {
    if (alertPanel === symbol) { setAlertPanel(null); return; }
    setAlertPanel(symbol);
    if (!overviewCache[symbol]) {
      setLoadingPanel(symbol);
      try {
        const ov = await api.overview(symbol);
        setOverviewCache(prev => ({ ...prev, [symbol]: ov }));
      } catch { /* non-fatal — will fall back to basic suggestions */ }
      setLoadingPanel(null);
    }
  }

  async function handleSetAlert(symbol: string, suggestion: AlertSuggestion) {
    if (!alertEmail) return;
    const key = `${symbol}:${suggestion.condition}:${suggestion.threshold}`;
    setAlertSaving(key);
    try {
      await api.createAlert({ symbol, condition: suggestion.condition, threshold: suggestion.threshold, email: alertEmail, note: suggestion.note });
      localStorage.setItem('stockai_alert_email', alertEmail);
      setAlertDone(prev => new Set(prev).add(key));
    } catch { /* non-fatal */ }
    setAlertSaving(null);
  }

  // Near-term outlook state
  const [outlook, setOutlook] = useState<OutlookItem[] | null>(null);
  const [outlookLoading, setOutlookLoading] = useState(false);
  const [outlookError, setOutlookError] = useState<string | null>(null);
  const [outlookStatus, setOutlookStatus] = useState('');
  const [outlookCollapsed, setOutlookCollapsed] = useState(false);

  const { data: rankData, isLoading } = useSWR('rankings-all', () => api.rankings());
  const { data: pricesData } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalsData } = useSWR('signals-' + getSignalStyle(), () => api.allSignals(getSignalStyle()));
  const { data: watchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());

  const watchedSet = useMemo(() => new Set(watchlist?.map(w => w.symbol) ?? []), [watchlist]);

  const priceMap = useMemo(() => {
    const m: Record<string, LatestPrice> = {};
    for (const p of pricesData ?? []) m[p.symbol] = p;
    return m;
  }, [pricesData]);

  const signalMap = useMemo(() => {
    const m: Record<string, SignalSummary> = {};
    for (const s of signalsData ?? []) m[s.symbol] = s;
    return m;
  }, [signalsData]);

  const opportunities = useMemo(() => {
    const all = rankData?.rankings ?? [];
    const filter = STRATEGY_FILTER[strategy];
    return all
      .filter(r => watchedSet.has(r.symbol))
      .filter(r => market === 'all' || r.market === market)
      .filter(r => (r.score ?? 0) > 0)
      .filter(r => filter(r, signalMap[r.symbol]))
      .map(r => ({
        row: r,
        lp: priceMap[r.symbol],
        sig: signalMap[r.symbol],
        stratScore: scoreFor(strategy, r, signalMap[r.symbol], priceMap[r.symbol]),
      }))
      .sort((a, b) => b.stratScore - a.stratScore)
      .slice(0, 20);
  }, [rankData, priceMap, signalMap, strategy, market, watchedSet]);

  async function generateOutlook() {
    if (!isAiConfigured()) {
      setOutlookError('No AI provider configured. Go to Settings → AI Assistant to add your API key.');
      return;
    }
    const top20 = opportunities.slice(0, 20);
    if (top20.length === 0) {
      setOutlookError('No stocks to analyse. Add stocks to your watchlist first.');
      return;
    }

    setOutlookLoading(true);
    setOutlookError(null);
    setOutlook(null);
    setOutlookCollapsed(false);

    try {
      setOutlookStatus(`Fetching news for top ${top20.length} opportunities…`);
      const newsResults = await Promise.allSettled(
        top20.map(o => api.getNews(o.row.symbol, 'yfinance,google').catch(() => [] as { title: string; sentiment_label: string }[]))
      );

      setOutlookStatus('Analysing signals, trends, and news with AI…');

      const stockContexts = top20.map((o, i) => {
        const { row: r, sig, lp } = o;
        const newsArr = newsResults[i].status === 'fulfilled'
          ? (newsResults[i] as PromiseFulfilledResult<{ title: string; sentiment_label: string }[]>).value
          : [];

        const headlines = newsArr
          .slice(0, 5)
          .map((n) => `  - [${n.sentiment_label}] ${n.title}`)
          .join('\n') || '  (no recent news)';

        const fairUpside = r.fair_price != null && lp?.price != null && lp.price > 0
          ? (((r.fair_price - lp.price) / lp.price) * 100).toFixed(1)
          : null;

        return `Symbol: ${r.symbol}
Name: ${r.name}${r.name_zh ? ` (${r.name_zh})` : ''}
Sector: ${r.sector ?? 'Unknown'} | Market: ${r.market}
Current Price: ${lp?.price != null ? lp.price.toFixed(2) : 'N/A'} | Today: ${lp?.change_pct != null ? `${lp.change_pct >= 0 ? '+' : ''}${lp.change_pct.toFixed(2)}%` : 'N/A'}
AI Signal: ${sig?.signal ?? 'N/A'} | Horizon: ${sig?.horizon ?? 'N/A'} | Confidence: ${sig?.confidence?.toFixed(0) ?? 0}% | Bullish Probability: ${sig?.bullish_probability != null ? `${sig.bullish_probability.toFixed(0)}%` : 'N/A'}
K-Score: ${(r.score ?? 0).toFixed(0)} | Technical: ${(r.technical ?? 0).toFixed(0)} | Momentum: ${(r.momentum ?? 0).toFixed(0)} | Value: ${(r.value ?? 0).toFixed(0)} | Growth: ${(r.growth ?? 0).toFixed(0)} | Volatility: ${(r.volatility ?? 0).toFixed(0)}
Fair Value Upside: ${fairUpside != null ? `${Number(fairUpside) >= 0 ? '+' : ''}${fairUpside}%` : 'N/A'}
Recent News Headlines (5 most recent):
${headlines}`;
      }) as string[];

      const systemPrompt = `You are a senior quantitative analyst at a hedge fund. Your task is to produce near-term (2–5 day) price direction predictions for a watchlist of stocks. You have access to proprietary AI signals, multi-factor K-Scores, and live news sentiment.

SCORING FRAMEWORK — use this to interpret inputs:
- K-Score (0–100): composite rank; ≥70 is strong, ≤30 is weak
- Technical sub-score: reflects RSI, EMA trend, breakout patterns
- Momentum sub-score: recent price velocity and volume confirmation
- Value sub-score: P/E, P/B, earnings yield vs peers
- Growth sub-score: revenue/earnings growth trajectory
- Volatility sub-score: LOWER = more stable (≥70 = low vol, ≤30 = high vol)
- AI Signal: BUY/HOLD/WAIT/SELL; Bullish Probability ≥65% is meaningful confirmation
- Fair Value Upside: model-estimated margin to intrinsic value; >10% is attractive, negative = overvalued

ANALYTICAL RULES:
1. BUY signal + Bullish Probability ≥65% + positive news = BULLISH with higher confidence
2. BUY signal + weak momentum (<40) = cap confidence at "medium" — momentum hasn't confirmed
3. SELL or WAIT signal + negative news = BEARISH regardless of high K-Score
4. Conflicting signals (e.g. BUY signal but bearish news + high volatility) → NEUTRAL, low confidence
5. Fair value upside >15% is a tailwind for bullish outlook; negative upside is a headwind
6. High volatility score (≤30) alone does not make a stock BEARISH — it widens the uncertainty band
7. Momentum sub-score ≥70 + positive news = strong near-term momentum catalyst
8. Horizon should match the AI signal horizon when available (SHORT = 1–3 days, SWING = 3–7 days, LONG = 1–4 weeks)

OUTPUT RULES:
- Each "reason" must cite at least one specific data point (score, %, signal, or headline keyword)
- Each catalyst bullet must be ≤10 words and actionable or observational (not generic)
- "key_risk" must name the single biggest threat to the predicted direction (e.g. earnings miss, sector rotation, overbought RSI, macro headwind)
- Never produce all BULLISH or all BEARISH — differentiate based on the data
- confidence = "high" only when signal, momentum, news, AND fair value all point the same way

Return ONLY a valid JSON array — no markdown fences, no prose outside the JSON. Each element must have exactly these fields:
{
  "symbol": "string",
  "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
  "horizon": "e.g. 2–3 days",
  "confidence": "high" | "medium" | "low",
  "reason": "1–2 sentences citing specific data points — primary near-term driver.",
  "catalysts": ["bullet 1 (≤10 words)", "bullet 2", "bullet 3"],
  "key_risk": "single biggest downside risk to this prediction (1 sentence)"
}`;

      const userMsg = `Predict near-term price direction for these ${stockContexts.length} stocks. Apply the analytical framework strictly and differentiate conviction levels based on data alignment:\n\n${stockContexts.join('\n\n---\n\n')}`;

      const raw = await askAI([{ role: 'user', content: userMsg }], systemPrompt, 8192);

      const jsonMatch = raw.match(/\[[\s\S]*\]/);
      if (!jsonMatch) throw new Error('AI response did not contain a JSON array. Try again.');

      let parsed: OutlookItem[];
      try {
        parsed = JSON.parse(jsonMatch[0]) as OutlookItem[];
      } catch {
        throw new Error('AI response was cut off or malformed. Try again (fewer stocks or a different AI model may help).');
      }

      const dirOrder: Record<OutlookDirection, number> = { BULLISH: 0, NEUTRAL: 1, BEARISH: 2 };
      const confOrder: Record<string, number> = { high: 0, medium: 1, low: 2 };
      parsed.sort((a, b) =>
        dirOrder[a.direction] - dirOrder[b.direction] ||
        confOrder[a.confidence] - confOrder[b.confidence]
      );

      setOutlook(parsed);
      setOutlookStatus('');
    } catch (e: unknown) {
      setOutlookError(e instanceof Error ? e.message : 'Failed to generate outlook.');
    } finally {
      setOutlookLoading(false);
      setOutlookStatus('');
    }
  }

  const active = STRATEGIES.find(s => s.key === strategy)!;

  const bullCount  = outlook?.filter(o => o.direction === 'BULLISH').length ?? 0;
  const bearCount  = outlook?.filter(o => o.direction === 'BEARISH').length ?? 0;
  const neutCount  = outlook?.filter(o => o.direction === 'NEUTRAL').length ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Page header */}
      <div>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#f1f5f9', marginBottom: '4px' }}>Opportunities</h1>
        <p style={{ fontSize: '13px', color: '#475569' }}>
          Top stocks ranked by strategy using K-Score sub-scores, AI signals, and live price data. Updated on every page load.
        </p>
      </div>

      {/* ── Near-Term AI Outlook section ─────────────────────────────── */}
      <div style={{
        borderRadius: '14px',
        border: '1px solid rgba(129,140,248,0.2)',
        background: 'linear-gradient(135deg, rgba(79,70,229,0.05) 0%, rgba(15,23,42,0.95) 100%)',
        overflow: 'hidden',
      }}>
        {/* Section header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 18px',
          borderBottom: outlook && !outlookCollapsed ? '1px solid rgba(129,140,248,0.12)' : 'none',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '32px', height: '32px', borderRadius: '8px',
              background: 'rgba(129,140,248,0.15)', border: '1px solid rgba(129,140,248,0.25)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px',
            }}>🔮</div>
            <div>
              <div style={{ fontSize: '14px', fontWeight: 700, color: '#c7d2fe' }}>Near-Term AI Outlook</div>
              <div style={{ fontSize: '11px', color: '#475569' }}>
                AI prediction for next 2–5 days based on news, signals, momentum &amp; business trends
              </div>
            </div>
            {outlook && (
              <div style={{ display: 'flex', gap: '6px', marginLeft: '8px' }}>
                {bullCount > 0 && (
                  <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px', background: 'rgba(34,197,94,0.12)', border: '1px solid rgba(34,197,94,0.3)', color: '#4ade80' }}>
                    ▲ {bullCount} bullish
                  </span>
                )}
                {bearCount > 0 && (
                  <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px', background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: '#f87171' }}>
                    ▼ {bearCount} bearish
                  </span>
                )}
                {neutCount > 0 && (
                  <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px', background: 'rgba(148,163,184,0.08)', border: '1px solid rgba(148,163,184,0.2)', color: '#94a3b8' }}>
                    ● {neutCount} neutral
                  </span>
                )}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {outlook && (
              <button
                onClick={() => setOutlookCollapsed(c => !c)}
                style={{
                  background: 'transparent', border: '1px solid #1e293b',
                  color: '#475569', padding: '4px 10px', borderRadius: '6px',
                  fontSize: '11px', cursor: 'pointer',
                }}
              >
                {outlookCollapsed ? 'Show' : 'Hide'}
              </button>
            )}
            <button
              onClick={generateOutlook}
              disabled={outlookLoading}
              style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '7px 14px', borderRadius: '8px', border: 'none',
                background: outlookLoading ? 'rgba(79,70,229,0.4)' : 'linear-gradient(135deg, #4f46e5, #6366f1)',
                color: '#fff', fontSize: '12px', fontWeight: 700,
                cursor: outlookLoading ? 'not-allowed' : 'pointer',
                boxShadow: outlookLoading ? 'none' : '0 4px 12px rgba(79,70,229,0.3)',
                transition: 'all 0.15s', whiteSpace: 'nowrap',
              }}
            >
              {outlookLoading ? (
                <>
                  <span style={{ display: 'inline-block', animation: 'spin 0.8s linear infinite' }}>↻</span>
                  Analysing…
                </>
              ) : outlook ? '↺ Refresh' : '✦ Generate Outlook'}
            </button>
          </div>
        </div>

        {/* Loading */}
        {outlookLoading && (
          <div style={{ padding: '32px 18px', textAlign: 'center' }}>
            <div style={{ fontSize: '24px', marginBottom: '10px', animation: 'pulse 1.5s ease-in-out infinite' }}>🔮</div>
            <div style={{ fontSize: '13px', color: '#818cf8', fontWeight: 600 }}>{outlookStatus || 'Generating outlook…'}</div>
            <div style={{ fontSize: '11px', color: '#334155', marginTop: '4px' }}>Fetching news + running AI analysis on all your watchlist stocks</div>
          </div>
        )}

        {/* Error */}
        {outlookError && !outlookLoading && (
          <div style={{ margin: '14px 18px', padding: '12px 14px', borderRadius: '8px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)', fontSize: '13px', color: '#f87171' }}>
            {outlookError}
          </div>
        )}

        {/* Empty prompt */}
        {!outlook && !outlookLoading && !outlookError && (
          <div style={{ padding: '28px 18px', textAlign: 'center', color: '#334155' }}>
            <div style={{ fontSize: '12px', color: '#475569', marginBottom: '6px' }}>
              Click <strong style={{ color: '#818cf8' }}>Generate Outlook</strong> to get AI-powered near-term predictions for every stock in your watchlist.
            </div>
            <div style={{ fontSize: '11px', color: '#334155' }}>
              Analyses recent news, business catalysts, AI signals, and price momentum to predict direction over the next 2–5 days.
            </div>
          </div>
        )}

        {/* Results grid */}
        {outlook && !outlookLoading && !outlookCollapsed && (
          <div style={{ padding: '14px 18px', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '10px' }}>
            {outlook.map(item => {
              const style = OUTLOOK_STYLE[item.direction];
              const r = rankData?.rankings.find(row => row.symbol === item.symbol);
              const sig = signalMap[item.symbol];
              const lp = priceMap[item.symbol];

              return (
                <Link key={item.symbol} href={`/stock/${item.symbol}`} style={{ textDecoration: 'none' }}>
                  <div
                    className="outlook-card"
                    style={{
                      borderRadius: '10px',
                      border: `1px solid ${style.border}`,
                      background: `linear-gradient(135deg, ${style.glow} 0%, #0f172a 100%)`,
                      padding: '14px',
                      transition: 'all 0.15s',
                      height: '100%',
                      boxSizing: 'border-box',
                    }}
                  >
                    {/* Top row: symbol + direction badge */}
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '8px' }}>
                      <div>
                        <div style={{ fontSize: '15px', fontWeight: 800, color: '#f1f5f9' }}>{item.symbol}</div>
                        <div style={{ fontSize: '10px', color: '#475569', marginTop: '1px' }}>
                          {r?.name_zh || r?.name || ''}
                        </div>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
                        <span style={{
                          fontSize: '11px', fontWeight: 800, padding: '3px 10px', borderRadius: '6px',
                          color: style.color, background: style.bg, border: `1px solid ${style.border}`,
                          letterSpacing: '0.04em',
                        }}>
                          {style.icon} {item.direction}
                        </span>
                        <span style={{
                          fontSize: '9px', fontWeight: 700,
                          color: CONF_COLOR[item.confidence],
                          textTransform: 'uppercase', letterSpacing: '0.05em',
                        }}>
                          {item.confidence} confidence
                        </span>
                      </div>
                    </div>

                    {/* Horizon + current signal */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '9px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px', background: 'rgba(129,140,248,0.12)', border: '1px solid rgba(129,140,248,0.2)', color: '#818cf8' }}>
                        ⏱ {item.horizon}
                      </span>
                      {sig && (
                        <span style={{ fontSize: '9px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px', color: SIG_COLOR[sig.signal]?.color, background: SIG_COLOR[sig.signal]?.bg, border: `1px solid ${SIG_COLOR[sig.signal]?.border}` }}>
                          {sig.signal} {sig.confidence.toFixed(0)}%
                        </span>
                      )}
                      {lp?.change_pct != null && (
                        <span style={{ fontSize: '9px', fontWeight: 700, color: lp.change_pct >= 0 ? '#4ade80' : '#f87171' }}>
                          {lp.change_pct >= 0 ? '▲' : '▼'} {Math.abs(lp.change_pct).toFixed(2)}%
                        </span>
                      )}
                    </div>

                    {/* AI reason */}
                    <p style={{ fontSize: '11px', color: '#94a3b8', lineHeight: 1.5, margin: '0 0 8px', fontStyle: 'italic' }}>
                      "{item.reason}"
                    </p>

                    {/* Catalyst bullets */}
                    {item.catalysts?.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                        {item.catalysts.map((c, i) => (
                          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '5px' }}>
                            <span style={{ fontSize: '8px', color: style.color, flexShrink: 0, marginTop: '2px' }}>{style.icon}</span>
                            <span style={{ fontSize: '10px', color: '#64748b', lineHeight: 1.4 }}>{c}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Key risk */}
                    {item.key_risk && (
                      <div style={{ marginTop: '8px', display: 'flex', alignItems: 'flex-start', gap: '5px', padding: '5px 8px', borderRadius: '6px', background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.15)' }}>
                        <span style={{ fontSize: '9px', color: '#fbbf24', flexShrink: 0, marginTop: '1px' }}>⚠</span>
                        <span style={{ fontSize: '10px', color: '#78716c', lineHeight: 1.4 }}>{item.key_risk}</span>
                      </div>
                    )}

                    {/* K-score footer */}
                    {r && (
                      <div style={{ marginTop: '10px', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.04)', display: 'flex', gap: '8px', alignItems: 'center' }}>
                        {[
                          { l: 'T', v: r.technical, t: 'Technical' },
                          { l: 'M', v: r.momentum,  t: 'Momentum'  },
                          { l: 'V', v: r.value,     t: 'Value'     },
                          { l: 'G', v: r.growth,    t: 'Growth'    },
                        ].map(({ l, v, t }) => v != null ? (
                          <div key={l} title={`${t}: ${v.toFixed(0)}`} style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
                            <span style={{ fontSize: '8px', color: '#334155', fontWeight: 700 }}>{l}</span>
                            <div style={{ width: '24px', height: '3px', borderRadius: '2px', background: '#1e293b', overflow: 'hidden' }}>
                              <div style={{ height: '100%', width: `${v}%`, background: scoreColor(v), borderRadius: '2px' }} />
                            </div>
                          </div>
                        ) : null)}
                        <span style={{ fontSize: '9px', color: '#334155', marginLeft: 'auto' }}>K {(r.score ?? 0).toFixed(0)}</span>
                      </div>
                    )}
                  </div>
                </Link>
              );
            })}
          </div>
        )}

        {/* Disclaimer */}
        {outlook && !outlookLoading && !outlookCollapsed && (
          <div style={{ padding: '8px 18px 12px', fontSize: '10px', color: '#1e293b', textAlign: 'center' }}>
            AI predictions are for informational purposes only and do not constitute financial advice. Always do your own research.
          </div>
        )}
      </div>
      {/* ── End Near-Term Outlook ────────────────────────────────────── */}

      {/* Strategy selector */}
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        {STRATEGIES.map(s => (
          <button
            key={s.key}
            onClick={() => { setStrategy(s.key); if (typeof window !== 'undefined') localStorage.setItem('stockai_opp_strategy', s.key); }}
            style={{
              display: 'flex', alignItems: 'center', gap: '7px',
              padding: '10px 16px', borderRadius: '10px', cursor: 'pointer',
              border: strategy === s.key ? '1px solid #4f46e5' : '1px solid #1e293b',
              background: strategy === s.key ? 'rgba(79,70,229,0.15)' : 'rgba(255,255,255,0.02)',
              color: strategy === s.key ? '#a5b4fc' : '#64748b',
              transition: 'all 0.15s',
              fontWeight: strategy === s.key ? 700 : 400,
            }}
          >
            <span style={{ fontSize: '16px' }}>{s.icon}</span>
            <div style={{ textAlign: 'left' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: strategy === s.key ? '#c7d2fe' : '#94a3b8' }}>{s.label}</div>
              <div style={{ fontSize: '10px', color: strategy === s.key ? '#818cf8' : '#334155' }}>{s.tagline}</div>
            </div>
          </button>
        ))}
      </div>

      {/* Strategy description + market filter row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
        <div style={{ borderRadius: '8px', border: '1px solid rgba(79,70,229,0.2)', background: 'rgba(79,70,229,0.06)', padding: '10px 14px', maxWidth: '600px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '2px' }}>
            <span style={{ fontSize: '15px' }}>{active.icon}</span>
            <span style={{ fontSize: '13px', fontWeight: 700, color: '#a5b4fc' }}>{active.label}</span>
          </div>
          <p style={{ fontSize: '12px', color: '#64748b', margin: 0 }}>{active.desc}</p>
        </div>

        {/* Market filter */}
        <div style={{ display: 'flex', borderRadius: '8px', border: '1px solid #1e293b', overflow: 'hidden', fontSize: '12px', fontWeight: 600, alignSelf: 'center' }}>
          {(['all', 'US', 'HK'] as Market[]).map(m => (
            <button
              key={m}
              onClick={() => setMarket(m)}
              style={{
                padding: '8px 16px', border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                background: market === m ? '#4f46e5' : 'transparent',
                color: market === m ? '#fff' : '#64748b',
              }}
            >
              {m === 'all' ? 'All Markets' : m}
            </button>
          ))}
        </div>
      </div>

      {/* Results count */}
      {!isLoading && (
        <div style={{ fontSize: '11px', color: '#334155' }}>
          {opportunities.length} {opportunities.length === 1 ? 'stock' : 'stocks'} matching {active.label} criteria{market !== 'all' ? ` in ${market}` : ''}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
          Computing opportunities…
        </div>
      )}

      {/* Opportunity cards */}
      {!isLoading && opportunities.length === 0 && (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#334155' }}>
          <div style={{ fontSize: '32px', marginBottom: '12px' }}>🔍</div>
          <div style={{ fontSize: '14px', color: '#475569' }}>No stocks match this strategy right now.</div>
          <div style={{ fontSize: '12px', color: '#334155', marginTop: '4px' }}>Try a different strategy or market, or ingest/train more data.</div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {opportunities.map(({ row: r, lp, sig }, idx) => {
          const sc = SIG_COLOR[sig?.signal ?? ''] ?? SIG_COLOR.HOLD;
          const reasons = getReasons(strategy, r, sig, lp);
          const keyMetric = getKeyMetric(strategy, r, sig, lp);
          const changeUp = (lp?.change_pct ?? 0) >= 0;
          const panelOpen = alertPanel === r.symbol;
          const panelLoading = loadingPanel === r.symbol;
          const suggestions = analyzeIndicators(overviewCache[r.symbol] ?? null, strategy, r, lp);

          return (
            <div key={r.symbol}>
              <Link href={`/stock/${r.symbol}`} style={{ textDecoration: 'none', display: 'block' }}>
                <div
                  className="opp-card"
                  style={{
                    borderRadius: panelOpen ? '12px 12px 0 0' : '12px',
                    border: '1px solid #1e293b',
                    borderBottom: panelOpen ? '1px solid rgba(99,102,241,0.3)' : '1px solid #1e293b',
                    background: '#0f172a', padding: '14px 18px',
                    display: 'grid',
                    gridTemplateColumns: '36px 1fr auto',
                    gap: '14px', alignItems: 'center',
                    transition: 'all 0.15s',
                  }}
                >
                  {/* Rank badge */}
                  <div style={{
                    width: '36px', height: '36px', borderRadius: '50%', flexShrink: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: idx === 0 ? 'rgba(250,204,21,0.15)' : idx === 1 ? 'rgba(148,163,184,0.1)' : idx === 2 ? 'rgba(251,146,60,0.1)' : 'rgba(255,255,255,0.03)',
                    border: idx === 0 ? '1px solid rgba(250,204,21,0.3)' : idx === 1 ? '1px solid rgba(148,163,184,0.2)' : idx === 2 ? '1px solid rgba(251,146,60,0.2)' : '1px solid #1e293b',
                    fontSize: '13px', fontWeight: 800,
                    color: idx === 0 ? '#facc15' : idx === 1 ? '#94a3b8' : idx === 2 ? '#fb923c' : '#334155',
                  }}>
                    {idx + 1}
                  </div>

                  {/* Main info */}
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '16px', fontWeight: 800, color: '#f1f5f9' }}>{r.symbol}</span>
                      <span style={{ fontSize: '11px', color: '#475569' }}>{r.name}</span>
                      <span style={{ fontSize: '9px', fontWeight: 700, padding: '1px 6px', borderRadius: '4px', background: r.market === 'US' ? 'rgba(59,130,246,0.15)' : 'rgba(236,72,153,0.15)', color: r.market === 'US' ? '#60a5fa' : '#f472b6', border: r.market === 'US' ? '1px solid rgba(59,130,246,0.25)' : '1px solid rgba(236,72,153,0.25)', letterSpacing: '0.05em' }}>
                        {r.market}
                      </span>
                      {r.sector && <span style={{ fontSize: '9px', color: '#334155' }}>{r.sector}</span>}
                      {sig && (
                        <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '4px', color: sc.color, background: sc.bg, border: `1px solid ${sc.border}` }}>
                          {sig.signal}
                        </span>
                      )}
                    </div>

                    {/* Reason bullets */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                      {reasons.map((rr, i) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                          <span style={{ fontSize: '9px', color: rr.positive ? '#4ade80' : '#fb923c', flexShrink: 0 }}>{rr.positive ? '▲' : '▼'}</span>
                          <span style={{ fontSize: '11px', color: '#64748b' }}>{rr.text}</span>
                        </div>
                      ))}
                      {reasons.length === 0 && (
                        <span style={{ fontSize: '11px', color: '#334155' }}>K-Score: {(r.score ?? 0).toFixed(0)} — view stock detail for full analysis</span>
                      )}
                    </div>

                    {/* Sub-score bar */}
                    <div style={{ display: 'flex', gap: '6px', marginTop: '8px', alignItems: 'center' }}>
                      {[
                        { label: 'T', val: r.technical, title: 'Technical' },
                        { label: 'M', val: r.momentum,  title: 'Momentum'  },
                        { label: 'V', val: r.value,     title: 'Value'     },
                        { label: 'G', val: r.growth,    title: 'Growth'    },
                      ].map(({ label, val, title }) => val != null ? (
                        <div key={label} title={`${title}: ${val.toFixed(0)}`} style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
                          <span style={{ fontSize: '9px', color: '#334155', fontWeight: 700 }}>{label}</span>
                          <div style={{ width: '32px', height: '4px', borderRadius: '2px', background: '#1e293b', overflow: 'hidden' }}>
                            <div style={{ height: '100%', width: `${val}%`, background: scoreColor(val), borderRadius: '2px' }} />
                          </div>
                        </div>
                      ) : null)}
                    </div>
                  </div>

                  {/* Right panel: price + key metric + bell */}
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    {lp ? (
                      <>
                        <div style={{ fontSize: '16px', fontWeight: 800, color: '#f1f5f9' }}>
                          ${lp.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </div>
                        {lp.change_pct != null && (
                          <div style={{ fontSize: '11px', fontWeight: 600, color: changeUp ? '#4ade80' : '#f87171' }}>
                            {changeUp ? '▲' : '▼'} {Math.abs(lp.change_pct).toFixed(2)}%
                          </div>
                        )}
                      </>
                    ) : (
                      <div style={{ fontSize: '13px', color: '#334155' }}>—</div>
                    )}
                    {keyMetric && (
                      <div style={{ marginTop: '6px', padding: '4px 10px', borderRadius: '6px', background: 'rgba(255,255,255,0.03)', border: '1px solid #1e293b', display: 'inline-block' }}>
                        <div style={{ fontSize: '9px', color: '#334155', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{keyMetric.label}</div>
                        <div style={{ fontSize: '14px', fontWeight: 800, color: keyMetric.color ?? '#e2e8f0' }}>{keyMetric.value}</div>
                      </div>
                    )}
                    <div style={{ fontSize: '10px', color: '#334155', marginTop: '4px' }}>
                      K {(r.score ?? 0).toFixed(0)}
                    </div>
                    {/* Bell button */}
                    <button
                      onClick={e => { e.preventDefault(); e.stopPropagation(); openAlertPanel(r.symbol); }}
                      title="Suggest alerts"
                      style={{
                        marginTop: '8px', display: 'block', marginLeft: 'auto',
                        background: panelOpen ? 'rgba(99,102,241,0.2)' : 'transparent',
                        border: `1px solid ${panelOpen ? 'rgba(99,102,241,0.5)' : '#1e293b'}`,
                        color: panelOpen ? '#818cf8' : '#334155',
                        borderRadius: '6px', padding: '4px 8px', fontSize: '13px',
                        cursor: 'pointer', transition: 'all 0.15s',
                      }}
                    >
                      🔔
                    </button>
                  </div>
                </div>
              </Link>

              {/* Alert suggestion panel */}
              {panelOpen && (
                <div style={{
                  background: 'rgba(15,23,42,0.98)', border: '1px solid rgba(99,102,241,0.3)',
                  borderTop: 'none', borderRadius: '0 0 12px 12px',
                  padding: '12px 18px', display: 'flex', flexDirection: 'column', gap: '8px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                      {panelLoading ? `Analysing ${r.symbol} indicators…` : `Suggested alerts — ${r.symbol}`}
                    </div>
                    {overviewCache[r.symbol] && (
                      <span style={{ fontSize: '10px', color: '#334155' }}>Based on RSI · MACD · BB · S/R levels</span>
                    )}
                  </div>

                  {/* Loading */}
                  {panelLoading && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '12px 0', color: '#475569', fontSize: '12px' }}>
                      <span style={{ display: 'inline-block', animation: 'spin 0.8s linear infinite' }}>↻</span>
                      Fetching technical data…
                    </div>
                  )}

                  {/* Email row */}
                  {!panelLoading && !alertEmail && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '11px', color: '#f87171', flexShrink: 0 }}>Email:</span>
                      <input
                        type="email"
                        placeholder="you@example.com"
                        value={alertEmail}
                        onChange={e => setAlertEmail(e.target.value)}
                        onClick={e => e.stopPropagation()}
                        style={{ background: '#0f172a', border: '1px solid rgba(239,68,68,0.4)', borderRadius: '6px', padding: '5px 9px', fontSize: '12px', color: '#e2e8f0', flex: 1 }}
                      />
                    </div>
                  )}
                  {!panelLoading && alertEmail && (
                    <div style={{ fontSize: '10px', color: '#334155' }}>→ {alertEmail}</div>
                  )}

                  {/* Suggestion rows */}
                  {!panelLoading && suggestions.map(s => {
                    const key = `${r.symbol}:${s.condition}:${s.threshold}`;
                    const done = alertDone.has(key);
                    const saving = alertSaving === key;
                    const isDown = s.condition.includes('below') || s.condition === 'death_cross';
                    return (
                      <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '10px', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', padding: '8px 12px', border: '1px solid #1e293b' }}>
                        <span style={{ fontSize: '12px', color: isDown ? '#fb923c' : '#4ade80', flexShrink: 0 }}>
                          {isDown ? '▼' : '▲'}
                        </span>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: '12px', color: '#cbd5e1' }}>{s.label}</div>
                          {s.sublabel && <div style={{ fontSize: '10px', color: '#475569', marginTop: '1px' }}>{s.sublabel}</div>}
                        </div>
                        <button
                          onClick={e => { e.stopPropagation(); handleSetAlert(r.symbol, s); }}
                          disabled={saving || done || !alertEmail}
                          style={{
                            padding: '4px 12px', borderRadius: '5px', fontSize: '11px', fontWeight: 700,
                            background: done ? 'rgba(34,197,94,0.15)' : saving ? '#1e293b' : !alertEmail ? '#1e293b' : 'rgba(99,102,241,0.2)',
                            color: done ? '#4ade80' : saving ? '#475569' : !alertEmail ? '#334155' : '#818cf8',
                            cursor: done || saving || !alertEmail ? 'not-allowed' : 'pointer',
                            border: done ? '1px solid rgba(34,197,94,0.3)' : '1px solid rgba(99,102,241,0.2)',
                            flexShrink: 0, transition: 'all 0.15s',
                          }}
                        >
                          {done ? '✓ Set' : saving ? '…' : 'Set Alert'}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <style>{`
        .opp-card:hover { border-color: #334155 !important; background: #0f1829 !important; }
        .outlook-card:hover { opacity: 0.9; transform: translateY(-1px); box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
      `}</style>
    </div>
  );
}

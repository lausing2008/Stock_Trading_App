'use client';
import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type RankingRow, type LatestPrice, type SignalSummary, type WatchlistItem } from '@/lib/api';
import { askAI, isAiConfigured } from '@/lib/ai';

type Strategy = 'all' | 'swing' | 'short' | 'longterm' | 'growth';
type Market = 'all' | 'US' | 'HK';
type OutlookDirection = 'BULLISH' | 'BEARISH' | 'NEUTRAL';

interface OutlookItem {
  symbol: string;
  direction: OutlookDirection;
  horizon: string;
  confidence: 'high' | 'medium' | 'low';
  reason: string;
  catalysts: string[];
}

const STRATEGIES: { key: Strategy; label: string; icon: string; tagline: string; desc: string }[] = [
  { key: 'all',      label: 'Top Picks',     icon: '⭐', tagline: 'Best overall K-Score',         desc: 'Highest composite score across technical, momentum, value, growth, and volatility.' },
  { key: 'swing',    label: 'Swing Trade',   icon: '📊', tagline: '5–30 day hold',                desc: 'Strong AI signal + technical setup. Best for defined entry/exit around a catalyst or pattern.' },
  { key: 'short',    label: 'Short-Term',    icon: '⚡', tagline: '1–5 day move',                 desc: 'High recent momentum and volume expansion. Best for capitalising on short breakouts or pullbacks.' },
  { key: 'longterm', label: 'Long-Term',     icon: '🏛️', tagline: '6–24 month horizon',           desc: 'Undervalued fundamentals with strong growth trajectory. Buy and hold at or below fair value.' },
  { key: 'growth',   label: 'Growth',        icon: '🚀', tagline: 'High growth momentum',         desc: 'Top growth + momentum scores. Companies growing revenue/earnings faster than the market.' },
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
    case 'all':      return r.score;
    case 'swing':    return tech * 0.40 + mom * 0.25 + sigB + conf * 0.15;
    case 'short':    return mom  * 0.50 + tech * 0.25 + Math.abs(chg) * 3 + vlt * 0.10;
    case 'longterm': return val  * 0.40 + grow * 0.30 + Math.max(0, upside) * 0.6 + vlt * 0.15;
    case 'growth':   return grow * 0.50 + mom  * 0.30 + tech * 0.20;
    default:         return r.score;
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
    default:
      return { label: 'K-Score', value: r.score.toFixed(0), color: scoreColor(r.score) };
  }
}

const STRATEGY_FILTER: Record<Strategy, (r: RankingRow, sig?: SignalSummary) => boolean> = {
  all:      () => true,
  swing:    (r, sig) => (sig?.signal === 'BUY' || sig?.signal === 'HOLD') && (r.technical ?? 0) >= 45,
  short:    (r) => (r.momentum ?? 0) >= 40,
  longterm: (r) => (r.value ?? 0) >= 40 || (r.growth ?? 0) >= 50,
  growth:   (r) => (r.growth ?? 0) >= 50,
};

export default function Opportunities() {
  const [strategy, setStrategy] = useState<Strategy>('all');
  const [market, setMarket] = useState<Market>('all');

  // Near-term outlook state
  const [outlook, setOutlook] = useState<OutlookItem[] | null>(null);
  const [outlookLoading, setOutlookLoading] = useState(false);
  const [outlookError, setOutlookError] = useState<string | null>(null);
  const [outlookStatus, setOutlookStatus] = useState('');
  const [outlookCollapsed, setOutlookCollapsed] = useState(false);

  const { data: rankData, isLoading } = useSWR('rankings-all', () => api.rankings());
  const { data: pricesData } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalsData } = useSWR('signals-all', () => api.allSignals());
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
      .filter(r => r.score > 0)
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
    const symbols = watchlist?.map(w => w.symbol) ?? [];
    if (symbols.length === 0) {
      setOutlookError('Your watchlist is empty. Add stocks first.');
      return;
    }

    setOutlookLoading(true);
    setOutlookError(null);
    setOutlook(null);
    setOutlookCollapsed(false);

    try {
      setOutlookStatus(`Fetching latest news for ${symbols.length} stocks…`);
      const newsResults = await Promise.allSettled(
        symbols.map(sym => api.getNews(sym, 'yfinance,google').catch(() => [] as { title: string; sentiment_label: string }[]))
      );

      setOutlookStatus('Analysing signals, trends, and news with AI…');

      const stockContexts = symbols.map((sym, i) => {
        const r = rankData?.rankings.find(row => row.symbol === sym);
        const sig = signalMap[sym];
        const lp = priceMap[sym];
        const newsArr = newsResults[i].status === 'fulfilled'
          ? (newsResults[i] as PromiseFulfilledResult<{ title: string; sentiment_label: string }[]>).value
          : [];

        if (!r) return null;

        const headlines = newsArr
          .slice(0, 5)
          .map((n) => `  - [${n.sentiment_label}] ${n.title}`)
          .join('\n') || '  (no recent news)';

        return `Symbol: ${sym}
Name: ${r.name}${r.name_zh ? ` (${r.name_zh})` : ''}
Sector: ${r.sector ?? 'Unknown'} | Market: ${r.market}
AI Signal: ${sig?.signal ?? 'N/A'} (${sig?.confidence?.toFixed(0) ?? 0}% confidence)
K-Score: ${r.score.toFixed(0)} | Technical: ${(r.technical ?? 0).toFixed(0)} | Momentum: ${(r.momentum ?? 0).toFixed(0)} | Value: ${(r.value ?? 0).toFixed(0)} | Growth: ${(r.growth ?? 0).toFixed(0)}
Today's Change: ${lp?.change_pct != null ? `${lp.change_pct >= 0 ? '+' : ''}${lp.change_pct.toFixed(2)}%` : 'N/A'}
Recent News Headlines:
${headlines}`;
      }).filter(Boolean) as string[];

      const systemPrompt = `You are a quantitative stock analyst specializing in short-term price prediction. Your task: for each stock, predict the near-term (2–5 day) price direction based on the AI signal, K-Score sub-scores, price momentum, and news headlines.

Be direct and specific. Identify the single most important near-term catalyst or risk.

Return ONLY a valid JSON array — no markdown fences, no prose outside the JSON. Each element must have exactly these fields:
{
  "symbol": "string",
  "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
  "horizon": "e.g. 2–3 days",
  "confidence": "high" | "medium" | "low",
  "reason": "1–2 sentences: the primary near-term driver, specific and actionable.",
  "catalysts": ["bullet 1 (≤8 words)", "bullet 2", "bullet 3"]
}`;

      const userMsg = `Predict near-term price direction for these ${stockContexts.length} stocks:\n\n${stockContexts.join('\n\n---\n\n')}`;

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
                        <span style={{ fontSize: '9px', color: '#334155', marginLeft: 'auto' }}>K {r.score.toFixed(0)}</span>
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
            onClick={() => setStrategy(s.key)}
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

          return (
            <Link
              key={r.symbol}
              href={`/stock/${r.symbol}`}
              style={{ textDecoration: 'none' }}
            >
              <div
                className="opp-card"
                style={{
                  borderRadius: '12px', border: '1px solid #1e293b',
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
                    {/* Market badge */}
                    <span style={{ fontSize: '9px', fontWeight: 700, padding: '1px 6px', borderRadius: '4px', background: r.market === 'US' ? 'rgba(59,130,246,0.15)' : 'rgba(236,72,153,0.15)', color: r.market === 'US' ? '#60a5fa' : '#f472b6', border: r.market === 'US' ? '1px solid rgba(59,130,246,0.25)' : '1px solid rgba(236,72,153,0.25)', letterSpacing: '0.05em' }}>
                      {r.market}
                    </span>
                    {r.sector && <span style={{ fontSize: '9px', color: '#334155' }}>{r.sector}</span>}
                    {/* Signal badge */}
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
                      <span style={{ fontSize: '11px', color: '#334155' }}>K-Score: {r.score.toFixed(0)} — view stock detail for full analysis</span>
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

                {/* Right panel: price + key metric */}
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
                    K {r.score.toFixed(0)}
                  </div>
                </div>
              </div>
            </Link>
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

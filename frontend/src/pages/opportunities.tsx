'use client';
import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type RankingRow, type LatestPrice, type SignalSummary, type WatchlistItem } from '@/lib/api';

type Strategy = 'all' | 'swing' | 'short' | 'longterm' | 'growth';
type Market = 'all' | 'US' | 'HK';

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

  const active = STRATEGIES.find(s => s.key === strategy)!;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Page header */}
      <div>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#f1f5f9', marginBottom: '4px' }}>Opportunities</h1>
        <p style={{ fontSize: '13px', color: '#475569' }}>
          Top stocks ranked by strategy using K-Score sub-scores, AI signals, and live price data. Updated on every page load.
        </p>
      </div>

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
      `}</style>
    </div>
  );
}

/**
 * Market index detail page — /market/[ticker] for every tile on the dashboard's market
 * overview (US indexes, index futures, 10-Yr yield, gold, bitcoin, Hang Seng).
 *
 * History is fetched from Yahoo on demand and never stored, so these symbols stay out of
 * signals, rankings and alerts. Quotes carry Yahoo's own exchange delay (shown on the page)
 * plus up to ~1 min of our cache.
 */
import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/router';
import Head from 'next/head';
import Link from 'next/link';
import useSWR from 'swr';
import type { IChartApi, UTCTimestamp } from 'lightweight-charts';
import { api, type MarketIndexDetail, type MarketIndexRange } from '@/lib/api';

const RANGES: { key: MarketIndexRange; label: string }[] = [
  { key: '1d', label: '1D' }, { key: '5d', label: '5D' }, { key: '1mo', label: '1M' },
  { key: '6mo', label: '6M' }, { key: '1y', label: '1Y' }, { key: '5y', label: '5Y' },
];

const CARD: React.CSSProperties = {
  background: '#0b1220', border: '1px solid #1e293b', borderRadius: 8, padding: 16,
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
};

function fmt(v: number | null | undefined, isYield: boolean) {
  if (v == null) return '—';
  return isYield ? `${v.toFixed(3)}%` : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function Chart({ data, isYield }: { data: MarketIndexDetail; isYield: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || data.bars.length === 0) return;
    // Loaded client-side only, like the stock page's PriceChart (dynamic, ssr: false).
    let chart: IChartApi | null = null;
    let cancelled = false;
    const resize = () => { if (ref.current && chart) chart.applyOptions({ width: ref.current.clientWidth }); };
    import('lightweight-charts').then(({ createChart }) => {
      if (cancelled || !ref.current) return;
      const intraday = !['1d', '1wk', '1mo'].includes(data.interval);
      chart = createChart(ref.current, {
        height: 380,
        layout: { background: { color: '#0b1220' }, textColor: '#94a3b8' },
        grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
        rightPriceScale: { borderColor: '#1e293b' },
        timeScale: { borderColor: '#1e293b', timeVisible: intraday, secondsVisible: false },
        localization: isYield ? { priceFormatter: (p: number) => `${p.toFixed(3)}%` } : undefined,
      });
      const first = data.bars[0].c;
      const last = data.bars[data.bars.length - 1].c;
      const up = last >= first;
      const series = chart.addAreaSeries({
        lineColor: up ? '#22c55e' : '#ef4444',
        topColor: up ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)',
        bottomColor: 'rgba(0,0,0,0)',
        lineWidth: 2,
      });
      series.setData(data.bars.map(b => ({ time: b.t as UTCTimestamp, value: b.c })));
      chart.timeScale().fitContent();
      resize();
      window.addEventListener('resize', resize);
    });
    return () => { cancelled = true; window.removeEventListener('resize', resize); chart?.remove(); };
  }, [data, isYield]);
  if (data.bars.length === 0) {
    return <div style={{ height: 380, display: 'grid', placeItems: 'center', color: '#64748b', fontSize: 13 }}>
      No price history returned by Yahoo for this range right now.
    </div>;
  }
  return <div ref={ref} style={{ width: '100%' }} />;
}

export default function MarketIndexPage() {
  const router = useRouter();
  const ticker = typeof router.query.ticker === 'string' ? router.query.ticker : '';
  const [range, setRange] = useState<MarketIndexRange>('1d');
  const { data, error } = useSWR<MarketIndexDetail>(
    ticker ? ['market-index', ticker, range] : null,
    () => api.marketIndexDetail(ticker, range),
    { refreshInterval: range === '1d' ? 60_000 : 0 },
  );

  const isYield = ticker === '^TNX';
  const isVix = ticker === '^VIX';
  const up = (data?.change ?? 0) >= 0;
  // Rising VIX is bad news for stocks, so its colours are inverted (matches the dashboard tile).
  const good = isVix ? !up : up;

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '20px 16px', display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Head><title>{data?.name ?? ticker} — Market</title></Head>
      <Link href="/" style={{ fontSize: 12, color: '#60a5fa', textDecoration: 'none' }}>← Dashboard</Link>

      {error && <div style={{ ...CARD, color: '#f87171', fontSize: 13 }}>Could not load {ticker}: {String(error.message ?? error)}</div>}

      {data && (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: 22, fontWeight: 800, color: '#f1f5f9', margin: 0 }}>{data.name}</h1>
            <span style={{ fontSize: 12, color: '#64748b' }}>{data.ticker}</span>
            <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
                           color: data.delay_minutes ? '#facc15' : '#4ade80',
                           background: data.delay_minutes ? 'rgba(250,204,21,0.1)' : 'rgba(34,197,94,0.12)' }}>
              {data.delay_minutes ? `Delayed ${data.delay_minutes} min` : 'Real-time'} · refreshes every 60 s
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
            <span style={{ fontSize: 30, fontWeight: 800, color: '#f1f5f9' }}>{fmt(data.price, isYield)}</span>
            {data.change != null && (
              <span style={{ fontSize: 15, fontWeight: 700, color: good ? '#4ade80' : '#f87171' }}>
                {up ? '▲' : '▼'} {isYield
                  ? `${Math.abs(data.change * 100).toFixed(1)} bp`
                  : `${Math.abs(data.change).toLocaleString(undefined, { maximumFractionDigits: 2 })} (${Math.abs(data.change_pct ?? 0).toFixed(2)}%)`}
              </span>
            )}
            <span style={{ fontSize: 11, color: '#64748b' }}>vs previous close</span>
          </div>

          <div style={CARD}>
            <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
              {RANGES.map(r => (
                <button key={r.key} onClick={() => setRange(r.key)}
                  style={{ fontSize: 12, fontWeight: 700, padding: '4px 10px', borderRadius: 4, cursor: 'pointer',
                           border: '1px solid #1e293b',
                           background: range === r.key ? '#1e3a8a' : 'transparent',
                           color: range === r.key ? '#e0e7ff' : '#94a3b8' }}>
                  {r.label}
                </button>
              ))}
            </div>
            <Chart data={data} isYield={isYield} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
            {[
              ['Open', fmt(data.open, isYield)],
              ['Day range', data.day_low != null && data.day_high != null ? `${fmt(data.day_low, isYield)} – ${fmt(data.day_high, isYield)}` : '—'],
              ['52-week range', data.year_low != null && data.year_high != null ? `${fmt(data.year_low, isYield)} – ${fmt(data.year_high, isYield)}` : '—'],
            ].map(([label, value]) => (
              <div key={label} style={CARD}>
                <div style={LABEL}>{label}</div>
                <div style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0', marginTop: 4 }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ ...CARD, fontSize: 13, color: '#cbd5e1', lineHeight: 1.5 }}>
            <div style={LABEL}>What this is</div>
            <div style={{ marginTop: 6 }}>{data.description}</div>
            <div style={{ marginTop: 8, fontSize: 11, color: '#64748b' }}>
              Source: Yahoo Finance. Shown for context only — not stored, and not used by signals, rankings or alerts.
            </div>
          </div>
        </>
      )}
      {!data && !error && <div style={{ color: '#64748b', fontSize: 13 }}>Loading {ticker}…</div>}
    </div>
  );
}

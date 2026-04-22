import { useRouter } from 'next/router';
import { useState, useEffect } from 'react';
import useSWR from 'swr';
import dynamic from 'next/dynamic';
import SignalCard from '@/components/SignalCard';
import NewsCard from '@/components/NewsCard';
import { api, type Overview, type Prediction, type NewsItem } from '@/lib/api';

function RefreshButton({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      style={{
        display: 'flex', alignItems: 'center', gap: '6px',
        padding: '6px 13px', borderRadius: '6px',
        border: '1px solid rgba(148,163,184,0.15)',
        background: 'rgba(255,255,255,0.03)',
        color: loading ? '#818cf8' : '#64748b',
        cursor: loading ? 'not-allowed' : 'pointer',
        fontSize: '12px', transition: 'all 0.15s',
      }}
    >
      <span style={{ display: 'inline-block', fontSize: '14px', lineHeight: 1, animation: loading ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
      {loading ? 'Refreshing…' : 'Refresh'}
    </button>
  );
}

const PriceChart = dynamic(() => import('@/components/PriceChart'), { ssr: false });

export default function StockDetail() {
  const r = useRouter();
  const symbol = (r.query.symbol as string) ?? '';

  const { data, error, isLoading, mutate: mutateOverview } = useSWR<Overview>(
    symbol ? `overview-${symbol}` : null,
    () => api.overview(symbol),
  );
  const { data: news, mutate: mutateNews } = useSWR<NewsItem[]>(
    symbol ? `news-${symbol}` : null,
    () => api.getNews(symbol),
  );

  const [watched, setWatched] = useState(false);
  const [watchPending, setWatchPending] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [mlResult, setMlResult] = useState<Prediction | null>(null);
  const [mlModel, setMlModel] = useState('xgboost');
  const [mlLoading, setMlLoading] = useState(false);
  const [mlError, setMlError] = useState('');
  const [trainAllState, setTrainAllState] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [trainAllMsg, setTrainAllMsg] = useState('');

  useEffect(() => {
    if (!symbol) return;
    api.isWatched(symbol).then(setWatched).catch(() => {});
  }, [symbol]);

  async function handleRefresh() {
    setRefreshing(true);
    await Promise.all([mutateOverview(), mutateNews()]);
    setRefreshing(false);
  }

  async function toggleWatch() {
    setWatchPending(true);
    try {
      if (watched) { await api.removeFromWatchlist(symbol); setWatched(false); }
      else { await api.addToWatchlist(symbol); setWatched(true); }
    } finally { setWatchPending(false); }
  }

  async function runML() {
    setMlLoading(true);
    setMlError('');
    try {
      const result = await api.predict(symbol, mlModel);
      setMlResult(result);
    } catch {
      setMlError('Model not trained yet. Train first.');
    } finally {
      setMlLoading(false);
    }
  }

  async function trainML() {
    setMlLoading(true);
    setMlError('');
    try {
      await api.trainModel(symbol, mlModel);
      setMlError('Training started — takes ~30s, then run predict.');
    } finally {
      setMlLoading(false);
    }
  }

  async function handleTrainAll() {
    if (trainAllState === 'running') return;
    setTrainAllState('running');
    setTrainAllMsg('');
    try {
      const stocks = await api.listStocks();
      await api.ingest(stocks.map(s => s.symbol));
      // Refresh this stock's chart with newly ingested prices
      await mutateOverview();
      const res = await api.trainAll();
      setTrainAllState('done');
      setTrainAllMsg(`✓ Ingested ${stocks.length} stocks · Scheduled ${res.count} ML models — ready in ~2–5 min`);
    } catch {
      setTrainAllState('error');
      setTrainAllMsg('Pipeline failed. Check backend logs.');
    }
  }

  if (isLoading) return <div className="text-slate-400 p-4">Loading…</div>;
  if (error || !data) return <div className="text-slate-300 p-4">Error loading {symbol}.</div>;

  const ranking = data.ranking;

  const levels = data.levels;
  const srLevels = levels?.support_resistance ?? [];
  const fibLevels = levels?.fibonacci ?? {};
  const bullPct = mlResult ? (mlResult.bullish_probability * 100).toFixed(1) : null;

  return (
    <div className="space-y-4">
      {/* Back button */}
      <div>
        <button
          onClick={() => r.back()}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            padding: '5px 12px', borderRadius: '6px', fontSize: '13px',
            border: '1px solid #1e293b', background: 'transparent',
            color: '#64748b', cursor: 'pointer', transition: 'all 0.15s',
          }}
        >
          ← Back
        </button>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-6">
          <div>
            <h1 className="text-2xl font-bold">{symbol}</h1>
            <div className="text-sm text-slate-400">{(data.price as { name?: string })?.name}</div>
            <div className="flex gap-3 mt-1 text-xs text-slate-500">
              {data.price && <span>{(data.price as { market?: string })?.market} · {(data.price as { exchange?: string })?.exchange}</span>}
              {data.price && <span>{(data.price as { sector?: string })?.sector}</span>}
            </div>
          </div>
          {ranking?.fair_price != null && (
            <div className="rounded-md border border-indigo-800 bg-indigo-950/40 px-4 py-2 text-center">
              <div className="text-xs text-indigo-400 font-medium mb-0.5">Fair Value</div>
              <div className="text-xl font-bold text-indigo-300">${ranking.fair_price.toFixed(2)}</div>
              {ranking?.score != null && (
                <div className="text-xs text-slate-500 mt-0.5">K-Score {ranking.score.toFixed(0)}</div>
              )}
            </div>
          )}
          {data.signal && (
            <div className={`rounded-md border px-4 py-2 text-center ${data.signal.signal === 'BUY' ? 'border-green-800 bg-green-950/40' : data.signal.signal === 'SELL' ? 'border-red-800 bg-red-950/40' : 'border-yellow-800 bg-yellow-950/40'}`}>
              <div className={`text-xs font-medium mb-0.5 ${data.signal.signal === 'BUY' ? 'text-green-400' : data.signal.signal === 'SELL' ? 'text-red-400' : 'text-yellow-400'}`}>Recommendation</div>
              <div className={`text-xl font-bold ${data.signal.signal === 'BUY' ? 'text-green-300' : data.signal.signal === 'SELL' ? 'text-red-300' : 'text-yellow-300'}`}>{data.signal.signal}</div>
              <div className="text-xs text-slate-500 mt-0.5">{(data.signal.bullish_probability * 100).toFixed(0)}% bullish</div>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <RefreshButton onClick={handleRefresh} loading={refreshing} />
          <button
            onClick={toggleWatch}
            disabled={watchPending}
            style={{
              padding: '6px 14px', borderRadius: '6px', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
              border: watched ? 'none' : '1px solid #475569',
              background: watched ? '#4f46e5' : 'transparent',
              color: watched ? '#ffffff' : '#cbd5e1', transition: 'all 0.15s',
            }}
          >
            {watched ? '★ Watching' : '☆ Watch'}
          </button>
        </div>
      </div>

      {/* Main layout: chart left, sidebar right */}
      <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 320px' }}>
        {/* Chart */}
        <div>
          {data.prices && data.prices.length > 0 ? (
            <PriceChart prices={data.prices} indicators={data.indicators} levels={data.levels} />
          ) : (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4 text-slate-400">
              No price data — run: POST /admin/ingest &#123;"symbols":["{symbol}"]&#125;
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-3">
          {/* AI Signal */}
          {data.signal && <SignalCard signal={data.signal} />}

          {/* K-Score */}
          {ranking && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              <div className="flex items-baseline justify-between mb-2">
                <h3 className="text-sm font-semibold text-slate-300">K-Score</h3>
                <span className="text-2xl font-bold">{ranking.score?.toFixed(1)}</span>
              </div>
              <div className="grid grid-cols-2 gap-y-1.5 text-xs text-slate-500">
                {[
                  ['Technical', ranking.technical],
                  ['Momentum', ranking.momentum],
                  ['Value', ranking.value],
                  ['Growth', ranking.growth],
                  ['Volatility', ranking.volatility],
                  ['Fair Price', ranking.fair_price != null ? `$${ranking.fair_price.toFixed(2)}` : '—'],
                ].map(([k, v]) => (
                  <div key={k as string}><span className="text-slate-600">{k}:</span> {typeof v === 'number' ? v.toFixed(0) : v}</div>
                ))}
              </div>
            </div>
          )}

          {/* ML Prediction */}
          <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
            <h3 className="text-sm font-semibold text-slate-300 mb-2">ML Prediction</h3>
            <div className="flex gap-2 mb-2">
              <select
                value={mlModel}
                onChange={e => setMlModel(e.target.value)}
                className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300"
              >
                {['xgboost', 'random_forest', 'gradient_boosting', 'lstm'].map(m => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            {mlResult && (
              <div className="mb-2">
                <div className={`text-lg font-bold ${mlResult.direction === 'UP' ? 'text-green-400' : 'text-red-400'}`}>
                  {mlResult.direction} · {bullPct}% bullish
                </div>
                <div className="text-xs text-slate-500">Confidence: {mlResult.confidence?.toFixed(1)}%</div>
                <div className="mt-1.5 h-1.5 rounded-full bg-slate-700 overflow-hidden">
                  <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${mlResult.bullish_probability * 100}%` }} />
                </div>
              </div>
            )}
            {mlError && <div style={{ fontSize: '11px', color: '#fbbf24', marginBottom: '8px' }}>{mlError}</div>}
            <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
              <button
                onClick={runML}
                disabled={mlLoading}
                style={{ flex: 1, borderRadius: '6px', background: '#4f46e5', border: 'none', padding: '6px', fontSize: '12px', color: '#fff', cursor: mlLoading ? 'not-allowed' : 'pointer', opacity: mlLoading ? 0.5 : 1 }}
              >
                {mlLoading ? 'Running…' : 'Predict'}
              </button>
              <button
                onClick={trainML}
                disabled={mlLoading}
                style={{ flex: 1, borderRadius: '6px', background: 'transparent', border: '1px solid #475569', padding: '6px', fontSize: '12px', color: '#94a3b8', cursor: mlLoading ? 'not-allowed' : 'pointer', opacity: mlLoading ? 0.5 : 1 }}
              >
                Train This
              </button>
            </div>
            <button
              onClick={handleTrainAll}
              disabled={trainAllState === 'running'}
              style={{
                width: '100%', borderRadius: '6px', padding: '7px',
                border: '1px solid rgba(99,102,241,0.3)',
                background: trainAllState === 'running' ? 'rgba(99,102,241,0.15)' : 'rgba(99,102,241,0.08)',
                color: trainAllState === 'running' ? '#818cf8' : '#6366f1',
                fontSize: '12px', fontWeight: 600, cursor: trainAllState === 'running' ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '5px',
              }}
            >
              <span style={{ animation: trainAllState === 'running' ? 'spin 0.8s linear infinite' : 'none', display: 'inline-block' }}>
                {trainAllState === 'running' ? '↻' : '⚡'}
              </span>
              {trainAllState === 'running' ? 'Training All…' : 'Train All Stocks'}
            </button>
            {trainAllMsg && (
              <div style={{ marginTop: '6px', fontSize: '11px', color: trainAllState === 'done' ? '#4ade80' : '#f87171' }}>
                {trainAllMsg}
              </div>
            )}
          </div>

          {/* Patterns */}
          {data.patterns?.patterns && data.patterns.patterns.length > 0 && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              <h3 className="text-sm font-semibold text-slate-300 mb-2">Chart Patterns</h3>
              <div className="space-y-1">
                {data.patterns.patterns.map((p, i) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <span className="text-slate-300">{p.name}</span>
                    <span className="text-slate-500">{(p.confidence * 100).toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Support / Resistance levels */}
          {srLevels.length > 0 && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              <h3 className="text-sm font-semibold text-slate-300 mb-2">Support &amp; Resistance</h3>
              <div className="space-y-1">
                {srLevels.slice(0, 6).map((lvl, i) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <span className={lvl.kind === 'support' ? 'text-green-400' : 'text-red-400'}>
                      {lvl.kind === 'support' ? 'S' : 'R'} ${lvl.price.toFixed(2)}
                    </span>
                    <span className="text-slate-500">{lvl.strength} touches</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Fibonacci levels */}
          {Object.keys(fibLevels).length > 0 && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              <h3 className="text-sm font-semibold text-slate-300 mb-2">Fibonacci Levels</h3>
              <div className="space-y-1">
                {Object.entries(fibLevels).map(([k, v]) => (
                  <div key={k} className="flex justify-between text-xs">
                    <span className="text-slate-500">{k}</span>
                    <span className="text-slate-300">${(v as number).toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Company Financials — full width */}
      {data.fundamentals && (() => {
        const f = data.fundamentals!;

        function fmtBig(n: number | null | undefined): string {
          if (n == null) return '—';
          const abs = Math.abs(n);
          if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
          if (abs >= 1e9)  return `${(n / 1e9).toFixed(2)}B`;
          if (abs >= 1e6)  return `${(n / 1e6).toFixed(2)}M`;
          if (abs >= 1e3)  return `${(n / 1e3).toFixed(1)}K`;
          return n.toFixed(2);
        }
        function fmtPct(n: number | null | undefined): string {
          if (n == null) return '—';
          return `${(n * 100).toFixed(1)}%`;
        }
        function fmtX(n: number | null | undefined): string {
          if (n == null) return '—';
          return `${n.toFixed(1)}x`;
        }
        function fmtNum(n: number | null | undefined, d = 2): string {
          if (n == null) return '—';
          return n.toFixed(d);
        }
        function growthColor(n: number | null | undefined): string {
          if (n == null) return '#94a3b8';
          return n >= 0 ? '#4ade80' : '#f87171';
        }

        const recColors: Record<string, string> = {
          buy: '#4ade80', 'strong_buy': '#22c55e',
          hold: '#facc15', neutral: '#facc15',
          sell: '#f87171', 'strong_sell': '#ef4444',
          underperform: '#fb923c', outperform: '#86efac',
        };
        const recLabel: Record<string, string> = {
          buy: 'BUY', strong_buy: 'STRONG BUY',
          hold: 'HOLD', neutral: 'NEUTRAL',
          sell: 'SELL', strong_sell: 'STRONG SELL',
          underperform: 'UNDERPERFORM', outperform: 'OUTPERFORM',
        };

        const card = (label: string, value: string, sub?: string, valueColor?: string) => (
          <div key={label} style={{ background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid #1e293b', padding: '10px 13px' }}>
            <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px' }}>{label}</div>
            <div style={{ fontSize: '15px', fontWeight: 700, color: valueColor ?? '#e2e8f0' }}>{value}</div>
            {sub && <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px' }}>{sub}</div>}
          </div>
        );

        const curPrice = (data.price as any)?.regularMarketPrice ?? null;
        const hi = f.week_52_high, lo = f.week_52_low;
        const rangePct = (hi && lo && hi > lo) ? ((((curPrice ?? lo) - lo) / (hi - lo)) * 100) : null;

        return (
          <div>
            <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', marginBottom: '12px' }}>Company Financials</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

              {/* Row 1 — Valuation */}
              <div>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#4f46e5', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Valuation</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: '8px' }}>
                  {card('Market Cap', fmtBig(f.market_cap))}
                  {card('Enterprise Value', fmtBig(f.enterprise_value))}
                  {card('P/E (TTM)', fmtX(f.trailing_pe))}
                  {card('Forward P/E', fmtX(f.forward_pe))}
                  {card('P/B Ratio', fmtX(f.price_to_book))}
                  {card('EV / EBITDA', fmtX(f.ev_to_ebitda))}
                </div>
              </div>

              {/* Row 2 — Income + Cash */}
              <div>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#0891b2', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Financials (TTM)</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: '8px' }}>
                  {card('Revenue', fmtBig(f.total_revenue), f.revenue_growth != null ? `${f.revenue_growth >= 0 ? '+' : ''}${fmtPct(f.revenue_growth)} YoY` : undefined, '#e2e8f0')}
                  {card('Gross Profit', fmtBig(f.gross_profit))}
                  {card('Net Income', fmtBig(f.net_income), undefined, f.net_income != null ? (f.net_income >= 0 ? '#4ade80' : '#f87171') : undefined)}
                  {card('EBITDA', fmtBig(f.ebitda))}
                  {card('Free Cash Flow', fmtBig(f.free_cashflow), undefined, f.free_cashflow != null ? (f.free_cashflow >= 0 ? '#4ade80' : '#f87171') : undefined)}
                  {card('Operating CF', fmtBig(f.operating_cashflow))}
                </div>
              </div>

              {/* Row 3 — Balance sheet + margins + per share */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
                {/* Balance sheet */}
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#7c3aed', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Balance Sheet</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {card('Total Cash', fmtBig(f.total_cash))}
                    {card('Total Debt', fmtBig(f.total_debt), undefined, f.total_debt != null && f.total_cash != null ? (f.total_cash > f.total_debt ? '#4ade80' : '#f87171') : undefined)}
                  </div>
                </div>
                {/* Margins */}
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#059669', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Margins</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {card('Gross Margin', fmtPct(f.gross_margin))}
                    {card('Operating Margin', fmtPct(f.operating_margin))}
                    {card('Profit Margin', fmtPct(f.profit_margin))}
                  </div>
                </div>
                {/* Returns + growth */}
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#b45309', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Returns &amp; Growth</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {card('ROE', fmtPct(f.return_on_equity))}
                    {card('ROA', fmtPct(f.return_on_assets))}
                    {card('Earnings Growth', fmtPct(f.earnings_growth), 'YoY', growthColor(f.earnings_growth))}
                  </div>
                </div>
              </div>

              {/* Row 4 — Per share + range + analyst */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                {/* Per share & risk */}
                <div>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>Per Share &amp; Risk</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                    {card('EPS (TTM)', fmtNum(f.trailing_eps))}
                    {card('Fwd EPS', fmtNum(f.forward_eps))}
                    {card('Book Value', fmtNum(f.book_value))}
                    {card('Dividend Yield', f.dividend_yield != null ? fmtPct(f.dividend_yield) : '—', f.dividend_rate != null ? `$${f.dividend_rate.toFixed(2)}/yr` : undefined)}
                    {card('Beta', fmtNum(f.beta), 'vs market')}
                    {card('Shares Out', fmtBig(f.shares_outstanding))}
                  </div>
                </div>

                {/* 52-week + analyst */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {/* 52-week range */}
                  {hi != null && lo != null && (
                    <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid #1e293b', padding: '12px 13px' }}>
                      <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>52-Week Range</div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '6px' }}>
                        <span style={{ color: '#f87171' }}>${lo.toFixed(2)}</span>
                        <span style={{ color: '#94a3b8' }}>Low → High</span>
                        <span style={{ color: '#4ade80' }}>${hi.toFixed(2)}</span>
                      </div>
                      <div style={{ height: '6px', background: '#1e293b', borderRadius: '3px', overflow: 'hidden', position: 'relative' }}>
                        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${rangePct ?? 50}%`, background: 'linear-gradient(90deg, #f87171, #facc15, #4ade80)', borderRadius: '3px' }} />
                      </div>
                      {curPrice && <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px', textAlign: 'center' }}>Current ${curPrice.toFixed(2)} · {rangePct != null ? `${rangePct.toFixed(0)}% of range` : ''}</div>}
                    </div>
                  )}
                  {/* Analyst consensus */}
                  {(f.target_price != null || f.recommendation != null) && (
                    <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid #1e293b', padding: '12px 13px' }}>
                      <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Analyst Consensus</div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        {f.recommendation && (
                          <div style={{ fontSize: '14px', fontWeight: 800, color: recColors[f.recommendation] ?? '#94a3b8' }}>
                            {recLabel[f.recommendation] ?? f.recommendation.toUpperCase()}
                          </div>
                        )}
                        {f.target_price != null && (
                          <div style={{ textAlign: 'right' }}>
                            <div style={{ fontSize: '16px', fontWeight: 700, color: '#818cf8' }}>${f.target_price.toFixed(2)}</div>
                            <div style={{ fontSize: '10px', color: '#475569' }}>{f.number_of_analysts != null ? `${f.number_of_analysts} analysts` : 'target'}</div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>

            </div>
          </div>
        );
      })()}

      {/* News feed — full width below chart */}
      <div>
        <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#cbd5e1', marginBottom: '12px' }}>
          News &amp; Sentiment
        </h2>
        {!news && <div style={{ fontSize: '12px', color: '#475569' }}>Loading news…</div>}
        {news && news.length === 0 && <div style={{ fontSize: '12px', color: '#475569' }}>No recent news found.</div>}
        {news && news.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '10px' }}>
            {news.map((item, i) => <NewsCard key={i} item={item} />)}
          </div>
        )}
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

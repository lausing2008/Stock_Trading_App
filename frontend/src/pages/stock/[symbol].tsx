import { useRouter } from 'next/router';
import { useState, useEffect, useRef } from 'react';
import useSWR from 'swr';
import dynamic from 'next/dynamic';
import SignalCard from '@/components/SignalCard';
import NewsCard from '@/components/NewsCard';
import { api, type Overview, type Prediction, type NewsItem, type LatestPrice } from '@/lib/api';
import { askAI, isAiConfigured, getAiProviderLabel, type AiMessage } from '@/lib/ai';
import { activeNewsSources, loadSettings } from '@/lib/settings';

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
  const { data: allPrices } = useSWR<LatestPrice[]>(
    'latest-prices',
    () => api.latestPrices(),
    { refreshInterval: 60_000 },
  );
  const newsSources = typeof window !== 'undefined' ? activeNewsSources() : 'yfinance,google';
  const { data: news, mutate: mutateNews } = useSWR<NewsItem[]>(
    symbol ? `news-${symbol}-${newsSources}` : null,
    () => api.getNews(symbol, newsSources),
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

  // AI chat state
  const [aiMessages, setAiMessages] = useState<AiMessage[]>([]);
  const [aiInput, setAiInput] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [aiOpen, setAiOpen] = useState(false);
  const aiBottomRef = useRef<HTMLDivElement>(null);

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

  async function sendAiMessage() {
    const text = aiInput.trim();
    if (!text || aiLoading) return;
    setAiError('');
    const userMsg: AiMessage = { role: 'user', content: text };
    const updated = [...aiMessages, userMsg];
    setAiMessages(updated);
    setAiInput('');
    setAiLoading(true);
    setTimeout(() => aiBottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    try {
      const systemCtx = [
        `You are a financial analyst assistant for the stock ${symbol} (${(data as Overview & { price?: { name?: string } })?.price?.name ?? symbol}).`,
        `Current price: ${data?.price ? JSON.stringify(data.price) : 'N/A'}`,
        data?.signal ? `Signal: ${data.signal.signal} (${(data.signal.bullish_probability * 100).toFixed(0)}% bullish, ${data.signal.confidence.toFixed(0)}% confidence)` : '',
        data?.ranking ? `K-Score: ${data.ranking.score?.toFixed(0)}, Fair Value: $${data.ranking.fair_price?.toFixed(2)}` : '',
        `Recent headlines: ${(news ?? []).slice(0, 5).map(n => n.title).join(' | ')}`,
        'Be concise, data-driven, and reference the above context in your answers.',
      ].filter(Boolean).join('\n');
      const reply = await askAI(updated, systemCtx);
      setAiMessages(prev => [...prev, { role: 'assistant', content: reply }]);
    } catch (e) {
      setAiError(e instanceof Error ? e.message : 'AI request failed');
    } finally {
      setAiLoading(false);
      setTimeout(() => aiBottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 100);
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

  const liveQuote = allPrices?.find(p => p.symbol === symbol) ?? null;
  const curPrice: number | null = liveQuote?.price ?? (data.prices && data.prices.length > 0 ? data.prices[data.prices.length - 1].close : null);
  const changePct: number | null = liveQuote?.change_pct ?? null;
  const prevClose: number | null = liveQuote?.prev_close ?? null;

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
          {/* Live price card */}
          <div style={{ textAlign: 'center', padding: '10px 20px', borderRadius: '8px', border: '1px solid #1e293b', background: 'rgba(255,255,255,0.02)', minWidth: '110px' }}>
            <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '2px' }}>
              {liveQuote ? 'Live Price' : 'Last Close'}
            </div>
            <div style={{ fontSize: '24px', fontWeight: 800, color: '#f1f5f9', lineHeight: 1.1 }}>
              {curPrice != null ? `$${curPrice.toFixed(2)}` : '—'}
            </div>
            {changePct != null && (
              <div style={{ fontSize: '13px', fontWeight: 700, marginTop: '2px', color: changePct >= 0 ? '#4ade80' : '#f87171' }}>
                {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
              </div>
            )}
            {prevClose != null && (
              <div style={{ fontSize: '10px', color: '#475569', marginTop: '1px' }}>
                Prev ${prevClose.toFixed(2)}
              </div>
            )}
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
          {data.signal && (() => {
            const s = data.signal.signal;
            const borderCls = s === 'BUY' ? 'border-green-800 bg-green-950/40' : s === 'SELL' ? 'border-red-800 bg-red-950/40' : s === 'WAIT' ? 'border-orange-800 bg-orange-950/40' : 'border-yellow-800 bg-yellow-950/40';
            const labelCls  = s === 'BUY' ? 'text-green-400'  : s === 'SELL' ? 'text-red-400'  : s === 'WAIT' ? 'text-orange-400'  : 'text-yellow-400';
            const valueCls  = s === 'BUY' ? 'text-green-300'  : s === 'SELL' ? 'text-red-300'  : s === 'WAIT' ? 'text-orange-300'  : 'text-yellow-300';
            return (
              <div className={`rounded-md border px-4 py-2 text-center ${borderCls}`}>
                <div className={`text-xs font-medium mb-0.5 ${labelCls}`}>AI Signal</div>
                <div className={`text-xl font-bold ${valueCls}`}>{s}</div>
                <div className="text-xs text-slate-500 mt-0.5">{(data.signal.bullish_probability * 100).toFixed(0)}% bullish</div>
              </div>
            );
          })()}
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

              {/* Row 4 — Per share & risk + 52-week range */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
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
                {hi != null && lo != null && (
                  <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                    <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>52-Week Range</div>
                    <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid #1e293b', padding: '14px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '8px' }}>
                        <span style={{ color: '#f87171' }}>${lo.toFixed(2)}</span>
                        <span style={{ color: '#64748b', fontSize: '11px' }}>52-Week Low → High</span>
                        <span style={{ color: '#4ade80' }}>${hi.toFixed(2)}</span>
                      </div>
                      <div style={{ height: '6px', background: '#1e293b', borderRadius: '3px', overflow: 'hidden', position: 'relative' }}>
                        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${rangePct ?? 50}%`, background: 'linear-gradient(90deg,#f87171,#facc15,#4ade80)', borderRadius: '3px' }} />
                      </div>
                      {curPrice && <div style={{ fontSize: '11px', color: '#64748b', marginTop: '6px', textAlign: 'center' }}>Current ${curPrice.toFixed(2)} · {rangePct != null ? `${rangePct.toFixed(0)}% of range` : ''}</div>}
                    </div>
                  </div>
                )}
              </div>

              {/* Row 5 — Analyst Ratings & Price Targets */}
              {(() => {
                const hasRatings = f.recommendation != null || f.target_price != null;
                const hasCounts = (f.analyst_strong_buy ?? 0) + (f.analyst_buy ?? 0) + (f.analyst_hold ?? 0) + (f.analyst_underperform ?? 0) + (f.analyst_sell ?? 0) > 0;
                const totalAnalysts = hasCounts
                  ? (f.analyst_strong_buy ?? 0) + (f.analyst_buy ?? 0) + (f.analyst_hold ?? 0) + (f.analyst_underperform ?? 0) + (f.analyst_sell ?? 0)
                  : (f.number_of_analysts ?? 0);
                if (!hasRatings) return null;

                // Price target range
                const tLow  = f.target_low;
                const tMed  = f.target_median;
                const tMean = f.target_price;
                const tHigh = f.target_high;
                const hasTargets = tLow != null && tHigh != null && tHigh > tLow;
                const rangeMin = hasTargets ? tLow! * 0.98 : null;
                const rangeMax = hasTargets ? tHigh! * 1.02 : null;
                const toBarPct = (p: number) =>
                  rangeMin != null && rangeMax != null
                    ? Math.max(0, Math.min(100, ((p - rangeMin) / (rangeMax - rangeMin)) * 100))
                    : null;

                // Upside from current price to mean target
                const upside = tMean != null && curPrice != null ? ((tMean - curPrice) / curPrice) * 100 : null;

                // Nearest support/resistance from srLevels
                const supports = srLevels.filter(l => l.kind === 'support' && curPrice != null && l.price < curPrice).sort((a, b) => b.price - a.price);
                const resistances = srLevels.filter(l => l.kind === 'resistance' && curPrice != null && l.price > curPrice).sort((a, b) => a.price - b.price);
                const nearestSupport = supports[0]?.price ?? null;
                const nearestResistance = resistances[0]?.price ?? null;

                // Rating bar segments
                const ratingSegs = [
                  { key: 'Strong Buy',  count: f.analyst_strong_buy  ?? 0, color: '#22c55e' },
                  { key: 'Buy',         count: f.analyst_buy         ?? 0, color: '#4ade80' },
                  { key: 'Hold',        count: f.analyst_hold        ?? 0, color: '#facc15' },
                  { key: 'Underperform',count: f.analyst_underperform ?? 0, color: '#fb923c' },
                  { key: 'Sell',        count: f.analyst_sell        ?? 0, color: '#ef4444' },
                ];

                // Recommendation mean → label + star score
                const recMean = f.recommendation_mean;
                const starScore = recMean != null ? Math.max(0, Math.min(5, 5 - recMean + 1)) : null;

                // Buy zone: from analyst low (or support) up to current price
                const buyLower = tLow ?? nearestSupport;
                const buyUpper = curPrice;

                // Sell zone: from mean target to high target (+ fair value if available)
                const sellLower = tMean;
                const sellUpper = tHigh;
                const fairPrice = ranking?.fair_price ?? null;

                return (
                  <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.2)', background: 'rgba(15,23,42,0.9)', overflow: 'hidden' }}>
                    {/* Section header */}
                    <div style={{ padding: '12px 16px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '6px' }}>
                      <div>
                        <div style={{ fontSize: '12px', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                          Analyst Ratings &amp; Price Targets
                        </div>
                        <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>
                          Via Yahoo Finance · consensus of Wall Street analysts · updated daily · not a personalised recommendation
                        </div>
                      </div>
                      {totalAnalysts > 0 && (
                        <span style={{ fontSize: '11px', color: '#475569' }}>{totalAnalysts} analysts</span>
                      )}
                    </div>

                    <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>

                      {/* Top row: rating distribution + consensus */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '16px', alignItems: 'start' }}>

                        {/* Rating bar + breakdown */}
                        <div>
                          {hasCounts && (
                            <>
                              {/* Stacked bar */}
                              <div style={{ display: 'flex', height: '10px', borderRadius: '5px', overflow: 'hidden', gap: '1px', marginBottom: '8px' }}>
                                {ratingSegs.map(seg => seg.count > 0 && (
                                  <div key={seg.key} title={`${seg.key}: ${seg.count}`}
                                    style={{ flex: seg.count, background: seg.color, minWidth: '4px' }} />
                                ))}
                              </div>
                              {/* Count labels */}
                              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                                {ratingSegs.map(seg => (
                                  <div key={seg.key} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                    <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: seg.color, display: 'inline-block', flexShrink: 0 }} />
                                    <span style={{ fontSize: '11px', color: '#64748b' }}>{seg.key}</span>
                                    <span style={{ fontSize: '12px', fontWeight: 700, color: seg.count > 0 ? seg.color : '#1e293b' }}>{seg.count}</span>
                                  </div>
                                ))}
                              </div>
                            </>
                          )}
                        </div>

                        {/* Consensus badge */}
                        {f.recommendation && (
                          <div style={{ textAlign: 'center', padding: '10px 18px', borderRadius: '10px', background: `${recColors[f.recommendation] ?? '#64748b'}12`, border: `1px solid ${recColors[f.recommendation] ?? '#64748b'}35` }}>
                            <div style={{ fontSize: '18px', fontWeight: 800, color: recColors[f.recommendation] ?? '#94a3b8', whiteSpace: 'nowrap' }}>
                              {recLabel[f.recommendation] ?? f.recommendation.toUpperCase()}
                            </div>
                            {starScore != null && (
                              <div style={{ fontSize: '12px', marginTop: '4px' }}>
                                {[1,2,3,4,5].map(i => (
                                  <span key={i} style={{ color: i <= Math.round(starScore) ? '#facc15' : '#1e293b', fontSize: '14px' }}>★</span>
                                ))}
                                <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px' }}>
                                  Mean score: {recMean?.toFixed(2)}
                                </div>
                                <div style={{ fontSize: '9px', color: '#334155', marginTop: '1px' }}>
                                  1.0 = Strong Buy · 5.0 = Sell
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Price target range visualization */}
                      {hasTargets && (
                        <div>
                          <div style={{ fontSize: '10px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '10px' }}>
                            Price Target Range
                            {upside != null && (
                              <span style={{ marginLeft: '10px', color: upside >= 0 ? '#4ade80' : '#f87171', fontWeight: 700, textTransform: 'none', letterSpacing: 0 }}>
                                {upside >= 0 ? '+' : ''}{upside.toFixed(1)}% to mean target
                              </span>
                            )}
                          </div>

                          {/* Range bar with price markers */}
                          <div style={{ position: 'relative', height: '40px', marginBottom: '4px' }}>
                            {/* Background bar */}
                            <div style={{ position: 'absolute', top: '18px', left: 0, right: 0, height: '4px', background: '#1e293b', borderRadius: '2px' }} />
                            {/* Filled bar: low → high */}
                            {toBarPct(tLow!) != null && toBarPct(tHigh!) != null && (
                              <div style={{
                                position: 'absolute', top: '18px', height: '4px', borderRadius: '2px',
                                left: `${toBarPct(tLow!)}%`,
                                width: `${toBarPct(tHigh!)! - toBarPct(tLow!)!}%`,
                                background: 'linear-gradient(90deg,#ef4444,#facc15,#22c55e)',
                              }} />
                            )}
                            {/* Markers */}
                            {[
                              { price: tLow,  label: `Low\n$${tLow!.toFixed(2)}`,   color: '#ef4444', size: 8 },
                              { price: tMed,  label: `Med\n$${tMed?.toFixed(2)}`,   color: '#facc15', size: 8 },
                              { price: tMean, label: `Mean\n$${tMean?.toFixed(2)}`, color: '#818cf8', size: 10 },
                              { price: tHigh, label: `High\n$${tHigh!.toFixed(2)}`, color: '#22c55e', size: 8 },
                              { price: curPrice, label: `Now\n$${curPrice?.toFixed(2)}`, color: '#f1f5f9', size: 12 },
                            ].filter(m => m.price != null).map(m => {
                              const pct = toBarPct(m.price!);
                              if (pct == null) return null;
                              const lines = m.label.split('\n');
                              return (
                                <div key={m.label} style={{ position: 'absolute', left: `${pct}%`, top: 0, transform: 'translateX(-50%)', textAlign: 'center', width: '48px', marginLeft: '-24px' }}>
                                  <div style={{ fontSize: '9px', color: m.color, fontWeight: 700, lineHeight: 1.2, whiteSpace: 'nowrap', marginBottom: '2px' }}>
                                    {lines[0]}
                                  </div>
                                  <div style={{
                                    width: `${m.size}px`, height: `${m.size}px`,
                                    borderRadius: '50%', background: m.color,
                                    margin: '0 auto',
                                    border: m.price === curPrice ? '2px solid #fff' : 'none',
                                    boxShadow: m.price === curPrice ? `0 0 6px ${m.color}` : 'none',
                                  }} />
                                  <div style={{ fontSize: '9px', color: m.color, fontWeight: 600, marginTop: '2px', whiteSpace: 'nowrap' }}>
                                    {lines[1]}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Buy zone + Sell / Target zone */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                        {/* BUY ZONE */}
                        <div style={{ borderRadius: '10px', padding: '12px 14px', background: 'rgba(34,197,94,0.06)', border: '1px solid rgba(34,197,94,0.2)' }}>
                          <div style={{ fontSize: '10px', fontWeight: 800, color: '#22c55e', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
                            ↓ Buy Zone
                          </div>
                          {buyLower != null && buyUpper != null ? (
                            <div style={{ fontSize: '20px', fontWeight: 800, color: '#4ade80', marginBottom: '6px' }}>
                              ${buyLower.toFixed(2)} – ${buyUpper.toFixed(2)}
                            </div>
                          ) : (
                            <div style={{ fontSize: '13px', color: '#475569', marginBottom: '6px' }}>See support levels</div>
                          )}
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                            {tLow != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Analyst low target: <span style={{ color: '#4ade80' }}>${tLow.toFixed(2)}</span></div>}
                            {nearestSupport != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Nearest support: <span style={{ color: '#4ade80' }}>${nearestSupport.toFixed(2)}</span></div>}
                            {curPrice != null && tMean != null && curPrice > tMean && (
                              <div style={{ fontSize: '11px', color: '#fb923c', marginTop: '4px' }}>⚠ Above analyst consensus — consider waiting for pullback</div>
                            )}
                            {curPrice != null && tMean != null && curPrice <= tMean && upside != null && (
                              <div style={{ fontSize: '11px', color: '#4ade80', marginTop: '4px' }}>+{upside.toFixed(1)}% upside to mean target</div>
                            )}
                          </div>
                        </div>

                        {/* SELL / TARGET ZONE */}
                        <div style={{ borderRadius: '10px', padding: '12px 14px', background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)' }}>
                          <div style={{ fontSize: '10px', fontWeight: 800, color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
                            ↑ Sell / Target Zone
                          </div>
                          {sellLower != null ? (
                            <div style={{ fontSize: '20px', fontWeight: 800, color: '#f87171', marginBottom: '6px' }}>
                              ${sellLower.toFixed(2)}{sellUpper != null ? ` – $${sellUpper.toFixed(2)}` : ''}
                            </div>
                          ) : (
                            <div style={{ fontSize: '13px', color: '#475569', marginBottom: '6px' }}>See resistance levels</div>
                          )}
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                            {tMean != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Analyst mean target: <span style={{ color: '#f87171' }}>${tMean.toFixed(2)}</span></div>}
                            {tHigh != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Bull case (high): <span style={{ color: '#f87171' }}>${tHigh.toFixed(2)}</span></div>}
                            {fairPrice != null && <div style={{ fontSize: '11px', color: '#64748b' }}>K-Score fair value: <span style={{ color: '#818cf8' }}>${fairPrice.toFixed(2)}</span></div>}
                            {nearestResistance != null && <div style={{ fontSize: '11px', color: '#64748b' }}>Nearest resistance: <span style={{ color: '#fb923c' }}>${nearestResistance.toFixed(2)}</span></div>}
                          </div>
                        </div>
                      </div>

                    </div>
                  </div>
                );
              })()}

            </div>
          </div>
        );
      })()}

      {/* AI Chat Panel */}
      <div style={{ borderRadius: '12px', border: '1px solid rgba(167,139,250,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
        <div style={{ height: '3px', background: 'linear-gradient(90deg,#a78bfa,#c4b5fd,#a78bfa)' }} />
        <button
          onClick={() => setAiOpen(o => !o)}
          style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '14px 20px', background: 'transparent', border: 'none', cursor: 'pointer',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '15px' }}>🤖</span>
            <span style={{ fontSize: '14px', fontWeight: 700, color: '#c4b5fd' }}>
              Ask AI about {symbol}
            </span>
            {isAiConfigured() && (
              <span style={{
                fontSize: '10px', padding: '1px 7px', borderRadius: '999px',
                background: 'rgba(167,139,250,0.15)', color: '#a78bfa', fontWeight: 700,
              }}>
                {getAiProviderLabel()}
              </span>
            )}
            {!isAiConfigured() && (
              <span style={{ fontSize: '11px', color: '#475569' }}>— configure in Settings</span>
            )}
          </div>
          <span style={{ color: '#475569', fontSize: '12px', transform: aiOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>▼</span>
        </button>

        {aiOpen && (
          <div style={{ borderTop: '1px solid #1e293b' }}>
            {!isAiConfigured() ? (
              <div style={{ padding: '20px', textAlign: 'center', fontSize: '13px', color: '#475569' }}>
                No AI provider configured.{' '}
                <a href="/settings" style={{ color: '#a78bfa', textDecoration: 'none' }}>Go to Settings → AI Assistant</a>
                {' '}to set up Claude or DeepSeek.
              </div>
            ) : (
              <>
                {/* Suggested questions */}
                {aiMessages.length === 0 && (
                  <div style={{ padding: '12px 16px', display: 'flex', gap: '6px', flexWrap: 'wrap', borderBottom: '1px solid #1e293b' }}>
                    {[
                      `Should I buy ${symbol} now?`,
                      `What are the key risks?`,
                      `Summarise the latest news`,
                      `What does the K-Score mean?`,
                    ].map(q => (
                      <button
                        key={q}
                        onClick={() => { setAiInput(q); }}
                        style={{
                          fontSize: '11px', padding: '4px 10px', borderRadius: '6px',
                          background: 'rgba(167,139,250,0.1)', border: '1px solid rgba(167,139,250,0.2)',
                          color: '#a78bfa', cursor: 'pointer',
                        }}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                )}

                {/* Message history */}
                {aiMessages.length > 0 && (
                  <div style={{ maxHeight: '360px', overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {aiMessages.map((m, i) => (
                      <div key={i} style={{
                        display: 'flex', flexDirection: m.role === 'user' ? 'row-reverse' : 'row', gap: '8px', alignItems: 'flex-start',
                      }}>
                        <div style={{
                          maxWidth: '80%', padding: '10px 14px', borderRadius: '10px', fontSize: '13px', lineHeight: 1.6,
                          background: m.role === 'user' ? 'rgba(167,139,250,0.15)' : 'rgba(255,255,255,0.04)',
                          border: m.role === 'user' ? '1px solid rgba(167,139,250,0.3)' : '1px solid #1e293b',
                          color: m.role === 'user' ? '#c4b5fd' : '#cbd5e1',
                          whiteSpace: 'pre-wrap',
                        }}>
                          {m.content}
                        </div>
                      </div>
                    ))}
                    {aiLoading && (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                        <div style={{ padding: '10px 14px', borderRadius: '10px', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', color: '#475569', fontSize: '13px' }}>
                          ⟳ Thinking…
                        </div>
                      </div>
                    )}
                    {aiError && (
                      <div style={{ padding: '8px 12px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '12px' }}>
                        {aiError}
                      </div>
                    )}
                    <div ref={aiBottomRef} />
                  </div>
                )}

                {/* Input */}
                <div style={{ padding: '12px 16px', borderTop: '1px solid #1e293b', display: 'flex', gap: '8px' }}>
                  <input
                    value={aiInput}
                    onChange={e => setAiInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAiMessage(); } }}
                    placeholder={`Ask anything about ${symbol}…`}
                    style={{
                      flex: 1, background: '#0f172a', border: '1px solid #1e293b',
                      borderRadius: '8px', padding: '9px 12px', fontSize: '13px',
                      color: '#e2e8f0', outline: 'none',
                    }}
                  />
                  <button
                    onClick={sendAiMessage}
                    disabled={aiLoading || !aiInput.trim()}
                    style={{
                      padding: '9px 18px', borderRadius: '8px', fontSize: '13px', fontWeight: 700,
                      cursor: aiLoading || !aiInput.trim() ? 'not-allowed' : 'pointer',
                      background: aiLoading || !aiInput.trim() ? '#1e293b' : 'linear-gradient(135deg,#7c3aed,#a78bfa)',
                      border: 'none', color: aiLoading || !aiInput.trim() ? '#475569' : '#fff',
                      transition: 'all 0.15s',
                    }}
                  >
                    Send
                  </button>
                  {aiMessages.length > 0 && (
                    <button
                      onClick={() => { setAiMessages([]); setAiError(''); }}
                      style={{ padding: '9px 12px', borderRadius: '8px', background: 'transparent', border: '1px solid #1e293b', color: '#475569', cursor: 'pointer', fontSize: '12px' }}
                    >
                      Clear
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>

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

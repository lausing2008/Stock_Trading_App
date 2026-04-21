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
      const res = await api.trainAll();
      setTrainAllState('done');
      setTrainAllMsg(`✓ Scheduled ${res.count} models — ready in ~2–5 min`);
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

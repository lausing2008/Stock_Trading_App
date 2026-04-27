'use client';
import { useState, useMemo, useCallback } from 'react';
import useSWR, { mutate as globalMutate } from 'swr';
import Link from 'next/link';
import { api, type Stock, type WatchlistItem, type RankingRow, type LatestPrice, type SignalSummary, type MarketIndex } from '@/lib/api';
import AddStockModal from '@/components/AddStockModal';

const SECTOR_COLOR: Record<string, { text: string; bg: string }> = {
  Technology:               { text: '#60a5fa', bg: 'rgba(30,58,138,0.3)' },
  Financial:                { text: '#34d399', bg: 'rgba(6,78,59,0.3)'   },
  'Consumer Cyclical':      { text: '#fb923c', bg: 'rgba(124,45,18,0.3)' },
  'Communication Services': { text: '#c084fc', bg: 'rgba(76,29,149,0.3)' },
  Healthcare:               { text: '#2dd4bf', bg: 'rgba(19,78,74,0.3)'  },
  Energy:                   { text: '#facc15', bg: 'rgba(113,63,18,0.3)' },
  'Consumer Defensive':     { text: '#cbd5e1', bg: 'rgba(30,41,59,0.5)'  },
  Industrials:              { text: '#22d3ee', bg: 'rgba(21,94,117,0.3)' },
};

function signalFromScore(score: number | undefined) {
  if (score == null) return null;
  if (score >= 65) return { label: 'BUY',  color: '#4ade80', bg: 'rgba(34,197,94,0.1)',  border: 'rgba(34,197,94,0.3)'  };
  if (score >= 40) return { label: 'HOLD', color: '#facc15', bg: 'rgba(250,204,21,0.1)', border: 'rgba(250,204,21,0.3)' };
  return               { label: 'SELL', color: '#f87171', bg: 'rgba(239,68,68,0.1)',  border: 'rgba(239,68,68,0.3)'  };
}

function scoreColor(score: number) {
  if (score >= 70) return '#4ade80';
  if (score >= 50) return '#facc15';
  return '#f87171';
}

type SortKey = 'symbol' | 'score' | 'sector' | 'market';
type MarketFilter = 'all' | 'US' | 'HK';

/* ── Market status helpers ─────────────────────────────────── */
function getMarketStatus() {
  const now = new Date();
  const utcMin = now.getUTCHours() * 60 + now.getUTCMinutes();
  const day = now.getUTCDay(); // 0=Sun 6=Sat
  const weekday = day >= 1 && day <= 5;

  // US: NYSE 9:30–16:00 ET. Use UTC-4 (EDT Apr–Nov, close enough).
  const etMin = ((utcMin - 4 * 60) + 1440) % 1440;
  const usOpen = weekday && etMin >= 9 * 60 + 30 && etMin < 16 * 60;
  const usPreMkt = weekday && etMin >= 4 * 60 && etMin < 9 * 60 + 30;

  // HK: HKEX 9:30–12:00 & 13:00–16:00 HKT (UTC+8)
  const hktMin = (utcMin + 8 * 60) % 1440;
  const hkOpen = weekday && (
    (hktMin >= 9 * 60 + 30 && hktMin < 12 * 60) ||
    (hktMin >= 13 * 60 && hktMin < 16 * 60)
  );
  const hkLunch = weekday && hktMin >= 12 * 60 && hktMin < 13 * 60;

  return { usOpen, usPreMkt, hkOpen, hkLunch };
}

function StatusBadge({ open, pre, lunch }: { open: boolean; pre?: boolean; lunch?: boolean }) {
  const label = open ? 'Open' : lunch ? 'Lunch' : pre ? 'Pre-mkt' : 'Closed';
  const color = open ? '#4ade80' : lunch ? '#facc15' : pre ? '#818cf8' : '#475569';
  const bg    = open ? 'rgba(34,197,94,0.12)' : lunch ? 'rgba(250,204,21,0.1)' : pre ? 'rgba(129,140,248,0.12)' : 'rgba(71,85,105,0.15)';
  return (
    <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px', color, background: bg, letterSpacing: '0.04em' }}>
      ● {label}
    </span>
  );
}

function MarketOverview({ indices, signals }: { indices: MarketIndex[]; signals: SignalSummary[] }) {
  const { usOpen, usPreMkt, hkOpen, hkLunch } = getMarketStatus();
  const us = indices.filter(i => i.market === 'US');
  const hk = indices.filter(i => i.market === 'HK');

  // Signal distribution from tracked stocks
  const sigCounts = { BUY: 0, HOLD: 0, WAIT: 0, SELL: 0 };
  for (const s of signals) {
    if (s.signal in sigCounts) sigCounts[s.signal as keyof typeof sigCounts]++;
  }
  const total = signals.length;

  function IndexTile({ idx }: { idx: MarketIndex }) {
    const up = (idx.change_pct ?? 0) >= 0;
    const isVix = idx.ticker === '^VIX';
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1px', minWidth: '90px' }}>
        <div style={{ fontSize: '10px', color: '#475569', fontWeight: 600 }}>{idx.name}</div>
        <div style={{ fontSize: '14px', fontWeight: 800, color: '#f1f5f9' }}>
          {idx.price != null ? idx.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'}
        </div>
        {idx.change_pct != null && (
          <div style={{ fontSize: '11px', fontWeight: 700, color: isVix ? (up ? '#f87171' : '#4ade80') : (up ? '#4ade80' : '#f87171') }}>
            {up ? '▲' : '▼'} {Math.abs(idx.change_pct).toFixed(2)}%
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: '10px' }}>

      {/* US Markets */}
      <div style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0b1120', padding: '12px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
          <span style={{ fontSize: '12px', fontWeight: 700, color: '#60a5fa' }}>🇺🇸 US Markets</span>
          <StatusBadge open={usOpen} pre={usPreMkt} />
        </div>
        <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
          {us.map(i => <IndexTile key={i.ticker} idx={i} />)}
        </div>
      </div>

      {/* HK Markets */}
      <div style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0b1120', padding: '12px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
          <span style={{ fontSize: '12px', fontWeight: 700, color: '#f472b6' }}>🇭🇰 HK Markets</span>
          <StatusBadge open={hkOpen} lunch={hkLunch} />
        </div>
        <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
          {hk.map(i => <IndexTile key={i.ticker} idx={i} />)}
        </div>
      </div>

      {/* Portfolio pulse */}
      {total > 0 && (
        <div style={{ borderRadius: '10px', border: '1px solid #1e293b', background: '#0b1120', padding: '12px 16px', minWidth: '160px' }}>
          <div style={{ fontSize: '12px', fontWeight: 700, color: '#94a3b8', marginBottom: '10px' }}>Portfolio Pulse</div>
          {/* Stacked bar */}
          <div style={{ display: 'flex', height: '6px', borderRadius: '3px', overflow: 'hidden', marginBottom: '8px' }}>
            {[
              { key: 'BUY',  color: '#22c55e' },
              { key: 'HOLD', color: '#facc15' },
              { key: 'WAIT', color: '#fb923c' },
              { key: 'SELL', color: '#ef4444' },
            ].map(({ key, color }) => {
              const count = sigCounts[key as keyof typeof sigCounts];
              return count > 0 ? (
                <div key={key} style={{ flex: count, background: color }} title={`${key}: ${count}`} />
              ) : null;
            })}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px' }}>
            {[
              { key: 'BUY',  color: '#4ade80', label: 'Buy'  },
              { key: 'HOLD', color: '#facc15', label: 'Hold' },
              { key: 'WAIT', color: '#fb923c', label: 'Wait' },
              { key: 'SELL', color: '#f87171', label: 'Sell' },
            ].map(({ key, color, label }) => (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: color, flexShrink: 0, display: 'inline-block' }} />
                <span style={{ fontSize: '10px', color: '#475569' }}>{label}</span>
                <span style={{ fontSize: '11px', fontWeight: 700, color }}>{sigCounts[key as keyof typeof sigCounts]}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const { data: stocks, error, mutate: mutateStocks } = useSWR<Stock[]>('stocks', () => api.listStocks());
  const { data: watchlist, mutate: mutateWatchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());
  const { data: rankingsData, mutate: mutateRankings } = useSWR<{ rankings: RankingRow[] }>('rankings-all', () => api.rankings());
  const { data: pricesData, mutate: mutatePrices } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalsData, mutate: mutateSignals } = useSWR<SignalSummary[]>('signals-all', () => api.allSignals());
  const { data: marketData } = useSWR<MarketIndex[]>('market-overview', () => api.marketOverview(), { refreshInterval: 60_000 });

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [search, setSearch] = useState('');
  const [market, setMarket] = useState<MarketFilter>('all');
  const [sort, setSort] = useState<SortKey>('symbol');
  const [refreshing, setRefreshing] = useState(false);
  const [trainState, setTrainState] = useState<null | 'running' | 'done' | 'error'>(null);
  const [trainInfo, setTrainInfo] = useState<{ ingestCount?: number; trainCount?: number; error?: string } | null>(null);

  const watchedSet = new Set(watchlist?.map(w => w.symbol) ?? []);
  const rankMap = useMemo(() => {
    const m: Record<string, RankingRow> = {};
    for (const r of rankingsData?.rankings ?? []) m[r.symbol] = r;
    return m;
  }, [rankingsData]);

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

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([mutateStocks(), mutateWatchlist(), mutateRankings(), mutatePrices(), mutateSignals()]);
    setRefreshing(false);
  }, [mutateStocks, mutateWatchlist, mutateRankings, mutatePrices, mutateSignals]);

  async function handleTrainAll() {
    if (trainState === 'running') return;
    setTrainState('running');
    setTrainInfo(null);
    try {
      const syms = watchlist?.map(w => w.symbol) ?? [];
      if (syms.length === 0) { setTrainState(null); return; }
      // Step 1: ingest latest price data
      const ingestRes = await api.ingest(syms);
      // Step 2: refresh UI with newly ingested prices
      await Promise.all([mutatePrices(), mutateRankings()]);
      // Step 3: schedule ML training for all
      const trainRes = await api.trainAll();
      setTrainInfo({ ingestCount: ingestRes.symbols ?? syms.length, trainCount: trainRes.count });
      setTrainState('done');
      // Signals update after models finish (~2-5 min); do a lazy refresh
      setTimeout(() => mutateSignals(), 5000);
    } catch (err) {
      setTrainInfo({ error: err instanceof Error ? err.message : 'Unknown error' });
      setTrainState('error');
    }
  }

  async function handleDelete(e: React.MouseEvent, symbol: string) {
    e.preventDefault();
    if (confirmDelete !== symbol) { setConfirmDelete(symbol); return; }
    setDeleting(symbol);
    setConfirmDelete(null);
    try {
      await api.removeFromWatchlist(symbol);
      mutateWatchlist();
    } finally { setDeleting(null); }
  }

  async function handleAdded(symbol: string) {
    try { await api.addToWatchlist(symbol); } catch {}
    await mutateWatchlist();
    setTimeout(() => { mutateStocks(); globalMutate('rankings-all'); globalMutate('latest-prices'); }, 1500);
  }

  const filtered = useMemo(() => {
    if (!stocks || !watchlist) return [];
    let list = stocks.filter(s => {
      if (!watchedSet.has(s.symbol)) return false;
      const q = search.toLowerCase();
      if (q && !s.symbol.toLowerCase().includes(q) && !s.name.toLowerCase().includes(q)) return false;
      if (market !== 'all' && s.market !== market) return false;
      return true;
    });
    list = [...list].sort((a, b) => {
      if (sort === 'symbol') return a.symbol.localeCompare(b.symbol);
      if (sort === 'score')  return (rankMap[b.symbol]?.score ?? 0) - (rankMap[a.symbol]?.score ?? 0);
      if (sort === 'sector') return (a.sector ?? '').localeCompare(b.sector ?? '');
      if (sort === 'market') return a.market.localeCompare(b.market);
      return 0;
    });
    return list;
  }, [stocks, search, market, sort, showWatchedOnly, rankMap, watchedSet]);

  const usCount  = stocks?.filter(s => watchedSet.has(s.symbol) && s.market === 'US').length ?? 0;
  const hkCount  = stocks?.filter(s => watchedSet.has(s.symbol) && s.market === 'HK').length ?? 0;
  const topRanked = rankingsData?.rankings.filter(r => watchedSet.has(r.symbol)).reduce(
    (best, r) => (!best || r.score > best.score) ? r : best,
    null as RankingRow | null,
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* Stats + action bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px', fontSize: '14px' }}>
          <div>
            <span style={{ fontSize: '24px', fontWeight: 700 }}>{watchedSet.size}</span>
            <span style={{ color: '#475569', marginLeft: '6px' }}>stocks</span>
          </div>
          <div style={{ color: '#334155' }}>|</div>
          <div style={{ display: 'flex', gap: '16px', color: '#64748b' }}>
            <span><span style={{ fontWeight: 600, color: '#e2e8f0' }}>{usCount}</span> US</span>
            <span><span style={{ fontWeight: 600, color: '#e2e8f0' }}>{hkCount}</span> HK</span>
          </div>
          {topRanked && (
            <>
              <div style={{ color: '#334155' }}>|</div>
              <div style={{ fontSize: '12px', color: '#64748b' }}>
                Top:{' '}
                <Link href={`/stock/${topRanked.symbol}`} style={{ color: '#818cf8' }}>{topRanked.symbol}</Link>
                <span style={{ marginLeft: '4px', fontWeight: 700, color: scoreColor(topRanked.score) }}>{topRanked.score.toFixed(0)}</span>
              </div>
            </>
          )}
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '7px 14px', borderRadius: '6px',
              border: '1px solid rgba(148,163,184,0.15)',
              background: 'rgba(255,255,255,0.03)',
              color: refreshing ? '#818cf8' : '#64748b',
              cursor: refreshing ? 'not-allowed' : 'pointer',
              fontSize: '13px', transition: 'all 0.15s',
            }}
          >
            <span style={{ display: 'inline-block', fontSize: '15px', lineHeight: 1, animation: refreshing ? 'spin 0.8s linear infinite' : 'none' }}>↻</span>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <button
            onClick={handleTrainAll}
            disabled={trainState === 'running'}
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '7px 14px', borderRadius: '6px',
              border: '1px solid rgba(99,102,241,0.25)',
              background: trainState === 'running' ? 'rgba(99,102,241,0.15)' : 'rgba(99,102,241,0.08)',
              color: trainState === 'running' ? '#818cf8' : '#6366f1',
              cursor: trainState === 'running' ? 'not-allowed' : 'pointer',
              fontSize: '13px', fontWeight: 500, transition: 'all 0.15s',
            }}
          >
            <span style={{ display: 'inline-block', fontSize: '13px', lineHeight: 1, animation: trainState === 'running' ? 'spin 0.8s linear infinite' : 'none' }}>
              {trainState === 'running' ? '↻' : '⚡'}
            </span>
            {trainState === 'running' ? 'Training…' : 'Train All'}
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '7px 16px', borderRadius: '6px',
              background: 'linear-gradient(135deg, #4f46e5, #6366f1)',
              border: 'none', color: '#ffffff',
              fontSize: '13px', fontWeight: 600, cursor: 'pointer',
              boxShadow: '0 4px 12px rgba(99,102,241,0.3)',
            }}
          >
            <span style={{ fontSize: '16px', lineHeight: 1 }}>+</span> Add Stock
          </button>
        </div>
      </div>

      {/* Train All progress panel */}
      {trainState && trainState !== null && (
        <div style={{
          borderRadius: '10px', padding: '14px 16px',
          border: `1px solid ${trainState === 'error' ? 'rgba(239,68,68,0.3)' : trainState === 'done' ? 'rgba(34,197,94,0.25)' : 'rgba(99,102,241,0.25)'}`,
          background: trainState === 'error' ? 'rgba(239,68,68,0.06)' : trainState === 'done' ? 'rgba(34,197,94,0.06)' : 'rgba(99,102,241,0.06)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {trainState === 'running' && (
                <div style={{ fontSize: '13px', fontWeight: 600, color: '#818cf8' }}>
                  ⚡ Ingesting prices &amp; scheduling ML training…
                </div>
              )}
              {trainState === 'done' && trainInfo && (
                <>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: '#4ade80' }}>
                    ✓ Pipeline scheduled successfully
                  </div>
                  <div style={{ fontSize: '12px', color: '#475569' }}>
                    Ingested {trainInfo.ingestCount} stocks · Queued {trainInfo.trainCount} ML training jobs
                    · Models ready in ~2–5 min
                  </div>
                </>
              )}
              {trainState === 'error' && (
                <div style={{ fontSize: '13px', color: '#f87171' }}>✕ {trainInfo?.error ?? 'Pipeline failed'}</div>
              )}
            </div>
            <button
              onClick={() => setTrainState(null)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#475569', fontSize: '14px', padding: '2px 6px' }}
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Market Overview */}
      {marketData && marketData.length > 0 && (
        <MarketOverview
          indices={marketData}
          signals={(signalsData ?? []).filter(s => watchedSet.has(s.symbol))}
        />
      )}

      {/* Search + Filter + Sort bar */}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search symbol or name…"
          style={{
            borderRadius: '6px', background: '#1e293b', border: '1px solid #334155',
            padding: '6px 12px', fontSize: '13px', color: '#e2e8f0',
            outline: 'none', width: '220px',
          }}
        />

        <div style={{ display: 'flex', borderRadius: '6px', border: '1px solid #334155', overflow: 'hidden', fontSize: '12px', fontWeight: 500 }}>
          {(['all', 'US', 'HK'] as MarketFilter[]).map(m => (
            <button
              key={m}
              onClick={() => setMarket(m)}
              style={{
                padding: '6px 12px', border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                background: market === m ? '#4f46e5' : 'transparent',
                color: market === m ? '#ffffff' : '#94a3b8',
              }}
            >
              {m === 'all' ? 'All' : m}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginLeft: 'auto', fontSize: '12px', color: '#475569' }}>
          Sort:
          {(['symbol', 'score', 'sector', 'market'] as SortKey[]).map(s => (
            <button
              key={s}
              onClick={() => setSort(s)}
              style={{
                padding: '4px 8px', borderRadius: '4px', border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                background: sort === s ? '#334155' : 'transparent',
                color: sort === s ? '#e2e8f0' : '#475569',
              }}
            >
              {s === 'score' ? 'K-Score' : s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div style={{ color: '#94a3b8', fontSize: '13px' }}>
          Backend unreachable. Start the stack via <code>make up</code>.
        </div>
      )}

      {watchedSet.size > 0 && filtered.length !== watchedSet.size && (
        <div style={{ fontSize: '11px', color: '#475569' }}>{filtered.length} of {watchedSet.size} stocks</div>
      )}

      {/* Stock grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '12px' }}>
        {filtered.map(s => {
          const rank   = rankMap[s.symbol];
          const lp     = priceMap[s.symbol];
          const realSig = signalMap[s.symbol];
          const sig = realSig
            ? {
                label: realSig.signal,
                color:  realSig.signal === 'BUY' ? '#4ade80' : realSig.signal === 'SELL' ? '#f87171' : realSig.signal === 'WAIT' ? '#fb923c' : '#facc15',
                bg:     realSig.signal === 'BUY' ? 'rgba(34,197,94,0.1)' : realSig.signal === 'SELL' ? 'rgba(239,68,68,0.1)' : realSig.signal === 'WAIT' ? 'rgba(251,146,60,0.1)' : 'rgba(250,204,21,0.1)',
                border: realSig.signal === 'BUY' ? 'rgba(34,197,94,0.3)' : realSig.signal === 'SELL' ? 'rgba(239,68,68,0.3)' : realSig.signal === 'WAIT' ? 'rgba(251,146,60,0.3)' : 'rgba(250,204,21,0.3)',
              }
            : signalFromScore(rank?.score);
          const sc     = SECTOR_COLOR[s.sector ?? ''];
          const changeUp = (lp?.change_pct ?? 0) >= 0;

          return (
            <Link
              key={s.symbol}
              href={`/stock/${s.symbol}`}
              style={{
                position: 'relative', display: 'block',
                borderRadius: '10px', border: '1px solid #1e293b',
                background: '#0f172a', padding: '14px 14px 12px',
                textDecoration: 'none', transition: 'all 0.15s',
              }}
              className="stock-card"
            >
              {/* Delete / remove button */}
              {confirmDelete === s.symbol ? (
                <div
                  onClick={e => e.preventDefault()}
                  style={{ position: 'absolute', top: '8px', left: '8px', display: 'flex', gap: '4px', zIndex: 2 }}
                >
                  <button
                    onClick={(e) => handleDelete(e, s.symbol)}
                    disabled={deleting === s.symbol}
                    style={{
                      padding: '3px 8px', borderRadius: '5px', fontSize: '11px', fontWeight: 700,
                      background: '#ef4444', border: 'none', color: '#fff', cursor: 'pointer',
                    }}
                  >
                    {deleting === s.symbol ? '…' : 'Remove?'}
                  </button>
                  <button
                    onClick={(e) => { e.preventDefault(); setConfirmDelete(null); }}
                    style={{
                      padding: '3px 7px', borderRadius: '5px', fontSize: '11px',
                      background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', cursor: 'pointer',
                    }}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={(e) => handleDelete(e, s.symbol)}
                  style={{
                    position: 'absolute', top: '10px', left: '10px',
                    background: 'none', border: 'none', cursor: 'pointer',
                    fontSize: '13px', lineHeight: 1, padding: '2px',
                    color: '#1e293b', transition: 'color 0.15s',
                  }}
                  className="delete-btn"
                  title="Remove from watchlist"
                >
                  ✕
                </button>
              )}

              {/* Symbol + price row */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', paddingLeft: '20px', marginBottom: '4px' }}>
                <div style={{ fontWeight: 700, fontSize: '17px', letterSpacing: '-0.01em' }}>{s.symbol}</div>
                {lp ? (
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontWeight: 600, fontSize: '14px', color: '#f1f5f9' }}>
                      {lp.currency === 'USD' ? '$' : ''}{lp.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                    {lp.change_pct != null && (
                      <div style={{ fontSize: '11px', fontWeight: 600, color: changeUp ? '#4ade80' : '#f87171' }}>
                        {changeUp ? '▲' : '▼'} {Math.abs(lp.change_pct).toFixed(2)}%
                      </div>
                    )}
                  </div>
                ) : (
                  rank?.score != null && (
                    <div style={{ textAlign: 'right', fontSize: '14px', fontWeight: 700, color: scoreColor(rank.score) }}>
                      {rank.score.toFixed(0)}
                      <div style={{ fontSize: '10px', fontWeight: 400, color: '#475569' }}>K-Score</div>
                    </div>
                  )
                )}
              </div>

              {/* Company name */}
              <div style={{ marginBottom: '8px', overflow: 'hidden' }}>
                <div style={{ fontSize: '12px', color: '#94a3b8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.name}
                </div>
                {s.name_zh && (
                  <div style={{ fontSize: '11px', color: '#64748b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: '1px' }}>
                    {s.name_zh}
                  </div>
                )}
              </div>

              {/* Sector + signal row */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
                  {s.sector && sc && (
                    <span style={{
                      fontSize: '10px', fontWeight: 600, padding: '2px 7px', borderRadius: '4px',
                      color: sc.text, background: sc.bg, whiteSpace: 'nowrap',
                    }}>
                      {s.sector}
                    </span>
                  )}
                  <span style={{ fontSize: '10px', color: '#334155', whiteSpace: 'nowrap' }}>
                    {s.market} · {s.exchange}
                  </span>
                </div>

                {/* Signal badge */}
                {sig && (
                  <span style={{
                    fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '4px',
                    color: sig.color, background: sig.bg, border: `1px solid ${sig.border}`,
                    letterSpacing: '0.04em', whiteSpace: 'nowrap',
                  }}>
                    {sig.label}
                  </span>
                )}
              </div>

              {/* Bottom row: K-Score + fair price */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                <span style={{ fontSize: '11px', color: '#334155' }}>
                  {rank?.score != null && (
                    <span style={{ fontWeight: 600, marginRight: '4px', color: scoreColor(rank.score) }}>
                      K {rank.score.toFixed(0)}
                    </span>
                  )}
                </span>
                {rank?.fair_price != null && (
                  <span style={{ fontSize: '11px', color: '#818cf8', fontWeight: 600 }}>
                    Fair ${rank.fair_price.toFixed(2)}
                  </span>
                )}
              </div>
            </Link>
          );
        })}
      </div>

      {/* Empty state */}
      {filtered.length === 0 && !error && stocks && watchlist && (
        <div style={{ textAlign: 'center', padding: '64px 0', color: '#475569' }}>
          {watchedSet.size === 0 ? (
            <>
              <div style={{ fontSize: '36px', marginBottom: '12px' }}>📋</div>
              <div style={{ fontSize: '15px', color: '#64748b' }}>Your watchlist is empty.</div>
              <div style={{ fontSize: '12px', marginTop: '6px' }}>Click + Add Stock to start tracking stocks.</div>
            </>
          ) : (
            <>
              <div style={{ fontSize: '36px', marginBottom: '12px' }}>🔍</div>
              <div>No stocks match your filter.</div>
              <button
                onClick={() => { setSearch(''); setMarket('all'); }}
                style={{ marginTop: '8px', color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontSize: '13px' }}
              >
                Clear filters
              </button>
            </>
          )}
        </div>
      )}

      {showAddModal && (
        <AddStockModal onClose={() => setShowAddModal(false)} onAdded={handleAdded} />
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .stock-card:hover { border-color: #334155 !important; background: #0f1829 !important; }
        .stock-card:hover .delete-btn { color: #475569 !important; }
        .delete-btn:hover { color: #ef4444 !important; }
      `}</style>
    </div>
  );
}

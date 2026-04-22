'use client';
import { useState, useMemo, useCallback } from 'react';
import useSWR, { mutate as globalMutate } from 'swr';
import Link from 'next/link';
import { api, type Stock, type WatchlistItem, type RankingRow, type LatestPrice, type SignalSummary } from '@/lib/api';
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

export default function Home() {
  const { data: stocks, error, mutate: mutateStocks } = useSWR<Stock[]>('stocks', () => api.listStocks());
  const { data: watchlist, mutate: mutateWatchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());
  const { data: rankingsData, mutate: mutateRankings } = useSWR<{ rankings: RankingRow[] }>('rankings-all', () => api.rankings());
  const { data: pricesData, mutate: mutatePrices } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalsData, mutate: mutateSignals } = useSWR<SignalSummary[]>('signals-all', () => api.allSignals());

  const [watchPending, setWatchPending] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [search, setSearch] = useState('');
  const [market, setMarket] = useState<MarketFilter>('all');
  const [sort, setSort] = useState<SortKey>('symbol');
  const [showWatchedOnly, setShowWatchedOnly] = useState(false);
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
      const syms = stocks?.map(s => s.symbol) ?? [];
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

  async function toggleWatch(e: React.MouseEvent, symbol: string) {
    e.preventDefault();
    if (watchPending) return;
    setWatchPending(symbol);
    try {
      if (watchedSet.has(symbol)) await api.removeFromWatchlist(symbol);
      else await api.addToWatchlist(symbol);
      mutateWatchlist();
    } finally { setWatchPending(null); }
  }

  function handleAdded(symbol: string) {
    setTimeout(() => { mutateStocks(); globalMutate('rankings-all'); globalMutate('latest-prices'); }, 1500);
  }

  const filtered = useMemo(() => {
    if (!stocks) return [];
    let list = stocks.filter(s => {
      const q = search.toLowerCase();
      if (q && !s.symbol.toLowerCase().includes(q) && !s.name.toLowerCase().includes(q)) return false;
      if (market !== 'all' && s.market !== market) return false;
      if (showWatchedOnly && !watchedSet.has(s.symbol)) return false;
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

  const usCount  = stocks?.filter(s => s.market === 'US').length ?? 0;
  const hkCount  = stocks?.filter(s => s.market === 'HK').length ?? 0;
  const topRanked = rankingsData?.rankings.reduce(
    (best, r) => (!best || r.score > best.score) ? r : best,
    null as RankingRow | null,
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* Stats + action bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px', fontSize: '14px' }}>
          <div>
            <span style={{ fontSize: '24px', fontWeight: 700 }}>{stocks?.length ?? '—'}</span>
            <span style={{ color: '#475569', marginLeft: '6px' }}>stocks</span>
          </div>
          <div style={{ color: '#334155' }}>|</div>
          <div style={{ display: 'flex', gap: '16px', color: '#64748b' }}>
            <span><span style={{ fontWeight: 600, color: '#e2e8f0' }}>{usCount}</span> US</span>
            <span><span style={{ fontWeight: 600, color: '#e2e8f0' }}>{hkCount}</span> HK</span>
            <span><span style={{ fontWeight: 600, color: '#e2e8f0' }}>{watchedSet.size}</span> watching</span>
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

        <button
          onClick={() => setShowWatchedOnly(v => !v)}
          style={{
            display: 'flex', alignItems: 'center', gap: '4px',
            padding: '6px 12px', borderRadius: '6px', cursor: 'pointer', fontSize: '12px', fontWeight: 500,
            border: showWatchedOnly ? '1px solid #4f46e5' : '1px solid #334155',
            background: showWatchedOnly ? 'rgba(79,70,229,0.15)' : 'transparent',
            color: showWatchedOnly ? '#818cf8' : '#94a3b8', transition: 'all 0.15s',
          }}
        >
          ★ Watching
        </button>

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

      {stocks && filtered.length !== stocks.length && (
        <div style={{ fontSize: '11px', color: '#475569' }}>{filtered.length} of {stocks.length} stocks</div>
      )}

      {/* Stock grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '12px' }}>
        {filtered.map(s => {
          const rank   = rankMap[s.symbol];
          const lp     = priceMap[s.symbol];
          const realSig = signalMap[s.symbol];
          const sig = realSig
            ? { label: realSig.signal, color: realSig.signal === 'BUY' ? '#4ade80' : realSig.signal === 'SELL' ? '#f87171' : '#facc15', bg: realSig.signal === 'BUY' ? 'rgba(34,197,94,0.1)' : realSig.signal === 'SELL' ? 'rgba(239,68,68,0.1)' : 'rgba(250,204,21,0.1)', border: realSig.signal === 'BUY' ? 'rgba(34,197,94,0.3)' : realSig.signal === 'SELL' ? 'rgba(239,68,68,0.3)' : 'rgba(250,204,21,0.3)' }
            : signalFromScore(rank?.score);
          const isWatched = watchedSet.has(s.symbol);
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
              {/* Watch star */}
              <button
                onClick={(e) => toggleWatch(e, s.symbol)}
                disabled={watchPending === s.symbol}
                style={{
                  position: 'absolute', top: '10px', right: '10px',
                  background: 'none', border: 'none', cursor: 'pointer',
                  fontSize: '16px', lineHeight: 1, padding: '2px',
                  color: isWatched ? '#818cf8' : '#334155',
                  transition: 'color 0.15s',
                }}
                title={isWatched ? 'Remove from watchlist' : 'Add to watchlist'}
              >
                {isWatched ? '★' : '☆'}
              </button>

              {/* Symbol + price row */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', paddingRight: '24px', marginBottom: '4px' }}>
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
              <div style={{ fontSize: '12px', color: '#94a3b8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginBottom: '8px' }}>
                {s.name}
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
      {filtered.length === 0 && !error && stocks && (
        <div style={{ textAlign: 'center', padding: '64px 0', color: '#475569' }}>
          <div style={{ fontSize: '36px', marginBottom: '12px' }}>🔍</div>
          <div>No stocks match your filter.</div>
          <button
            onClick={() => { setSearch(''); setMarket('all'); setShowWatchedOnly(false); }}
            style={{ marginTop: '8px', color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontSize: '13px' }}
          >
            Clear filters
          </button>
        </div>
      )}

      {showAddModal && (
        <AddStockModal onClose={() => setShowAddModal(false)} onAdded={handleAdded} />
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .stock-card:hover { border-color: #334155 !important; background: #0f1829 !important; }
      `}</style>
    </div>
  );
}

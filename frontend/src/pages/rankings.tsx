import { useState, useMemo } from 'react';
import useSWR from 'swr';
import RankingsTable from '@/components/RankingsTable';
import MarketClosedBanner from '@/components/MarketClosedBanner';
import PeerCompareDrawer from '@/components/PeerCompareDrawer';
import { api, type Stock, type WatchlistItem, type WatchlistMeta, type LatestPrice, type RankingRow, type SignalSummary, type TradePlan } from '@/lib/api';
import { getSignalStyle } from '@/lib/settings';

export default function RankingsPage() {
  const [market, setMarket] = useState<'US' | 'HK' | ''>('');
  const [filterWatchlistId, setFilterWatchlistId] = useState<number | null>(null);
  const [compareSymbols, setCompareSymbols] = useState<Set<string>>(new Set());
  const [compareOpen, setCompareOpen] = useState(false);

  function toggleCompare(symbol: string) {
    setCompareSymbols(prev => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else if (next.size < 4) next.add(symbol);
      return next;
    });
  }
  const { data, error, isLoading } = useSWR(
    `rankings-${market}`,
    () => api.rankings(market || undefined),
  );
  const { data: watchlists } = useSWR<WatchlistMeta[]>('watchlists', () => api.listWatchlists());
  const { data: watchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());
  const { data: filteredWatchlistItems } = useSWR<WatchlistItem[]>(
    filterWatchlistId != null ? `watchlist-${filterWatchlistId}` : null,
    () => api.listWatchlist(filterWatchlistId!),
  );
  const { data: stocks } = useSWR<Stock[]>('stocks', () => api.listStocks());
  const watchedSet = useMemo(() => new Set(watchlist?.map(w => w.symbol) ?? []), [watchlist]);
  const filteredSet = useMemo(
    () => filterWatchlistId != null ? new Set(filteredWatchlistItems?.map(w => w.symbol) ?? []) : watchedSet,
    [filterWatchlistId, filteredWatchlistItems, watchedSet],
  );
  const { data: pricesData } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalList } = useSWR<SignalSummary[]>('signals-' + getSignalStyle(), () => api.allSignals(getSignalStyle()), { refreshInterval: 300_000 });
  const { data: boardData } = useSWR<TradePlan[]>('board', () => api.listBoard());
  const boardSet = useMemo(() => new Set(boardData?.filter(p => p.stage !== 'closed').map(p => p.symbol) ?? []), [boardData]);

  const { data: sectorEtf } = useSWR('sector-rotation-etf', () => api.sectorRotationEtf(), { revalidateOnFocus: false });

  const priceMap = useMemo(() => {
    const m: Record<string, LatestPrice> = {};
    for (const p of pricesData ?? []) m[p.symbol] = p;
    return m;
  }, [pricesData]);

  const signalMap = useMemo(() => {
    const m: Record<string, SignalSummary> = {};
    for (const s of signalList ?? []) m[s.symbol] = s;
    return m;
  }, [signalList]);

  const rows = useMemo((): RankingRow[] => {
    if (!data) return [];
    const rankedSymbols = new Set(data.rankings.map(r => r.symbol));

    const ranked = data.rankings.filter(r => filteredSet.has(r.symbol));

    const unranked: RankingRow[] = (stocks ?? [])
      .filter(s => filteredSet.has(s.symbol) && !rankedSymbols.has(s.symbol))
      .filter(s => !market || s.market === market.toUpperCase())
      .map(s => ({
        symbol: s.symbol,
        name: s.name,
        name_zh: s.name_zh,
        market: s.market,
        sector: s.sector ?? null,
        score: null,
        technical: null,
        momentum: null,
        value: null,
        growth: null,
        volatility: null,
        fair_price: null,
        relative_strength: null,
      }));

    return [...ranked, ...unranked];
  }, [data, filteredSet, stocks, market]);

  const compareRows = useMemo(
    () => rows.filter(r => compareSymbols.has(r.symbol)),
    [rows, compareSymbols],
  );

  return (
    <div>
      <MarketClosedBanner />
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">Rankings</h1>
        <div className="flex gap-2 text-sm items-center">
          {compareSymbols.size >= 2 && (
            <button
              onClick={() => setCompareOpen(true)}
              style={{
                padding: '4px 14px', borderRadius: 6, fontSize: 12, fontWeight: 700,
                border: '1px solid #6366f1', background: 'rgba(99,102,241,0.15)',
                color: '#818cf8', cursor: 'pointer',
              }}
            >
              Compare ({compareSymbols.size})
            </button>
          )}
          {compareSymbols.size > 0 && (
            <button
              onClick={() => setCompareSymbols(new Set())}
              style={{
                padding: '4px 10px', borderRadius: 6, fontSize: 11,
                border: '1px solid #334155', background: 'transparent',
                color: '#475569', cursor: 'pointer',
              }}
            >
              Clear
            </button>
          )}
          <button
            onClick={() => {
              if (!rows.length) return;
              const headers = ['Symbol','Name','Market','Sector','K-Score','Technical','Momentum','Value','Growth','Volatility','Signal','Bullish%','Confidence','Price','Change%'];
              const csvRows = rows.map(r => {
                const lp = priceMap[r.symbol];
                const sig = signalMap[r.symbol];
                return [
                  r.symbol, r.name, r.market, r.sector ?? '',
                  r.score?.toFixed(1) ?? '', r.technical?.toFixed(1) ?? '',
                  r.momentum?.toFixed(1) ?? '', r.value?.toFixed(1) ?? '',
                  r.growth?.toFixed(1) ?? '', r.volatility?.toFixed(1) ?? '',
                  sig?.signal ?? '', sig?.bullish_probability != null ? (sig.bullish_probability * 100).toFixed(1) : '',
                  sig?.confidence?.toFixed(1) ?? '',
                  lp?.price?.toFixed(2) ?? '', lp?.change_pct?.toFixed(2) ?? '',
                ].map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',');
              });
              const csv = [headers.join(','), ...csvRows].join('\n');
              const a = document.createElement('a');
              a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
              a.download = `rankings-${new Date().toISOString().slice(0,10)}.csv`;
              a.click();
            }}
            style={{ padding: '4px 12px', borderRadius: 6, fontSize: 11, border: '1px solid #334155', background: '#0b1420', color: '#64748b', cursor: 'pointer' }}
          >
            ↓ CSV
          </button>
          <select
            value={filterWatchlistId ?? ''}
            onChange={e => setFilterWatchlistId(e.target.value === '' ? null : Number(e.target.value))}
            style={{
              padding: '4px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
              border: '1px solid #334155', background: '#0f172a', color: filterWatchlistId ? '#a5b4fc' : '#475569',
              appearance: 'none', WebkitAppearance: 'none',
            }}
          >
            <option value="">All watchlists</option>
            {(watchlists ?? []).map(w => (
              <option key={w.id} value={w.id}>{w.name} ({w.item_count})</option>
            ))}
          </select>
          {(['', 'US', 'HK'] as const).map((m) => (
            <button
              key={m || 'all'}
              onClick={() => setMarket(m)}
              className={`px-3 py-1 rounded border border-slate-800 ${market === m ? 'bg-indigo-600' : 'bg-slate-900'}`}
            >
              {m || 'All'}
            </button>
          ))}
        </div>
      </div>
      {/* Sector Rotation Heatmap (RES-4) */}
      {sectorEtf && !sectorEtf.error && sectorEtf.sectors.length > 0 && (
        <div style={{ marginBottom: 24, background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '14px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8' }}>Sector Rotation — 1m vs SPY</div>
            <div style={{ fontSize: 11, color: '#334155' }}>
              SPY 1m: <span style={{ color: (sectorEtf.spy_1m ?? 0) >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>{sectorEtf.spy_1m != null ? `${sectorEtf.spy_1m >= 0 ? '+' : ''}${sectorEtf.spy_1m.toFixed(1)}%` : '—'}</span>
            </div>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {sectorEtf.sectors.map(s => {
              const bg = s.status === 'leading' ? 'rgba(34,197,94,0.18)' : s.status === 'in-line' ? 'rgba(99,102,241,0.12)' : s.status === 'lagging' ? 'rgba(245,158,11,0.15)' : 'rgba(239,68,68,0.15)';
              const border = s.status === 'leading' ? 'rgba(34,197,94,0.35)' : s.status === 'in-line' ? 'rgba(99,102,241,0.3)' : s.status === 'lagging' ? 'rgba(245,158,11,0.35)' : 'rgba(239,68,68,0.35)';
              const textColor = s.status === 'leading' ? '#4ade80' : s.status === 'in-line' ? '#818cf8' : s.status === 'lagging' ? '#fbbf24' : '#f87171';
              return (
                <div key={s.etf} style={{ background: bg, border: `1px solid ${border}`, borderRadius: 6, padding: '6px 10px', minWidth: 90 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0' }}>{s.etf}</div>
                  <div style={{ fontSize: 10, color: '#64748b', marginBottom: 3 }}>{s.sector.length > 18 ? s.sector.slice(0, 17) + '…' : s.sector}</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: textColor }}>
                    {s.ret_1m != null ? `${s.ret_1m >= 0 ? '+' : ''}${s.ret_1m.toFixed(1)}%` : '—'}
                  </div>
                  <div style={{ fontSize: 9, color: '#475569' }}>
                    1w: {s.ret_1w != null ? `${s.ret_1w >= 0 ? '+' : ''}${s.ret_1w.toFixed(1)}%` : '—'} · 3m: {s.ret_3m != null ? `${s.ret_3m >= 0 ? '+' : ''}${s.ret_3m.toFixed(1)}%` : '—'}
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 10, color: '#334155', marginTop: 8, display: 'flex', gap: 16 }}>
            {[['leading', '#4ade80', '≥SPY+3%'], ['in-line', '#818cf8', 'within 3%'], ['lagging', '#fbbf24', 'SPY−1% to −5%'], ['distributing', '#f87171', '≤SPY−5%']].map(([label, color]) => (
              <span key={label}><span style={{ color: color as string }}>■</span> {label}</span>
            ))}
          </div>
        </div>
      )}

      {isLoading && <div>Loading…</div>}
      {error && <div className="text-slate-300">Unable to load rankings.</div>}
      {data && (
        <RankingsTable
          rows={rows}
          prices={priceMap}
          signals={signalMap}
          selectedSymbols={compareSymbols}
          onToggleCompare={toggleCompare}
          boardSet={boardSet}
        />
      )}
      {compareOpen && compareRows.length >= 2 && (
        <PeerCompareDrawer
          rows={compareRows}
          prices={priceMap}
          onClose={() => setCompareOpen(false)}
        />
      )}
    </div>
  );
}

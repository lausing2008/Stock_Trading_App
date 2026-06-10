import { useState, useMemo } from 'react';
import useSWR from 'swr';
import RankingsTable from '@/components/RankingsTable';
import MarketClosedBanner from '@/components/MarketClosedBanner';
import PeerCompareDrawer from '@/components/PeerCompareDrawer';
import { api, type Stock, type WatchlistItem, type LatestPrice, type RankingRow, type SignalSummary, type TradePlan } from '@/lib/api';
import { getSignalStyle } from '@/lib/settings';

export default function RankingsPage() {
  const [market, setMarket] = useState<'US' | 'HK' | ''>('');
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
  const { data: watchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());
  const { data: stocks } = useSWR<Stock[]>('stocks', () => api.listStocks());
  const watchedSet = useMemo(() => new Set(watchlist?.map(w => w.symbol) ?? []), [watchlist]);
  const { data: pricesData } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalList } = useSWR<SignalSummary[]>('signals-' + getSignalStyle(), () => api.allSignals(getSignalStyle()), { refreshInterval: 300_000 });
  const { data: boardData } = useSWR<TradePlan[]>('board', () => api.listBoard());
  const boardSet = useMemo(() => new Set(boardData?.filter(p => p.stage !== 'closed').map(p => p.symbol) ?? []), [boardData]);

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

    // Ranked stocks filtered to user's watchlist
    const ranked = data.rankings.filter(r => watchedSet.has(r.symbol));

    // Watchlisted stocks not yet in rankings (insufficient price history)
    const unranked: RankingRow[] = (stocks ?? [])
      .filter(s => watchedSet.has(s.symbol) && !rankedSymbols.has(s.symbol))
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
  }, [data, watchedSet, stocks, market]);

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

import { useState, useMemo } from 'react';
import useSWR from 'swr';
import RankingsTable from '@/components/RankingsTable';
import { api, type Stock, type WatchlistItem, type LatestPrice, type RankingRow, type SignalSummary } from '@/lib/api';

export default function RankingsPage() {
  const [market, setMarket] = useState<'US' | 'HK' | ''>('');
  const { data, error, isLoading } = useSWR(
    `rankings-${market}`,
    () => api.rankings(market || undefined),
  );
  const { data: watchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());
  const { data: stocks } = useSWR<Stock[]>('stocks', () => api.listStocks());
  const watchedSet = useMemo(() => new Set(watchlist?.map(w => w.symbol) ?? []), [watchlist]);
  const { data: pricesData } = useSWR<LatestPrice[]>('latest-prices', () => api.latestPrices(), { refreshInterval: 60_000 });
  const { data: signalList } = useSWR<SignalSummary[]>('signals-all', () => api.allSignals(), { refreshInterval: 300_000 });

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

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">Rankings</h1>
        <div className="flex gap-2 text-sm">
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
      {data && <RankingsTable rows={rows} prices={priceMap} signals={signalMap} />}
    </div>
  );
}

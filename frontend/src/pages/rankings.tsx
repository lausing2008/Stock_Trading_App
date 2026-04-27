import { useState, useMemo } from 'react';
import useSWR from 'swr';
import RankingsTable from '@/components/RankingsTable';
import { api, type WatchlistItem } from '@/lib/api';

export default function RankingsPage() {
  const [market, setMarket] = useState<'US' | 'HK' | ''>('');
  const { data, error, isLoading } = useSWR(
    `rankings-${market}`,
    () => api.rankings(market || undefined),
  );
  const { data: watchlist } = useSWR<WatchlistItem[]>('watchlist', () => api.listWatchlist());

  const watchedSet = useMemo(() => new Set(watchlist?.map(w => w.symbol) ?? []), [watchlist]);

  const filteredRankings = useMemo(
    () => data ? { rankings: data.rankings.filter(r => watchedSet.has(r.symbol)) } : undefined,
    [data, watchedSet],
  );

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
      {filteredRankings && <RankingsTable rows={filteredRankings.rankings} />}
    </div>
  );
}

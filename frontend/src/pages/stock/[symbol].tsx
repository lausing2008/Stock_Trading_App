import { useRouter } from 'next/router';
import useSWR from 'swr';
import dynamic from 'next/dynamic';
import SignalCard from '@/components/SignalCard';
import { api, type Overview } from '@/lib/api';

const PriceChart = dynamic(() => import('@/components/PriceChart'), { ssr: false });

export default function StockDetail() {
  const r = useRouter();
  const symbol = (r.query.symbol as string) ?? '';
  const { data, error, isLoading } = useSWR<Overview>(
    symbol ? `overview-${symbol}` : null,
    () => api.overview(symbol),
  );

  if (isLoading) return <div>Loading…</div>;
  if (error || !data) return <div className="text-slate-300">Error loading {symbol}.</div>;

  const ranking = data.ranking as { score: number; technical: number; momentum: number; value: number; growth: number; volatility: number; fair_price: number | null } | null;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">{symbol}</h1>
        <div className="text-slate-400">{(data.price as { name?: string })?.name}</div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '2fr 1fr' }}>
        <div>
          {data.prices && data.prices.length > 0 ? (
            <PriceChart prices={data.prices} indicators={data.indicators} />
          ) : (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              No price data. Trigger ingestion: POST /admin/ingest &#123;"symbols":["{symbol}"]&#125;
            </div>
          )}
        </div>
        <div className="grid gap-4">
          {data.signal && <SignalCard signal={data.signal} />}
          {ranking && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              <h3 className="text-lg font-semibold mb-2">K-Score</h3>
              <div className="text-2xl font-bold">{ranking.score?.toFixed(1)}</div>
              <div className="text-xs text-slate-500 mt-3 grid grid-cols-2 gap-3">
                <div>Technical: {ranking.technical?.toFixed(0)}</div>
                <div>Momentum: {ranking.momentum?.toFixed(0)}</div>
                <div>Value: {ranking.value?.toFixed(0)}</div>
                <div>Growth: {ranking.growth?.toFixed(0)}</div>
                <div>Volatility: {ranking.volatility?.toFixed(0)}</div>
                <div>Fair Price: {ranking.fair_price?.toFixed(2) ?? '—'}</div>
              </div>
            </div>
          )}
          {data.patterns?.patterns && data.patterns.patterns.length > 0 && (
            <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
              <h3 className="text-lg font-semibold mb-2">Patterns</h3>
              {data.patterns.patterns.map((p, i) => (
                <div key={i} className="text-sm text-slate-300">
                  {p.name} <span className="text-xs text-slate-500">({(p.confidence * 100).toFixed(0)}%)</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

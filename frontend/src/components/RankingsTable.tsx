import type { RankingRow, LatestPrice } from '@/lib/api';

type PriceMap = Record<string, LatestPrice>;

export default function RankingsTable({ rows, prices }: { rows: RankingRow[]; prices?: PriceMap }) {
  return (
    <div className="overflow-x-auto rounded-md border border-slate-800">
      <table className="w-full text-left text-sm text-slate-200">
        <thead className="bg-slate-800/60 text-slate-300">
          <tr>
            <th className="px-3 py-2">#</th>
            <th className="px-3 py-2">Symbol</th>
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Market</th>
            <th className="px-3 py-2 text-right">Price</th>
            <th className="px-3 py-2 text-right">Change</th>
            <th className="px-3 py-2 text-right">K-Score</th>
            <th className="px-3 py-2 text-right">Fair Price</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const lp = prices?.[r.symbol];
            const changeUp = (lp?.change_pct ?? 0) >= 0;
            return (
              <tr key={r.symbol} className="border-t border-slate-800 hover:bg-slate-900">
                <td className="px-3 py-2 text-slate-500">{i + 1}</td>
                <td className="px-3 py-2 font-medium">
                  <a href={`/stock/${r.symbol}`} className="text-indigo-400 hover:underline">{r.symbol}</a>
                </td>
                <td className="px-3 py-2">
                  <div>{r.name}</div>
                  {r.name_zh && <div className="text-xs text-slate-500 mt-0.5">{r.name_zh}</div>}
                </td>
                <td className="px-3 py-2 text-slate-400">{r.market}</td>
                <td className="px-3 py-2 text-right font-semibold">
                  {lp ? `$${lp.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
                </td>
                <td className="px-3 py-2 text-right text-xs font-semibold" style={{ color: lp?.change_pct != null ? (changeUp ? '#4ade80' : '#f87171') : '#475569' }}>
                  {lp?.change_pct != null ? `${changeUp ? '▲' : '▼'} ${Math.abs(lp.change_pct).toFixed(2)}%` : '—'}
                </td>
                <td className="px-3 py-2 text-right font-semibold">{r.score.toFixed(1)}</td>
                <td className="px-3 py-2 text-right text-indigo-400">{r.fair_price?.toFixed(2) ?? '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

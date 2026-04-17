import useSWR from 'swr';
import Link from 'next/link';
import { api, type Stock } from '@/lib/api';

export default function Home() {
  const { data, error } = useSWR<Stock[]>('stocks', () => api.listStocks());

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Universe</h1>
      {error && <div className="text-slate-300">Backend unreachable. Start the stack via <code>make up</code>.</div>}
      <div className="grid grid-cols-3 gap-4">
        {data?.map((s) => (
          <Link
            key={s.symbol}
            href={`/stock/${s.symbol}`}
            className="rounded-md border border-slate-800 bg-slate-900 p-4 hover:bg-slate-800"
          >
            <div className="flex items-center justify-between">
              <div className="font-bold text-lg">{s.symbol}</div>
              <div className="text-xs text-slate-500">{s.market} / {s.exchange}</div>
            </div>
            <div className="text-sm text-slate-300">{s.name}</div>
            <div className="text-xs text-slate-500 mt-3">{s.sector}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}

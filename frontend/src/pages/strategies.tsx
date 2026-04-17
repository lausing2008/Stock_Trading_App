import StrategyBuilder from '@/components/StrategyBuilder';
import useSWR from 'swr';
import { api } from '@/lib/api';

export default function Strategies() {
  const { data } = useSWR('strategies', () => api.listStrategies());
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Strategies</h1>
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <StrategyBuilder />
        <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
          <h3 className="text-lg font-semibold mb-2">Saved</h3>
          {(data ?? []).map((s) => (
            <div key={s.id} className="border-t border-slate-800 py-1 text-sm">
              <span className="font-medium">{s.name}</span>
              <span className="text-slate-500"> — #{s.id}</span>
            </div>
          ))}
          {!data?.length && <div className="text-sm text-slate-500">No strategies yet.</div>}
        </div>
      </div>
    </div>
  );
}

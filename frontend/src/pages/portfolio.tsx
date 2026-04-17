import { useState } from 'react';
import { api, type PortfolioWeights } from '@/lib/api';

export default function PortfolioPage() {
  const [symbols, setSymbols] = useState('AAPL,MSFT,NVDA,GOOGL,AMZN');
  const [method, setMethod] = useState<'mean_variance' | 'risk_parity' | 'ai_allocation'>('mean_variance');
  const [result, setResult] = useState<PortfolioWeights | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    try {
      const r = await api.optimizePortfolio({
        symbols: symbols.split(',').map((s) => s.trim()).filter(Boolean),
        method,
        lookback_days: 365,
      });
      setResult(r);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Portfolio Optimizer</h1>
      <div className="rounded-md border border-slate-800 bg-slate-900 p-4 mb-4">
        <label className="text-sm text-slate-300">Symbols (comma-separated)</label>
        <input className="w-full rounded bg-slate-800 px-2 py-1 mb-3 mt-1" value={symbols} onChange={(e) => setSymbols(e.target.value)} />

        <label className="text-sm text-slate-300">Method</label>
        <select className="w-full rounded bg-slate-800 px-2 py-1 mb-3 mt-1" value={method} onChange={(e) => setMethod(e.target.value as typeof method)}>
          <option value="mean_variance">Mean Variance</option>
          <option value="risk_parity">Risk Parity</option>
          <option value="ai_allocation">AI Allocation (K-Score filter)</option>
        </select>

        <button className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-500" onClick={run} disabled={loading}>
          {loading ? 'Optimizing…' : 'Optimize'}
        </button>
      </div>

      {result && (
        <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-lg font-semibold mb-2">Allocation — {result.method}</h2>
          {Object.entries(result.weights).map(([sym, w]) => (
            <div key={sym} className="flex justify-between border-t border-slate-800 py-1">
              <span>{sym}</span>
              <span className="font-mono">{(w * 100).toFixed(1)}%</span>
            </div>
          ))}
          <div className="flex justify-between border-t border-slate-800 py-1 text-slate-400">
            <span>Cash</span>
            <span className="font-mono">{(result.cash * 100).toFixed(1)}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

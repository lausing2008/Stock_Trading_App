import type { Signal } from '@/lib/api';

const COLOR: Record<string, string> = { BUY: 'bg-green-600', SELL: 'bg-red-600', HOLD: 'bg-yellow-500' };

export default function SignalCard({ signal }: { signal: Signal }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-100">AI Signal</h3>
        <span className={`rounded px-2 py-0.5 text-sm font-bold text-white ${COLOR[signal.signal]}`}>
          {signal.signal}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm text-slate-300">
        <div>
          <div className="text-slate-500">Confidence</div>
          <div className="text-2xl font-semibold text-slate-100">{signal.confidence.toFixed(0)}</div>
        </div>
        <div>
          <div className="text-slate-500">Bullish Prob.</div>
          <div className="text-2xl font-semibold text-slate-100">{(signal.bullish_probability * 100).toFixed(1)}%</div>
        </div>
        <div>
          <div className="text-slate-500">Horizon</div>
          <div className="font-medium">{signal.horizon}</div>
        </div>
      </div>
    </div>
  );
}

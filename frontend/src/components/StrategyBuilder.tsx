'use client';
import { useState } from 'react';
import { api } from '@/lib/api';

type Cond = { feature: string; op: string; right: string };

const FEATURES = ['close', 'sma_20', 'sma_50', 'sma_200', 'rsi_14', 'macd', 'macd_signal', 'macd_hist'];
const OPS = ['<', '<=', '>', '>=', '==', 'crosses_above', 'crosses_below'];

export default function StrategyBuilder() {
  const [name, setName] = useState('My Strategy');
  const [entry, setEntry] = useState<Cond[]>([{ feature: 'rsi_14', op: '<', right: '30' }]);
  const [exitRules, setExit] = useState<Cond[]>([{ feature: 'rsi_14', op: '>', right: '70' }]);
  const [result, setResult] = useState<unknown>(null);

  function addCond(kind: 'entry' | 'exit') {
    const fresh = { feature: 'close', op: '>', right: 'sma_50' };
    if (kind === 'entry') setEntry((e) => [...e, fresh]);
    else setExit((e) => [...e, fresh]);
  }

  function toNode(conds: Cond[]) {
    const nodes = conds.map((c) => ({
      op: c.op,
      left: c.feature,
      right: isNaN(Number(c.right)) ? c.right : Number(c.right),
    }));
    return nodes.length === 1 ? nodes[0] : { op: 'and', nodes };
  }

  async function save() {
    const rule_dsl = { entry: toNode(entry), exit: toNode(exitRules) };
    const { id } = await api.createStrategy({ name, rule_dsl });
    const res = await api.backtest({ strategy_id: id, symbol: 'AAPL' });
    setResult(res);
  }

  return (
    <div className="rounded-md border border-slate-800 bg-slate-900 p-4 text-slate-100">
      <h3 className="mb-3 text-lg font-semibold">Strategy Builder</h3>
      <label className="mb-2 block text-sm text-slate-300">Name</label>
      <input
        className="mb-4 w-full rounded bg-slate-800 px-2 py-1"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />

      {(['entry', 'exit'] as const).map((kind) => {
        const rows = kind === 'entry' ? entry : exitRules;
        const setRows = kind === 'entry' ? setEntry : setExit;
        return (
          <div key={kind} className="mb-4">
            <div className="mb-1 text-sm font-medium text-slate-300">{kind === 'entry' ? 'Entry (AND)' : 'Exit (AND)'}</div>
            {rows.map((r, i) => (
              <div key={i} className="mb-1 flex gap-2">
                <select className="rounded bg-slate-800 px-2 py-1" value={r.feature}
                        onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, feature: e.target.value } : x)))}>
                  {FEATURES.map((f) => <option key={f}>{f}</option>)}
                </select>
                <select className="rounded bg-slate-800 px-2 py-1" value={r.op}
                        onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)))}>
                  {OPS.map((o) => <option key={o}>{o}</option>)}
                </select>
                <input className="w-32 rounded bg-slate-800 px-2 py-1" value={r.right}
                       onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, right: e.target.value } : x)))} />
              </div>
            ))}
            <button className="mt-1 rounded bg-slate-700 px-2 py-0.5 text-xs" onClick={() => addCond(kind)}>+ add</button>
          </div>
        );
      })}

      <button className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-500" onClick={save}>
        Save + Backtest on AAPL
      </button>

      {result != null && (
        <pre className="mt-3 max-h-64 overflow-auto rounded bg-slate-950 p-2 text-xs">
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
}

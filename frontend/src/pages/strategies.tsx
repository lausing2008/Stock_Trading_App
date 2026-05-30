'use client';
import { useState, useEffect } from 'react';
import useSWR from 'swr';
import { api, type Stock, type Backtest } from '@/lib/api';

type Cond = { feature: string; op: string; right: string };
type TradeRecord = { entry_ts: string; entry: number; exit_ts?: string; exit?: number; ret?: number };

interface Preset {
  key: string;
  label: string;
  icon: string;
  tagline: string;
  description: string;
  entry: Cond[];
  exit: Cond[];
}

const FEATURES = ['close', 'sma_20', 'sma_50', 'sma_200', 'rsi_14', 'macd', 'macd_signal', 'macd_hist'];
const OPS = ['<', '<=', '>', '>=', 'crosses_above', 'crosses_below'];

const OPP_TO_PRESET: Record<string, string> = {
  swing: 'swing', short: 'short', longterm: 'longterm', growth: 'growth', all: 'rsi_bounce', aisignal: 'ai_signal',
};

const PRESETS: Preset[] = [
  {
    key: 'swing',
    label: 'Swing Trade', icon: '📊', tagline: '5–30 day hold',
    description: 'RSI below 50 (not overbought) with price above SMA20 trend. Looks for setups where momentum can extend before an exit.',
    entry: [{ feature: 'rsi_14', op: '<', right: '50' }, { feature: 'close', op: '>', right: 'sma_20' }],
    exit:  [{ feature: 'rsi_14', op: '>', right: '65' }],
  },
  {
    key: 'short',
    label: 'Short-Term Momentum', icon: '⚡', tagline: '1–5 day breakout',
    description: 'Price crossing above the 20-day moving average signals short-term momentum. Exit immediately on reversal below SMA20.',
    entry: [{ feature: 'close', op: 'crosses_above', right: 'sma_20' }],
    exit:  [{ feature: 'close', op: 'crosses_below', right: 'sma_20' }],
  },
  {
    key: 'longterm',
    label: 'Long-Term Value', icon: '🏛️', tagline: '6–24 month horizon',
    description: 'Oversold RSI with price below SMA200 — a classic deep-value entry for patient investors. Exit when RSI signals overbought.',
    entry: [{ feature: 'rsi_14', op: '<', right: '40' }, { feature: 'close', op: '<', right: 'sma_200' }],
    exit:  [{ feature: 'rsi_14', op: '>', right: '65' }],
  },
  {
    key: 'growth',
    label: 'Growth Momentum', icon: '🚀', tagline: 'Ride the uptrend',
    description: 'Price above SMA50 with positive MACD histogram confirms an accelerating uptrend. Exit when histogram momentum fades.',
    entry: [{ feature: 'close', op: '>', right: 'sma_50' }, { feature: 'macd_hist', op: '>', right: '0' }],
    exit:  [{ feature: 'macd_hist', op: '<', right: '0' }],
  },
  {
    key: 'rsi_bounce',
    label: 'RSI Oversold Bounce', icon: '↩️', tagline: 'Mean reversion',
    description: 'Classic contrarian entry. RSI below 30 signals extreme selling pressure — buy the dip and exit at RSI 60 recovery.',
    entry: [{ feature: 'rsi_14', op: '<', right: '30' }],
    exit:  [{ feature: 'rsi_14', op: '>', right: '60' }],
  },
  {
    key: 'golden_cross',
    label: 'Golden Cross', icon: '✨', tagline: 'Long-term bullish trend',
    description: 'SMA50 crosses above SMA200 — one of the most reliable long-term bullish signals. Hold until the death cross reversal.',
    entry: [{ feature: 'sma_50', op: 'crosses_above', right: 'sma_200' }],
    exit:  [{ feature: 'sma_50', op: 'crosses_below', right: 'sma_200' }],
  },
  {
    key: 'death_cross',
    label: 'Death Cross Exit', icon: '☠️', tagline: 'Capital preservation',
    description: 'Hold long while price stays above SMA50, then exit immediately when SMA50 crosses below SMA200 — a major bear warning.',
    entry: [{ feature: 'close', op: '>', right: 'sma_50' }],
    exit:  [{ feature: 'sma_50', op: 'crosses_below', right: 'sma_200' }],
  },
  {
    key: 'macd_crossover',
    label: 'MACD Crossover', icon: '📈', tagline: 'Classic momentum signal',
    description: 'MACD line crossing above the signal line. One of the most widely used momentum confirmation patterns in technical analysis.',
    entry: [{ feature: 'macd', op: 'crosses_above', right: 'macd_signal' }],
    exit:  [{ feature: 'macd', op: 'crosses_below', right: 'macd_signal' }],
  },
  {
    key: 'sma50_breakout',
    label: 'SMA50 Breakout', icon: '🎯', tagline: 'Trend following',
    description: 'Price crossing above the 50-day SMA confirms a medium-term trend change. Ride the position while price holds above.',
    entry: [{ feature: 'close', op: 'crosses_above', right: 'sma_50' }],
    exit:  [{ feature: 'close', op: 'crosses_below', right: 'sma_50' }],
  },
  {
    key: 'mean_reversion',
    label: 'Mean Reversion', icon: '⚖️', tagline: 'Buy dips in uptrend',
    description: 'RSI below 35 with price under SMA50. Expects a bounce back toward the SMA20 mean on oversold conditions.',
    entry: [{ feature: 'rsi_14', op: '<', right: '35' }, { feature: 'close', op: '<', right: 'sma_50' }],
    exit:  [{ feature: 'close', op: '>', right: 'sma_20' }],
  },
  {
    key: 'ai_signal',
    label: 'AI Signal', icon: '🤖', tagline: 'Approximation of BUY signal',
    description: 'Mimics the AI engine\'s BUY signal: positive MACD histogram (momentum building), RSI not yet overbought, and price holding above SMA50 trend. Exit when momentum turns or RSI reaches overbought.',
    entry: [{ feature: 'macd_hist', op: '>', right: '0' }, { feature: 'rsi_14', op: '<', right: '62' }, { feature: 'close', op: '>', right: 'sma_50' }],
    exit:  [{ feature: 'macd_hist', op: '<', right: '0' }],
  },
];

function fromNode(node: any): Cond[] {
  if (!node) return [];
  if (node.op === 'and' && Array.isArray(node.nodes)) {
    return node.nodes
      .filter((n: any) => n.left && n.op)
      .map((n: any) => ({ feature: String(n.left), op: n.op, right: String(n.right) }));
  }
  if (node.left && node.op) {
    return [{ feature: String(node.left), op: node.op, right: String(node.right) }];
  }
  return [];
}

function toNode(conds: Cond[]): object {
  const nodes = conds.map((c) => ({
    op: c.op,
    left: c.feature,
    right: isNaN(Number(c.right)) ? c.right : Number(c.right),
  }));
  return nodes.length === 1 ? nodes[0] : { op: 'and', nodes };
}

function fmtPct(n: number, d = 1) {
  return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(d)}%`;
}

function fmtDate(iso: string) {
  return iso.split('T')[0];
}

function threeYearsAgo() {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 3);
  return d.toISOString().split('T')[0];
}

function today() {
  return new Date().toISOString().split('T')[0];
}

function EquityCurve({ data }: { data: { ts: string; equity: number }[] }) {
  if (!data || data.length < 2) return null;
  const values = data.map(d => d.equity);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const range = maxV - minV || 1;
  const W = 600, H = 100, PAD = 3;
  const pts = values
    .map((v, i) => `${(i / (values.length - 1)) * W},${H - PAD - ((v - minV) / range) * (H - PAD * 2)}`)
    .join(' ');
  const final = values[values.length - 1];
  const col = final >= 1 ? '#4ade80' : '#f87171';
  const baselineY = H - PAD - ((1 - minV) / range) * (H - PAD * 2);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: '160px' }} preserveAspectRatio="none">
      <defs>
        <linearGradient id="ecg" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={col} stopOpacity="0.28" />
          <stop offset="100%" stopColor={col} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={`0,${H} ${pts} ${W},${H}`} fill="url(#ecg)" />
      <polyline points={pts} fill="none" stroke={col} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      {minV < 1 && maxV > 1 && (
        <line x1="0" y1={baselineY} x2={W} y2={baselineY}
          stroke="#475569" strokeWidth="0.6" strokeDasharray="5,4" vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}

const inp: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #1e293b', borderRadius: '6px',
  padding: '7px 10px', fontSize: '12px', color: '#e2e8f0', outline: 'none',
};
const lbl: React.CSSProperties = {
  fontSize: '10px', color: '#64748b', fontWeight: 600, textTransform: 'uppercase',
  letterSpacing: '0.05em', display: 'block', marginBottom: '5px',
};

function retColor(n: number) {
  return n >= 0 ? '#4ade80' : '#f87171';
}

export default function StrategiesPage() {
  const { data: stocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks());
  const { data: saved, mutate: mutateSaved } = useSWR<{ id: number; name: string; description?: string }[]>(
    'strategies', () => api.listStrategies()
  );

  const [selectedPreset, setSelectedPreset] = useState('rsi_bounce');
  const [name, setName]         = useState('RSI Oversold Bounce');
  const [entry, setEntry]       = useState<Cond[]>([{ feature: 'rsi_14', op: '<', right: '30' }]);
  const [exitRules, setExit]    = useState<Cond[]>([{ feature: 'rsi_14', op: '>', right: '60' }]);
  const [symbol, setSymbol]     = useState('');
  const [startDate, setStart]   = useState(threeYearsAgo);
  const [endDate, setEnd]       = useState(today);
  const [running, setRunning]   = useState(false);
  const [result, setResult]     = useState<Backtest | null>(null);
  const [trades, setTrades]     = useState<TradeRecord[]>([]);
  const [runSymbol, setRunSym]  = useState('');
  const [error, setError]       = useState('');
  const [showAll, setShowAll]   = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [loading,  setLoading]  = useState<number | null>(null);
  const [saving,   setSaving]   = useState(false);
  const [savedOk,  setSavedOk]  = useState(false);

  // Load the current Opportunities strategy as default on mount
  useEffect(() => {
    const oppKey = typeof window !== 'undefined' ? localStorage.getItem('stockai_opp_strategy') : null;
    const presetKey = (oppKey && OPP_TO_PRESET[oppKey]) ? OPP_TO_PRESET[oppKey] : 'rsi_bounce';
    const p = PRESETS.find(x => x.key === presetKey);
    if (p) applyPreset(p, false);
  }, []);

  // Pick a default symbol once stocks are loaded
  useEffect(() => {
    if (stocks?.length && !symbol) {
      const preferred = ['AAPL', 'TSLA', 'NVDA', 'MSFT', 'AMZN'];
      const found = preferred.find(s => stocks.some(st => st.symbol === s));
      setSymbol(found ?? stocks[0].symbol);
    }
  }, [stocks, symbol]);

  function applyPreset(p: Preset, clearResult = true) {
    setSelectedPreset(p.key);
    setName(p.label);
    setEntry(p.entry.map(c => ({ ...c })));
    setExit(p.exit.map(c => ({ ...c })));
    if (clearResult) { setResult(null); setError(''); setTrades([]); }
  }

  function addCond(kind: 'entry' | 'exit') {
    const blank: Cond = { feature: 'rsi_14', op: '<', right: '50' };
    if (kind === 'entry') setEntry(e => [...e, { ...blank }]);
    else setExit(e => [...e, { ...blank }]);
  }

  function removeCond(kind: 'entry' | 'exit', idx: number) {
    if (kind === 'entry') setEntry(e => e.filter((_, i) => i !== idx));
    else setExit(e => e.filter((_, i) => i !== idx));
  }

  function updateCond(kind: 'entry' | 'exit', idx: number, patch: Partial<Cond>) {
    if (kind === 'entry') setEntry(e => e.map((c, i) => i === idx ? { ...c, ...patch } : c));
    else setExit(e => e.map((c, i) => i === idx ? { ...c, ...patch } : c));
  }

  async function runBacktest() {
    if (!symbol || entry.length === 0) return;
    setRunning(true);
    setError('');
    setResult(null);
    setTrades([]);
    setRunSym(symbol);
    setSavedOk(false);
    try {
      const rule_dsl: { entry: object; exit?: object } = { entry: toNode(entry) };
      if (exitRules.length > 0) rule_dsl.exit = toNode(exitRules);
      const res = await api.backtest({ rule_dsl, symbol, start: startDate, end: endDate });
      setResult(res);
      const raw = res as unknown as { trades: TradeRecord[] };
      setTrades(Array.isArray(raw.trades) ? raw.trades : []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Backtest failed — make sure this stock has enough price history.');
    } finally {
      setRunning(false);
    }
  }

  async function saveStrategy() {
    if (!result || entry.length === 0) return;
    setSaving(true);
    setSavedOk(false);
    try {
      const rule_dsl: { entry: object; exit?: object } = { entry: toNode(entry) };
      if (exitRules.length > 0) rule_dsl.exit = toNode(exitRules);
      await api.createStrategy({ name, rule_dsl });
      await mutateSaved();
      setSavedOk(true);
    } catch {}
    finally { setSaving(false); }
  }

  async function handleRetest(id: number) {
    setLoading(id);
    setError('');
    setResult(null);
    setTrades([]);
    setSavedOk(false);
    try {
      const s = await api.getStrategy(id);
      const dsl = s.rule_dsl as { entry: any; exit?: any };
      // Populate the form so the user can see what ran
      setEntry(fromNode(dsl.entry));
      setExit(fromNode(dsl.exit ?? null));
      setName(s.name.replace(/ — .+$/, ''));
      setSelectedPreset('');
      setRunSym(symbol);
      setRunning(true);
      // Run immediately with the fetched rule_dsl — avoids async state-update lag
      const res = await api.backtest({ rule_dsl: dsl, symbol, start: startDate, end: endDate });
      setResult(res);
      const raw = res as unknown as { trades: TradeRecord[] };
      setTrades(Array.isArray(raw.trades) ? raw.trades : []);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Backtest failed — make sure this stock has enough price history.');
    } finally {
      setLoading(null);
      setRunning(false);
    }
  }

  async function handleDelete(id: number) {
    setDeleting(id);
    try {
      await api.deleteStrategy(id);
      await mutateSaved();
    } catch {}
    finally { setDeleting(null); }
  }

  const displayTrades = showAll ? trades : trades.slice(-20);

  return (
    <div style={{ maxWidth: '1120px', margin: '0 auto' }}>

      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 800, color: '#f1f5f9', margin: '0 0 4px' }}>Strategy Backtester</h1>
        <p style={{ fontSize: '12px', color: '#475569', margin: 0 }}>
          Test rule-based entry/exit strategies on historical daily price data. Fees: 5 bps + 2 bps slippage per trade. Long-only, next-bar fill.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: '16px', alignItems: 'start' }}>

        {/* Left: Template list */}
        <div>
          <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '10px' }}>
            Templates
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {PRESETS.map(p => (
              <button
                key={p.key}
                onClick={() => applyPreset(p)}
                style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                  padding: '9px 12px', borderRadius: '8px', cursor: 'pointer', textAlign: 'left',
                  border: selectedPreset === p.key ? '1px solid rgba(99,102,241,0.6)' : '1px solid #1e293b',
                  background: selectedPreset === p.key ? 'rgba(79,70,229,0.15)' : 'rgba(255,255,255,0.02)',
                  transition: 'all 0.1s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
                  <span style={{ fontSize: '13px' }}>{p.icon}</span>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: selectedPreset === p.key ? '#c7d2fe' : '#94a3b8' }}>
                    {p.label}
                  </span>
                </div>
                <span style={{ fontSize: '10px', color: selectedPreset === p.key ? '#818cf8' : '#334155' }}>
                  {p.tagline}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Right: Builder */}
        <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
          <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
          <div style={{ padding: '20px 24px' }}>

            {/* Preset description */}
            {(() => {
              const p = PRESETS.find(x => x.key === selectedPreset);
              return p ? (
                <div style={{ marginBottom: '16px', padding: '10px 13px', borderRadius: '8px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.15)', fontSize: '12px', color: '#94a3b8', lineHeight: 1.6 }}>
                  {p.description}
                </div>
              ) : null;
            })()}

            {/* Strategy name */}
            <div style={{ marginBottom: '16px' }}>
              <label style={lbl}>Strategy Name</label>
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                style={{ ...inp, width: '100%', boxSizing: 'border-box' }}
              />
            </div>

            {/* Conditions */}
            {(['entry', 'exit'] as const).map(kind => {
              const conds = kind === 'entry' ? entry : exitRules;
              return (
                <div key={kind} style={{ marginBottom: '16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <label style={{ ...lbl, margin: 0 }}>
                      {kind === 'entry' ? 'Entry Conditions (AND)' : 'Exit Conditions (AND)'}
                    </label>
                    <button
                      onClick={() => addCond(kind)}
                      style={{ fontSize: '10px', color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}
                    >
                      + Add
                    </button>
                  </div>
                  {conds.length === 0 && (
                    <div style={{ fontSize: '11px', color: '#334155', fontStyle: 'italic', padding: '4px 0' }}>
                      {kind === 'exit' ? 'No exit rule — position held to end of period.' : 'No conditions set.'}
                    </div>
                  )}
                  {conds.map((c, i) => (
                    <div key={i} style={{ display: 'flex', gap: '6px', marginBottom: '6px', alignItems: 'center' }}>
                      <select value={c.feature} onChange={e => updateCond(kind, i, { feature: e.target.value })} style={inp}>
                        {FEATURES.map(f => <option key={f} value={f}>{f}</option>)}
                      </select>
                      <select value={c.op} onChange={e => updateCond(kind, i, { op: e.target.value })} style={{ ...inp, minWidth: '140px' }}>
                        {OPS.map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                      <input
                        value={c.right}
                        onChange={e => updateCond(kind, i, { right: e.target.value })}
                        style={{ ...inp, width: '90px' }}
                        placeholder="value or field"
                      />
                      <button
                        onClick={() => removeCond(kind, i)}
                        style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '14px', flexShrink: 0, padding: '2px 4px' }}
                        title="Remove"
                      >✕</button>
                    </div>
                  ))}
                </div>
              );
            })}

            {/* Stock picker + date range */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 140px 140px', gap: '12px', marginBottom: '16px' }}>
              <div>
                <label style={lbl}>Stock to Backtest</label>
                <select value={symbol} onChange={e => setSymbol(e.target.value)} style={{ ...inp, width: '100%' }}>
                  {(stocks ?? []).map(s => (
                    <option key={`${s.symbol}-${s.exchange}`} value={s.symbol}>
                      {s.symbol} — {s.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label style={lbl}>Start Date</label>
                <input type="date" value={startDate} onChange={e => setStart(e.target.value)}
                  style={{ ...inp, width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label style={lbl}>End Date</label>
                <input type="date" value={endDate} onChange={e => setEnd(e.target.value)}
                  style={{ ...inp, width: '100%', boxSizing: 'border-box' }} />
              </div>
            </div>

            {/* Run button */}
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
              <button
                onClick={runBacktest}
                disabled={running || !symbol || entry.length === 0}
                style={{
                  background: 'linear-gradient(135deg,#4f46e5,#6366f1)',
                  border: 'none', color: '#fff', padding: '10px 28px',
                  borderRadius: '8px', fontSize: '13px', fontWeight: 700,
                  cursor: (running || !symbol) ? 'not-allowed' : 'pointer',
                  opacity: (running || !symbol) ? 0.6 : 1,
                }}
              >
                {running ? '⏳ Running…' : '▶ Run Backtest'}
              </button>
              {running && (
                <span style={{ fontSize: '11px', color: '#475569' }}>Fetching price history &amp; computing trades…</span>
              )}
            </div>

            {error && (
              <div style={{ marginTop: '10px', fontSize: '12px', color: '#f87171', padding: '8px 12px', borderRadius: '6px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
                {error}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Results */}
      {result && (
        <div style={{ marginTop: '20px', borderRadius: '12px', border: '1px solid #1e293b', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>

          {/* Result header */}
          <div style={{ padding: '16px 22px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
            <div>
              <div style={{ fontSize: '15px', fontWeight: 800, color: '#f1f5f9' }}>{name} — {runSymbol}</div>
              <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>{startDate} → {endDate} · {result.n_trades} trades</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div style={{ fontSize: '28px', fontWeight: 900, color: retColor(result.total_return) }}>
                {fmtPct(result.total_return)}
              </div>
              <button
                onClick={saveStrategy}
                disabled={saving || savedOk}
                style={{
                  background: savedOk ? 'rgba(74,222,128,0.12)' : 'rgba(99,102,241,0.15)',
                  border: savedOk ? '1px solid rgba(74,222,128,0.4)' : '1px solid rgba(99,102,241,0.4)',
                  color: savedOk ? '#4ade80' : '#818cf8',
                  padding: '6px 16px', borderRadius: '6px',
                  fontSize: '12px', fontWeight: 700, cursor: (saving || savedOk) ? 'default' : 'pointer',
                  opacity: saving ? 0.6 : 1, whiteSpace: 'nowrap',
                }}
              >
                {saving ? 'Saving…' : savedOk ? '✓ Saved' : '+ Save Strategy'}
              </button>
            </div>
          </div>

          {/* Metric cards */}
          <div style={{ display: 'flex', gap: '8px', padding: '16px 22px', flexWrap: 'wrap' }}>
            {[
              { label: 'Total Return',  value: fmtPct(result.total_return),                color: retColor(result.total_return) },
              { label: 'CAGR',          value: fmtPct(result.cagr),                        color: retColor(result.cagr) },
              { label: 'Sharpe Ratio',  value: result.sharpe.toFixed(2),                   color: result.sharpe >= 1.5 ? '#4ade80' : result.sharpe >= 0.5 ? '#facc15' : '#f87171' },
              { label: 'Max Drawdown',  value: fmtPct(result.max_drawdown),                color: '#f87171' },
              { label: 'Win Rate',      value: `${(result.win_rate * 100).toFixed(0)}%`,   color: result.win_rate >= 0.55 ? '#4ade80' : result.win_rate >= 0.4 ? '#facc15' : '#f87171' },
              { label: 'Profit Factor', value: result.profit_factor.toFixed(2),            color: result.profit_factor >= 1.5 ? '#4ade80' : result.profit_factor >= 1 ? '#facc15' : '#f87171' },
              { label: 'Trades',        value: String(result.n_trades),                    color: '#94a3b8' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ flex: '1 1 110px', minWidth: '100px', padding: '12px 14px', borderRadius: '8px', background: '#080f1a', border: '1px solid #1e293b' }}>
                <div style={{ fontSize: '9px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '5px' }}>{label}</div>
                <div style={{ fontSize: '18px', fontWeight: 800, color }}>{value}</div>
              </div>
            ))}
          </div>

          {/* Equity curve */}
          {result.equity_curve?.length > 1 && (
            <div style={{ padding: '0 22px 12px' }}>
              <div style={{ fontSize: '10px', color: '#334155', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px' }}>
                Equity Curve
              </div>
              <div style={{ borderRadius: '8px', background: '#080f1a', border: '1px solid #1e293b', padding: '8px 12px' }}>
                <EquityCurve data={result.equity_curve} />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9px', color: '#334155', marginTop: '4px' }}>
                  <span>{startDate}</span>
                  <span>{endDate}</span>
                </div>
              </div>
            </div>
          )}

          {/* Trade history */}
          {trades.length > 0 && (
            <div style={{ padding: '0 22px 22px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
                <div style={{ fontSize: '10px', color: '#334155', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  Trade History ({trades.length})
                </div>
                {trades.length > 20 && (
                  <button onClick={() => setShowAll(t => !t)}
                    style={{ fontSize: '10px', color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}>
                    {showAll ? 'Show recent 20' : `Show all ${trades.length}`}
                  </button>
                )}
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px' }}>
                  <thead>
                    <tr>
                      {['#', 'Entry Date', 'Entry $', 'Exit Date', 'Exit $', 'Return'].map(h => (
                        <th key={h} style={{
                          textAlign: 'left', padding: '5px 10px',
                          color: '#334155', fontWeight: 700, fontSize: '9px',
                          textTransform: 'uppercase', letterSpacing: '0.04em',
                          borderBottom: '1px solid #1e293b',
                        }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {displayTrades.map((t, i) => {
                      const idx = trades.length - displayTrades.length + i + 1;
                      const pos = (t.ret ?? 0) >= 0;
                      return (
                        <tr key={i}>
                          <td style={{ padding: '5px 10px', color: '#334155', borderBottom: '1px solid #0a111f' }}>{idx}</td>
                          <td style={{ padding: '5px 10px', color: '#64748b', borderBottom: '1px solid #0a111f', fontFamily: 'monospace' }}>{fmtDate(t.entry_ts)}</td>
                          <td style={{ padding: '5px 10px', color: '#94a3b8', borderBottom: '1px solid #0a111f' }}>{t.entry.toFixed(2)}</td>
                          <td style={{ padding: '5px 10px', color: '#64748b', borderBottom: '1px solid #0a111f', fontFamily: 'monospace' }}>{t.exit_ts ? fmtDate(t.exit_ts) : '—'}</td>
                          <td style={{ padding: '5px 10px', color: '#94a3b8', borderBottom: '1px solid #0a111f' }}>{t.exit != null ? t.exit.toFixed(2) : '—'}</td>
                          <td style={{ padding: '5px 10px', fontWeight: 700, borderBottom: '1px solid #0a111f', color: pos ? '#4ade80' : '#f87171' }}>
                            {t.ret != null ? fmtPct(t.ret) : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Saved strategies */}
      {(saved ?? []).length > 0 && (
        <div style={{ marginTop: '24px' }}>
          <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '10px' }}>
            Saved Strategies ({saved!.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
            {saved!.map(s => (
              <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 14px', borderRadius: '8px', border: '1px solid #1e293b', background: 'rgba(15,23,42,0.6)' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: '#94a3b8', flex: 1 }}>{s.name}</span>
                <span style={{ fontSize: '10px', color: '#1e293b', fontFamily: 'monospace' }}>#{s.id}</span>
                <button
                  onClick={() => handleRetest(s.id)}
                  disabled={loading === s.id || running}
                  style={{ background: 'none', border: '1px solid rgba(99,102,241,0.35)', color: '#818cf8', cursor: 'pointer', fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '5px', opacity: (loading === s.id || running) ? 0.4 : 1 }}
                  title="Run backtest with this strategy"
                >{loading === s.id ? '⏳' : 'Retest'}</button>
                <button
                  onClick={() => handleDelete(s.id)}
                  disabled={deleting === s.id}
                  style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '13px', padding: '2px 4px', opacity: deleting === s.id ? 0.4 : 1 }}
                  title="Delete"
                >✕</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

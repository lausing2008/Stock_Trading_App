'use client';
import { useState, useEffect } from 'react';
import useSWR from 'swr';
import { api, type Stock, type BacktestRun, type BacktestDetail } from '@/lib/api';

type Cond = { feature: string; op: string; right: string };

interface Preset {
  key: string; label: string; icon: string; tagline: string; description: string;
  entry: Cond[]; exit: Cond[];
}

const FEATURES = [
  'close', 'sma_20', 'sma_50', 'sma_200',
  'ema_12', 'ema_26',
  'rsi_14',
  'macd', 'macd_signal', 'macd_hist',
  'atr_14',
  'bb_upper', 'bb_lower', 'bb_pct',
  'volume_ratio',
];
const OPS = ['<', '<=', '>', '>=', 'crosses_above', 'crosses_below'];

const OPP_TO_PRESET: Record<string, string> = {
  swing: 'swing', short: 'short', longterm: 'longterm', growth: 'growth', all: 'rsi_bounce', aisignal: 'ai_signal',
};

const PRESETS: Preset[] = [
  {
    key: 'swing', label: 'Swing Trade', icon: '📊', tagline: '5–30 day hold',
    description: 'RSI below 50 (not overbought) with price above SMA20 trend. Looks for setups where momentum can extend before an exit.',
    entry: [{ feature: 'rsi_14', op: '<', right: '50' }, { feature: 'close', op: '>', right: 'sma_20' }],
    exit:  [{ feature: 'rsi_14', op: '>', right: '65' }],
  },
  {
    key: 'short', label: 'Short-Term Momentum', icon: '⚡', tagline: '1–5 day breakout',
    description: 'Price crossing above the 20-day moving average signals short-term momentum. Exit immediately on reversal below SMA20.',
    entry: [{ feature: 'close', op: 'crosses_above', right: 'sma_20' }],
    exit:  [{ feature: 'close', op: 'crosses_below', right: 'sma_20' }],
  },
  {
    key: 'longterm', label: 'Long-Term Value', icon: '🏛️', tagline: '6–24 month horizon',
    description: 'Buys oversold dips (RSI < 45) while the 200-day uptrend is confirmed (price above SMA200). Enters quality stocks on pullbacks rather than catching a falling knife. Exit when RSI reaches overbought.',
    entry: [{ feature: 'rsi_14', op: '<', right: '45' }, { feature: 'close', op: '>', right: 'sma_200' }],
    exit:  [{ feature: 'rsi_14', op: '>', right: '70' }],
  },
  {
    key: 'growth', label: 'Growth Momentum', icon: '🚀', tagline: 'Ride the uptrend',
    description: 'Price above SMA50 with positive MACD histogram confirms an accelerating uptrend. Exit when histogram momentum fades.',
    entry: [{ feature: 'close', op: '>', right: 'sma_50' }, { feature: 'macd_hist', op: '>', right: '0' }],
    exit:  [{ feature: 'macd_hist', op: '<', right: '0' }],
  },
  {
    key: 'rsi_bounce', label: 'RSI Oversold Bounce', icon: '↩️', tagline: 'Mean reversion',
    description: 'Classic contrarian entry. RSI below 30 signals extreme selling pressure — buy the dip and exit at RSI 60 recovery.',
    entry: [{ feature: 'rsi_14', op: '<', right: '30' }],
    exit:  [{ feature: 'rsi_14', op: '>', right: '60' }],
  },
  {
    key: 'golden_cross', label: 'Golden Cross', icon: '✨', tagline: 'Long-term bullish trend',
    description: 'SMA50 crosses above SMA200 — one of the most reliable long-term bullish signals. Hold until the death cross reversal.',
    entry: [{ feature: 'sma_50', op: 'crosses_above', right: 'sma_200' }],
    exit:  [{ feature: 'sma_50', op: 'crosses_below', right: 'sma_200' }],
  },
  {
    key: 'death_cross', label: 'Death Cross Exit', icon: '☠️', tagline: 'Capital preservation',
    description: 'Hold long while price stays above SMA50, then exit immediately when SMA50 crosses below SMA200 — a major bear warning.',
    entry: [{ feature: 'close', op: '>', right: 'sma_50' }],
    exit:  [{ feature: 'sma_50', op: 'crosses_below', right: 'sma_200' }],
  },
  {
    key: 'macd_crossover', label: 'MACD Crossover', icon: '📈', tagline: 'Classic momentum signal',
    description: 'MACD line crossing above the signal line. One of the most widely used momentum confirmation patterns in technical analysis.',
    entry: [{ feature: 'macd', op: 'crosses_above', right: 'macd_signal' }],
    exit:  [{ feature: 'macd', op: 'crosses_below', right: 'macd_signal' }],
  },
  {
    key: 'sma50_breakout', label: 'SMA50 Breakout', icon: '🎯', tagline: 'Trend following',
    description: 'Price crossing above the 50-day SMA confirms a medium-term trend change. Ride the position while price holds above.',
    entry: [{ feature: 'close', op: 'crosses_above', right: 'sma_50' }],
    exit:  [{ feature: 'close', op: 'crosses_below', right: 'sma_50' }],
  },
  {
    key: 'mean_reversion', label: 'Mean Reversion', icon: '⚖️', tagline: 'Buy dips in uptrend',
    description: 'RSI below 35 with price under SMA50. Expects a bounce back toward the SMA20 mean on oversold conditions.',
    entry: [{ feature: 'rsi_14', op: '<', right: '35' }, { feature: 'close', op: '<', right: 'sma_50' }],
    exit:  [{ feature: 'close', op: '>', right: 'sma_20' }],
  },
  {
    key: 'ai_signal', label: 'AI Signal', icon: '🤖', tagline: 'Mirrors live BUY conditions',
    description: 'Approximates the live AI engine\'s SWING BUY conditions: RSI in the optimal 40–68 zone, MACD histogram positive (momentum building), price above SMA50, and SMA50 above SMA200 (golden cross — weekly uptrend confirmed). These match the TA thresholds the signal engine uses before applying ML. Exit when MACD histogram turns negative (momentum stall).',
    entry: [
      { feature: 'rsi_14', op: '>', right: '40' },
      { feature: 'rsi_14', op: '<', right: '68' },
      { feature: 'macd_hist', op: '>', right: '0' },
      { feature: 'close', op: '>', right: 'sma_50' },
      { feature: 'sma_50', op: '>', right: 'sma_200' },
    ],
    exit:  [{ feature: 'macd_hist', op: '<', right: '0' }],
  },
  {
    key: 'volume_breakout', label: 'Volume Breakout', icon: '📣', tagline: '1–10 day breakout',
    description: 'Buys when price crosses above SMA20 on at least 1.5× average volume — confirming real institutional participation, not a fake-out. Exit immediately if price falls back below SMA20.',
    entry: [
      { feature: 'close', op: 'crosses_above', right: 'sma_20' },
      { feature: 'volume_ratio', op: '>=', right: '1.5' },
    ],
    exit: [{ feature: 'close', op: 'crosses_below', right: 'sma_20' }],
  },
  {
    key: 'bb_bounce', label: 'Bollinger Bounce', icon: '🎱', tagline: 'Mean reversion at the band',
    description: 'Enters when price is near the lower Bollinger Band (bb_pct < 0.2) with RSI also oversold (< 38). Expects a mean-reversion snap back toward the middle band. Exit when price reaches the upper half of the band (bb_pct > 0.75).',
    entry: [
      { feature: 'bb_pct', op: '<', right: '0.2' },
      { feature: 'rsi_14', op: '<', right: '38' },
    ],
    exit: [{ feature: 'bb_pct', op: '>', right: '0.75' }],
  },
];

function fromNode(node: any): Cond[] {
  if (!node) return [];
  if (node.op === 'and' && Array.isArray(node.nodes)) {
    return node.nodes
      .filter((n: any) => n.left && n.op)
      .map((n: any) => ({ feature: String(n.left), op: n.op, right: String(n.right) }));
  }
  if (node.left && node.op) return [{ feature: String(node.left), op: node.op, right: String(node.right) }];
  return [];
}

function toNode(conds: Cond[]): object {
  const nodes = conds.map((c) => ({
    op: c.op, left: c.feature,
    right: isNaN(Number(c.right)) ? c.right : Number(c.right),
  }));
  return nodes.length === 1 ? nodes[0] : { op: 'and', nodes };
}

function fmtPct(n: number, d = 1) { return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(d)}%`; }
function fmtDate(iso: string) { return iso.split('T')[0]; }
function fmtSavedAt(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}
function threeYearsAgo() { const d = new Date(); d.setFullYear(d.getFullYear() - 3); return d.toISOString().split('T')[0]; }
function today() { return new Date().toISOString().split('T')[0]; }
function retColor(n: number) { return n >= 0 ? '#4ade80' : '#f87171'; }

const COMPARE_COLORS = ['#818cf8', '#4ade80', '#fb923c'];

function EquityCurve({ data }: { data: { ts: string; equity: number }[] }) {
  if (!data || data.length < 2) return null;
  const values = data.map(d => d.equity);
  const minV = Math.min(...values), maxV = Math.max(...values);
  const range = maxV - minV || 1;
  const W = 600, H = 100, PAD = 3;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * W},${H - PAD - ((v - minV) / range) * (H - PAD * 2)}`).join(' ');
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
        <line x1="0" y1={baselineY} x2={W} y2={baselineY} stroke="#475569" strokeWidth="0.6" strokeDasharray="5,4" vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}

function EquityCurveOverlay({ series }: { series: { name: string; data: { equity: number }[]; color: string }[] }) {
  if (!series.length) return null;
  const allVals = series.flatMap(s => s.data.map(d => d.equity));
  if (allVals.length < 2) return null;
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const range = maxV - minV || 1;
  const W = 600, H = 100, PAD = 3;
  const pts = (data: { equity: number }[]) =>
    data.map((d, i) => `${(i / Math.max(data.length - 1, 1)) * W},${H - PAD - ((d.equity - minV) / range) * (H - PAD * 2)}`).join(' ');
  const baselineY = H - PAD - ((1 - minV) / range) * (H - PAD * 2);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: '160px' }} preserveAspectRatio="none">
      {minV < 1 && maxV > 1 && (
        <line x1="0" y1={baselineY} x2={W} y2={baselineY} stroke="#475569" strokeWidth="0.6" strokeDasharray="5,4" vectorEffect="non-scaling-stroke" />
      )}
      {series.map(s => (
        <polyline key={s.name} points={pts(s.data)} fill="none" stroke={s.color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" opacity="0.9" />
      ))}
    </svg>
  );
}

function generateAnalysis(runs: BacktestDetail[]): string[] {
  if (runs.length < 2) return [];
  const insights: string[] = [];
  const byReturn   = [...runs].sort((a, b) => b.total_return   - a.total_return);
  const bySharpe   = [...runs].sort((a, b) => b.sharpe         - a.sharpe);
  const byDD       = [...runs].sort((a, b) => b.max_drawdown   - a.max_drawdown);
  const byWR       = [...runs].sort((a, b) => b.win_rate       - a.win_rate);
  const byPF       = [...runs].sort((a, b) => b.profit_factor  - a.profit_factor);

  const pts: Record<number, number> = {};
  runs.forEach(r => { pts[r.id] = 0; });
  [byReturn[0], bySharpe[0], byDD[0], byWR[0]].forEach(r => { if (r) pts[r.id]++; });
  const winnerId = Number(Object.entries(pts).sort((a, b) => Number(b[1]) - Number(a[1]))[0][0]);
  const winner = runs.find(r => r.id === winnerId);
  if (winner) insights.push(`Overall: ${winner.name} leads in ${pts[winnerId]} of 4 categories.`);

  const retGap = byReturn[0].total_return - byReturn[runs.length - 1].total_return;
  insights.push(`Return: ${byReturn[0].name} returned ${fmtPct(byReturn[0].total_return)}, outperforming ${byReturn[runs.length - 1].name} (${fmtPct(byReturn[runs.length - 1].total_return)}) by ${fmtPct(retGap)}.`);

  if (bySharpe[0].id !== byReturn[0].id) {
    insights.push(`Risk-adjusted: ${bySharpe[0].name} has the best Sharpe (${bySharpe[0].sharpe.toFixed(2)} vs ${bySharpe[runs.length - 1].sharpe.toFixed(2)}) — better return per unit of risk despite lower raw return.`);
  } else {
    insights.push(`Risk-adjusted: ${bySharpe[0].name} also leads on Sharpe (${bySharpe[0].sharpe.toFixed(2)}), confirming its edge is not just luck.`);
  }

  insights.push(`Drawdown: ${byDD[0].name} had the smallest decline (${fmtPct(byDD[0].max_drawdown)}) — best capital preservation. ${byDD[runs.length - 1].name} suffered ${fmtPct(byDD[runs.length - 1].max_drawdown)}.`);
  insights.push(`Win rate: ${byWR[0].name} was right ${(byWR[0].win_rate * 100).toFixed(0)}% of trades. ${byPF[0].name} had the best profit factor (${byPF[0].profit_factor.toFixed(2)}) — wins were ${byPF[0].profit_factor.toFixed(1)}x larger than losses.`);

  return insights;
}

const inp: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #1e293b', borderRadius: '6px',
  padding: '7px 10px', fontSize: '12px', color: '#e2e8f0', outline: 'none',
};
const lbl: React.CSSProperties = {
  fontSize: '10px', color: '#64748b', fontWeight: 600, textTransform: 'uppercase',
  letterSpacing: '0.05em', display: 'block', marginBottom: '5px',
};

export default function StrategiesPage() {
  const { data: stocks } = useSWR<Stock[]>('stocks-all', () => api.listStocks());
  const { data: savedRuns, mutate: mutateSaved } = useSWR<BacktestRun[]>('backtests', () => api.listBacktests());

  const [selectedPreset, setSelectedPreset] = useState('rsi_bounce');
  const [name, setName]         = useState('RSI Oversold Bounce');
  const [entry, setEntry]       = useState<Cond[]>([{ feature: 'rsi_14', op: '<', right: '30' }]);
  const [exitRules, setExit]    = useState<Cond[]>([{ feature: 'rsi_14', op: '>', right: '60' }]);
  const [symbol, setSymbol]     = useState('');
  const [startDate, setStart]   = useState(threeYearsAgo);
  const [endDate, setEnd]       = useState(today);
  const [running, setRunning]   = useState(false);
  const [result, setResult]     = useState<BacktestDetail | null>(null);
  const [runSymbol, setRunSym]  = useState('');
  const [error, setError]       = useState('');
  const [showAll, setShowAll]   = useState(false);

  const [deleting,      setDeleting]      = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [loadingRun,  setLoadingRun]  = useState<number | null>(null);
  const [compareIds,  setCompareIds]  = useState<number[]>([]);
  const [compareData, setCompareData] = useState<Map<number, BacktestDetail>>(new Map());
  const [loadingCmp,  setLoadingCmp]  = useState<number | null>(null);

  useEffect(() => {
    const oppKey = typeof window !== 'undefined' ? localStorage.getItem('stockai_opp_strategy') : null;
    const presetKey = (oppKey && OPP_TO_PRESET[oppKey]) ? OPP_TO_PRESET[oppKey] : 'rsi_bounce';
    const p = PRESETS.find(x => x.key === presetKey);
    if (p) applyPreset(p, false);
  }, []);

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
    if (clearResult) { setResult(null); setError(''); }
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
    if (startDate && endDate && endDate <= startDate) {
      setError('End date must be after start date.');
      return;
    }
    setRunning(true); setError(''); setResult(null); setRunSym(symbol);
    try {
      const rule_dsl: { entry: object; exit?: object } = { entry: toNode(entry) };
      if (exitRules.length > 0) rule_dsl.exit = toNode(exitRules);
      const runName = `${name} — ${symbol}`;
      const res = await api.backtest({ rule_dsl, name: runName, symbol, start: startDate, end: endDate });
      setResult({
        id: res.backtest_id!,
        name: runName,
        symbol,
        start: startDate,
        end: endDate,
        created_at: new Date().toISOString(),
        total_return: res.total_return,
        cagr: res.cagr,
        sharpe: res.sharpe,
        max_drawdown: res.max_drawdown,
        win_rate: res.win_rate,
        profit_factor: res.profit_factor,
        n_trades: res.n_trades,
        equity_curve: res.equity_curve,
        trades: res.trades ?? [],
        rule_dsl,
      });
      await mutateSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Backtest failed — make sure this stock has enough price history.');
    } finally { setRunning(false); }
  }

  async function handleLoad(id: number) {
    setLoadingRun(id);
    try {
      const detail = await api.getBacktest(id);
      setEntry(fromNode(detail.rule_dsl?.entry));
      setExit(fromNode(detail.rule_dsl?.exit ?? null));
      setName(detail.name.replace(/ — .+$/, ''));
      setSelectedPreset('');
      setResult(detail);
      setRunSym(detail.symbol);
      setError('');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load backtest.');
    } finally { setLoadingRun(null); }
  }

  async function handleDelete(id: number) {
    setConfirmDelete(null);
    setDeleting(id);
    try {
      await api.deleteBacktest(id);
      setCompareIds(prev => prev.filter(x => x !== id));
      setCompareData(prev => { const m = new Map(prev); m.delete(id); return m; });
      if (result?.id === id) setResult(null);
      await mutateSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to delete backtest.');
    } finally { setDeleting(null); }
  }

  async function toggleCompare(id: number) {
    if (compareIds.includes(id)) {
      setCompareIds(prev => prev.filter(x => x !== id));
      setCompareData(prev => { const m = new Map(prev); m.delete(id); return m; });
      return;
    }
    if (compareIds.length >= 3) return;
    setCompareIds(prev => [...prev, id]);
    if (!compareData.has(id)) {
      setLoadingCmp(id);
      try {
        const detail = await api.getBacktest(id);
        setCompareData(prev => new Map(prev).set(id, detail));
      } catch {}
      finally { setLoadingCmp(null); }
    }
  }

  const compareRuns = compareIds.map(id => compareData.get(id)).filter(Boolean) as BacktestDetail[];
  const displayTrades = showAll ? (result?.trades ?? []) : (result?.trades ?? []).slice(-20);

  return (
    <div style={{ maxWidth: '1120px', margin: '0 auto' }}>

      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 800, color: '#f1f5f9', margin: '0 0 4px' }}>Strategy Backtester</h1>
        <p style={{ fontSize: '12px', color: '#475569', margin: 0 }}>
          Every run is auto-saved. Load past results, compare up to 3 runs side-by-side.
          Fees: 5 bps + 2 bps slippage · Long-only · Next-bar fill · Features: RSI, MACD, SMA, ATR, Bollinger Bands, volume ratio.
        </p>
      </div>

      {/* Builder */}
      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: '16px', alignItems: 'start' }}>

        {/* Templates */}
        <div>
          <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '10px' }}>Templates</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {PRESETS.map(p => (
              <button key={p.key} onClick={() => applyPreset(p)}
                style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                  padding: '9px 12px', borderRadius: '8px', cursor: 'pointer', textAlign: 'left',
                  border: selectedPreset === p.key ? '1px solid rgba(99,102,241,0.6)' : '1px solid #1e293b',
                  background: selectedPreset === p.key ? 'rgba(79,70,229,0.15)' : 'rgba(255,255,255,0.02)',
                  transition: 'all 0.1s',
                }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
                  <span style={{ fontSize: '13px' }}>{p.icon}</span>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: selectedPreset === p.key ? '#c7d2fe' : '#94a3b8' }}>{p.label}</span>
                </div>
                <span style={{ fontSize: '10px', color: selectedPreset === p.key ? '#818cf8' : '#334155' }}>{p.tagline}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Condition builder */}
        <div style={{ borderRadius: '12px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
          <div style={{ height: '3px', background: 'linear-gradient(90deg,#4f46e5,#818cf8,#4f46e5)' }} />
          <div style={{ padding: '20px 24px' }}>
            {(() => { const p = PRESETS.find(x => x.key === selectedPreset); return p ? (
              <div style={{ marginBottom: '16px', padding: '10px 13px', borderRadius: '8px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.15)', fontSize: '12px', color: '#94a3b8', lineHeight: 1.6 }}>{p.description}</div>
            ) : null; })()}

            <div style={{ marginBottom: '16px' }}>
              <label style={lbl}>Strategy Name</label>
              <input value={name} onChange={e => setName(e.target.value)} style={{ ...inp, width: '100%', boxSizing: 'border-box' }} />
            </div>

            {(['entry', 'exit'] as const).map(kind => {
              const conds = kind === 'entry' ? entry : exitRules;
              return (
                <div key={kind} style={{ marginBottom: '16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <label style={{ ...lbl, margin: 0 }}>{kind === 'entry' ? 'Entry Conditions (AND)' : 'Exit Conditions (AND)'}</label>
                    <button onClick={() => addCond(kind)} style={{ fontSize: '10px', color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}>+ Add</button>
                  </div>
                  {conds.length === 0 && (
                    <div style={{ fontSize: '11px', color: '#334155', fontStyle: 'italic', padding: '4px 0' }}>{kind === 'exit' ? 'No exit rule — position held to end of period.' : 'No conditions set.'}</div>
                  )}
                  {conds.map((c, i) => (
                    <div key={i} style={{ display: 'flex', gap: '6px', marginBottom: '6px', alignItems: 'center' }}>
                      <select value={c.feature} onChange={e => updateCond(kind, i, { feature: e.target.value })} style={inp}>
                        {FEATURES.map(f => <option key={f} value={f}>{f}</option>)}
                      </select>
                      <select value={c.op} onChange={e => updateCond(kind, i, { op: e.target.value })} style={{ ...inp, minWidth: '140px' }}>
                        {OPS.map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                      <input value={c.right} onChange={e => updateCond(kind, i, { right: e.target.value })} style={{ ...inp, width: '90px' }} placeholder="value or field" />
                      <button onClick={() => removeCond(kind, i)} style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '14px', flexShrink: 0, padding: '2px 4px' }} title="Remove">✕</button>
                    </div>
                  ))}
                </div>
              );
            })}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 140px 140px', gap: '12px', marginBottom: '16px' }}>
              <div>
                <label style={lbl}>Stock to Backtest</label>
                <select value={symbol} onChange={e => setSymbol(e.target.value)} style={{ ...inp, width: '100%' }}>
                  {(stocks ?? []).map(s => <option key={`${s.symbol}-${s.exchange}`} value={s.symbol}>{s.symbol} — {s.name}</option>)}
                </select>
              </div>
              <div>
                <label style={lbl}>Start Date</label>
                <input type="date" value={startDate} onChange={e => setStart(e.target.value)} style={{ ...inp, width: '100%', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label style={lbl}>End Date</label>
                <input type="date" value={endDate} onChange={e => setEnd(e.target.value)} style={{ ...inp, width: '100%', boxSizing: 'border-box' }} />
              </div>
            </div>

            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
              <button onClick={runBacktest} disabled={running || !symbol || entry.length === 0}
                style={{ background: 'linear-gradient(135deg,#4f46e5,#6366f1)', border: 'none', color: '#fff', padding: '10px 28px', borderRadius: '8px', fontSize: '13px', fontWeight: 700, cursor: (running || !symbol) ? 'not-allowed' : 'pointer', opacity: (running || !symbol) ? 0.6 : 1 }}>
                {running ? '⏳ Running…' : '▶ Run Backtest'}
              </button>
              {running && <span style={{ fontSize: '11px', color: '#475569' }}>Fetching price history &amp; computing trades…</span>}
            </div>

            {error && (
              <div style={{ marginTop: '10px', fontSize: '12px', color: '#f87171', padding: '8px 12px', borderRadius: '6px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>{error}</div>
            )}
          </div>
        </div>
      </div>

      {/* Result */}
      {result && (
        <div style={{ marginTop: '20px', borderRadius: '12px', border: '1px solid #1e293b', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
          <div style={{ padding: '16px 22px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
            <div>
              <div style={{ fontSize: '15px', fontWeight: 800, color: '#f1f5f9' }}>{result.name}</div>
              <div style={{ fontSize: '11px', color: '#475569', marginTop: '2px' }}>{result.start} → {result.end} · {result.n_trades} trades</div>
            </div>
            <div style={{ fontSize: '28px', fontWeight: 900, color: retColor(result.total_return) }}>{fmtPct(result.total_return)}</div>
          </div>

          <div style={{ display: 'flex', gap: '8px', padding: '16px 22px', flexWrap: 'wrap' }}>
            {[
              { label: 'Total Return',  value: fmtPct(result.total_return),               color: retColor(result.total_return) },
              { label: 'CAGR',          value: fmtPct(result.cagr),                       color: retColor(result.cagr) },
              { label: 'Sharpe Ratio',  value: result.sharpe.toFixed(2),                  color: result.sharpe >= 1.5 ? '#4ade80' : result.sharpe >= 0.5 ? '#facc15' : '#f87171' },
              { label: 'Max Drawdown',  value: fmtPct(result.max_drawdown),               color: '#f87171' },
              { label: 'Win Rate',      value: `${(result.win_rate * 100).toFixed(0)}%`,  color: result.win_rate >= 0.55 ? '#4ade80' : result.win_rate >= 0.4 ? '#facc15' : '#f87171' },
              { label: 'Profit Factor', value: result.profit_factor.toFixed(2),           color: result.profit_factor >= 1.5 ? '#4ade80' : result.profit_factor >= 1 ? '#facc15' : '#f87171' },
              { label: 'Trades',        value: String(result.n_trades),                   color: '#94a3b8' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ flex: '1 1 110px', minWidth: '100px', padding: '12px 14px', borderRadius: '8px', background: '#080f1a', border: '1px solid #1e293b' }}>
                <div style={{ fontSize: '9px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '5px' }}>{label}</div>
                <div style={{ fontSize: '18px', fontWeight: 800, color }}>{value}</div>
              </div>
            ))}
          </div>

          {result.equity_curve?.length > 1 && (
            <div style={{ padding: '0 22px 12px' }}>
              <div style={{ fontSize: '10px', color: '#334155', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px' }}>Equity Curve</div>
              <div style={{ borderRadius: '8px', background: '#080f1a', border: '1px solid #1e293b', padding: '8px 12px' }}>
                <EquityCurve data={result.equity_curve} />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9px', color: '#334155', marginTop: '4px' }}>
                  <span>{result.start}</span><span>{result.end}</span>
                </div>
              </div>
            </div>
          )}

          {displayTrades.length > 0 && (
            <div style={{ padding: '0 22px 22px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
                <div style={{ fontSize: '10px', color: '#334155', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Trade History ({result.trades.length})</div>
                {result.trades.length > 20 && (
                  <button onClick={() => setShowAll(t => !t)} style={{ fontSize: '10px', color: '#818cf8', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}>
                    {showAll ? 'Show recent 20' : `Show all ${result.trades.length}`}
                  </button>
                )}
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px' }}>
                  <thead>
                    <tr>{['#', 'Entry Date', 'Entry $', 'Exit Date', 'Exit $', 'Return'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '5px 10px', color: '#334155', fontWeight: 700, fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {displayTrades.map((t, i) => {
                      const idx = result.trades.length - displayTrades.length + i + 1;
                      const pos = (t.ret ?? 0) >= 0;
                      return (
                        <tr key={i}>
                          <td style={{ padding: '5px 10px', color: '#334155', borderBottom: '1px solid #0a111f' }}>{idx}</td>
                          <td style={{ padding: '5px 10px', color: '#64748b', borderBottom: '1px solid #0a111f', fontFamily: 'monospace' }}>{fmtDate(t.entry_ts)}</td>
                          <td style={{ padding: '5px 10px', color: '#94a3b8', borderBottom: '1px solid #0a111f' }}>{t.entry.toFixed(2)}</td>
                          <td style={{ padding: '5px 10px', color: '#64748b', borderBottom: '1px solid #0a111f', fontFamily: 'monospace' }}>{t.exit_ts ? fmtDate(t.exit_ts) : '—'}</td>
                          <td style={{ padding: '5px 10px', color: '#94a3b8', borderBottom: '1px solid #0a111f' }}>{t.exit != null ? t.exit.toFixed(2) : '—'}</td>
                          <td style={{ padding: '5px 10px', fontWeight: 700, borderBottom: '1px solid #0a111f', color: pos ? '#4ade80' : '#f87171' }}>{t.ret != null ? fmtPct(t.ret) : '—'}</td>
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

      {/* Saved runs */}
      {(savedRuns ?? []).length > 0 && (
        <div style={{ marginTop: '28px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
            <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Saved Runs ({savedRuns!.length})
            </div>
            {compareIds.length > 0 && (
              <div style={{ fontSize: '11px', color: '#818cf8', fontWeight: 600 }}>
                {compareIds.length} selected for compare {compareIds.length < 2 && '— pick at least 2'}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {savedRuns!.map((run, idx) => {
              const color = COMPARE_COLORS[compareIds.indexOf(run.id)] ?? undefined;
              const selected = compareIds.includes(run.id);
              return (
                <div key={run.id}
                  style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '12px 16px', borderRadius: '10px', border: selected ? `1px solid ${color}55` : '1px solid #1e293b', background: selected ? `${color}0d` : 'rgba(15,23,42,0.6)', transition: 'all 0.15s' }}>

                  {/* Compare checkbox */}
                  <button onClick={() => toggleCompare(run.id)} disabled={!selected && compareIds.length >= 3}
                    style={{ width: '18px', height: '18px', flexShrink: 0, borderRadius: '4px', border: selected ? `2px solid ${color}` : '2px solid #1e293b', background: selected ? `${color}33` : 'transparent', cursor: (!selected && compareIds.length >= 3) ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    title={selected ? 'Remove from compare' : 'Add to compare'}>
                    {selected && <div style={{ width: '8px', height: '8px', borderRadius: '2px', background: color }} />}
                    {loadingCmp === run.id && <span style={{ fontSize: '8px', color: '#475569' }}>…</span>}
                  </button>

                  {/* Color dot for selected */}
                  {selected && color && <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: color, flexShrink: 0 }} />}

                  {/* Info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: '12px', fontWeight: 700, color: '#cbd5e1', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{run.name}</div>
                    <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>
                      {run.start} → {run.end} · {run.n_trades} trades
                      {run.created_at && <span style={{ color: '#1e3a5f', marginLeft: '6px' }}>· {fmtSavedAt(run.created_at)}</span>}
                    </div>
                  </div>

                  {/* Metrics */}
                  <div style={{ display: 'flex', gap: '16px', flexShrink: 0 }}>
                    {[
                      { label: 'Return', value: fmtPct(run.total_return), color: retColor(run.total_return) },
                      { label: 'Sharpe', value: run.sharpe.toFixed(2), color: run.sharpe >= 1.5 ? '#4ade80' : run.sharpe >= 0.5 ? '#facc15' : '#f87171' },
                      { label: 'Win', value: `${(run.win_rate * 100).toFixed(0)}%`, color: run.win_rate >= 0.55 ? '#4ade80' : '#94a3b8' },
                      { label: 'DD', value: fmtPct(run.max_drawdown), color: '#f87171' },
                    ].map(m => (
                      <div key={m.label} style={{ textAlign: 'right' }}>
                        <div style={{ fontSize: '9px', color: '#334155', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{m.label}</div>
                        <div style={{ fontSize: '13px', fontWeight: 700, color: m.color }}>{m.value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Actions */}
                  <div style={{ display: 'flex', gap: '6px', flexShrink: 0, alignItems: 'center' }}>
                    <button onClick={() => handleLoad(run.id)} disabled={loadingRun === run.id}
                      style={{ background: 'none', border: '1px solid rgba(99,102,241,0.35)', color: '#818cf8', cursor: 'pointer', fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '5px', opacity: loadingRun === run.id ? 0.4 : 1 }}
                      title="Load result">
                      {loadingRun === run.id ? '…' : 'Load'}
                    </button>
                    {confirmDelete === run.id ? (
                      <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                        <span style={{ fontSize: '10px', color: '#f87171', whiteSpace: 'nowrap' }}>Delete?</span>
                        <button onClick={() => handleDelete(run.id)} disabled={deleting === run.id}
                          style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)', color: '#f87171', cursor: 'pointer', fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px' }}>
                          Yes
                        </button>
                        <button onClick={() => setConfirmDelete(null)}
                          style={{ background: 'none', border: '1px solid #1e293b', color: '#475569', cursor: 'pointer', fontSize: '10px', padding: '2px 7px', borderRadius: '4px' }}>
                          No
                        </button>
                      </div>
                    ) : (
                      <button onClick={() => setConfirmDelete(run.id)} disabled={deleting === run.id}
                        style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '13px', padding: '2px 4px', opacity: deleting === run.id ? 0.4 : 1 }}
                        title="Delete">✕</button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Compare panel */}
      {compareIds.length >= 2 && compareRuns.length >= 2 && (
        <div style={{ marginTop: '24px', borderRadius: '12px', border: '1px solid rgba(99,102,241,0.3)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
          <div style={{ height: '3px', background: `linear-gradient(90deg, ${COMPARE_COLORS.slice(0, compareRuns.length).join(', ')})` }} />
          <div style={{ padding: '20px 24px' }}>

            <div style={{ fontSize: '13px', fontWeight: 800, color: '#f1f5f9', marginBottom: '16px' }}>
              Comparison ({compareRuns.length} runs)
            </div>

            {/* Overlay equity curve */}
            <div style={{ borderRadius: '8px', background: '#080f1a', border: '1px solid #1e293b', padding: '12px 16px', marginBottom: '20px' }}>
              <EquityCurveOverlay series={compareRuns.map((r, i) => ({ name: r.name, data: r.equity_curve ?? [], color: COMPARE_COLORS[i] }))} />
              {/* Legend */}
              <div style={{ display: 'flex', gap: '16px', marginTop: '10px', flexWrap: 'wrap' }}>
                {compareRuns.map((r, i) => (
                  <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <div style={{ width: '20px', height: '2px', background: COMPARE_COLORS[i], borderRadius: '1px' }} />
                    <span style={{ fontSize: '10px', color: '#64748b' }}>{r.name}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Metrics table */}
            <div style={{ overflowX: 'auto', marginBottom: '20px' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left', padding: '8px 12px', color: '#334155', fontWeight: 700, fontSize: '10px', textTransform: 'uppercase', borderBottom: '1px solid #1e293b' }}>Metric</th>
                    {compareRuns.map((r, i) => (
                      <th key={r.id} style={{ textAlign: 'right', padding: '8px 12px', borderBottom: '1px solid #1e293b' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '6px' }}>
                          <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: COMPARE_COLORS[i], flexShrink: 0 }} />
                          <span style={{ fontSize: '11px', fontWeight: 700, color: '#94a3b8', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: 'Total Return',  vals: compareRuns.map(r => r.total_return),   fmt: (v: number) => fmtPct(v),                       higherBetter: true  },
                    { label: 'CAGR',          vals: compareRuns.map(r => r.cagr),            fmt: (v: number) => fmtPct(v),                       higherBetter: true  },
                    { label: 'Sharpe Ratio',  vals: compareRuns.map(r => r.sharpe),          fmt: (v: number) => v.toFixed(2),                    higherBetter: true  },
                    { label: 'Max Drawdown',  vals: compareRuns.map(r => r.max_drawdown),    fmt: (v: number) => fmtPct(v),                       higherBetter: true  },
                    { label: 'Win Rate',      vals: compareRuns.map(r => r.win_rate),        fmt: (v: number) => `${(v * 100).toFixed(0)}%`,      higherBetter: true  },
                    { label: 'Profit Factor', vals: compareRuns.map(r => r.profit_factor),   fmt: (v: number) => v.toFixed(2),                    higherBetter: true  },
                    { label: 'Trades',        vals: compareRuns.map(r => r.n_trades),        fmt: (v: number) => String(v),                       higherBetter: null  },
                  ].map(({ label, vals, fmt, higherBetter }, ri) => {
                    return (
                      <tr key={label} style={{ background: ri % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                        <td style={{ padding: '9px 12px', color: '#475569', fontSize: '11px', fontWeight: 600, borderBottom: '1px solid #0a111f' }}>{label}</td>
                        {vals.map((v, i) => {
                          const isBest = higherBetter !== null && (
                            higherBetter ? v === Math.max(...vals) : v === Math.min(...vals)
                          );
                          return (
                            <td key={i} style={{ textAlign: 'right', padding: '9px 12px', fontWeight: isBest ? 800 : 500, color: isBest ? COMPARE_COLORS[i] : '#64748b', fontSize: '12px', borderBottom: '1px solid #0a111f' }}>
                              {fmt(v)}{isBest && <span style={{ marginLeft: '4px', fontSize: '9px' }}>✓</span>}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Analysis */}
            <div style={{ borderRadius: '8px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.15)', padding: '14px 16px' }}>
              <div style={{ fontSize: '10px', color: '#475569', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '10px' }}>Analysis</div>
              {generateAnalysis(compareRuns).map((line, i) => (
                <div key={i} style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.7, marginBottom: '4px' }}>
                  <span style={{ color: '#4f46e5', marginRight: '6px' }}>▶</span>{line}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

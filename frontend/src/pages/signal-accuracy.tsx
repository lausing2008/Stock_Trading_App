/**
 * Signal Accuracy Tracker page (/signal-accuracy) — measures how often the
 * AI signal engine's BUY and SELL calls predicted the correct direction.
 *
 * Data source: GET /signals/accuracy?lookback_days=N (signal-engine service).
 * For each persisted BUY or SELL signal, the backend joins the close price on
 * the signal date to the close price ~5 trading days later and checks:
 *   BUY correct  → exit price > entry price
 *   SELL correct → exit price < entry price
 * Only signals at least 7 days old are included (needs time to settle).
 *
 * How to read the stats
 * ─────────────────────
 * Overall Accuracy   — % of all evaluated signals that pointed the right way.
 *                      > 50% beats a coin flip; > 60% indicates real signal value.
 * BUY / SELL Accuracy — accuracy split by signal type. Often one direction is
 *                       more reliable than the other.
 * Avg BUY Return     — average price change 5 days after a BUY signal.
 *                      Positive = signals are calling entries at the right time.
 * Avg SELL Return    — shown as the decline after a SELL signal.
 * Profit Factor      — total gain from correct signals ÷ total loss from wrong
 *                      ones. Above 1.5 = good; below 1.0 = signals losing money.
 *
 * Accuracy bar
 * ────────────
 * The horizontal bar has a centre line at 50% (random baseline). Green means
 * above random, yellow means near-random, red means below random.
 *
 * Practical workflow
 * ──────────────────
 * 1. Start with the 90d window (default) for a statistically meaningful sample.
 * 2. Compare 30d vs 90d accuracy — if 30d is higher, the model is improving.
 * 3. Filter by BUY/SELL separately to decide how much weight to give each type.
 * 4. Click "Wrong" to study only the misses — look for sector or market-regime
 *    patterns that cause the signal engine to fail.
 * 5. Type a symbol to see accuracy for a single stock.
 *
 * Filters / sort
 * ──────────────
 * Lookback   — 30d / 60d / 90d / 180d
 * Symbol     — free-text filter on ticker
 * Signal     — ALL / BUY / SELL
 * Outcome    — ALL / CORRECT / WRONG
 * Sort by    — Date (newest first) / Confidence / Return %
 */
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type SignalAccuracyRow, type FactorRow, type MLWeightCurvePoint, type WalkForwardReport, type WalkForwardWindow, type OutcomesSummary, type OutcomesCalibration, type SignalAccuracyReport, type AlphaDecayReport, type AlphaDecayCurvePoint } from '@/lib/api';
import { getSession } from '@/lib/auth';

type RollingPoint = { date: string; accuracy: number; signal_count: number };

function RollingAccuracyChart({ series, driftWarning, latestAccuracy, window: win }: {
  series: RollingPoint[];
  driftWarning: boolean;
  latestAccuracy: number | null;
  window: number;
}) {
  if (series.length < 2) return null;
  const accs = series.map(p => p.accuracy);
  const minA = Math.min(...accs, 40);
  const maxA = Math.max(...accs, 70);
  const range = maxA - minA || 1;
  const h = 80;

  return (
    <div style={{ background: '#0f172a', border: `1px solid ${driftWarning ? 'rgba(239,68,68,0.4)' : '#1e293b'}`, borderRadius: 8, padding: '14px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8' }}>Rolling {win}-day BUY Accuracy</div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>Model drift monitor — each point = accuracy over trailing {win} days</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          {latestAccuracy != null && (
            <div style={{ fontSize: 14, fontWeight: 700, color: latestAccuracy >= 60 ? '#4ade80' : latestAccuracy >= 50 ? '#facc15' : '#f87171' }}>
              {latestAccuracy.toFixed(1)}% now
            </div>
          )}
          {driftWarning && (
            <div style={{ fontSize: 10, color: '#f87171', fontWeight: 700, marginTop: 2 }}>⚠ DRIFT DETECTED</div>
          )}
        </div>
      </div>

      {/* Line chart — SVG polyline */}
      <div style={{ position: 'relative', height: h + 20 }}>
        <svg width="100%" height={h} style={{ overflow: 'visible' }}>
          {/* 50% reference line */}
          <line
            x1="0" y1={`${((maxA - 50) / range) * h}`}
            x2="100%" y2={`${((maxA - 50) / range) * h}`}
            stroke="#334155" strokeWidth="1" strokeDasharray="4,3"
          />
          {/* 55% reference line */}
          <line
            x1="0" y1={`${((maxA - 55) / range) * h}`}
            x2="100%" y2={`${((maxA - 55) / range) * h}`}
            stroke="#475569" strokeWidth="1" strokeDasharray="2,4"
          />
          <polyline
            fill="none"
            stroke={driftWarning ? '#f87171' : '#818cf8'}
            strokeWidth="1.5"
            strokeLinejoin="round"
            points={series.map((p, i) => {
              const x = (i / (series.length - 1)) * 100;
              const y = ((maxA - p.accuracy) / range) * h;
              return `${x}%,${y}`;
            }).join(' ')}
          />
          {/* dots at first and last */}
          {[series[0], series[series.length - 1]].map((p, idx) => {
            const i = idx === 0 ? 0 : series.length - 1;
            const x = (i / (series.length - 1)) * 100;
            const y = ((maxA - p.accuracy) / range) * h;
            return <circle key={idx} cx={`${x}%`} cy={y} r={3} fill={driftWarning ? '#f87171' : '#818cf8'} />;
          })}
        </svg>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#334155', marginTop: 2 }}>
          <span>{series[0]?.date}</span>
          <span style={{ fontSize: 10, color: '#475569' }}>— 50% random  ··· 55% target</span>
          <span>{series[series.length - 1]?.date}</span>
        </div>
      </div>
    </div>
  );
}

const LOOKBACK_OPTIONS = [
  { label: '30d', value: 30 },
  { label: '60d', value: 60 },
  { label: '90d', value: 90 },
  { label: '180d', value: 180 },
];

function pct(n: number | null, digits = 1) {
  return n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function acc(n: number | null) {
  return n == null ? '—' : `${n.toFixed(1)}%`;
}

function FactorBar({ value, color, maxPct = 50 }: { value: number | null; color: string; maxPct?: number }) {
  if (value == null) return <div style={{ height: 10, background: '#1e293b', borderRadius: 3, flex: 1 }} />;
  const clamped = Math.max(-maxPct, Math.min(maxPct, value));
  const pct = Math.abs(clamped) / maxPct * 50; // 50% of bar width each side
  return (
    <div style={{ flex: 1, height: 10, background: '#1e293b', borderRadius: 3, position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: '#334155', zIndex: 1 }} />
      {clamped >= 0
        ? <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: `${pct}%`, background: color, borderRadius: '0 3px 3px 0' }} />
        : <div style={{ position: 'absolute', right: '50%', top: 0, bottom: 0, width: `${pct}%`, background: color, borderRadius: '3px 0 0 3px' }} />
      }
    </div>
  );
}

function MLWeightChart({ curve, optimalWeight, formulaRange, signalCount }: {
  curve: MLWeightCurvePoint[];
  optimalWeight: number | null;
  formulaRange: [number, number];
  signalCount: number;
}) {
  if (!curve.length) return null;
  const accs = curve.map(p => p.accuracy ?? 0);
  const minAcc = Math.min(...accs);
  const maxAcc = Math.max(...accs);
  const range = maxAcc - minAcc || 1;

  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 11, color: '#64748b' }}>
            Empirical sweep across {signalCount} BUY signals (180d). Each bar = accuracy if that blend weight had been used.
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0, marginLeft: 16 }}>
          {optimalWeight != null && (
            <div style={{ fontSize: 13, fontWeight: 700, color: '#4ade80' }}>
              Optimal: {Math.round(optimalWeight * 100)}% ML
            </div>
          )}
          <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
            Current formula: {Math.round(formulaRange[0] * 100)}–{Math.round(formulaRange[1] * 100)}% ML
          </div>
        </div>
      </div>

      {/* Bar chart */}
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 60 }}>
        {curve.map(p => {
          const acc = p.accuracy ?? 0;
          const heightPct = range > 0 ? ((acc - minAcc) / range) * 80 + 20 : 50;
          const isOptimal = p.weight === optimalWeight;
          const inFormula = p.weight >= formulaRange[0] && p.weight <= formulaRange[1];
          const color = isOptimal ? '#4ade80' : inFormula ? '#818cf8' : '#334155';
          return (
            <div key={p.weight} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div title={`w=${p.weight} → ${acc.toFixed(1)}% acc`} style={{
                width: '100%', height: `${heightPct}%`, background: color, borderRadius: '2px 2px 0 0',
                transition: 'background 0.15s',
              }} />
            </div>
          );
        })}
      </div>

      {/* X-axis labels */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
        <span style={{ fontSize: 9, color: '#475569' }}>0% ML (TA only)</span>
        <span style={{ fontSize: 9, color: '#475569' }}>50%</span>
        <span style={{ fontSize: 9, color: '#475569' }}>100% ML</span>
      </div>

      <div style={{ marginTop: 8, display: 'flex', gap: 14, fontSize: 10, color: '#475569' }}>
        <span><span style={{ color: '#4ade80' }}>■</span> Empirical optimum</span>
        <span><span style={{ color: '#818cf8' }}>■</span> Current formula range (40–75%)</span>
        <span><span style={{ color: '#334155' }}>■</span> Outside formula</span>
      </div>
    </div>
  );
}

function FactorChart({ factors }: { factors: FactorRow[] }) {
  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr 60px 1fr 60px', gap: '6px 10px', alignItems: 'center' }}>
        {/* header */}
        <div style={{ fontSize: 10, color: '#475569' }} />
        <div style={{ fontSize: 10, color: '#4ade80', textAlign: 'center' }}>✓ Correct ({factors[0]?.correct_count ?? 0})</div>
        <div style={{ fontSize: 10, color: '#64748b', textAlign: 'center' }}>avg</div>
        <div style={{ fontSize: 10, color: '#f87171', textAlign: 'center' }}>✗ Wrong ({factors[0]?.wrong_count ?? 0})</div>
        <div style={{ fontSize: 10, color: '#64748b', textAlign: 'center' }}>avg</div>

        {factors.map(f => {
          const fmt = (v: number | null) => {
            if (v == null) return '—';
            if (f.key === 'ml_probability') return `${(v * 100).toFixed(0)}%`;
            if (f.key === 'ta_score') return v.toFixed(2);
            if (f.key === 'volume_z') return v.toFixed(2);
            return v.toFixed(1);
          };
          return [
            <div key={f.key + '-label'} style={{ fontSize: 11, color: '#94a3b8', fontWeight: 500 }}>{f.label}</div>,
            <FactorBar key={f.key + '-cb'} value={f.correct_dev_pct} color="#22c55e" />,
            <div key={f.key + '-ca'} style={{ fontSize: 11, color: '#4ade80', textAlign: 'right', fontWeight: 600 }}>{fmt(f.correct_avg)}</div>,
            <FactorBar key={f.key + '-wb'} value={f.wrong_dev_pct} color="#ef4444" />,
            <div key={f.key + '-wa'} style={{ fontSize: 11, color: '#f87171', textAlign: 'right', fontWeight: 600 }}>{fmt(f.wrong_avg)}</div>,
          ];
        })}
      </div>
      <div style={{ marginTop: 10, fontSize: 10, color: '#334155' }}>
        Bars show deviation from neutral baseline (RSI 50, ADX 20, Vol Z 0, ML 50%, Sentiment 50, TA 0.5). Green bar right = factor above neutral for correct signals.
      </div>
    </div>
  );
}

function WalkForwardSection() {
  const [testDays, setTestDays] = useState(30);
  const [holdDays, setHoldDays] = useState(5);
  const [selectedWindow, setSelectedWindow] = useState<WalkForwardWindow | null>(null);

  const { data, isLoading, error } = useSWR<WalkForwardReport>(
    ['walkforward', testDays, holdDays],
    () => api.walkForward(testDays, holdDays, 365),
    { revalidateOnFocus: false },
  );

  const { data: drillData, isLoading: drillLoading } = useSWR<SignalAccuracyReport>(
    selectedWindow ? ['wf-drill', selectedWindow.start, selectedWindow.end] : null,
    () => api.signalAccuracy(90, undefined, selectedWindow!.start, selectedWindow!.end),
    { revalidateOnFocus: false },
  );

  const wfStatCard = (label: string, value: string, color?: string) => (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 16px', minWidth: 110 }}>
      <div style={{ fontSize: 20, fontWeight: 700, color: color ?? '#e2e8f0' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{label}</div>
    </div>
  );

  function windowColor(accuracy: number) {
    if (accuracy >= 65) return '#15803d';
    if (accuracy >= 60) return '#166534';
    if (accuracy >= 55) return '#1e3a5f';
    if (accuracy >= 50) return '#1e293b';
    return '#7f1d1d';
  }

  function windowTextColor(accuracy: number) {
    if (accuracy >= 55) return '#4ade80';
    if (accuracy >= 50) return '#94a3b8';
    return '#f87171';
  }

  if (isLoading) return <div style={{ color: '#64748b', textAlign: 'center', padding: 60 }}>Running walk-forward backtest…</div>;
  if (error) return <div style={{ color: '#f87171', padding: 16 }}>Failed to load walk-forward data.</div>;
  if (!data || data.total_windows === 0) return (
    <div style={{ textAlign: 'center', padding: '60px 0', color: '#475569' }}>
      <div style={{ fontSize: 32, marginBottom: 8 }}>📉</div>
      <div>Not enough signal history for a walk-forward test.</div>
      <div style={{ fontSize: 12, marginTop: 4 }}>Need at least 60 days of BUY signals with settled outcomes. Check back after the system accumulates more history.</div>
    </div>
  );

  const sharpeColor = data.sharpe == null ? undefined : data.sharpe >= 1.0 ? '#4ade80' : data.sharpe >= 0.5 ? '#facc15' : '#f87171';
  const accColor = data.overall_accuracy == null ? undefined : data.overall_accuracy >= 60 ? '#4ade80' : data.overall_accuracy >= 50 ? '#facc15' : '#f87171';
  const retColor = data.total_return_pct == null ? undefined : data.total_return_pct > 0 ? '#4ade80' : '#f87171';

  // Equity curve chart
  const hasEquity = data.windows.length >= 2;
  const equityVals = data.windows.map(w => w.equity);
  const benchVals = data.benchmark?.windows.map(w => w.equity) ?? [];
  const allVals = [...equityVals, ...benchVals, 1.0];
  const minEq = Math.min(...allVals) * 0.98;
  const maxEq = Math.max(...allVals) * 1.02;
  const eqRange = maxEq - minEq || 0.01;
  const chartH = 100;

  function toY(v: number) { return ((maxEq - v) / eqRange) * chartH; }
  function toX(i: number, total: number) { return total <= 1 ? 50 : (i / (total - 1)) * 100; }

  return (
    <div>
      {/* Controls */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 24, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Test window</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {[30, 60].map(v => (
              <button key={v} onClick={() => setTestDays(v)}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                  borderColor: testDays === v ? '#6366f1' : '#1e293b',
                  background: testDays === v ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: testDays === v ? '#818cf8' : '#64748b' }}>
                {v}d
              </button>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Hold period</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {[5, 10].map(v => (
              <button key={v} onClick={() => setHoldDays(v)}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                  borderColor: holdDays === v ? '#6366f1' : '#1e293b',
                  background: holdDays === v ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: holdDays === v ? '#818cf8' : '#64748b' }}>
                {v}d
              </button>
            ))}
          </div>
        </div>
        <div style={{ fontSize: 11, color: '#334155', marginLeft: 'auto' }}>
          365d lookback · {data.signal_count} BUY signals across {data.total_windows} test windows
        </div>
      </div>

      {/* Stat cards */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 24 }}>
        {wfStatCard('Out-of-Sample Accuracy', data.overall_accuracy != null ? `${data.overall_accuracy.toFixed(1)}%` : '—', accColor)}
        {wfStatCard('Sharpe Ratio', data.sharpe != null ? data.sharpe.toFixed(2) : '—', sharpeColor)}
        {wfStatCard('Total Return', data.total_return_pct != null ? `${data.total_return_pct > 0 ? '+' : ''}${data.total_return_pct.toFixed(1)}%` : '—', retColor)}
        {wfStatCard('Max Drawdown', data.max_drawdown != null ? `${data.max_drawdown.toFixed(1)}%` : '—', data.max_drawdown != null && data.max_drawdown > 10 ? '#f87171' : '#94a3b8')}
        {wfStatCard('Profitable Windows', `${data.profitable_windows} / ${data.total_windows}`, data.profitable_windows > data.total_windows / 2 ? '#4ade80' : '#f87171')}
        {data.benchmark && wfStatCard(`vs ${data.benchmark.symbol}`, `${data.benchmark.total_return_pct > 0 ? '+' : ''}${data.benchmark.total_return_pct.toFixed(1)}%`, '#64748b')}
      </div>

      {/* Sharpe interpretation */}
      {data.sharpe != null && (
        <div style={{ marginBottom: 20, padding: '10px 14px', borderRadius: 8, border: '1px solid #1e293b', background: '#0f172a', fontSize: 12 }}>
          <span style={{ color: '#64748b' }}>Signal alpha assessment: </span>
          <span style={{ color: sharpeColor, fontWeight: 600 }}>
            {data.sharpe >= 1.0 ? 'Sharpe ≥ 1.0 — signals generating real out-of-sample alpha'
             : data.sharpe >= 0.5 ? 'Sharpe 0.5–1.0 — modest edge, worth monitoring as sample grows'
             : 'Sharpe < 0.5 — limited out-of-sample edge detected, possible curve-fitting'}
          </span>
        </div>
      )}

      {/* Per-window heatmap */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>Per-window accuracy heatmap <span style={{ fontSize: 10, fontWeight: 400, color: '#475569' }}>— click a cell to inspect signals</span></div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {(data.windows as WalkForwardWindow[]).map((w, i) => {
            const isSelected = selectedWindow?.start === w.start;
            return (
              <div key={i}
                onClick={() => setSelectedWindow(isSelected ? null : w)}
                title={`${w.start} – ${w.end}\n${w.n_signals} signals · ${w.accuracy}% · avg ${w.avg_return_pct > 0 ? '+' : ''}${w.avg_return_pct.toFixed(1)}%`}
                style={{ background: windowColor(w.accuracy), borderRadius: 6, padding: '6px 10px', minWidth: 52, textAlign: 'center', cursor: 'pointer',
                  outline: isSelected ? '2px solid #818cf8' : 'none', outlineOffset: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: windowTextColor(w.accuracy) }}>{w.accuracy.toFixed(0)}%</div>
                <div style={{ fontSize: 9, color: '#475569', marginTop: 1 }}>{w.start.slice(5)}</div>
                <div style={{ fontSize: 9, color: w.avg_return_pct > 0 ? '#4ade80' : '#f87171', marginTop: 1 }}>
                  {w.avg_return_pct > 0 ? '+' : ''}{w.avg_return_pct.toFixed(1)}%
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 10, color: '#475569' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ display: 'inline-block', width: 10, height: 10, background: '#15803d', borderRadius: 2 }} /> ≥65%</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ display: 'inline-block', width: 10, height: 10, background: '#1e3a5f', borderRadius: 2 }} /> 55–64%</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ display: 'inline-block', width: 10, height: 10, background: '#1e293b', borderRadius: 2 }} /> 50–54%</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ display: 'inline-block', width: 10, height: 10, background: '#7f1d1d', borderRadius: 2 }} /> &lt;50%</span>
        </div>
      </div>

      {/* Drill-down panel */}
      {selectedWindow && (
        <div style={{ marginBottom: 24, border: '1px solid #312e81', borderRadius: 10, background: '#0f0f2a', padding: '16px 18px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#818cf8' }}>
                Window {selectedWindow.start} – {selectedWindow.end}
              </div>
              <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
                {selectedWindow.n_signals} signals · {selectedWindow.accuracy.toFixed(0)}% accuracy · avg {selectedWindow.avg_return_pct > 0 ? '+' : ''}{selectedWindow.avg_return_pct.toFixed(1)}%
              </div>
            </div>
            <button onClick={() => setSelectedWindow(null)}
              style={{ background: 'none', border: '1px solid #1e293b', borderRadius: 6, color: '#475569', padding: '3px 10px', cursor: 'pointer', fontSize: 12 }}>
              ✕ Close
            </button>
          </div>
          {drillLoading && <div style={{ color: '#475569', fontSize: 12, padding: '12px 0' }}>Loading signals…</div>}
          {!drillLoading && drillData && drillData.signals.length === 0 && (
            <div style={{ color: '#475569', fontSize: 12 }}>No evaluated signals found for this window.</div>
          )}
          {!drillLoading && drillData && drillData.signals.length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #1e293b' }}>
                    {['Symbol', 'Date', 'Signal', 'Conf%', 'Entry', 'Exit', 'Return', 'Result'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '4px 8px', color: '#475569', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...drillData.signals].sort((a, b) => Number(b.correct) - Number(a.correct)).map((s, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid #0f172a' }}>
                      <td style={{ padding: '5px 8px', fontWeight: 700, color: '#e2e8f0' }}>
                        <Link href={`/stock/${s.symbol}`} style={{ color: '#e2e8f0', textDecoration: 'none' }}>{s.symbol}</Link>
                      </td>
                      <td style={{ padding: '5px 8px', color: '#64748b' }}>{s.signal_date}</td>
                      <td style={{ padding: '5px 8px' }}>
                        <span style={{ fontWeight: 700, color: s.signal === 'BUY' ? '#4ade80' : '#f87171' }}>{s.signal}</span>
                      </td>
                      <td style={{ padding: '5px 8px', color: '#94a3b8' }}>{s.confidence != null ? `${(s.confidence * 100).toFixed(0)}%` : '—'}</td>
                      <td style={{ padding: '5px 8px', color: '#64748b' }}>{s.entry_price.toFixed(2)}</td>
                      <td style={{ padding: '5px 8px', color: '#64748b' }}>{s.exit_price.toFixed(2)}</td>
                      <td style={{ padding: '5px 8px', fontWeight: 600, color: s.pct_change > 0 ? '#4ade80' : '#f87171' }}>
                        {s.pct_change > 0 ? '+' : ''}{s.pct_change.toFixed(1)}%
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <span style={{ padding: '2px 7px', borderRadius: 4, fontSize: 10, fontWeight: 700,
                          background: s.correct ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)',
                          color: s.correct ? '#4ade80' : '#f87171',
                          border: `1px solid ${s.correct ? 'rgba(74,222,128,0.3)' : 'rgba(248,113,113,0.3)'}` }}>
                          {s.correct ? '✓ Correct' : '✗ Wrong'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ marginTop: 8, fontSize: 11, color: '#475569' }}>
                Correct: {drillData.signals.filter(s => s.correct).length} / {drillData.signals.length}
                {drillData.overall_accuracy != null && <> · Window accuracy: <span style={{ color: drillData.overall_accuracy >= 60 ? '#4ade80' : drillData.overall_accuracy >= 50 ? '#fbbf24' : '#f87171' }}>{drillData.overall_accuracy.toFixed(1)}%</span></>}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Equity curve */}
      {hasEquity && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8' }}>Walk-Forward Equity Curve</div>
              <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>Compounded average return per test window vs {data.benchmark?.symbol ?? 'benchmark'} buy-and-hold</div>
            </div>
            <div style={{ display: 'flex', gap: 14, fontSize: 11 }}>
              <span style={{ color: '#4ade80' }}>— Signals</span>
              {data.benchmark && <span style={{ color: '#64748b' }}>— {data.benchmark.symbol}</span>}
            </div>
          </div>
          <div style={{ position: 'relative', height: chartH + 24 }}>
            <svg width="100%" height={chartH} style={{ overflow: 'visible' }}>
              {/* Baseline at 1.0 */}
              <line x1="0" y1={toY(1.0)} x2="100%" y2={toY(1.0)}
                stroke="#334155" strokeWidth="1" strokeDasharray="4,3" />
              {/* Benchmark line */}
              {data.benchmark && data.benchmark.windows.length >= 2 && (
                <polyline fill="none" stroke="#475569" strokeWidth="1.5" strokeLinejoin="round"
                  points={data.benchmark.windows.map((bw, i) => {
                    const x = toX(i, data.benchmark!.windows.length);
                    const y = toY(bw.equity);
                    return `${x}%,${y}`;
                  }).join(' ')} />
              )}
              {/* Signals equity line */}
              <polyline fill="none" stroke="#4ade80" strokeWidth="2" strokeLinejoin="round"
                points={data.windows.map((w, i) => {
                  const x = toX(i, data.windows.length);
                  const y = toY(w.equity);
                  return `${x}%,${y}`;
                }).join(' ')} />
              {/* Start/end dots */}
              {[0, data.windows.length - 1].map(idx => (
                <circle key={idx}
                  cx={`${toX(idx, data.windows.length)}%`}
                  cy={toY(data.windows[idx].equity)}
                  r={3} fill="#4ade80" />
              ))}
            </svg>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#334155', marginTop: 2 }}>
              <span>{data.windows[0]?.start}</span>
              <span style={{ color: '#475569' }}>— 1.0× baseline</span>
              <span>{data.windows[data.windows.length - 1]?.end}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const DECAY_HORIZONS = ['SWING', 'SHORT', 'LONG', 'GROWTH'] as const;
const DECAY_LOOKBACKS = [
  { label: '90d', value: 90 },
  { label: '180d', value: 180 },
  { label: '365d', value: 365 },
];

function AlphaDecaySection() {
  const [horizon, setHorizon] = useState<typeof DECAY_HORIZONS[number]>('SWING');
  const [lookbackDays, setLookbackDays] = useState(180);

  const { data, isLoading, error } = useSWR<AlphaDecayReport>(
    ['alpha-decay', horizon, lookbackDays],
    () => api.alphaDecay(horizon, lookbackDays),
    { revalidateOnFocus: false },
  );

  const curve: AlphaDecayCurvePoint[] = data?.curve ?? [];
  const hasData = curve.length > 0 && curve.some(p => p.avg_return_pct != null);

  // SVG chart setup
  const chartW = 560;
  const chartH = 120;
  const padL = 36;
  const padR = 10;
  const padT = 10;
  const padB = 24;
  const innerW = chartW - padL - padR;
  const innerH = chartH - padT - padB;

  const returns = curve.map(p => p.avg_return_pct ?? 0);
  const p25s = curve.map(p => p.p25 ?? 0);
  const p75s = curve.map(p => p.p75 ?? 0);
  const allVals = [...returns, ...p25s, ...p75s, 0];
  const minV = Math.min(...allVals) - 0.5;
  const maxV = Math.max(...allVals) + 0.5;
  const range = maxV - minV || 1;

  function toX(i: number) {
    return padL + (i / Math.max(curve.length - 1, 1)) * innerW;
  }
  function toY(v: number) {
    return padT + ((maxV - v) / range) * innerH;
  }

  const zeroY = toY(0);
  const avgPoints = curve.map((p, i) => `${toX(i)},${toY(p.avg_return_pct ?? 0)}`).join(' ');
  const bandPoints = [
    ...curve.map((p, i) => `${toX(i)},${toY(p.p75 ?? (p.avg_return_pct ?? 0))}`),
    ...[...curve].reverse().map((p, i) => `${toX(curve.length - 1 - i)},${toY(p.p25 ?? (p.avg_return_pct ?? 0))}`),
  ].join(' ');

  return (
    <div>
      {/* Controls */}
      <div style={{ display: 'flex', gap: 20, marginBottom: 20, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Horizon</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {DECAY_HORIZONS.map(h => (
              <button key={h} onClick={() => setHorizon(h)}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                  borderColor: horizon === h ? '#6366f1' : '#1e293b',
                  background: horizon === h ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: horizon === h ? '#818cf8' : '#64748b' }}>
                {h}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Lookback</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {DECAY_LOOKBACKS.map(o => (
              <button key={o.value} onClick={() => setLookbackDays(o.value)}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                  borderColor: lookbackDays === o.value ? '#6366f1' : '#1e293b',
                  background: lookbackDays === o.value ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: lookbackDays === o.value ? '#818cf8' : '#64748b' }}>
                {o.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading && <div style={{ color: '#64748b', textAlign: 'center', padding: 60 }}>Computing alpha decay curve…</div>}
      {error && <div style={{ color: '#f87171', padding: 16 }}>Failed to load decay data.</div>}

      {!isLoading && !error && !hasData && (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#475569' }}>
          <div style={{ fontSize: 32, marginBottom: 8 }}>📉</div>
          <div>Not enough signal history for {horizon} decay analysis.</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>Need BUY signals with settled outcomes in the {lookbackDays}d window. Try a longer lookback.</div>
        </div>
      )}

      {!isLoading && !error && hasData && data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Optimal hold chip */}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 16px' }}>
              <div style={{ fontSize: 11, color: '#64748b' }}>Signal count ({lookbackDays}d)</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', marginTop: 2 }}>{data.signal_count}</div>
            </div>
            {data.optimal_hold_days != null && (
              <div style={{ background: '#0f172a', border: '1px solid rgba(74,222,128,0.3)', borderRadius: 8, padding: '10px 16px' }}>
                <div style={{ fontSize: 11, color: '#64748b' }}>Optimal hold — {horizon}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: '#4ade80', marginTop: 2 }}>
                  {data.optimal_hold_days}d
                  {data.optimal_return_pct != null && (
                    <span style={{ fontSize: 13, color: '#86efac', marginLeft: 8 }}>
                      (peak α = {data.optimal_return_pct >= 0 ? '+' : ''}{data.optimal_return_pct.toFixed(2)}%)
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
                  Based on {data.signal_count} {horizon} BUY signals
                </div>
              </div>
            )}
          </div>

          {/* SVG line chart */}
          <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4 }}>
              Cumulative return after BUY signal — {horizon} ({lookbackDays}d lookback)
            </div>
            <div style={{ fontSize: 11, color: '#475569', marginBottom: 12 }}>
              Avg return at each day post-entry. Band = 25th–75th percentile range.
            </div>
            <div style={{ overflowX: 'auto' }}>
              <svg width={chartW} height={chartH} style={{ display: 'block' }}>
                {/* Zero line */}
                <line x1={padL} y1={zeroY} x2={chartW - padR} y2={zeroY}
                  stroke="#334155" strokeWidth={1} strokeDasharray="4,3" />

                {/* p25–p75 band */}
                {curve.length >= 2 && (
                  <polygon points={bandPoints} fill="rgba(99,102,241,0.12)" />
                )}

                {/* Optimal day marker */}
                {data.optimal_hold_days != null && (() => {
                  const optIdx = curve.findIndex(p => p.day === data.optimal_hold_days);
                  if (optIdx < 0) return null;
                  const ox = toX(optIdx);
                  return (
                    <line x1={ox} y1={padT} x2={ox} y2={chartH - padB}
                      stroke="rgba(74,222,128,0.4)" strokeWidth={1} strokeDasharray="3,3" />
                  );
                })()}

                {/* Avg return line */}
                {curve.length >= 2 && (
                  <polyline
                    fill="none"
                    stroke="#818cf8"
                    strokeWidth={2}
                    strokeLinejoin="round"
                    points={avgPoints}
                  />
                )}

                {/* Data point dots */}
                {curve.map((p, i) => {
                  if (p.avg_return_pct == null) return null;
                  const isOptimal = p.day === data.optimal_hold_days;
                  return (
                    <circle key={p.day}
                      cx={toX(i)} cy={toY(p.avg_return_pct)}
                      r={isOptimal ? 5 : 3}
                      fill={isOptimal ? '#4ade80' : '#818cf8'}
                    >
                      <title>{`Day ${p.day}: ${p.avg_return_pct >= 0 ? '+' : ''}${p.avg_return_pct.toFixed(2)}% (n=${p.n})`}</title>
                    </circle>
                  );
                })}

                {/* Y-axis labels */}
                {[minV, 0, maxV].map(v => (
                  <text key={v} x={padL - 4} y={toY(v) + 4}
                    textAnchor="end" fontSize={9} fill="#475569">
                    {v >= 0 ? '+' : ''}{v.toFixed(1)}%
                  </text>
                ))}

                {/* X-axis day labels */}
                {curve.map((p, i) => (
                  <text key={p.day} x={toX(i)} y={chartH - 4}
                    textAnchor="middle" fontSize={9} fill="#475569">
                    {p.day}d
                  </text>
                ))}
              </svg>
            </div>
            <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 10, color: '#475569' }}>
              <span><span style={{ color: '#818cf8' }}>—</span> Avg return</span>
              <span><span style={{ color: 'rgba(99,102,241,0.4)' }}>■</span> p25–p75 band</span>
              <span><span style={{ color: 'rgba(74,222,128,0.5)' }}>|</span> Optimal hold day</span>
            </div>
          </div>

          {/* Per-day table */}
          <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 10 }}>Per-day breakdown</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #1e293b' }}>
                    {['Hold Day', 'Avg Return', 'p25', 'p75', 'N signals'].map(h => (
                      <th key={h} style={{ padding: '4px 10px', textAlign: 'left', color: '#475569', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {curve.map(p => {
                    const isOpt = p.day === data.optimal_hold_days;
                    const r = p.avg_return_pct;
                    return (
                      <tr key={p.day} style={{ borderBottom: '1px solid #0f172a', background: isOpt ? 'rgba(74,222,128,0.05)' : 'transparent' }}>
                        <td style={{ padding: '5px 10px', color: isOpt ? '#4ade80' : '#94a3b8', fontWeight: isOpt ? 700 : 400 }}>
                          {p.day}d {isOpt && <span style={{ fontSize: 10, color: '#4ade80' }}>★ optimal</span>}
                        </td>
                        <td style={{ padding: '5px 10px', fontWeight: 600, color: r == null ? '#334155' : r >= 0 ? '#4ade80' : '#f87171' }}>
                          {r == null ? '—' : `${r >= 0 ? '+' : ''}${r.toFixed(2)}%`}
                        </td>
                        <td style={{ padding: '5px 10px', color: p.p25 == null ? '#334155' : p.p25 >= 0 ? '#86efac' : '#fca5a5' }}>
                          {p.p25 == null ? '—' : `${p.p25 >= 0 ? '+' : ''}${p.p25.toFixed(2)}%`}
                        </td>
                        <td style={{ padding: '5px 10px', color: p.p75 == null ? '#334155' : p.p75 >= 0 ? '#86efac' : '#fca5a5' }}>
                          {p.p75 == null ? '—' : `${p.p75 >= 0 ? '+' : ''}${p.p75.toFixed(2)}%`}
                        </td>
                        <td style={{ padding: '5px 10px', color: '#64748b' }}>{p.n ?? '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ fontSize: 11, color: '#334155', padding: '8px 12px', background: '#0a0f1a', borderRadius: 6 }}>
            ℹ️ Entry = first daily close on or after BUY signal date. Return = (close at day N − entry) / entry. Up to 5 calendar-day slippage allowed for weekends/holidays.
          </div>
        </div>
      )}
    </div>
  );
}

export default function SignalAccuracyPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    if (s.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const [activeTab, setActiveTab] = useState<'overview' | 'walkforward' | 'outcomes' | 'decay' | 'ic' | 'attribution' | 'filter-audit'>('overview');
  const [lookback, setLookback] = useState(90);
  const [filterSymbol, setFilterSymbol] = useState('');
  const [signalFilter, setSignalFilter] = useState<'ALL' | 'BUY' | 'SELL'>('ALL');
  const [showOnly, setShowOnly] = useState<'ALL' | 'CORRECT' | 'WRONG'>('ALL');
  const [sortBy, setSortBy] = useState<'date' | 'confidence' | 'pct_change'>('date');
  const [resetting, setResetting] = useState(false);
  const [resetMsg, setResetMsg] = useState('');
  const [evaluating, setEvaluating] = useState(false);
  const [evaluateMsg, setEvaluateMsg] = useState('');
  const [calibrating, setCalibrating] = useState(false);
  const [calibrateResult, setCalibrateResult] = useState<{ optimal_weight: number | null; optimal_accuracy: number; applied: boolean } | null>(null);
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [page, setPage] = useState(1);

  const useDateRange = fromDate !== '' && toDate !== '';

  const { data, isLoading, error, mutate } = useSWR(
    authed ? ['signal-accuracy', lookback, fromDate, toDate, page] : null,
    () => api.signalAccuracy(lookback, undefined, fromDate || undefined, toDate || undefined, page),
    { revalidateOnFocus: false },
  );

  const { data: factorData } = useSWR(
    authed ? ['factor-exposure', lookback] : null,
    () => api.factorExposure(lookback),
    { revalidateOnFocus: false },
  );

  const { data: mlWeight } = useSWR(
    authed ? 'ml-weight-validation' : null,
    () => api.mlWeightValidation(180),
    { revalidateOnFocus: false },
  );

  const [outcomesMarket, setOutcomesMarket] = useState<'ALL' | 'US' | 'HK'>('ALL');
  const { data: outcomesData, mutate: mutateOutcomes } = useSWR<OutcomesSummary>(
    authed ? ['outcomes-summary', lookback, outcomesMarket] : null,
    () => api.outcomesSummary(undefined, lookback, outcomesMarket === 'ALL' ? undefined : outcomesMarket),
    { revalidateOnFocus: false },
  );
  const { data: calibrationData } = useSWR<OutcomesCalibration>(
    authed ? 'outcomes-calibrate' : null,
    () => api.calibrateOutcomes(180, 15),
    { revalidateOnFocus: false },
  );

  const { data: rollingData } = useSWR(
    authed ? 'rolling-accuracy' : null,
    () => api.rollingAccuracy(30, 180),
    { revalidateOnFocus: false },
  );

  const [icHorizon, setIcHorizon] = useState('SWING');
  const { data: icData } = useSWR(
    authed && activeTab === 'ic' ? ['ic', icHorizon] : null,
    () => api.informationCoefficient(icHorizon, 365),
    { revalidateOnFocus: false },
  );

  const [attrHorizon, setAttrHorizon] = useState('SWING');
  const { data: attrData } = useSWR(
    authed && activeTab === 'attribution' ? ['factor-attribution', attrHorizon] : null,
    () => api.factorAttribution(attrHorizon, 365, 10),
    { revalidateOnFocus: false },
  );

  const [faStyle, setFaStyle] = useState('SWING');
  const [faHold, setFaHold] = useState(10);
  const { data: filterAuditData } = useSWR(
    authed && activeTab === 'filter-audit' ? ['filter-audit', faStyle, faHold, lookback] : null,
    () => api.filterAudit(lookback, faStyle, faHold),
    { revalidateOnFocus: false },
  );

  const { data: wfOverview } = useSWR<WalkForwardReport>(
    authed ? 'wf-overview-panel' : null,
    () => api.walkForward(30, 5, 365),
    { revalidateOnFocus: false },
  );

  if (!authed) return null;

  async function handleReset() {
    if (!confirm('Wipe all persisted signals and re-persist fresh ones? This cannot be undone.')) return;
    setResetting(true);
    setResetMsg('Wiping signals…');
    try {
      const res = await api.resetSignals();
      setResetMsg(`Deleted ${res.deleted} signals. Re-persisting ${res.repersisting} stocks in background…`);
      setTimeout(() => { mutate(); setResetMsg(''); }, 5000);
    } catch {
      setResetMsg('Reset failed.');
    } finally {
      setResetting(false);
    }
  }

  const rows: SignalAccuracyRow[] = (data?.signals ?? []).filter(r => {
    if (filterSymbol && !r.symbol.includes(filterSymbol.toUpperCase())) return false;
    if (signalFilter !== 'ALL' && r.signal !== signalFilter) return false;
    if (showOnly === 'CORRECT' && !r.correct) return false;
    if (showOnly === 'WRONG' && r.correct) return false;
    return true;
  }).sort((a, b) => {
    if (sortBy === 'date') return b.signal_date.localeCompare(a.signal_date);
    if (sortBy === 'confidence') return b.confidence - a.confidence;
    return b.pct_change - a.pct_change;
  });

  const statCard = (label: string, value: string, sub?: string, color?: string) => (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 16px', minWidth: 110 }}>
      <div style={{ fontSize: 22, fontWeight: 700, color: color ?? '#e2e8f0' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: '#475569', marginTop: 1 }}>{sub}</div>}
    </div>
  );

  const overallColor = data?.overall_accuracy != null
    ? data.overall_accuracy >= 60 ? '#4ade80' : data.overall_accuracy >= 50 ? '#facc15' : '#f87171'
    : undefined;

  return (
    <div style={{ padding: '24px 0' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>Signal Accuracy Tracker</h1>
          <p style={{ fontSize: 13, color: '#64748b' }}>
            How often did past BUY/SELL signals predict the correct direction within ~5 trading days?
          </p>
          {resetMsg && <p style={{ fontSize: 12, color: '#f97316', marginTop: 4 }}>{resetMsg}</p>}
        </div>
        <button
          onClick={handleReset}
          disabled={resetting}
          style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: resetting ? 'not-allowed' : 'pointer',
            border: '1px solid #7f1d1d', background: 'rgba(127,29,29,0.2)', color: '#f87171',
            opacity: resetting ? 0.5 : 1, whiteSpace: 'nowrap', marginTop: 4 }}>
          {resetting ? 'Resetting…' : 'Reset Signals'}
        </button>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid #1e293b', paddingBottom: 0 }}>
        {([['overview', 'Overview'], ['walkforward', 'Walk-Forward'], ['outcomes', 'Outcomes'], ['decay', 'Alpha Decay'], ['ic', 'IC Score'], ['attribution', 'Factor Attribution'], ['filter-audit', 'Filter Audit']] as const).map(([tab, label]) => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            style={{ padding: '7px 18px', borderRadius: '6px 6px 0 0', fontSize: 13, fontWeight: 500, cursor: 'pointer',
              border: '1px solid', borderBottom: activeTab === tab ? '1px solid #0f172a' : '1px solid transparent',
              borderColor: activeTab === tab ? '#1e293b' : 'transparent',
              background: activeTab === tab ? '#0f172a' : 'transparent',
              color: activeTab === tab ? '#e2e8f0' : '#64748b',
              marginBottom: activeTab === tab ? -1 : 0 }}>
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'walkforward' && <WalkForwardSection />}
      {activeTab === 'decay' && <AlphaDecaySection />}

      {activeTab === 'outcomes' && (
        <div>
          {/* Market filter */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
            {(['ALL', 'US', 'HK'] as const).map(m => (
              <button key={m} onClick={() => setOutcomesMarket(m)}
                style={{ padding: '5px 14px', borderRadius: 6, border: '1px solid',
                  borderColor: outcomesMarket === m ? '#60a5fa' : '#334155',
                  background: outcomesMarket === m ? 'rgba(96,165,250,0.12)' : 'transparent',
                  color: outcomesMarket === m ? '#60a5fa' : '#64748b', fontSize: 12, cursor: 'pointer' }}>
                {m}
              </button>
            ))}
            <span style={{ fontSize: 11, color: '#475569', alignSelf: 'center', marginLeft: 4 }}>market</span>
          </div>
          {!outcomesData || outcomesData.total === 0 ? (
            <div style={{ textAlign: 'center', padding: '60px 0', color: '#475569' }}>
              <div style={{ fontSize: 36, marginBottom: 8 }}>📊</div>
              <div>No evaluated outcomes yet.</div>
              <div style={{ fontSize: 12, marginTop: 4, color: '#334155' }}>
                signal_outcomes are recorded when a BUY/SELL signal matures past its hold window (SHORT=7d, SWING=14d, LONG=28d).
                Data accumulates automatically — check back after ~2 weeks of daily signal runs.
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Data coverage note + Evaluate Now button */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                {outcomesData.date_range?.oldest && (
                  <div style={{ flex: 1, background: 'rgba(15,23,42,0.8)', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 14px', fontSize: 11, color: '#64748b', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: '#475569' }}>📅</span>
                    <span>
                      <strong style={{ color: '#94a3b8' }}>Evaluated signals:</strong>{' '}
                      {outcomesData.date_range.oldest} → {outcomesData.date_range.newest}.{' '}
                      SWING/LONG outcomes take 14–28 days to mature — post-SA-31 data (buy_threshold raised Jun 17) will appear as signals from Jun 3+ mature.
                    </span>
                  </div>
                )}
                <button
                  disabled={evaluating}
                  onClick={async () => {
                    setEvaluating(true); setEvaluateMsg('');
                    try {
                      const r = await api.evaluateOutcomes();
                      setEvaluateMsg(`✓ ${r.evaluated} new, ${r.updated_windows} windows updated`);
                      mutateOutcomes();
                    } catch { setEvaluateMsg('Failed — check signal-engine logs'); }
                    finally { setEvaluating(false); }
                  }}
                  style={{ padding: '8px 14px', background: evaluating ? '#1e293b' : 'rgba(96,165,250,0.1)', border: '1px solid #3b82f6', borderRadius: 6, color: '#60a5fa', fontSize: 12, cursor: evaluating ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap' }}
                >
                  {evaluating ? 'Evaluating…' : 'Evaluate Now'}
                </button>
                {evaluateMsg && <span style={{ fontSize: 11, color: evaluateMsg.startsWith('✓') ? '#4ade80' : '#f87171' }}>{evaluateMsg}</span>}
              </div>
              {/* Overall */}
              {outcomesData.overall && (
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 10 }}>
                    Overall — {outcomesData.total} evaluated signals ({outcomesData.days_lookback}d window)
                  </div>
                  <div style={{ display: 'flex', gap: 24 }}>
                    <div>
                      <div style={{ fontSize: 22, fontWeight: 800, color: outcomesData.overall.win_rate >= 0.55 ? '#4ade80' : outcomesData.overall.win_rate >= 0.50 ? '#facc15' : '#f87171' }}>
                        {(outcomesData.overall.win_rate * 100).toFixed(1)}%
                      </div>
                      <div style={{ fontSize: 11, color: '#475569' }}>Win rate</div>
                    </div>
                    {outcomesData.overall.avg_return_pct != null && (
                      <div>
                        <div style={{ fontSize: 22, fontWeight: 800, color: outcomesData.overall.avg_return_pct >= 0 ? '#4ade80' : '#f87171' }}>
                          {outcomesData.overall.avg_return_pct >= 0 ? '+' : ''}{outcomesData.overall.avg_return_pct.toFixed(2)}%
                        </div>
                        <div style={{ fontSize: 11, color: '#475569' }}>Avg return</div>
                      </div>
                    )}
                    {outcomesData.overall.median_return_pct != null && (
                      <div>
                        <div style={{ fontSize: 22, fontWeight: 800, color: outcomesData.overall.median_return_pct >= 0 ? '#4ade80' : '#f87171' }}>
                          {outcomesData.overall.median_return_pct >= 0 ? '+' : ''}{outcomesData.overall.median_return_pct.toFixed(2)}%
                        </div>
                        <div style={{ fontSize: 11, color: '#475569' }}>Median return</div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Confidence band table */}
              {outcomesData.by_confidence_band && outcomesData.by_confidence_band.length > 0 && (
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 10 }}>
                    Win Rate by Confidence Band — confirms confidence % is calibrated
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ color: '#475569', textAlign: 'left' }}>
                        <th style={{ padding: '4px 8px' }}>Confidence</th>
                        <th style={{ padding: '4px 8px', textAlign: 'right' }}>Signals</th>
                        <th style={{ padding: '4px 8px', textAlign: 'right' }}>Win Rate</th>
                        <th style={{ padding: '4px 8px', textAlign: 'right' }}>Avg Return</th>
                        <th style={{ padding: '4px 8px' }}>Calibration</th>
                      </tr>
                    </thead>
                    <tbody>
                      {outcomesData.by_confidence_band.map(b => {
                        const wr = b.win_rate * 100;
                        const wrColor = wr >= 60 ? '#4ade80' : wr >= 50 ? '#facc15' : '#f87171';
                        const barW = Math.min(100, wr * 2);
                        return (
                          <tr key={b.band} style={{ borderTop: '1px solid #1e293b' }}>
                            <td style={{ padding: '6px 8px', color: '#e2e8f0', fontWeight: 600 }}>{b.band}%</td>
                            <td style={{ padding: '6px 8px', textAlign: 'right', color: '#94a3b8' }}>{b.count}</td>
                            <td style={{ padding: '6px 8px', textAlign: 'right', color: wrColor, fontWeight: 700 }}>{wr.toFixed(1)}%</td>
                            <td style={{ padding: '6px 8px', textAlign: 'right', color: b.avg_return_pct != null ? (b.avg_return_pct >= 0 ? '#4ade80' : '#f87171') : '#475569' }}>
                              {b.avg_return_pct != null ? `${b.avg_return_pct >= 0 ? '+' : ''}${b.avg_return_pct.toFixed(2)}%` : '—'}
                            </td>
                            <td style={{ padding: '6px 8px' }}>
                              <div style={{ height: 8, background: '#1e293b', borderRadius: 4, overflow: 'hidden', width: 80 }}>
                                <div style={{ height: '100%', width: `${barW}%`, background: wrColor, borderRadius: 4 }} />
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  <div style={{ fontSize: 10, color: '#334155', marginTop: 8 }}>
                    Goal: higher confidence bands should show higher win rates. Flat bars = confidence not calibrated — trigger SA-5/SA-6 Optuna tuning.
                  </div>
                </div>
              )}

              {/* By horizon and regime in 2-col grid */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                {outcomesData.by_horizon && Object.keys(outcomesData.by_horizon).length > 0 && (
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 10 }}>Win Rate by Horizon</div>
                    {Object.entries(outcomesData.by_horizon).map(([h, v]) => (
                      <div key={h} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderTop: '1px solid #1e293b', fontSize: 12 }}>
                        <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{h}</span>
                        <span style={{ color: '#94a3b8' }}>{v.count} signals</span>
                        <span style={{ color: v.win_rate >= 0.55 ? '#4ade80' : v.win_rate >= 0.50 ? '#facc15' : '#f87171', fontWeight: 700 }}>
                          {(v.win_rate * 100).toFixed(1)}%
                        </span>
                        <span style={{ color: v.avg_return_pct != null ? (v.avg_return_pct >= 0 ? '#4ade80' : '#f87171') : '#475569', fontSize: 11 }}>
                          {v.avg_return_pct != null ? `${v.avg_return_pct >= 0 ? '+' : ''}${v.avg_return_pct.toFixed(2)}%` : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {outcomesData.by_direction && Object.keys(outcomesData.by_direction).length > 0 && (
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 10 }}>Win Rate by Direction</div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'auto auto auto auto auto', gap: '4px 12px', fontSize: 11, marginBottom: 6 }}>
                      <span style={{ color: '#475569', fontWeight: 600 }}>Style</span>
                      <span style={{ color: '#475569', fontWeight: 600 }}>Dir</span>
                      <span style={{ color: '#475569', fontWeight: 600, textAlign: 'right' }}>n</span>
                      <span style={{ color: '#475569', fontWeight: 600, textAlign: 'right' }}>Win %</span>
                      <span style={{ color: '#475569', fontWeight: 600, textAlign: 'right' }}>Avg Ret</span>
                    </div>
                    {Object.entries(outcomesData.by_direction).map(([key, v]) => {
                      const [h, dir] = key.split('/');
                      const wr = v.win_rate * 100;
                      return (
                        <div key={key} style={{ display: 'grid', gridTemplateColumns: 'auto auto auto auto auto', gap: '4px 12px', fontSize: 12, padding: '4px 0', borderTop: '1px solid #1e293b', alignItems: 'center' }}>
                          <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{h}</span>
                          <span style={{ color: dir === 'BUY' ? '#4ade80' : '#f87171', fontWeight: 700, fontSize: 11 }}>{dir}</span>
                          <span style={{ color: '#64748b', textAlign: 'right' }}>{v.count}</span>
                          <span style={{ color: wr >= 55 ? '#4ade80' : wr >= 50 ? '#facc15' : '#f87171', fontWeight: 700, textAlign: 'right' }}>
                            {wr.toFixed(1)}%
                          </span>
                          <span style={{ color: v.avg_return_pct != null ? (v.avg_return_pct >= 0 ? '#4ade80' : '#f87171') : '#475569', textAlign: 'right' }}>
                            {v.avg_return_pct != null ? `${v.avg_return_pct >= 0 ? '+' : ''}${v.avg_return_pct.toFixed(2)}%` : '—'}
                          </span>
                        </div>
                      );
                    })}
                    <div style={{ fontSize: 10, color: '#334155', marginTop: 8 }}>
                      Large BUY/SELL gap = directional bias. BUY accuracy below 40% = signal too bullish; consider raising buy_threshold.
                    </div>
                  </div>
                )}
                {outcomesData.by_market_regime && Object.keys(outcomesData.by_market_regime).length > 0 && (
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 10 }}>Win Rate by Market Regime</div>
                    {Object.entries(outcomesData.by_market_regime).map(([r, v]) => (
                      <div key={r} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderTop: '1px solid #1e293b', fontSize: 12 }}>
                        <span style={{ color: '#e2e8f0', fontWeight: 600, textTransform: 'capitalize' }}>{r.replace(/_/g, ' ')}</span>
                        <span style={{ color: '#94a3b8' }}>{v.count}</span>
                        <span style={{ color: v.win_rate >= 0.55 ? '#4ade80' : v.win_rate >= 0.50 ? '#facc15' : '#f87171', fontWeight: 700 }}>
                          {(v.win_rate * 100).toFixed(1)}%
                        </span>
                      </div>
                    ))}
                    <div style={{ fontSize: 10, color: '#334155', marginTop: 8 }}>
                      Bear market win rate should be lower — if not, check signal compression is working.
                    </div>
                  </div>
                )}
              </div>

              <div style={{ fontSize: 11, color: '#334155', padding: '8px 12px', background: '#0a0f1a', borderRadius: 6 }}>
                ℹ️ Outcomes use fixed hold windows: SHORT=7d, SWING=14d, LONG=28d. Entry = first close ≥ signal date. Exit = first close ≥ entry + hold days.
                Once SWING outcomes exceed 500, run Optuna on signal parameters — see SIGNAL_ACCURACY.md for the tuning workflow.
              </div>
            </div>
          )}

          {/* Threshold Calibration */}
          {calibrationData && calibrationData.calibrations.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>
                Threshold Calibration
                <span style={{ fontSize: 11, color: '#475569', fontWeight: 400, marginLeft: 8 }}>
                  last {calibrationData.days}d · min {calibrationData.min_samples} samples · BUY signals only
                </span>
              </div>
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 12 }}>
                Sweeps confidence thresholds to find the cut that maximises expected value (win rate × avg return).
                A green suggested threshold means raising the bar improves edge. Apply via SA-XX signal tuning.
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #1e293b' }}>
                      {['Style', 'Current Threshold', 'Suggested', 'Change', 'N Signals', 'Win Rate', 'Avg Return', 'Exp. Value', 'EV Lift'].map(h => (
                        <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontSize: 11, color: '#64748b', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {calibrationData.calibrations.map(row => {
                      const hasSuggestion = row.suggested_threshold != null && row.at_suggested_threshold != null;
                      const diff = hasSuggestion ? row.suggested_threshold! - row.current_threshold : null;
                      const lift = row.ev_lift_pct;
                      const liftColor = lift == null ? '#475569' : lift > 0.1 ? '#4ade80' : lift < -0.1 ? '#f87171' : '#facc15';
                      const diffColor = diff == null ? '#475569' : diff > 0 ? '#f87171' : diff < 0 ? '#4ade80' : '#475569';
                      return (
                        <tr key={row.horizon} style={{ borderBottom: '1px solid #0f172a' }}>
                          <td style={{ padding: '8px 10px', fontWeight: 700, color: '#818cf8' }}>{row.horizon}</td>
                          <td style={{ padding: '8px 10px', color: '#94a3b8', fontFamily: 'monospace' }}>
                            {(row.current_threshold * 100).toFixed(0)}%
                            {row.at_current_threshold && (
                              <div style={{ fontSize: 10, color: '#475569' }}>
                                n={row.at_current_threshold.n} · {(row.at_current_threshold.win_rate * 100).toFixed(0)}% wr
                              </div>
                            )}
                          </td>
                          <td style={{ padding: '8px 10px', fontFamily: 'monospace', color: hasSuggestion ? '#e2e8f0' : '#334155' }}>
                            {hasSuggestion ? `${(row.suggested_threshold! * 100).toFixed(0)}%` : '—'}
                            {row.at_suggested_threshold && (
                              <div style={{ fontSize: 10, color: '#475569' }}>
                                n={row.at_suggested_threshold.n} · {(row.at_suggested_threshold.win_rate * 100).toFixed(0)}% wr
                              </div>
                            )}
                          </td>
                          <td style={{ padding: '8px 10px', color: diffColor, fontFamily: 'monospace', fontWeight: 700 }}>
                            {diff == null ? '—' : diff === 0 ? '→ same' : `${diff > 0 ? '+' : ''}${(diff * 100).toFixed(0)}pp`}
                          </td>
                          <td style={{ padding: '8px 10px', color: '#64748b' }}>{row.n_total}</td>
                          <td style={{ padding: '8px 10px', color: '#94a3b8' }}>
                            {row.at_suggested_threshold ? `${(row.at_suggested_threshold.win_rate * 100).toFixed(1)}%` : '—'}
                          </td>
                          <td style={{ padding: '8px 10px', color: row.at_suggested_threshold?.avg_return_pct != null && row.at_suggested_threshold.avg_return_pct > 0 ? '#4ade80' : '#f87171' }}>
                            {row.at_suggested_threshold?.avg_return_pct != null ? `${row.at_suggested_threshold.avg_return_pct > 0 ? '+' : ''}${row.at_suggested_threshold.avg_return_pct.toFixed(2)}%` : '—'}
                          </td>
                          <td style={{ padding: '8px 10px', color: '#94a3b8' }}>
                            {row.at_suggested_threshold?.expected_value_pct != null ? `${row.at_suggested_threshold.expected_value_pct.toFixed(3)}%` : '—'}
                          </td>
                          <td style={{ padding: '8px 10px', color: liftColor, fontWeight: 700 }}>
                            {lift != null ? `${lift > 0 ? '+' : ''}${lift.toFixed(3)}%` : row.note ?? '—'}
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

      {activeTab === 'ic' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: '#64748b' }}>Horizon:</span>
            {(['SHORT', 'SWING', 'LONG'] as const).map(h => (
              <button key={h} onClick={() => setIcHorizon(h)}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                  border: '1px solid', borderColor: icHorizon === h ? '#6366f1' : '#1e293b',
                  background: icHorizon === h ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: icHorizon === h ? '#818cf8' : '#64748b' }}>{h}</button>
            ))}
          </div>
          {!icData ? (
            <div style={{ color: '#475569', textAlign: 'center', padding: 40 }}>Loading IC data…</div>
          ) : icData.message || !icData.monthly_ic?.length ? (
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 24, textAlign: 'center' }}>
              <div style={{ fontSize: 32, marginBottom: 8 }}>📈</div>
              <div style={{ color: '#64748b', fontSize: 13 }}>{icData.message || 'Not enough data yet'}</div>
              <div style={{ color: '#334155', fontSize: 11, marginTop: 8 }}>IC requires at least 5 BUY signals with evaluated outcomes per month.</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                {[
                  { label: 'IC Mean', value: icData.ic_mean?.toFixed(4), target: '> 0.05 = good', color: (icData.ic_mean ?? 0) > 0.05 ? '#4ade80' : (icData.ic_mean ?? 0) > 0.02 ? '#facc15' : '#f87171' },
                  { label: 'IC Std Dev', value: icData.ic_std?.toFixed(4), color: '#94a3b8' },
                  { label: 'IC IR (mean/std)', value: icData.ic_ir?.toFixed(3) ?? '—', target: '> 0.5 = excellent', color: (icData.ic_ir ?? 0) > 0.5 ? '#4ade80' : '#94a3b8' },
                  { label: 'Months', value: String(icData.total_periods), color: '#94a3b8' },
                ].map(stat => (
                  <div key={stat.label} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '12px 18px', minWidth: 130 }}>
                    <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', marginBottom: 4 }}>{stat.label}</div>
                    <div style={{ fontSize: 22, fontWeight: 800, color: stat.color }}>{stat.value ?? '—'}</div>
                    {stat.target && <div style={{ fontSize: 10, color: '#334155', marginTop: 2 }}>{stat.target}</div>}
                  </div>
                ))}
              </div>
              <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 12 }}>Monthly IC — {icHorizon}</div>
                <div style={{ display: 'flex', gap: 4, alignItems: 'flex-end', height: 80 }}>
                  {icData.monthly_ic.map(m => {
                    const height = Math.abs(m.ic) * 800;
                    const color = m.ic > 0.05 ? '#4ade80' : m.ic > 0 ? '#86efac' : '#f87171';
                    return (
                      <div key={m.month} title={`${m.month}: IC=${m.ic.toFixed(3)}, n=${m.n}`}
                        style={{ flex: 1, background: color, height: `${Math.min(80, height)}px`, borderRadius: '2px 2px 0 0',
                          opacity: 0.85, transition: 'height 0.3s', minWidth: 8 }} />
                    );
                  })}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#334155', marginTop: 4 }}>
                  <span>{icData.monthly_ic[0]?.month}</span>
                  <span>{icData.monthly_ic[icData.monthly_ic.length - 1]?.month}</span>
                </div>
              </div>
              <div style={{ fontSize: 11, color: '#334155', padding: '8px 12px', background: '#0a0f1a', borderRadius: 6 }}>
                ℹ️ IC = Spearman rank correlation between predicted probability rank and actual return rank. IC &gt; 0.05 is considered good in quant finance. IC_IR (mean÷std) &gt; 0.5 indicates a consistent signal.
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'attribution' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: '#64748b' }}>Horizon:</span>
            {(['SHORT', 'SWING', 'LONG'] as const).map(h => (
              <button key={h} onClick={() => setAttrHorizon(h)}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                  border: '1px solid', borderColor: attrHorizon === h ? '#6366f1' : '#1e293b',
                  background: attrHorizon === h ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: attrHorizon === h ? '#818cf8' : '#64748b' }}>{h}</button>
            ))}
          </div>
          {!attrData ? (
            <div style={{ color: '#475569', textAlign: 'center', padding: 40 }}>Loading factor attribution…</div>
          ) : attrData.message || !attrData.factors?.length ? (
            <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 24, textAlign: 'center' }}>
              <div style={{ fontSize: 32, marginBottom: 8 }}>🔬</div>
              <div style={{ color: '#64748b', fontSize: 13 }}>{attrData.message || 'No factor data yet'}</div>
              <div style={{ color: '#334155', fontSize: 11, marginTop: 8 }}>Requires at least 10 evaluated outcomes per reason flag.</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 16px' }}>
                  <div style={{ fontSize: 10, color: '#475569' }}>WINNERS</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: '#4ade80' }}>{attrData.total_winners}</div>
                </div>
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 16px' }}>
                  <div style={{ fontSize: 10, color: '#475569' }}>LOSERS</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: '#f87171' }}>{attrData.total_losers}</div>
                </div>
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 16px' }}>
                  <div style={{ fontSize: 10, color: '#475569' }}>FACTORS ANALYSED</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: '#94a3b8' }}>{attrData.factors.length}</div>
                </div>
              </div>
              <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 12 }}>
                  Factor Edge — Win% minus Loss% (green = more common in winners, red = more common in losers)
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {attrData.factors.map(f => {
                    const maxEdge = Math.max(...attrData.factors.map(x => Math.abs(x.edge)));
                    const barWidth = maxEdge > 0 ? Math.abs(f.edge) / maxEdge * 100 : 0;
                    const color = f.edge > 0 ? '#4ade80' : '#f87171';
                    return (
                      <div key={f.factor} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div style={{ width: 200, fontSize: 11, color: '#94a3b8', textAlign: 'right', flexShrink: 0, textTransform: 'capitalize' }}>
                          {f.factor.replace(/_/g, ' ')}
                        </div>
                        <div style={{ flex: 1, position: 'relative', height: 18, background: '#1e293b', borderRadius: 3 }}>
                          <div style={{ position: 'absolute', left: f.edge >= 0 ? 0 : `${100 - barWidth}%`, width: `${barWidth}%`,
                            height: '100%', background: color, borderRadius: 3, opacity: 0.8 }} />
                        </div>
                        <div style={{ width: 50, fontSize: 11, fontWeight: 600, color, textAlign: 'right', flexShrink: 0 }}>
                          {f.edge > 0 ? '+' : ''}{f.edge.toFixed(1)}%
                        </div>
                        <div style={{ width: 120, fontSize: 10, color: '#475569', flexShrink: 0 }}>
                          W:{f.win_pct.toFixed(0)}% L:{f.los_pct.toFixed(0)}% (n={f.win_count + f.los_count})
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div style={{ fontSize: 11, color: '#334155', padding: '8px 12px', background: '#0a0f1a', borderRadius: 6 }}>
                ℹ️ Edge = win_pct − loss_pct. Positive edge means the factor appears more often in winning trades. Factors with near-zero edge are noise.
                Feed high-edge factors into SA-13 conviction gate weights.
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'filter-audit' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: '#64748b' }}>Style:</span>
            {(['SHORT', 'SWING', 'LONG', 'GROWTH'] as const).map(h => (
              <button key={h} onClick={() => setFaStyle(h)}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                  border: '1px solid', borderColor: faStyle === h ? '#6366f1' : '#1e293b',
                  background: faStyle === h ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: faStyle === h ? '#818cf8' : '#64748b' }}>{h}</button>
            ))}
            <span style={{ fontSize: 12, color: '#64748b', marginLeft: 8 }}>Hold days:</span>
            {([7, 10, 14, 20] as const).map(d => (
              <button key={d} onClick={() => setFaHold(d)}
                style={{ padding: '4px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                  border: '1px solid', borderColor: faHold === d ? '#6366f1' : '#1e293b',
                  background: faHold === d ? 'rgba(99,102,241,0.15)' : 'transparent',
                  color: faHold === d ? '#818cf8' : '#64748b' }}>{d}d</button>
            ))}
          </div>
          {!filterAuditData ? (
            <div style={{ color: '#475569', textAlign: 'center', padding: 40 }}>Loading filter audit…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                {[
                  ['Signals Analysed', filterAuditData.n_buy_signals_found, '#94a3b8'],
                  ['With Return Data', filterAuditData.n_with_return_data, '#94a3b8'],
                  ['Overall Win Rate', filterAuditData.overall_win_rate_pct != null ? `${filterAuditData.overall_win_rate_pct}%` : '—', (filterAuditData.overall_win_rate_pct ?? 0) >= 55 ? '#4ade80' : '#f87171'],
                ].map(([label, val, color]) => (
                  <div key={String(label)} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '10px 16px' }}>
                    <div style={{ fontSize: 10, color: '#475569' }}>{label}</div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: String(color) }}>{String(val)}</div>
                  </div>
                ))}
              </div>

              {/* Per-filter breakdown */}
              {filterAuditData.by_filter_name?.length > 0 && (
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4 }}>
                    Per-Filter Win Rate Analysis
                  </div>
                  <div style={{ fontSize: 11, color: '#475569', marginBottom: 12 }}>
                    Edge = win_rate_active − win_rate_inactive. Negative = filter correctly blocks weaker signals. Positive = filter is harmful (blocking better signals than it passes).
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr>
                          {['Filter', 'N Active', 'WR Active', 'N Inactive', 'WR Inactive', 'Edge', 'Verdict'].map(h => (
                            <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#475569', fontWeight: 600, borderBottom: '1px solid #1e293b', whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {filterAuditData.by_filter_name.map(f => {
                          const verdictColor = f.verdict === 'predictive' ? '#4ade80' : f.verdict === 'harmful' ? '#f87171' : '#facc15';
                          const edgeColor = f.edge_pct < -3 ? '#4ade80' : f.edge_pct > 3 ? '#f87171' : '#94a3b8';
                          return (
                            <tr key={f.filter} style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                              <td style={{ padding: '6px 10px', color: '#e2e8f0', fontFamily: 'monospace', fontSize: 10 }}>{f.filter}</td>
                              <td style={{ padding: '6px 10px', color: '#64748b' }}>{f.n_active}</td>
                              <td style={{ padding: '6px 10px', color: '#94a3b8', fontWeight: 600 }}>{f.win_rate_active != null ? `${f.win_rate_active}%` : '—'}</td>
                              <td style={{ padding: '6px 10px', color: '#64748b' }}>{f.n_inactive}</td>
                              <td style={{ padding: '6px 10px', color: '#94a3b8', fontWeight: 600 }}>{f.win_rate_inactive != null ? `${f.win_rate_inactive}%` : '—'}</td>
                              <td style={{ padding: '6px 10px', color: edgeColor, fontWeight: 700 }}>{f.edge_pct > 0 ? '+' : ''}{f.edge_pct}%</td>
                              <td style={{ padding: '6px 10px' }}>
                                <span style={{ padding: '2px 8px', borderRadius: 4, background: `${verdictColor}22`, color: verdictColor, fontSize: 10, fontWeight: 700, textTransform: 'uppercase' }}>{f.verdict}</span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* By filter count */}
              {filterAuditData.by_filter_count?.length > 0 && (
                <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 12 }}>
                    Win Rate by Filter Count (0 = no suppressions active)
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                    <thead>
                      <tr>
                        {['Filters Active', 'N Trades', 'Win Rate', 'Avg Return'].map(h => (
                          <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#475569', fontWeight: 600, borderBottom: '1px solid #1e293b' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {filterAuditData.by_filter_count.map(row => (
                        <tr key={row.filter_count} style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
                          <td style={{ padding: '6px 10px', color: '#94a3b8', fontWeight: 700 }}>{row.filter_count}</td>
                          <td style={{ padding: '6px 10px', color: '#64748b' }}>{row.trade_count}</td>
                          <td style={{ padding: '6px 10px', color: (row.win_rate_pct ?? 0) >= 55 ? '#4ade80' : '#f87171', fontWeight: 600 }}>
                            {row.win_rate_pct != null ? `${row.win_rate_pct}%` : '—'}
                          </td>
                          <td style={{ padding: '6px 10px', color: (row.avg_return_pct ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                            {row.avg_return_pct != null ? `${row.avg_return_pct > 0 ? '+' : ''}${row.avg_return_pct}%` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div style={{ fontSize: 11, color: '#334155', padding: '8px 12px', background: '#0a0f1a', borderRadius: 6 }}>
                ℹ️ Run this analysis once you have 200+ evaluated BUY signals. Filters with positive edge (harmful) should be disabled or inverted in signals.py.
                Filters with negative edge ≤ −5% are strongly predictive. Use SA-6 findings to inform TA weight calibration.
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'overview' && <>
      {/* Controls */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 20, alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {LOOKBACK_OPTIONS.map(o => (
            <button key={o.value} onClick={() => { setLookback(o.value); setFromDate(''); setToDate(''); setPage(1); }}
              style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
                borderColor: lookback === o.value && !useDateRange ? '#6366f1' : '#1e293b',
                background: lookback === o.value && !useDateRange ? 'rgba(99,102,241,0.15)' : 'transparent',
                color: lookback === o.value && !useDateRange ? '#818cf8' : '#64748b' }}>
              {o.label}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: '#475569' }}>From</span>
          <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)}
            style={{ padding: '3px 8px', borderRadius: 6, border: `1px solid ${useDateRange ? '#6366f1' : '#1e293b'}`, background: '#0f172a', color: '#e2e8f0', fontSize: 12 }} />
          <span style={{ fontSize: 11, color: '#475569' }}>To</span>
          <input type="date" value={toDate} onChange={e => setToDate(e.target.value)}
            style={{ padding: '3px 8px', borderRadius: 6, border: `1px solid ${useDateRange ? '#6366f1' : '#1e293b'}`, background: '#0f172a', color: '#e2e8f0', fontSize: 12 }} />
          {useDateRange && (
            <button onClick={() => { setFromDate(''); setToDate(''); }}
              style={{ padding: '3px 8px', borderRadius: 6, fontSize: 11, border: '1px solid #334155', background: 'transparent', color: '#64748b', cursor: 'pointer' }}>
              ✕ Clear
            </button>
          )}
        </div>
        <input
          value={filterSymbol} onChange={e => setFilterSymbol(e.target.value)}
          placeholder="Filter symbol…"
          style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: 12, width: 120 }}
        />
        {(['ALL', 'BUY', 'SELL'] as const).map(v => (
          <button key={v} onClick={() => setSignalFilter(v)}
            style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer', border: '1px solid',
              borderColor: signalFilter === v ? (v === 'BUY' ? '#166534' : v === 'SELL' ? '#991b1b' : '#334155') : '#1e293b',
              background: signalFilter === v ? (v === 'BUY' ? 'rgba(22,101,52,0.2)' : v === 'SELL' ? 'rgba(153,27,27,0.2)' : 'rgba(51,65,85,0.2)') : 'transparent',
              color: signalFilter === v ? (v === 'BUY' ? '#4ade80' : v === 'SELL' ? '#f87171' : '#94a3b8') : '#64748b' }}>
            {v}
          </button>
        ))}
        {(['ALL', 'CORRECT', 'WRONG'] as const).map(v => (
          <button key={v} onClick={() => setShowOnly(v)}
            style={{ padding: '4px 10px', borderRadius: 6, fontSize: 11, cursor: 'pointer', border: '1px solid',
              borderColor: showOnly === v ? '#475569' : '#1e293b',
              background: showOnly === v ? 'rgba(71,85,105,0.2)' : 'transparent',
              color: showOnly === v ? '#94a3b8' : '#475569' }}>
            {v}
          </button>
        ))}
      </div>

      {/* Summary cards */}
      {data && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 24 }}>
          {statCard('Overall Accuracy', acc(data.overall_accuracy), `${data.total_signals} signals evaluated`, overallColor)}
          {statCard('BUY Accuracy', acc(data.buy_accuracy), `${data.buy_count} BUY signals`, data.buy_accuracy != null && data.buy_accuracy >= 55 ? '#4ade80' : '#f87171')}
          {statCard('SELL Accuracy', acc(data.sell_accuracy), `${data.sell_count} SELL signals`, data.sell_accuracy != null && data.sell_accuracy >= 55 ? '#4ade80' : '#f87171')}
          {statCard('Avg BUY Return', pct(data.avg_buy_return_pct), '5-day avg after BUY', data.avg_buy_return_pct != null && data.avg_buy_return_pct > 0 ? '#4ade80' : '#f87171')}
          {statCard('Avg SELL Return', pct(data.avg_sell_return_pct != null ? -data.avg_sell_return_pct : null), '5-day decline after SELL', data.avg_sell_return_pct != null && data.avg_sell_return_pct < 0 ? '#4ade80' : '#f87171')}
          {statCard('Profit Factor', data.profit_factor != null ? data.profit_factor.toFixed(2) : '—', 'wins / losses magnitude', data.profit_factor != null && data.profit_factor >= 1.5 ? '#4ade80' : '#facc15')}
        </div>
      )}

      {/* Accuracy bar */}
      {data?.overall_accuracy != null && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#64748b', marginBottom: 4 }}>
            <span>0%</span><span>50% (random)</span><span>100%</span>
          </div>
          <div style={{ height: 8, borderRadius: 4, background: '#1e293b', overflow: 'hidden', position: 'relative' }}>
            <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: '#334155' }} />
            <div style={{ height: '100%', width: `${data.overall_accuracy}%`, borderRadius: 4,
              background: data.overall_accuracy >= 60 ? '#22c55e' : data.overall_accuracy >= 50 ? '#eab308' : '#ef4444' }} />
          </div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>
            {data.overall_accuracy >= 60 ? 'Above-random accuracy — signals showing predictive value' :
             data.overall_accuracy >= 50 ? 'Near-random — signals slightly better than a coin flip' :
             'Below-random — signals may need recalibration'}
          </div>
        </div>
      )}

      {/* Live vs Backtest Comparison */}
      {data && wfOverview && wfOverview.overall_accuracy != null && (
        <div style={{ marginBottom: 28, background: '#0a0f1e', border: '1px solid #1e293b', borderRadius: 8, padding: '16px 18px' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', marginBottom: 12 }}>
            Live vs Walk-Forward Backtest
            <span style={{ fontSize: 10, fontWeight: 400, color: '#475569', marginLeft: 8 }}>
              Does live performance match what backtesting predicted?
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px', gap: 0 }}>
            {/* Header */}
            <div style={{ fontSize: 10, fontWeight: 600, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '0 0 8px 0' }}>Metric</div>
            <div style={{ fontSize: 10, fontWeight: 600, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '0 0 8px 0', textAlign: 'center' }}>Live · {lookback}d</div>
            <div style={{ fontSize: 10, fontWeight: 600, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '0 0 8px 0', textAlign: 'center' }}>WF Backtest</div>
            {/* Overall */}
            {[
              { label: 'Overall Accuracy', live: data.overall_accuracy, bt: wfOverview.overall_accuracy },
              { label: 'BUY Accuracy', live: data.buy_accuracy, bt: null },
            ].filter(r => r.live != null).map(r => {
              const delta = r.bt != null && r.live != null ? r.live - r.bt : null;
              const deltaColor = delta == null ? '#64748b' : delta >= 2 ? '#4ade80' : delta <= -2 ? '#f87171' : '#facc15';
              const liveColor = r.live != null && r.live >= 60 ? '#4ade80' : r.live != null && r.live >= 50 ? '#facc15' : '#f87171';
              const btColor = r.bt != null && r.bt >= 60 ? '#4ade80' : r.bt != null && r.bt >= 50 ? '#facc15' : '#f87171';
              return (
                <div key={r.label} style={{ display: 'contents' }}>
                  <div style={{ fontSize: 12, color: '#cbd5e1', padding: '7px 0', borderTop: '1px solid #1e293b' }}>{r.label}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: liveColor, padding: '7px 0', borderTop: '1px solid #1e293b', textAlign: 'center' }}>
                    {r.live != null ? `${r.live.toFixed(1)}%` : '—'}
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 700, padding: '7px 0', borderTop: '1px solid #1e293b', textAlign: 'center' }}>
                    {r.bt != null ? (
                      <span style={{ color: btColor }}>{r.bt.toFixed(1)}%</span>
                    ) : (
                      <span style={{ color: '#334155' }}>—</span>
                    )}
                    {delta != null && (
                      <span style={{ fontSize: 10, color: deltaColor, marginLeft: 4 }}>
                        {delta >= 0 ? '+' : ''}{delta.toFixed(1)}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
            {/* Signal count row */}
            <div style={{ fontSize: 12, color: '#cbd5e1', padding: '7px 0', borderTop: '1px solid #1e293b' }}>Signal Count</div>
            <div style={{ fontSize: 12, color: '#94a3b8', padding: '7px 0', borderTop: '1px solid #1e293b', textAlign: 'center' }}>{data.total_signals}</div>
            <div style={{ fontSize: 12, color: '#94a3b8', padding: '7px 0', borderTop: '1px solid #1e293b', textAlign: 'center' }}>
              {wfOverview.windows ? (wfOverview.windows as WalkForwardWindow[]).reduce((s, w) => s + w.n_signals, 0) : '—'}
            </div>
          </div>
          {(() => {
            const delta = data.overall_accuracy != null && wfOverview.overall_accuracy != null
              ? data.overall_accuracy - wfOverview.overall_accuracy : null;
            if (delta == null) return null;
            const msg = Math.abs(delta) < 2
              ? 'Live and backtest accuracy are closely aligned — model is stable.'
              : delta > 0
              ? 'Live accuracy exceeds backtest — model may be over-optimized or market conditions favor current setup.'
              : 'Live accuracy lags backtest — potential live-production gap; check data freshness and feature parity.';
            const msgColor = Math.abs(delta) < 2 ? '#4ade80' : delta > 0 ? '#facc15' : '#f87171';
            return <div style={{ fontSize: 11, color: msgColor, marginTop: 10, paddingTop: 10, borderTop: '1px solid #1e293b' }}>{msg}</div>;
          })()}
        </div>
      )}

      {/* ML Weight Validation */}
  {mlWeight && mlWeight.curve.length > 0 && (
    <div style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8' }}>ML/TA Fusion Weight — Empirical Validation</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {calibrateResult && (
            <span style={{ fontSize: '11px', color: calibrateResult.applied ? '#4ade80' : '#f87171' }}>
              {calibrateResult.applied
                ? `Applied w=${calibrateResult.optimal_weight?.toFixed(2)} (${calibrateResult.optimal_accuracy?.toFixed(1)}% acc)`
                : 'Not applied'}
            </span>
          )}
          <button
            onClick={async () => {
              setCalibrating(true);
              setCalibrateResult(null);
              try {
                const res = await api.calibrateMlWeight(180);
                setCalibrateResult(res);
              } catch { /* ignore */ }
              setCalibrating(false);
            }}
            disabled={calibrating}
            style={{
              padding: '5px 12px', borderRadius: '7px', fontSize: '11px', fontWeight: 600, cursor: calibrating ? 'not-allowed' : 'pointer',
              border: '1px solid rgba(129,140,248,0.4)', background: 'rgba(79,70,229,0.15)',
              color: calibrating ? '#475569' : '#a5b4fc', transition: 'all 0.15s',
            }}
          >
            {calibrating ? 'Calibrating…' : 'Apply optimal weight'}
          </button>
        </div>
      </div>
      <MLWeightChart
        curve={mlWeight.curve}
        optimalWeight={mlWeight.optimal_weight}
        formulaRange={mlWeight.current_formula_range}
        signalCount={mlWeight.signal_count}
      />
    </div>
  )}

  {/* Rolling Accuracy / Drift Monitor */}
  {rollingData && rollingData.series.length > 0 && (
    <div style={{ marginBottom: 28 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', marginBottom: 2 }}>Model Drift Detection</div>
      <RollingAccuracyChart
        series={rollingData.series}
        driftWarning={rollingData.drift_warning}
        latestAccuracy={rollingData.latest_accuracy}
        window={rollingData.window}
      />
    </div>
  )}

  {/* Factor Exposure */}
  {factorData && factorData.factors.length > 0 && (
    <div style={{ marginBottom: 28 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', marginBottom: 2 }}>Factor Exposure</div>
      <div style={{ fontSize: 11, color: '#475569', marginBottom: 12 }}>
        Average factor value for correct vs wrong BUY signals. Bars show deviation from neutral baseline.
        If correct signals score higher on a factor, that factor is driving the wins.
      </div>
      <FactorChart factors={factorData.factors} />
    </div>
  )}

  {isLoading && <div style={{ color: '#64748b', textAlign: 'center', padding: 40 }}>Loading signal history…</div>}
      {error && <div style={{ color: '#f87171', padding: 16 }}>Failed to load accuracy data.</div>}

      {/* Signal table */}
      {!isLoading && rows.length > 0 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontSize: 12, color: '#64748b' }}>{rows.length} signal{rows.length !== 1 ? 's' : ''} shown</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {([['date', 'Date'], ['confidence', 'Confidence'], ['pct_change', 'Return']] as const).map(([k, label]) => (
                <button key={k} onClick={() => setSortBy(k)}
                  style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid',
                    borderColor: sortBy === k ? '#6366f1' : '#1e293b',
                    background: sortBy === k ? 'rgba(99,102,241,0.1)' : 'transparent',
                    color: sortBy === k ? '#818cf8' : '#475569' }}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  {['Date', 'Symbol', 'Signal', 'Confidence', 'Entry', 'Exit (5d)', 'Return', 'Outcome'].map(h => (
                    <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#64748b', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #0f172a' }}>
                    <td style={{ padding: '7px 10px', color: '#64748b' }}>{r.signal_date}</td>
                    <td style={{ padding: '7px 10px' }}>
                      <Link href={`/stock/${r.symbol}`} style={{ color: '#818cf8', fontWeight: 600 }}>{r.symbol}</Link>
                      <div style={{ fontSize: 10, color: '#475569' }}>{r.name}</div>
                    </td>
                    <td style={{ padding: '7px 10px' }}>
                      <span style={{ padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 700,
                        background: r.signal === 'BUY' ? 'rgba(22,101,52,0.3)' : 'rgba(153,27,27,0.3)',
                        color: r.signal === 'BUY' ? '#4ade80' : '#f87171' }}>
                        {r.signal}
                      </span>
                    </td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>
                      {r.confidence.toFixed(0)}
                      <div style={{ fontSize: 10, color: '#475569' }}>
                        {r.bullish_probability != null ? `${(r.bullish_probability * 100).toFixed(0)}% bull` : ''}
                      </div>
                    </td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>${r.entry_price.toFixed(2)}</td>
                    <td style={{ padding: '7px 10px', color: '#94a3b8' }}>
                      ${r.exit_price.toFixed(2)}
                      <div style={{ fontSize: 10, color: '#475569' }}>{r.days_held}d later</div>
                    </td>
                    <td style={{ padding: '7px 10px', fontWeight: 600, color: r.pct_change >= 0 ? '#4ade80' : '#f87171' }}>
                      {pct(r.pct_change)}
                    </td>
                    <td style={{ padding: '7px 10px' }}>
                      <span style={{ fontSize: 13 }}>{r.correct ? '✓' : '✗'}</span>
                      <span style={{ fontSize: 11, marginLeft: 4, color: r.correct ? '#4ade80' : '#f87171' }}>
                        {r.correct ? 'Correct' : 'Wrong'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data?.has_more && (
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <button
                onClick={() => setPage(p => p + 1)}
                style={{ padding: '6px 20px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #334155', background: 'transparent', color: '#94a3b8' }}
              >
                Load more ({data.total_signals - (data.page * data.page_size)} remaining)
              </button>
            </div>
          )}
        </div>
      )}

      {!isLoading && !error && rows.length === 0 && data && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#475569' }}>
          <div style={{ fontSize: 36, marginBottom: 8 }}>📊</div>
          <div>No completed signals in this window.</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>Signals need ~7 days to settle before they're evaluated. Try a longer lookback or refresh signals.</div>
        </div>
      )}
      </>}
    </div>
  );
}

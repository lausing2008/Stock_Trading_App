import { useState, useEffect, useRef, useMemo } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import useSWR from 'swr';
import { getSession } from '@/lib/auth';
import {
  api,
  type PaperPortfolioSummary,
  type PaperPortfolioListItem,
  type PaperCompareData,
  type PaperTradeParamResult,
  type PaperPosition,
  type PaperTrade,
  type PaperEquityPoint,
  type PaperDecisionItem,
  type PaperPortfolioConfig,
  type ResearchSummary,
} from '@/lib/api';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(digits) + '%';
}

function fmtUSD(v: number | null | undefined): string {
  if (v == null) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(v);
}

function fmtTs(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' }) +
      ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch { return ts; }
}

function fmtDate(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' });
  } catch { return ts; }
}

const EXIT_COLORS: Record<string, string> = {
  stop_hit: '#ef4444',
  target_reached: '#22c55e',
  signal_exit: '#f59e0b',
  time_stop: '#94a3b8',
  momentum_exit: '#a78bfa',
  manual_reset: '#64748b',
};

const EXIT_LABELS: Record<string, string> = {
  stop_hit: 'Stop Hit',
  target_reached: 'Target',
  signal_exit: 'Signal Exit',
  time_stop: 'Time Stop',
  momentum_exit: 'Momentum Exit',
  manual_reset: 'Reset',
};

function ExitBadge({ reason }: { reason: string | null }) {
  if (!reason) return <span style={{ color: '#64748b' }}>—</span>;
  const color = EXIT_COLORS[reason] ?? '#94a3b8';
  const label = EXIT_LABELS[reason] ?? reason;
  return (
    <span style={{
      background: color + '22', color, border: `1px solid ${color}44`,
      borderRadius: 4, padding: '2px 7px', fontSize: 11, fontWeight: 600,
    }}>{label}</span>
  );
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{
      background: '#1e293b', borderRadius: 10, padding: '14px 18px',
      border: '1px solid #334155', minWidth: 130,
    }}>
      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: color ?? '#f1f5f9' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ── Benchmark Comparison Table ────────────────────────────────────────────────

function BenchmarkTable({ data }: { data: PaperEquityPoint[] }) {
  if (!data.length) return null;

  const sorted = [...data].sort((a, b) => a.date.localeCompare(b.date));
  const latest = sorted[sorted.length - 1];
  if (!latest) return null;

  function periodReturn(daysBack: number, field: 'equity' | 'spy_close' | 'qqq_close'): number | null {
    const cutoff = new Date(latest.date);
    cutoff.setDate(cutoff.getDate() - daysBack);
    const cutoffStr = cutoff.toISOString().slice(0, 10);
    // Find nearest point at or before cutoff
    const base = [...sorted].reverse().find(d => d.date <= cutoffStr);
    if (!base) return null;
    const baseVal = base[field];
    const latestVal = latest[field];
    if (!baseVal || !latestVal) return null;
    return (latestVal / baseVal - 1) * 100;
  }

  const periods = [
    { label: '1W', days: 7 },
    { label: '1M', days: 30 },
    { label: '3M', days: 90 },
    { label: 'Inception', days: 10000 },
  ];

  const rows = periods.map(p => ({
    label: p.label,
    portfolio: periodReturn(p.days, 'equity'),
    spy: periodReturn(p.days, 'spy_close'),
    qqq: periodReturn(p.days, 'qqq_close'),
  })).filter(r => r.portfolio !== null || r.spy !== null);

  if (!rows.length) return null;

  const cellStyle = (v: number | null, highlight = false): React.CSSProperties => ({
    padding: '8px 14px',
    textAlign: 'right',
    fontWeight: highlight ? 700 : 400,
    color: v == null ? '#475569' : highlight
      ? (v >= 0 ? '#4ade80' : '#f87171')
      : '#94a3b8',
    fontSize: 13,
  });

  const outStyle = (portfolio: number | null, bench: number | null): React.CSSProperties => {
    if (portfolio == null || bench == null) return { padding: '8px 14px', textAlign: 'right', color: '#475569', fontSize: 13 };
    const diff = portfolio - bench;
    return { padding: '8px 14px', textAlign: 'right', color: diff >= 0 ? '#4ade80' : '#f87171', fontSize: 13, fontWeight: 600 };
  };

  return (
    <div style={{ background: '#0f172a', borderRadius: 10, border: '1px solid #1e293b', padding: '14px 0', marginTop: 16 }}>
      <div style={{ fontSize: 12, color: '#64748b', padding: '0 16px 10px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
        Benchmark Comparison
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1e293b' }}>
              <th style={{ padding: '6px 16px', textAlign: 'left', color: '#64748b', fontWeight: 600, fontSize: 11 }}>Period</th>
              <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>Portfolio</th>
              <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>SPY</th>
              <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>QQQ</th>
              <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>vs SPY</th>
              <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>vs QQQ</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.label} style={{ borderBottom: '1px solid #0f172a' }}>
                <td style={{ padding: '8px 16px', color: '#e2e8f0', fontWeight: 600, fontSize: 13 }}>{r.label}</td>
                <td style={cellStyle(r.portfolio, true)}>{r.portfolio != null ? (r.portfolio >= 0 ? '+' : '') + r.portfolio.toFixed(2) + '%' : '—'}</td>
                <td style={cellStyle(r.spy)}>{r.spy != null ? (r.spy >= 0 ? '+' : '') + r.spy.toFixed(2) + '%' : '—'}</td>
                <td style={cellStyle(r.qqq)}>{r.qqq != null ? (r.qqq >= 0 ? '+' : '') + r.qqq.toFixed(2) + '%' : '—'}</td>
                <td style={outStyle(r.portfolio, r.spy)}>{r.portfolio != null && r.spy != null ? (r.portfolio - r.spy >= 0 ? '+' : '') + (r.portfolio - r.spy).toFixed(2) + '%' : '—'}</td>
                <td style={outStyle(r.portfolio, r.qqq)}>{r.portfolio != null && r.qqq != null ? (r.portfolio - r.qqq >= 0 ? '+' : '') + (r.portfolio - r.qqq).toFixed(2) + '%' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Equity Curve Chart ─────────────────────────────────────────────────────────

function EquityChart({ data, initialCapital }: { data: PaperEquityPoint[]; initialCapital: number }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !data.length) return;
    let cancelled = false;

    import('plotly.js-dist-min').then((Plotly: any) => {
      if (cancelled || !ref.current) return;

      const dates = data.map(d => d.date);
      const equity = data.map(d => d.equity);

      // Normalise benchmarks to same starting equity for comparison
      const spyStart = data.find(d => d.spy_close != null)?.spy_close;
      const qqqStart = data.find(d => d.qqq_close != null)?.qqq_close;

      const traces: any[] = [
        {
          x: dates, y: equity, name: 'Portfolio',
          type: 'scatter', mode: 'lines',
          line: { color: '#22c55e', width: 2.5 },
          hovertemplate: '%{x}: $%{y:,.0f}<extra>Portfolio</extra>',
        },
      ];

      if (spyStart) {
        traces.push({
          x: dates,
          y: data.map(d => d.spy_close != null ? initialCapital * (d.spy_close / spyStart) : null),
          name: 'SPY',
          type: 'scatter', mode: 'lines',
          line: { color: '#60a5fa', width: 1.5, dash: 'dot' },
          hovertemplate: '%{x}: $%{y:,.0f}<extra>SPY</extra>',
        });
      }
      if (qqqStart) {
        traces.push({
          x: dates,
          y: data.map(d => d.qqq_close != null ? initialCapital * (d.qqq_close / qqqStart) : null),
          name: 'QQQ',
          type: 'scatter', mode: 'lines',
          line: { color: '#a78bfa', width: 1.5, dash: 'dot' },
          hovertemplate: '%{x}: $%{y:,.0f}<extra>QQQ</extra>',
        });
      }

      // PT-A2: build regime shading shapes from consecutive same-regime spans
      const REGIME_COLORS: Record<string, string> = {
        bull:     'rgba(34,197,94,0.08)',
        bear:     'rgba(239,68,68,0.08)',
        risk_off: 'rgba(249,115,22,0.10)',
        choppy:   'rgba(245,158,11,0.08)',
      };
      const shapes: any[] = [];
      let spanStart = 0;
      for (let i = 1; i <= dates.length; i++) {
        const prev = data[i - 1]?.market_regime ?? null;
        const curr = data[i]?.market_regime ?? null;
        if (curr !== prev || i === dates.length) {
          const fillcolor = prev ? REGIME_COLORS[prev] : null;
          if (fillcolor) {
            shapes.push({
              type: 'rect', layer: 'below',
              x0: dates[spanStart], x1: dates[i - 1],
              y0: 0, y1: 1, yref: 'paper',
              fillcolor, line: { width: 0 },
            });
          }
          spanStart = i;
        }
      }

      const layout = {
        paper_bgcolor: '#0f172a', plot_bgcolor: '#0f172a',
        margin: { t: 10, b: 40, l: 60, r: 10 },
        height: 240,
        xaxis: { color: '#64748b', gridcolor: '#1e293b', showgrid: true },
        yaxis: { color: '#64748b', gridcolor: '#1e293b', tickprefix: '$', tickformat: ',.0f' },
        legend: { font: { color: '#94a3b8', size: 11 }, bgcolor: 'transparent', orientation: 'h', x: 0, y: -0.15 },
        hovermode: 'x unified',
        shapes,
      };

      Plotly.react(ref.current, traces, layout, { displayModeBar: false, responsive: true });
    });

    return () => { cancelled = true; };
  }, [data, initialCapital]);

  if (!data.length) {
    return (
      <div style={{ background: '#1e293b', borderRadius: 10, padding: 24, textAlign: 'center', color: '#64748b', border: '1px solid #334155' }}>
        No equity curve data yet — curve updates post-market daily.
      </div>
    );
  }

  return <div ref={ref} style={{ width: '100%' }} />;
}

// ── Portfolio comparison card ─────────────────────────────────────────────────

const STYLE_COLORS: Record<string, string> = {
  GROWTH: '#22c55e',
  SWING:  '#3b82f6',
  LONG:   '#a78bfa',
  SHORT:  '#f87171',
};

function PortfolioCard({
  portfolio, selected, isBestSharpe, onSelect,
}: {
  portfolio: PaperPortfolioListItem;
  selected: boolean;
  isBestSharpe: boolean;
  onSelect: () => void;
}) {
  const retColor = portfolio.total_return_pct >= 0 ? '#22c55e' : '#ef4444';
  const styleColor = STYLE_COLORS[portfolio.trading_style] ?? '#94a3b8';
  const state = portfolio.is_running ? 'Running' : portfolio.is_paused ? 'Paused' : 'Stopped';
  const stateColor = portfolio.is_running ? '#22c55e' : portfolio.is_paused ? '#f59e0b' : '#ef4444';

  return (
    <div
      onClick={onSelect}
      style={{
        background: selected ? '#1a2744' : '#1e293b',
        border: `2px solid ${selected ? '#3b82f6' : '#334155'}`,
        borderRadius: 12, padding: '14px 18px', cursor: 'pointer',
        minWidth: 200, flex: '1 1 200px', maxWidth: 280,
        transition: 'border-color 0.15s, background 0.15s', position: 'relative',
      }}
    >
      {isBestSharpe && (
        <span title="Best Sharpe Ratio" style={{
          position: 'absolute', top: 8, right: 8, fontSize: 13, color: '#f59e0b',
        }}>★</span>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 8 }}>
        <span style={{
          fontSize: 10, fontWeight: 700, color: styleColor,
          background: styleColor + '22', border: `1px solid ${styleColor}44`,
          borderRadius: 4, padding: '2px 7px',
        }}>{portfolio.trading_style}</span>
        <span style={{ fontSize: 10, color: stateColor, fontWeight: 600 }}>● {state}</span>
      </div>
      <div style={{ fontSize: 14, fontWeight: 700, color: '#f1f5f9', marginBottom: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {portfolio.name}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, color: retColor }}>
        {portfolio.total_return_pct >= 0 ? '+' : ''}{portfolio.total_return_pct.toFixed(1)}%
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
        {new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(portfolio.current_equity)}
      </div>
      <div style={{ display: 'flex', gap: 12, marginTop: 10, fontSize: 11, color: '#94a3b8' }}>
        <span>Win {portfolio.win_rate_pct.toFixed(0)}%</span>
        <span>Sharpe {portfolio.sharpe != null ? portfolio.sharpe.toFixed(2) : '—'}</span>
        <span>{portfolio.open_positions} open</span>
      </div>
      {portfolio.cagr_pct != null && (
        <div style={{ fontSize: 10, color: portfolio.cagr_pct >= 0 ? '#4ade80' : '#f87171', marginTop: 4 }}>
          CAGR {portfolio.cagr_pct >= 0 ? '+' : ''}{portfolio.cagr_pct.toFixed(1)}%
          {portfolio.sortino != null && <span style={{ color: '#94a3b8', marginLeft: 8 }}>Sortino {portfolio.sortino.toFixed(2)}</span>}
        </div>
      )}
    </div>
  );
}

// ── Compare equity chart (overlay all portfolios + SPY) ───────────────────────

function CompareEquityChart({ data }: { data: PaperCompareData[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !data.length) return;
    let cancelled = false;

    import('plotly.js-dist-min').then((Plotly: any) => {
      if (cancelled || !ref.current) return;

      const COLORS = ['#22c55e', '#3b82f6', '#a78bfa', '#f59e0b', '#ec4899', '#06b6d4'];

      const traces: any[] = data.map((p, i) => {
        if (!p.curve.length) return null;
        const startEquity = p.curve[0].equity;
        return {
          x: p.curve.map(d => d.date),
          y: p.curve.map(d => ((d.equity / startEquity) - 1) * 100),
          name: `${p.name} (${p.trading_style})`,
          type: 'scatter', mode: 'lines',
          line: { color: COLORS[i % COLORS.length], width: 2.5 },
          hovertemplate: `%{x}: %{y:+.1f}%<extra>${p.name}</extra>`,
        };
      }).filter(Boolean);

      // Add SPY from the first portfolio that has spy data
      const firstWithSpy = data.find(p => p.curve.some(d => d.spy_close != null));
      if (firstWithSpy) {
        const spyStart = firstWithSpy.curve.find(d => d.spy_close != null)?.spy_close;
        if (spyStart) {
          traces.push({
            x: firstWithSpy.curve.map(d => d.date),
            y: firstWithSpy.curve.map(d => d.spy_close != null ? ((d.spy_close / spyStart) - 1) * 100 : null),
            name: 'SPY',
            type: 'scatter', mode: 'lines',
            line: { color: '#64748b', width: 1.5, dash: 'dot' },
            hovertemplate: '%{x}: %{y:+.1f}%<extra>SPY</extra>',
          });
        }
      }

      const layout = {
        paper_bgcolor: '#0f172a', plot_bgcolor: '#0f172a',
        margin: { t: 10, b: 40, l: 55, r: 10 },
        height: 220,
        xaxis: { color: '#64748b', gridcolor: '#1e293b', showgrid: true },
        yaxis: { color: '#64748b', gridcolor: '#1e293b', ticksuffix: '%', zeroline: true, zerolinecolor: '#334155' },
        legend: { font: { color: '#94a3b8', size: 11 }, bgcolor: 'transparent', orientation: 'h', x: 0, y: -0.2 },
        hovermode: 'x unified',
      };

      Plotly.react(ref.current, traces, layout, { displayModeBar: false, responsive: true });
    });

    return () => { cancelled = true; };
  }, [data]);

  return <div ref={ref} style={{ width: '100%' }} />;
}

// ── Create portfolio modal ─────────────────────────────────────────────────────

function CreatePortfolioModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('');
  const [style, setStyle] = useState('SWING');
  const [capital, setCapital] = useState('100000');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  async function create() {
    const cap = parseFloat(capital);
    if (!name.trim()) { setErr('Name is required'); return; }
    if (isNaN(cap) || cap <= 0) { setErr('Capital must be > 0'); return; }
    setSaving(true); setErr('');
    try {
      await api.paperCreate({ name: name.trim(), trading_style: style, initial_capital: cap });
      onCreated();
      onClose();
    } catch (e: any) {
      setErr(e?.message ?? 'Failed to create portfolio');
    } finally {
      setSaving(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
    color: '#f1f5f9', padding: '8px 10px', fontSize: 13, boxSizing: 'border-box',
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        background: '#1e293b', borderRadius: 12, padding: 28, width: 360,
        border: '1px solid #334155', boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 20 }}>New Paper Portfolio</div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 5 }}>Name</div>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. SWING A/B Test"
              style={inputStyle} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 5 }}>Strategy Style</div>
            <select value={style} onChange={e => setStyle(e.target.value)} style={{ ...inputStyle, cursor: 'pointer' }}>
              <option value="SWING">SWING — medium-term momentum</option>
              <option value="GROWTH">GROWTH — high-volatility momentum</option>
              <option value="LONG">LONG — trend-following long-term</option>
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 5 }}>Initial Capital ($)</div>
            <input value={capital} onChange={e => setCapital(e.target.value)} type="number" min="1"
              style={inputStyle} />
          </div>
        </div>

        {err && <div style={{ color: '#f87171', fontSize: 12, marginTop: 10 }}>{err}</div>}

        <div style={{ display: 'flex', gap: 8, marginTop: 20, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{ background: '#0f172a', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, padding: '7px 16px', cursor: 'pointer' }}>
            Cancel
          </button>
          <button onClick={create} disabled={saving} style={{ background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, padding: '7px 16px', cursor: saving ? 'not-allowed' : 'pointer', fontWeight: 600, opacity: saving ? 0.7 : 1 }}>
            {saving ? 'Creating…' : 'Create Portfolio'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Engine state badge (visible to all) ───────────────────────────────────────

function EngineStateBadge({ config }: { config: PaperPortfolioConfig }) {
  const enabled = config.enabled !== false;
  const paused = config.paused === true;
  const state = !enabled ? 'stopped' : paused ? 'paused' : 'running';
  const meta = {
    running: { label: '● Running', color: '#22c55e', bg: '#14532d33' },
    paused:  { label: '⏸ Paused',  color: '#f59e0b', bg: '#78350f33' },
    stopped: { label: '■ Stopped', color: '#ef4444', bg: '#7f1d1d33' },
  }[state];
  return (
    <span style={{
      fontSize: 12, fontWeight: 700, color: meta.color,
      background: meta.bg, border: `1px solid ${meta.color}44`,
      borderRadius: 5, padding: '4px 10px',
    }}>{meta.label}</span>
  );
}

// ── Engine controls (admin only) ──────────────────────────────────────────────

function EngineControls({ config, onDone, portfolioId }: { config: PaperPortfolioConfig; onDone: () => void; portfolioId?: number | null }) {
  const [busy, setBusy] = useState(false);
  const enabled = config.enabled !== false;
  const paused = config.paused === true;

  async function setState(state: 'running' | 'paused' | 'stopped') {
    setBusy(true);
    try { await api.paperSetEngine(state, portfolioId); onDone(); }
    catch { /* swallow — summary will stay current */ }
    finally { setBusy(false); }
  }

  const btnBase: React.CSSProperties = {
    border: 'none', borderRadius: 6, padding: '5px 13px',
    fontSize: 12, fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer',
    opacity: busy ? 0.6 : 1, transition: 'opacity 0.15s',
  };

  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {/* Start */}
      <button
        disabled={busy || (enabled && !paused)}
        onClick={() => setState('running')}
        title="Start — engine scans for new entries"
        style={{ ...btnBase, background: (enabled && !paused) ? '#166534' : '#22c55e', color: '#fff' }}
      >▶ Start</button>

      {/* Pause */}
      <button
        disabled={busy || !enabled || paused}
        onClick={() => setState('paused')}
        title="Pause — monitors open positions but stops new entries"
        style={{ ...btnBase, background: (!enabled || paused) ? '#78350f' : '#d97706', color: '#fff' }}
      >⏸ Pause</button>

      {/* Stop */}
      <button
        disabled={busy || !enabled}
        onClick={() => setState('stopped')}
        title="Stop — engine completely halted"
        style={{ ...btnBase, background: !enabled ? '#7f1d1d' : '#ef4444', color: '#fff' }}
      >■ Stop</button>
    </div>
  );
}

// ── Capital Panel (admin only) ────────────────────────────────────────────────

function CapitalPanel({
  initialCapital, currentCash, onSave, portfolioId,
}: { initialCapital: number; currentCash: number; onSave: () => void; portfolioId?: number | null }) {
  const [newInitial, setNewInitial] = useState('');
  const [newCash, setNewCash] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  async function save() {
    const body: { initial_capital?: number; current_cash?: number } = {};
    if (newInitial) body.initial_capital = parseFloat(newInitial);
    if (newCash)    body.current_cash    = parseFloat(newCash);
    if (!Object.keys(body).length) return;
    setSaving(true); setMsg('');
    try {
      const r = await api.paperSetCapital(body, portfolioId);
      setMsg(`Saved — capital $${r.initial_capital.toLocaleString()}, cash $${r.current_cash.toLocaleString()}`);
      setNewInitial(''); setNewCash('');
      onSave();
    } catch { setMsg('Error saving'); }
    finally { setSaving(false); }
  }

  const inputStyle: React.CSSProperties = {
    background: '#0f172a', border: '1px solid #334155', borderRadius: 5,
    color: '#f1f5f9', padding: '6px 10px', fontSize: 13, width: 140,
  };

  return (
    <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, border: '1px solid #334155' }}>
      <div style={{ fontWeight: 600, marginBottom: 14, color: '#f1f5f9' }}>Capital Settings (admin)</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'flex-end' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 11, color: '#94a3b8' }}>
            Initial Capital (benchmark) — current: ${initialCapital.toLocaleString()}
          </span>
          <input
            type="number" min="1000" step="1000"
            placeholder={`$${initialCapital.toLocaleString()}`}
            value={newInitial}
            onChange={e => setNewInitial(e.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 11, color: '#94a3b8' }}>
            Available Cash — current: ${currentCash.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </span>
          <input
            type="number" min="0" step="1000"
            placeholder={`$${currentCash.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
            value={newCash}
            onChange={e => setNewCash(e.target.value)}
            style={inputStyle}
          />
        </label>
        <button
          onClick={save}
          disabled={saving || (!newInitial && !newCash)}
          style={{
            background: '#3b82f6', color: '#fff', border: 'none',
            borderRadius: 6, padding: '7px 18px', cursor: 'pointer',
            fontWeight: 600, fontSize: 13, opacity: saving ? 0.6 : 1,
          }}
        >{saving ? 'Saving…' : 'Set Capital'}</button>
        {msg && <span style={{ color: msg.startsWith('Err') ? '#ef4444' : '#22c55e', fontSize: 12, alignSelf: 'center' }}>{msg}</span>}
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 10 }}>
        Initial Capital sets the return % baseline. Available Cash adjusts spendable funds without closing positions.
      </div>
    </div>
  );
}

// ── Config Panel ──────────────────────────────────────────────────────────────

function ConfigPanel({ config, onSave, portfolioId }: { config: PaperPortfolioConfig; onSave: () => void; portfolioId?: number | null }) {
  const [draft, setDraft] = useState<Partial<PaperPortfolioConfig>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  function field(key: keyof PaperPortfolioConfig, label: string, step = 0.01) {
    const cur = draft[key] ?? config[key];
    return (
      <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 11, color: '#94a3b8' }}>{label}</span>
        <input
          type="number" step={step}
          value={String(cur)}
          onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) setDraft(d => ({ ...d, [key]: v })); }}
          style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 5, color: '#f1f5f9', padding: '5px 8px', fontSize: 13, width: 120 }}
        />
      </label>
    );
  }

  async function save() {
    setSaving(true); setMsg('');
    try {
      await api.paperConfigure(draft, portfolioId);
      setMsg('Saved');
      onSave();
      setDraft({});
    } catch { setMsg('Error saving'); }
    finally { setSaving(false); }
  }

  async function reset() {
    if (!confirm('Reset portfolio? All open positions will be force-closed and cash reset to initial capital.')) return;
    try {
      const r = await api.paperReset(portfolioId);
      setMsg(`Reset — ${r.positions_closed} positions closed`);
      onSave();
    } catch { setMsg('Reset failed'); }
  }

  return (
    <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, border: '1px solid #334155' }}>
      <div style={{ fontWeight: 600, marginBottom: 16, color: '#f1f5f9' }}>Portfolio Config (admin)</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        {field('max_positions', 'Max Positions', 1)}
        {field('risk_per_trade_pct', 'Risk/Trade %')}
        {field('max_position_pct', 'Max Position %')}
        {field('min_confidence', 'Min Confidence', 1)}
        {field('min_kscore', 'Min K-Score', 1)}
        {field('min_rr_ratio', 'Min R:R', 0.1)}
        {field('min_entry_score', 'Min Entry Score', 1)}
        {field('max_hold_days', 'Max Hold Days', 1)}
        {field('trail_atr_mult', 'Trail ATR ×')}
        {field('partial_tp_pct', 'Partial TP %')}
        {field('trail_trigger_pct', 'Trail Trigger %')}
        {field('breakeven_trigger_pct', 'Breakeven %')}
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 16, alignItems: 'center' }}>
        <button
          onClick={save} disabled={saving || !Object.keys(draft).length}
          style={{ background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, padding: '7px 16px', cursor: 'pointer', fontWeight: 600 }}
        >{saving ? 'Saving…' : 'Save'}</button>
        <button
          onClick={reset}
          style={{ background: '#1e293b', color: '#ef4444', border: '1px solid #ef4444', borderRadius: 6, padding: '7px 16px', cursor: 'pointer', fontWeight: 600 }}
        >Reset Portfolio</button>
        {msg && <span style={{ color: msg.startsWith('Err') ? '#ef4444' : '#22c55e', fontSize: 13 }}>{msg}</span>}
      </div>
    </div>
  );
}

// ── AL-4: Trade Parameter Optimizer panel (admin only) ────────────────────────

const STYLE_OPTIONS = ['SWING', 'GROWTH', 'LONG', 'SHORT'];

function TradeParamsPanel({ onDone }: { onDone: () => void }) {
  const { data: params, mutate: mutateParams } = useSWR(
    'paper-trade-params', () => api.paperTradeParams(), { revalidateOnFocus: false }
  );
  const [tuningStyle, setTuningStyle] = useState('SWING');
  const [nTrials, setNTrials] = useState('80');
  const [launching, setLaunching] = useState(false);
  const [msg, setMsg] = useState('');

  async function launch() {
    setLaunching(true); setMsg('');
    try {
      const r = await api.paperTuneParams(tuningStyle, parseInt(nTrials) || 80);
      if (r.status === 'already_running') {
        setMsg(`${tuningStyle} tuning already running — check back in a few minutes`);
      } else {
        setMsg(`Started ${r.n_trials}-trial Optuna search for ${tuningStyle} — runs in background`);
        setTimeout(() => mutateParams(), 5000);
      }
    } catch { setMsg('Failed to start tuning'); }
    finally { setLaunching(false); }
  }

  const fmtPct = (v: number | undefined) => v != null ? `${((v - 1) * 100).toFixed(1)}%` : '—';
  const fmtStopPct = (v: number | undefined) => v != null ? `${((v - 1) * 100).toFixed(1)}%` : '—';

  return (
    <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, border: '1px solid #334155' }}>
      <div style={{ fontWeight: 600, marginBottom: 4, color: '#f1f5f9' }}>Trade Parameter Optimizer (AL-4)</div>
      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 16 }}>
        Optuna tunes stop %, take-profit %, and max hold days using your actual closed paper trades as the dataset.
        Params are saved to <code>/data/models/trade_params.json</code> and applied on the next engine cycle.
      </div>

      {params && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 16 }}>
          {STYLE_OPTIONS.map(style => {
            const p = params[style];
            if (!p) return null;
            const stopVal = p.best_stop_pct ?? p.stop_pct;
            const tpVal = p.best_tp_pct ?? p.tp_pct;
            const holdVal = p.best_max_hold_days ?? p.max_hold_days;
            return (
              <div key={style} style={{
                background: '#0f172a', borderRadius: 8, padding: '10px 14px',
                border: `1px solid ${p.is_tuned ? 'rgba(34,197,94,0.25)' : '#334155'}`,
                minWidth: 170,
              }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                  <span style={{
                    fontSize: 10, fontWeight: 700,
                    color: STYLE_COLORS[style] ?? '#94a3b8',
                    background: (STYLE_COLORS[style] ?? '#94a3b8') + '22',
                    borderRadius: 4, padding: '1px 6px',
                  }}>{style}</span>
                  {p.is_running && <span style={{ fontSize: 10, color: '#f59e0b' }}>⏳ Tuning…</span>}
                  {p.is_tuned && !p.is_running && <span style={{ fontSize: 10, color: '#22c55e' }}>✓ Tuned</span>}
                </div>
                <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.8 }}>
                  <div>Stop: <span style={{ color: '#f87171', fontWeight: 600 }}>{fmtStopPct(stopVal)}</span></div>
                  <div>Target: <span style={{ color: '#4ade80', fontWeight: 600 }}>+{fmtPct(tpVal)}</span></div>
                  <div>Hold: <span style={{ color: '#f1f5f9', fontWeight: 600 }}>{holdVal ?? '—'}d</span></div>
                  {p.best_sharpe != null && (
                    <div style={{ fontSize: 10, color: '#64748b', marginTop: 2 }}>
                      Sharpe {p.best_sharpe.toFixed(2)} · {p.n_trades} trades
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={tuningStyle} onChange={e => setTuningStyle(e.target.value)}
          style={{ background: '#0f172a', border: '1px solid #334155', color: '#f1f5f9', borderRadius: 5, padding: '5px 10px', fontSize: 12 }}>
          {STYLE_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <input value={nTrials} onChange={e => setNTrials(e.target.value)} type="number" min="20" max="300"
          placeholder="80 trials"
          style={{ width: 90, background: '#0f172a', border: '1px solid #334155', color: '#f1f5f9', borderRadius: 5, padding: '5px 8px', fontSize: 12 }} />
        <button onClick={launch} disabled={launching}
          style={{ background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 16px', cursor: launching ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: 12, opacity: launching ? 0.7 : 1 }}>
          {launching ? 'Starting…' : 'Run Optuna'}
        </button>
        <button onClick={() => mutateParams()}
          style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '6px 12px', cursor: 'pointer', fontSize: 12 }}>
          Refresh
        </button>
        {msg && <span style={{ fontSize: 11, color: '#f59e0b' }}>{msg}</span>}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

const TABS = ['Positions', 'Decisions', 'Closed Trades', 'Equity Curve', 'Attribution'] as const;
type Tab = typeof TABS[number];

export default function PaperPortfolioPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>('Positions');
  const [isAdmin, setIsAdmin] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [tradesPage, setTradesPage] = useState(1);
  const [decPage, setDecPage] = useState(1);
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<number | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [expandedDecisionId, setExpandedDecisionId] = useState<number | null>(null);
  // Broker assignment per portfolio
  const [portfolioBroker, setPortfolioBroker] = useState<{ broker_connection_id: number | null; broker: import('@/lib/api').BrokerConnection | null } | null>(null);
  const [brokerConnections, setBrokerConnections] = useState<import('@/lib/api').BrokerConnection[]>([]);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (session.role !== 'admin') { router.replace('/'); return; }
    setIsAdmin(true);
    setAuthed(true);
  }, [router]);

  const { data: portfolioList, mutate: mutateList } = useSWR(
    authed ? 'paper-list' : null, () => api.paperList(), { refreshInterval: 60_000 }
  );

  // Auto-select first portfolio when list loads
  useEffect(() => {
    if (portfolioList?.length && selectedPortfolioId === null) {
      setSelectedPortfolioId(portfolioList[0].id);
    }
  }, [portfolioList, selectedPortfolioId]);

  // Load broker connections list once
  useEffect(() => {
    if (!authed) return;
    api.brokerList().then(setBrokerConnections).catch(() => {});
  }, [authed]);

  // Load broker assignment whenever selected portfolio changes
  useEffect(() => {
    if (!selectedPortfolioId) { setPortfolioBroker(null); return; }
    api.brokerGetPortfolioBroker(selectedPortfolioId).then(setPortfolioBroker).catch(() => {});
  }, [selectedPortfolioId]);

  const { data: compareData } = useSWR(
    authed && (portfolioList?.length ?? 0) > 1 ? 'paper-compare' : null,
    () => api.paperCompare(180), { refreshInterval: 300_000 }
  );

  const { data: summary, mutate: mutateSummary, error: summaryError } = useSWR(
    authed && selectedPortfolioId != null ? ['paper-summary', selectedPortfolioId] : null,
    () => api.paperSummary(selectedPortfolioId), { refreshInterval: 60_000 }
  );
  const { data: positions } = useSWR(
    authed && tab === 'Positions' && selectedPortfolioId != null ? ['paper-positions', selectedPortfolioId] : null,
    () => api.paperPositions(selectedPortfolioId), { refreshInterval: 60_000 }
  );

  // INT-9: research verdicts for open positions
  const [posResearchMap, setPosResearchMap] = useState<Record<string, ResearchSummary>>({});
  const posSymbols = useMemo(() => positions?.map(p => p.symbol) ?? [], [positions]);
  useEffect(() => {
    if (!posSymbols.length) { setPosResearchMap({}); return; }
    api.getResearchBatch(posSymbols).then(r => setPosResearchMap(r ?? {})).catch(() => {});
  }, [posSymbols.join(',')]);
  const { data: trades } = useSWR(
    authed && tab === 'Closed Trades' && selectedPortfolioId != null ? ['paper-trades', tradesPage, selectedPortfolioId] : null,
    () => api.paperTrades({ page: tradesPage, limit: 50, portfolioId: selectedPortfolioId })
  );
  const { data: curve } = useSWR(
    authed && tab === 'Equity Curve' && selectedPortfolioId != null ? ['paper-curve', selectedPortfolioId] : null,
    () => api.paperEquityCurve(180, selectedPortfolioId)
  );
  const { data: decisions } = useSWR(
    authed && tab === 'Decisions' && selectedPortfolioId != null ? ['paper-decisions', decPage, selectedPortfolioId] : null,
    () => api.paperDecisions({ page: decPage, limit: 50, days_back: 90, portfolioId: selectedPortfolioId })
  );
  const { data: attribution } = useSWR(
    authed && tab === 'Attribution' && selectedPortfolioId != null ? ['paper-attribution', selectedPortfolioId] : null,
    () => api.paperAttribution(selectedPortfolioId), { revalidateOnFocus: false }
  );

  if (!authed) return null;

  if (summaryError) {
    return (
      <main style={{ minHeight: '100vh', background: '#0f172a', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ color: '#f87171' }}>Failed to load paper portfolio data.</div>
      </main>
    );
  }

  if (!portfolioList || !summary) {
    return (
      <main style={{ minHeight: '100vh', background: '#0f172a', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ color: '#94a3b8' }}>Loading paper portfolio…</div>
      </main>
    );
  }

  const multiPortfolio = portfolioList.length > 1;
  const bestSharpeId = portfolioList.reduce<number | null>((best, p) => {
    if (p.sharpe == null) return best;
    const bestSharpe = portfolioList.find(x => x.id === best)?.sharpe ?? null;
    return bestSharpe == null || p.sharpe > bestSharpe ? p.id : best;
  }, null);

  const ret = summary.total_return_pct;
  const retColor = ret >= 0 ? '#22c55e' : '#ef4444';

  return (
    <main style={{ minHeight: '100vh', background: '#0f172a', color: '#f1f5f9', padding: '24px 20px', fontFamily: 'sans-serif' }}>
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>Paper Portfolio</div>
            <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 3 }}>
              {multiPortfolio ? `${portfolioList.length} active portfolios · A/B strategy comparison` : `${summary.trading_style} style · autonomous paper trading engine`}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <EngineStateBadge config={summary.config} />
            {isAdmin && <EngineControls config={summary.config} onDone={mutateSummary} portfolioId={selectedPortfolioId} />}
            {isAdmin && (
              <button
                onClick={() => setShowCreateModal(true)}
                style={{ fontSize: 12, color: '#3b82f6', background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.3)', borderRadius: 5, padding: '4px 10px', cursor: 'pointer', fontWeight: 600 }}
              >+ New Portfolio</button>
            )}
            <span style={{ fontSize: 12, color: '#64748b', background: '#1e293b', border: '1px solid #334155', borderRadius: 5, padding: '4px 10px' }}>
              Live · 60s refresh
            </span>
            <Link href="/" style={{ fontSize: 12, color: '#64748b', textDecoration: 'none' }}>← Home</Link>
          </div>
        </div>

        {/* Disclosure banner */}
        <div style={{ background: 'rgba(251,191,36,0.07)', border: '1px solid rgba(251,191,36,0.2)', borderRadius: 6, padding: '8px 14px', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: '#fbbf24', fontSize: 13 }}>⚠</span>
          <span style={{ fontSize: 11, color: '#94a3b8' }}>
            <strong style={{ color: '#fbbf24', fontWeight: 600 }}>Simulated results only.</strong>{' '}
            All trades include 10 bps entry + 10 bps exit slippage. No commissions, market impact, or liquidity constraints are modelled.
            Real-world performance will differ. Past paper-trading results do not guarantee future live returns.
          </span>
        </div>

        {/* Broker assignment bar */}
        {isAdmin && selectedPortfolioId != null && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Broker:</span>
            {portfolioBroker?.broker ? (
              <>
                <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 5, background: 'rgba(34,211,238,0.1)', border: '1px solid rgba(34,211,238,0.25)', color: '#22d3ee' }}>
                  {portfolioBroker.broker.broker_type === 'etrade' ? 'E*Trade Live' :
                   portfolioBroker.broker.broker_type === 'etrade_sandbox' ? 'E*Trade Sandbox' : 'Fidelity Manual'}
                  {' — '}{portfolioBroker.broker.name}
                  {portfolioBroker.broker.is_authorized
                    ? <span style={{ marginLeft: 6, color: '#4ade80' }}>✓</span>
                    : <span style={{ marginLeft: 6, color: '#fbbf24' }}>⚠ not authorized</span>}
                </span>
                <button onClick={() => { if (selectedPortfolioId) api.brokerAssignPortfolio(selectedPortfolioId, null).then(() => api.brokerGetPortfolioBroker(selectedPortfolioId).then(setPortfolioBroker)); }}
                  style={{ fontSize: 11, color: '#94a3b8', background: 'transparent', border: '1px solid #334155', borderRadius: 4, padding: '3px 8px', cursor: 'pointer' }}>
                  Unlink
                </button>
              </>
            ) : (
              <>
                <span style={{ fontSize: 11, color: '#475569' }}>Paper only (simulation)</span>
                {brokerConnections.length > 0 && (
                  <select
                    defaultValue=""
                    onChange={e => {
                      const id = parseInt(e.target.value);
                      if (!isNaN(id) && selectedPortfolioId) {
                        api.brokerAssignPortfolio(selectedPortfolioId, id).then(() =>
                          api.brokerGetPortfolioBroker(selectedPortfolioId).then(setPortfolioBroker)
                        );
                      }
                    }}
                    style={{ fontSize: 11, background: '#0f172a', border: '1px solid #1e293b', borderRadius: 5, padding: '4px 8px', color: '#94a3b8', cursor: 'pointer' }}
                  >
                    <option value="">Link a broker…</option>
                    {brokerConnections.map(b => (
                      <option key={b.id} value={b.id}>{b.name} ({b.broker_type})</option>
                    ))}
                  </select>
                )}
                {brokerConnections.length === 0 && (
                  <Link href="/settings" style={{ fontSize: 11, color: '#22d3ee', textDecoration: 'none' }}>+ Add broker in Settings →</Link>
                )}
              </>
            )}
          </div>
        )}

        {/* Multi-portfolio comparison grid */}
        {multiPortfolio && (
          <div style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
              {portfolioList.map(p => (
                <PortfolioCard
                  key={p.id}
                  portfolio={p}
                  selected={p.id === selectedPortfolioId}
                  isBestSharpe={p.id === bestSharpeId}
                  onSelect={() => { setSelectedPortfolioId(p.id); setTab('Positions'); setTradesPage(1); setDecPage(1); }}
                />
              ))}
            </div>
            {compareData && compareData.some(d => d.curve.length > 0) && (
              <div style={{ background: '#0f172a', borderRadius: 10, border: '1px solid #1e293b', padding: '14px 12px' }}>
                <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>Normalized return % — all portfolios vs SPY</div>
                <CompareEquityChart data={compareData} />
              </div>
            )}
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8, marginTop: 4 }}>
              Click a portfolio card to view its detail below
            </div>
          </div>
        )}

        {showCreateModal && (
          <CreatePortfolioModal
            onClose={() => setShowCreateModal(false)}
            onCreated={() => { mutateList(); }}
          />
        )}

        {/* Stat strip */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 24 }}>
          <StatCard label="Equity" value={fmtUSD(summary.current_equity)} sub={`Cash ${fmtUSD(summary.current_cash)}`} />
          <StatCard label="Total Return" value={fmtPct(summary.total_return_pct)} color={retColor}
            sub={`Initial ${fmtUSD(summary.initial_capital)}`} />
          <StatCard label="Realized P&L" value={fmtUSD(summary.total_realized_pnl)}
            color={summary.total_realized_pnl >= 0 ? '#22c55e' : '#ef4444'} />
          <StatCard label="Unrealized P&L" value={fmtUSD(summary.total_unrealized_pnl)}
            color={summary.total_unrealized_pnl >= 0 ? '#22c55e' : '#ef4444'}
            sub={`${summary.open_positions} open positions`} />
          <StatCard label="Win Rate" value={summary.win_rate_pct.toFixed(1) + '%'}
            sub={`${summary.closed_trades} closed trades`} />
          <StatCard label="Avg Win / Loss"
            value={`${fmtPct(summary.avg_win_pct, 1)} / ${fmtPct(summary.avg_loss_pct, 1)}`}
            color={summary.avg_win_pct > Math.abs(summary.avg_loss_pct) ? '#22c55e' : '#f59e0b'} />
          {summary.cagr_pct != null && (
            <StatCard
              label="CAGR"
              value={(summary.cagr_pct >= 0 ? '+' : '') + summary.cagr_pct.toFixed(1) + '%'}
              color={summary.cagr_pct >= 15 ? '#22c55e' : summary.cagr_pct >= 0 ? '#f59e0b' : '#ef4444'}
              sub="annualised compound return"
            />
          )}
          <StatCard
            label="Sharpe Ratio"
            value={summary.sharpe != null ? summary.sharpe.toFixed(2) : '—'}
            color={summary.sharpe == null ? undefined : summary.sharpe >= 1 ? '#22c55e' : summary.sharpe >= 0 ? '#f59e0b' : '#ef4444'}
            sub={summary.insufficient_data ? `< 20 days data (${summary.data_days ?? 0}d)` : 'annualised, rf=5%'}
          />
          {summary.sortino != null && (
            <StatCard
              label="Sortino Ratio"
              value={summary.sortino.toFixed(2)}
              color={summary.sortino >= 1.5 ? '#22c55e' : summary.sortino >= 0 ? '#f59e0b' : '#ef4444'}
              sub="return / downside vol"
            />
          )}
          <StatCard
            label="Max Drawdown"
            value={summary.max_drawdown_pct != null ? `-${summary.max_drawdown_pct.toFixed(1)}%` : '—'}
            color={summary.max_drawdown_pct == null ? undefined : summary.max_drawdown_pct <= 10 ? '#22c55e' : summary.max_drawdown_pct <= 20 ? '#f59e0b' : '#ef4444'}
            sub="peak → trough"
          />
          <StatCard
            label="Calmar Ratio"
            value={summary.calmar != null ? summary.calmar.toFixed(2) : '—'}
            color={summary.calmar == null ? undefined : summary.calmar >= 1 ? '#22c55e' : summary.calmar >= 0.5 ? '#f59e0b' : '#ef4444'}
            sub="return / drawdown"
          />
          {summary.outperformance_vs_spy != null && (
            <StatCard
              label="vs SPY"
              value={(summary.outperformance_vs_spy >= 0 ? '+' : '') + summary.outperformance_vs_spy.toFixed(1) + '%'}
              color={summary.outperformance_vs_spy >= 0 ? '#22c55e' : '#ef4444'}
              sub="portfolio excess return"
            />
          )}
          {summary.outperformance_vs_qqq != null && (
            <StatCard
              label="vs QQQ"
              value={(summary.outperformance_vs_qqq >= 0 ? '+' : '') + summary.outperformance_vs_qqq.toFixed(1) + '%'}
              color={summary.outperformance_vs_qqq >= 0 ? '#22c55e' : '#ef4444'}
              sub="portfolio excess return"
            />
          )}
          {summary.regime_state && (
            <StatCard
              label="Regime"
              value={summary.regime_state.replace('_', ' ').toUpperCase()}
              color={
                summary.regime_state === 'bull' ? '#22c55e' :
                summary.regime_state === 'neutral' ? '#94a3b8' :
                summary.regime_state === 'choppy' ? '#f59e0b' :
                summary.regime_state === 'risk_off' ? '#f97316' : '#ef4444'
              }
              sub={summary.regime_vix != null ? `VIX ${summary.regime_vix.toFixed(1)}` : 'market regime'}
            />
          )}
          {summary.alpha != null && (
            <StatCard
              label="Alpha (ann.)"
              value={(summary.alpha >= 0 ? '+' : '') + summary.alpha.toFixed(1) + '%'}
              color={summary.alpha >= 0 ? '#22c55e' : '#ef4444'}
              sub={summary.beta != null ? `β ${summary.beta.toFixed(2)}` : 'vs SPY'}
            />
          )}
          {summary.info_ratio != null && (
            <StatCard
              label="Info Ratio"
              value={summary.info_ratio.toFixed(2)}
              color={summary.info_ratio >= 0.5 ? '#22c55e' : summary.info_ratio >= 0 ? '#f59e0b' : '#ef4444'}
              sub="active return / tracking err"
            />
          )}
          {summary.profit_factor != null && (
            <StatCard
              label="Profit Factor"
              value={summary.profit_factor.toFixed(2)}
              color={summary.profit_factor >= 1.5 ? '#22c55e' : summary.profit_factor >= 1 ? '#f59e0b' : '#ef4444'}
              sub="gross profit / gross loss"
            />
          )}
          {summary.avg_hold_days != null && (
            <StatCard
              label="Avg Hold"
              value={summary.avg_hold_days.toFixed(0) + 'd'}
              sub={`${summary.closed_trades} trades · expectancy ${summary.expectancy_pct != null ? (summary.expectancy_pct >= 0 ? '+' : '') + summary.expectancy_pct.toFixed(1) + '%' : '—'}`}
            />
          )}
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, borderBottom: '1px solid #1e293b', paddingBottom: 1 }}>
          {TABS.map(t => (
            <button key={t} onClick={() => setTab(t)} style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: '8px 16px',
              color: tab === t ? '#3b82f6' : '#94a3b8',
              borderBottom: tab === t ? '2px solid #3b82f6' : '2px solid transparent',
              fontWeight: tab === t ? 600 : 400, fontSize: 14, transition: 'all 0.15s',
            }}>{t}</button>
          ))}
        </div>

        {/* Positions tab */}
        {tab === 'Positions' && (
          <div style={{ overflowX: 'auto' }}>
            {!positions?.length ? (
              <div style={{ color: '#64748b', padding: 24, textAlign: 'center' }}>
                No open positions. The engine enters trades during market hours when BUY signals appear.
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                    {['Symbol', 'Entry', 'Current', 'Shares', 'Value', 'P&L', 'Stop', 'Target', 'Days', 'Score', 'R:R', 'Conf', 'Research'].map(h => (
                      <th key={h} style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map(p => (
                    <tr key={p.id} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '9px 10px' }}>
                        <Link href={`/stock/${p.symbol}`} style={{ color: '#60a5fa', fontWeight: 600, textDecoration: 'none' }}>{p.symbol}</Link>
                      </td>
                      <td style={{ padding: '9px 10px' }}>${p.entry_price.toFixed(2)}</td>
                      <td style={{ padding: '9px 10px' }}>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{p.shares.toFixed(2)}</td>
                      <td style={{ padding: '9px 10px' }}>${p.position_value.toFixed(0)}</td>
                      <td style={{ padding: '9px 10px', color: p.unrealized_pnl >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                        {fmtPct(p.unrealized_pct)} (${p.unrealized_pnl.toFixed(0)})
                      </td>
                      <td style={{ padding: '9px 10px', color: '#f59e0b' }}>${p.current_stop.toFixed(2)}</td>
                      <td style={{ padding: '9px 10px', color: '#94a3b8' }}>{p.take_profit != null ? `$${p.take_profit.toFixed(2)}` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{p.hold_days}d</td>
                      <td style={{ padding: '9px 10px' }}>{p.entry_score ?? '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{p.rr_ratio_at_entry != null ? `${p.rr_ratio_at_entry.toFixed(1)}:1` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{p.confidence_at_entry != null ? `${p.confidence_at_entry.toFixed(0)}%` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>
                        {(() => {
                          const rs = posResearchMap[p.symbol];
                          if (!rs) return <span style={{ fontSize: 10, color: '#334155' }}>—</span>;
                          const RC: Record<string, string> = { 'STRONG BUY': '#4ade80', BUY: '#86efac', WATCH: '#facc15', AVOID: '#fb923c', SELL: '#f87171' };
                          const col = RC[rs.recommendation] ?? '#94a3b8';
                          const warn = rs.recommendation === 'AVOID' || rs.recommendation === 'SELL';
                          const genDate = rs.generated_at ? new Date(rs.generated_at) : null;
                          const ageH = genDate && !isNaN(genDate.getTime()) ? Math.round((Date.now() - genDate.getTime()) / 3600000) : null;
                          return (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                              <span style={{ fontSize: 10, fontWeight: 700, color: col, background: warn ? 'rgba(251,146,60,0.12)' : 'transparent', border: warn ? '1px solid rgba(251,146,60,0.3)' : 'none', borderRadius: 3, padding: warn ? '1px 4px' : 0 }}>
                                {rs.recommendation === 'STRONG BUY' ? 'S.BUY' : rs.recommendation}
                              </span>
                              {ageH !== null && <span style={{ fontSize: 9, color: '#475569' }}>{ageH}h ago</span>}
                            </div>
                          );
                        })()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* Decisions tab */}
        {tab === 'Decisions' && (
          <div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                    {['Symbol', 'Time', 'Price', 'Score', 'R:R', 'Conf', 'K-Score', 'Regime', 'Status', 'P&L', 'Notes'].map(h => (
                      <th key={h} style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(decisions?.items ?? []).map(d => {
                    const isExpanded = expandedDecisionId === d.id;
                    const reasons = d.entry_reasons ?? {};
                    const exitReasons = d.exit_reasons ?? {};
                    const reasonKeys = Object.keys(reasons).filter(k => !['stability_days'].includes(k));
                    return (
                    <>
                    <tr key={d.id}
                      onClick={() => setExpandedDecisionId(isExpanded ? null : d.id)}
                      style={{ borderBottom: isExpanded ? 'none' : '1px solid #1e293b', cursor: 'pointer',
                        background: isExpanded ? '#0f1a2e' : undefined, transition: 'background 0.1s' }}>
                      <td style={{ padding: '9px 10px' }}>
                        <Link href={`/stock/${d.symbol}`} style={{ color: '#60a5fa', fontWeight: 600, textDecoration: 'none' }}
                          onClick={e => e.stopPropagation()}>{d.symbol}</Link>
                      </td>
                      <td style={{ padding: '9px 10px', color: '#64748b' }}>{fmtTs(d.entry_time)}</td>
                      <td style={{ padding: '9px 10px' }}>${d.entry_price.toFixed(2)}</td>
                      <td style={{ padding: '9px 10px', fontWeight: 700, color: (d.entry_score ?? 0) >= 5 ? '#22c55e' : '#f1f5f9' }}>
                        {d.entry_score ?? '—'}
                      </td>
                      <td style={{ padding: '9px 10px' }}>{d.rr_ratio_at_entry != null ? `${d.rr_ratio_at_entry.toFixed(1)}:1` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{d.confidence_at_entry != null ? `${d.confidence_at_entry.toFixed(0)}%` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{d.kscore_at_entry != null ? d.kscore_at_entry.toFixed(0) : '—'}</td>
                      <td style={{ padding: '9px 10px', color: '#94a3b8', textTransform: 'capitalize' }}>{d.market_regime_at_entry ?? '—'}</td>
                      <td style={{ padding: '9px 10px' }}>
                        {d.stage === 'open' ? (
                          <span style={{ color: '#22c55e', fontSize: 11, fontWeight: 600 }}>OPEN</span>
                        ) : (
                          <ExitBadge reason={d.exit_reason} />
                        )}
                      </td>
                      <td style={{ padding: '9px 10px', color: (d.pct_return ?? 0) >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                        {fmtPct(d.pct_return)}
                      </td>
                      <td style={{ padding: '9px 10px', color: '#64748b', maxWidth: 300, fontSize: 11 }}>
                        <span style={{ marginRight: 6 }}>{(d.decision_notes ?? []).slice(0, 2).join(' · ')}</span>
                        <span style={{ color: '#334155', fontSize: 10 }}>{isExpanded ? '▲' : '▼'}</span>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${d.id}-expand`} style={{ borderBottom: '1px solid #1e293b' }}>
                        <td colSpan={11} style={{ padding: '0 10px 14px 10px', background: '#0f1a2e' }}>
                          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 12 }}>
                            {/* Entry stats */}
                            <div>
                              <div style={{ color: '#64748b', fontSize: 10, marginBottom: 6, fontWeight: 600, letterSpacing: 1 }}>ENTRY DETAILS</div>
                              <div style={{ color: '#94a3b8', lineHeight: 1.8 }}>
                                <div>Shares: <strong style={{ color: '#f1f5f9' }}>{d.shares}</strong></div>
                                <div>Stop: <strong style={{ color: '#f87171' }}>${d.stop_loss.toFixed(2)}</strong></div>
                                {d.take_profit && <div>Target: <strong style={{ color: '#4ade80' }}>${d.take_profit.toFixed(2)}</strong></div>}
                                {d.hold_days > 0 && <div>Held: <strong style={{ color: '#f1f5f9' }}>{d.hold_days}d</strong></div>}
                              </div>
                            </div>
                            {/* All decision notes */}
                            {(d.decision_notes ?? []).length > 0 && (
                              <div style={{ flex: 1, minWidth: 200 }}>
                                <div style={{ color: '#64748b', fontSize: 10, marginBottom: 6, fontWeight: 600, letterSpacing: 1 }}>ENTRY NOTES</div>
                                {d.decision_notes.map((n, i) => (
                                  <div key={i} style={{ color: '#94a3b8', lineHeight: 1.8 }}>• {n}</div>
                                ))}
                              </div>
                            )}
                            {/* Signal reasons */}
                            {reasonKeys.length > 0 && (
                              <div style={{ flex: 2, minWidth: 240 }}>
                                <div style={{ color: '#64748b', fontSize: 10, marginBottom: 6, fontWeight: 600, letterSpacing: 1 }}>AI SIGNAL REASONS</div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px' }}>
                                  {reasonKeys.map(k => {
                                    const v = reasons[k];
                                    const display = typeof v === 'number' ? (v as number).toFixed(2) : String(v);
                                    const isPos = typeof v === 'number' && (v as number) > 0;
                                    const isNeg = typeof v === 'number' && (v as number) < 0;
                                    return (
                                      <span key={k} style={{ fontSize: 11, color: isPos ? '#4ade80' : isNeg ? '#f87171' : '#94a3b8' }}>
                                        {k.replace(/_/g, ' ')}: <strong>{display}</strong>
                                      </span>
                                    );
                                  })}
                                </div>
                              </div>
                            )}
                            {/* Exit reasons */}
                            {Object.keys(exitReasons).length > 0 && (
                              <div style={{ flex: 1, minWidth: 200 }}>
                                <div style={{ color: '#64748b', fontSize: 10, marginBottom: 6, fontWeight: 600, letterSpacing: 1 }}>EXIT REASON</div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px' }}>
                                  {Object.entries(exitReasons).map(([k, v]) => (
                                    <span key={k} style={{ fontSize: 11, color: '#94a3b8' }}>
                                      {k.replace(/_/g, ' ')}: <strong style={{ color: '#f1f5f9' }}>{String(v)}</strong>
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                    </>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {decisions && decisions.pages > 1 && (
              <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'center' }}>
                <button disabled={decPage === 1} onClick={() => setDecPage(p => p - 1)}
                  style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: 5, padding: '5px 12px', cursor: 'pointer' }}>←</button>
                <span style={{ color: '#64748b', lineHeight: '30px' }}>{decPage} / {decisions.pages}</span>
                <button disabled={decPage === decisions.pages} onClick={() => setDecPage(p => p + 1)}
                  style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: 5, padding: '5px 12px', cursor: 'pointer' }}>→</button>
              </div>
            )}
          </div>
        )}

        {/* Closed Trades tab */}
        {tab === 'Closed Trades' && (
          <div>
            <div style={{ overflowX: 'auto' }}>
              {!trades?.items.length ? (
                <div style={{ color: '#64748b', padding: 24, textAlign: 'center' }}>No closed trades yet.</div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                      {['Symbol', 'Entry', 'Exit', 'Entry $', 'Exit $', 'P&L %', 'P&L $', 'Days', 'Exit Reason', 'R:R', 'Score'].map(h => (
                        <th key={h} style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {trades.items.map(t => (
                      <tr key={t.id} style={{ borderBottom: '1px solid #1e293b' }}>
                        <td style={{ padding: '9px 10px' }}>
                          <Link href={`/stock/${t.symbol}`} style={{ color: '#60a5fa', fontWeight: 600, textDecoration: 'none' }}>{t.symbol}</Link>
                        </td>
                        <td style={{ padding: '9px 10px', color: '#64748b' }}>{fmtDate(t.entry_date)}</td>
                        <td style={{ padding: '9px 10px', color: '#64748b' }}>{fmtDate(t.exit_time)}</td>
                        <td style={{ padding: '9px 10px' }}>${t.entry_price.toFixed(2)}</td>
                        <td style={{ padding: '9px 10px' }}>{t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '—'}</td>
                        <td style={{ padding: '9px 10px', color: (t.pct_return ?? 0) >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>
                          {fmtPct(t.pct_return)}
                        </td>
                        <td style={{ padding: '9px 10px', color: (t.pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                          {t.pnl != null ? `$${t.pnl.toFixed(0)}` : '—'}
                        </td>
                        <td style={{ padding: '9px 10px' }}>{t.hold_days}d</td>
                        <td style={{ padding: '9px 10px' }}><ExitBadge reason={t.exit_reason} /></td>
                        <td style={{ padding: '9px 10px' }}>{t.rr_ratio_at_entry != null ? `${t.rr_ratio_at_entry.toFixed(1)}:1` : '—'}</td>
                        <td style={{ padding: '9px 10px' }}>{t.entry_score ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            {trades && trades.pages > 1 && (
              <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'center' }}>
                <button disabled={tradesPage === 1} onClick={() => setTradesPage(p => p - 1)}
                  style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: 5, padding: '5px 12px', cursor: 'pointer' }}>←</button>
                <span style={{ color: '#64748b', lineHeight: '30px' }}>{tradesPage} / {trades.pages}</span>
                <button disabled={tradesPage === trades.pages} onClick={() => setTradesPage(p => p + 1)}
                  style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: 5, padding: '5px 12px', cursor: 'pointer' }}>→</button>
              </div>
            )}
          </div>
        )}

        {/* Equity Curve tab */}
        {tab === 'Equity Curve' && (
          <div>
            <div style={{ background: '#0f172a', borderRadius: 10, border: '1px solid #1e293b', padding: '16px 12px', marginBottom: 20 }}>
              <div style={{ fontSize: 13, color: '#94a3b8', marginBottom: 10 }}>
                Portfolio equity vs SPY/QQQ benchmarks (rebased to same starting capital)
              </div>
              <EquityChart data={curve ?? []} initialCapital={summary.initial_capital} />
            </div>
            {(curve?.length ?? 0) > 0 && <BenchmarkTable data={curve ?? []} />}
            {(curve?.length ?? 0) === 0 && (
              <div style={{ color: '#64748b', fontSize: 13, textAlign: 'center' }}>
                Equity curve snapshots are taken once per day after market close. Check back after first trading session.
              </div>
            )}
          </div>
        )}

        {tab === 'Attribution' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {!attribution || attribution.message ? (
              <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 24, textAlign: 'center' }}>
                <div style={{ fontSize: 32, marginBottom: 8 }}>📊</div>
                <div style={{ color: '#64748b' }}>{attribution?.message ?? 'No closed trades yet.'}</div>
              </div>
            ) : (
              <>
                {attribution.best_profile && (
                  <div style={{ background: 'rgba(34,197,94,0.07)', border: '1px solid rgba(34,197,94,0.25)', borderRadius: 8, padding: '12px 16px', display: 'flex', gap: 12, alignItems: 'center' }}>
                    <span style={{ fontSize: 20 }}>🏆</span>
                    <div>
                      <span style={{ color: '#4ade80', fontWeight: 700, fontSize: 13 }}>Best entry profile: </span>
                      <span style={{ color: '#e2e8f0', fontSize: 13 }}>Score {attribution.best_profile.score_band} · Confidence {attribution.best_profile.conf_band}</span>
                      <span style={{ color: '#4ade80', fontWeight: 700, fontSize: 13 }}> → {attribution.best_profile.win_rate}% win rate</span>
                      <span style={{ color: '#64748b', fontSize: 12 }}> (n={attribution.best_profile.count})</span>
                    </div>
                  </div>
                )}
                {[
                  { title: 'By Entry Score', rows: attribution.by_score },
                  { title: 'By Confidence at Entry', rows: attribution.by_confidence },
                  { title: 'By Market Regime at Entry', rows: attribution.by_regime },
                  { title: 'By Risk:Reward Ratio', rows: attribution.by_rr },
                ].map(({ title, rows }) => (
                  <div key={title} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 12 }}>{title}</div>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid #1e293b' }}>
                          {['Band', 'Trades', 'Win Rate', 'Avg Return', 'Profit Factor'].map(h => (
                            <th key={h} style={{ padding: '4px 8px', textAlign: h === 'Band' ? 'left' : 'right', color: '#475569', fontWeight: 500 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.filter(r => r.count > 0).map(r => (
                          <tr key={r.band} style={{ borderBottom: '1px solid #0f172a' }}>
                            <td style={{ padding: '5px 8px', color: '#e2e8f0', fontWeight: 500 }}>{r.band}</td>
                            <td style={{ padding: '5px 8px', color: '#64748b', textAlign: 'right' }}>{r.count}</td>
                            <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600,
                              color: r.win_rate == null ? '#475569' : r.win_rate >= 60 ? '#4ade80' : r.win_rate >= 50 ? '#facc15' : '#f87171' }}>
                              {r.win_rate != null ? `${r.win_rate}%` : '—'}
                            </td>
                            <td style={{ padding: '5px 8px', textAlign: 'right',
                              color: r.avg_return == null ? '#475569' : r.avg_return >= 0 ? '#4ade80' : '#f87171' }}>
                              {r.avg_return != null ? `${r.avg_return >= 0 ? '+' : ''}${r.avg_return.toFixed(2)}%` : '—'}
                            </td>
                            <td style={{ padding: '5px 8px', textAlign: 'right',
                              color: r.profit_factor == null ? '#475569' : r.profit_factor >= 1.5 ? '#4ade80' : r.profit_factor >= 1 ? '#facc15' : '#f87171' }}>
                              {r.profit_factor != null ? r.profit_factor.toFixed(2) : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </>
            )}
          </div>
        )}

        {/* Admin panels (bottom) */}
        {isAdmin && (
          <div style={{ marginTop: 32, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <CapitalPanel
              initialCapital={summary.initial_capital}
              currentCash={summary.current_cash}
              onSave={mutateSummary}
              portfolioId={selectedPortfolioId}
            />
            <ConfigPanel config={summary.config} onSave={mutateSummary} portfolioId={selectedPortfolioId} />
            <TradeParamsPanel onDone={mutateSummary} />
          </div>
        )}

        {/* Explainer */}
        <div style={{ marginTop: 32, background: '#1e293b', borderRadius: 10, padding: 16, border: '1px solid #334155', fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
          <strong style={{ color: '#94a3b8' }}>How it works:</strong> The paper engine runs every 5–10 minutes during market hours.
          It scans for fresh GROWTH-style BUY signals, scores entry quality (R:R, RSI, regime, sector, conviction),
          and enters simulated positions when the score meets the threshold. It monitors all open positions each cycle,
          updating trailing stops and exiting when stops, targets, signal reversals, or time limits are reached.
          Initial capital: {fmtUSD(summary.initial_capital)}. Risk per trade: {(summary.config.risk_per_trade_pct * 100).toFixed(0)}% of equity.
        </div>
      </div>
    </main>
  );
}

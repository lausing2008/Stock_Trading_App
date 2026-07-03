import React, { useState, useEffect, useRef, useMemo } from 'react';
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
  type DeDivergenceResponse,
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

function fmtCurrency(v: number | null | undefined, market?: string): string {
  if (v == null) return '—';
  if (market === 'HK') {
    return 'HK$' + new Intl.NumberFormat('en-HK', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(v);
  }
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
  hold_stall_timeout: '#64748b',
  momentum_exit: '#a78bfa',
  manual_exit: '#475569',
  manual_reset: '#64748b',
};

const EXIT_LABELS: Record<string, string> = {
  stop_hit: 'SL',
  target_reached: 'TP',
  signal_exit: 'Sig',
  time_stop: 'Days',
  hold_stall_timeout: 'Stall',
  momentum_exit: 'Mom',
  manual_exit: 'Manual',
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

  function periodReturn(daysBack: number, field: 'equity' | 'spy_close' | 'qqq_close' | 'hsi_close'): number | null {
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
    hsi: periodReturn(p.days, 'hsi_close'),
  })).filter(r => r.portfolio !== null || r.spy !== null);

  const hasHsi = rows.some(r => r.hsi != null);

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
              {hasHsi && <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>HSI</th>}
              <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>vs SPY</th>
              <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>vs QQQ</th>
              {hasHsi && <th style={{ padding: '6px 14px', textAlign: 'right', color: '#64748b', fontWeight: 600, fontSize: 11 }}>vs HSI</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.label} style={{ borderBottom: '1px solid #0f172a' }}>
                <td style={{ padding: '8px 16px', color: '#e2e8f0', fontWeight: 600, fontSize: 13 }}>{r.label}</td>
                <td style={cellStyle(r.portfolio, true)}>{r.portfolio != null ? (r.portfolio >= 0 ? '+' : '') + r.portfolio.toFixed(2) + '%' : '—'}</td>
                <td style={cellStyle(r.spy)}>{r.spy != null ? (r.spy >= 0 ? '+' : '') + r.spy.toFixed(2) + '%' : '—'}</td>
                <td style={cellStyle(r.qqq)}>{r.qqq != null ? (r.qqq >= 0 ? '+' : '') + r.qqq.toFixed(2) + '%' : '—'}</td>
                {hasHsi && <td style={cellStyle(r.hsi)}>{r.hsi != null ? (r.hsi >= 0 ? '+' : '') + r.hsi.toFixed(2) + '%' : '—'}</td>}
                <td style={outStyle(r.portfolio, r.spy)}>{r.portfolio != null && r.spy != null ? (r.portfolio - r.spy >= 0 ? '+' : '') + (r.portfolio - r.spy).toFixed(2) + '%' : '—'}</td>
                <td style={outStyle(r.portfolio, r.qqq)}>{r.portfolio != null && r.qqq != null ? (r.portfolio - r.qqq >= 0 ? '+' : '') + (r.portfolio - r.qqq).toFixed(2) + '%' : '—'}</td>
                {hasHsi && <td style={outStyle(r.portfolio, r.hsi)}>{r.portfolio != null && r.hsi != null ? (r.portfolio - r.hsi >= 0 ? '+' : '') + (r.portfolio - r.hsi).toFixed(2) + '%' : '—'}</td>}
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
      const hsiStart = data.find(d => d.hsi_close != null)?.hsi_close;
      if (hsiStart) {
        traces.push({
          x: dates,
          y: data.map(d => d.hsi_close != null ? initialCapital * (d.hsi_close / hsiStart) : null),
          name: 'HSI',
          type: 'scatter', mode: 'lines',
          line: { color: '#fb923c', width: 1.5, dash: 'dot' },
          hovertemplate: '%{x}: $%{y:,.0f}<extra>HSI</extra>',
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

const GATE_LABELS: Record<string, string> = {
  regime_bear:       'Bear Market — entries suspended',
  regime_risk_off:   'Risk-Off Regime — entries suspended',
  regime_suspension: 'Sustained Stress — entries suspended',
  entry_throttle:    'Regime throttle — 1 entry/day limit',
  heat_brake:        'Heat Brake — too many recent stops',
  index_trend:       'Index down >1.5% today',
  market_cluster_cap:'Market position cap reached',
  drawdown:          'Portfolio drawdown limit hit',
  daily_loss:        'Daily loss limit hit',
  weekly_loss:       'Weekly loss limit hit',
  weekly_gain_lock:  'Weekly gain lock — protecting profits',
  consecutive_losses:'Consecutive-loss limit hit',
  daily_entry_cap:   'Daily entry cap reached',
};

function PortfolioCard({
  portfolio, selected, isBestSharpe, onSelect, onToggleActive,
}: {
  portfolio: PaperPortfolioListItem;
  selected: boolean;
  isBestSharpe: boolean;
  onSelect: () => void;
  onToggleActive: (active: boolean) => void;
}) {
  const retColor = portfolio.total_return_pct >= 0 ? '#22c55e' : '#ef4444';
  const styleColor = STYLE_COLORS[portfolio.trading_style] ?? '#94a3b8';
  const isActive = portfolio.is_active ?? true;
  const state = !isActive ? 'Disabled' : portfolio.is_running ? 'Running' : portfolio.is_paused ? 'Paused' : 'Stopped';
  const stateColor = !isActive ? '#475569' : portfolio.is_running ? '#22c55e' : portfolio.is_paused ? '#f59e0b' : '#ef4444';

  return (
    <div
      onClick={onSelect}
      style={{
        background: selected ? '#1a2744' : '#1e293b',
        border: `2px solid ${selected ? '#3b82f6' : '#334155'}`,
        borderRadius: 12, padding: '14px 18px', cursor: 'pointer',
        minWidth: 200, flex: '1 1 200px', maxWidth: 280,
        transition: 'border-color 0.15s, background 0.15s', position: 'relative',
        opacity: isActive ? 1 : 0.55,
      }}
    >
      {isBestSharpe && isActive && (
        <span title="Best Sharpe Ratio" style={{
          position: 'absolute', top: 8, right: 8, fontSize: 13, color: '#f59e0b',
        }}>★</span>
      )}
      {/* On/Off toggle — stop propagation so clicking it doesn't select the portfolio */}
      <button
        title={isActive ? 'Disable portfolio (pause paper trading)' : 'Enable portfolio (resume paper trading)'}
        onClick={e => { e.stopPropagation(); onToggleActive(!isActive); }}
        style={{
          position: 'absolute', top: 8, right: isBestSharpe && isActive ? 28 : 8,
          padding: '2px 7px', fontSize: 10, fontWeight: 700, borderRadius: 4, border: 'none',
          cursor: 'pointer', background: isActive ? 'rgba(34,197,94,0.15)' : 'rgba(71,85,105,0.3)',
          color: isActive ? '#22c55e' : '#64748b',
        }}
      >{isActive ? 'ON' : 'OFF'}</button>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 10, fontWeight: 700, color: styleColor,
          background: styleColor + '22', border: `1px solid ${styleColor}44`,
          borderRadius: 4, padding: '2px 7px',
        }}>{portfolio.trading_style}</span>
        <span style={{
          fontSize: 10, fontWeight: 700,
          color: portfolio.market === 'HK' ? '#fb923c' : '#22d3ee',
          background: portfolio.market === 'HK' ? 'rgba(251,146,60,0.12)' : 'rgba(34,211,238,0.1)',
          border: `1px solid ${portfolio.market === 'HK' ? 'rgba(251,146,60,0.3)' : 'rgba(34,211,238,0.25)'}`,
          borderRadius: 4, padding: '2px 7px',
        }}>{portfolio.market ?? 'US'}</span>
        <span style={{ fontSize: 10, color: stateColor, fontWeight: 600 }}>● {state}</span>
      </div>
      {portfolio.entry_gate_block && (
        <div title={portfolio.entry_gate_block.reason} style={{
          marginBottom: 6, padding: '3px 7px', borderRadius: 5,
          background: 'rgba(251,146,60,0.1)', border: '1px solid rgba(251,146,60,0.3)',
          fontSize: 10, fontWeight: 600, color: '#fb923c',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          cursor: 'help',
        }}>
          ⊘ {GATE_LABELS[portfolio.entry_gate_block.gate] ?? portfolio.entry_gate_block.gate}
        </div>
      )}
      {/* T232-WHYNOTRADE: no portfolio-level gate fired, but every candidate individually
          failed its own check (K-Score, volume, TA score, cooldown, ...) — surfaces the
          top reasons instead of requiring a container-log dig, per user request. */}
      {!portfolio.entry_gate_block && portfolio.no_entry_summary && portfolio.no_entry_summary.top_reasons.length > 0 && (
        <div
          title={portfolio.no_entry_summary.top_reasons.map(r => `${r.label}: ${r.count}`).join(' · ')}
          style={{
            marginBottom: 6, padding: '3px 7px', borderRadius: 5,
            background: 'rgba(148,163,184,0.08)', border: '1px solid rgba(148,163,184,0.25)',
            fontSize: 10, fontWeight: 600, color: '#94a3b8',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            cursor: 'help',
          }}
        >
          ⓘ Not trading: {portfolio.no_entry_summary.top_reasons[0].label}
          {portfolio.no_entry_summary.top_reasons.length > 1 ? ` +${portfolio.no_entry_summary.top_reasons.length - 1} more` : ''}
        </div>
      )}
      <div style={{ fontSize: 14, fontWeight: 700, color: '#f1f5f9', marginBottom: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {portfolio.name}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, color: retColor }}>
        {portfolio.total_return_pct >= 0 ? '+' : ''}{portfolio.total_return_pct.toFixed(1)}%
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
        {fmtCurrency(portfolio.current_equity, portfolio.market)}
      </div>
      <div style={{ display: 'flex', gap: 12, marginTop: 10, fontSize: 11, color: '#94a3b8' }}>
        <span>Win {portfolio.win_rate_pct != null ? `${portfolio.win_rate_pct.toFixed(0)}%` : '—'}</span>
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

// ── Monte Carlo simulation — bootstrap paths from historical daily returns ──────

function MonteCarloSection({ data }: { data: PaperEquityPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);

  const stats = useMemo(() => {
    if (data.length < 5) return null;
    const equities = data.map(d => d.equity);
    // daily log-returns
    const returns: number[] = [];
    for (let i = 1; i < equities.length; i++) {
      if (equities[i - 1] > 0) returns.push(Math.log(equities[i] / equities[i - 1]));
    }
    if (returns.length < 3) return null;

    const N_PATHS = 1000;
    const HORIZON = 252; // trading days
    const startVal = equities[equities.length - 1];

    // seeded LCG so chart is stable on re-renders
    let seed = 0x12345678;
    const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 0xFFFFFFFF; };
    const randNorm = () => {
      // Box-Muller
      const u = Math.max(rand(), 1e-12);
      const v = rand();
      return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    };

    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / returns.length;
    const std = Math.sqrt(variance);

    // Build paths
    const paths: number[][] = [];
    for (let p = 0; p < N_PATHS; p++) {
      let val = startVal;
      const path: number[] = [val];
      for (let d = 0; d < HORIZON; d++) {
        val *= Math.exp(mean + std * randNorm());
        path.push(val);
      }
      paths.push(path);
    }

    // Compute percentile bands at each step
    const PCTS = [5, 25, 50, 75, 95];
    const bands: Record<number, number[]> = {};
    for (const pct of PCTS) bands[pct] = [];
    for (let d = 0; d <= HORIZON; d++) {
      const vals = paths.map(p => p[d]).sort((a, b) => a - b);
      for (const pct of PCTS) {
        const idx = Math.floor((pct / 100) * (vals.length - 1));
        bands[pct].push(vals[idx]);
      }
    }

    const terminalVals = paths.map(p => p[HORIZON]);
    const probPositive = terminalVals.filter(v => v > startVal).length / N_PATHS;
    const medianTerminal = bands[50][HORIZON];
    const p10Terminal = bands[5][HORIZON];
    const p90Terminal = bands[95][HORIZON];

    return { bands, HORIZON, startVal, probPositive, medianTerminal, p10Terminal, p90Terminal, N_PATHS };
  }, [data]);

  useEffect(() => {
    if (!ref.current || !stats) return;
    let cancelled = false;
    const { bands, HORIZON, startVal, probPositive, medianTerminal } = stats;

    import('plotly.js-dist-min').then((Plotly: any) => {
      if (cancelled || !ref.current) return;
      const xs = Array.from({ length: HORIZON + 1 }, (_, i) => i);

      const traces: any[] = [
        // 5-95 band (fill)
        { x: xs, y: bands[5], name: 'P5', type: 'scatter', mode: 'lines', line: { width: 0, color: 'transparent' }, showlegend: false, hoverinfo: 'skip' },
        { x: xs, y: bands[95], name: 'P5–P95', type: 'scatter', mode: 'lines', fill: 'tonexty',
          fillcolor: 'rgba(99,102,241,0.08)', line: { width: 0, color: 'transparent' }, showlegend: true,
          hovertemplate: 'P95: $%{y:,.0f}<extra></extra>' },
        // 25-75 band (fill)
        { x: xs, y: bands[25], name: 'P25', type: 'scatter', mode: 'lines', line: { width: 0, color: 'transparent' }, showlegend: false, hoverinfo: 'skip' },
        { x: xs, y: bands[75], name: 'P25–P75', type: 'scatter', mode: 'lines', fill: 'tonexty',
          fillcolor: 'rgba(99,102,241,0.15)', line: { width: 0, color: 'transparent' }, showlegend: true,
          hovertemplate: 'P75: $%{y:,.0f}<extra></extra>' },
        // median
        { x: xs, y: bands[50], name: 'Median (P50)', type: 'scatter', mode: 'lines',
          line: { color: '#818cf8', width: 2 }, hovertemplate: 'Median: $%{y:,.0f}<extra></extra>' },
        // today line
        { x: [0], y: [startVal], name: 'Today', type: 'scatter', mode: 'markers',
          marker: { color: '#22c55e', size: 8 }, hovertemplate: `Today: $${startVal.toLocaleString(undefined, { maximumFractionDigits: 0 })}<extra></extra>` },
      ];

      const layout = {
        paper_bgcolor: '#0f172a', plot_bgcolor: '#0f172a',
        margin: { t: 10, b: 40, l: 60, r: 10 },
        height: 220,
        font: { family: 'monospace', size: 11, color: '#94a3b8' },
        xaxis: { title: 'Trading Days', color: '#475569', gridcolor: '#1e293b', zeroline: false },
        yaxis: { tickprefix: '$', color: '#475569', gridcolor: '#1e293b', zeroline: false },
        legend: { orientation: 'h', x: 0, y: 1.08, font: { size: 11 } },
        hovermode: 'x unified',
        annotations: [{
          x: HORIZON, y: medianTerminal,
          text: `Median: $${medianTerminal.toLocaleString(undefined, { maximumFractionDigits: 0 })}`,
          showarrow: false, font: { color: '#818cf8', size: 10 }, xanchor: 'right',
        }],
      };

      Plotly.react(ref.current, traces, layout, { displayModeBar: false, responsive: true });
    });
    return () => { cancelled = true; };
  }, [stats]);

  if (!stats) {
    return (
      <div style={{ color: '#64748b', fontSize: 13, textAlign: 'center', padding: '20px 0' }}>
        Not enough equity data for Monte Carlo (need at least 5 data points).
      </div>
    );
  }

  const { probPositive, medianTerminal, p10Terminal, p90Terminal, N_PATHS, startVal } = stats;

  return (
    <div style={{ background: '#0f172a', borderRadius: 10, border: '1px solid #1e293b', padding: '16px 12px', marginTop: 20 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0', marginBottom: 4 }}>Monte Carlo Projection — 1 Year ({HORIZON_LABEL})</div>
      <div style={{ fontSize: 11, color: '#475569', marginBottom: 12 }}>
        {N_PATHS.toLocaleString()} bootstrap paths from historical daily return distribution
      </div>
      <div ref={ref} />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
        {[
          { label: 'P(positive)', value: `${(probPositive * 100).toFixed(1)}%`, color: probPositive >= 0.6 ? '#4ade80' : probPositive >= 0.4 ? '#facc15' : '#f87171' },
          { label: 'P10 terminal', value: `$${p10Terminal.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: p10Terminal >= startVal ? '#4ade80' : '#f87171' },
          { label: 'Median terminal', value: `$${medianTerminal.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: medianTerminal >= startVal ? '#4ade80' : '#f87171' },
          { label: 'P90 terminal', value: `$${p90Terminal.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: '#a5b4fc' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ background: '#0a1628', border: '1px solid #1e293b', borderRadius: 6, padding: '8px 12px', minWidth: 110 }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 10, color: '#334155', marginTop: 8 }}>
        Simulated using bootstrap resampling of observed daily returns. Not financial advice.
      </div>
    </div>
  );
}

const HORIZON_LABEL = '252 trading days';

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
  const [market, setMarket] = useState('US');
  const [capital, setCapital] = useState('100000');
  const [brokerId, setBrokerId] = useState<string>('');
  const [brokers, setBrokers] = useState<import('@/lib/api').BrokerConnection[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  // Load authorized US broker connections for linking
  useEffect(() => {
    api.brokerList()
      .then(list => setBrokers(list.filter(b => b.is_authorized && b.broker_type !== 'fidelity_manual')))
      .catch(() => {});
  }, []);

  async function create() {
    const cap = parseFloat(capital);
    if (!name.trim()) { setErr('Name is required'); return; }
    if (isNaN(cap) || cap <= 0) { setErr('Capital must be > 0'); return; }
    setSaving(true); setErr('');
    try {
      const body: Parameters<typeof api.paperCreate>[0] = {
        name: name.trim(), trading_style: style, market, initial_capital: cap,
      };
      if (brokerId) (body as any).broker_connection_id = parseInt(brokerId);
      await api.paperCreate(body);
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

  const usBrokers = brokers.filter(b => !b.broker_type.includes('hk'));

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        background: '#1e293b', borderRadius: 12, padding: 28, width: 380,
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
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 5 }}>Market</div>
            <select value={market} onChange={e => { setMarket(e.target.value); if (e.target.value === 'HK') setBrokerId(''); }} style={{ ...inputStyle, cursor: 'pointer' }}>
              <option value="US">US — NYSE / NASDAQ</option>
              <option value="HK">HK — Hong Kong Exchange</option>
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 5 }}>Initial Capital ($)</div>
            <input value={capital} onChange={e => setCapital(e.target.value)} type="number" min="1"
              style={inputStyle} />
          </div>

          {/* Broker link — only shown for US (HK stocks not supported on US brokers) */}
          {market === 'US' && (
            <div>
              <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 5 }}>
                Broker Account <span style={{ color: '#475569' }}>(optional — routes real orders)</span>
              </div>
              {usBrokers.length > 0 ? (
                <select value={brokerId} onChange={e => setBrokerId(e.target.value)} style={{ ...inputStyle, cursor: 'pointer' }}>
                  <option value="">No broker — simulation only</option>
                  {usBrokers.map(b => (
                    <option key={b.id} value={b.id}>
                      {b.name} ({b.broker_type === 'etrade_sandbox' ? 'E*Trade Sandbox' : 'E*Trade Live'})
                    </option>
                  ))}
                </select>
              ) : (
                <div style={{ fontSize: 12, color: '#475569', padding: '8px 10px', background: '#0f172a', borderRadius: 6, border: '1px solid #1e293b' }}>
                  No authorized broker —{' '}
                  <a href="/settings" style={{ color: '#38bdf8', textDecoration: 'none' }}>add one in Settings</a>
                </div>
              )}
              {brokerId && (
                <div style={{ fontSize: 11, color: '#22c55e', marginTop: 5 }}>
                  Real orders will be submitted to E*Trade on each engine cycle.
                </div>
              )}
            </div>
          )}
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
  const [overrideMsg, setOverrideMsg] = useState('');
  const [overrideBusy, setOverrideBusy] = useState(false);

  const overrideUntil = config.regime_risk_off_override_until ? new Date(config.regime_risk_off_override_until) : null;
  const overrideActive = !!overrideUntil && overrideUntil.getTime() > Date.now();

  async function setRiskOffOverride(hours: number) {
    setOverrideBusy(true); setOverrideMsg('');
    try {
      await api.paperSetRiskOffOverride(hours, portfolioId);
      setOverrideMsg(`Override active for ${hours}h`);
      onSave();
    } catch { setOverrideMsg('Failed to set override'); }
    finally { setOverrideBusy(false); }
  }

  async function clearRiskOffOverride() {
    setOverrideBusy(true); setOverrideMsg('');
    try {
      await api.paperClearRiskOffOverride(portfolioId);
      setOverrideMsg('Override cleared');
      onSave();
    } catch { setOverrideMsg('Failed to clear override'); }
    finally { setOverrideBusy(false); }
  }

  function field(key: keyof PaperPortfolioConfig, label: string, step = 0.01, placeholder?: string, min?: number) {
    const cur = draft[key] ?? config[key];
    const isDefined = cur !== undefined && cur !== null;
    return (
      <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 11, color: '#94a3b8' }}>{label}</span>
        <input
          type="number" step={step} min={min}
          value={isDefined ? String(cur) : ''}
          placeholder={placeholder ?? '—'}
          onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) setDraft(d => ({ ...d, [key]: v })); }}
          style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 5, color: isDefined ? '#f1f5f9' : '#64748b', padding: '5px 8px', fontSize: 13, width: 120 }}
        />
      </label>
    );
  }

  async function save() {
    setSaving(true); setMsg('');
    try {
      const r = await api.paperConfigure(draft, portfolioId);
      // T232-CONFIGGAP: the backend used to silently drop unrecognized keys with no
      // indication to the user that nothing happened — surface it if it ever recurs.
      setMsg(r.ignored_keys?.length ? `Saved (ignored unknown: ${r.ignored_keys.join(', ')})` : 'Saved');
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

  function section(label: string) {
    return (
      <div style={{ width: '100%', borderTop: '1px solid #334155', paddingTop: 12, marginTop: 4 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1 }}>{label}</span>
      </div>
    );
  }

  return (
    <div id="config" style={{ background: '#1e293b', borderRadius: 10, padding: 20, border: '1px solid #334155' }}>
      <div style={{ fontWeight: 600, marginBottom: 16, color: '#f1f5f9' }}>Portfolio Config (admin)</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        {section('Position Limits')}
        {field('max_positions', 'Max Positions', 1, undefined, 1)}
        {field('max_market_positions', 'Max Market Pos', 1, 'default 4', 1)}
        {field('max_sector_positions', 'Max Sector Pos', 1, 'default 3', 1)}
        {field('max_entries_per_day', 'Max Entries/Day', 1, 'default 3', 1)}
        {field('max_open_exposure_pct', 'Max Open Exposure %', 0.01, 'default 0.40')}
        {field('equity_floor_pct', 'Equity Floor %', 0.01, 'default 0.80')}

        {section('Entry Quality')}
        {field('min_confidence', 'Min Confidence', 1)}
        {field('min_kscore', 'Min K-Score', 1)}
        {field('min_rr_ratio', 'Min R:R', 0.1)}
        {field('min_entry_score', 'Min Entry Score', 1)}
        {field('min_ta_score', 'Min TA Score', 0.1, 'default 0')}
        {field('min_volume_z', 'Min Volume Z', 0.1, 'default -1.5')}
        {field('max_entry_gap_pct', 'Max Entry Gap %', 0.01, 'default 0.04')}

        {section('Risk / Sizing')}
        {field('risk_per_trade_pct', 'Risk/Trade %')}
        {field('max_position_pct', 'Max Position %')}
        {field('max_sector_pct', 'Max Sector %')}

        {section('Exit Management')}
        {field('max_hold_days', 'Max Hold Days', 1, undefined, 1)}
        {field('hold_stall_days', 'Stall Timeout Days', 1, 'default 30', 1)}
        {field('wait_exit_days', 'Wait Exit Days', 1, undefined, 1)}
        {field('trail_atr_mult', 'Trail ATR ×')}
        {field('trail_trigger_pct', 'Trail Trigger %')}
        {field('breakeven_trigger_pct', 'Breakeven %')}
        {field('partial_tp_pct', 'Partial TP1 %')}
        {field('partial_tp2_pct', 'Partial TP2 %', 0.01, 'default 0.12')}
        {field('stop_cooldown_hours', 'Stop Cooldown (hrs)', 1)}

        {section('Circuit Breakers')}
        {field('max_daily_loss_pct', 'Max Daily Loss %', 0.01, 'default 0.04')}
        {field('max_weekly_loss_pct', 'Max Weekly Loss %', 0.01, 'default 0.08')}
        {field('max_portfolio_drawdown_pct', 'Max Drawdown %', 0.01, 'default 0.20')}
        {field('max_consecutive_losses', 'Max Consec. Losses', 1, 'default 3', 1)}
      </div>

      {section('Regime Gate Override')}
      <div style={{ marginTop: 10 }}>
        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>
          When the regime is risk_off, new entries are blocked entirely (0% win rate on risk_off entries historically).
          Use this only when you have a deliberate reason to override — it self-expires, no need to remember to turn it back off.
        </div>
        {overrideActive ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, background: '#422006', border: '1px solid #f59e0b', borderRadius: 6, padding: '8px 12px' }}>
            <span style={{ color: '#fbbf24', fontSize: 13, fontWeight: 600 }}>
              ⚠ Override active until {overrideUntil!.toLocaleString()}
            </span>
            <button
              onClick={clearRiskOffOverride} disabled={overrideBusy}
              style={{ background: 'transparent', color: '#f87171', border: '1px solid #f87171', borderRadius: 5, padding: '4px 10px', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
            >Cancel Override</button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button
              onClick={() => setRiskOffOverride(4)} disabled={overrideBusy}
              style={{ background: '#1e293b', color: '#fbbf24', border: '1px solid #f59e0b', borderRadius: 6, padding: '7px 14px', cursor: 'pointer', fontWeight: 600, fontSize: 12 }}
            >Allow trading for 4 hours</button>
            <button
              onClick={() => setRiskOffOverride(24)} disabled={overrideBusy}
              style={{ background: '#1e293b', color: '#fbbf24', border: '1px solid #f59e0b', borderRadius: 6, padding: '7px 14px', cursor: 'pointer', fontWeight: 600, fontSize: 12 }}
            >Allow trading for 1 day</button>
          </div>
        )}
        {overrideMsg && <span style={{ color: overrideMsg.startsWith('Failed') ? '#ef4444' : '#22c55e', fontSize: 12, marginLeft: 10 }}>{overrideMsg}</span>}
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

// ── AUD19-UX1: ML Intelligence Status panel (RL agent + entry calibration) ─────

function MLIntelligencePanel() {
  const { data: rl, mutate: mutateRl } = useSWR('rl-status', () => api.rlStatus(), { revalidateOnFocus: false });
  const { data: ef, mutate: mutateEf } = useSWR('entry-factors', () => api.entryFactors(), { revalidateOnFocus: false });
  const [trainMsg, setTrainMsg] = useState('');
  const [calMsg, setCalMsg] = useState('');

  async function trainRl() {
    setTrainMsg('Starting...');
    try { await api.rlTrain(); setTrainMsg('Training started in background'); setTimeout(() => mutateRl(), 5000); }
    catch { setTrainMsg('Failed'); }
  }
  async function calibrate() {
    setCalMsg('Starting...');
    try { await api.calibrateEntry(); setCalMsg('Calibration started in background'); setTimeout(() => mutateEf(), 5000); }
    catch { setCalMsg('Failed'); }
  }

  const cardStyle: React.CSSProperties = { background: '#0f172a', borderRadius: 8, padding: '12px 16px', border: '1px solid #334155', flex: 1, minWidth: 200 };
  const labelStyle: React.CSSProperties = { fontSize: 11, color: '#64748b', marginBottom: 2 };
  const valStyle: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#f1f5f9' };

  return (
    <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, border: '1px solid #334155' }}>
      <div style={{ fontWeight: 600, marginBottom: 4, color: '#f1f5f9' }}>ML Intelligence Status (AL-1 · PT-3)</div>
      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 16 }}>
        RL policy uses Ridge regression on closed trade history to adjust entry scores (±1). Entry calibration fits logistic regression on win/loss outcomes to compute calibrated win-probability.
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        {/* RL Policy */}
        <div style={cardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: '#818cf8' }}>RL Policy (AL-1)</span>
            <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4,
              background: (rl?.status === 'trained' || rl?.status === 'ready') ? 'rgba(34,197,94,0.15)' : 'rgba(100,116,139,0.15)',
              color: (rl?.status === 'trained' || rl?.status === 'ready') ? '#4ade80' : '#64748b' }}>
              {rl?.status ?? '...'}
            </span>
          </div>
          {(rl?.status === 'trained' || rl?.status === 'ready') ? (
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.8 }}>
              <div><span style={labelStyle}>Trades used:</span> <span style={valStyle}>{rl.n_trades}</span></div>
              <div><span style={labelStyle}>Win rate:</span> <span style={{ ...valStyle, color: (rl.win_rate ?? 0) >= 0.5 ? '#4ade80' : '#f87171' }}>{rl.win_rate != null ? `${(rl.win_rate * 100).toFixed(1)}%` : '—'}</span></div>
              <div><span style={labelStyle}>BUY threshold:</span> <span style={valStyle}>{rl.threshold != null ? `Q ≥ ${(rl.threshold * 100).toFixed(0)}th %ile` : '—'}</span></div>
              {rl.trained_at && <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>Updated {new Date(rl.trained_at).toLocaleDateString()}</div>}
            </div>
          ) : (
            <div style={{ fontSize: 11, color: '#475569' }}>Not trained — runs Sunday after close when ≥50 closed trades exist.</div>
          )}
        </div>

        {/* Entry Calibration */}
        <div style={cardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: '#f59e0b' }}>Entry Calibration (PT-3)</span>
            <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4,
              background: ef?.status === 'calibrated' ? 'rgba(34,197,94,0.15)' : 'rgba(100,116,139,0.15)',
              color: ef?.status === 'calibrated' ? '#4ade80' : '#64748b' }}>
              {ef?.status ?? '...'}
            </span>
          </div>
          {ef?.status === 'calibrated' ? (
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.8 }}>
              <div><span style={labelStyle}>Trades used:</span> <span style={valStyle}>{ef.n_trades}</span></div>
              <div><span style={labelStyle}>Win rate:</span> <span style={{ ...valStyle, color: (ef.win_rate ?? 0) >= 0.5 ? '#4ade80' : '#f87171' }}>{ef.win_rate != null ? `${(ef.win_rate * 100).toFixed(1)}%` : '—'}</span></div>
              <div><span style={labelStyle}>Win-prob threshold:</span> <span style={valStyle}>{ef.threshold != null ? `${(ef.threshold * 100).toFixed(0)}%` : '—'}</span></div>
              <div style={{ marginTop: 6, fontSize: 11, color: '#64748b' }}>Weights:
                R:R {ef.w_rr?.toFixed(3) ?? '—'} · Conf {ef.w_confidence?.toFixed(3) ?? '—'} · Score {ef.w_score?.toFixed(3) ?? '—'} · KScore {ef.w_kscore?.toFixed(3) ?? '—'}
              </div>
              {ef.calibrated_at && <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>Updated {new Date(ef.calibrated_at).toLocaleDateString()}</div>}
            </div>
          ) : (
            <div style={{ fontSize: 11, color: '#475569' }}>Not calibrated — runs Sunday after close when ≥100 closed trades exist.</div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button onClick={trainRl}
          style={{ background: '#4f46e5', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontWeight: 600, fontSize: 12 }}>
          Train RL Now
        </button>
        <button onClick={calibrate}
          style={{ background: '#b45309', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontWeight: 600, fontSize: 12 }}>
          Calibrate Entry Now
        </button>
        <button onClick={() => { mutateRl(); mutateEf(); }}
          style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '6px 12px', cursor: 'pointer', fontSize: 12 }}>
          Refresh
        </button>
        {trainMsg && <span style={{ fontSize: 11, color: '#f59e0b' }}>{trainMsg}</span>}
        {calMsg && <span style={{ fontSize: 11, color: '#f59e0b' }}>{calMsg}</span>}
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

const TABS = ['Positions', 'Decisions', 'Journal', 'Closed Trades', 'Equity Curve', 'Attribution', 'Risk', 'DE Audit'] as const;
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
  const [expandedPositionId, setExpandedPositionId] = useState<number | null>(null);
  // Broker assignment per portfolio
  const [portfolioBroker, setPortfolioBroker] = useState<{ broker_connection_id: number | null; broker: import('@/lib/api').BrokerConnection | null } | null>(null);
  const [brokerConnections, setBrokerConnections] = useState<import('@/lib/api').BrokerConnection[]>([]);
  // ETrade re-auth flow state
  const [reAuthUrl, setReAuthUrl] = useState<string | null>(null);
  const [reAuthPin, setReAuthPin] = useState('');
  const [reAuthLoading, setReAuthLoading] = useState(false);
  const [reAuthError, setReAuthError] = useState<string | null>(null);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setIsAdmin(session.role === 'admin');
    setAuthed(true);
  }, [router]);

  // Reset pagination when switching tabs so stale page numbers don't carry over.
  useEffect(() => {
    setTradesPage(1);
    setDecPage(1);
  }, [tab]);

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
  const { data: positions, mutate: mutatePositions } = useSWR(
    authed && (tab === 'Positions' || tab === 'Risk') && selectedPortfolioId != null ? ['paper-positions', selectedPortfolioId] : null,
    () => api.paperPositions(selectedPortfolioId), { refreshInterval: 60_000 }
  );
  const [exitConfirm, setExitConfirm] = useState<{ tradeId: number; symbol: string } | null>(null);
  const [exitBusy, setExitBusy] = useState(false);
  const [exitMsg, setExitMsg] = useState('');

  // INT-9: research verdicts for open positions
  const [posResearchMap, setPosResearchMap] = useState<Record<string, ResearchSummary>>({});
  const posSymbols = useMemo(() => positions?.map(p => p.symbol) ?? [], [positions]);
  const posSymbolKey = useMemo(() => posSymbols.join(','), [posSymbols]);
  useEffect(() => {
    if (!posSymbols.length) { setPosResearchMap({}); return; }
    api.getResearchBatch(posSymbols).then(r => setPosResearchMap(r ?? {})).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [posSymbolKey]);
  const { data: trades } = useSWR(
    authed && tab === 'Closed Trades' && selectedPortfolioId != null ? ['paper-trades', tradesPage, selectedPortfolioId] : null,
    () => api.paperTrades({ page: tradesPage, limit: 50, portfolioId: selectedPortfolioId })
  );
  const { data: curve } = useSWR(
    authed && tab === 'Equity Curve' && selectedPortfolioId != null ? ['paper-curve', selectedPortfolioId] : null,
    () => api.paperEquityCurve(180, selectedPortfolioId)
  );
  const { data: decisions } = useSWR(
    authed && (tab === 'Decisions' || tab === 'Journal') && selectedPortfolioId != null ? ['paper-decisions', decPage, selectedPortfolioId] : null,
    () => api.paperDecisions({ page: decPage, limit: 50, days_back: 180, portfolioId: selectedPortfolioId })
  );
  const { data: attribution } = useSWR(
    authed && tab === 'Attribution' && selectedPortfolioId != null ? ['paper-attribution', selectedPortfolioId] : null,
    () => api.paperAttribution(selectedPortfolioId), { revalidateOnFocus: false }
  );
  const { data: deAudit } = useSWR<DeDivergenceResponse>(
    authed && tab === 'DE Audit' ? 'de-divergences' : null,
    () => api.deDivergences(200), { revalidateOnFocus: false, refreshInterval: 60_000 }
  );

  if (!authed) return null;

  if (!summary && summaryError) {
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
  const selectedMarket = portfolioList.find(p => p.id === selectedPortfolioId)?.market ?? 'US';
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
            <Link href="/paper-gates" style={{ fontSize: 12, color: '#64748b', textDecoration: 'none' }}>Entry Gates</Link>
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
                    ? <span style={{ marginLeft: 6, color: '#4ade80' }}>✓ authorized</span>
                    : <span style={{ marginLeft: 6, color: '#fbbf24' }}>⚠ tokens expired</span>}
                </span>
                <button onClick={() => { if (selectedPortfolioId) api.brokerAssignPortfolio(selectedPortfolioId, null).then(() => api.brokerGetPortfolioBroker(selectedPortfolioId).then(setPortfolioBroker)); }}
                  style={{ fontSize: 11, color: '#94a3b8', background: 'transparent', border: '1px solid #334155', borderRadius: 4, padding: '3px 8px', cursor: 'pointer' }}>
                  Unlink
                </button>
                {!portfolioBroker.broker.is_authorized && (
                  <button
                    disabled={reAuthLoading}
                    onClick={async () => {
                      setReAuthLoading(true); setReAuthError(null); setReAuthUrl(null); setReAuthPin('');
                      try {
                        const res = await api.brokerOAuthStart(portfolioBroker.broker_connection_id!);
                        setReAuthUrl(res.authorize_url);
                      } catch (e: any) { setReAuthError(e.message || 'Failed to start re-auth'); }
                      finally { setReAuthLoading(false); }
                    }}
                    style={{ fontSize: 11, color: '#f97316', background: 'rgba(249,115,22,0.1)', border: '1px solid rgba(249,115,22,0.3)', borderRadius: 4, padding: '3px 10px', cursor: 'pointer', fontWeight: 600 }}>
                    {reAuthLoading ? 'Starting…' : '⟳ Re-authorize'}
                  </button>
                )}
              {/* Re-auth flow panel */}
              {reAuthUrl && (
                <div style={{ marginTop: 10, padding: '12px 14px', background: 'rgba(249,115,22,0.08)', border: '1px solid rgba(249,115,22,0.25)', borderRadius: 8 }}>
                  <div style={{ fontSize: 12, color: '#fed7aa', fontWeight: 600, marginBottom: 6 }}>Step 1 — Authorize in E*Trade</div>
                  <a href={reAuthUrl} target="_blank" rel="noreferrer"
                    style={{ display: 'inline-block', fontSize: 12, background: '#f97316', color: '#fff', padding: '6px 14px', borderRadius: 6, textDecoration: 'none', fontWeight: 600 }}>
                    Open E*Trade Authorization →
                  </a>
                  <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 8, marginBottom: 8 }}>
                    After authorizing you will see a PIN. Enter it below:
                  </div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <input
                      value={reAuthPin}
                      onChange={e => setReAuthPin(e.target.value)}
                      placeholder="Enter PIN…"
                      style={{ fontSize: 13, background: '#0f172a', border: '1px solid #334155', borderRadius: 5, padding: '5px 10px', color: '#f1f5f9', width: 130 }}
                    />
                    <button
                      disabled={!reAuthPin.trim() || reAuthLoading}
                      onClick={async () => {
                        setReAuthLoading(true); setReAuthError(null);
                        try {
                          await api.brokerOAuthComplete(portfolioBroker.broker_connection_id!, reAuthPin.trim());
                          setReAuthUrl(null); setReAuthPin('');
                          api.brokerGetPortfolioBroker(selectedPortfolioId!).then(setPortfolioBroker);
                        } catch (e: any) { setReAuthError(e.message || 'Invalid PIN — try again'); }
                        finally { setReAuthLoading(false); }
                      }}
                      style={{ fontSize: 12, background: '#22c55e', color: '#fff', border: 'none', borderRadius: 5, padding: '6px 14px', cursor: 'pointer', fontWeight: 600 }}>
                      {reAuthLoading ? 'Verifying…' : 'Submit PIN'}
                    </button>
                  </div>
                  {reAuthError && <div style={{ fontSize: 12, color: '#f87171', marginTop: 6 }}>{reAuthError}</div>}
                </div>
              )}
            </>) : (
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

        {/* Portfolio selector — always visible */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 11, color: '#64748b', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 10 }}>
            Portfolios
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: multiPortfolio ? 16 : 0 }}>
            {portfolioList.map(p => (
              <PortfolioCard
                key={p.id}
                portfolio={p}
                selected={p.id === selectedPortfolioId}
                isBestSharpe={p.id === bestSharpeId}
                onSelect={() => { setSelectedPortfolioId(p.id); setTradesPage(1); setDecPage(1); }}
                onToggleActive={async (active) => {
                  await api.paperToggleActive(p.id, active);
                  mutateList();
                }}
              />
            ))}
          </div>
          {multiPortfolio && compareData && compareData.some(d => d.curve.length > 0) && (
            <div style={{ background: '#0f172a', borderRadius: 10, border: '1px solid #1e293b', padding: '14px 12px' }}>
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>Normalized return % — all portfolios vs SPY</div>
              <CompareEquityChart data={compareData} />
            </div>
          )}
          {multiPortfolio && (
            <div style={{ fontSize: 11, color: '#64748b', marginTop: 6 }}>
              Click a portfolio card to switch view
            </div>
          )}
        </div>

        {showCreateModal && (
          <CreatePortfolioModal
            onClose={() => setShowCreateModal(false)}
            onCreated={() => { mutateList(); }}
          />
        )}

        {/* Manual exit confirmation modal */}
        {exitConfirm && (
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
            <div style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 12, padding: 28, width: 340 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#f1f5f9', marginBottom: 8 }}>Exit {exitConfirm.symbol}?</div>
              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 20 }}>
                This will force-close the position at the current live price (with exit slippage). The trade will appear in Closed Trades.
              </div>
              {exitMsg && <div style={{ fontSize: 12, color: exitMsg.startsWith('Error') ? '#f87171' : '#4ade80', marginBottom: 12 }}>{exitMsg}</div>}
              <div style={{ display: 'flex', gap: 10 }}>
                <button
                  disabled={exitBusy}
                  onClick={async () => {
                    setExitBusy(true);
                    setExitMsg('');
                    try {
                      const res = await api.paperManualExit(exitConfirm.tradeId, selectedPortfolioId);
                      setExitMsg(`Exited at $${res.exit_price.toFixed(2)} · P&L: ${res.pnl >= 0 ? '+' : ''}$${res.pnl.toFixed(2)} (${res.pnl_pct.toFixed(1)}%)`);
                      mutatePositions();
                      mutateSummary();
                      setTimeout(() => { setExitConfirm(null); setExitMsg(''); }, 2000);
                    } catch (e: unknown) {
                      setExitMsg('Error: ' + (e instanceof Error ? e.message : 'Failed'));
                    } finally {
                      setExitBusy(false);
                    }
                  }}
                  style={{ flex: 1, padding: '8px 0', background: '#ef4444', color: '#fff', border: 'none', borderRadius: 6, fontWeight: 700, cursor: exitBusy ? 'not-allowed' : 'pointer', opacity: exitBusy ? 0.6 : 1 }}
                >{exitBusy ? 'Exiting…' : 'Confirm Exit'}</button>
                <button
                  onClick={() => { setExitConfirm(null); setExitMsg(''); }}
                  style={{ flex: 1, padding: '8px 0', background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer' }}
                >Cancel</button>
              </div>
            </div>
          </div>
        )}

        {/* Bear regime suspension banner */}
        {summary.regime_state === 'bear' && (
          <div style={{ marginBottom: 16, padding: '10px 16px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 16 }}>🐻</span>
            <div>
              <span style={{ fontSize: 12, fontWeight: 700, color: '#f87171' }}>Bear Regime — New entries suspended</span>
              <span style={{ fontSize: 11, color: '#94a3b8', marginLeft: 10 }}>
                {(summary.regime_notes?.length ? summary.regime_notes.join(' · ') : null) ?? (selectedMarket === 'HK' ? 'HSI below 200-day SMA' : 'Market in bear regime')}
              </span>
            </div>
          </div>
        )}

        {/* Stat strip */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 24 }}>
          <StatCard label="Equity" value={fmtCurrency(summary.current_equity, selectedMarket)} sub={`Cash ${fmtCurrency(summary.current_cash, selectedMarket)}`} />
          <StatCard label="Total Return" value={fmtPct(summary.total_return_pct)} color={retColor}
            sub={`Initial ${fmtCurrency(summary.initial_capital, selectedMarket)}`} />
          <StatCard label="Realized P&L" value={fmtCurrency(summary.total_realized_pnl, selectedMarket)}
            color={summary.total_realized_pnl >= 0 ? '#22c55e' : '#ef4444'} />
          <StatCard label="Unrealized P&L" value={fmtCurrency(summary.total_unrealized_pnl, selectedMarket)}
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
          {summary.outperformance_vs_hsi != null && (
            <StatCard
              label="vs HSI"
              value={(summary.outperformance_vs_hsi >= 0 ? '+' : '') + summary.outperformance_vs_hsi.toFixed(1) + '%'}
              color={summary.outperformance_vs_hsi >= 0 ? '#22c55e' : '#ef4444'}
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
              sub={
                summary.regime_vix != null
                  ? `VIX ${summary.regime_vix.toFixed(1)}`
                  : (summary.regime_notes?.[0] ?? 'HSI-based (no VIX equivalent)')
              }
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

        {/* Position quality transition panel — shows confidence band distribution and grandfathered vs new-rule positions */}
        {positions && positions.length > 0 && (() => {
          const minConf: Record<string, number> = { SWING: 50, GROWTH: 45, LONG: 40, SHORT: 30 };
          const bands = [
            { label: '0–29',  min: 0,  max: 29,  color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
            { label: '30–49', min: 30, max: 49,  color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
            { label: '50–64', min: 50, max: 64,  color: '#fbbf24', bg: 'rgba(251,191,36,0.1)'  },
            { label: '65–79', min: 65, max: 79,  color: '#94a3b8', bg: 'rgba(100,116,139,0.1)' },
            { label: '80+',   min: 80, max: 999, color: '#22c55e', bg: 'rgba(34,197,94,0.1)'   },
          ];
          const belowThresh = positions.filter(p => (p.confidence_at_entry ?? 0) < (minConf[p.trading_style] ?? 45)).length;
          const aboveThresh = positions.length - belowThresh;
          return (
            <div style={{ marginBottom: 16, padding: '12px 16px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Position Quality</span>
                {belowThresh > 0 && (
                  <span style={{ fontSize: 11, color: '#f97316', background: 'rgba(249,115,22,0.1)', padding: '2px 8px', borderRadius: 4 }}>
                    {belowThresh} grandfathered · below new threshold
                  </span>
                )}
                {aboveThresh > 0 && (
                  <span style={{ fontSize: 11, color: '#22c55e', background: 'rgba(34,197,94,0.1)', padding: '2px 8px', borderRadius: 4 }}>
                    {aboveThresh} meeting new standards
                  </span>
                )}
                <span style={{ fontSize: 10, color: '#334155', marginLeft: 'auto' }}>GROWTH ≥45 · SWING ≥50 · LONG ≥40</span>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {bands.map(b => {
                  const count = positions.filter(p => (p.confidence_at_entry ?? 0) >= b.min && (p.confidence_at_entry ?? 0) <= b.max).length;
                  if (count === 0) return null;
                  return (
                    <div key={b.label} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 5, background: b.bg, border: `1px solid ${b.color}30` }}>
                      <span style={{ fontSize: 11, color: b.color, fontWeight: 700 }}>conf {b.label}</span>
                      <span style={{ fontSize: 14, fontWeight: 800, color: b.color }}>{count}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}

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
          <div>
            {positions && positions.length > 0 && (() => {
              const inProfit = positions.filter(p => p.unrealized_pnl > 0).length;
              const inLoss   = positions.filter(p => p.unrealized_pnl < 0).length;
              const flat     = positions.length - inProfit - inLoss;
              const totalPnl = positions.reduce((s, p) => s + p.unrealized_pnl, 0);
              const profitPct = positions.length ? Math.round(inProfit / positions.length * 100) : 0;
              return (
                <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                  <div style={{ display: 'flex', flex: 1, minWidth: 200, height: 6, borderRadius: 4, overflow: 'hidden', background: '#1e293b' }}>
                    <div style={{ width: `${profitPct}%`, background: '#22c55e', transition: 'width 0.3s' }} />
                    <div style={{ width: `${positions.length ? Math.round(inLoss / positions.length * 100) : 0}%`, background: '#ef4444', transition: 'width 0.3s' }} />
                  </div>
                  <span style={{ fontSize: 12, color: '#22c55e', fontWeight: 700 }}>{inProfit} green</span>
                  {flat > 0 && <span style={{ fontSize: 12, color: '#64748b' }}>{flat} flat</span>}
                  <span style={{ fontSize: 12, color: '#ef4444', fontWeight: 700 }}>{inLoss} red</span>
                  <span style={{ fontSize: 12, color: totalPnl >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700, marginLeft: 4 }}>
                    {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(0)} open P&L
                  </span>
                </div>
              );
            })()}
          <div style={{ overflowX: 'auto' }}>
            {!positions?.length ? (
              <div style={{ color: '#64748b', padding: 24, textAlign: 'center' }}>
                No open positions. The engine enters trades during market hours when BUY signals appear.
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                    {['Symbol', 'Entry', 'Current', 'Shares', 'Value', '% Port', 'P&L', 'Range', 'Stop', 'Status', 'Target', 'Days', 'Score', 'R:R', 'Conf', 'Signal', 'Research', ''].map(h => (
                      <th key={h} style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 500 }} title={h === 'Range' ? 'Entry → Current → Target progress bar. Shows how far current price has moved toward take-profit.' : h === 'Signal' ? 'Current SWING signal from DB — flip to SELL may indicate exit opportunity' : undefined}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const totalPV = positions.reduce((s, pos) => s + pos.position_value, 0);
                    return positions.map(p => {
                    const isExpanded = expandedPositionId === p.id;
                    const reasons = p.entry_reasons as Record<string, unknown> | null;
                    return (
                    <React.Fragment key={p.id}>
                    <tr style={{ borderBottom: isExpanded ? 'none' : '1px solid #1e293b', cursor: 'pointer', background: isExpanded ? 'rgba(96,165,250,0.04)' : undefined }}
                        onClick={() => setExpandedPositionId(isExpanded ? null : p.id)}>
                      <td style={{ padding: '9px 10px' }}>
                        <Link href={`/stock/${p.symbol}`} style={{ color: '#60a5fa', fontWeight: 600, textDecoration: 'none' }}
                              onClick={e => e.stopPropagation()}>{p.symbol}</Link>
                        <span style={{ marginLeft: 6, fontSize: 10, color: '#475569' }}>{isExpanded ? '▲' : '▼'}</span>
                      </td>
                      <td style={{ padding: '9px 10px' }}>${p.entry_price.toFixed(2)}</td>
                      <td style={{ padding: '9px 10px' }}>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{p.shares.toFixed(2)}</td>
                      <td style={{ padding: '9px 10px' }}>${p.position_value.toFixed(0)}</td>
                      <td style={{ padding: '9px 10px', fontWeight: 600 }}>
                        {totalPV > 0 ? (() => {
                          const pct = p.position_value / totalPV * 100;
                          const color = pct >= 25 ? '#fbbf24' : '#94a3b8';
                          return <span style={{ color }} title={pct >= 25 ? 'High concentration — >25% of deployed capital' : undefined}>{pct.toFixed(1)}%</span>;
                        })() : '—'}
                      </td>
                      <td style={{ padding: '9px 10px', color: p.unrealized_pnl >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                        {fmtPct(p.unrealized_pct)} (${p.unrealized_pnl.toFixed(0)})
                      </td>
                      <td style={{ padding: '9px 10px' }}>
                        {(() => {
                          const entry = p.entry_price;
                          const cur = p.current_price;
                          const tgt = p.take_profit;
                          if (!cur || !tgt || tgt <= entry) {
                            return <span style={{ fontSize: 10, color: '#334155' }}>—</span>;
                          }
                          const span = tgt - entry;
                          const progress = Math.max(0, Math.min(1, (cur - entry) / span));
                          const pct = (progress * 100).toFixed(0);
                          const barColor = cur >= entry ? '#22c55e' : '#ef4444';
                          return (
                            <div style={{ width: 72 }} title={`Entry $${entry.toFixed(2)} → Current $${cur.toFixed(2)} → Target $${tgt.toFixed(2)} (${pct}% of way)`}>
                              <div style={{ height: 5, borderRadius: 3, background: '#1e293b', position: 'relative', overflow: 'hidden' }}>
                                <div style={{ position: 'absolute', left: 0, width: `${progress * 100}%`, height: '100%', background: barColor, borderRadius: 3, transition: 'width 0.3s' }} />
                              </div>
                              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: '#475569', marginTop: 2 }}>
                                <span>E</span>
                                <span style={{ color: cur >= entry ? '#4ade80' : '#f87171', fontWeight: 600 }}>{pct}%</span>
                                <span>T</span>
                              </div>
                            </div>
                          );
                        })()}
                      </td>
                      <td style={{ padding: '9px 10px', color: '#f59e0b' }}>${p.current_stop.toFixed(2)}</td>
                      <td style={{ padding: '9px 10px' }}>
                        {(() => {
                          const isBreakeven = p.current_stop >= p.entry_price * 0.999;
                          const distToStop = p.current_price != null ? (p.current_price - p.current_stop) / p.current_price : null;
                          const isNearStop = distToStop != null && distToStop < 0.02 && !isBreakeven;
                          const distToTarget = p.take_profit != null && p.current_price != null ? (p.take_profit - p.current_price) / p.take_profit : null;
                          const isNearTarget = distToTarget != null && distToTarget < 0.05;
                          if (isNearStop) return <span style={{ fontSize: 10, fontWeight: 700, color: '#ef4444', background: 'rgba(239,68,68,0.12)', padding: '2px 6px', borderRadius: 3 }}>⚠ STOP</span>;
                          if (isNearTarget) return <span style={{ fontSize: 10, fontWeight: 700, color: '#22c55e', background: 'rgba(34,197,94,0.1)', padding: '2px 6px', borderRadius: 3 }}>◎ TARGET</span>;
                          if (isBreakeven) return <span style={{ fontSize: 10, fontWeight: 700, color: '#f59e0b', background: 'rgba(251,191,36,0.1)', padding: '2px 6px', borderRadius: 3 }}>⬆ BE</span>;
                          return <span style={{ fontSize: 10, color: '#475569' }}>—</span>;
                        })()}
                      </td>
                      <td style={{ padding: '9px 10px', color: '#94a3b8' }}>{p.take_profit != null ? `$${p.take_profit.toFixed(2)}` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>
                        {(() => {
                          const expected: Record<string, number> = { SHORT: 7, SWING: 14, LONG: 28, GROWTH: 20 };
                          const exp = expected[p.trading_style] ?? 14;
                          const pct = Math.min(1, p.hold_days / exp);
                          const over = p.hold_days > exp;
                          const barColor = over ? '#f87171' : pct > 0.7 ? '#facc15' : '#60a5fa';
                          return (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                              <span style={{ fontSize: 11, color: over ? '#f87171' : '#e2e8f0' }}>{p.hold_days}d / {exp}d</span>
                              <div style={{ height: 3, width: 60, background: '#1e293b', borderRadius: 2 }}>
                                <div style={{ height: '100%', width: `${pct * 100}%`, background: barColor, borderRadius: 2 }} />
                              </div>
                            </div>
                          );
                        })()}
                      </td>
                      <td style={{ padding: '9px 10px' }}>{p.entry_score ?? '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{p.rr_ratio_at_entry != null ? `${p.rr_ratio_at_entry.toFixed(1)}:1` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>{p.confidence_at_entry != null ? `${p.confidence_at_entry.toFixed(0)}%` : '—'}</td>
                      <td style={{ padding: '9px 10px' }}>
                        {(() => {
                          const sig = p.current_signal;
                          if (!sig) return <span style={{ fontSize: 10, color: '#334155' }}>—</span>;
                          const SC: Record<string, string> = { BUY: '#22c55e', HOLD: '#facc15', WAIT: '#f97316', SELL: '#ef4444' };
                          const col = SC[sig] ?? '#94a3b8';
                          const isSell = sig === 'SELL';
                          return (
                            <span style={{ fontSize: 10, fontWeight: 700, color: col, background: `${col}22`, border: isSell ? `1px solid ${col}66` : 'none', padding: '2px 6px', borderRadius: 3 }}
                                  title={isSell ? '⚠️ Signal flipped to SELL — consider exiting' : undefined}>
                              {isSell ? '⚠ ' : ''}{sig}
                            </span>
                          );
                        })()}
                      </td>
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
                      <td style={{ padding: '9px 10px' }} onClick={e => e.stopPropagation()}>
                        <button
                          onClick={() => setExitConfirm({ tradeId: p.id, symbol: p.symbol })}
                          style={{ padding: '3px 8px', fontSize: 11, fontWeight: 600, background: 'rgba(239,68,68,0.12)', color: '#f87171', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 4, cursor: 'pointer' }}
                        >Exit</button>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr style={{ borderBottom: '1px solid #1e293b', background: 'rgba(15,23,42,0.6)' }}>
                        <td colSpan={16} style={{ padding: '12px 16px' }}>
                          <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
                            {p.decision_notes?.length > 0 && (
                              <div>
                                <div style={{ fontSize: 10, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>Entry Notes</div>
                                <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 3 }}>
                                  {p.decision_notes.map((note, i) => (
                                    <li key={i} style={{ fontSize: 11, color: '#94a3b8', display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                                      <span style={{ color: '#22c55e', marginTop: 1 }}>✓</span>
                                      {note}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {reasons && (
                              <div>
                                <div style={{ fontSize: 10, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>Signal Factors</div>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, auto)', gap: '4px 20px' }}>
                                  {[
                                    ['TA Score', typeof reasons.ta_score === 'number' ? (reasons.ta_score as number).toFixed(3) : null],
                                    ['ML Prob', typeof reasons.ml_probability === 'number' ? (reasons.ml_probability as number).toFixed(3) : null],
                                    ['ML Agree', reasons.ml_agreement as string | null],
                                    ['Pillars', reasons.independent_pillars_active as string | null],
                                    ['Weekly', reasons.weekly_trend as string | null],
                                    ['Regime', reasons.market_regime as string | null],
                                    ['High', p.highest_price != null ? `$${p.highest_price.toFixed(2)}` : null],
                                    ['Max %', p.highest_price != null && p.entry_price > 0 ? `+${((p.highest_price / p.entry_price - 1) * 100).toFixed(1)}%` : null],
                                    ['Entry Regime', p.market_regime_at_entry as string | null],
                                  ].filter(([, v]) => v !== null && v !== undefined).map(([label, val]) => (
                                    <React.Fragment key={label as string}>
                                      <span style={{ fontSize: 10, color: '#475569' }}>{label}</span>
                                      <span style={{ fontSize: 11, color: '#cbd5e1' }}>{String(val)}</span>
                                    </React.Fragment>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                    </React.Fragment>
                    );
                  });
                  })()}
                </tbody>
                {positions && positions.length > 0 && (() => {
                  const totalPV = positions.reduce((s, p) => s + p.position_value, 0);
                  const totalUnreal = positions.reduce((s, p) => s + p.unrealized_pnl, 0);
                  const totalUnrealColor = totalUnreal >= 0 ? '#22c55e' : '#ef4444';
                  const sellCount = positions.filter(p => p.current_signal === 'SELL').length;
                  return (
                    <tfoot>
                      <tr style={{ borderTop: '2px solid #334155', background: '#0f172a', fontWeight: 700 }}>
                        <td style={{ padding: '8px 10px', color: '#94a3b8', fontSize: 12 }}>
                          {positions.length} positions{sellCount > 0 ? ` · ` : ''}
                          {sellCount > 0 && <span style={{ color: '#ef4444', fontSize: 11 }}>{sellCount} SELL ⚠</span>}
                        </td>
                        <td colSpan={3} />
                        <td style={{ padding: '8px 10px', color: '#e2e8f0', fontSize: 13 }}>${totalPV.toFixed(0)}</td>
                        <td colSpan={1} />
                        <td style={{ padding: '8px 10px', color: totalUnrealColor, fontSize: 13 }}>
                          {totalUnreal >= 0 ? '+' : ''}${totalUnreal.toFixed(0)}
                        </td>
                        <td colSpan={11} />
                      </tr>
                    </tfoot>
                  );
                })()}
              </table>
            )}
          </div>
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
                    <React.Fragment key={d.id}>
                    <tr
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
                      <tr style={{ borderBottom: '1px solid #1e293b' }}>
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
                                    const isNum = typeof v === 'number' && !isNaN(v as number);
                                    const display = isNum ? (v as number).toFixed(2) : (v == null || (typeof v === 'number' && isNaN(v as number))) ? '—' : String(v);
                                    const isPos = isNum && (v as number) > 0;
                                    const isNeg = isNum && (v as number) < 0;
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
                    </React.Fragment>
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

        {/* Journal tab — chronological decision log with full entry+exit reasoning */}
        {tab === 'Journal' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* Portfolio label + switch hint */}
            <div style={{ fontSize: 12, color: '#475569', padding: '4px 0', display: 'flex', gap: 16, alignItems: 'center' }}>
              <span>Portfolio: <strong style={{ color: '#94a3b8' }}>{portfolioList.find(p => p.id === selectedPortfolioId)?.name ?? '—'}</strong></span>
              <span style={{ fontSize: 11, color: '#334155' }}>Select a different portfolio from the list above to switch.</span>
            </div>
            {!(decisions?.items?.length) ? (
              <div style={{ color: '#64748b', padding: 40, textAlign: 'center' }}>No trade records yet for this portfolio.</div>
            ) : (
              decisions.items.map(d => {
                const er = d.entry_reasons as Record<string, unknown> ?? {};
                const xr = d.exit_reasons as Record<string, unknown> ?? {};
                const notes = (d.decision_notes as string[]) ?? [];
                const isWin = d.pnl != null && d.pnl > 0;
                const isClosed = d.stage === 'closed';
                return (
                  <div key={d.id} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, overflow: 'hidden' }}>
                    {/* Header */}
                    <div style={{ padding: '10px 14px', borderBottom: '1px solid #1e293b', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                      <Link href={`/stock/${d.symbol}`} style={{ fontWeight: 700, fontSize: 14, color: '#60a5fa', textDecoration: 'none' }}>{d.symbol}</Link>
                      <span style={{ fontSize: 10, background: 'rgba(148,163,184,0.1)', border: '1px solid #334155', borderRadius: 4, padding: '2px 6px', color: '#94a3b8' }}>{d.trading_style}</span>
                      {isClosed ? (
                        <span style={{ fontSize: 11, fontWeight: 700, color: isWin ? '#22c55e' : '#ef4444', background: isWin ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)', padding: '2px 8px', borderRadius: 4 }}>
                          {isWin ? '✓ WIN' : '✗ LOSS'} {d.pct_return != null ? fmtPct(d.pct_return) : ''}
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, color: '#f59e0b', background: 'rgba(245,158,11,0.1)', padding: '2px 6px', borderRadius: 4 }}>OPEN</span>
                      )}
                      <span style={{ marginLeft: 'auto', fontSize: 11, color: '#475569' }}>
                        {fmtTs(d.entry_time)}{d.exit_time ? ` → ${fmtTs(d.exit_time)}` : ''}{d.hold_days ? ` · ${d.hold_days}d` : ''}
                      </span>
                    </div>
                    <div style={{ padding: '10px 14px', display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                      {/* Entry reasoning */}
                      <div style={{ flex: 1, minWidth: 200 }}>
                        <div style={{ fontSize: 10, fontWeight: 600, color: '#22c55e', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>Entry — ${d.entry_price?.toFixed(2)}</div>
                        {notes.length > 0 && (
                          <ul style={{ margin: '0 0 8px', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 2 }}>
                            {notes.map((n, i) => <li key={i} style={{ fontSize: 11, color: '#94a3b8' }}>· {n}</li>)}
                          </ul>
                        )}
                        <div style={{ display: 'grid', gridTemplateColumns: 'auto auto', gap: '2px 16px' }}>
                          {([
                            ['TA Score', typeof er.ta_score === 'number' ? (er.ta_score as number).toFixed(3) : null],
                            ['ML Prob', typeof er.ml_probability === 'number' ? (er.ml_probability as number).toFixed(3) : null],
                            ['ML Agree', er.ml_agreement as string],
                            ['Regime', er.market_regime as string],
                            ['Weekly', er.weekly_trend as string],
                            ['Pillars', er.independent_pillars_active as string],
                          ] as [string, string | null][]).filter(([, v]) => v != null).map(([k, v]) => (
                            <React.Fragment key={k}>
                              <span style={{ fontSize: 10, color: '#475569' }}>{k}</span>
                              <span style={{ fontSize: 10, color: '#cbd5e1' }}>{v}</span>
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                      {/* Exit reasoning */}
                      {isClosed && (
                        <div style={{ flex: 1, minWidth: 200 }}>
                          <div style={{ fontSize: 10, fontWeight: 600, color: isWin ? '#22c55e' : '#ef4444', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
                            Exit — {d.exit_price != null ? `$${d.exit_price.toFixed(2)}` : ''}
                          </div>
                          <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6 }}>{d.exit_reason ?? '—'}</div>
                          {Object.keys(xr).length > 0 && (
                            <div style={{ display: 'grid', gridTemplateColumns: 'auto auto', gap: '2px 16px' }}>
                              {(Object.entries(xr).slice(0, 8) as [string, unknown][]).map(([k, v]) => (
                                <React.Fragment key={k}>
                                  <span style={{ fontSize: 10, color: '#475569' }}>{k.replace(/_/g, ' ')}</span>
                                  <span style={{ fontSize: 10, color: '#cbd5e1' }}>{String(v)}</span>
                                </React.Fragment>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* Closed Trades tab */}
        {tab === 'Closed Trades' && (
          <div>
            {/* Stats bar */}
            {summary.closed_trades > 0 && (
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14, padding: '10px 14px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8 }}>
                {[
                  { label: 'Closed', value: String(summary.closed_trades), color: '#94a3b8' },
                  {
                    label: 'Win Rate',
                    value: `${summary.win_rate_pct.toFixed(1)}%`,
                    color: summary.win_rate_pct >= 55 ? '#22c55e' : summary.win_rate_pct >= 45 ? '#f59e0b' : '#ef4444',
                  },
                  {
                    label: 'Avg Win / Loss',
                    value: `${fmtPct(summary.avg_win_pct, 1)} / ${fmtPct(summary.avg_loss_pct, 1)}`,
                    color: summary.avg_win_pct > Math.abs(summary.avg_loss_pct ?? 0) ? '#22c55e' : '#f59e0b',
                  },
                  ...(summary.profit_factor != null ? [{
                    label: 'Profit Factor',
                    value: summary.profit_factor.toFixed(2),
                    color: summary.profit_factor >= 1.5 ? '#22c55e' : summary.profit_factor >= 1 ? '#f59e0b' : '#ef4444',
                  }] : []),
                  ...(summary.expectancy_pct != null ? [{
                    label: 'Expectancy',
                    value: `${summary.expectancy_pct >= 0 ? '+' : ''}${summary.expectancy_pct.toFixed(1)}%`,
                    color: summary.expectancy_pct >= 0 ? '#22c55e' : '#ef4444',
                  }] : []),
                ].map(s => (
                  <div key={s.label} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{s.label}</span>
                    <span style={{ fontSize: 14, fontWeight: 700, color: s.color }}>{s.value}</span>
                  </div>
                ))}
                {summary.exit_breakdown && Object.keys(summary.exit_breakdown).length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, borderLeft: '1px solid #1e293b', paddingLeft: 12 }}>
                    <span style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Exit Reasons</span>
                    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                      {Object.entries(summary.exit_breakdown)
                        .sort((a, b) => b[1] - a[1])
                        .map(([reason, count]) => {
                          const col = EXIT_COLORS[reason] ?? '#94a3b8';
                          const label = EXIT_LABELS[reason] ?? reason.slice(0, 5);
                          return (
                            <span key={reason} style={{ fontSize: 11, fontWeight: 700, color: col, background: `${col}18`, border: `1px solid ${col}44`, padding: '1px 5px', borderRadius: 3 }}
                                  title={`${reason}: ${count} trades`}>
                              {label} {count}
                            </span>
                          );
                        })}
                    </div>
                  </div>
                )}
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
              {trades?.items?.length ? (
                <button
                  onClick={async () => {
                    const url = api.paperTradesCsvUrl(selectedPortfolioId);
                    const token = typeof window !== 'undefined' ? localStorage.getItem('stockai_jwt') : null;
                    const r = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
                    if (!r.ok) return;
                    const blob = await r.blob();
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = `paper-trades-${new Date().toISOString().slice(0, 10)}.csv`;
                    a.click();
                  }}
                  style={{ padding: '6px 14px', borderRadius: 7, border: '1px solid #334155', background: '#0f172a', color: '#94a3b8', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}
                >
                  ↓ Export All CSV
                </button>
              ) : null}
            </div>
            <div style={{ overflowX: 'auto' }}>
              {!trades?.items.length ? (
                <div style={{ color: '#64748b', padding: 24, textAlign: 'center' }}>No closed trades yet.</div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
                      {['Symbol', 'Style', 'Entry', 'Exit', 'Entry $', 'Exit $', 'P&L %', 'P&L $', 'Days', 'Exit Reason', 'R:R', 'Score', 'Conf'].map(h => (
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
                        <td style={{ padding: '9px 10px', color: '#94a3b8', fontSize: 11 }}>{t.trading_style ?? '—'}</td>
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
                        <td style={{ padding: '9px 10px', color: t.confidence_at_entry != null ? (t.confidence_at_entry >= 50 ? '#94a3b8' : '#f97316') : '#475569', fontSize: 11 }}>
                          {t.confidence_at_entry != null ? `${t.confidence_at_entry.toFixed(0)}%` : '—'}
                        </td>
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
            {(curve?.length ?? 0) > 0 && <MonteCarloSection data={curve ?? []} />}
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

        {/* Risk tab */}
        {tab === 'Risk' && (() => {
          const pos = positions ?? [];
          const totalEquity = summary?.current_equity ?? summary?.initial_capital ?? 1;
          const totalAtRisk = pos.reduce((s, p) => s + (p.entry_price - (p.current_stop ?? p.stop_loss)) * p.shares, 0);
          const totalPositionValue = pos.reduce((s, p) => s + p.position_value, 0);
          const totalUnrealized = pos.reduce((s, p) => s + p.unrealized_pnl, 0);
          // Sector concentration
          const sectorMap: Record<string, number> = {};
          for (const p of pos) {
            const s = p.sector ?? 'Unknown';
            sectorMap[s] = (sectorMap[s] ?? 0) + p.position_value;
          }
          const sectors = Object.entries(sectorMap).sort((a, b) => b[1] - a[1]);
          // Regime at entry concentration
          const regimeMap: Record<string, number> = {};
          for (const p of pos) {
            const r = p.market_regime_at_entry ?? 'unknown';
            regimeMap[r] = (regimeMap[r] ?? 0) + p.position_value;
          }
          const regimes = Object.entries(regimeMap).sort((a, b) => b[1] - a[1]);
          const REGIME_COLOR: Record<string, string> = { bull: '#22c55e', neutral: '#94a3b8', choppy: '#f59e0b', risk_off: '#f97316', bear: '#ef4444', unknown: '#475569' };

          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {pos.length === 0 ? (
                <div style={{ padding: 24, background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, textAlign: 'center', color: '#475569' }}>
                  No open positions to analyze.
                </div>
              ) : (
                <>
                  {/* Summary bar */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                    {[
                      { label: 'Positions Open', value: pos.length, color: '#e2e8f0' },
                      { label: 'Total $ at Risk', value: `$${totalAtRisk.toFixed(0)}`, color: totalAtRisk > totalEquity * 0.05 ? '#ef4444' : '#f59e0b' },
                      { label: 'Risk % of Equity', value: `${((totalAtRisk / totalEquity) * 100).toFixed(1)}%`, color: totalAtRisk / totalEquity > 0.05 ? '#ef4444' : '#22c55e' },
                      { label: 'Unrealized P&L', value: `${totalUnrealized >= 0 ? '+' : ''}$${totalUnrealized.toFixed(0)}`, color: totalUnrealized >= 0 ? '#22c55e' : '#ef4444' },
                    ].map(({ label, value, color }) => (
                      <div key={label} style={{ padding: '12px 14px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
                        <div style={{ fontSize: 11, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>{label}</div>
                        <div style={{ fontSize: 20, fontWeight: 800, color }}>{value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Per-position stop heat map */}
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 14 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
                      Stop Distance Heat Map
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                          <tr style={{ borderBottom: '1px solid #1e293b' }}>
                            {['Symbol', 'Entry', 'Current', 'Stop', 'Stop Distance', '$ at Risk', 'Size % Equity', 'Unrealized'].map(h => (
                              <th key={h} style={{ padding: '5px 10px', textAlign: 'left', color: '#475569', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {[...pos].sort((a, b) => {
                            const stopDistA = ((a.current_price ?? a.entry_price) - (a.current_stop ?? a.stop_loss)) / (a.current_price ?? a.entry_price);
                            const stopDistB = ((b.current_price ?? b.entry_price) - (b.current_stop ?? b.stop_loss)) / (b.current_price ?? b.entry_price);
                            return stopDistA - stopDistB;
                          }).map(p => {
                            const cur = p.current_price ?? p.entry_price;
                            const stop = p.current_stop ?? p.stop_loss;
                            const stopDist = ((cur - stop) / cur) * 100;
                            const dollarRisk = (p.entry_price - (p.current_stop ?? p.stop_loss)) * p.shares;
                            const sizePct = (p.position_value / totalEquity) * 100;
                            const danger = stopDist < 3;
                            const warn = stopDist < 6;
                            return (
                              <tr key={p.id} style={{ borderBottom: '1px solid #0f172a', background: danger ? 'rgba(239,68,68,0.05)' : 'transparent' }}>
                                <td style={{ padding: '5px 10px', fontWeight: 700, color: '#e2e8f0' }}>{p.symbol}</td>
                                <td style={{ padding: '5px 10px', color: '#94a3b8' }}>${p.entry_price.toFixed(2)}</td>
                                <td style={{ padding: '5px 10px', color: p.unrealized_pct >= 0 ? '#22c55e' : '#ef4444' }}>${cur.toFixed(2)}</td>
                                <td style={{ padding: '5px 10px', color: '#64748b' }}>${stop.toFixed(2)}</td>
                                <td style={{ padding: '5px 10px', fontWeight: 700, color: danger ? '#ef4444' : warn ? '#f59e0b' : '#22c55e' }}>
                                  {stopDist.toFixed(1)}%
                                </td>
                                <td style={{ padding: '5px 10px', color: '#94a3b8' }}>${dollarRisk.toFixed(0)}</td>
                                <td style={{ padding: '5px 10px', color: sizePct > 15 ? '#f59e0b' : '#94a3b8' }}>{sizePct.toFixed(1)}%</td>
                                <td style={{ padding: '5px 10px', fontWeight: 600, color: p.unrealized_pct >= 0 ? '#22c55e' : '#ef4444' }}>
                                  {p.unrealized_pct >= 0 ? '+' : ''}{p.unrealized_pct.toFixed(1)}%
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Two-column: sector + regime */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 14 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>Sector Concentration</div>
                      {sectors.map(([sector, val]) => {
                        const pct = (val / totalPositionValue) * 100;
                        return (
                          <div key={sector} style={{ marginBottom: 8 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3, fontSize: 12 }}>
                              <span style={{ color: '#94a3b8' }}>{sector}</span>
                              <span style={{ color: pct > 40 ? '#f59e0b' : '#64748b', fontWeight: 600 }}>{pct.toFixed(0)}%</span>
                            </div>
                            <div style={{ height: 6, background: '#1e293b', borderRadius: 3 }}>
                              <div style={{ width: `${pct}%`, height: '100%', background: pct > 40 ? '#f59e0b' : '#6366f1', borderRadius: 3 }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 14 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>Regime at Entry</div>
                      {regimes.map(([regime, val]) => {
                        const pct = (val / totalPositionValue) * 100;
                        const color = REGIME_COLOR[regime] ?? '#94a3b8';
                        return (
                          <div key={regime} style={{ marginBottom: 8 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3, fontSize: 12 }}>
                              <span style={{ color, fontWeight: 600 }}>{regime}</span>
                              <span style={{ color: '#64748b' }}>{pct.toFixed(0)}%</span>
                            </div>
                            <div style={{ height: 6, background: '#1e293b', borderRadius: 3 }}>
                              <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </>
              )}
            </div>
          );
        })()}

        {/* DE Audit tab */}
        {tab === 'DE Audit' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Summary bar */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {[
                { label: 'Total Divergences', value: deAudit?.total_divergences ?? '—', color: '#ef4444' },
                { label: 'Total Agreements', value: deAudit?.total_agreements ?? '—', color: '#22c55e' },
                { label: 'Agreement Rate', value: deAudit?.agreement_rate_pct != null ? `${deAudit.agreement_rate_pct}%` : '—', color: '#6366f1' },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ flex: 1, minWidth: 160, padding: '12px 16px', background: '#0f172a', borderRadius: 8, border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: 11, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 22, fontWeight: 800, color }}>{value}</div>
                </div>
              ))}
            </div>

            {!deAudit || (deAudit.total_divergences === 0 && deAudit.total_agreements === 0) ? (
              <div style={{ padding: 24, background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, textAlign: 'center', color: '#475569' }}>
                No shadow data yet. The Decision Engine runs alongside every scan cycle during market hours.
              </div>
            ) : (
              <>
                {/* Divergences */}
                {(deAudit?.divergences?.length ?? 0) > 0 && (
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 14 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
                      Divergences ({deAudit!.total_divergences}) — where DE and paper engine disagree
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                          <tr style={{ borderBottom: '1px solid #1e293b' }}>
                            {['Time', 'Symbol', 'Paper', 'Paper Score', 'DE Verdict', 'DE Score', 'DE Min', 'Blocked Reason'].map(h => (
                              <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#475569', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {deAudit!.divergences.slice(0, 50).map((d, i) => (
                            <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                              <td style={{ padding: '5px 10px', color: '#64748b', whiteSpace: 'nowrap' }}>{new Date(d.ts).toLocaleString()}</td>
                              <td style={{ padding: '5px 10px', fontWeight: 700, color: '#e2e8f0' }}>{d.symbol}</td>
                              <td style={{ padding: '5px 10px', color: d.paper_enter ? '#22c55e' : '#64748b', fontWeight: 600 }}>{d.paper_enter ? 'ENTER' : 'SKIP'}</td>
                              <td style={{ padding: '5px 10px', color: '#94a3b8', textAlign: 'center' }}>{d.paper_score}</td>
                              <td style={{ padding: '5px 10px', fontWeight: 700, color: ['BUY','SCALE'].includes(d.de_verdict) ? '#22c55e' : d.de_verdict === 'BLOCKED' ? '#ef4444' : '#64748b' }}>{d.de_verdict}</td>
                              <td style={{ padding: '5px 10px', color: '#94a3b8', textAlign: 'center' }}>{d.de_score}</td>
                              <td style={{ padding: '5px 10px', color: '#64748b', textAlign: 'center' }}>{d.de_min_score}</td>
                              <td style={{ padding: '5px 10px', color: '#f97316', fontSize: 11 }}>{d.de_blocked_reason ?? '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Recent agreements (sample) */}
                {(deAudit?.agreements?.length ?? 0) > 0 && (
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: 14 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: '#22c55e', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
                      Recent Agreements (last 20 shown)
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                          <tr style={{ borderBottom: '1px solid #1e293b' }}>
                            {['Time', 'Symbol', 'Decision', 'DE Score', 'Paper Score'].map(h => (
                              <th key={h} style={{ padding: '6px 10px', textAlign: 'left', color: '#475569', fontWeight: 600 }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {deAudit!.agreements.slice(0, 20).map((a, i) => (
                            <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                              <td style={{ padding: '5px 10px', color: '#64748b', whiteSpace: 'nowrap' }}>{new Date(a.ts).toLocaleString()}</td>
                              <td style={{ padding: '5px 10px', fontWeight: 700, color: '#e2e8f0' }}>{a.symbol}</td>
                              <td style={{ padding: '5px 10px', color: a.paper_enter ? '#22c55e' : '#64748b', fontWeight: 600 }}>{a.paper_enter ? 'ENTER' : 'SKIP'}</td>
                              <td style={{ padding: '5px 10px', color: '#94a3b8', textAlign: 'center' }}>{a.de_score}</td>
                              <td style={{ padding: '5px 10px', color: '#94a3b8', textAlign: 'center' }}>{a.paper_score}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
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
            <MLIntelligencePanel />
          </div>
        )}

        {/* Explainer */}
        <div style={{ marginTop: 32, background: '#1e293b', borderRadius: 10, padding: 16, border: '1px solid #334155', fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
          <strong style={{ color: '#94a3b8' }}>How it works:</strong> The paper engine runs every 5–10 minutes during market hours.
          It scans for fresh {summary.trading_style}-style BUY signals, scores entry quality (R:R, RSI, regime, sector, conviction),
          and enters simulated positions when the score meets the threshold. It monitors all open positions each cycle,
          updating trailing stops and exiting when stops, targets, signal reversals, or time limits are reached.
          Initial capital: {fmtCurrency(summary.initial_capital, selectedMarket)}. Risk per trade: {(summary.config.risk_per_trade_pct * 100).toFixed(0)}% of equity.
        </div>
      </div>
    </main>
  );
}

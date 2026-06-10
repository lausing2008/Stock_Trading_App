import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import useSWR from 'swr';
import { getSession } from '@/lib/auth';
import {
  api,
  type PaperPortfolioSummary,
  type PaperPosition,
  type PaperTrade,
  type PaperEquityPoint,
  type PaperDecisionItem,
  type PaperPortfolioConfig,
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

      const layout = {
        paper_bgcolor: '#0f172a', plot_bgcolor: '#0f172a',
        margin: { t: 10, b: 40, l: 60, r: 10 },
        height: 240,
        xaxis: { color: '#64748b', gridcolor: '#1e293b', showgrid: true },
        yaxis: { color: '#64748b', gridcolor: '#1e293b', tickprefix: '$', tickformat: ',.0f' },
        legend: { font: { color: '#94a3b8', size: 11 }, bgcolor: 'transparent', orientation: 'h', x: 0, y: -0.15 },
        hovermode: 'x unified',
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

function EngineControls({ config, onDone }: { config: PaperPortfolioConfig; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const enabled = config.enabled !== false;
  const paused = config.paused === true;

  async function setState(state: 'running' | 'paused' | 'stopped') {
    setBusy(true);
    try { await api.paperSetEngine(state); onDone(); }
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
  initialCapital, currentCash, onSave,
}: { initialCapital: number; currentCash: number; onSave: () => void }) {
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
      const r = await api.paperSetCapital(body);
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

function ConfigPanel({ config, onSave }: { config: PaperPortfolioConfig; onSave: () => void }) {
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
      await api.paperConfigure(draft);
      setMsg('Saved');
      onSave();
      setDraft({});
    } catch { setMsg('Error saving'); }
    finally { setSaving(false); }
  }

  async function reset() {
    if (!confirm('Reset portfolio? All open positions will be force-closed and cash reset to initial capital.')) return;
    try {
      const r = await api.paperReset();
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

// ── Main Page ─────────────────────────────────────────────────────────────────

const TABS = ['Positions', 'Decisions', 'Closed Trades', 'Equity Curve'] as const;
type Tab = typeof TABS[number];

export default function PaperPortfolioPage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>('Positions');
  const [isAdmin, setIsAdmin] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [tradesPage, setTradesPage] = useState(1);
  const [decPage, setDecPage] = useState(1);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (session.role === 'admin') setIsAdmin(true);
    setAuthed(true);
  }, [router]);

  const { data: summary, mutate: mutateSummary } = useSWR(
    authed ? 'paper-summary' : null, () => api.paperSummary(), { refreshInterval: 60_000 }
  );
  const { data: positions } = useSWR(
    authed && tab === 'Positions' ? 'paper-positions' : null,
    () => api.paperPositions(), { refreshInterval: 60_000 }
  );
  const { data: trades } = useSWR(
    authed && tab === 'Closed Trades' ? ['paper-trades', tradesPage] : null,
    () => api.paperTrades({ page: tradesPage, limit: 50 })
  );
  const { data: curve } = useSWR(
    authed && tab === 'Equity Curve' ? 'paper-curve' : null,
    () => api.paperEquityCurve(180)
  );
  const { data: decisions } = useSWR(
    authed && tab === 'Decisions' ? ['paper-decisions', decPage] : null,
    () => api.paperDecisions({ page: decPage, limit: 50, days_back: 90 })
  );

  if (!authed || !summary) {
    return (
      <main style={{ minHeight: '100vh', background: '#0f172a', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ color: '#94a3b8' }}>{!authed ? 'Authenticating…' : 'Loading paper portfolio…'}</div>
      </main>
    );
  }

  const ret = summary.total_return_pct;
  const retColor = ret >= 0 ? '#22c55e' : '#ef4444';

  return (
    <main style={{ minHeight: '100vh', background: '#0f172a', color: '#f1f5f9', padding: '24px 20px', fontFamily: 'sans-serif' }}>
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24, flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>Paper Portfolio</div>
            <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 3 }}>
              {summary.trading_style} style · autonomous paper trading engine (WF-2)
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <EngineStateBadge config={summary.config} />
            {isAdmin && <EngineControls config={summary.config} onDone={mutateSummary} />}
            <span style={{ fontSize: 12, color: '#64748b', background: '#1e293b', border: '1px solid #334155', borderRadius: 5, padding: '4px 10px' }}>
              Live · 60s refresh
            </span>
            <Link href="/" style={{ fontSize: 12, color: '#64748b', textDecoration: 'none' }}>← Home</Link>
          </div>
        </div>

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
                    {['Symbol', 'Entry', 'Current', 'Shares', 'Value', 'P&L', 'Stop', 'Target', 'Days', 'Score', 'R:R', 'Conf'].map(h => (
                      <th key={h} style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map(p => (
                    <tr key={p.id} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '9px 10px' }}>
                        <Link href={`/stocks/${p.symbol}`} style={{ color: '#60a5fa', fontWeight: 600, textDecoration: 'none' }}>{p.symbol}</Link>
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
                  {(decisions?.items ?? []).map(d => (
                    <tr key={d.id} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '9px 10px' }}>
                        <Link href={`/stocks/${d.symbol}`} style={{ color: '#60a5fa', fontWeight: 600, textDecoration: 'none' }}>{d.symbol}</Link>
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
                        {(d.decision_notes ?? []).slice(0, 2).join(' · ')}
                      </td>
                    </tr>
                  ))}
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
                          <Link href={`/stocks/${t.symbol}`} style={{ color: '#60a5fa', fontWeight: 600, textDecoration: 'none' }}>{t.symbol}</Link>
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
            {(curve?.length ?? 0) === 0 && (
              <div style={{ color: '#64748b', fontSize: 13, textAlign: 'center' }}>
                Equity curve snapshots are taken once per day after market close. Check back after first trading session.
              </div>
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
            />
            <ConfigPanel config={summary.config} onSave={mutateSummary} />
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

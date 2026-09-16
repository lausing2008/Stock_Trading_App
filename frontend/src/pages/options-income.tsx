import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import Head from 'next/head';
import useSWR from 'swr';
import { getSession, isAdmin } from '@/lib/auth';
import {
  api,
  type OptionsIncomeCandidate,
  type OptionsIncomePortfolioListItem,
  type OptionsIncomePosition,
  type OptionsIncomeEquityPoint,
} from '@/lib/api';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUSD(v: number | null | undefined): string {
  if (v == null) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(v);
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(digits) + '%';
}

function fmtDate(d: string | null | undefined): string {
  if (!d) return '—';
  try { return new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' }); }
  catch { return d; }
}

const STRATEGY_LABEL: Record<string, string> = {
  COVERED_CALL: 'Covered Call',
  CASH_SECURED_PUT: 'Cash-Secured Put',
};
const STRATEGY_COLOR: Record<string, string> = {
  COVERED_CALL: '#38bdf8',
  CASH_SECURED_PUT: '#a78bfa',
};

function StrategyBadge({ strategy }: { strategy: string }) {
  const c = STRATEGY_COLOR[strategy] ?? '#64748b';
  return (
    <span style={{ fontSize: 10, fontWeight: 700, color: c, background: c + '18', border: `1px solid ${c}44`, borderRadius: 5, padding: '2px 7px', whiteSpace: 'nowrap' }}>
      {STRATEGY_LABEL[strategy] ?? strategy}
    </span>
  );
}

// ── Styles (matches paper-gates.tsx / paper-portfolio.tsx conventions) ───────

const PAGE: React.CSSProperties = {
  minHeight: '100vh', background: '#0a0f1e', color: '#e2e8f0',
  fontFamily: 'ui-monospace, monospace', padding: '24px 20px', maxWidth: 1200, margin: '0 auto',
};
const SECTION: React.CSSProperties = { marginBottom: 28 };
const H2: React.CSSProperties = { fontSize: 14, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 14, paddingBottom: 6, borderBottom: '1px solid #1e293b' };
const CARD: React.CSSProperties = { background: '#111827', border: '1px solid #1e293b', borderRadius: 10, padding: '14px 18px' };
const TH: React.CSSProperties = { textAlign: 'left', padding: '6px 10px', fontSize: 10, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', whiteSpace: 'nowrap' };
const TD: React.CSSProperties = { padding: '9px 10px', fontSize: 12, color: '#cbd5e1', verticalAlign: 'top', borderBottom: '1px solid #0f172a', whiteSpace: 'nowrap' };
const BTN: React.CSSProperties = { padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #334155', background: 'transparent', color: '#cbd5e1' };
const BTN_PRIMARY: React.CSSProperties = { ...BTN, background: '#22c55e', border: '1px solid #22c55e', color: '#052e16' };

// ── Sparkline (lightweight inline SVG — no charting library dependency) ──────

function Sparkline({ points }: { points: OptionsIncomeEquityPoint[] }) {
  if (points.length < 2) {
    return <div style={{ fontSize: 12, color: '#64748b', padding: '20px 0', textAlign: 'center' }}>Equity snapshots are taken once per day — check back after the first full trading day.</div>;
  }
  const w = 100, h = 32, pad = 2;
  const values = points.map(p => p.equity);
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const xStep = (w - pad * 2) / (points.length - 1);
  const path = values.map((v, i) => {
    const x = pad + i * xStep;
    const y = h - pad - ((v - min) / span) * (h - pad * 2);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
  const up = values[values.length - 1] >= values[0];
  const color = up ? '#22c55e' : '#ef4444';
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%', height: 48 }}>
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// ── Create portfolio modal ────────────────────────────────────────────────────

function CreateIncomePortfolioModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('US Options Income');
  const [capital, setCapital] = useState('250000');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  async function create() {
    const cap = parseFloat(capital);
    if (!name.trim()) { setErr('Name is required'); return; }
    if (isNaN(cap) || cap <= 0) { setErr('Capital must be > 0'); return; }
    setSaving(true); setErr('');
    try {
      await api.incomeCreatePortfolio({ name: name.trim(), initial_capital: cap });
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
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div style={{ background: '#1e293b', borderRadius: 12, padding: 28, width: 380, border: '1px solid #334155', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }} onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 4 }}>New Options Income Portfolio</div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 20 }}>
          Sells covered calls and cash-secured puts automatically. A single high-priced stock's
          collateral is capped at 25% of capital, so a book with only 1-2x the size of the
          largest expected position (e.g. $50k for a $500 stock) can't be diversified.
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={{ fontSize: 11, color: '#94a3b8', display: 'block', marginBottom: 4 }}>Name</label>
            <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: '#94a3b8', display: 'block', marginBottom: 4 }}>Initial Capital ($)</label>
            <input style={inputStyle} type="number" value={capital} onChange={e => setCapital(e.target.value)} />
          </div>
          {err && <div style={{ color: '#f87171', fontSize: 12 }}>{err}</div>}
          <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
            <button style={{ ...BTN, flex: 1 }} onClick={onClose} disabled={saving}>Cancel</button>
            <button style={{ ...BTN_PRIMARY, flex: 1, opacity: saving ? 0.6 : 1 }} onClick={create} disabled={saving}>
              {saving ? 'Creating…' : 'Create'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

type Tab = 'Open Positions' | 'Closed Positions' | 'Candidates';

export default function OptionsIncomePage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [admin, setAdmin] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>('Open Positions');
  const [showCreate, setShowCreate] = useState(false);
  const [strategyFilter, setStrategyFilter] = useState<'ALL' | 'COVERED_CALL' | 'CASH_SECURED_PUT'>('ALL');
  const [runningStep, setRunningStep] = useState(false);
  const [runStepMsg, setRunStepMsg] = useState('');

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    // This page sits in _app.tsx's `adminOnly: true` Admin group, same as paper-portfolio.tsx
    // right next to it — matching that sibling page's own established pattern exactly: login
    // is the page-level gate, isAdmin only controls which ACTIONS render (create portfolio,
    // run step now). An earlier version of this guard redirected non-admin/non-advanced users
    // away entirely, which was actually STRICTER than its own nav placement implied — an
    // Advanced-tier non-admin user would never see this link in their (adminOnly) nav to begin
    // with, so the extra redirect was unreachable dead logic, not a real protection.
    setAdmin(isAdmin(s));
    setAuthed(true);
  }, [router]);

  const { data: portfolios, mutate: mutatePortfolios } = useSWR(
    authed ? 'income-portfolios' : null, () => api.incomePortfolios(), { refreshInterval: 60_000 },
  );

  useEffect(() => {
    if (portfolios?.length && selectedId === null) setSelectedId(portfolios[0].id);
  }, [portfolios, selectedId]);

  const selected = portfolios?.find(p => p.id === selectedId) ?? null;

  const { data: openPositions } = useSWR(
    authed && selectedId != null && tab === 'Open Positions' ? ['income-positions-open', selectedId] : null,
    () => api.incomePositions(selectedId!, 'open'), { refreshInterval: 60_000 },
  );
  const { data: closedPositions } = useSWR(
    authed && selectedId != null && tab === 'Closed Positions' ? ['income-positions-closed', selectedId] : null,
    () => api.incomePositions(selectedId!, 'closed'), { refreshInterval: 60_000 },
  );
  const { data: equityCurve } = useSWR(
    authed && selectedId != null ? ['income-equity-curve', selectedId] : null,
    () => api.incomeEquityCurve(selectedId!), { refreshInterval: 60_000 },
  );
  const { data: candidatesResp } = useSWR(
    authed && tab === 'Candidates' ? ['income-candidates', strategyFilter] : null,
    () => api.incomeCandidates(strategyFilter === 'ALL' ? undefined : { strategy: strategyFilter }),
    { refreshInterval: 300_000 },
  );

  async function runStepNow() {
    setRunningStep(true); setRunStepMsg('');
    try {
      await api.incomeRunStepNow();
      setRunStepMsg('Ran — refreshing…');
      await mutatePortfolios();
      setTimeout(() => setRunStepMsg(''), 4000);
    } catch (e: any) {
      setRunStepMsg(e?.message ?? 'Failed to run step');
    } finally {
      setRunningStep(false);
    }
  }

  if (!authed) return null;

  return (
    <>
      <Head><title>Options Income — StockAI</title></Head>
      <div style={PAGE}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 6, flexWrap: 'wrap' }}>
          <Link href="/paper-portfolio" style={{ fontSize: 12, color: '#64748b', textDecoration: 'none' }}>← Paper Portfolio</Link>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: '#f1f5f9', margin: 0 }}>Options Income Engine</h1>
        </div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 24 }}>
          Systematic covered calls and cash-secured puts, scored daily from each symbol&apos;s latest
          archived option chain. Both strategies close at expiry — assigned or liquidated at the
          market close — rather than roll.
        </div>

        {/* ── Portfolio selector ─────────────────────────────────────────────── */}
        <div style={SECTION}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
            <div style={H2}>Portfolios</div>
            {admin && (
              <>
                <button style={BTN} onClick={() => setShowCreate(true)}>+ New Portfolio</button>
                <button style={{ ...BTN, opacity: runningStep ? 0.6 : 1 }} onClick={runStepNow} disabled={runningStep}>
                  {runningStep ? 'Running…' : '▶ Run Step Now'}
                </button>
                {runStepMsg && <span style={{ fontSize: 11, color: '#94a3b8' }}>{runStepMsg}</span>}
              </>
            )}
          </div>

          {!portfolios?.length ? (
            <div style={{ ...CARD, textAlign: 'center', color: '#64748b', fontSize: 13 }}>
              No options income portfolios yet.{admin ? ' Create one to get started.' : ''}
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
              {portfolios.map(p => (
                <button
                  key={p.id}
                  onClick={() => setSelectedId(p.id)}
                  style={{
                    ...CARD, cursor: 'pointer', textAlign: 'left', minWidth: 220,
                    borderColor: selectedId === p.id ? '#22c55e88' : '#1e293b',
                    background: selectedId === p.id ? 'rgba(34,197,94,0.06)' : '#111827',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
                    <span style={{ fontWeight: 700, fontSize: 13, color: '#f1f5f9' }}>{p.name}</span>
                    {!p.is_active && <span style={{ fontSize: 10, color: '#f59e0b' }}>PAUSED</span>}
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 700, color: p.total_return_pct >= 0 ? '#4ade80' : '#f87171' }}>
                    {fmtUSD(p.current_equity)} <span style={{ fontSize: 12 }}>({fmtPct(p.total_return_pct)})</span>
                  </div>
                  <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
                    {p.open_positions} open · {p.closed_positions} closed · {fmtUSD(p.total_premium_collected)} premium collected
                  </div>
                </button>
              ))}
            </div>
          )}

          {selected && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
              {[
                { label: 'Current Equity', value: fmtUSD(selected.current_equity) },
                { label: 'Total Return', value: fmtPct(selected.total_return_pct), color: selected.total_return_pct >= 0 ? '#4ade80' : '#f87171' },
                { label: 'Win Rate', value: `${selected.win_rate_pct.toFixed(1)}%` },
                { label: 'Assignment Rate', value: `${selected.assignment_rate_pct.toFixed(1)}%` },
                { label: 'Cash Available', value: fmtUSD(selected.current_cash) },
                { label: 'Premium Collected', value: fmtUSD(selected.total_premium_collected), color: '#4ade80' },
              ].map(stat => (
                <div key={stat.label} style={CARD}>
                  <div style={{ fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>{stat.label}</div>
                  <div style={{ fontSize: 17, fontWeight: 700, color: stat.color ?? '#e2e8f0' }}>{stat.value}</div>
                </div>
              ))}
              <div style={{ ...CARD, gridColumn: 'span 2', minWidth: 260 }}>
                <div style={{ fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>Equity Curve</div>
                <Sparkline points={equityCurve ?? []} />
              </div>
            </div>
          )}
        </div>

        {showCreate && <CreateIncomePortfolioModal onClose={() => setShowCreate(false)} onCreated={() => mutatePortfolios()} />}

        {/* ── Tabs ───────────────────────────────────────────────────────────── */}
        <div style={SECTION}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, borderBottom: '1px solid #1e293b', paddingBottom: 10 }}>
            {(['Open Positions', 'Closed Positions', 'Candidates'] as Tab[]).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  border: '1px solid ' + (tab === t ? '#22c55e' : '#334155'),
                  background: tab === t ? 'rgba(34,197,94,0.12)' : 'transparent',
                  color: tab === t ? '#4ade80' : '#94a3b8',
                }}
              >
                {t}
              </button>
            ))}
          </div>

          {tab === 'Candidates' && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
              {(['ALL', 'COVERED_CALL', 'CASH_SECURED_PUT'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setStrategyFilter(s)}
                  style={{
                    ...BTN, padding: '4px 10px', fontSize: 11,
                    borderColor: strategyFilter === s ? '#38bdf8' : '#334155',
                    color: strategyFilter === s ? '#38bdf8' : '#94a3b8',
                  }}
                >
                  {s === 'ALL' ? 'All Strategies' : STRATEGY_LABEL[s]}
                </button>
              ))}
            </div>
          )}

          {tab === 'Open Positions' && (!selectedId ? null : (
            <PositionsTable positions={openPositions ?? []} emptyLabel="No open positions." />
          ))}
          {tab === 'Closed Positions' && (!selectedId ? null : (
            <PositionsTable positions={closedPositions ?? []} emptyLabel="No closed positions yet." showClose />
          ))}
          {tab === 'Candidates' && (
            <CandidatesTable candidates={candidatesResp?.candidates ?? []} />
          )}
        </div>
      </div>
    </>
  );
}

function PositionsTable({ positions, emptyLabel, showClose }: { positions: OptionsIncomePosition[]; emptyLabel: string; showClose?: boolean }) {
  if (!positions.length) {
    return <div style={{ ...CARD, textAlign: 'center', color: '#64748b', fontSize: 13 }}>{emptyLabel}</div>;
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>Symbol</th>
            <th style={TH}>Strategy</th>
            <th style={TH}>Strike</th>
            <th style={TH}>Expiry</th>
            <th style={TH}>Contracts</th>
            <th style={TH}>Entry Price</th>
            <th style={TH}>Premium</th>
            <th style={TH}>Collateral</th>
            {showClose && <th style={TH}>Close Price</th>}
            {showClose && <th style={TH}>Assigned</th>}
            {showClose && <th style={TH}>P&amp;L</th>}
            {showClose && <th style={TH}>Return</th>}
          </tr>
        </thead>
        <tbody>
          {positions.map(p => (
            <tr key={p.id}>
              <td style={{ ...TD, fontWeight: 700, color: '#f1f5f9' }}>{p.symbol}</td>
              <td style={TD}><StrategyBadge strategy={p.strategy} /></td>
              <td style={TD}>${p.strike.toFixed(2)}</td>
              <td style={TD}>{fmtDate(p.expiry)}</td>
              <td style={TD}>{p.contracts}</td>
              <td style={TD}>${p.underlying_entry_price.toFixed(2)}</td>
              <td style={{ ...TD, color: '#4ade80' }}>{fmtUSD(p.total_premium_collected)}</td>
              <td style={TD}>{fmtUSD(p.collateral_reserved)}</td>
              {showClose && <td style={TD}>{p.underlying_close_price != null ? `$${p.underlying_close_price.toFixed(2)}` : '—'}</td>}
              {showClose && <td style={TD}>{p.assigned == null ? '—' : (p.assigned ? <span style={{ color: '#f59e0b' }}>Yes</span> : <span style={{ color: '#64748b' }}>No</span>)}</td>}
              {showClose && <td style={{ ...TD, color: (p.pnl ?? 0) >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>{p.pnl != null ? fmtUSD(p.pnl) : '—'}</td>}
              {showClose && <td style={{ ...TD, color: (p.pct_return_on_collateral ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>{fmtPct(p.pct_return_on_collateral)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CandidatesTable({ candidates }: { candidates: OptionsIncomeCandidate[] }) {
  if (!candidates.length) {
    return <div style={{ ...CARD, textAlign: 'center', color: '#64748b', fontSize: 13 }}>No candidates found — chain data may be stale or no contracts clear the delta/liquidity filters right now.</div>;
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={TH}>Symbol</th>
            <th style={TH}>Strategy</th>
            <th style={TH}>Strike</th>
            <th style={TH}>Expiry</th>
            <th style={TH}>DTE</th>
            <th style={TH}>Delta</th>
            <th style={TH}>Premium</th>
            <th style={TH}>Ann. Yield</th>
            <th style={TH}>Cap / Purchase Price</th>
            <th style={TH}>Collateral</th>
            <th style={TH}>OI</th>
            <th style={TH}>IV</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map(c => (
            <tr key={`${c.symbol}-${c.strategy}-${c.option_symbol}`}>
              <td style={{ ...TD, fontWeight: 700, color: '#f1f5f9' }}>{c.symbol}</td>
              <td style={TD}><StrategyBadge strategy={c.strategy} /></td>
              <td style={TD}>${c.strike.toFixed(2)}</td>
              <td style={TD}>{fmtDate(c.expiry)}</td>
              <td style={TD}>{c.days_to_expiry}d</td>
              <td style={TD}>{c.delta.toFixed(2)}</td>
              <td style={{ ...TD, color: '#4ade80' }}>${c.premium.toFixed(2)}</td>
              <td style={{ ...TD, fontWeight: 700, color: '#38bdf8' }}>{c.annualized_yield_pct.toFixed(1)}%</td>
              <td style={TD}>${(c.effective_cap_price ?? c.effective_purchase_price ?? 0).toFixed(2)}</td>
              <td style={TD}>{fmtUSD(c.collateral_required)}</td>
              <td style={TD}>{c.open_interest ?? '—'}</td>
              <td style={TD}>{c.iv != null ? `${(c.iv * 100).toFixed(0)}%` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

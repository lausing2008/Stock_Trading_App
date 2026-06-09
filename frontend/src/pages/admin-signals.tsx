import { useState, useCallback, useEffect } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type AdminSignalLogItem } from '@/lib/api';
import { getSession } from '@/lib/auth';

// ── Static config ─────────────────────────────────────────────────────────────

const SIGNAL_OPTS = ['ALL', 'BUY', 'HOLD', 'WAIT', 'SELL'] as const;
const HORIZON_OPTS = ['ALL', 'SHORT', 'SWING', 'LONG', 'GROWTH'] as const;
const DAYS_OPTS = [7, 30, 60, 90, 180] as const;
const PAGE_LIMIT = 50;

const SIGNAL_COLORS: Record<string, string> = {
  BUY: '#22c55e', HOLD: '#38bdf8', WAIT: '#f59e0b', SELL: '#ef4444',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function fmtTs(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' }) +
      ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch { return ts; }
}

function exportCSV(items: AdminSignalLogItem[]) {
  const headers = ['Symbol', 'Name', 'Market', 'Signal', 'Horizon', 'Confidence', 'Bull%', 'Generated At', 'Outcome%', 'Correct', 'Entry', 'Exit', 'Exit Date'];
  const rows = items.map(r => [
    r.symbol, r.name, r.market, r.signal, r.horizon,
    r.confidence.toFixed(1), r.bullish_probability != null ? (r.bullish_probability * 100).toFixed(1) : '',
    r.generated_at,
    r.outcome_pct != null ? r.outcome_pct.toFixed(2) : '',
    r.is_correct == null ? '' : r.is_correct ? 'Y' : 'N',
    r.entry_price != null ? r.entry_price.toFixed(2) : '',
    r.exit_price != null ? r.exit_price.toFixed(2) : '',
    r.exit_date ?? '',
  ]);
  const csv = [headers, ...rows].map(r => r.map(c => `"${c}"`).join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `signal-log-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Cell components ───────────────────────────────────────────────────────────

function SignalBadge({ sig }: { sig: string }) {
  return (
    <span style={{
      background: `${SIGNAL_COLORS[sig] ?? '#64748b'}22`,
      color: SIGNAL_COLORS[sig] ?? '#94a3b8',
      border: `1px solid ${SIGNAL_COLORS[sig] ?? '#64748b'}44`,
      borderRadius: 4, padding: '1px 7px', fontSize: 11, fontWeight: 700, letterSpacing: 0.3,
    }}>
      {sig}
    </span>
  );
}

function ConfBar({ val }: { val: number }) {
  const pct = Math.min(100, Math.max(0, val));
  const col = pct >= 70 ? '#22c55e' : pct >= 55 ? '#f59e0b' : '#64748b';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 48, height: 5, background: '#1e293b', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: col, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color: col, fontWeight: 600 }}>{pct.toFixed(0)}%</span>
    </div>
  );
}

function OutcomeCell({ pct, correct }: { pct: number | null; correct: boolean | null }) {
  if (pct == null) return <span style={{ color: '#475569', fontSize: 11 }}>Pending</span>;
  const col = pct >= 0 ? '#22c55e' : '#ef4444';
  return (
    <span style={{ color: col, fontWeight: 600, fontSize: 12 }}>
      {correct != null && (
        <span style={{ marginRight: 4 }}>{correct ? '✓' : '✗'}</span>
      )}
      {fmtPct(pct)}
    </span>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AdminSignalsPage() {
  const router = useRouter();

  // Admin gate — decode role from JWT
  const [userRole, setUserRole] = useState<string | null>(null);
  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    setUserRole(session.role);
    if (session.role !== 'admin') router.replace('/');
  }, [router]);

  // Filters
  const [symbolFilter, setSymbolFilter] = useState('');
  const [signalFilter, setSignalFilter] = useState<typeof SIGNAL_OPTS[number]>('ALL');
  const [horizonFilter, setHorizonFilter] = useState<typeof HORIZON_OPTS[number]>('ALL');
  const [daysBack, setDaysBack] = useState<typeof DAYS_OPTS[number]>(30);
  const [page, setPage] = useState(1);

  // Fetch
  const { data, isLoading, error } = useSWR(
    userRole === 'admin'
      ? ['admin-signal-log', symbolFilter, signalFilter, horizonFilter, daysBack, page]
      : null,
    () => api.getAdminSignalLog({
      symbol: symbolFilter.trim().toUpperCase() || undefined,
      signal_type: signalFilter !== 'ALL' ? signalFilter : undefined,
      horizon: horizonFilter !== 'ALL' ? horizonFilter : undefined,
      days_back: daysBack,
      page,
      limit: PAGE_LIMIT,
    }),
    { revalidateOnFocus: false }
  );

  const handleFilterChange = useCallback(() => setPage(1), []);

  if (userRole == null) return null;
  if (userRole !== 'admin') return null;

  // ── Styles ─────────────────────────────────────────────────────────────────

  const card: React.CSSProperties = {
    background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8,
    padding: '12px 16px', marginBottom: 12,
  };
  const inp: React.CSSProperties = {
    background: '#1e293b', border: '1px solid #334155', borderRadius: 5,
    color: '#e2e8f0', fontSize: 12, padding: '5px 9px', outline: 'none',
  };
  const btn: React.CSSProperties = {
    background: '#1e293b', border: '1px solid #334155', borderRadius: 5,
    color: '#94a3b8', fontSize: 11, padding: '5px 12px', cursor: 'pointer',
  };

  const items = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  // Summary stats from loaded page
  const resolved = items.filter(r => r.is_correct != null);
  const correct = resolved.filter(r => r.is_correct).length;
  const winRate = resolved.length > 0 ? ((correct / resolved.length) * 100).toFixed(0) : '—';
  const buyCount = items.filter(r => r.signal === 'BUY').length;
  const sellCount = items.filter(r => r.signal === 'SELL').length;

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto', fontFamily: 'system-ui, sans-serif', color: '#e2e8f0' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: '#f1f5f9' }}>
            System Signal Log
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#64748b' }}>
            Admin-only — all signals generated by the system, with outcomes when resolved
          </p>
        </div>
        <button
          style={{ ...btn, color: '#4ade80', borderColor: '#166534' }}
          onClick={() => items.length > 0 && exportCSV(items)}
          disabled={items.length === 0}
        >
          Export CSV
        </button>
      </div>

      {/* Stat strip */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        {[
          { label: 'Total signals', val: data?.total != null ? String(data.total) : '—' },
          { label: 'BUY on page', val: String(buyCount) },
          { label: 'SELL on page', val: String(sellCount) },
          { label: 'Win rate (resolved)', val: resolved.length > 0 ? `${winRate}% of ${resolved.length}` : '—' },
        ].map(s => (
          <div key={s.label} style={{ ...card, flex: '0 0 auto', margin: 0, padding: '8px 16px', minWidth: 140 }}>
            <div style={{ fontSize: 18, fontWeight: 800, color: '#f1f5f9' }}>{s.val}</div>
            <div style={{ fontSize: 10, color: '#64748b', marginTop: 2 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={{ ...card, display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', marginBottom: 16 }}>
        <input
          placeholder="Filter symbol…"
          value={symbolFilter}
          onChange={e => { setSymbolFilter(e.target.value); handleFilterChange(); }}
          style={{ ...inp, width: 130 }}
        />
        <select value={signalFilter} onChange={e => { setSignalFilter(e.target.value as typeof SIGNAL_OPTS[number]); handleFilterChange(); }} style={inp}>
          {SIGNAL_OPTS.map(s => <option key={s}>{s}</option>)}
        </select>
        <select value={horizonFilter} onChange={e => { setHorizonFilter(e.target.value as typeof HORIZON_OPTS[number]); handleFilterChange(); }} style={inp}>
          {HORIZON_OPTS.map(h => <option key={h}>{h}</option>)}
        </select>
        <select value={daysBack} onChange={e => { setDaysBack(Number(e.target.value) as typeof DAYS_OPTS[number]); handleFilterChange(); }} style={inp}>
          {DAYS_OPTS.map(d => <option key={d} value={d}>Last {d}d</option>)}
        </select>
        <span style={{ fontSize: 11, color: '#475569', marginLeft: 4 }}>
          {isLoading ? 'Loading…' : data ? `${data.total} signals found` : ''}
        </span>
      </div>

      {/* Error */}
      {error && (
        <div style={{ ...card, color: '#ef4444', marginBottom: 12 }}>
          Failed to load signal log: {String(error?.message ?? error)}
        </div>
      )}

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1e293b' }}>
              {['Symbol', 'Signal', 'Confidence', 'Horizon', 'Bull%', 'Generated', 'Outcome', 'Entry', 'Exit', 'Source'].map(h => (
                <th key={h} style={{ padding: '8px 10px', textAlign: 'left', color: '#64748b', fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap' }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={10} style={{ padding: 24, textAlign: 'center', color: '#475569' }}>Loading…</td></tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr><td colSpan={10} style={{ padding: 24, textAlign: 'center', color: '#475569' }}>No signals in this range</td></tr>
            )}
            {items.map(row => (
              <tr key={row.id} style={{ borderBottom: '1px solid #0f172a' }}
                onMouseEnter={e => (e.currentTarget.style.background = '#1e293b')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>
                  <Link href={`/stock/${row.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none' }}>
                    {row.symbol}
                  </Link>
                  <div style={{ color: '#475569', fontSize: 10, marginTop: 1 }}>{row.name.slice(0, 22)}</div>
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <SignalBadge sig={row.signal} />
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <ConfBar val={row.confidence} />
                </td>
                <td style={{ padding: '7px 10px', color: '#94a3b8' }}>{row.horizon}</td>
                <td style={{ padding: '7px 10px', color: '#94a3b8' }}>
                  {row.bullish_probability != null ? `${(row.bullish_probability * 100).toFixed(0)}%` : '—'}
                </td>
                <td style={{ padding: '7px 10px', color: '#64748b', whiteSpace: 'nowrap' }}>
                  {fmtTs(row.generated_at)}
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <OutcomeCell pct={row.outcome_pct} correct={row.is_correct} />
                </td>
                <td style={{ padding: '7px 10px', color: '#64748b' }}>
                  {row.entry_price != null ? row.entry_price.toFixed(2) : '—'}
                </td>
                <td style={{ padding: '7px 10px', color: '#64748b' }}>
                  {row.exit_price != null ? row.exit_price.toFixed(2) : '—'}
                  {row.exit_date && <div style={{ fontSize: 10, color: '#475569' }}>{row.exit_date.slice(0, 10)}</div>}
                </td>
                <td style={{ padding: '7px 10px', color: '#475569' }}>{row.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginTop: 16 }}>
          <button style={btn} disabled={page === 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
          <span style={{ fontSize: 12, color: '#64748b' }}>Page {page} / {totalPages}</span>
          <button style={btn} disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next →</button>
        </div>
      )}

      {/* Explainer */}
      <div style={{ marginTop: 24, padding: '12px 16px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 11, color: '#475569', lineHeight: 1.7 }}>
        <strong style={{ color: '#64748b' }}>How to read this:</strong>{' '}
        Every BUY/SELL signal fired by the system is logged here. <strong>Outcome</strong> fills in automatically after the hold window closes (SHORT=7d, SWING=14d, LONG=28d) — ✓ means price moved in the signal direction, ✗ means it did not.
        <strong> Pending</strong> = hold window not yet closed. Use the confidence bar and bull% to gauge signal conviction. CSV export available for offline analysis.
      </div>
    </div>
  );
}

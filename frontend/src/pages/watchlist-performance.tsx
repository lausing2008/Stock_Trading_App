import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type WatchlistPerfStock, type WatchlistRotationHistoryRow } from '@/lib/api';
import { getSession } from '@/lib/auth';

// ── Static config ─────────────────────────────────────────────────────────────

const STYLE_OPTS = ['GROWTH', 'SWING', 'SHORT', 'LONG'] as const;
const DAYS_OPTS = [30, 60, 90, 180] as const;

const STYLE_COLORS: Record<string, string> = {
  GROWTH: '#38bdf8', SWING: '#f59e0b', SHORT: '#ef4444', LONG: '#22c55e',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function winRateColor(wr: number | null): string {
  if (wr == null) return '#475569';
  if (wr >= 0.55) return '#22c55e';
  if (wr >= 0.45) return '#f59e0b';
  return '#ef4444';
}

// ── Cell components ───────────────────────────────────────────────────────────

function WinRatePill({ row }: { row: WatchlistPerfStock }) {
  if (row.win_rate == null) {
    return <span style={{ color: '#475569', fontSize: 11 }}>No data</span>;
  }
  const col = winRateColor(row.win_rate);
  return (
    <span style={{
      background: `${col}22`, color: col, border: `1px solid ${col}44`,
      borderRadius: 20, padding: '2px 9px', fontSize: 12, fontWeight: 700,
    }}>
      {(row.win_rate * 100).toFixed(0)}%
      {!row.reliable && <span style={{ marginLeft: 4, color: '#475569', fontWeight: 400 }}>⚠</span>}
    </span>
  );
}

function SectorBar({ sectorPct, maxSectorPct }: { sectorPct: Record<string, number>; maxSectorPct: number }) {
  const entries = Object.entries(sectorPct).sort((a, b) => b[1] - a[1]);
  const palette = ['#3b6ea5', '#c97b3d', '#5a9c6f', '#8b5fa8', '#b04a4a', '#c9a13d', '#4a8ba0', '#7a7a7a', '#a05a8b', '#8a8a3d'];
  const overCapSectors = entries.filter(([, pct]) => pct > maxSectorPct * 100);

  return (
    <div>
      <div style={{ display: 'flex', height: 24, borderRadius: 6, overflow: 'hidden', border: '1px solid #1e293b' }}>
        {entries.map(([sector, pct], i) => (
          <div
            key={sector}
            title={`${sector}: ${pct}%`}
            style={{
              width: `${pct}%`, background: palette[i % palette.length],
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 10, fontWeight: 600, color: 'white', overflow: 'hidden', whiteSpace: 'nowrap',
            }}
          >
            {pct >= 8 ? `${sector} ${pct}%` : ''}
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', marginTop: 8, fontSize: 11, color: '#94a3b8' }}>
        {entries.map(([sector, pct], i) => (
          <span key={sector} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: palette[i % palette.length], display: 'inline-block' }} />
            {sector} {pct}%
          </span>
        ))}
      </div>
      {overCapSectors.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, color: '#ef4444', fontWeight: 600 }}>
          ⚠ {overCapSectors.map(([s, p]) => `${s} (${p}%)`).join(', ')} over the {(maxSectorPct * 100).toFixed(0)}% sector cap —
          entries here may be silently blocked even when the stock itself qualifies.
        </div>
      )}
    </div>
  );
}

// ── Rotation history ──────────────────────────────────────────────────────────

function RotationHistorySection({ style }: { style: string }) {
  const [reverting, setReverting] = useState<number | null>(null);
  const [revertError, setRevertError] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR(
    ['watchlist-rotation-history', style],
    () => api.getWatchlistRotationHistory({ style, limit: 100 }),
    { revalidateOnFocus: false }
  );

  async function handleRevert(row: WatchlistRotationHistoryRow) {
    const symbol = row.action === 'drop' ? row.old_value.symbol : row.new_value.symbol;
    if (!confirm(`Revert this ${row.action === 'drop' ? 're-add' : 'removal of'} ${symbol}?`)) return;
    setReverting(row.id);
    setRevertError(null);
    try {
      await api.revertWatchlistRotation(row.id);
      mutate();
    } catch (e: unknown) {
      setRevertError((e as Error)?.message || 'Revert failed');
    } finally {
      setReverting(null);
    }
  }

  const rows = data?.rows ?? [];

  return (
    <div style={{ marginTop: '32px' }}>
      <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>
        ROTATION HISTORY — WATCHLIST-AUTO-ROTATION
      </div>
      <p style={{ fontSize: '11px', color: '#475569', marginBottom: '10px' }}>
        Every add/drop the weekly auto-rotation job has made for this style. Reverting an "add" removes that
        stock again; reverting a "drop" re-adds it to the same watchlist it came from.
      </p>
      {revertError && (
        <div style={{ padding: '10px 14px', borderRadius: '8px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', fontSize: '12px', color: '#f87171', marginBottom: '10px' }}>
          {revertError}
        </div>
      )}
      <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
            <thead>
              <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                {['When', 'Action', 'Symbol', 'Watchlist', 'Win Rate / K-Score', 'n', ''].map(h => (
                  <th key={h} style={{ textAlign: h === '' || h === 'When' || h === 'Symbol' || h === 'Watchlist' ? 'left' : 'right', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={7} style={{ padding: '20px', textAlign: 'center', color: '#475569' }}>Loading…</td></tr>
              )}
              {!isLoading && rows.map(row => {
                const info = row.action === 'drop' ? row.old_value : row.new_value;
                return (
                  <tr key={row.id} style={{ borderBottom: '1px solid #1e293b', opacity: row.reverted ? 0.5 : 1 }}>
                    <td style={{ padding: '8px 12px', color: '#64748b', whiteSpace: 'nowrap' }}>
                      {new Date(row.ts).toLocaleDateString()} {new Date(row.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{
                        padding: '2px 8px', borderRadius: 20, fontSize: 11, fontWeight: 700,
                        background: row.action === 'add' ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
                        color: row.action === 'add' ? '#22c55e' : '#ef4444',
                      }}>
                        {row.action === 'add' ? '+ Added' : '− Dropped'}
                      </span>
                    </td>
                    <td style={{ padding: '8px 12px', fontWeight: 700, color: '#e2e8f0' }}>{info.symbol ?? '—'}</td>
                    <td style={{ padding: '8px 12px', color: '#94a3b8' }}>{info.watchlist_name ?? '—'}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', color: '#64748b' }}>
                      {row.action === 'drop'
                        ? (row.validation_ev_pct != null ? `${(row.validation_ev_pct * 100).toFixed(0)}% (floor ${((row.baseline_validation_ev_pct ?? 0) * 100).toFixed(0)}%)` : '—')
                        : (row.new_value.kscore != null ? row.new_value.kscore.toFixed(1) : '—')}
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', color: '#64748b' }}>{row.validation_n ?? '—'}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                      {row.reverted ? (
                        <span style={{ fontSize: 11, color: '#475569' }}>Reverted</span>
                      ) : (
                        <button
                          onClick={() => handleRevert(row)}
                          disabled={reverting === row.id}
                          style={{
                            padding: '3px 10px', borderRadius: '5px', fontSize: '11px', fontWeight: 600,
                            cursor: reverting === row.id ? 'not-allowed' : 'pointer',
                            border: '1px solid #1e293b', background: 'transparent',
                            color: reverting === row.id ? '#334155' : '#38bdf8',
                          }}
                        >
                          {reverting === row.id ? 'Reverting…' : 'Revert'}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={7} style={{ padding: '20px', textAlign: 'center', color: '#475569' }}>No rotation actions recorded yet for this style.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WatchlistPerformancePage() {
  const router = useRouter();

  // Admin gate — decode role from JWT
  const [authed, setAuthed] = useState(false);
  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (session.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const [style, setStyle] = useState<typeof STYLE_OPTS[number]>('GROWTH');
  const [daysBack, setDaysBack] = useState<typeof DAYS_OPTS[number]>(90);
  const [minOutcomes, setMinOutcomes] = useState(4);
  const [sortBy, setSortBy] = useState<'win_rate' | 'symbol' | 'n'>('win_rate');

  const { data, isLoading, error, mutate } = useSWR(
    authed ? ['watchlist-performance', style, daysBack, minOutcomes] : null,
    () => api.getWatchlistPerformance({ style, days_back: daysBack, min_outcomes: minOutcomes, candidate_limit: 10 }),
    { revalidateOnFocus: false }
  );

  if (!authed) return null;

  const rows = data?.watchlist_perf ?? [];
  const sorted = [...rows].sort((a, b) => {
    if (sortBy === 'symbol') return a.symbol.localeCompare(b.symbol);
    if (sortBy === 'n') return b.n - a.n;
    // win_rate: nulls last, then ascending (worst first)
    if (a.win_rate == null && b.win_rate == null) return 0;
    if (a.win_rate == null) return 1;
    if (b.win_rate == null) return -1;
    return a.win_rate - b.win_rate;
  });

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '24px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Watchlist Performance</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>
            Per-style win rate, sector concentration, and top candidates not yet on the watchlist
          </p>
        </div>
        <button
          onClick={() => mutate()}
          style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}
        >
          ↺ Refresh
        </button>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '20px' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {STYLE_OPTS.map(s => (
            <button
              key={s}
              onClick={() => setStyle(s)}
              style={{
                padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 700, cursor: 'pointer',
                border: `1px solid ${style === s ? STYLE_COLORS[s] : '#1e293b'}`,
                background: style === s ? `${STYLE_COLORS[s]}22` : 'transparent',
                color: style === s ? STYLE_COLORS[s] : '#64748b',
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <select
          value={daysBack}
          onChange={e => setDaysBack(Number(e.target.value) as typeof DAYS_OPTS[number])}
          style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          {DAYS_OPTS.map(d => <option key={d} value={d}>{d}d lookback</option>)}
        </select>
        <select
          value={minOutcomes}
          onChange={e => setMinOutcomes(Number(e.target.value))}
          style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          {[2, 4, 6, 10].map(n => <option key={n} value={n}>min {n} outcomes = reliable</option>)}
        </select>
      </div>

      {isLoading && (
        <div style={{ textAlign: 'center', padding: '40px', color: '#475569', fontSize: '13px' }}>Loading…</div>
      )}
      {error && (
        <div style={{ padding: '16px 20px', borderRadius: '10px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', fontSize: '13px', color: '#f87171' }}>
          Failed to load watchlist performance data.
        </div>
      )}

      {data && (
        <>
          {/* Summary stats */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '10px', marginBottom: '24px' }}>
            <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '20px', fontWeight: 800, color: '#e2e8f0' }}>{data.total_watchlist_stocks}</div>
              <div style={{ fontSize: '11px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Watchlist stocks</div>
            </div>
            <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '20px', fontWeight: 800, color: winRateColor(data.avg_win_rate) }}>
                {data.avg_win_rate != null ? `${(data.avg_win_rate * 100).toFixed(0)}%` : '—'}
              </div>
              <div style={{ fontSize: '11px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Avg win rate (reliable)</div>
            </div>
            <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '20px', fontWeight: 800, color: '#e2e8f0' }}>{data.n_reliable}</div>
              <div style={{ fontSize: '11px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Reliable (n≥{data.min_outcomes})</div>
            </div>
            <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '20px', fontWeight: 800, color: '#e2e8f0' }}>{(data.max_sector_pct * 100).toFixed(0)}%</div>
              <div style={{ fontSize: '11px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Sector cap (live)</div>
            </div>
          </div>

          {/* Sector composition */}
          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>SECTOR COMPOSITION</div>
          <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b', marginBottom: '24px' }}>
            {Object.keys(data.sector_pct).length > 0
              ? <SectorBar sectorPct={data.sector_pct} maxSectorPct={data.max_sector_pct} />
              : <div style={{ fontSize: '12px', color: '#475569' }}>No stocks on this watchlist yet.</div>}
          </div>

          {/* Per-symbol win rate table */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
            <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>WIN RATE BY SYMBOL</div>
            <div style={{ display: 'flex', gap: 4 }}>
              {(['win_rate', 'symbol', 'n'] as const).map(s => (
                <button
                  key={s}
                  onClick={() => setSortBy(s)}
                  style={{
                    padding: '3px 10px', borderRadius: '5px', fontSize: '10.5px', fontWeight: 600, cursor: 'pointer',
                    border: `1px solid ${sortBy === s ? '#38bdf8' : '#1e293b'}`,
                    background: sortBy === s ? 'rgba(56,189,248,0.1)' : 'transparent',
                    color: sortBy === s ? '#38bdf8' : '#64748b',
                  }}
                >
                  {s === 'win_rate' ? 'Worst first' : s === 'symbol' ? 'A-Z' : 'Most data'}
                </button>
              ))}
            </div>
          </div>
          <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden', marginBottom: '24px' }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
                <thead>
                  <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                    {['Symbol', 'Sector', 'Market', 'Win Rate', 'Avg Return', 'n'].map(h => (
                      <th key={h} style={{ textAlign: h === 'Symbol' || h === 'Sector' || h === 'Market' ? 'left' : 'right', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(row => (
                    <tr key={row.stock_id} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '8px 12px', fontWeight: 700, color: '#e2e8f0' }}>{row.symbol}</td>
                      <td style={{ padding: '8px 12px', color: '#94a3b8' }}>{row.sector}</td>
                      <td style={{ padding: '8px 12px', color: '#64748b' }}>{row.market}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right' }}><WinRatePill row={row} /></td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (row.avg_return_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                        {fmtPct(row.avg_return_pct)}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: '#64748b' }}>{row.n}</td>
                    </tr>
                  ))}
                  {sorted.length === 0 && (
                    <tr><td colSpan={6} style={{ padding: '20px', textAlign: 'center', color: '#475569' }}>No stocks on this watchlist.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <p style={{ fontSize: '11px', color: '#334155', marginTop: '-14px', marginBottom: '24px' }}>
            ⚠ = fewer than {data.min_outcomes} resolved outcomes — treat as noise, not a verdict.
          </p>

          {/* Candidates not on watchlist */}
          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>
            TOP-RANKED CANDIDATES NOT ON THIS WATCHLIST
          </div>
          <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden' }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
                <thead>
                  <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                    {['Symbol', 'Sector', 'Market', 'K-Score'].map(h => (
                      <th key={h} style={{ textAlign: h === 'K-Score' ? 'right' : 'left', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.candidates.map(c => (
                    <tr key={c.symbol} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '8px 12px', fontWeight: 700, color: '#e2e8f0' }}>{c.symbol}</td>
                      <td style={{ padding: '8px 12px', color: '#94a3b8' }}>{c.sector}</td>
                      <td style={{ padding: '8px 12px', color: '#64748b' }}>{c.market}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: '#38bdf8', fontWeight: 700 }}>{c.score.toFixed(1)}</td>
                    </tr>
                  ))}
                  {data.candidates.length === 0 && (
                    <tr><td colSpan={4} style={{ padding: '20px', textAlign: 'center', color: '#475569' }}>No candidates found.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <RotationHistorySection style={style} />
        </>
      )}
    </div>
  );
}

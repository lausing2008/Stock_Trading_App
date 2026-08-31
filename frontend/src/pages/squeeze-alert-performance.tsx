import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type SqueezeAlertTypeSummary, type SqueezeAlertWindowStat, type SqueezeAlertBacktestWindow } from '@/lib/api';
import { getSession } from '@/lib/auth';

// T264-SQUEEZEALERT-PERFORMANCE — direct user request: "design a page under Admin to measure
// the option sell and short squeeze performance and win rates if I buy from the signal, the
// first email alert." Mirrors watchlist-performance.tsx's own structural convention.

// ── Static config ─────────────────────────────────────────────────────────────

const DAYS_OPTS = [30, 90, 180, 365] as const;

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

// AUD-SQUEEZE-IGNITION-DASHBOARD-OMITTED: recent_alerts has NO alert_type filter on the
// backend, so a squeeze_ignition row always appeared here — but this row-label lookup was a
// hardcoded 3-way ternary (short_squeeze / gamma_unwind_calls / else "Gamma (Puts)") that
// silently mislabeled every squeeze_ignition row as "Gamma (Puts)" since it matched neither
// of the first two branches. A real, explicit map avoids this "any 4th type falls into the
// last branch by accident" trap for any future 5th alert type too.
const ALERT_TYPE_ROW_LABELS: Record<string, string> = {
  short_squeeze: 'Short Squeeze',
  squeeze_ignition: 'Ignition',
  gamma_unwind_calls: 'Gamma (Calls)',
  gamma_unwind_puts: 'Gamma (Puts)',
};

function winRateColor(wr: number | null): string {
  if (wr == null) return '#475569';
  if (wr >= 0.55) return '#22c55e';
  if (wr >= 0.45) return '#f59e0b';
  return '#ef4444';
}

function WinRatePill({ stat }: { stat: SqueezeAlertWindowStat }) {
  if (stat == null || stat.win_rate == null) {
    return <span style={{ color: '#475569', fontSize: 11 }}>No data</span>;
  }
  const col = winRateColor(stat.win_rate);
  return (
    <span style={{
      background: `${col}22`, color: col, border: `1px solid ${col}44`,
      borderRadius: 20, padding: '2px 9px', fontSize: 12, fontWeight: 700,
    }}>
      {(stat.win_rate * 100).toFixed(0)}%
    </span>
  );
}

function TypeCard({ row }: { row: SqueezeAlertTypeSummary }) {
  const primary = row.window_10d;
  return (
    <div style={{ padding: '16px 18px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
      <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: 4 }}>{row.label}</div>
      <div style={{ fontSize: '11px', color: '#475569', marginBottom: 12 }}>
        {row.fired_count} alert{row.fired_count === 1 ? '' : 's'} fired in window
        {primary && ` · ${primary.n} outcome${primary.n === 1 ? '' : 's'} resolved (10d)`}
      </div>
      {primary ? (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <WinRatePill stat={primary} />
          <span style={{ fontSize: '15px', fontWeight: 700, color: (primary.avg_return_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>
            {fmtPct(primary.avg_return_pct)}
          </span>
          <span style={{ fontSize: '11px', color: '#475569' }}>avg return, 10d</span>
        </div>
      ) : (
        <div style={{ fontSize: '12px', color: '#475569' }}>No 10-day outcomes resolved yet in this window.</div>
      )}
      <div style={{ display: 'flex', gap: 16, marginTop: 10, fontSize: '11px', color: '#64748b', flexWrap: 'wrap' }}>
        <span>1d: {row.window_1d ? `${fmtPct(row.window_1d.avg_return_pct)} (${row.window_1d.n})` : '—'}</span>
        <span>2d: {row.window_2d ? `${fmtPct(row.window_2d.avg_return_pct)} (${row.window_2d.n})` : '—'}</span>
        <span>3d: {row.window_3d ? `${fmtPct(row.window_3d.avg_return_pct)} (${row.window_3d.n})` : '—'}</span>
        <span>5d: {row.window_5d ? `${fmtPct(row.window_5d.avg_return_pct)} (${row.window_5d.n})` : '—'}</span>
        <span>20d: {row.window_20d ? `${fmtPct(row.window_20d.avg_return_pct)} (${row.window_20d.n})` : '—'}</span>
      </div>
    </div>
  );
}

function BacktestWindowCell({ label, w }: { label: string; w: SqueezeAlertBacktestWindow }) {
  if (w == null || w.win_rate == null) {
    return (
      <div style={{ padding: '12px 14px', borderRadius: '8px', background: '#0d1424', border: '1px solid #1e293b' }}>
        <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>{label}</div>
        <div style={{ fontSize: '12px', color: '#475569' }}>{w?.note ?? 'No resolved candidates yet.'}</div>
      </div>
    );
  }
  const col = winRateColor(w.win_rate);
  return (
    <div style={{ padding: '12px 14px', borderRadius: '8px', background: '#0d1424', border: '1px solid #1e293b' }}>
      <div style={{ fontSize: '10px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontSize: '16px', fontWeight: 800, color: col }}>{(w.win_rate * 100).toFixed(0)}%</span>
        <span style={{ fontSize: '13px', fontWeight: 700, color: w.avg_return_pct != null && w.avg_return_pct >= 0 ? '#22c55e' : '#ef4444' }}>
          {fmtPct(w.avg_return_pct)}
        </span>
      </div>
      <div style={{ fontSize: '11px', color: '#64748b', marginTop: 2 }}>n={w.n}</div>
    </div>
  );
}

function BacktestSection() {
  const [weeksBack, setWeeksBack] = useState(52);
  const { data, isLoading, error } = useSWR(
    ['squeeze-alert-backtest', weeksBack],
    () => api.getSqueezeAlertBacktest({ weeks_back: weeksBack, min_samples: 15 }),
    { revalidateOnFocus: false }
  );

  return (
    <div style={{ marginTop: '28px', paddingTop: '20px', borderTop: '1px solid #1e293b' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>
          RETROACTIVE BACKTEST — SHORT SQUEEZE ONLY
        </div>
        <select
          value={weeksBack}
          onChange={e => setWeeksBack(Number(e.target.value))}
          style={{ padding: '4px 8px', borderRadius: '6px', fontSize: '11px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          {[26, 52, 104, 260].map(w => <option key={w} value={w}>{w} weeks back</option>)}
        </select>
      </div>
      <p style={{ fontSize: '11px', color: '#334155', marginBottom: '14px', maxWidth: 700 }}>
        Runs the SAME short-interest floor and intraday-move threshold the live alert uses
        against already-stored weekly fundamentals snapshots and daily price bars — a proxy for
        &quot;if this scan had run over the past N weeks,&quot; not a replay of the live minute-
        by-minute scan. Gamma Unwind can&apos;t be backtested at all — yfinance has no historical
        options open-interest data, and this app stores none either.
      </p>
      {isLoading && <div style={{ fontSize: '12px', color: '#475569' }}>Loading…</div>}
      {error && <div style={{ fontSize: '12px', color: '#f87171' }}>Failed to load the backtest.</div>}
      {data && (
        <>
          <div style={{ fontSize: '11px', color: '#64748b', marginBottom: 10 }}>
            {data.n_snapshots_qualifying} qualifying weekly snapshot{data.n_snapshots_qualifying === 1 ? '' : 's'}, {data.n_candidate_days} candidate day{data.n_candidate_days === 1 ? '' : 's'} found
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px' }}>
            <BacktestWindowCell label="5d" w={data.window_5d} />
            <BacktestWindowCell label="10d" w={data.window_10d} />
            <BacktestWindowCell label="20d" w={data.window_20d} />
          </div>
        </>
      )}
    </div>
  );
}

export default function SqueezeAlertPerformancePage() {
  const router = useRouter();

  const [authed, setAuthed] = useState(false);
  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (session.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const [daysBack, setDaysBack] = useState<typeof DAYS_OPTS[number]>(180);

  const { data, isLoading, error, mutate } = useSWR(
    authed ? ['squeeze-alert-performance', daysBack] : null,
    () => api.getSqueezeAlertPerformance({ days_back: daysBack, limit: 100 }),
    { revalidateOnFocus: false }
  );

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '24px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>
            Short Squeeze / Options Alert Performance
          </h1>
          <p style={{ fontSize: '12px', color: '#475569', maxWidth: 640 }}>
            Win rate and forward return if you bought right when each alert type&apos;s email
            first arrived — entry price is the live price captured at the moment the alert
            transitioned to &quot;newly qualifying,&quot; not a re-fire of an already-active setup.
          </p>
        </div>
        <button
          onClick={() => mutate()}
          style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}
        >
          ↺ Refresh
        </button>
      </div>

      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '20px' }}>
        <select
          value={daysBack}
          onChange={e => setDaysBack(Number(e.target.value) as typeof DAYS_OPTS[number])}
          style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          {DAYS_OPTS.map(d => <option key={d} value={d}>{d}d lookback</option>)}
        </select>
      </div>

      {isLoading && (
        <div style={{ textAlign: 'center', padding: '40px', color: '#475569', fontSize: '13px' }}>Loading…</div>
      )}
      {error && (
        <div style={{ padding: '16px 20px', borderRadius: '10px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', fontSize: '13px', color: '#f87171' }}>
          Failed to load squeeze/gamma-unwind alert performance data.
        </div>
      )}

      {data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px', marginBottom: '24px' }}>
            {data.by_alert_type.map(row => <TypeCard key={row.alert_type} row={row} />)}
          </div>

          <p style={{ fontSize: '11px', color: '#334155', marginBottom: '16px' }}>
            Gamma Unwind — Calls Dominant is a bullish options-positioning read; Gamma Unwind —
            Puts Dominant is this app&apos;s closest existing concept to &quot;option sell&quot;
            performance (a bearish-leaning options-positioning signal, not a stock-borrowing
            short) — win = price FELL past the cost hurdle for that row, the mirror of how
            Short Squeeze&apos;s BUY thesis is scored. Neither is a real GEX calculation — see
            the Gamma Unwind alert email&apos;s own explicit caveat.
          </p>

          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>
            RECENT ALERTS (MOST RECENT FIRST)
          </div>
          <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden' }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
                <thead>
                  <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                    {['Date', 'Type', 'Symbol', 'Alert Price', 'Entry Price', '1d', '2d', '3d', '5d', '10d', '20d'].map(h => (
                      <th key={h} style={{ textAlign: h === 'Date' || h === 'Type' || h === 'Symbol' ? 'left' : 'right', padding: '8px 12px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.recent_alerts.map((row, i) => (
                    <tr key={`${row.alert_type}-${row.symbol}-${row.fired_date}-${i}`} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '8px 12px', color: '#64748b' }}>{row.fired_date}</td>
                      <td style={{ padding: '8px 12px', color: '#94a3b8' }}>
                        {ALERT_TYPE_ROW_LABELS[row.alert_type] ?? row.alert_type}
                      </td>
                      <td style={{ padding: '8px 12px', fontWeight: 700, color: '#e2e8f0' }}>{row.symbol}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: '#64748b' }}>{row.alert_price.toFixed(2)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: '#64748b' }}>{row.entry_price != null ? row.entry_price.toFixed(2) : '—'}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (row.return_1d ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(row.return_1d)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (row.return_2d ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(row.return_2d)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (row.return_3d ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(row.return_3d)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (row.return_5d ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(row.return_5d)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (row.return_10d ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(row.return_10d)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: (row.return_20d ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(row.return_20d)}</td>
                    </tr>
                  ))}
                  {data.recent_alerts.length === 0 && (
                    <tr><td colSpan={11} style={{ padding: '20px', textAlign: 'center', color: '#475569' }}>No alerts fired in this window.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <BacktestSection />
    </div>
  );
}

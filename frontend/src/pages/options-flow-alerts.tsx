import { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import {
  api,
  type OptionsFlowAlertDirectionSummary,
  type OptionsFlowAlertRow,
  type OptionsFlowAlertBacktestResponse,
  type OptionsFlowAlertBacktestWindowStat,
} from '@/lib/api';
import { getSession } from '@/lib/auth';

// MPE-OPTIONS-FLOW-ALERT — dashboard for the real Unusual Whales unusual-options-activity
// alert. Direct follow-up to a user asking "857 alerts in one email, how do I use this, can we
// make it better?" — the backend fix (tighter thresholds, a per-(symbol,direction) cooldown,
// an email cap) still only ever sends a TOP-N slice by email; this page shows the FULL,
// uncapped OptionsFlowAlertOutcome ledger (every candidate is recorded regardless of the email
// cap — see check_options_flow_alerts()'s own docstring) with real filtering/sorting so a user
// can actually work through the day's full list, not just the top handful an inbox can hold.

const DAYS_OPTS = [7, 30, 90, 180] as const;

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function fmtMoney(v: number | null): string {
  if (v == null) return '—';
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function winRateColor(wr: number | null): string {
  if (wr == null) return '#475569';
  if (wr >= 0.55) return '#22c55e';
  if (wr >= 0.45) return '#f59e0b';
  return '#ef4444';
}

// AUD-SQUEEZE2-BEARISHRETURNCOLOR: OptionsFlowAlertDirectionSummary/Row (the LIVE performance
// endpoint) store the raw, unflipped price-move return — a bearish "win" (price correctly
// fell) is a negative number. Only used for the live performance cards/table below, NOT the
// backtest section — options_flow_alert_backtest() already sign-adjusts avg_return_pct itself
// (AUD-SQUEEZE2-MIXEDDIRECTIONRETURN) so a flat >=0=green is correct there.
function returnColor(returnPct: number | null | undefined, direction: string): string {
  if (returnPct == null) return '#475569';
  const isWin = direction === 'bearish' ? returnPct <= 0 : returnPct >= 0;
  return isWin ? '#22c55e' : '#ef4444';
}

function DirectionCard({ row }: { row: OptionsFlowAlertDirectionSummary }) {
  const primary = row.window_10d;
  const dirColor = row.direction === 'bullish' ? '#22c55e' : '#ef4444';
  return (
    <div style={{ padding: '16px 18px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
      <div style={{ fontSize: '13px', fontWeight: 700, color: dirColor, marginBottom: 4, textTransform: 'uppercase' }}>
        {row.direction}
      </div>
      <div style={{ fontSize: '11px', color: '#475569', marginBottom: 12 }}>
        {row.fired_count} alert{row.fired_count === 1 ? '' : 's'} fired in window
        {primary && ` · ${primary.n} outcome${primary.n === 1 ? '' : 's'} resolved (10d)`}
      </div>
      {primary ? (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <span style={{
            background: `${winRateColor(primary.win_rate)}22`, color: winRateColor(primary.win_rate),
            border: `1px solid ${winRateColor(primary.win_rate)}44`, borderRadius: 20, padding: '2px 9px', fontSize: 12, fontWeight: 700,
          }}>
            {primary.win_rate != null ? `${(primary.win_rate * 100).toFixed(0)}%` : '—'}
          </span>
          <span style={{ fontSize: '15px', fontWeight: 700, color: returnColor(primary.avg_return_pct, row.direction) }}>
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

function BacktestWindowCell({ label, w }: { label: string; w: OptionsFlowAlertBacktestWindowStat }) {
  if (w == null || w.win_rate == null) {
    return (
      <div style={{ padding: '10px 12px', borderRadius: '8px', background: '#0d1424', border: '1px solid #1e293b' }}>
        <div style={{ fontSize: '9.5px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 5 }}>{label}</div>
        <div style={{ fontSize: '11px', color: '#475569' }}>{w?.note ?? `n=${w?.n ?? 0}`}</div>
      </div>
    );
  }
  const col = winRateColor(w.win_rate);
  return (
    <div style={{ padding: '10px 12px', borderRadius: '8px', background: '#0d1424', border: '1px solid #1e293b' }}>
      <div style={{ fontSize: '9.5px', color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 5 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: '14px', fontWeight: 800, color: col }}>{(w.win_rate * 100).toFixed(0)}%</span>
        <span style={{ fontSize: '12px', fontWeight: 700, color: (w.avg_return_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(w.avg_return_pct)}</span>
      </div>
      <div style={{ fontSize: '10px', color: '#64748b', marginTop: 2 }}>n={w.n}</div>
    </div>
  );
}

function BacktestWindowRow({ w }: { w: { window_1d: OptionsFlowAlertBacktestWindowStat; window_2d: OptionsFlowAlertBacktestWindowStat; window_3d: OptionsFlowAlertBacktestWindowStat; window_5d: OptionsFlowAlertBacktestWindowStat; window_10d: OptionsFlowAlertBacktestWindowStat; window_20d: OptionsFlowAlertBacktestWindowStat } }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(90px, 1fr))', gap: '8px' }}>
      <BacktestWindowCell label="1d" w={w.window_1d} />
      <BacktestWindowCell label="2d" w={w.window_2d} />
      <BacktestWindowCell label="3d" w={w.window_3d} />
      <BacktestWindowCell label="5d" w={w.window_5d} />
      <BacktestWindowCell label="10d" w={w.window_10d} />
      <BacktestWindowCell label="20d" w={w.window_20d} />
    </div>
  );
}

function BacktestSection() {
  const [daysBack, setDaysBack] = useState(60);
  const [result, setResult] = useState<OptionsFlowAlertBacktestResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const runBacktest = async () => {
    setRunning(true);
    setRunError(null);
    try {
      const data = await api.getOptionsFlowAlertBacktest({ days_back: daysBack, min_samples: 10 });
      setResult(data);
    } catch {
      setRunError('Failed to run the backtest.');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div style={{ marginTop: '28px', paddingTop: '20px', borderTop: '1px solid #1e293b' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>
          HISTORICAL BACKTEST — SAME DIRECTION OR DIFFERENT? WHEN TO ENTER?
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            value={daysBack}
            onChange={e => setDaysBack(Number(e.target.value))}
            style={{ padding: '4px 8px', borderRadius: '6px', fontSize: '11px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
          >
            {[14, 30, 60, 90].map(d => <option key={d} value={d}>{d}d back</option>)}
          </select>
          <button
            onClick={runBacktest}
            disabled={running}
            style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: running ? 'default' : 'pointer', border: '1px solid #6d28d9', background: running ? 'transparent' : 'rgba(109,40,217,0.15)', color: '#a78bfa' }}
          >
            {running ? 'Running… (can take a minute)' : 'Run backtest'}
          </button>
        </div>
      </div>
      <p style={{ fontSize: '11px', color: '#334155', marginBottom: '14px', maxWidth: 760 }}>
        A GENUINE historical replay — Unusual Whales retains real flow-alert history (confirmed
        live, not a proxy) — using the SAME filter the live alert uses today. Answers: does
        following the alert&apos;s own implied direction actually work (by_direction), does a
        sweep/bigger-volume-OI-ratio signal predict better (by_sweep / by_volume_oi_band)?
        Capped at 1,000 rows/symbol (UW&apos;s own newest-first pagination) — see
        get_historical_flow_alerts()&apos;s own docstring for the exact bound.
      </p>

      {runError && <div style={{ fontSize: '12px', color: '#f87171', marginBottom: 12 }}>{runError}</div>}

      {result && result.reason && (
        <div style={{ fontSize: '12px', color: '#94a3b8', padding: '16px', textAlign: 'center' }}>
          No result — {result.reason.replace(/_/g, ' ')}.
        </div>
      )}

      {result && !result.reason && (
        <>
          <div style={{ fontSize: '11px', color: '#64748b', marginBottom: 14 }}>
            {result.n_alerts_replayed} real alert{result.n_alerts_replayed === 1 ? '' : 's'} replayed
            across {result.n_symbols_scanned} symbol{result.n_symbols_scanned === 1 ? '' : 's'}, last {result.days_back} days.
          </div>

          <div style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '11px', fontWeight: 700, color: '#94a3b8', marginBottom: 8 }}>
              BY DIRECTION — does following the alert&apos;s own implied direction work?
            </div>
            {result.by_direction.map(row => (
              <div key={row.direction} style={{ marginBottom: 10 }}>
                <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: 6, color: row.direction === 'bullish' ? '#22c55e' : '#ef4444', textTransform: 'uppercase' }}>
                  {row.direction} <span style={{ color: '#475569', fontWeight: 400, textTransform: 'none' }}>({row.n_alerts} alerts)</span>
                </div>
                <BacktestWindowRow w={row} />
              </div>
            ))}
          </div>

          <div style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '11px', fontWeight: 700, color: '#94a3b8', marginBottom: 8 }}>
              BY SWEEP — does urgent, cross-exchange activity predict better?
            </div>
            {result.by_sweep.map(row => (
              <div key={String(row.has_sweep)} style={{ marginBottom: 10 }}>
                <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: 6, color: '#94a3b8' }}>
                  {row.has_sweep ? '⚡ Sweep' : 'Non-sweep'} <span style={{ color: '#475569', fontWeight: 400 }}>({row.n_alerts} alerts)</span>
                </div>
                <BacktestWindowRow w={row} />
              </div>
            ))}
          </div>

          <div>
            <div style={{ fontSize: '11px', fontWeight: 700, color: '#94a3b8', marginBottom: 8 }}>
              BY VOLUME/OI RATIO — does a bigger, more unusual signal predict better?
            </div>
            {result.by_volume_oi_band.map(row => (
              <div key={row.band} style={{ marginBottom: 10 }}>
                <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: 6, color: '#94a3b8' }}>
                  {row.band} <span style={{ color: '#475569', fontWeight: 400 }}>({row.n_alerts} alerts)</span>
                </div>
                <BacktestWindowRow w={row} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

type DirFilter = 'all' | 'bullish' | 'bearish';
type SortKey = 'fired_date' | 'total_premium' | 'volume_oi_ratio';

export default function OptionsFlowAlertsPage() {
  const router = useRouter();

  const [authed, setAuthed] = useState(false);
  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (session.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const [daysBack, setDaysBack] = useState<typeof DAYS_OPTS[number]>(30);
  const [dirFilter, setDirFilter] = useState<DirFilter>('all');
  const [sweepOnly, setSweepOnly] = useState(false);
  const [minPremium, setMinPremium] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>('fired_date');

  const { data, isLoading, error, mutate } = useSWR(
    authed ? ['options-flow-alert-performance', daysBack] : null,
    () => api.getOptionsFlowAlertPerformance({ days_back: daysBack, limit: 500 }),
    { revalidateOnFocus: false, refreshInterval: 60_000 }
  );

  const filteredRows = useMemo(() => {
    if (!data) return [];
    let rows = data.recent_alerts;
    if (dirFilter !== 'all') rows = rows.filter(r => r.direction === dirFilter);
    if (sweepOnly) rows = rows.filter(r => r.has_sweep);
    if (minPremium > 0) rows = rows.filter(r => (r.total_premium ?? 0) >= minPremium);
    const sorted = [...rows];
    if (sortKey === 'total_premium') sorted.sort((a, b) => (b.total_premium ?? 0) - (a.total_premium ?? 0));
    else if (sortKey === 'volume_oi_ratio') sorted.sort((a, b) => (b.volume_oi_ratio ?? 0) - (a.volume_oi_ratio ?? 0));
    else sorted.sort((a, b) => (b.fired_date + b.symbol).localeCompare(a.fired_date + a.symbol));
    return sorted;
  }, [data, dirFilter, sweepOnly, minPremium, sortKey]);

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>
            🎯 Unusual Options Activity
          </h1>
          <p style={{ fontSize: '12px', color: '#475569', maxWidth: 720 }}>
            Real Unusual Whales flow-alerts — a rule-based sweep/repeated-hits detection over
            the full options tape, direction derived from the real ask-side/bid-side premium
            split (not a naive call=bullish/put=bearish read — a bid-side-dominant PUT means
            aggressive put SELLING, a bullish bet). This is the FULL, uncapped list; the email
            only sends the top ~12 by premium size per cycle, with a per-(symbol, direction)
            30-minute cooldown so the same setup can&apos;t re-alert faster than that.
          </p>
        </div>
        <button
          onClick={() => mutate()}
          style={{ padding: '6px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#94a3b8' }}
        >
          ↺ Refresh
        </button>
      </div>

      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '20px', alignItems: 'center' }}>
        <select
          value={daysBack}
          onChange={e => setDaysBack(Number(e.target.value) as typeof DAYS_OPTS[number])}
          style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          {DAYS_OPTS.map(d => <option key={d} value={d}>{d}d lookback</option>)}
        </select>
        <select
          value={dirFilter}
          onChange={e => setDirFilter(e.target.value as DirFilter)}
          style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          <option value="all">All directions</option>
          <option value="bullish">Bullish only</option>
          <option value="bearish">Bearish only</option>
        </select>
        <select
          value={minPremium}
          onChange={e => setMinPremium(Number(e.target.value))}
          style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          <option value={0}>Any premium</option>
          <option value={250_000}>$250K+ premium</option>
          <option value={500_000}>$500K+ premium</option>
          <option value={1_000_000}>$1M+ premium</option>
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '12px', color: '#94a3b8', cursor: 'pointer' }}>
          <input type="checkbox" checked={sweepOnly} onChange={e => setSweepOnly(e.target.checked)} />
          Sweeps only
        </label>
        <select
          value={sortKey}
          onChange={e => setSortKey(e.target.value as SortKey)}
          style={{ padding: '6px 10px', borderRadius: '6px', fontSize: '12px', background: '#0d1424', border: '1px solid #1e293b', color: '#94a3b8' }}
        >
          <option value="fired_date">Sort: most recent</option>
          <option value="total_premium">Sort: largest premium</option>
          <option value="volume_oi_ratio">Sort: highest volume/OI ratio</option>
        </select>
      </div>

      {isLoading && (
        <div style={{ textAlign: 'center', padding: '40px', color: '#475569', fontSize: '13px' }}>Loading…</div>
      )}
      {error && (
        <div style={{ padding: '16px 20px', borderRadius: '10px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', fontSize: '13px', color: '#f87171' }}>
          Failed to load options-flow alert data.
        </div>
      )}

      {data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px', marginBottom: '20px' }}>
            {data.by_direction.map(row => <DirectionCard key={row.direction} row={row} />)}
          </div>

          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '8px', flexWrap: 'wrap', gap: 8 }}>
            <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>
              {filteredRows.length} OF {data.recent_alerts.length} ALERTS SHOWN
            </div>
          </div>

          <div style={{ borderRadius: '10px', border: '1px solid #1e293b', overflow: 'hidden' }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12.5px' }}>
                <thead>
                  <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
                    {['Date', 'Symbol', 'Dir', 'Type', 'Strike', 'Expiry', 'Premium', 'Vol/OI', 'Side', 'Sweep', '10d', 'Win?'].map(h => (
                      <th key={h} style={{ textAlign: ['Date', 'Symbol'].includes(h) ? 'left' : 'right', padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: '10.5px', textTransform: 'uppercase', letterSpacing: '0.04em', borderBottom: '1px solid #1e293b' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((row: OptionsFlowAlertRow, i) => (
                    <tr key={`${row.option_chain}-${row.fired_date}-${i}`} style={{ borderBottom: '1px solid #1e293b' }}>
                      <td style={{ padding: '8px 10px', color: '#64748b' }}>{row.fired_date}</td>
                      <td style={{ padding: '8px 10px', fontWeight: 700, color: '#e2e8f0' }}>{row.symbol}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: row.direction === 'bullish' ? '#22c55e' : '#ef4444' }}>
                        {row.direction === 'bullish' ? '▲' : '▼'}
                      </td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', color: '#94a3b8', textTransform: 'uppercase' }}>{row.option_type}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', color: '#64748b' }}>{row.strike != null ? `$${row.strike.toFixed(2)}` : '—'}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', color: '#64748b' }}>{row.expiry ?? '—'}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: '#e2e8f0' }}>{fmtMoney(row.total_premium)}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', color: '#64748b' }}>{row.volume_oi_ratio != null ? `${row.volume_oi_ratio.toFixed(1)}x` : '—'}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', fontSize: '11px', color: row.ask_side_dominant ? '#22c55e' : '#f59e0b' }}>
                        {row.ask_side_dominant ? 'Ask (buy)' : 'Bid (sell)'}
                      </td>
                      <td style={{ padding: '8px 10px', textAlign: 'right' }}>{row.has_sweep ? '⚡' : ''}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right', color: returnColor(row.return_10d, row.direction) }}>{fmtPct(row.return_10d)}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                        {row.is_correct_10d == null ? <span style={{ color: '#475569' }}>—</span> : row.is_correct_10d ? <span style={{ color: '#22c55e' }}>✓</span> : <span style={{ color: '#ef4444' }}>✗</span>}
                      </td>
                    </tr>
                  ))}
                  {filteredRows.length === 0 && (
                    <tr><td colSpan={12} style={{ padding: '20px', textAlign: 'center', color: '#475569' }}>No alerts match these filters.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ marginTop: '20px', padding: '14px 16px', borderRadius: '10px', background: 'rgba(148,163,184,0.05)', border: '1px solid #1e293b' }}>
            <div style={{ fontSize: '11px', fontWeight: 700, color: '#94a3b8', marginBottom: 8 }}>HOW TO READ THIS</div>
            <ul style={{ fontSize: '11.5px', color: '#64748b', margin: 0, paddingLeft: '18px', lineHeight: 1.6 }}>
              <li><strong style={{ color: '#94a3b8' }}>Side</strong>: &quot;Ask (buy)&quot; means the trade printed aggressively at the ask — someone paid up to get in fast. &quot;Bid (sell)&quot; means someone sold aggressively at the bid.</li>
              <li>A CALL bought at the ask, or a PUT sold at the bid, both read <span style={{ color: '#22c55e', fontWeight: 700 }}>bullish</span>. A PUT bought at the ask, or a CALL sold at the bid, both read <span style={{ color: '#ef4444', fontWeight: 700 }}>bearish</span>.</li>
              <li><strong style={{ color: '#94a3b8' }}>Vol/OI</strong> — today&apos;s volume vs. existing open interest. Above 1x means MORE contracts traded today than were already open — new positioning, not just existing holders trading among themselves.</li>
              <li><strong style={{ color: '#94a3b8' }}>⚡ Sweep</strong> — the order hit multiple exchanges near-simultaneously, typical of someone trying to fill a large order fast before the price moves against them.</li>
              <li>This reports a <strong style={{ color: '#94a3b8' }}>measured fact</strong> — large, urgent options positioning was detected — never a prediction the stock will actually move. The win-rate columns only populate once &gt;=30 resolved outcomes exist for that direction. Not financial advice.</li>
            </ul>
          </div>
        </>
      )}

      <BacktestSection />
    </div>
  );
}

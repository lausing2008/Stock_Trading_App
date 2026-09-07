/**
 * Backtest Results — /backtest-results. Admin-only.
 *
 * The platform has 11 admin backtest endpoints under /paper-portfolio/backtest/* and, until
 * now, ZERO UI for any of them — they were curl-only research tools. This surfaces the two
 * gate-replay ones (BT-1 full-history replay, BT-2 replay fidelity) built 2026-09-07.
 *
 * ⚠ THE SINGLE MOST IMPORTANT THING THIS PAGE MUST COMMUNICATE — and the reason the scope
 * banner below is not dismissible:
 *
 * These backtests replay `_should_enter()`, which is the DECISION-ENGINE-OUTAGE FALLBACK gate,
 * NOT the live authoritative one. `_DEFAULT_CONFIG["decision_engine_mode"]` is "primary"
 * (paper_trading_engine.py:656), meaning decision-engine's own check_hard_rejects()/
 * compute_score() decides real entries; _should_enter() only runs when DE is unreachable. A
 * result here describes what the FALLBACK gate would have admitted — it is emphatically not a
 * measurement of live entry behaviour, and reading it as one would be a serious
 * misinterpretation.
 *
 * They also test the AI-SIGNAL → PAPER-TRADE-ENTRY path only. They do NOT test the AI Signal
 * email ALERT (check_signal_alerts(), a different gate: _is_conviction_buy()) and do NOT test
 * any OPTIONS alert (squeeze/gamma/options-flow — those have their own separate outcome tables
 * and their own dashboards, linked below).
 */
import { useState } from 'react';
import { useRouter } from 'next/router';
import { useEffect } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { api, type ReplayFidelityResponse, type ReplayFullHistoryResponse } from '@/lib/api';
import { getSession } from '@/lib/auth';

const STYLES = ['SWING', 'GROWTH', 'SHORT', 'LONG'] as const;
const MARKETS = ['US', 'HK'] as const;

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

function winRateColor(wr: number | null | undefined): string {
  if (wr == null) return '#475569';
  if (wr >= 0.55) return '#22c55e';
  if (wr >= 0.45) return '#f59e0b';
  return '#ef4444';
}

/** signal_outcomes.pct_return is a FRACTION (not x100) — the same column name on paper_trades
 * IS x100, a documented 100x trap in this codebase. These values come from signal_outcomes. */
function returnColor(v: number | null | undefined): string {
  if (v == null) return '#475569';
  return v >= 0 ? '#22c55e' : '#ef4444';
}

function Card({ title, children, hint }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '16px 18px', marginBottom: 16 }}>
      <div style={{ marginBottom: 10 }}>
        <h2 style={{ fontSize: 14, fontWeight: 700, color: '#cbd5e1', margin: 0 }}>{title}</h2>
        {hint && <p style={{ fontSize: 11.5, color: '#64748b', margin: '4px 0 0' }}>{hint}</p>}
      </div>
      {children}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div style={{ minWidth: 96 }}>
      <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 700, color: color ?? '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  );
}

function SkipReasons({ counts }: { counts: Record<string, number> | undefined }) {
  const entries = Object.entries(counts ?? {});
  if (entries.length === 0) return null;
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>
        Why the gate rejected
      </div>
      {entries.map(([reason, n]) => (
        <div key={reason} style={{ display: 'flex', gap: 10, fontSize: 11.5, color: '#94a3b8', padding: '3px 0', borderBottom: '1px solid #0f172a' }}>
          <span style={{ color: '#e2e8f0', fontWeight: 700, minWidth: 34, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{n}</span>
          <span style={{ wordBreak: 'break-word' }}>{reason}</span>
        </div>
      ))}
    </div>
  );
}

export default function BacktestResultsPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [style, setStyle] = useState<string>('SWING');
  const [market, setMarket] = useState<string>('US');

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    if (s.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const { data: fullHistory, isLoading: fhLoading } = useSWR(
    authed ? `bt-full-${style}-${market}` : null,
    () => api.getReplayFullHistory({ style, market }),
    { revalidateOnFocus: false },
  );
  const { data: fidelity, isLoading: fidLoading } = useSWR(
    authed ? `bt-fid-${style}-${market}` : null,
    () => api.getReplayFidelity({ style, market, window_days: 120 }),
    { revalidateOnFocus: false },
  );

  if (!authed) return null;

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '24px 0 60px' }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', marginBottom: 6 }}>Backtest Results</h1>
        <p style={{ fontSize: 13, color: '#64748b', maxWidth: 760 }}>
          Gate-replay backtests over persisted signal history. Research output — nothing here writes
          config or promotes a parameter.
        </p>
      </div>

      {/* Scope banner — deliberately prominent and not dismissible. Misreading these numbers as
          live entry behaviour or as alert performance is the main risk this page carries. */}
      <div style={{ padding: '14px 16px', borderRadius: 10, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', marginBottom: 20 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: '#f87171', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>
          What these numbers are — and are not
        </div>
        <div style={{ fontSize: 12.5, color: '#cbd5e1', lineHeight: 1.7 }}>
          These replay <strong style={{ color: '#e2e8f0' }}>_should_enter()</strong>, the
          decision-engine-<em>outage fallback</em> gate — <strong style={{ color: '#f87171' }}>not the
          live authoritative one</strong>. <code style={{ color: '#f59e0b' }}>decision_engine_mode</code>{' '}
          defaults to <code style={{ color: '#f59e0b' }}>&quot;primary&quot;</code>, so decision-engine
          decides real entries and this gate only runs when DE is unreachable.
          <br /><br />
          Scope: the <strong style={{ color: '#e2e8f0' }}>AI Signal → paper-trade entry</strong> path
          only. <strong>Not</strong> the AI Signal email alert (a different gate,{' '}
          <code style={{ color: '#f59e0b' }}>_is_conviction_buy()</code>), and{' '}
          <strong>not</strong> any options alert — those have their own outcome tables and pages:{' '}
          <Link href="/squeeze-alert-performance" style={{ color: '#38bdf8', textDecoration: 'none' }}>Squeeze Alert Performance</Link>
          {' · '}
          <Link href="/options-flow-alerts" style={{ color: '#38bdf8', textDecoration: 'none' }}>Options Flow Alerts</Link>.
          <br /><br />
          Replay is also <strong>regime-blind</strong> (no historical regime data exists) and models{' '}
          <strong>no portfolio state</strong> (max positions, sector caps, cash, daily caps) — so it
          over-counts entries a real book could not all have taken.
        </div>
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 18, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {STYLES.map(s => (
            <button key={s} onClick={() => setStyle(s)} style={{
              padding: '5px 13px', fontSize: 12, fontWeight: 700, cursor: 'pointer', borderRadius: 6,
              background: style === s ? 'rgba(129,140,248,0.15)' : 'transparent',
              border: `1px solid ${style === s ? 'rgba(129,140,248,0.4)' : '#1e293b'}`,
              color: style === s ? '#818cf8' : '#64748b',
            }}>{s}</button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {MARKETS.map(m => (
            <button key={m} onClick={() => setMarket(m)} style={{
              padding: '5px 13px', fontSize: 12, fontWeight: 700, cursor: 'pointer', borderRadius: 6,
              background: market === m ? 'rgba(56,189,248,0.15)' : 'transparent',
              border: `1px solid ${market === m ? 'rgba(56,189,248,0.4)' : '#1e293b'}`,
              color: market === m ? '#38bdf8' : '#64748b',
            }}>{m}</button>
          ))}
        </div>
      </div>

      {/* BT-1 */}
      <Card
        title="BT-1 — Full-history gate replay"
        hint="Current gates replayed across all persisted signal history (from 2026-05-25, the first date a frozen sig.reasons snapshot exists). Outcomes are real forward returns; only the decision is recomputed."
      >
        {fhLoading && <div style={{ fontSize: 12, color: '#475569' }}>Running replay…</div>}
        {!fhLoading && fullHistory?.skipped_reason && (
          <div style={{ fontSize: 12, color: '#f59e0b' }}>{fullHistory.skipped_reason}</div>
        )}
        {!fhLoading && fullHistory && !fullHistory.skipped_reason && (
          <>
            <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', marginBottom: 12 }}>
              <Stat label="Signals seen" value={fullHistory.n_signals_seen.toLocaleString()} />
              <Stat label="Entered" value={fullHistory.n_entered.toLocaleString()} />
              <Stat label="Win rate" value={pct(fullHistory.win_rate)} color={winRateColor(fullHistory.win_rate)} />
              <Stat label="Avg return" value={pct(fullHistory.avg_return_pct, 2)} color={returnColor(fullHistory.avg_return_pct)} />
              <Stat label="Weeks" value={fullHistory.n_distinct_weeks} />
              <Stat label="Max / week" value={fullHistory.max_entries_in_one_week} />
            </div>
            {/* Clustering note is rendered as a warning, not a footnote — a raw entry count
                hides correlation, and this platform has already been burned by a 9.1% win rate
                on n=11 where 9 fired inside a single 8-day window. */}
            <div style={{
              padding: '10px 12px', borderRadius: 8, fontSize: 12, lineHeight: 1.6,
              background: fullHistory.effective_sample_note.startsWith('HIGHLY CLUSTERED')
                ? 'rgba(239,68,68,0.08)' : 'rgba(56,189,248,0.06)',
              border: `1px solid ${fullHistory.effective_sample_note.startsWith('HIGHLY CLUSTERED')
                ? 'rgba(239,68,68,0.3)' : 'rgba(56,189,248,0.2)'}`,
              color: '#cbd5e1',
            }}>
              <strong style={{ color: '#e2e8f0' }}>Effective sample: </strong>
              {fullHistory.effective_sample_note}
            </div>
            <SkipReasons counts={fullHistory.skip_reason_counts} />
          </>
        )}
      </Card>

      {/* BT-2 */}
      <Card
        title="BT-2 — Replay fidelity vs. real paper trades"
        hint="Does the replay reproduce the entries the live engine actually made? Judge on recall — a replayed SKIP on a real trade is the suspicious direction; a replayed ENTER on a skipped signal is often just a full book."
      >
        {fidLoading && <div style={{ fontSize: 12, color: '#475569' }}>Checking fidelity…</div>}
        {!fidLoading && fidelity?.skipped_reason && (
          <div style={{ fontSize: 12, color: '#f59e0b' }}>{fidelity.skipped_reason}</div>
        )}
        {!fidLoading && fidelity && !fidelity.skipped_reason && (
          <>
            <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', marginBottom: 12 }}>
              <Stat label="Real trades" value={fidelity.n_real_trades} />
              <Stat label="Visible to replay" value={fidelity.n_real_matched_in_replay} />
              <Stat label="Replay entered" value={fidelity.n_replay_entered} color="#22c55e" />
              <Stat label="Replay skipped" value={fidelity.n_replay_skipped} color="#f87171" />
              <Stat label="Recall" value={pct(fidelity.recall_on_real_trades)} color={winRateColor(fidelity.recall_on_real_trades)} />
            </div>
            <div style={{ fontSize: 11.5, color: '#64748b', lineHeight: 1.6 }}>
              Low recall is <strong style={{ color: '#94a3b8' }}>not automatically a defect</strong> —
              gates have been retuned since these trades were taken (e.g. the anti-chasing ROC10
              filter shipped 2026-09-05), so the replay applies <em>today&apos;s</em> gates to{' '}
              <em>yesterday&apos;s</em> decisions. Read the rejection reasons below before treating a
              low number as a bug.
            </div>
            <SkipReasons counts={fidelity.skip_reason_counts} />
          </>
        )}
      </Card>

      <div style={{ fontSize: 11, color: '#334155', marginTop: 18, lineHeight: 1.7 }}>
        Other backtest endpoints exist without UI yet (min-entry-score, blocked-entry-scores,
        calibration-feedback, extended-gate, portfolio, drawdown-breaker-sweep,
        risk-per-trade-sweep, open-risk-cap-sweep, scorer-sweep) — all admin-only, all
        research-only, none wired to a promotion gate.
      </div>
    </div>
  );
}

import { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type SchedulerJob, type MlModelMetric, type SignalSummary, type ServiceHealthReport } from '@/lib/api';
import { getSession } from '@/lib/auth';

const JOB_META: Record<string, { label: string; maxAgeDays: number; desc: string }> = {
  // Key scheduled jobs
  weekly_refresh:            { label: 'Weekly Full Refresh',         maxAgeDays: 8,  desc: 'Force re-ingest all stocks — 3 years of OHLCV (Sun 14:00 PST)' },
  us_post_close:             { label: 'US Post-Close',               maxAgeDays: 3,  desc: 'Final daily bar + ML retrain for all US stocks (Mon–Fri 16:30 ET)' },
  hk_post_close:             { label: 'HK Post-Close',               maxAgeDays: 3,  desc: 'Final daily bar + ML retrain for all HK stocks (Mon–Fri 16:30 HKT)' },
  paper_trading:             { label: 'Paper Trading Engine',        maxAgeDays: 2,  desc: 'Autonomous GROWTH-style paper trade step (runs after each US refresh)' },
  // Morning digest — T232-UI2: the two per-market jobs (morning_digest_us/hk) were merged into
  // one combined job; this page still looked for the old keys, which stopped being written and
  // went permanently stale ("1 day ago" and climbing) while the real job's status was invisible.
  morning_digest_combined:   { label: 'Morning Digest (HK + US)',    maxAgeDays: 2,  desc: 'Combined email digest of top signals + open positions, both markets (Mon–Fri 08:50 HKT)' },
  // Intraday refresh (full pipeline)
  us_refresh:                { label: 'US Intraday Refresh',         maxAgeDays: 2,  desc: 'Prices + K-Score rankings + signals every 5 min (US market hours)' },
  hk_refresh:                { label: 'HK Intraday Refresh',         maxAgeDays: 2,  desc: 'Prices + K-Score rankings + signals every 5 min (HK market hours)' },
  us_open_burst:             { label: 'US Open Burst',               maxAgeDays: 2,  desc: 'Dense refresh at open — 5 runs 09:25–09:45 ET' },
  us_intra:                  { label: 'US Intraday',                 maxAgeDays: 2,  desc: 'Prices + rankings + signals every 5 min (10:00–15:00 ET)' },
  us_close_burst:            { label: 'US Close Burst',              maxAgeDays: 2,  desc: 'Dense refresh at close — every 5 min 15:30–16:15 ET' },
  us_5m_intraday:            { label: 'US 5m Bars',                  maxAgeDays: 2,  desc: '5-minute intraday bar ingestion only (09:30–16:00 ET, no signals)' },
  hk_open_burst:             { label: 'HK Open Burst',               maxAgeDays: 2,  desc: 'Dense refresh at open — 5 runs 09:25–09:45 HKT' },
  hk_intra:                  { label: 'HK Intraday',                 maxAgeDays: 2,  desc: 'Prices + rankings + signals every 5 min (10:00–15:00 HKT, skip lunch)' },
  hk_close_burst:            { label: 'HK Close Burst',              maxAgeDays: 2,  desc: 'Dense refresh at close — every 5 min 15:30–16:15 HKT' },
  hk_5m_intraday:            { label: 'HK 5m Bars',                  maxAgeDays: 2,  desc: '5-minute intraday bar ingestion only (09:30–16:00 HKT, skip lunch)' },
  // Always-on background jobs
  live_price_cache_refresh:  { label: 'Live Price Cache',            maxAgeDays: 1,  desc: 'Writes live prices to Redis every 1 min during market hours (US/HK 09–17)' },
  price_alert_check:         { label: 'Price Alert Check',           maxAgeDays: 1,  desc: 'Checks user alert thresholds against Redis live cache every 1 min' },
  // Maintenance
  paper_portfolio_digest:    { label: 'Portfolio Digest Email',      maxAgeDays: 2,  desc: 'After-market portfolio digest email to all users — 17:00 ET on trading days' },
  db_purge_weekly:           { label: 'DB Weekly Purge',             maxAgeDays: 8,  desc: 'Deletes prices_5m + scheduler_jobs rows older than 90 days (Sun 15:00 PST)' },
  tune_all_sent:             { label: 'Optuna Tune-All',             maxAgeDays: 8,  desc: 'Weekly XGBoost hyperparameter tuning sent to ML service (Optuna search)' },
  calibrate_ta_weights_sent: { label: 'TA Weight Calibration',       maxAgeDays: 8,  desc: 'Weekly TA logistic regression calibration — updates ta_weights.json' },
  rl_agent_train:            { label: 'RL Agent Train',              maxAgeDays: 8,  desc: 'Contextual bandit (Ridge Q-function) trained on closed paper trades. Requires ≥50 closed trades — shows "skipped" until paper trading accumulates enough history.' },
};

function relTime(iso: string): string {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function JobCard({ job }: { job: SchedulerJob }) {
  const meta = JOB_META[job.job] ?? { label: job.job, maxAgeDays: 7, desc: '' };
  const ageDays = (Date.now() - new Date(job.last_run).getTime()) / 86400000;
  const stale = ageDays > meta.maxAgeDays;

  const isSkipped = job.status.startsWith('skipped:') || job.status.startsWith('Need ');
  const statusColor  = job.status === 'ok' ? '#4ade80' : job.status === 'error' ? '#f87171' : isSkipped ? '#fbbf24' : '#94a3b8';
  const statusBg     = job.status === 'ok' ? 'rgba(74,222,128,0.08)' : job.status === 'error' ? 'rgba(239,68,68,0.1)' : isSkipped ? 'rgba(251,191,36,0.08)' : 'rgba(148,163,184,0.06)';
  const statusBorder = job.status === 'ok' ? 'rgba(74,222,128,0.2)'  : job.status === 'error' ? 'rgba(239,68,68,0.3)'   : isSkipped ? 'rgba(251,191,36,0.2)'    : 'rgba(148,163,184,0.15)';
  const statusLabel  = job.status === 'ok' ? '✓ OK' : job.status === 'error' ? '✗ Error' : isSkipped ? '⊘ Skipped' : '– Unknown';

  return (
    <div style={{
      padding: '14px 16px', borderRadius: '10px',
      background: stale ? 'rgba(251,191,36,0.04)' : '#0d1424',
      border: `1px solid ${stale ? 'rgba(251,191,36,0.3)' : job.status === 'error' ? 'rgba(239,68,68,0.3)' : '#1e293b'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '8px' }}>
        <div>
          <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: '2px' }}>{meta.label}</div>
          {meta.desc && <div style={{ fontSize: '10px', color: '#334155' }}>{meta.desc}</div>}
        </div>
        <span style={{
          fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '5px',
          color: statusColor, background: statusBg, border: `1px solid ${statusBorder}`,
          whiteSpace: 'nowrap', marginLeft: '12px', flexShrink: 0,
        }}>
          {statusLabel}
        </span>
      </div>
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', fontSize: '11px' }}>
        <div>
          <span style={{ color: '#475569' }}>Last run </span>
          <span style={{ color: stale ? '#fbbf24' : '#94a3b8', fontWeight: 600 }}>{relTime(job.last_run)}</span>
          {stale && <span style={{ color: '#fbbf24', marginLeft: '4px' }}>⚠ stale</span>}
        </div>
        {job.duration_s > 0 && (
          <div>
            <span style={{ color: '#475569' }}>Duration </span>
            <span style={{ color: '#64748b' }}>
              {job.duration_s >= 60 ? `${Math.floor(job.duration_s / 60)}m ${Math.floor(job.duration_s % 60)}s` : `${job.duration_s.toFixed(1)}s`}
            </span>
          </div>
        )}
      </div>
      {job.error && (
        <div style={{ marginTop: '8px', padding: '6px 10px', borderRadius: '5px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', fontSize: '11px', color: '#f87171', fontFamily: 'monospace', wordBreak: 'break-all' }}>
          {job.error}
        </div>
      )}
    </div>
  );
}

function MlRow({ m }: { m: MlModelMetric }) {
  const auc = m.test_auc ?? 0;
  const aucColor = auc >= 0.65 ? '#4ade80' : auc >= 0.55 ? '#fbbf24' : '#f87171';
  const overfit = (m.overfit_gap ?? 0) > 0.1;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '6px 10px', borderRadius: '6px',
      background: overfit ? 'rgba(239,68,68,0.04)' : '#080f1e',
      border: `1px solid ${overfit ? 'rgba(239,68,68,0.2)' : '#1e293b'}`,
    }}>
      <span style={{ fontSize: '12px', fontWeight: 600, color: '#cbd5e1' }}>{m.symbol}</span>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <span style={{ fontSize: '11px', fontWeight: 700, color: aucColor }}>AUC {auc.toFixed(3)}</span>
        {m.cv_auc != null && <span style={{ fontSize: '10px', color: '#475569' }}>CV {m.cv_auc.toFixed(3)}</span>}
        {overfit && <span style={{ fontSize: '10px', color: '#f87171' }}>⚠ overfit</span>}
      </div>
    </div>
  );
}

export default function AdminHealthPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    if (s.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const { data, isLoading, mutate } = useSWR(
    authed ? 'scheduler-status' : null,
    () => api.schedulerStatus(),
    { revalidateOnFocus: false, refreshInterval: 30_000 },
  );

  const { data: mlData } = useSWR(
    authed ? 'ml-metrics-all' : null,
    () => api.mlMetrics('xgboost'),
    { revalidateOnFocus: false },
  );

  const { data: signalsData } = useSWR<SignalSummary[]>(
    authed ? 'signals-SWING' : null,
    () => api.allSignals('SWING'),
    { revalidateOnFocus: false, refreshInterval: 120_000 },
  );

  const { data: healthData, mutate: mutateHealth } = useSWR<ServiceHealthReport>(
    authed ? 'health-deep' : null,
    () => api.healthDeep(),
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );

  const signalCounts = useMemo(() => {
    const counts: Record<string, number> = { BUY: 0, SELL: 0, WAIT: 0, HOLD: 0 };
    for (const s of signalsData ?? []) {
      if (s.signal in counts) counts[s.signal]++;
    }
    return counts;
  }, [signalsData]);

  const jobs = data?.jobs ?? [];
  const errorCount = jobs.filter(j => j.status === 'error').length;
  const staleCount = jobs.filter(j => {
    const meta = JOB_META[j.job];
    if (!meta) return false;
    return (Date.now() - new Date(j.last_run).getTime()) / 86400000 > meta.maxAgeDays;
  }).length;

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '24px 0' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>System Health</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>Scheduler job status — refreshes every 30s</p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {errorCount > 0 && (
            <span style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 700, color: '#f87171', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)' }}>
              {errorCount} error{errorCount !== 1 ? 's' : ''}
            </span>
          )}
          {staleCount > 0 && (
            <span style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 700, color: '#fbbf24', background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.3)' }}>
              {staleCount} stale
            </span>
          )}
          {errorCount === 0 && staleCount === 0 && jobs.length > 0 && (
            <span style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 700, color: '#4ade80', background: 'rgba(74,222,128,0.08)', border: '1px solid rgba(74,222,128,0.2)' }}>
              All healthy
            </span>
          )}
          <button
            onClick={() => mutate()}
            style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#475569' }}
          >
            ↺ Refresh
          </button>
        </div>
      </div>

      {isLoading && (
        <div style={{ textAlign: 'center', padding: '40px', color: '#475569', fontSize: '13px' }}>Loading…</div>
      )}

      {!isLoading && jobs.length === 0 && (
        <div style={{ padding: '20px 24px', borderRadius: '10px', background: 'rgba(99,102,241,0.05)', border: '1px solid #1e293b', fontSize: '13px', color: '#475569' }}>
          No job records found. Status is written to Redis after each scheduler run. Records appear after the first scheduled job completes.
        </div>
      )}

      {jobs.length > 0 && (
        <>
          {/* Key jobs */}
          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>SCHEDULED JOBS</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '10px', marginBottom: '24px' }}>
            {['weekly_refresh', 'us_post_close', 'hk_post_close', 'paper_trading'].map(key => {
              const job = jobs.find(j => j.job === key);
              if (!job) return (
                <div key={key} style={{ padding: '14px 16px', borderRadius: '10px', background: '#080f1e', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#334155' }}>{JOB_META[key]?.label ?? key}</div>
                  {JOB_META[key]?.desc && <div style={{ fontSize: '10px', color: '#1e293b', marginTop: '2px' }}>{JOB_META[key].desc}</div>}
                  <div style={{ fontSize: '11px', color: '#1e293b', marginTop: '4px' }}>No record yet</div>
                </div>
              );
              return <JobCard key={key} job={job} />;
            })}
          </div>

          {/* Morning digest */}
          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>MORNING DIGEST</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '10px', marginBottom: '24px' }}>
            {(() => {
              const key = 'morning_digest_combined';
              const job = jobs.find(j => j.job === key);
              if (!job) return (
                <div key={key} style={{ padding: '14px 16px', borderRadius: '10px', background: '#080f1e', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#334155' }}>{JOB_META[key]?.label ?? key}</div>
                  {JOB_META[key]?.desc && <div style={{ fontSize: '10px', color: '#1e293b', marginTop: '2px' }}>{JOB_META[key].desc}</div>}
                  <div style={{ fontSize: '11px', color: '#1e293b', marginTop: '4px' }}>No record yet</div>
                </div>
              );
              return <JobCard key={key} job={job} />;
            })()}
          </div>

          {/* Intraday + background jobs */}
          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>INTRADAY &amp; BACKGROUND</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '10px' }}>
            {jobs.filter(j => !['weekly_refresh', 'us_post_close', 'hk_post_close', 'paper_trading', 'morning_digest_combined'].includes(j.job)).map(j => (
              <JobCard key={j.job} job={j} />
            ))}
          </div>
        </>
      )}

      {/* Signal Refresh Health */}
      {signalsData && (
        <div style={{ marginTop: '28px' }}>
          <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em', marginBottom: '10px' }}>SIGNAL REFRESH HEALTH</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '10px' }}>

            {/* Signal distribution card */}
            <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: '10px' }}>
                Signal Distribution (SWING)
                <span style={{ marginLeft: '8px', fontSize: '11px', color: '#334155', fontWeight: 400 }}>{signalsData.length} stocks</span>
              </div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {[
                  { label: 'BUY',  color: '#4ade80', bg: 'rgba(74,222,128,0.1)',  border: 'rgba(74,222,128,0.3)'  },
                  { label: 'SELL', color: '#f87171', bg: 'rgba(239,68,68,0.1)',   border: 'rgba(239,68,68,0.3)'   },
                  { label: 'WAIT', color: '#fbbf24', bg: 'rgba(251,191,36,0.08)', border: 'rgba(251,191,36,0.25)' },
                  { label: 'HOLD', color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', border: 'rgba(148,163,184,0.2)' },
                ].map(({ label, color, bg, border }) => (
                  <div key={label} style={{ flex: 1, minWidth: '70px', padding: '10px 8px', borderRadius: '8px', background: bg, border: `1px solid ${border}`, textAlign: 'center' }}>
                    <div style={{ fontSize: '20px', fontWeight: 800, color }}>{signalCounts[label]}</div>
                    <div style={{ fontSize: '10px', color: '#475569', marginTop: '2px', fontWeight: 600 }}>{label}</div>
                    <div style={{ fontSize: '10px', color: '#334155', marginTop: '1px' }}>
                      {signalsData.length > 0 ? `${((signalCounts[label] / signalsData.length) * 100).toFixed(0)}%` : '—'}
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: '10px' }}>
                <div style={{ height: '4px', borderRadius: '2px', background: '#1e293b', overflow: 'hidden', display: 'flex' }}>
                  {(['BUY', 'SELL', 'WAIT', 'HOLD'] as const).map((k, i) => (
                    <div key={k} style={{
                      width: `${signalsData.length ? (signalCounts[k] / signalsData.length) * 100 : 0}%`,
                      background: ['#4ade80','#f87171','#fbbf24','#475569'][i],
                    }} />
                  ))}
                </div>
                <div style={{ fontSize: '10px', color: '#334155', marginTop: '4px' }}>
                  Bull/Bear ratio: {signalCounts.SELL > 0 ? (signalCounts.BUY / signalCounts.SELL).toFixed(1) : '∞'}
                </div>
              </div>
            </div>

            {/* Last refresh card */}
            {(() => {
              const usJob = jobs.find(j => j.job === 'us_refresh');
              const hkJob = jobs.find(j => j.job === 'hk_refresh');
              const freshSignals = signalsData.filter(s => s.ts && (Date.now() - new Date(s.ts).getTime()) < 86400000 * 2).length;
              const staleSignals = signalsData.length - freshSignals;
              return (
                <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: '10px' }}>Signal Freshness</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {usJob && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: '12px', color: '#64748b' }}>US last refresh</span>
                        <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>{relTime(usJob.last_run)}</span>
                      </div>
                    )}
                    {hkJob && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: '12px', color: '#64748b' }}>HK last refresh</span>
                        <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>{relTime(hkJob.last_run)}</span>
                      </div>
                    )}
                    <div style={{ borderTop: '1px solid #1e293b', paddingTop: '8px', display: 'flex', gap: '10px' }}>
                      <div style={{ flex: 1, textAlign: 'center', padding: '6px', borderRadius: '6px', background: 'rgba(74,222,128,0.06)', border: '1px solid rgba(74,222,128,0.15)' }}>
                        <div style={{ fontSize: '18px', fontWeight: 800, color: '#4ade80' }}>{freshSignals}</div>
                        <div style={{ fontSize: '10px', color: '#475569' }}>Fresh ≤2d</div>
                      </div>
                      <div style={{ flex: 1, textAlign: 'center', padding: '6px', borderRadius: '6px', background: staleSignals > 0 ? 'rgba(251,191,36,0.06)' : 'rgba(255,255,255,0.02)', border: `1px solid ${staleSignals > 0 ? 'rgba(251,191,36,0.2)' : '#1e293b'}` }}>
                        <div style={{ fontSize: '18px', fontWeight: 800, color: staleSignals > 0 ? '#fbbf24' : '#334155' }}>{staleSignals}</div>
                        <div style={{ fontSize: '10px', color: '#475569' }}>Stale &gt;2d</div>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* Service Connectivity */}
      <div style={{ marginTop: '28px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
          <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>SERVICE CONNECTIVITY</div>
          <button
            onClick={() => mutateHealth()}
            style={{ padding: '3px 10px', borderRadius: '5px', fontSize: '10px', fontWeight: 600, cursor: 'pointer', border: '1px solid #1e293b', background: 'transparent', color: '#475569' }}
          >
            ↺
          </button>
        </div>
        {!healthData && (
          <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#080f1e', border: '1px solid #1e293b', fontSize: '12px', color: '#334155' }}>
            Loading service ping…
          </div>
        )}
        {healthData && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '8px' }}>
            {healthData.results.map(r => {
              const ok = r.status === 'ok';
              const timeout = r.status === 'timeout';
              const color = ok ? '#4ade80' : timeout ? '#fbbf24' : '#f87171';
              const bg = ok ? 'rgba(74,222,128,0.04)' : timeout ? 'rgba(251,191,36,0.06)' : 'rgba(239,68,68,0.07)';
              const border = ok ? 'rgba(74,222,128,0.15)' : timeout ? 'rgba(251,191,36,0.25)' : 'rgba(239,68,68,0.25)';
              return (
                <div key={r.service} style={{ padding: '10px 14px', borderRadius: '8px', background: bg, border: `1px solid ${border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '12px', fontWeight: 600, color: '#cbd5e1', fontFamily: 'monospace' }}>{r.service}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '10px', color: '#475569' }}>{r.latency_ms}ms</span>
                    <span style={{ fontSize: '11px', fontWeight: 700, color }}>{ok ? '✓' : timeout ? '⏱' : '✗'}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {healthData && (
          <div style={{ marginTop: '8px', fontSize: '11px', color: '#334155' }}>
            {healthData.services_ok}/{healthData.services_total} services reachable — refreshes every 60s
          </div>
        )}
      </div>

      {/* ML Training Health */}
      {mlData && mlData.count > 0 && (
        <div style={{ marginTop: '28px' }}>
          <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em', marginBottom: '10px' }}>ML TRAINING HEALTH</div>
          {(() => {
            const usJob = jobs.find(j => j.job === 'us_post_close');
            const hkJob = jobs.find(j => j.job === 'hk_post_close');
            const all = mlData.symbols.filter((m: MlModelMetric) => m.test_auc != null);
            const avgAuc = all.length > 0 ? all.reduce((s: number, m: MlModelMetric) => s + (m.test_auc ?? 0), 0) / all.length : 0;
            const goodModels = all.filter((m: MlModelMetric) => (m.test_auc ?? 0) >= 0.65).length;
            const weakModels = all.filter((m: MlModelMetric) => (m.test_auc ?? 0) < 0.55).length;
            const overfitModels = all.filter((m: MlModelMetric) => (m.overfit_gap ?? 0) > 0.1).length;
            const aucColor = avgAuc >= 0.65 ? '#4ade80' : avgAuc >= 0.55 ? '#fbbf24' : '#f87171';
            return (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '10px' }}>
                <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: '10px' }}>Model Quality</div>
                  <div style={{ display: 'flex', gap: '8px', marginBottom: '10px' }}>
                    <div style={{ flex: 1, textAlign: 'center', padding: '8px', borderRadius: '6px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.15)' }}>
                      <div style={{ fontSize: '20px', fontWeight: 800, color: aucColor }}>{avgAuc.toFixed(3)}</div>
                      <div style={{ fontSize: '10px', color: '#475569' }}>Avg AUC</div>
                    </div>
                    <div style={{ flex: 1, textAlign: 'center', padding: '8px', borderRadius: '6px', background: 'rgba(74,222,128,0.06)', border: '1px solid rgba(74,222,128,0.15)' }}>
                      <div style={{ fontSize: '20px', fontWeight: 800, color: '#4ade80' }}>{goodModels}</div>
                      <div style={{ fontSize: '10px', color: '#475569' }}>Good ≥0.65</div>
                    </div>
                    <div style={{ flex: 1, textAlign: 'center', padding: '8px', borderRadius: '6px', background: weakModels > 0 ? 'rgba(251,191,36,0.06)' : 'rgba(255,255,255,0.02)', border: `1px solid ${weakModels > 0 ? 'rgba(251,191,36,0.2)' : '#1e293b'}` }}>
                      <div style={{ fontSize: '20px', fontWeight: 800, color: weakModels > 0 ? '#fbbf24' : '#334155' }}>{weakModels}</div>
                      <div style={{ fontSize: '10px', color: '#475569' }}>Weak &lt;0.55</div>
                    </div>
                    <div style={{ flex: 1, textAlign: 'center', padding: '8px', borderRadius: '6px', background: overfitModels > 0 ? 'rgba(239,68,68,0.06)' : 'rgba(255,255,255,0.02)', border: `1px solid ${overfitModels > 0 ? 'rgba(239,68,68,0.2)' : '#1e293b'}` }}>
                      <div style={{ fontSize: '20px', fontWeight: 800, color: overfitModels > 0 ? '#f87171' : '#334155' }}>{overfitModels}</div>
                      <div style={{ fontSize: '10px', color: '#475569' }}>Overfit</div>
                    </div>
                  </div>
                  <div style={{ fontSize: '11px', color: '#334155', display: 'flex', gap: '16px' }}>
                    <span>{mlData.count} models total</span>
                    {all.length < mlData.count && <span style={{ color: '#fbbf24' }}>⚠ {mlData.count - all.length} missing metrics</span>}
                  </div>
                </div>

                <div style={{ padding: '14px 16px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', marginBottom: '10px' }}>Last Retrain</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {usJob ? (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: '12px', color: '#64748b' }}>US Post-Close</span>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>{relTime(usJob.last_run)}</span>
                          <span style={{ fontSize: '10px', padding: '1px 6px', borderRadius: '4px', color: usJob.status === 'ok' ? '#4ade80' : '#f87171', background: usJob.status === 'ok' ? 'rgba(74,222,128,0.1)' : 'rgba(239,68,68,0.1)', border: `1px solid ${usJob.status === 'ok' ? 'rgba(74,222,128,0.3)' : 'rgba(239,68,68,0.3)'}` }}>
                            {usJob.status === 'ok' ? '✓' : '✗'}
                          </span>
                        </div>
                      </div>
                    ) : <span style={{ fontSize: '12px', color: '#334155' }}>US — no record yet</span>}
                    {hkJob ? (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: '12px', color: '#64748b' }}>HK Post-Close</span>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>{relTime(hkJob.last_run)}</span>
                          <span style={{ fontSize: '10px', padding: '1px 6px', borderRadius: '4px', color: hkJob.status === 'ok' ? '#4ade80' : '#f87171', background: hkJob.status === 'ok' ? 'rgba(74,222,128,0.1)' : 'rgba(239,68,68,0.1)', border: `1px solid ${hkJob.status === 'ok' ? 'rgba(74,222,128,0.3)' : 'rgba(239,68,68,0.3)'}` }}>
                            {hkJob.status === 'ok' ? '✓' : '✗'}
                          </span>
                        </div>
                      </div>
                    ) : <span style={{ fontSize: '12px', color: '#334155' }}>HK — no record yet</span>}
                    <div style={{ borderTop: '1px solid #1e293b', paddingTop: '8px', fontSize: '11px', color: '#334155' }}>
                      Retraining runs at US 16:30 ET and HK 16:30 HKT on market days
                    </div>
                  </div>
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* Schedule Reference */}
      <div style={{ marginTop: '32px' }}>
        <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em', marginBottom: '12px' }}>SCHEDULE REFERENCE</div>
        <div style={{ padding: '16px', borderRadius: '10px', background: '#080f1e', border: '1px solid #1e293b' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>

            {/* US column */}
            <div>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#60a5fa', marginBottom: '10px' }}>🇺🇸 US (America/New_York)</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {[
                  { time: '09:00 ET', label: 'Morning Digest', desc: 'Email of top signals + moves', color: '#a78bfa' },
                  { time: '09:25–09:45 ET', label: 'Open Burst', desc: 'Dense refresh at open (5 runs)', color: '#60a5fa' },
                  { time: '10:00–15:00 ET', label: 'Intraday', desc: 'Full pipeline every 5 min', color: '#60a5fa' },
                  { time: '15:30–16:15 ET', label: 'Close Burst', desc: 'Dense refresh at close', color: '#60a5fa' },
                  { time: '16:30 ET', label: 'Post-Close', desc: 'Final bar + ML retrain', color: '#4ade80' },
                  { time: '09:00–17:00 ET', label: 'Live Price Cache', desc: 'Refresh every 1 min (market hours)', color: '#94a3b8' },
                ].map(row => (
                  <div key={row.time} style={{ display: 'flex', gap: '10px', alignItems: 'baseline' }}>
                    <span style={{ fontSize: '10px', color: '#475569', minWidth: '110px', fontFamily: 'monospace', flexShrink: 0 }}>{row.time}</span>
                    <div>
                      <span style={{ fontSize: '11px', fontWeight: 700, color: row.color }}>{row.label}</span>
                      <span style={{ fontSize: '10px', color: '#334155', marginLeft: '6px' }}>{row.desc}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* HK column */}
            <div>
              <div style={{ fontSize: '11px', fontWeight: 700, color: '#f97316', marginBottom: '10px' }}>🇭🇰 HK (Asia/Hong_Kong)</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {[
                  { time: '08:55 HKT', label: 'Morning Digest', desc: 'Email of top signals + moves', color: '#a78bfa' },
                  { time: '09:25–09:45 HKT', label: 'Open Burst', desc: 'Dense refresh at open (5 runs)', color: '#f97316' },
                  { time: '10:00–15:00 HKT', label: 'Intraday', desc: 'Full pipeline every 5 min (skip 12–13)', color: '#f97316' },
                  { time: '15:30–16:15 HKT', label: 'Close Burst', desc: 'Dense refresh at close', color: '#f97316' },
                  { time: '16:30 HKT', label: 'Post-Close', desc: 'Final bar + ML retrain', color: '#4ade80' },
                  { time: '09:00–17:00 HKT', label: 'Live Price Cache', desc: 'Refresh every 1 min (market hours)', color: '#94a3b8' },
                ].map(row => (
                  <div key={row.time} style={{ display: 'flex', gap: '10px', alignItems: 'baseline' }}>
                    <span style={{ fontSize: '10px', color: '#475569', minWidth: '110px', fontFamily: 'monospace', flexShrink: 0 }}>{row.time}</span>
                    <div>
                      <span style={{ fontSize: '11px', fontWeight: 700, color: row.color }}>{row.label}</span>
                      <span style={{ fontSize: '10px', color: '#334155', marginLeft: '6px' }}>{row.desc}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

          </div>

          {/* Weekly / always-on footer */}
          <div style={{ marginTop: '14px', paddingTop: '12px', borderTop: '1px solid #1e293b', display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
            <div>
              <span style={{ fontSize: '10px', color: '#475569', fontFamily: 'monospace', marginRight: '8px' }}>Every 1 min</span>
              <span style={{ fontSize: '10px', color: '#94a3b8', fontWeight: 600 }}>Price Alerts</span>
              <span style={{ fontSize: '10px', color: '#334155', marginLeft: '6px' }}>Check thresholds against Redis live cache</span>
            </div>
            <div>
              <span style={{ fontSize: '10px', color: '#475569', fontFamily: 'monospace', marginRight: '8px' }}>Sun 14:00 PST</span>
              <span style={{ fontSize: '10px', color: '#fbbf24', fontWeight: 600 }}>Weekly Refresh</span>
              <span style={{ fontSize: '10px', color: '#334155', marginLeft: '6px' }}>Force re-ingest 3 years of history for all stocks</span>
            </div>
            <div>
              <span style={{ fontSize: '10px', color: '#475569', fontFamily: 'monospace', marginRight: '8px' }}>Sun 15:00 PST</span>
              <span style={{ fontSize: '10px', color: '#64748b', fontWeight: 600 }}>DB Purge</span>
              <span style={{ fontSize: '10px', color: '#334155', marginLeft: '6px' }}>Delete prices_5m + job logs older than 90 days</span>
            </div>
          </div>
        </div>
      </div>

      {/* ML Model Metrics */}
      {mlData && mlData.count > 0 && (
        <div style={{ marginTop: '32px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
            <div>
              <div style={{ fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em', marginBottom: '4px' }}>ML MODEL ACCURACY</div>
              <div style={{ fontSize: '11px', color: '#334155' }}>{mlData.count} trained XGBoost models — sorted by test AUC</div>
            </div>
          </div>

          {/* Bottom 5 — worst AUC */}
          {(() => {
            const all = mlData.symbols.filter((m: MlModelMetric) => m.test_auc != null);
            const top5 = all.slice(0, 5);
            const bot5 = [...all].reverse().slice(0, 5);
            const avgAuc = all.reduce((s: number, m: MlModelMetric) => s + (m.test_auc ?? 0), 0) / (all.length || 1);
            const overfit = all.filter((m: MlModelMetric) => (m.overfit_gap ?? 0) > 0.1);
            return (
              <>
                <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', flexWrap: 'wrap' }}>
                  <span style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 700, color: '#94a3b8', background: '#0d1424', border: '1px solid #1e293b' }}>
                    Avg AUC: {avgAuc.toFixed(3)}
                  </span>
                  {overfit.length > 0 && (
                    <span style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 700, color: '#f87171', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
                      ⚠ {overfit.length} overfitting (gap &gt;0.10)
                    </span>
                  )}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                  <div>
                    <div style={{ fontSize: '10px', color: '#4ade80', fontWeight: 700, marginBottom: '6px', letterSpacing: '0.04em' }}>TOP 5 — Highest AUC</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      {top5.map((m: MlModelMetric) => <MlRow key={m.symbol} m={m} />)}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: '10px', color: '#f87171', fontWeight: 700, marginBottom: '6px', letterSpacing: '0.04em' }}>BOTTOM 5 — Lowest AUC</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      {bot5.map((m: MlModelMetric) => <MlRow key={m.symbol} m={m} />)}
                    </div>
                  </div>
                </div>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

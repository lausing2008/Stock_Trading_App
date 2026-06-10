import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api, type SchedulerJob } from '@/lib/api';
import { getSession } from '@/lib/auth';

const JOB_META: Record<string, { label: string; maxAgeDays: number; desc: string }> = {
  weekly_refresh:          { label: 'Weekly Full Refresh',       maxAgeDays: 8,  desc: 'Force re-ingest all stocks (Sun 14:00 PST)' },
  us_post_close:           { label: 'US Post-Close',             maxAgeDays: 3,  desc: 'Final bar + ML retrain (Mon–Fri 16:30 ET)' },
  hk_post_close:           { label: 'HK Post-Close',             maxAgeDays: 3,  desc: 'Final bar + ML retrain (Mon–Fri 16:30 HKT)' },
  us_refresh:              { label: 'US Intraday Refresh',       maxAgeDays: 2,  desc: 'Prices + rankings + signals during market hours' },
  hk_refresh:              { label: 'HK Intraday Refresh',       maxAgeDays: 2,  desc: 'Prices + rankings + signals during market hours' },
  paper_trading:           { label: 'Paper Trading Engine',      maxAgeDays: 2,  desc: 'GROWTH-style autonomous trading step' },
  tune_all_sent:           { label: 'Optuna Tune-All (sent)',    maxAgeDays: 8,  desc: 'Weekly hyperparameter tuning request sent to ML service' },
  calibrate_ta_weights_sent: { label: 'TA Weight Calibration (sent)', maxAgeDays: 8, desc: 'Weekly TA logistic regression calibration request sent' },
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

  const statusColor = job.status === 'ok' ? '#4ade80' : job.status === 'error' ? '#f87171' : '#94a3b8';
  const statusBg = job.status === 'ok' ? 'rgba(74,222,128,0.08)' : job.status === 'error' ? 'rgba(239,68,68,0.1)' : 'rgba(148,163,184,0.06)';
  const statusBorder = job.status === 'ok' ? 'rgba(74,222,128,0.2)' : job.status === 'error' ? 'rgba(239,68,68,0.3)' : 'rgba(148,163,184,0.15)';
  const statusLabel = job.status === 'ok' ? '✓ OK' : job.status === 'error' ? '✗ Error' : '– Skipped';

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
          {/* Key jobs first */}
          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>SCHEDULED JOBS</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '10px', marginBottom: '24px' }}>
            {['weekly_refresh', 'us_post_close', 'hk_post_close', 'paper_trading'].map(key => {
              const job = jobs.find(j => j.job === key);
              if (!job) return (
                <div key={key} style={{ padding: '14px 16px', borderRadius: '10px', background: '#080f1e', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '13px', fontWeight: 700, color: '#334155' }}>{JOB_META[key]?.label ?? key}</div>
                  <div style={{ fontSize: '11px', color: '#1e293b', marginTop: '4px' }}>No record yet</div>
                </div>
              );
              return <JobCard key={key} job={job} />;
            })}
          </div>

          {/* Intraday + background jobs */}
          <div style={{ marginBottom: '8px', fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em' }}>INTRADAY &amp; BACKGROUND</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: '10px' }}>
            {jobs.filter(j => !['weekly_refresh', 'us_post_close', 'hk_post_close', 'paper_trading'].includes(j.job)).map(j => (
              <JobCard key={j.job} job={j} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Fix Effectiveness — T325-FIXEFFECTIVENESS. Direct user request (2026-09-02), after the AI
 * Signal deep audit: "I would like to have a dashboard to show the performance after we
 * applied the fix so that we can compare later and see if the fix really works."
 *
 * A general tracker, not a one-off AI-Signal page — any future significant fix from a later
 * audit domain (Decision-Making, Paper Trading, Model Training, Short Squeeze, Options)
 * registers itself the same way (POST /fix-effectiveness/register) and appears here
 * automatically. AI Signal's AUD-SIGNAL3-EVALSELECTIONBIAS fix is entry #1.
 */
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import useSWR from 'swr';
import { api } from '@/lib/api';
import type { FixRecordResponse, FixMetricBucket } from '@/lib/api';
import { getSession } from '@/lib/auth';

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function fmtWinRate(v: number | null): string {
  if (v == null) return '—';
  return (v * 100).toFixed(1) + '%';
}

function deltaColor(before: number | null, after: number | null, higherIsBetter = true): string {
  if (before == null || after == null) return '#64748b';
  const delta = after - before;
  if (Math.abs(delta) < 0.001) return '#64748b';
  const improved = higherIsBetter ? delta > 0 : delta < 0;
  return improved ? '#22c55e' : '#ef4444';
}

function BucketRow({ label, baseline, latest }: { label: string; baseline: FixMetricBucket; latest: FixMetricBucket | null }) {
  return (
    <tr style={{ borderBottom: '1px solid #1e293b' }}>
      <td style={{ padding: '8px 10px', fontWeight: 700, color: '#e2e8f0' }}>{label}</td>
      <td style={{ padding: '8px 10px', textAlign: 'right', color: '#64748b' }}>{fmtWinRate(baseline.win_rate_5d)}</td>
      <td style={{ padding: '8px 10px', textAlign: 'right', color: '#64748b' }}>{fmtPct(baseline.avg_return_5d_pct)}</td>
      <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: latest ? deltaColor(baseline.win_rate_5d, latest.win_rate_5d) : '#475569' }}>
        {latest ? fmtWinRate(latest.win_rate_5d) : 'No snapshot yet'}
      </td>
      <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: latest ? deltaColor(baseline.avg_return_5d_pct, latest.avg_return_5d_pct) : '#475569' }}>
        {latest ? fmtPct(latest.avg_return_5d_pct) : '—'}
      </td>
      <td style={{ padding: '8px 10px', textAlign: 'right', color: '#475569', fontSize: 11 }}>{latest ? latest.resolved_5d : baseline.resolved_5d}</td>
    </tr>
  );
}

function FixCard({ record, onSnapshot, snapshotting }: { record: FixRecordResponse; onSnapshot: (fixId: string) => void; snapshotting: boolean }) {
  const latest = record.snapshots.length > 0 ? record.snapshots[record.snapshots.length - 1] : null;
  const daysSinceFixed = Math.floor((Date.now() - new Date(record.fixed_at).getTime()) / 86_400_000);
  const daysUntilDue = record.recheck_after_days - (latest
    ? Math.floor((Date.now() - new Date(latest.taken_at).getTime()) / 86_400_000)
    : daysSinceFixed);
  const isDue = daysUntilDue <= 0;

  const bucketKeys = Object.keys(record.baseline_metrics.by_bucket).sort();

  return (
    <div style={{ borderRadius: 10, border: '1px solid #1e293b', padding: '16px 18px', marginBottom: 16, background: '#0d1424' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: '#a78bfa', background: 'rgba(167,139,250,0.12)', border: '1px solid rgba(167,139,250,0.3)', borderRadius: 4, padding: '2px 7px', textTransform: 'uppercase' }}>
              {record.domain.replace('_', ' ')}
            </span>
            <span style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>{record.title}</span>
          </div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>
            {record.fix_id} · fixed {new Date(record.fixed_at).toLocaleDateString()} ({daysSinceFixed}d ago)
            {record.audit_doc_path && <> · <a href="#" onClick={e => e.preventDefault()} style={{ color: '#38bdf8' }} title={record.audit_doc_path}>audit doc</a></>}
          </div>
        </div>
        <button
          onClick={() => onSnapshot(record.fix_id)}
          disabled={snapshotting}
          style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: snapshotting ? 'default' : 'pointer', border: `1px solid ${isDue ? '#22c55e' : '#1e293b'}`, background: isDue ? 'rgba(34,197,94,0.12)' : 'transparent', color: isDue ? '#4ade80' : '#94a3b8' }}
        >
          {snapshotting ? '⟳ Measuring…' : isDue ? '● Due — re-measure now' : `Re-measure (${daysUntilDue}d until due)`}
        </button>
      </div>

      {record.success_criteria && (
        <div style={{ fontSize: 11.5, color: '#64748b', marginBottom: 12, fontStyle: 'italic' }}>
          Success looks like: {record.success_criteria}
        </div>
      )}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead>
            <tr style={{ background: 'rgba(148,163,184,0.05)' }}>
              <th style={{ textAlign: 'left', padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase' }}>Bucket</th>
              <th style={{ textAlign: 'right', padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase' }}>Baseline win%</th>
              <th style={{ textAlign: 'right', padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase' }}>Baseline ret%</th>
              <th style={{ textAlign: 'right', padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase' }}>Latest win%</th>
              <th style={{ textAlign: 'right', padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase' }}>Latest ret%</th>
              <th style={{ textAlign: 'right', padding: '8px 10px', color: '#475569', fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase' }}>N</th>
            </tr>
          </thead>
          <tbody>
            {bucketKeys.map(key => (
              <BucketRow
                key={key}
                label={key.replace('|', ' ')}
                baseline={record.baseline_metrics.by_bucket[key]}
                latest={latest ? latest.metrics.by_bucket[key] ?? null : null}
              />
            ))}
          </tbody>
        </table>
      </div>

      {record.snapshots.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: '#475569' }}>
          {record.snapshots.length} snapshot{record.snapshots.length !== 1 ? 's' : ''} taken · most recent {new Date(latest!.taken_at).toLocaleDateString()}
          {latest!.note && <> · {latest!.note}</>}
        </div>
      )}
      {record.snapshots.length === 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: '#f59e0b' }}>
          No re-measurement yet — check back in ~{record.recheck_after_days} days, or click Re-measure once enough time has passed for fresh data to accumulate.
        </div>
      )}
    </div>
  );
}

export default function FixEffectivenessPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [snapshottingId, setSnapshottingId] = useState<string | null>(null);

  useEffect(() => {
    const session = getSession();
    if (!session) { router.replace('/login'); return; }
    if (session.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  const { data, isLoading, error, mutate } = useSWR(
    authed ? 'fix-effectiveness-records' : null,
    () => api.getFixRecords(),
    { revalidateOnFocus: false },
  );

  const handleSnapshot = async (fixId: string) => {
    setSnapshottingId(fixId);
    try {
      await api.takeFixSnapshot(fixId);
      await mutate();
    } catch {
      // swallow — the button re-enables and the user can retry; no fabricated success state
    } finally {
      setSnapshottingId(null);
    }
  };

  if (!authed) return null;

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '24px 0' }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', marginBottom: 4 }}>Fix Effectiveness</h1>
        <p style={{ fontSize: 12, color: '#475569', maxWidth: 720 }}>
          Baseline vs. re-measured metrics for significant bug fixes found during platform
          audits — did the fix actually move the numbers? Auto-rechecked daily once a fix's own
          recheck window has passed; a manual re-measure is always available.
        </p>
      </div>

      {isLoading && <div style={{ textAlign: 'center', padding: 40, color: '#475569', fontSize: 13 }}>Loading…</div>}
      {error && (
        <div style={{ padding: '16px 20px', borderRadius: 10, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', fontSize: 13, color: '#f87171' }}>
          Failed to load fix records.
        </div>
      )}
      {data && data.length === 0 && (
        <div style={{ padding: '16px 20px', borderRadius: 10, background: 'rgba(148,163,184,0.05)', border: '1px solid #1e293b', fontSize: 13, color: '#94a3b8' }}>
          No fixes tracked yet.
        </div>
      )}
      {data && data.map(record => (
        <FixCard key={record.fix_id} record={record} onSnapshot={handleSnapshot} snapshotting={snapshottingId === record.fix_id} />
      ))}
    </div>
  );
}

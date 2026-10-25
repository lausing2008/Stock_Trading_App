import RiskDeliveryScheduleCard from '@/components/RiskDeliveryScheduleCard';
import React, { useCallback, useEffect, useState } from 'react';
import { api, RiskAlertEvent, RiskAlertSummaryResponse } from '@/lib/api';

// Price-risk alert history.
//
// The channel is in SHADOW MODE: detections are recorded and nothing is delivered. That is the
// single most important thing this page has to communicate, because an empty inbox alongside a
// full history is otherwise indistinguishable from a broken alert channel. Every row therefore
// carries its own delivery state, and the banner says plainly what the system is doing.

const RULE_LABEL: Record<string, string> = {
  session_decline: 'One-session decline',
  pullback: 'Pullback from recent peak',
  basket_breadth: 'Theme-wide decline',
  unmeasurable: 'Could not be measured',
  slow_decline: 'Multi-session decline',
  basket_slow_decline: 'Multi-session theme decline',
};

const STATE_LABEL: Record<string, string> = {
  shadow_suppressed: 'Not sent — shadow mode',
  preference_disabled: 'Not sent — turned off',
  in_app_only: 'In-app only',
  eligible_for_email: 'Eligible for email',
  pending_digest: 'Waiting for a digest',
  email_queued: 'Queued for email; delivery rules still apply',
  historical_no_alert: 'History only — too old to notify',
  email_capped: 'Daily email limit reached',
};

function pct(v: number | null): string {
  return v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
}

export default function RiskAlertsPage() {
  const [events, setEvents] = useState<RiskAlertEvent[]>([]);
  const [summary, setSummary] = useState<RiskAlertSummaryResponse | null>(null);
  const [days, setDays] = useState(14);
  const [rule, setRule] = useState('');
  const [scope, setScope] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, s] = await Promise.all([
        api.riskAlertHistory({ days, rule: rule || undefined, scope: scope || undefined }),
        api.riskAlertSummary(days),
      ]);
      setEvents(h.events);
      setSummary(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load alert history');
    } finally {
      setLoading(false);
    }
  }, [days, rule, scope]);

  useEffect(() => { void load(); }, [load]);

  const shadowCount = summary?.by_delivery_mode?.shadow ?? 0;

  return (
    <div style={{ padding: '24px 16px', maxWidth: 1100, margin: '0 auto' }}>
      <h1 style={{ fontSize: 24, marginBottom: 4 }}>Price-risk alerts</h1>
      <p style={{ color: '#94a3b8', marginTop: 0, fontSize: 14 }}>
        Measured moves in followed stocks and named benchmark ETFs, independent of the AI signal&apos;s direction label —
        a label does not change merely because a price falls.
      </p>
      <p style={{ color: '#94a3b8', fontSize: 13 }}>SPY, QQQ and SOXX benchmark observations are research in shadow mode; they do not send alerts.</p>
      <RiskDeliveryScheduleCard />

      {shadowCount > 0 && (
        <div style={{ border: '1px solid #f59e0b', background: '#f59e0b18', borderRadius: 8,
                      padding: '10px 14px', margin: '16px 0', fontSize: 14, color: '#fbbf24' }}>
          <strong>Shadow mode.</strong> {shadowCount} of these were detected with delivery
          deliberately switched off, so nothing was sent for them. Thresholds are proposed
          starting values being measured against real volume before this channel is turned on.
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', margin: '16px 0' }}>
        <label style={{ fontSize: 13, color: '#94a3b8' }}>
          Window{' '}
          <select value={days} onChange={e => setDays(Number(e.target.value))}
                  style={{ background: '#0f172a', color: '#e2e8f0', border: '1px solid #1e293b',
                           borderRadius: 6, padding: '4px 8px' }}>
            {[7, 14, 30, 90].map(d => <option key={d} value={d}>{d} days</option>)}
          </select>
        </label>
        <label style={{ fontSize: 13, color: '#94a3b8' }}>Scope{' '}
          <select aria-label="Risk event scope" value={scope} onChange={e => setScope(e.target.value)}>
            <option value="">All scopes</option><option value="symbol">Followed stocks</option>
            <option value="benchmark">Benchmark research</option><option value="theme">Themes</option>
          </select>
        </label>
        <label style={{ fontSize: 13, color: '#94a3b8' }}>
          Kind{' '}
          <select value={rule} onChange={e => setRule(e.target.value)}
                  style={{ background: '#0f172a', color: '#e2e8f0', border: '1px solid #1e293b',
                           borderRadius: 6, padding: '4px 8px' }}>
            <option value="">All</option>
            {Object.entries(RULE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <button onClick={() => void load()} disabled={loading}
                style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155',
                         borderRadius: 6, padding: '5px 12px', cursor: 'pointer' }}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {(scope || rule) && <p style={{ color: '#94a3b8', fontSize: 12 }}>Summary counts cover all scopes and kinds in the selected time window.</p>}
      {summary && (
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', margin: '12px 0 20px',
                      fontSize: 13, color: '#cbd5e1' }}>
          <span><strong>{summary.events_total}</strong> detections</span>
          <span><strong>{summary.events_per_day}</strong>/day</span>
          <span><strong>{summary.distinct_subjects}</strong> subjects</span>
          {summary.unmeasurable_events > 0 && (
            <span style={{ color: '#f87171' }}>
              <strong>{summary.unmeasurable_events}</strong> could not be measured
            </span>
          )}
        </div>
      )}

      {error && <p style={{ color: '#f87171' }}>{error}</p>}
      {!loading && !error && events.length === 0 && (
        <p style={{ color: '#94a3b8' }}>
          No detections in this window. That is an absence of qualifying moves, not a
          confirmation that the detector ran — the summary above shows what it has recorded.
        </p>
      )}

      <div style={{ display: 'grid', gap: 10 }}>
        {events.map(ev => (
          <div key={ev.id} style={{ border: '1px solid #1e293b', borderRadius: 8, padding: 14,
                                    background: '#0b1220' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12,
                          flexWrap: 'wrap', alignItems: 'baseline' }}>
              <div>
                <strong style={{ fontSize: 16 }}>{ev.subject}</strong>{' '}
                {ev.scope === 'benchmark' && <span>Benchmark research · shadow only</span>}
                <span style={{ color: '#94a3b8', fontSize: 13 }}>
                  {RULE_LABEL[ev.rule] ?? ev.rule}
                </span>
              </div>
              <div style={{ fontVariantNumeric: 'tabular-nums',
                            color: ev.rule === 'unmeasurable' ? '#94a3b8'
                                 : (ev.change_pct ?? 0) < 0 ? '#f87171' : '#4ade80',
                            fontSize: 16 }}>
                {ev.rule === 'unmeasurable' ? '—' : pct(ev.change_pct)}
              </div>
            </div>

            <div style={{ color: '#94a3b8', fontSize: 12, marginTop: 6 }}>
              {ev.observed_from ? `${ev.observed_from} → ` : ''}{ev.observed_to}
              {ev.window_sessions ? ` · ${ev.window_sessions} sessions` : ''}
              {ev.step ? ` · severity step ${ev.step}${ev.ladder ? ` of ${ev.ladder.length}` : ''}` : ''}
            </div>

            {/* Evidence status is shown even when it is fine. A figure whose basis is only
                visible when something is wrong trains people to assume silence means verified. */}
            <div style={{ marginTop: 8, fontSize: 12, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ border: '1px solid #334155', borderRadius: 999, padding: '2px 8px',
                             color: ev.evidence_status === 'verified' ? '#4ade80' : '#fbbf24' }}>
                {ev.evidence_status}
              </span>
              {ev.price_basis && (
                <span style={{ color: '#64748b' }}>basis: {ev.price_basis}</span>
              )}
              <span style={{ color: '#64748b' }}>
                {STATE_LABEL[ev.delivery_state ?? ''] ?? ev.delivery_state ?? 'no receipt for you'}
              </span>
            </div>

            {ev.evidence_reason && (
              <div style={{ marginTop: 6, fontSize: 12, color: '#f87171' }}>
                {ev.evidence_reason}
              </div>
            )}
            {ev.delivery_reason && (
              <div style={{ marginTop: 4, fontSize: 12, color: '#64748b' }}>
                {ev.delivery_reason}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

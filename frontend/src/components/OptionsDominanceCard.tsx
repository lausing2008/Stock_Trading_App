import React from 'react';
import type { OptionsDominanceRow, OptionsDominanceSide, OptionsFlow } from '@/lib/api';
import { agoText, etText, optionsFlowUnavailableText, requestFailedText } from '@/lib/optionsFlowStatus';
import { useNow } from '@/lib/useNow';

const names = { volume: 'Contract volume', open_interest: 'Open interest', traded_premium: 'Paid premium', aggressor_estimate: 'Buy/sell estimate' };
const labels = { call_heavy: 'Call-heavy volume', put_heavy: 'Put-heavy volume', mixed: 'Mixed volume',
  unmeasured: 'Share unavailable', more_calls: 'Higher call OI', more_puts: 'Higher put OI', equal: 'Equal OI' };

function count(side?: OptionsDominanceSide) {
  if (!side) return 'Unavailable';
  if (side.value != null) return side.value.toLocaleString();
  if (side.observed_subtotal != null) return `≥${side.observed_subtotal.toLocaleString()} (${side.known}/${side.total} contracts measured)`;
  return 'Not measured';
}

function share(row: OptionsDominanceRow) {
  if (row.call_share == null || row.put_share == null) {
    return row.status === 'zero' ? 'Measured total is zero; no share' : 'Share unavailable';
  }
  return `${(row.call_share * 100).toFixed(1)}% calls / ${(row.put_share * 100).toFixed(1)}% puts`;
}

/** One descriptive snapshot. Reuses the page's flow response, with no extra polling. */
export default function OptionsDominanceCard({ flow, error, onRetry }: { flow?: OptionsFlow; error?: unknown; onRetry: () => void }) {
  const now = useNow(30_000);
  const summary = flow?.dominance?.method_version === 'options_dominance_v1' ? flow.dominance : undefined;
  const available = summary?.available;
  return <section aria-label="Options activity and flow" style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: '14px 16px', marginBottom: 16 }}>
    <h2 style={{ color: '#cbd5e1', fontSize: 15, margin: '0 0 8px' }}>Options activity and flow</h2>
    {error ? <div role="alert" style={{ color: '#fbbf24', fontSize: 12 }}>
      {requestFailedText(error)} {available ? 'Showing the previously computed snapshot; it is not current.' : ''}
      <button type="button" onClick={onRetry} style={{ marginLeft: 8 }}>Retry</button>
    </div> : null}
    {!available ? <p role="status" style={{ color: '#94a3b8', fontSize: 12 }}>
      {!flow ? error ? 'Activity is unknown while this request is failing.' : 'Loading options activity…'
        : !flow.available ? optionsFlowUnavailableText(flow) : 'Activity summary unavailable from this response.'}
    </p> : <>
      <p style={{ color: '#94a3b8', fontSize: 12, margin: '4px 0 8px' }}>
        Snapshot session {summary.source_session} · {summary.scope.included.length} of {summary.scope.requested.length} selected expirations · coverage {summary.scope.coverage_status}.
        {' '}Included: {summary.scope.included.join(', ') || 'none'}.
        {' '}Computed {etText(summary.computed_at)} ({agoText((now - Date.parse(summary.computed_at)) / 60_000)}).
      </p>
      <p style={{ color: '#94a3b8', fontSize: 11 }}>Selected scope: nearest four expirations of the source session. Shares describe included expirations only, including historical contracts; they are not a current trade-candidate list.</p>
      {summary.scope.missing.length > 0 && <p style={{ color: '#fbbf24', fontSize: 12 }}>Missing expiration reads: {summary.scope.missing.join(', ')}. This is a partial snapshot.</p>}
      {summary.scope.coverage_status !== 'complete' && <p style={{ color: '#fbbf24', fontSize: 11 }}>Complete chain coverage is not established. Observed values do not prove absent activity elsewhere.</p>}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', fontSize: 12, color: '#cbd5e1', borderCollapse: 'collapse' }}>
          <thead><tr>{['Measure', 'Calls', 'Puts', 'Observed composition', 'Evidence'].map(h => <th key={h} style={{ padding: 8, textAlign: 'left' }}>{h}</th>)}</tr></thead>
          <tbody>{summary.rows.map(row => <tr key={row.metric} style={{ borderTop: '1px solid #334155' }}>
            <th scope="row" style={{ padding: 8, textAlign: 'left' }}>{names[row.metric]}</th>
            <td style={{ padding: 8 }}>{count(row.sides?.call)}</td><td style={{ padding: 8 }}>{count(row.sides?.put)}</td>
            <td style={{ padding: 8 }}>{row.sides ? <>{row.composition ? labels[row.composition] : 'Share unavailable'}<br />{share(row)}</> : 'Unavailable'}</td>
            <td style={{ padding: 8, color: '#94a3b8' }}>{row.status === 'partial' ? 'Partial measurements. Shares withheld. ' : ''}{row.note}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {summary.volume_oi_disagree && <p style={{ color: '#fbbf24', fontSize: 12 }}>Volume and OI favor different sides. These are different measurements; they are not combined into one directional verdict.</p>}
      <p style={{ color: '#94a3b8', fontSize: 11 }}>Volume cut points: C/P ≥ 1.3 call-heavy; C/P ≤ 0.8 put-heavy; mixed otherwise. OI compares counts, using no volume threshold.</p>
      <p style={{ color: '#cbd5e1', fontSize: 12, marginBottom: 0 }}>Research only. Direction is not established: calls and puts can be bought or sold for different purposes.</p>
    </>}
  </section>;
}

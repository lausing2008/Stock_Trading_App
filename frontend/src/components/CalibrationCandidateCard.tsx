import React, { useState } from 'react';
import { api, type CalibrationComparison } from '@/lib/api';
export default function CalibrationCandidateCard() {
  const [provisional, setProvisional] = useState(false);
  const [report, setReport] = useState<CalibrationComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function run() {
    setBusy(true); setError(''); setReport(null);
    try { setReport(await api.calibrationCandidates(provisional)); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load research comparison'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Calibration research" style={{ padding: 16, margin: '16px 0', border: '1px solid #334155', borderRadius: 10, color: '#cbd5e1', fontSize: 12 }}>
    <h3 style={{ marginTop: 0 }}>Longer-history and pooled calibration research</h3>
    <p>Read-only chronological comparison. Training uses labels resolved before prediction time. This never changes production confidence, models or trading rules.</p>
    <label><input type="checkbox" checked={provisional} disabled={busy} onChange={e => { setProvisional(e.target.checked); setReport(null); }} /> Include provisional evidence for research</label>
    <button disabled={busy} onClick={run} style={{ marginLeft: 10 }}>{busy ? 'Comparing…' : 'Run comparison'}</button>
    {error && <p role="alert">{error}</p>}
    {report && <>
      <p><strong>{report.status.replaceAll('_', ' ')}</strong>: {report.reason}</p>
      <p>{report.mode === 'provisional_research' ? 'Includes provisional observations; results are not verified.' : 'Verified observations only.'}</p>
      {report.evidence_counts && <p>Evidence: {Object.entries(report.evidence_counts).map(([k, n]) => `${k}: ${n}`).join(' · ')}</p>}
      {report.excluded && <p>Excluded: {Object.entries(report.excluded).map(([k, n]) => `${k.replaceAll('_', ' ')}: ${n}`).join(' · ') || 'none'}</p>}
      {report.test_first && <p>Held-out signals: {report.test_first} to {report.test_last}; {report.test_signals} distinct signal records.</p>}
      {!!report.candidates.length && <table style={{ width: '100%', textAlign: 'left' }}>
        <thead><tr><th>Candidate</th><th>Scope</th><th>Predictions</th><th>Same test rows</th><th>Mean squared probability error</th></tr></thead>
        <tbody>{report.candidates.map(x => <tr key={x.candidate}><td>{x.candidate.replaceAll('_', ' ')}</td><td>{x.scope.replaceAll('_', ' ')}</td><td>{x.forecast_count}</td><td>{x.paired_count}</td><td>{x.paired_brier === null ? 'Not measurable' : x.paired_brier.toFixed(4)}</td></tr>)}</tbody>
      </table>}
      <p>{report.note}</p>
      {(report.limitations ?? []).map(x => <p key={x} style={{ color: '#94a3b8' }}>{x}</p>)}
    </>}
  </section>;
}

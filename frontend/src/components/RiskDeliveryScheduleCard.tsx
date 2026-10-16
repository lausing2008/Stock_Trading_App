import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
export default function RiskDeliveryScheduleCard() {
  const [value, setValue] = useState<{ enabled: boolean; timezone: string; start: string; end: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  useEffect(() => { let live = true; api.getRiskDeliverySchedule().then(x => { if (live) setValue(x); })
    .catch(() => { if (live) setError('Quiet-hour preferences could not be loaded.'); }); return () => { live = false; }; }, []);
  async function save() {
    if (!value) return; setBusy(true); setError(''); setSaved(false);
    try { setValue(await api.setRiskDeliverySchedule(value)); setSaved(true); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save quiet hours'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Price-risk email quiet hours" style={{ padding: 12, margin: '12px 0', border: '1px solid #334155', borderRadius: 8, color: '#cbd5e1', fontSize: 12 }}>
    <strong>Price-risk email quiet hours</strong>
    <p>This schedule does not enable delivery or subscribe you. In-app history stays available. Queued mail waits; evidence that expires while waiting is not sent.</p>
    {value && <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
      <label><input type="checkbox" checked={value.enabled} onChange={e => { setSaved(false); setValue({ ...value, enabled: e.target.checked }); }} /> Pause price-risk email during quiet hours</label>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '8px 0' }}>
        <label>Time zone <input aria-label="Quiet-hour time zone" value={value.timezone} onChange={e => { setSaved(false); setValue({ ...value, timezone: e.target.value }); }} /></label>
        <label>Start <input aria-label="Quiet-hour start" type="time" value={value.start} onChange={e => { setSaved(false); setValue({ ...value, start: e.target.value }); }} /></label>
        <label>End <input aria-label="Quiet-hour end" type="time" value={value.end} onChange={e => { setSaved(false); setValue({ ...value, end: e.target.value }); }} /></label>
      </div>
      <button onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save quiet hours'}</button>
      {saved && <span role="status"> Quiet hours saved.</span>}
    </fieldset>}
    {error && <p role="alert" style={{ color: '#fca5a5' }}>{error}</p>}
  </section>;
}

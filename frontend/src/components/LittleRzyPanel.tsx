import React, { useState } from 'react';
import useSWR from 'swr';
import { api } from '@/lib/api';
import type { RzyPreferences } from '@/lib/rzy';
import Link from 'next/link';
import { getSession } from '@/lib/auth';

export default function LittleRzyPanel() {
  const { data: preferences, error, mutate } = useSWR('rzy-preferences', api.rzyPreferences);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const isAdmin = getSession()?.role === 'admin';
  async function update(patch: Partial<RzyPreferences>) {
    if (!preferences) return; setBusy(true); setMessage('');
    try { await mutate(await api.saveRzyPreferences({ ...preferences, ...patch }), false); setMessage('Preferences saved.'); }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Could not save preferences.'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Short-term trading and Little RZY" style={{ padding: 16, border: '1px solid #334155', borderRadius: 8, margin: '16px 0' }}>
    <h2>Short-term trading & Little RZY-inspired</h2>
    <p>Independent rule-based continuation signals in either direction. Reversal watches do not open trades. No live broker execution.</p>
    <>
      <p>Morning preparation: your US watchlist, 20 minutes before regular open (normally 09:10 Eastern / 06:10 Pacific), on trading days.</p>
      {error && <p role="alert">Email preferences could not be loaded. Nothing was enabled.</p>}
      {!preferences && !error && <p>Loading preferences…</p>}
      {preferences && <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
        <label style={{ display: 'block' }}><input type="checkbox" checked={preferences.morning_digest} onChange={e => void update({ morning_digest: e.target.checked })} /> Morning short-term watchlist digest</label>
        <label style={{ display: 'block' }}><input type="checkbox" checked={preferences.pattern_email} onChange={e => void update({ pattern_email: e.target.checked })} /> Little RZY continuation and reversal-watch emails</label>
        <p>Alert timeframes:</p>{['5m','15m','1h','1d'].map(tf => <label key={tf} style={{ marginRight: 12 }}><input type="checkbox" checked={preferences.timeframes.includes(tf)} onChange={e => {
          const next = e.target.checked ? [...preferences.timeframes, tf] : preferences.timeframes.filter(x => x !== tf);
          if (!next.length) { setMessage('Keep at least one timeframe.'); return; } void update({ timeframes: next });
        }} /> {tf}</label>)}
      </fieldset>}
    </>
    {isAdmin && <p>The digest preview, research alerts, data health and research paper ledger are on <Link href="/little-rzy" style={{ color: '#2dd4bf' }}>Admin → Little RZY</Link>.</p>}
    {message && <p role="status">{message}</p>}
    <details><summary>How the signals work</summary><p>Confirmed 7-left/7-right pivots; 20-period Bollinger Bands at 2 population standard deviations; Wilder ATR 14. Anchors and targets freeze at confirmation. Entry waits for a later completed trendline-rejection candle. Paper fills, if eligible, occur at the next candle open with costs. Defaults are research assumptions, not measured win rates.</p></details>
  </section>;
}

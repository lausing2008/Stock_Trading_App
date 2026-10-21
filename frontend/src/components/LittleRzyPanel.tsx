import React, { useState } from 'react';
import useSWR from 'swr';
import { api } from '@/lib/api';
import type { RzyPreferences } from '@/lib/rzy';

export default function LittleRzyPanel({ paperOnly = false }: { paperOnly?: boolean }) {
  const { data: preferences, error, mutate } = useSWR(paperOnly ? null : 'rzy-preferences', api.rzyPreferences);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.rzyDigest>> | null>(null);
  const [paper, setPaper] = useState<Awaited<ReturnType<typeof api.rzyPaper>> | null>(null);
  async function update(patch: Partial<RzyPreferences>) {
    if (!preferences) return; setBusy(true); setMessage('');
    try { await mutate(await api.saveRzyPreferences({ ...preferences, ...patch }), false); setMessage('Preferences saved.'); }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Could not save preferences.'); }
    finally { setBusy(false); }
  }
  async function loadPreview() { setBusy(true); setMessage(''); try { setPreview(await api.rzyDigest()); } catch { setMessage('Digest preview unavailable.'); } finally { setBusy(false); } }
  async function loadPaper() { setBusy(true); setMessage(''); try { setPaper(await api.rzyPaper()); } catch { setMessage('Research paper ledger unavailable.'); } finally { setBusy(false); } }
  return <section aria-label="Short-term trading and Little RZY" style={{ padding: 16, border: '1px solid #334155', borderRadius: 8, margin: '16px 0' }}>
    <h2>Short-term trading & Little RZY-inspired</h2>
    <p>Independent rule-based continuation signals in either direction. Reversal watches do not open trades. No live broker execution.</p>
    {!paperOnly && <>
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
      <p><button disabled={busy} onClick={() => void loadPreview()}>Preview morning candidates</button></p>
      {preview && preview.available === false && <p role="status">{preview.message ?? 'Preview not available yet.'}</p>}
      {preview && preview.available !== false && <div>
        {preview.snapshot_from && <p role="status">Saved scan snapshot, not recomputed now: oldest stream checked {new Date(preview.snapshot_from).toLocaleString()} ({preview.snapshot_age_minutes} min ago){preview.snapshot_to && preview.snapshot_to !== preview.snapshot_from ? `, newest ${new Date(preview.snapshot_to).toLocaleString()}` : ''}; {preview.streams_with_snapshot} of {preview.streams_expected} streams have a snapshot.</p>}
        <p>{preview.note}</p>{preview.candidates.length ? preview.candidates.map(c => <p key={`${c.symbol}:${c.timeframe}`}><strong>{c.symbol} · {c.direction.toUpperCase()} · {c.timeframe}</strong> · entry reference {c.entry_reference?.toFixed(2) ?? "unavailable"} · provisional stop {c.provisional_stop?.toFixed(2) ?? "unavailable"} · target {c.target.toFixed(2)} · planned R/R {c.planned_reward_risk?.toFixed(2) ?? "unavailable"}. {c.guidance}</p>) : <p>No qualifying candidates. Do not force a trade.</p>}
        <details><summary>Excluded symbols and reasons ({preview.exclusions.length})</summary>{preview.exclusions.map((x,i) => <p key={i}>{x.symbol} {x.timeframe}: {x.reason}</p>)}</details></div>}
    </>}
    <button disabled={busy} onClick={() => void loadPaper()}>Show RZY research paper ledger</button>
    {paper && <div><p>{paper.note}</p>{paper.positions.length ? paper.positions.map((p,i) => <p key={i}>{p.symbol} {p.timeframe} · {p.state} {p.cancelled ? '(cancelled)' : ''} · {p.reason ?? ''}{p.fill != null && ` · simulated fill ${p.fill.toFixed(4)}`}{p.net != null && ` · net per unit ${p.net.toFixed(4)}`}</p>) : <p>No research paper positions yet.</p>}</div>}
    {message && <p role="status">{message}</p>}
    <details><summary>How the signals work</summary><p>Confirmed 7-left/7-right pivots; 20-period Bollinger Bands at 2 population standard deviations; Wilder ATR 14. Anchors and targets freeze at confirmation. Entry waits for a later completed trendline-rejection candle. Paper fills, if eligible, occur at the next candle open with costs. Defaults are research assumptions, not measured win rates.</p></details>
  </section>;
}

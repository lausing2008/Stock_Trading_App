import React, { useEffect, useRef, useState } from 'react';
import { api, type OptionSnapshot } from '@/lib/api';
import { useNow } from '@/lib/useNow';

export default function OptionsSnapshotControls({ symbol, snapshot, onSnapshot, expiries = [] }: {
  symbol: string; expiries?: string[]; snapshot?: OptionSnapshot; onSnapshot: (value: OptionSnapshot | undefined) => void;
}) {
  const now = useNow(1000);
  const [scopeExpiry, setScopeExpiry] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAt, setRetryAt] = useState(0);
  const generation = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => {
    setPending(false); setError(null); setRetryAt(0); setScopeExpiry('');
    return () => { generation.current += 1; inFlight.current = false; };
  }, [symbol]);
  const wait = Math.max(0, Math.ceil((Math.max(retryAt, snapshot ? Date.parse(snapshot.refresh_after) || 0 : 0) - now) / 1000));
  const expired = snapshot && !(Date.parse(snapshot.expires_at) > now);
  const refresh = async () => {
    if (inFlight.current || wait) return;
    inFlight.current = true;
    const request = ++generation.current;
    setPending(true); setError(null);
    try {
      const result = await api.refreshOptionsChain(symbol, ...(scopeExpiry ? [scopeExpiry] as const : []));
      if (request !== generation.current) return;
      if (!result.available || !result.snapshot || result.snapshot.symbol !== symbol || !/^[0-9a-f]{32}$/.test(result.snapshot.id) || !result.snapshot.pagination_complete || result.snapshot.contracts < 1) {
        setRetryAt(Date.now() + (result.retry_after ?? 10) * 1000);
        throw new Error(result.message ?? 'The provider did not return a matching complete snapshot.');
      }
      onSnapshot(result.snapshot);
    } catch (e) {
      if (request === generation.current) {
        const message = e instanceof Error ? e.message : 'Option-chain refresh failed.';
        setError(message);
        setRetryAt(previous => Math.max(previous, Date.now() + (/^429\b/.test(message) ? 60_000 : 10_000)));
      }
    } finally {
      if (request === generation.current) { inFlight.current = false; setPending(false); }
    }
  };
  const actionStyle: React.CSSProperties = { padding: '8px 12px', borderRadius: 6, border: '1px solid #0ea5e9', background: '#075985', color: '#f8fafc', fontWeight: 600, cursor: pending || wait ? 'default' : 'pointer', opacity: pending || wait ? .65 : 1 };
  return <section aria-label="Option chain refresh" aria-busy={pending} style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: 14 }}>
    <label style={{ display: 'block', marginBottom: 10, fontSize: 12, fontWeight: 600 }}>Refresh expiration scope <select style={{ display: 'block', width: '100%', maxWidth: 480, minWidth: 0, marginTop: 5, padding: 6, borderRadius: 6, background: '#0b1220', color: '#e2e8f0', border: '1px solid #475569' }} aria-label="Refresh expiration scope" disabled={pending} value={scopeExpiry} onChange={e => setScopeExpiry(e.target.value)}>
      <option value="">All available expirations (bounded complete capture)</option>
      {[...new Set([...(snapshot?.current_expiries ?? snapshot?.expiries ?? []), ...expiries])].filter(exp => exp >= new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now))).sort().map(exp => <option key={exp} value={exp}>{exp} only</option>)}
    </select></label>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><button style={actionStyle} type="button" disabled={pending || wait > 0} onClick={() => void refresh()}>
      {pending ? 'Refreshing option chain…' : wait ? `Refresh available in ${wait}s` : 'Refresh option chain & recalculate'}
    </button>
    {snapshot && <button type="button" disabled={pending} style={{ ...actionStyle, borderColor: '#475569', background: '#1e293b', opacity: pending ? .65 : 1 }} onClick={() => { setError(null); onSnapshot(undefined); }}>Use settled archive</button>}</div>
    {error && <p role="alert">Refresh failed: {error} Previous data remains displayed; it has not been marked as refreshed.</p>}
    <div role="status" style={{ fontSize: 12, color: expired ? '#fbbf24' : '#cbd5e1', marginTop: 10 }}>
      {snapshot ? <>
        <p>Scope: {snapshot.capture_expiry ? `${snapshot.capture_expiry} only` : 'all provider expirations in this capture'}. A single-expiration capture cannot compare other expirations.</p>
        <strong>{expired ? 'Snapshot expired — refresh required.' : 'Latest available provider capture'}</strong>
        <p>Captured {snapshot.captured_at} · {snapshot.contracts.toLocaleString()} supported contracts across {snapshot.expiries.length} expirations · {snapshot.pages} pages.
          {' '}{snapshot.rejected_contracts > 0 && `${snapshot.rejected_contracts} unreadable or unsupported rows excluded.`}</p>
        <p>Stock reference ${snapshot.stock_reference.toFixed(2)} · reported stock time {snapshot.stock_observed_at}.
          {' '}Latest reported trade session {snapshot.source_session}; this is not a verified quote timestamp.</p>
        <p>{snapshot.limitations}</p>
      </> : <p>Settled archive mode. Refresh requests the provider’s latest available bid/ask and Greeks; it may still return the last market session when markets are closed.</p>}
    </div>
    <p style={{ fontSize: 12, color: '#94a3b8' }}>A complete capture updates the chain table, gain/loss option prices and strategy calculations together. Previous contract choices and assignment acceptance reset. Gain/loss calculator fills and what-if prices stay yours. Flow, pressure and event panels retain their own source dates.</p>
    <p style={{ fontSize: 12, color: '#94a3b8' }}>For a conditional best fit, choose your objective, holdings, budget and holding period below. The shortlist ranks reference distance, spread and open interest within your bounds; it does not forecast the highest return. Entry remains WAIT for fresh broker checks.</p>
  </section>;
}

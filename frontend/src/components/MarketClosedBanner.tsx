'use client';
import { useState, useEffect } from 'react';

function getMarketState(): { anyOpen: boolean; usOpen: boolean; hkOpen: boolean; usPreMkt: boolean; nextEvent: string } {
  const now = new Date();
  const utcMin = now.getUTCHours() * 60 + now.getUTCMinutes();
  const day = now.getUTCDay();
  const weekday = day >= 1 && day <= 5;

  const etMin = ((utcMin - 4 * 60) + 1440) % 1440;
  const usOpen = weekday && etMin >= 9 * 60 + 30 && etMin < 16 * 60;
  const usPreMkt = weekday && etMin >= 4 * 60 && etMin < 9 * 60 + 30;

  const hktMin = (utcMin + 8 * 60) % 1440;
  const hkOpen = weekday && (
    (hktMin >= 9 * 60 + 30 && hktMin < 12 * 60) ||
    (hktMin >= 13 * 60 && hktMin < 16 * 60)
  );

  let nextEvent = '';
  if (!weekday) {
    nextEvent = 'Opens Monday';
  } else if (usPreMkt) {
    nextEvent = 'US opens at 9:30 AM ET';
  } else if (!usOpen && etMin < 9 * 60 + 30) {
    nextEvent = 'US opens at 9:30 AM ET';
  } else if (!usOpen && etMin >= 16 * 60) {
    nextEvent = 'US opens tomorrow 9:30 AM ET';
  }

  return { anyOpen: usOpen || hkOpen, usOpen, hkOpen, usPreMkt, nextEvent };
}

export default function MarketClosedBanner() {
  const [dismissed, setDismissed] = useState(false);
  const [state, setState] = useState(() => getMarketState());

  useEffect(() => {
    const id = setInterval(() => setState(getMarketState()), 60_000);
    return () => clearInterval(id);
  }, []);

  if (state.anyOpen || dismissed) return null;

  const label = state.usPreMkt ? 'Pre-Market' : 'Markets Closed';
  const sub = state.usPreMkt
    ? 'US pre-market is active. Regular session opens at 9:30 AM ET.'
    : `Both US and HK markets are closed. ${state.nextEvent ? state.nextEvent + '.' : ''} Prices shown may be delayed.`;

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      background: 'rgba(71,85,105,0.18)', border: '1px solid rgba(71,85,105,0.35)',
      borderRadius: '8px', padding: '8px 14px', marginBottom: '16px',
      fontSize: '12px', color: '#94a3b8', gap: '8px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{
          fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px',
          color: '#475569', background: 'rgba(71,85,105,0.25)', letterSpacing: '0.05em',
        }}>
          ● {label}
        </span>
        <span>{sub}</span>
      </div>
      <button
        onClick={() => setDismissed(true)}
        style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '14px', padding: '0 2px', lineHeight: 1 }}
        aria-label="Dismiss"
      >×</button>
    </div>
  );
}

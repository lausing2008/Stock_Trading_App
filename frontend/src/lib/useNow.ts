// Audit R02 (2026-10-22): a clock that ticks, so ages and expiry eligibility are recomputed in an
// open tab instead of freezing at the moment the response arrived.
import { useEffect, useState } from 'react';

export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** SWR refresh cadence per feed, matching each backend cache. Refreshes run only while the page
 * is visible (SWR's default `refreshWhenHidden: false`). */
export const REFRESH_MS = {
  optionsFlow: 15 * 60_000,      // _OPTIONS_TTL
  gammaExposure: 60_000,         // NOPE's own 1-minute server cache; GEX itself is cached 15 min
  darkPool: 15 * 60_000,         // _DARK_POOL_TTL
  expirations: 60 * 60_000,      // a settled-session rollup
} as const;

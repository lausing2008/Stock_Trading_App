import type { Price } from './api';

export type SwingPivot = {
  idx: number;      // index into the same bars[] array this was computed from
  ts: string;
  price: number;
  kind: 'high' | 'low';
};

/**
 * Port of services/technical-analysis/src/indicators/trendlines.py's _find_pivots() — a
 * local max/min within +-order bars. Ported client-side (rather than a new backend endpoint)
 * because the consumer (Fixed Range VP's click-snap) needs pivots indexed against the exact
 * same activePrices[] array PriceChart.tsx already has in memory, matching how volumeProfile.ts
 * and indicators.ts already do local computation instead of a chart-only-feature round-trip.
 *
 * Detects on high/low (not close) — matching trendlines.py's own T247-TA-CLUSTERPIVOTS-CLOSE-
 * HIGH-MISMATCH fix (_cluster_pivots reads high/low, not detect_trendlines' close-based pivots,
 * which serve a different purpose). A "swing high/low" a user wants to click-snap to is the
 * bar's actual high/low extremum, not wherever it happened to close.
 */
export function detectSwingPivots(bars: Price[], order = 5): SwingPivot[] {
  const n = bars.length;
  const pivots: SwingPivot[] = [];
  if (n < order * 2 + 1) return pivots;

  for (let i = order; i < n - order; i++) {
    let isHigh = true;
    let isLow = true;
    const hi = bars[i].high;
    const lo = bars[i].low;
    for (let j = i - order; j <= i + order; j++) {
      if (j === i) continue;
      if (bars[j].high > hi) isHigh = false;
      if (bars[j].low < lo) isLow = false;
    }
    if (isHigh) pivots.push({ idx: i, ts: bars[i].ts, price: hi, kind: 'high' });
    if (isLow) pivots.push({ idx: i, ts: bars[i].ts, price: lo, kind: 'low' });
  }
  return pivots;
}

/** Nearest pivot to a given bar index, within maxDistance bars — used to snap a raw click
 * (an arbitrary bar index) onto the nearest real swing extremum instead of requiring
 * pixel-perfect manual clicking. Returns null if nothing qualifies within range. */
export function nearestPivot(pivots: SwingPivot[], targetIdx: number, maxDistance = 10): SwingPivot | null {
  let best: SwingPivot | null = null;
  let bestDist = Infinity;
  for (const p of pivots) {
    const dist = Math.abs(p.idx - targetIdx);
    if (dist <= maxDistance && dist < bestDist) {
      best = p;
      bestDist = dist;
    }
  }
  return best;
}

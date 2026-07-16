import type { FairValueGap } from './api';

export type FvgTradePlan = {
  entry: number;
  stop: number;
  target: number;
  rr: number;
  gap: FairValueGap;
};

/**
 * Derive an entry/stop/target plan from the nearest unfilled Fair Value Gap relative to the
 * current price — the standard ICT/smart-money-concepts read: enter on a retrace INTO the gap
 * (not at its exact edge, which is rarely touched precisely), stop just beyond the gap's FAR
 * edge (if price fully closes the gap and keeps going, the setup is invalidated), target using
 * a minimum reward:risk floor derived from the gap's own size — a real, structural distance
 * already on the chart, rather than an arbitrary fixed R multiple.
 *
 * Only bullish gaps BELOW current price (price may retrace down into a support gap) and
 * bearish gaps ABOVE current price (price may retrace up into a resistance gap) are candidates
 * — a bullish gap already above price, or a bearish gap already below it, has nothing left to
 * retrace into from here and isn't a usable entry zone from the current price.
 */
export function nearestActionableFvg(
  gaps: FairValueGap[] | undefined | null,
  currentPrice: number | null | undefined,
  minRR = 1.5,
): FvgTradePlan | null {
  if (!gaps || gaps.length === 0 || currentPrice == null || currentPrice <= 0) return null;

  const candidates = gaps.filter(g => {
    if (g.filled) return false;
    if (g.kind === 'bullish') return g.top <= currentPrice; // price is above the gap, can retrace down into it
    return g.bottom >= currentPrice; // bearish: price is below the gap, can retrace up into it
  });
  if (candidates.length === 0) return null;

  // Nearest to current price (smallest distance from price to the gap's near edge) —
  // the gap most likely to actually get touched next, not the biggest or oldest one.
  candidates.sort((a, b) => {
    const distA = a.kind === 'bullish' ? currentPrice - a.top : a.bottom - currentPrice;
    const distB = b.kind === 'bullish' ? currentPrice - b.top : b.bottom - currentPrice;
    return distA - distB;
  });

  for (const gap of candidates) {
    const gapSize = gap.top - gap.bottom;
    if (gapSize <= 0) continue;

    const entry = gap.kind === 'bullish'
      ? (gap.top + gap.bottom) / 2  // midpoint retrace into a bullish (support) gap
      : (gap.top + gap.bottom) / 2; // midpoint retrace into a bearish (resistance) gap

    const stop = gap.kind === 'bullish'
      ? gap.bottom - gapSize * 0.1   // just beyond the far (lower) edge, small buffer
      : gap.top + gapSize * 0.1;     // just beyond the far (upper) edge, small buffer

    const risk = Math.abs(entry - stop);
    if (risk <= 0) continue;

    const target = gap.kind === 'bullish'
      ? entry + risk * minRR
      : entry - risk * minRR;

    const rr = Math.abs(target - entry) / risk;
    return { entry, stop, target, rr, gap };
  }
  return null;
}

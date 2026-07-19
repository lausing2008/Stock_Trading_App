import type { FairValueGap } from './api';
import type { SwingPivot } from './swingPivots';
import type { VolumeProfileResult } from './volumeProfile';

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

// ── Combination 1: FVG anchored to a real Swing Pivot ────────────────────────────────────
//
// nearestActionableFvg() picks a gap by pure price-distance to the current price — it says
// nothing about whether that gap zone is also somewhere a real discretionary trader would
// recognize as structural. A gap whose edge lines up with an actual swing high/low (the same
// points swingPivots.ts detects for Fixed Range VP) is a stronger, more corroborated level
// than one that just happens to be the nearest untraded pocket. This mirrors the same
// "anchor with real structure instead of an arbitrary point" idea already used for
// Fixed Range VP's click-snap — applied here to FVG's own pick instead of a user's click.

export type PivotAnchor = {
  pivot: SwingPivot;
  /** Absolute % distance between the pivot's price and the gap edge it was matched against. */
  distancePct: number;
};

/**
 * Checks whether a Fair Value Gap's boundary sits close to a real swing pivot's price level.
 * Compares against the gap's FAR edge (the one the stop sits beyond) — that's the edge whose
 * structural significance actually matters for the trade thesis: a bullish gap's bottom (the
 * support floor) or a bearish gap's top (the resistance ceiling), not the near edge that's
 * just wherever price happens to be retracing from right now.
 *
 * tolerancePct is expressed as a fraction of the compared price (default 0.015 = 1.5%) rather
 * than an absolute distance, since it needs to scale sensibly across very differently priced
 * stocks (a $5 stock vs. a $500 stock).
 */
export function nearestPivotToFvg(
  gap: FairValueGap,
  pivots: SwingPivot[] | undefined | null,
  tolerancePct = 0.015,
): PivotAnchor | null {
  if (!pivots || pivots.length === 0) return null;
  const farEdge = gap.kind === 'bullish' ? gap.bottom : gap.top;
  if (farEdge <= 0) return null;

  let best: SwingPivot | null = null;
  let bestDistPct = Infinity;
  for (const p of pivots) {
    const distPct = Math.abs(p.price - farEdge) / farEdge;
    if (distPct < bestDistPct) {
      best = p;
      bestDistPct = distPct;
    }
  }
  if (!best || bestDistPct > tolerancePct) return null;
  return { pivot: best, distancePct: bestDistPct };
}

// ── Combination 2: FVG vs. Volume Profile POC/HVN/thin-zone context ──────────────────────
//
// Volume Profile answers "where did the market spend the most volume agreeing on fair price."
// A Fair Value Gap that overlaps a POC/HVN band is a level with real historical volume
// conviction behind it — a much stronger candidate to actually hold on retest than a gap
// sitting in a low-volume/untraded pocket of the same profile, which the market moved through
// quickly and has comparatively little reason to respect on a revisit. This is the same
// "practical entry read" reasoning already documented for Volume Profile alone (POC/HVN act as
// support/resistance magnets; thin zones don't), applied here to grade a specific FVG zone
// instead of a generic price level.

export type VolumeContext = 'poc' | 'hvn' | 'thin' | 'unknown';

/**
 * Classifies a Fair Value Gap zone against a Volume Profile's POC/HVN bands.
 * - 'poc'     — the gap's [bottom, top] range contains the profile's POC.
 * - 'hvn'     — contains (or sits within tolerancePct of) one of the profile's HVN levels.
 * - 'thin'    — overlaps profiled buckets, but none of them are POC/HVN — a comparatively
 *               low-volume zone even though it WAS profiled.
 * - 'unknown' — the gap falls entirely outside the profile's own price range (nothing to
 *               compare against — a different range was profiled, not "definitely thin").
 */
export function classifyFvgVolumeContext(
  gap: FairValueGap,
  profile: VolumeProfileResult | null | undefined,
  tolerancePct = 0.005,
): VolumeContext {
  if (!profile) return 'unknown';
  const { bottom, top } = gap;
  const profileLow = profile.buckets[0]?.priceLow;
  const profileHigh = profile.buckets.at(-1)?.priceHigh;
  if (profileLow == null || profileHigh == null) return 'unknown';
  if (top < profileLow || bottom > profileHigh) return 'unknown'; // no overlap with the profiled range at all

  const inRange = (price: number) => price >= bottom - bottom * tolerancePct && price <= top + top * tolerancePct;
  if (inRange(profile.poc)) return 'poc';
  if (profile.hvn.some(inRange)) return 'hvn';

  const overlapsAnyBucket = profile.buckets.some(b => b.priceHigh >= bottom && b.priceLow <= top);
  return overlapsAnyBucket ? 'thin' : 'unknown';
}

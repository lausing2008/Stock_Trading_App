import { describe, it, expect } from 'vitest';
import { nearestActionableFvg, nearestPivotToFvg, classifyFvgVolumeContext } from './fvgTradePlan';
import type { FairValueGap } from './api';
import type { SwingPivot } from './swingPivots';
import type { VolumeProfileResult } from './volumeProfile';

function fvg(overrides: Partial<FairValueGap>): FairValueGap {
  return { top: 110, bottom: 100, kind: 'bullish', idx: 0, filled: false, filled_idx: null, ...overrides };
}

function pivot(overrides: Partial<SwingPivot>): SwingPivot {
  return { idx: 0, ts: '2026-01-01', price: 100, kind: 'low', ...overrides };
}

/** Build a VolumeProfileResult with buckets spanning [rangeLow, rangeHigh] at a fixed size.
 * pocPrice always gets the single highest-volume bucket (the real POC); hvnPrices (if given,
 * and distinct from pocPrice) each get a smaller local spike — a real secondary peak, not
 * just "some volume," so it's distinguishable from the flat baseline everywhere else. */
function profile(rangeLow: number, rangeHigh: number, numBuckets: number, pocPrice: number, hvnPrices: number[] = []): VolumeProfileResult {
  const bucketSize = (rangeHigh - rangeLow) / numBuckets;
  const buckets = Array.from({ length: numBuckets }, (_, i) => {
    const priceLow = rangeLow + i * bucketSize;
    const priceHigh = priceLow + bucketSize;
    const price = (priceLow + priceHigh) / 2;
    if (Math.abs(price - pocPrice) < bucketSize / 2) return { priceLow, priceHigh, price, volume: 1000 };
    if (hvnPrices.some(hp => Math.abs(price - hp) < bucketSize / 2)) return { priceLow, priceHigh, price, volume: 500 };
    return { priceLow, priceHigh, price, volume: 50 };
  });
  const totalVolume = buckets.reduce((s, b) => s + b.volume, 0);
  return { buckets, poc: pocPrice, vah: rangeHigh, val: rangeLow, hvn: hvnPrices, lvn: [], totalVolume };
}

describe('nearestActionableFvg', () => {
  it('returns null when there are no gaps', () => {
    expect(nearestActionableFvg([], 150)).toBeNull();
    expect(nearestActionableFvg(null, 150)).toBeNull();
  });

  it('returns null when current price is missing or non-positive', () => {
    expect(nearestActionableFvg([fvg({})], null)).toBeNull();
    expect(nearestActionableFvg([fvg({})], 0)).toBeNull();
  });

  it('picks a bullish gap below current price as a long entry zone', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish' });
    const plan = nearestActionableFvg([gap], 100);
    expect(plan).not.toBeNull();
    expect(plan!.gap).toBe(gap);
    expect(plan!.entry).toBeCloseTo(92.5, 5); // midpoint
    expect(plan!.stop).toBeLessThan(gap.bottom); // beyond the far (lower) edge
    expect(plan!.target).toBeGreaterThan(plan!.entry); // long target above entry
  });

  it('picks a bearish gap above current price as a short entry zone', () => {
    const gap = fvg({ top: 110, bottom: 105, kind: 'bearish' });
    const plan = nearestActionableFvg([gap], 100);
    expect(plan).not.toBeNull();
    expect(plan!.entry).toBeCloseTo(107.5, 5);
    expect(plan!.stop).toBeGreaterThan(gap.top); // beyond the far (upper) edge
    expect(plan!.target).toBeLessThan(plan!.entry); // short target below entry
  });

  it('ignores a bullish gap already above current price (nothing to retrace into)', () => {
    const gap = fvg({ top: 120, bottom: 115, kind: 'bullish' });
    expect(nearestActionableFvg([gap], 100)).toBeNull();
  });

  it('ignores a bearish gap already below current price', () => {
    const gap = fvg({ top: 90, bottom: 85, kind: 'bearish' });
    expect(nearestActionableFvg([gap], 100)).toBeNull();
  });

  it('ignores filled gaps', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish', filled: true, filled_idx: 42 });
    expect(nearestActionableFvg([gap], 100)).toBeNull();
  });

  it('picks the gap nearest to current price when multiple are actionable', () => {
    const near = fvg({ top: 98, bottom: 96, kind: 'bullish', idx: 1 });
    const far = fvg({ top: 80, bottom: 75, kind: 'bullish', idx: 2 });
    const plan = nearestActionableFvg([far, near], 100);
    expect(plan!.gap).toBe(near);
  });

  it('respects a custom minRR for the target distance', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish' });
    const plan = nearestActionableFvg([gap], 100, 2.0);
    expect(plan).not.toBeNull();
    const risk = Math.abs(plan!.entry - plan!.stop);
    const reward = Math.abs(plan!.target - plan!.entry);
    expect(reward / risk).toBeCloseTo(2.0, 5);
  });

  it('skips a degenerate zero-size gap', () => {
    const gap = fvg({ top: 95, bottom: 95, kind: 'bullish' });
    expect(nearestActionableFvg([gap], 100)).toBeNull();
  });
});

describe('nearestPivotToFvg', () => {
  it('returns null when there are no pivots', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish' });
    expect(nearestPivotToFvg(gap, [])).toBeNull();
    expect(nearestPivotToFvg(gap, null)).toBeNull();
  });

  it('matches a bullish gap against its FAR (bottom) edge, not the near edge', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish' });
    const farPivot = pivot({ price: 90.2, kind: 'low' }); // very close to bottom (far edge)
    const nearPivot = pivot({ price: 95.5, kind: 'high' }); // close to top (near edge) — should NOT match
    const anchor = nearestPivotToFvg(gap, [nearPivot, farPivot]);
    expect(anchor).not.toBeNull();
    expect(anchor!.pivot).toBe(farPivot);
  });

  it('matches a bearish gap against its FAR (top) edge', () => {
    const gap = fvg({ top: 110, bottom: 105, kind: 'bearish' });
    const farPivot = pivot({ price: 110.3, kind: 'high' });
    const anchor = nearestPivotToFvg(gap, [farPivot]);
    expect(anchor).not.toBeNull();
    expect(anchor!.pivot).toBe(farPivot);
    expect(anchor!.distancePct).toBeCloseTo(0.3 / 110, 5); // |110.3 - 110| / farEdge(=gap.top=110)
  });

  it('returns null when the nearest pivot is outside tolerancePct', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish' });
    const farPivot = pivot({ price: 80, kind: 'low' }); // 11%+ away — well outside default 1.5%
    expect(nearestPivotToFvg(gap, [farPivot])).toBeNull();
  });

  it('picks the closest pivot among several candidates', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish' });
    const closer = pivot({ price: 90.1, kind: 'low', idx: 1 });
    const farther = pivot({ price: 89.5, kind: 'low', idx: 2 });
    const anchor = nearestPivotToFvg(gap, [farther, closer]);
    expect(anchor!.pivot).toBe(closer);
  });

  it('respects a custom tolerancePct', () => {
    const gap = fvg({ top: 95, bottom: 90, kind: 'bullish' });
    const farPivot = pivot({ price: 88, kind: 'low' }); // ~2.2% from bottom
    expect(nearestPivotToFvg(gap, [farPivot], 0.015)).toBeNull(); // outside default tolerance
    expect(nearestPivotToFvg(gap, [farPivot], 0.03)).not.toBeNull(); // inside a looser tolerance
  });
});

describe('classifyFvgVolumeContext', () => {
  it('returns "unknown" when there is no profile', () => {
    const gap = fvg({ top: 95, bottom: 90 });
    expect(classifyFvgVolumeContext(gap, null)).toBe('unknown');
    expect(classifyFvgVolumeContext(gap, undefined)).toBe('unknown');
  });

  it('returns "unknown" when the gap falls entirely outside the profiled range', () => {
    const gap = fvg({ top: 200, bottom: 195 });
    const p = profile(80, 120, 20, 100);
    expect(classifyFvgVolumeContext(gap, p)).toBe('unknown');
  });

  it('classifies "poc" when the gap zone contains the profile POC', () => {
    const p = profile(80, 120, 20, 100); // POC spike at 100
    const gap = fvg({ top: 102, bottom: 98 }); // brackets 100
    expect(classifyFvgVolumeContext(gap, p)).toBe('poc');
  });

  it('classifies "hvn" when the gap zone contains an HVN but not the POC', () => {
    const p = profile(80, 120, 20, 100, [105]); // real POC at 100, secondary HVN spike at 105
    const gap = fvg({ top: 107, bottom: 103 }); // brackets the HVN at 105, not the POC at 100
    expect(classifyFvgVolumeContext(gap, p)).toBe('hvn');
  });

  it('classifies "thin" when the gap overlaps the profile but hits neither POC nor HVN', () => {
    const p = profile(80, 120, 20, 100); // spike far from the gap below
    const gap = fvg({ top: 85, bottom: 82 }); // low-volume baseline zone, but still in-range
    expect(classifyFvgVolumeContext(gap, p)).toBe('thin');
  });
});

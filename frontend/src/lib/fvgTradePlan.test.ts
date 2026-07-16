import { describe, it, expect } from 'vitest';
import { nearestActionableFvg } from './fvgTradePlan';
import type { FairValueGap } from './api';

function fvg(overrides: Partial<FairValueGap>): FairValueGap {
  return { top: 110, bottom: 100, kind: 'bullish', idx: 0, filled: false, filled_idx: null, ...overrides };
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

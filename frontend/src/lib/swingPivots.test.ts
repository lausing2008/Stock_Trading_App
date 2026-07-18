import { describe, it, expect } from 'vitest';
import { detectSwingPivots, nearestPivot } from './swingPivots';
import type { Price } from './api';

function bar(high: number, low: number, ts: string): Price {
  return { ts, open: (high + low) / 2, high, low, close: (high + low) / 2, volume: 1000 };
}

// A clean up-down-up-down zigzag so pivot bars are unambiguous.
function zigzag(): Price[] {
  const highs = [100, 102, 104, 106, 108, 106, 104, 102, 100, 102, 104, 106];
  const lows =  [ 95,  97,  99, 101, 103, 101,  99,  97,  95,  97,  99, 101];
  return highs.map((h, i) => bar(h, lows[i], `2026-01-${String(i + 1).padStart(2, '0')}`));
}

describe('detectSwingPivots', () => {
  it('returns empty for too few bars (fewer than 2*order+1)', () => {
    const bars = zigzag().slice(0, 5);
    expect(detectSwingPivots(bars, 5)).toEqual([]);
  });

  it('marks the real local max as a high pivot', () => {
    const bars = zigzag();
    const pivots = detectSwingPivots(bars, 3);
    const highs = pivots.filter(p => p.kind === 'high');
    // index 4 (high=108) is the peak of the first hump, within order=3 of both edges checked
    expect(highs.some(p => p.idx === 4 && p.price === 108)).toBe(true);
  });

  it('marks the real local min as a low pivot', () => {
    const bars = zigzag();
    const pivots = detectSwingPivots(bars, 3);
    const lows = pivots.filter(p => p.kind === 'low');
    // index 8 (low=95) is the trough between the two humps
    expect(lows.some(p => p.idx === 8 && p.price === 95)).toBe(true);
  });

  it('never flags a strictly monotonic run as a pivot', () => {
    // Strictly increasing highs/lows — no interior bar is ever both >= AND the true extremum
    // except possibly the very edges, which are excluded by the +-order window entirely.
    const bars = Array.from({ length: 15 }, (_, i) => bar(100 + i, 95 + i, `2026-02-${String(i + 1).padStart(2, '0')}`));
    const pivots = detectSwingPivots(bars, 3);
    // A monotonic run has no local max/min in the interior at all.
    expect(pivots.length).toBe(0);
  });

  it('excludes the first and last `order` bars from consideration (matches the Python range(order, n-order))', () => {
    const bars = zigzag();
    const pivots = detectSwingPivots(bars, 3);
    expect(pivots.every(p => p.idx >= 3 && p.idx <= bars.length - 1 - 3)).toBe(true);
  });

  it('carries the correct ts through to the pivot record', () => {
    const bars = zigzag();
    const pivots = detectSwingPivots(bars, 3);
    const peak = pivots.find(p => p.idx === 4)!;
    expect(peak.ts).toBe(bars[4].ts);
  });
});

describe('nearestPivot', () => {
  const pivots = [
    { idx: 4, ts: 'a', price: 108, kind: 'high' as const },
    { idx: 8, ts: 'b', price: 95, kind: 'low' as const },
  ];

  it('returns the closest pivot within maxDistance', () => {
    expect(nearestPivot(pivots, 5, 10)).toEqual(pivots[0]);
    expect(nearestPivot(pivots, 9, 10)).toEqual(pivots[1]);
  });

  it('returns null when nothing is within maxDistance', () => {
    expect(nearestPivot(pivots, 50, 5)).toBeNull();
  });

  it('picks the strictly nearer pivot when two are both in range', () => {
    // idx=6 is 2 away from idx=4 and 2 away from idx=8 — tie goes to whichever is checked
    // first in iteration order (idx=4, the first element), matching a stable "first-wins" tie-break.
    expect(nearestPivot(pivots, 6, 10)).toEqual(pivots[0]);
  });

  it('returns null for an empty pivot list', () => {
    expect(nearestPivot([], 5, 10)).toBeNull();
  });
});

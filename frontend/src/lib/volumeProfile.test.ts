import { describe, it, expect } from 'vitest';
import { computeVolumeProfile, sessionBars } from './volumeProfile';
import type { Price } from './api';

function bar(high: number, low: number, volume: number, ts = '2026-01-01'): Price {
  return { ts, open: (high + low) / 2, high, low, close: (high + low) / 2, volume };
}

describe('computeVolumeProfile', () => {
  it('returns null for an empty bar list', () => {
    expect(computeVolumeProfile([])).toBeNull();
  });

  it('returns null for a degenerate single flat bar (high === low)', () => {
    // rangeHigh === rangeLow, nothing to bucket
    expect(computeVolumeProfile([bar(100, 100, 500)], 10)).toBeNull();
  });

  it('places POC near the price level with concentrated volume', () => {
    const bars = [
      bar(110, 100, 1000), // spreads across the low bucket range
      bar(101, 100, 9000), // concentrated near 100 — should dominate POC
    ];
    const r = computeVolumeProfile(bars, 10)!;
    expect(r).not.toBeNull();
    expect(r.poc).toBeLessThan(105);
    expect(r.totalVolume).toBe(10000);
  });

  it('brackets POC with VAH >= POC >= VAL and encloses >= the requested value-area volume', () => {
    const bars = Array.from({ length: 20 }, (_, i) =>
      bar(100 + i + 1, 100 + i, i === 10 ? 5000 : 100)
    );
    const r = computeVolumeProfile(bars, 20)!;
    expect(r.val).toBeLessThanOrEqual(r.poc);
    expect(r.poc).toBeLessThanOrEqual(r.vah);
    const areaVolume = r.buckets
      .filter(b => b.price >= r.val && b.price <= r.vah)
      .reduce((s, b) => s + b.volume, 0);
    expect(areaVolume / r.totalVolume).toBeGreaterThanOrEqual(0.70 - 1e-9);
  });

  it('detects HVN/LVN as real interior local peaks/troughs', () => {
    const bars = [
      bar(105, 100, 100),
      bar(110, 105, 500), // peak
      bar(115, 110, 50),  // trough
      bar(120, 115, 400), // peak
    ];
    const r = computeVolumeProfile(bars, 4)!;
    expect(r.hvn.length).toBeGreaterThanOrEqual(1);
  });

  it('ignores zero-volume bars without crashing or contributing volume', () => {
    const bars = [bar(105, 100, 0), bar(110, 105, 1000)];
    const r = computeVolumeProfile(bars, 10)!;
    expect(r).not.toBeNull();
    expect(r.totalVolume).toBe(1000);
  });

  it('every bucket has priceLow < priceHigh and price at the midpoint', () => {
    const bars = [bar(110, 100, 1000)];
    const r = computeVolumeProfile(bars, 5)!;
    for (const b of r.buckets) {
      expect(b.priceLow).toBeLessThan(b.priceHigh);
      expect(b.price).toBeCloseTo((b.priceLow + b.priceHigh) / 2);
    }
  });
});

describe('sessionBars', () => {
  it('returns an empty array for an empty input', () => {
    expect(sessionBars([])).toEqual([]);
  });

  it('slices to only the bars from the most recent calendar date', () => {
    const bars = [
      bar(100, 99, 10, '2026-01-01T09:00:00'),
      bar(101, 100, 10, '2026-01-01T15:00:00'),
      bar(102, 101, 10, '2026-01-02T09:00:00'),
      bar(103, 102, 10, '2026-01-02T15:00:00'),
    ];
    const result = sessionBars(bars);
    expect(result).toHaveLength(2);
    expect(result.every(b => b.ts.startsWith('2026-01-02'))).toBe(true);
  });

  it('returns all bars when every bar is on the same date', () => {
    const bars = [bar(100, 99, 10, '2026-01-01T09:00:00'), bar(101, 100, 10, '2026-01-01T15:00:00')];
    expect(sessionBars(bars)).toHaveLength(2);
  });
});

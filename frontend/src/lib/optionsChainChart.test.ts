import { describe, it, expect } from 'vitest';
import { aggregateOiByStrike, maxOiAcrossStrikes, fmtOi, labelStepFor, hasNoRealOi } from './optionsChainChart';
import type { OptionsChainRow } from './api';

function row(strike: number, oi: number): OptionsChainRow {
  return { strike, bid: 0, ask: 0, last_price: 0, volume: 0, oi, iv: 0, itm: false };
}

describe('aggregateOiByStrike', () => {
  it('returns one point per distinct strike across both sides, sorted ascending', () => {
    const calls = [row(110, 500), row(100, 200)];
    const puts = [row(105, 300)];
    const points = aggregateOiByStrike(calls, puts);
    expect(points.map(p => p.strike)).toEqual([100, 105, 110]);
  });

  it('assigns callOi/putOi correctly per strike, zero when a side has no row at that strike', () => {
    const calls = [row(100, 500)];
    const puts = [row(100, 300)];
    const points = aggregateOiByStrike(calls, puts);
    expect(points).toEqual([{ strike: 100, callOi: 500, putOi: 300 }]);
  });

  it('a strike present only on the calls side has putOi=0, not undefined or NaN', () => {
    const points = aggregateOiByStrike([row(100, 500)], []);
    expect(points).toEqual([{ strike: 100, callOi: 500, putOi: 0 }]);
  });

  it('a strike present only on the puts side has callOi=0', () => {
    const points = aggregateOiByStrike([], [row(100, 300)]);
    expect(points).toEqual([{ strike: 100, callOi: 0, putOi: 300 }]);
  });

  it('sums OI when the same strike appears more than once on one side', () => {
    const calls = [row(100, 500), row(100, 250)];
    const points = aggregateOiByStrike(calls, []);
    expect(points).toEqual([{ strike: 100, callOi: 750, putOi: 0 }]);
  });

  it('returns an empty array when both sides are empty', () => {
    expect(aggregateOiByStrike([], [])).toEqual([]);
  });
});

describe('maxOiAcrossStrikes', () => {
  it('finds the largest OI value across either side, at any strike', () => {
    const points = aggregateOiByStrike([row(100, 500), row(110, 900)], [row(100, 300)]);
    expect(maxOiAcrossStrikes(points)).toBe(900);
  });

  it('a put-side max still wins over a smaller call-side value', () => {
    const points = aggregateOiByStrike([row(100, 200)], [row(100, 900)]);
    expect(maxOiAcrossStrikes(points)).toBe(900);
  });

  it('returns 1 (never 0) for an empty points array, so callers can safely divide by it', () => {
    expect(maxOiAcrossStrikes([])).toBe(1);
  });

  it('returns 1 when every point has zero OI on both sides', () => {
    const points = aggregateOiByStrike([row(100, 0)], [row(100, 0)]);
    expect(maxOiAcrossStrikes(points)).toBe(1);
  });
});

describe('fmtOi', () => {
  it('formats millions with one decimal and an M suffix', () => {
    expect(fmtOi(1_500_000)).toBe('1.5M');
  });

  it('formats thousands rounded with a K suffix', () => {
    expect(fmtOi(42_000)).toBe('42K');
  });

  it('formats sub-thousand values as a plain integer string', () => {
    expect(fmtOi(900)).toBe('900');
  });

  it('formats zero as a plain "0", not "0K" or "NaN"', () => {
    expect(fmtOi(0)).toBe('0');
  });

  it('the 1000/1_000_000 boundaries themselves use the larger unit', () => {
    expect(fmtOi(1_000)).toBe('1K');
    expect(fmtOi(1_000_000)).toBe('1.0M');
  });
});

describe('labelStepFor', () => {
  it('returns 1 (label every strike) when the count is already under the max', () => {
    expect(labelStepFor(10, 14)).toBe(1);
  });

  it('returns a step large enough to keep labels within the max count', () => {
    // 30 strikes, max 14 labels -> ceil(30/14) = 3 -> labels at i=0,3,6,...,27 = 10 labels, <= 14
    const step = labelStepFor(30, 14);
    expect(step).toBe(3);
    const labelCount = Math.ceil(30 / step);
    expect(labelCount).toBeLessThanOrEqual(14);
  });

  it('never returns 0 even for a single strike, which would cause a divide-by-zero-style modulo bug', () => {
    expect(labelStepFor(1, 14)).toBe(1);
  });

  it('never returns 0 for a zero strikeCount — a bare Math.ceil(0/max) would be 0, and `i % 0` is always NaN/truthy, silently rendering zero labels', () => {
    expect(labelStepFor(0, 14)).toBe(1);
  });

  it('defaults maxLabels to 14 when not specified', () => {
    expect(labelStepFor(30)).toBe(labelStepFor(30, 14));
  });
});

describe('hasNoRealOi', () => {
  it('is true for an empty points array', () => {
    expect(hasNoRealOi([])).toBe(true);
  });

  it('is true when many strikes exist but every single one has oi=0 on both sides (the real MU-expiry bug case)', () => {
    const points = aggregateOiByStrike(
      [row(480, 0), row(590, 0), row(600, 0)],
      [row(480, 0), row(605, 0)],
    );
    expect(points.length).toBeGreaterThan(0);
    expect(hasNoRealOi(points)).toBe(true);
  });

  it('is false when at least one strike has real call OI', () => {
    const points = aggregateOiByStrike([row(100, 0), row(110, 50)], [row(100, 0)]);
    expect(hasNoRealOi(points)).toBe(false);
  });

  it('is false when at least one strike has real put OI', () => {
    const points = aggregateOiByStrike([row(100, 0)], [row(100, 0), row(110, 25)]);
    expect(hasNoRealOi(points)).toBe(false);
  });
});

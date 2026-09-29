import { describe, it, expect } from 'vitest';
import { segments, volBounds, fmtVol, fmtSpread, spreadRead, dateTicks, shortDate } from './ivHvChart';
import type { IvHvPoint } from './ivHvChart';

function pt(date: string, iv: number | null, hv: number | null): IvHvPoint {
  return { date, iv, hv };
}

describe('segments', () => {
  it('returns one run when every reading is present', () => {
    const points = [pt('2026-09-01', 0.2, 0.18), pt('2026-09-02', 0.21, 0.19)];
    expect(segments(points, 'iv')).toHaveLength(1);
    expect(segments(points, 'iv')[0]).toEqual([{ i: 0, value: 0.2 }, { i: 1, value: 0.21 }]);
  });

  it('BREAKS the line at a gap instead of bridging it', () => {
    // The point of the whole module. A single polyline through the hole would draw a straight
    // segment across 2026-09-02 as though a reading had been taken there.
    const points = [pt('2026-09-01', 0.2, null), pt('2026-09-02', null, null), pt('2026-09-03', 0.3, null)];
    const runs = segments(points, 'iv');
    expect(runs).toHaveLength(2);
    expect(runs[0]).toEqual([{ i: 0, value: 0.2 }]);
    expect(runs[1]).toEqual([{ i: 2, value: 0.3 }]);
  });

  it('keeps the original index so both series share one x-axis', () => {
    // HV starts later than IV (it needs a full 20-session window first). If the runs were
    // re-indexed from zero the two lines would be drawn over different date ranges.
    const points = [pt('2026-09-01', 0.2, null), pt('2026-09-02', 0.21, null), pt('2026-09-03', 0.22, 0.19)];
    expect(segments(points, 'hv')[0]).toEqual([{ i: 2, value: 0.19 }]);
  });

  it('treats a non-finite reading as a gap, not as a value', () => {
    const points = [pt('2026-09-01', 0.2, null), pt('2026-09-02', NaN, null), pt('2026-09-03', 0.3, null)];
    expect(segments(points, 'iv')).toHaveLength(2);
  });

  it('returns nothing for a series that is entirely absent', () => {
    expect(segments([pt('2026-09-01', 0.2, null)], 'hv')).toEqual([]);
  });
});

describe('volBounds', () => {
  it('spans both series, not just one', () => {
    // A scale fitted to IV alone would push HV off the chart, or vice versa.
    const b = volBounds([pt('2026-09-01', 0.5, 0.1)]);
    expect(b.min).toBeLessThanOrEqual(0.1);
    expect(b.max).toBeGreaterThanOrEqual(0.5);
  });

  it('never returns a negative floor', () => {
    // Padding below a near-zero minimum would imply volatility can be negative. The spread has
    // to be WIDE for the 8% padding to actually cross zero — a narrow band never does, so a
    // narrow fixture here would pass against an unclamped implementation and prove nothing.
    const b = volBounds([pt('2026-09-01', 1.2, 0.01)]);
    expect(b.min).toBeGreaterThanOrEqual(0);
    // Confirm the fixture really is one that would go negative without the clamp.
    expect(0.01 - (1.2 - 0.01) * 0.08).toBeLessThan(0);
  });

  it('gives a flat series a usable band rather than a zero-height one', () => {
    const b = volBounds([pt('2026-09-01', 0.2, 0.2), pt('2026-09-02', 0.2, 0.2)]);
    expect(b.max).toBeGreaterThan(b.min);
  });

  it('falls back to a sane band when there is nothing to plot', () => {
    const b = volBounds([]);
    expect(b.max).toBeGreaterThan(b.min);
  });
});

describe('fmtVol', () => {
  it('renders a fraction as a percentage', () => {
    expect(fmtVol(0.2473)).toBe('24.7%');
  });

  it('renders a missing reading as a dash, never as zero', () => {
    // A "0.0%" would read as a measured claim that the stock did not move.
    expect(fmtVol(null)).toBe('—');
    expect(fmtVol(undefined)).toBe('—');
    expect(fmtVol(NaN)).toBe('—');
  });
});

describe('fmtSpread', () => {
  it('reports the difference of two rates in POINTS, with a sign', () => {
    expect(fmtSpread(0.067)).toBe('+6.7 pts');
    expect(fmtSpread(-0.032)).toBe('-3.2 pts');
  });

  it('renders a missing spread as a dash', () => {
    expect(fmtSpread(null)).toBe('—');
  });
});

describe('spreadRead', () => {
  it('describes a wide positive gap as options priced above realized movement', () => {
    expect(spreadRead(0.08).tone).toBe('rich');
  });

  it('describes a wide negative gap as options priced below it', () => {
    expect(spreadRead(-0.08).tone).toBe('cheap');
  });

  it('calls a small gap broadly in line rather than picking a side', () => {
    expect(spreadRead(0.01).tone).toBe('fair');
    expect(spreadRead(-0.01).tone).toBe('fair');
  });

  it('says so when there is no overlap, rather than defaulting to fair', () => {
    expect(spreadRead(null).tone).toBe('unknown');
  });
});

describe('dateTicks', () => {
  it('always includes the first and last point', () => {
    const points = Array.from({ length: 60 }, (_, i) => pt(`2026-09-${i}`, 0.2, 0.2));
    const ticks = dateTicks(points, 6);
    expect(ticks[0]).toBe(0);
    expect(ticks[ticks.length - 1]).toBe(59);
  });

  it('never exceeds the cap', () => {
    const points = Array.from({ length: 250 }, (_, i) => pt(`d${i}`, 0.2, 0.2));
    expect(dateTicks(points, 6).length).toBeLessThanOrEqual(6);
  });

  it('labels every point when there are fewer than the cap', () => {
    const points = [pt('a', 0.2, 0.2), pt('b', 0.2, 0.2)];
    expect(dateTicks(points, 6)).toEqual([0, 1]);
  });

  it('returns nothing for an empty series', () => {
    expect(dateTicks([], 6)).toEqual([]);
  });
});

describe('shortDate', () => {
  it('formats an ISO trading date', () => {
    expect(shortDate('2026-09-28')).toBe('Sep 28');
    expect(shortDate('2026-01-01')).toBe('Jan 1');
  });

  it('does not shift the day in a timezone west of UTC', () => {
    // Built from the string, never `new Date('2026-09-28')`, which parses as UTC midnight and
    // renders as the 27th anywhere in the Americas. These are trading days, not instants.
    expect(shortDate('2026-09-28')).toBe('Sep 28');
    expect(shortDate('2026-03-01')).toBe('Mar 1');
    expect(shortDate('2026-12-31')).toBe('Dec 31');
  });

  it('passes through anything that is not an ISO date', () => {
    expect(shortDate('nonsense')).toBe('nonsense');
    expect(shortDate('2026-13-01')).toBe('2026-13-01');
  });
});

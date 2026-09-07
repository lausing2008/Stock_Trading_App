import { describe, it, expect } from 'vitest';
import {
  aggregateGexOiByStrike,
  maxGexOi,
  hasNoRealGexOi,
  buildMaxPainSeries,
  maxPainRange,
  buildTermStructure,
  maxTermStructureOi,
  fmtCompact,
  fmtExpiryLabel,
  labelStepFor,
} from './optionsTabCharts';
import type { OIPerStrikeRow, MaxPainRow, OptionsExpirationRow } from './api';

function oiRow(strike: number | null, call_oi: number | null, put_oi: number | null): OIPerStrikeRow {
  return { strike, call_oi, put_oi };
}

function expRow(expiry: string, call_oi: number, put_oi: number, overrides: Partial<OptionsExpirationRow> = {}): OptionsExpirationRow {
  return {
    expiry, call_oi, put_oi,
    total_oi: call_oi + put_oi,
    call_volume: 0, put_volume: 0,
    put_call_oi_ratio: call_oi > 0 ? put_oi / call_oi : null,
    concentration_pct: 0,
    level: 'normal',
    ...overrides,
  };
}

// ── aggregateGexOiByStrike ────────────────────────────────────────────────────────────────

describe('aggregateGexOiByStrike', () => {
  it('returns one ascending point per distinct strike', () => {
    const got = aggregateGexOiByStrike([oiRow(110, 5, 1), oiRow(100, 2, 3)]);
    expect(got.map(p => p.strike)).toEqual([100, 110]);
  });

  it('sums call and put OI when a strike appears more than once', () => {
    const got = aggregateGexOiByStrike([oiRow(100, 10, 5), oiRow(100, 7, 3)]);
    expect(got).toEqual([{ strike: 100, callOi: 17, putOi: 8 }]);
  });

  it('drops rows with a null strike — there is no x-position for them', () => {
    const got = aggregateGexOiByStrike([oiRow(null, 99, 99), oiRow(100, 1, 1)]);
    expect(got).toEqual([{ strike: 100, callOi: 1, putOi: 1 }]);
  });

  it('treats a null OI on one side as 0 for that side only, keeping the strike', () => {
    const got = aggregateGexOiByStrike([oiRow(100, null, 42)]);
    expect(got).toEqual([{ strike: 100, callOi: 0, putOi: 42 }]);
  });

  it('preserves a strike of 0 rather than dropping it as falsy', () => {
    const got = aggregateGexOiByStrike([oiRow(0, 5, 5)]);
    expect(got).toEqual([{ strike: 0, callOi: 5, putOi: 5 }]);
  });

  it('returns an empty array for empty, undefined, or null input', () => {
    expect(aggregateGexOiByStrike([])).toEqual([]);
    expect(aggregateGexOiByStrike(undefined)).toEqual([]);
    expect(aggregateGexOiByStrike(null)).toEqual([]);
  });
});

// ── maxGexOi ──────────────────────────────────────────────────────────────────────────────

describe('maxGexOi', () => {
  it('returns the largest single-side value across all strikes', () => {
    expect(maxGexOi([
      { strike: 100, callOi: 10, putOi: 40 },
      { strike: 110, callOi: 25, putOi: 5 },
    ])).toBe(40);
  });

  it('floors at 1 so an all-zero dataset cannot divide to Infinity or NaN', () => {
    expect(maxGexOi([{ strike: 100, callOi: 0, putOi: 0 }])).toBe(1);
    expect(maxGexOi([])).toBe(1);
  });
});

// ── hasNoRealGexOi ────────────────────────────────────────────────────────────────────────

describe('hasNoRealGexOi', () => {
  it('is true for no points at all', () => {
    expect(hasNoRealGexOi([])).toBe(true);
  });

  it('is true when every strike reports zero on BOTH sides', () => {
    // The real BUG-OPTIONSCHAINCHART-ALLZEROOI shape: many strikes resolve, all with oi=0,
    // which a bare length check would render as an unexplained empty grid.
    expect(hasNoRealGexOi([
      { strike: 100, callOi: 0, putOi: 0 },
      { strike: 105, callOi: 0, putOi: 0 },
    ])).toBe(true);
  });

  it('is false when any single side at any strike has real OI', () => {
    expect(hasNoRealGexOi([
      { strike: 100, callOi: 0, putOi: 0 },
      { strike: 105, callOi: 0, putOi: 1 },
    ])).toBe(false);
  });
});

// ── buildMaxPainSeries ────────────────────────────────────────────────────────────────────

describe('buildMaxPainSeries', () => {
  it('builds a chronological series from ISO expiry strings', () => {
    const rows: MaxPainRow[] = [
      { expiry: '2026-10-16', max_pain: 105 },
      { expiry: '2026-09-18', max_pain: 100 },
    ];
    expect(buildMaxPainSeries(rows)).toEqual([
      { expiry: '2026-09-18', maxPain: 100 },
      { expiry: '2026-10-16', maxPain: 105 },
    ]);
  });

  it('drops rows missing either the expiry or the max_pain value', () => {
    const rows: MaxPainRow[] = [
      { expiry: null, max_pain: 100 },
      { expiry: '2026-09-18', max_pain: null },
      { expiry: '2026-10-16', max_pain: 105 },
    ];
    expect(buildMaxPainSeries(rows)).toEqual([{ expiry: '2026-10-16', maxPain: 105 }]);
  });

  it('keeps a genuine max_pain of 0 rather than dropping it as falsy', () => {
    expect(buildMaxPainSeries([{ expiry: '2026-09-18', max_pain: 0 }]))
      .toEqual([{ expiry: '2026-09-18', maxPain: 0 }]);
  });

  it('returns empty for empty, undefined, or null input', () => {
    expect(buildMaxPainSeries([])).toEqual([]);
    expect(buildMaxPainSeries(undefined)).toEqual([]);
    expect(buildMaxPainSeries(null)).toEqual([]);
  });
});

// ── maxPainRange ──────────────────────────────────────────────────────────────────────────

describe('maxPainRange', () => {
  it('returns a padded min/max spanning the series', () => {
    const got = maxPainRange([
      { expiry: '2026-09-18', maxPain: 100 },
      { expiry: '2026-10-16', maxPain: 200 },
    ], 0.1);
    expect(got).toEqual({ min: 90, max: 210 });
  });

  it('pads around a single point so it renders mid-plot, not on the boundary', () => {
    const got = maxPainRange([{ expiry: '2026-09-18', maxPain: 100 }], 0.05);
    expect(got!.min).toBeLessThan(100);
    expect(got!.max).toBeGreaterThan(100);
  });

  it('pads a flat series where every expiry shares one max-pain strike', () => {
    const got = maxPainRange([
      { expiry: '2026-09-18', maxPain: 50 },
      { expiry: '2026-10-16', maxPain: 50 },
    ]);
    expect(got!.min).toBeLessThan(50);
    expect(got!.max).toBeGreaterThan(50);
  });

  it('still produces a non-degenerate range for an all-zero series', () => {
    // Math.abs(0) * pct === 0, so the `|| 1` fallback has to kick in or min === max === 0
    // and every point would divide by a zero-height plot.
    const got = maxPainRange([{ expiry: '2026-09-18', maxPain: 0 }]);
    expect(got!.max).toBeGreaterThan(got!.min);
  });

  it('returns null for an empty series so the caller can render its own empty state', () => {
    expect(maxPainRange([])).toBeNull();
  });
});

// ── buildTermStructure ────────────────────────────────────────────────────────────────────

describe('buildTermStructure', () => {
  it('sorts chronologically and maps every field through', () => {
    const got = buildTermStructure([
      expRow('2026-10-16', 10, 20),
      expRow('2026-09-18', 5, 5),
    ]);
    expect(got.map(p => p.expiry)).toEqual(['2026-09-18', '2026-10-16']);
    expect(got[1]).toMatchObject({ callOi: 10, putOi: 20, totalOi: 30 });
  });

  it('caps to the NEAREST expiries, not an arbitrary slice', () => {
    const rows = ['2026-09-18', '2026-10-16', '2026-11-20', '2027-01-15'].map(e => expRow(e, 1, 1));
    const got = buildTermStructure(rows, 2);
    expect(got.map(p => p.expiry)).toEqual(['2026-09-18', '2026-10-16']);
  });

  it('returns everything when limit is 0 or negative', () => {
    const rows = ['2026-09-18', '2026-10-16', '2026-11-20'].map(e => expRow(e, 1, 1));
    expect(buildTermStructure(rows, 0)).toHaveLength(3);
    expect(buildTermStructure(rows, -1)).toHaveLength(3);
  });

  it('does not mutate the caller’s array while sorting', () => {
    const rows = [expRow('2026-10-16', 1, 1), expRow('2026-09-18', 1, 1)];
    buildTermStructure(rows);
    expect(rows[0].expiry).toBe('2026-10-16');  // original order intact
  });

  it('returns empty for empty, undefined, or null input', () => {
    expect(buildTermStructure([])).toEqual([]);
    expect(buildTermStructure(undefined)).toEqual([]);
    expect(buildTermStructure(null)).toEqual([]);
  });
});

// ── maxTermStructureOi ────────────────────────────────────────────────────────────────────

describe('maxTermStructureOi', () => {
  it('returns the largest STACKED total, since the bars are stacked', () => {
    const got = maxTermStructureOi([
      { expiry: 'a', callOi: 10, putOi: 20, totalOi: 30, putCallOiRatio: null, concentrationPct: 0, level: 'normal' },
      { expiry: 'b', callOi: 25, putOi: 1, totalOi: 26, putCallOiRatio: null, concentrationPct: 0, level: 'normal' },
    ]);
    expect(got).toBe(30);  // 10+20 stacked beats 25+1
  });

  it('floors at 1 for an empty or all-zero series', () => {
    expect(maxTermStructureOi([])).toBe(1);
  });
});

// ── formatting helpers ────────────────────────────────────────────────────────────────────

describe('fmtCompact', () => {
  it('formats thousands and millions', () => {
    expect(fmtCompact(12_345)).toBe('12.3K');
    expect(fmtCompact(1_234_567)).toBe('1.2M');
  });

  it('leaves values under 1000 as plain rounded integers', () => {
    expect(fmtCompact(999)).toBe('999');
    expect(fmtCompact(0)).toBe('0');
  });

  it('handles negatives without losing the sign or mis-bucketing', () => {
    expect(fmtCompact(-12_345)).toBe('-12.3K');
  });
});

describe('fmtExpiryLabel', () => {
  it('shortens an ISO date to a compact month/day label', () => {
    expect(fmtExpiryLabel('2026-09-18')).toBe('Sep 18');
    expect(fmtExpiryLabel('2026-01-02')).toBe('Jan 2');
  });

  it('returns the input unchanged for an unexpected shape rather than rendering Invalid Date', () => {
    expect(fmtExpiryLabel('not-a-date')).toBe('not-a-date');
    expect(fmtExpiryLabel('2026-09')).toBe('2026-09');
  });

  it('returns the input unchanged for an out-of-range month', () => {
    expect(fmtExpiryLabel('2026-13-01')).toBe('2026-13-01');
  });
});

describe('labelStepFor', () => {
  it('thins labels so they do not overlap', () => {
    expect(labelStepFor(24, 12)).toBe(2);
    expect(labelStepFor(10, 12)).toBe(1);
  });

  it('always returns at least 1 — a step of 0 would render no labels at all', () => {
    // `i % 0` is NaN in JS, so every label check would be falsy and the axis would silently
    // come out blank. This is the exact gap a T270 sabotage cycle caught late.
    expect(labelStepFor(0)).toBe(1);
  });
});

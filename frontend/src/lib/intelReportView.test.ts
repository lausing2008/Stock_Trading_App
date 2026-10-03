import { describe, it, expect } from 'vitest';
import {
  renderKind, tableColumns, sectionRank, windowOf, orderFields, formatScalar,
} from './intelReportView';

/** The exact shape sector leadership produces, which rendered as "[object Object]". */
const SECTOR_LEADERSHIP = {
  sessions: 20,
  ranked: [
    { sector: 'Technology', mean_return_pct: 4.21, symbols: 31 },
    { sector: 'Energy', mean_return_pct: -1.08, symbols: 12 },
  ],
  basis: 'equal-weighted mean of covered symbols per sector, not a sector index',
};

describe('nested values render structurally, not as [object Object]', () => {
  it('a list of uniform objects is a table', () => {
    expect(renderKind(SECTOR_LEADERSHIP.ranked)).toBe('table');
  });

  it('the containing object is an object, and its nested list still resolves to a table', () => {
    expect(renderKind(SECTOR_LEADERSHIP)).toBe('object');
    expect(renderKind(SECTOR_LEADERSHIP.ranked)).toBe('table');
  });

  it('a table exposes every column any row carries', () => {
    expect(tableColumns(SECTOR_LEADERSHIP.ranked)).toEqual(
      ['sector', 'mean_return_pct', 'symbols']);
    expect(tableColumns([{ a: 1 }, { b: 2 }])).toEqual(['a', 'b']);
  });

  it('a list of scalars is a list, not a table', () => {
    expect(renderKind(['a', 'b'])).toBe('list');
  });

  it.each([[null, 'empty'], [undefined, 'empty'], [[], 'empty'],
           [1, 'scalar'], ['x', 'scalar'], [true, 'scalar']])(
    'classifies %j as %s', (v, kind) => expect(renderKind(v)).toBe(kind));

  it('no rendered value is ever the literal [object Object]', () => {
    // The defect's signature: the only way to produce it is String() on an object.
    for (const v of [SECTOR_LEADERSHIP, SECTOR_LEADERSHIP.ranked, { a: { b: 1 } }]) {
      expect(renderKind(v)).not.toBe('scalar');
    }
  });
});

describe('field ordering', () => {
  const f = (state = 'OK') => ({ state });

  it('orders return windows numerically, not alphabetically', () => {
    const keys = ['return_20_bars', 'return_1_bars', 'return_63_bars', 'return_5_bars'];
    const out = orderFields(keys.map(k => [k, f()] as [string, { state: string }]));
    expect(out.map(([k]) => k)).toEqual(
      ['return_1_bars', 'return_5_bars', 'return_20_bars', 'return_63_bars']);
  });

  it('puts identity before conclusions before raw inputs', () => {
    expect(sectionRank('issuer')).toBeLessThan(sectionRank('return_5_bars'));
    expect(sectionRank('price_as_of')).toBeLessThan(sectionRank('scenarios'));
    expect(sectionRank('event_coverage')).toBe(sectionRank('event_identity'));
  });

  it('puts resolved fields before unsourced ones whatever their section', () => {
    const out = orderFields([
      ['issuer', f('UNAVAILABLE')],
      ['scenarios', f('OK')],
    ]);
    expect(out[0][0]).toBe('scenarios');
  });

  it('reads the window out of both naming conventions', () => {
    expect(windowOf('return_20_bars')).toBe(20);
    expect(windowOf('run_up_5_bars')).toBe(5);
    expect(windowOf('issuer')).toBeNull();
  });
});

describe('scalar formatting', () => {
  it('renders a percent field with a percent sign', () => {
    expect(formatScalar(15.38, 'pct')).toBe('15.38%');
  });

  it('does not invent a unit where there is none', () => {
    expect(formatScalar(42)).toBe('42');
  });

  it('shows an absent value as a dash, never as null', () => {
    expect(formatScalar(null)).toBe('—');
    expect(formatScalar(undefined)).toBe('—');
  });

  it('renders booleans as words', () => {
    expect(formatScalar(true)).toBe('yes');
  });
});

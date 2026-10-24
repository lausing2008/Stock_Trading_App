import { expect, it } from 'vitest';
import type { OptionStrategy } from './api';
import { candidateDte, compareCandidateRows } from './optionCandidateComparison';
const today = '2026-01-02';
function candidate(id: string, spread: number | null, oi: number | null, capital: number, expiry = '2026-02-06'): OptionStrategy {
  return { name: id, collateral_per_contract: capital, legs: [{ option_symbol: id, strike: 110, spread_pct: spread, oi, expiry, days_to_expiry: 999 }] } as OptionStrategy;
}
it('keeps server order and input identities without mutating the source', () => {
  const values = [candidate('B', 9, 10, 200), candidate('A', 2, 20, 100)];
  expect(compareCandidateRows(values, 'server', today).map(r => r.sourceIndex)).toEqual([0, 1]);
  const sorted = compareCandidateRows(values, 'spread', today);
  expect(sorted.map(r => r.sourceIndex)).toEqual([1, 0]);
  expect(sorted[0].strategy).toBe(values[1]);
  expect(values.map(v => v.name)).toEqual(['B', 'A']);
});
it('treats zero spread/OI as measured values and unknown/nonfinite values as last', () => {
  const values = [candidate('missing', null, null, 100), candidate('zero', 0, 0, 100), candidate('known', 5, 10, 100), candidate('invalid', NaN, -1, 100)];
  expect(compareCandidateRows(values, 'spread', today).map(r => r.strategy.name)).toEqual(['zero', 'known', 'invalid', 'missing']);
  expect(compareCandidateRows(values, 'oi', today).map(r => r.strategy.name)).toEqual(['known', 'zero', 'invalid', 'missing']);
});
it('uses current calendar DTE rather than stored age and rejects normalized invalid dates', () => {
  expect(candidateDte('2026-02-06', today)).toBe(35);
  expect(candidateDte('2026-02-30', today)).toBeNull();
  const values = [candidate('late', 1, 1, 100, '2026-03-06'), candidate('soon', 1, 1, 100)];
  expect(compareCandidateRows(values, 'expiry', today).map(r => r.strategy.name)).toEqual(['soon', 'late']);
  expect(compareCandidateRows(values, 'expiry', '2026-01-03')[0].dte).toBe(34);
});
it('sorts positive stated capital only and resolves metric ties by deterministic identity', () => {
  const values = [candidate('B', 2, 10, 200), candidate('A', 2, 10, 100), candidate('C', 2, 10, 0)];
  expect(compareCandidateRows(values, 'capital', today).map(r => r.strategy.name)).toEqual(['A', 'B', 'C']);
  const reversed = [...values].reverse();
  expect(compareCandidateRows(values, 'spread', today).map(r => r.strategy.name)).toEqual(compareCandidateRows(reversed, 'spread', today).map(r => r.strategy.name));
});

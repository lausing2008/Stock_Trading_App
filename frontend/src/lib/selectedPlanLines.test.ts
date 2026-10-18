import { expect, it } from 'vitest';
import { setupFixture } from '@/components/__fixtures__/tradeSetup';
import { selectedPlanLines } from './selectedPlanLines';

it('plots exactly the selected scenario and its two targets', () => {
  expect(selectedPlanLines(setupFixture('breakout')).map(l => l.price)).toEqual([101, 99, 105, 107]);
  expect(selectedPlanLines({ ...setupFixture(), shares_basis: 'held' })[0].label).toBe('Original cost');
});
it('excludes blocked, unavailable, stale, missing and malformed levels', () => {
  for (const state of ['blocked', 'unavailable', 'stale'] as const) expect(selectedPlanLines({ ...setupFixture(), state })).toEqual([]);
  expect(selectedPlanLines()).toEqual([]);
  expect(selectedPlanLines({ ...setupFixture(), stop: null, targets: [{ price: NaN, label: 'bad', reward_risk: null }] }).map(l => l.price)).toEqual([99]);
});

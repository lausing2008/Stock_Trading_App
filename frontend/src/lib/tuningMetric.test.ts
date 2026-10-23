import { expect, it } from 'vitest';
import { tuningMetric } from './tuningMetric';
it('distinguishes legacy hit-rate fractions from return percentages', () => {
  expect(tuningMetric({triggered_by:'watchdog',validation_ev_pct:.21,new_value:null})).toBe('Hit rate 21.0%');
  expect(tuningMetric({triggered_by:'manual',validation_ev_pct:.21,new_value:null})).toBe('Gross return 0.21%');
});
it('prefers the typed hit-rate value and preserves unknowns', () => {
  expect(tuningMetric({triggered_by:'watchdog',validation_ev_pct:null,new_value:{hit_rate_fraction:.4}})).toBe('Hit rate 40.0%');
  expect(tuningMetric({triggered_by:'watchdog',validation_ev_pct:null,new_value:null})).toBe('Hit rate unavailable');
});

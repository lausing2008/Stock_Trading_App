import { expect, it } from 'vitest';
import type { OptionStrategyLeg } from './api';
import { EMPTY_OPTION_PREFERENCES, optionPreferenceBlocker, parseOptionPreferences } from './optionSearchPreferences';
const blank = { min_dte: '', max_dte: '', min_abs_delta: '', max_abs_delta: '' };
const leg: OptionStrategyLeg = { action: 'sell', right: 'call', strike: 110, price_per_share: 2, cost_per_contract: 200,
  expiry: '2026-02-06', days_to_expiry: 999, delta: .3 };
it('keeps blanks unconstrained and zero as a real requested bound', () => {
  expect(parseOptionPreferences(blank)).toEqual({ values: EMPTY_OPTION_PREFERENCES, error: null });
  expect(parseOptionPreferences({ ...blank, max_abs_delta: '0' }).values.max_abs_delta).toBe(0);
  expect(optionPreferenceBlocker({ ...leg, delta: null }, EMPTY_OPTION_PREFERENCES, '2026-01-02')).toBeNull();
});
it.each([{ min_dte: '0' }, { max_dte: '1096' }, { min_dte: '1.5' }, { min_abs_delta: '-.1' },
  { max_abs_delta: '1.1' }, { min_dte: '40', max_dte: '30' }, { min_abs_delta: '.5', max_abs_delta: '.4' }])('refuses invalid or inverted bounds %j', values => {
  expect(parseOptionPreferences({ ...blank, ...values }).error).not.toBeNull();
});
it('uses current calendar dates instead of cached DTE and includes boundaries', () => {
  const values = { ...EMPTY_OPTION_PREFERENCES, min_dte: 35, max_dte: 35 };
  expect(optionPreferenceBlocker(leg, values, '2026-01-02')).toBeNull();
  expect(optionPreferenceBlocker(leg, values, '2026-01-03')).toContain('days-to-expiry band');
});
it.each([null, NaN, 1.1, -.3])('blocks unavailable or invalid call delta %s', delta => {
  expect(optionPreferenceBlocker({ ...leg, delta }, { ...EMPTY_OPTION_PREFERENCES, min_abs_delta: .2 }, '2026-01-02')).not.toBeNull();
});
it('uses put contract magnitude independently of buy/sell position sign', () => {
  const values = { ...EMPTY_OPTION_PREFERENCES, min_abs_delta: .3, max_abs_delta: .4 };
  expect(optionPreferenceBlocker({ ...leg, right: 'put', delta: -.3 }, values, '2026-01-02')).toBeNull();
  expect(optionPreferenceBlocker({ ...leg, right: 'put', delta: .3 }, values, '2026-01-02')).toContain('sign');
  expect(optionPreferenceBlocker({ ...leg, delta: .41 }, values, '2026-01-02')).toContain('absolute-delta range');
});

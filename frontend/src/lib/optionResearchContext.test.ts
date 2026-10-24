import { expect, it } from 'vitest';
import type { Signal } from './api';
import { OPTION_HOLDING_DEFAULTS, reviewOptionResearchContext } from './optionResearchContext';
const now = Date.parse('2026-10-23T18:00:00Z');
const signal = (over: Partial<Signal> = {}): Signal => ({ symbol: 'EXAMPLE', horizon: 'SWING', signal: 'BUY', ts: '2026-10-23T17:00:00Z', confidence: 99, bullish_probability: .99,
  reasons: { market_regime: 'bear', trend_above_sma50: true, sma50_above_sma200: false, eps_revision_direction: 1 }, ...over });
const review = (s = signal(), horizon = 'SWING') => reviewOptionResearchContext({ symbol: 'EXAMPLE', horizon, signal: s }, now);
it('uses a matching dated signal while keeping input observation dates and option profitability unverified', () => {
  const r = review();
  expect(r.status).toBe('recorded'); expect(r.conflict).toBe(true);
  expect(r.rows.find(row => row.label === 'Market regime')?.value).toBe('bear');
  expect(r.rows.find(row => row.label === 'Stock trend')?.value).toContain('SMA50 below SMA200');
  expect(r.rows.find(row => row.label === 'Company outlook proxy')?.value).toBe('Analyst recommendations improved');
  expect(r.rows.find(row => row.label === 'Option-horizon return forecast')?.value).toBe('Unavailable');
  expect(r.note).toContain('not independent votes');
});
it.each(['SHORT', 'SWING', 'LONG', 'GROWTH'])('keeps %s evidence bound to its own horizon', horizon => {
  expect(review(signal({ horizon }), horizon).status).toBe('recorded');
  expect(review(signal({ horizon: 'WRONG' }), horizon).rows.every(row => row.value === 'Unavailable')).toBe(true);
});
it.each([
  ['2026-10-20T18:00:00Z', 'recorded'], ['2026-10-20T17:59:59Z', 'stale'],
  ['2026-10-23T18:00:01Z', 'future'], ['2026-10-23T17:00:00', 'timezone_unverified'], ['bad', 'undated'], [null, 'undated'],
] as const)('classifies timestamp %s as %s without using browser-local timezone assumptions', (ts, expected) => {
  expect(review(signal({ ts })).status).toBe(expected);
});
it('rejects wrong-symbol and missing signals without leaking their reasons', () => {
  const r = review(signal({ symbol: 'OTHER' }));
  expect(r.status).toBe('mismatched'); expect(r.recordedAt).toBeNull();
  expect(r.rows.every(row => row.value === 'Unavailable')).toBe(true);
  expect(reviewOptionResearchContext({ symbol: 'EXAMPLE', horizon: 'SWING' }, now).status).toBe('missing');
});
it('distinguishes unknown fields from genuine false and unchanged observations', () => {
  const r = review(signal({ reasons: { trend_above_sma50: false, sma50_above_sma200: false, eps_revision_direction: 0 } }));
  expect(r.rows[2].value).toBe('Price below SMA50; SMA50 below SMA200');
  expect(r.rows[3].value).toContain('unchanged'); expect(r.rows[1].value).toBe('Unavailable');
  expect(review(signal({ reasons: {} })).rows[2].value).toBe('Unavailable');
});
it('does not manufacture a company EPS forecast from the legacy misnamed recommendation proxy', () => {
  expect(review().rows[3].observation).toContain('not an EPS revision');
  expect(review().note).toContain('Market-aware preference unavailable');
  expect(OPTION_HOLDING_DEFAULTS).toEqual({ SHORT: 7, SWING: 14, LONG: 28, GROWTH: 14 });
});
it('rejects an unsupported horizon and safely treats malformed payload fields as unknown', () => {
  expect(review(signal({ horizon: 'DAY' }), 'DAY').status).toBe('unsupported_horizon');
  expect(review({ ...signal(), symbol: undefined } as unknown as Signal).status).toBe('mismatched');
});

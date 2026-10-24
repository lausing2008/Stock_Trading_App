import { expect, it } from 'vitest';
import { calendarDay, optionEventWindow, reviewOptionCalendar } from './optionCalendarEvidence';
const now = Date.parse('2026-01-02T17:00:00Z');
const context = { symbol: 'EXAMPLE', retrievedAt: '2026-01-02T16:00:00Z', earningsDate: '2026-01-10', exDividendDate: '2026-01-20' };
const calendar = reviewOptionCalendar(context, 'EXAMPLE', now);

it.each(['2026-02-30', '2025-02-29', '2026-13-01', '2026-1-02', '2026-01-02T12:00:00Z'])('refuses invalid calendar day %s', raw => {
  expect(calendarDay(raw)).toBeNull();
});
it('accepts actual leap days', () => { expect(calendarDay('2028-02-29')).not.toBeNull(); });
it.each([
  [undefined, 'missing'], [{ ...context, symbol: 'OTHER' }, 'mismatched'],
  [{ ...context, retrievedAt: null }, 'undated'],
  [{ ...context, retrievedAt: '2026-01-02T16:00:00' }, 'timezone_or_date_unverified'],
  [{ ...context, retrievedAt: '2026-02-30T16:00:00Z' }, 'timezone_or_date_unverified'],
  [{ ...context, retrievedAt: '2026-01-02T18:00:00Z' }, 'future'],
  [{ ...context, retrievedAt: '2026-01-02T25:00:00Z' }, 'invalid'],
  [{ ...context, retrievedAt: '2026-01-01T16:59:59Z' }, 'stale'],
] as const)('does not trust unverified retrieval %j', (raw, status) => {
  const result = reviewOptionCalendar(raw, 'EXAMPLE', now);
  expect(result.status).toBe(status);
  expect(optionEventWindow(result, '2026-02-10', '2026-02-06', '2026-01-02', 7).code).toBe('unknown');
});
it('accepts exactly 24 hours and timezone offsets, then expires the record', () => {
  expect(reviewOptionCalendar({ ...context, retrievedAt: '2026-01-01T17:00:00Z' }, 'EXAMPLE', now).current).toBe(true);
  expect(reviewOptionCalendar({ ...context, retrievedAt: '2026-01-02T11:00:00-05:00' }, 'EXAMPLE', now).current).toBe(true);
  expect(reviewOptionCalendar(context, 'EXAMPLE', now + 24 * 3_600_000).current).toBe(false);
});
it.each([
  [null, 'missing'], ['bad', 'invalid'], ['2026-01-01', 'past'],
  ['2026-01-02', 'in_hold'], ['2026-01-09', 'in_hold'],
  ['2026-01-10', 'before_expiry'], ['2026-02-06', 'before_expiry'], ['2026-02-07', 'after_expiry'],
] as const)('classifies reported date %s conservatively', (event, code) => {
  expect(optionEventWindow(calendar, event, '2026-02-06', '2026-01-02', 7).code).toBe(code);
});
it('needs a readable future option expiry, and still reviews events beyond the planned exit', () => {
  expect(optionEventWindow(calendar, '2026-01-20', '2026-02-06', '2026-01-02', 7).label).toContain('after planned hold, but by expiry');
  expect(optionEventWindow(calendar, '2026-01-20', undefined, '2026-01-02', 7).code).toBe('unknown');
  expect(optionEventWindow(calendar, '2026-01-20', '2026-01-02', '2026-01-02', 7).code).toBe('unknown');
});

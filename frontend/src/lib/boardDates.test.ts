import { expect, it } from 'vitest';
import { relDate, utcMs } from './boardDates';

it('reads the API\'s zone-less timestamps as UTC, so a new card is "Today", never "-1d ago"', () => {
  const now = Date.parse('2026-10-21T07:10:00Z');
  expect(utcMs('2026-10-21T07:05:00')).toBe(Date.parse('2026-10-21T07:05:00Z'));
  expect(utcMs('2026-10-21T07:05:00+00:00')).toBe(Date.parse('2026-10-21T07:05:00Z'));
  expect(relDate('2026-10-21T07:05:00', now)).toBe('Today');
  expect(relDate('2026-10-20T06:00:00', now)).toBe('Yesterday');
  expect(relDate('2026-10-17T07:00:00', now)).toBe('4d ago');
});

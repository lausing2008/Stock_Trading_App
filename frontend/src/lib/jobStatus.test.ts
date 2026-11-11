import { expect, it } from 'vitest';
import { degradedReason, jobStatusKind } from './jobStatus';

it('classifies the statuses the scheduler records', () => {
  expect(jobStatusKind('ok')).toBe('ok');
  expect(jobStatusKind('error')).toBe('error');
  expect(jobStatusKind('degraded: measured 0 of 39 (no_live_price 39)')).toBe('degraded');
  expect(jobStatusKind('skipped: US market closed')).toBe('skipped');
  expect(jobStatusKind('Need 30 outcomes')).toBe('skipped');
  expect(jobStatusKind(undefined)).toBe('unknown');
});

it('extracts the degraded reason', () => {
  expect(degradedReason('degraded: measured 0 of 39 (no_live_price 39)')).toBe('measured 0 of 39 (no_live_price 39)');
  expect(degradedReason('ok')).toBeNull();
});

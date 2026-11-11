import { expect, it } from 'vitest';
import { darkPoolBaselineText } from './darkPoolBaseline';

it('states the multiple, the median and the sample when the relative bar ran', () => {
  expect(darkPoolBaselineText({ status: 'ok', median: 410_000, prints: 640, window_days: 14, vs_baseline: 12.3, qualified_by: 'floor_and_relative' }))
    .toEqual({ text: '12.3× median $410k, 640 prints / 14d', floorOnly: false });
});

it('says when only the $1M floor applied, and why', () => {
  expect(darkPoolBaselineText({ status: 'query_failed', median: null, prints: null, window_days: 14, vs_baseline: null, qualified_by: 'floor_only' }))
    .toEqual({ text: '$1M floor only (baseline query failed)', floorOnly: true });
  expect(darkPoolBaselineText({ status: 'insufficient_history', median: null, prints: 6, window_days: 14, vs_baseline: null, qualified_by: 'floor_only' }).text)
    .toBe('$1M floor only (too little captured history)');
});

it('says not recorded for rows from before it was stored', () => {
  expect(darkPoolBaselineText(null).text).toBe('not recorded');
  expect(darkPoolBaselineText(undefined).text).toBe('not recorded');
});

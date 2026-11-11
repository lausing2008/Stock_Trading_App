import { expect, it } from 'vitest';
import { isPrimeCandidate } from './squeezeCandidates';

const base = { momentum_score: 70, short_percent_of_float: 22, ranking_is_stale: false, is_stale: false };

it('badges a current high-short, strong-momentum row', () => {
  expect(isPrimeCandidate(base)).toBe(true);
});

it('does not badge on a stale ranking or stale short interest (AUD-SQZ-DATAAGE)', () => {
  expect(isPrimeCandidate({ ...base, ranking_is_stale: true })).toBe(false);
  expect(isPrimeCandidate({ ...base, is_stale: true })).toBe(false);
});

it('keeps the original thresholds', () => {
  expect(isPrimeCandidate({ ...base, momentum_score: 50 })).toBe(false);
  expect(isPrimeCandidate({ ...base, short_percent_of_float: 14.9 })).toBe(false);
  expect(isPrimeCandidate({ ...base, momentum_score: null })).toBe(false);
});

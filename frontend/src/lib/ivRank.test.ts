import { expect, it } from 'vitest';
import { IV_RANK_CHEAP, IV_RANK_RICH, ivRankRead } from './ivRank';

it('reads rich at 70 and cheap at 30, inclusive (AUD-OPT-IVRANK-ALIGN)', () => {
  expect([IV_RANK_RICH, IV_RANK_CHEAP]).toEqual([70, 30]);
  expect(ivRankRead(70)).toBe('options relatively expensive');
  expect(ivRankRead(69.9)).toBe('mid-range');
  expect(ivRankRead(60)).toBe('mid-range');
  expect(ivRankRead(30)).toBe('options relatively cheap');
});

it('makes no claim on a missing or out-of-range rank', () => {
  for (const v of [null, undefined, NaN, -1, 101]) expect(ivRankRead(v as number)).toBeNull();
});
